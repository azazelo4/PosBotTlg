"""Аудит Н-1: бот работает только в личном чате. В группе вход сотрудника не создаёт сессию группы,
а команды и кнопки участников не обрабатываются."""
from itertools import count

from telebot import types

from conftest import ADMIN, PHONES, STRANGER, login, db_query
from core.auth import is_private_update
from core.state import bot, user_data

GROUP = -1005550001
_n = count(900000)


def _update(from_id, chat, **body):
    return types.Update.de_json({"update_id": next(_n), "message": {
        "message_id": next(_n), "date": 0, "chat": chat, "from": {"id": from_id, "is_bot": False, "first_name": "X"},
        **body}})


def _callback(from_id, chat, data):
    return types.Update.de_json({"update_id": next(_n), "callback_query": {
        "id": str(next(_n)), "chat_instance": "x", "data": data,
        "from": {"id": from_id, "is_bot": False, "first_name": "X"},
        "message": {"message_id": 5, "date": 0, "chat": chat, "text": "m"}}})


GROUP_CHAT = {"id": GROUP, "type": "supergroup", "title": "Группа"}


def test_group_login_and_commands_are_ignored(tg):
    start = len(tg.calls)
    bot.process_new_updates([_update(ADMIN, GROUP_CHAT, text="/start")])
    bot.process_new_updates([_update(ADMIN, GROUP_CHAT, contact={"phone_number": PHONES[ADMIN], "first_name": "A",
                                                                 "user_id": ADMIN})])
    for text in ("Персонал", "Веб-админка", "Касса", "/start r_" + "a" * 32, "/stop"):
        bot.process_new_updates([_update(STRANGER, GROUP_CHAT, text=text)])
    bot.process_new_updates([_callback(STRANGER, GROUP_CHAT, "um_list")])
    assert tg.calls[start:] == []                                           # в группе бот молчит
    assert GROUP not in user_data
    assert db_query("SELECT chat_id FROM users WHERE role='admin'")[0]['chat_id'] != GROUP
    # в личке всё работает как раньше
    login(tg, ADMIN)
    assert "Управление персоналом" in tg.texts(tg.send(ADMIN, "Персонал"))


def test_admin_session_not_usable_from_group(tg):
    """Администратор вошёл в личке; его же сообщения и кнопки из группы не исполняются."""
    login(tg, ADMIN)
    start = len(tg.calls)
    bot.process_new_updates([_update(ADMIN, GROUP_CHAT, text="Персонал")])
    bot.process_new_updates([_callback(ADMIN, GROUP_CHAT, "um_list")])
    assert tg.calls[start:] == []


def test_private_update_property():
    private = {"id": ADMIN, "type": "private"}
    assert is_private_update(_update(ADMIN, private, text="x").message)
    assert not is_private_update(_update(STRANGER, private, text="x").message)          # чат не отправителя
    assert not is_private_update(_update(ADMIN, GROUP_CHAT, text="x").message)
    assert not is_private_update(_update(ADMIN, {"id": ADMIN, "type": "group"}, text="x").message)
    assert is_private_update(_callback(ADMIN, private, "x").callback_query)
    assert not is_private_update(_callback(STRANGER, private, "x").callback_query)
    bot_msg = types.Update.de_json({"update_id": 1, "message": {"message_id": 1, "date": 0, "chat": private,
                                    "from": {"id": ADMIN, "is_bot": True, "first_name": "B"}, "text": "x"}})
    assert not is_private_update(bot_msg.message)


def test_group_bindings_cleared_on_start(tg):
    from core.database import get_connection
    from core.schema import ensure_schema
    db_query("UPDATE users SET chat_id = %s WHERE role='admin'", (GROUP,))
    db_query("""INSERT INTO customers (phone, chat_id, consent_at, consent_source, created_at, updated_at)
                VALUES ('+77017770000', %s, NOW(), 'telegram', NOW(), NOW())""", (GROUP - 1,))
    conn = get_connection()
    try:
        ensure_schema(conn)
    finally:
        conn.close()
    assert db_query("SELECT chat_id FROM users WHERE role='admin'")[0]['chat_id'] is None
    assert db_query("SELECT chat_id FROM customers")[0]['chat_id'] is None
