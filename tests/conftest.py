"""Тестовый стенд: настоящая MySQL/MariaDB + имитация Telegram.

Апдейты прогоняются через настоящий диспетчер telebot (фильтры, middleware),
а вызовы Telegram API перехватываются и записываются.

Запуск: TEST_DB_HOST=127.0.0.1 TEST_DB_USER=... TEST_DB_PASS=... pytest -q
"""
import itertools
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("TOKEN", "123456:TEST")
os.environ["DB_HOST"] = os.environ.get("TEST_DB_HOST", "127.0.0.1")
os.environ["DB_PORT"] = os.environ.get("TEST_DB_PORT", "3306")
os.environ["DB_USER"] = os.environ.get("TEST_DB_USER", "bot")
os.environ["DB_PASS"] = os.environ.get("TEST_DB_PASS", "botpass")
os.environ["DB_NAME"] = os.environ.get("TEST_DB_NAME", "bot_test")
os.environ["ADMIN_PHONE"] = "+77010000001"
os.environ["ADMIN_NAME"] = "Админ"
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ["WEB_URL"] = "https://pos.test"

from telebot import types  # noqa: E402

import main  # noqa: E402
from database import get_connection  # noqa: E402
from state import bot, user_data, user_state  # noqa: E402

ADMIN, CASHIER, MANAGER, STRANGER = 1001, 1002, 1003, 1004
PHONES = {ADMIN: "77010000001", CASHIER: "+77010000002", MANAGER: "87010000003", STRANGER: "77019999999"}

_ids = itertools.count(1)


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.documents = []
        self.menu_buttons = {}

    def _record(self, kind, chat_id, text=None, markup=None, **extra):
        self.calls.append(SimpleNamespace(kind=kind, chat_id=chat_id, text=text, markup=markup, **extra))
        return SimpleNamespace(message_id=next(_ids), chat=SimpleNamespace(id=chat_id))

    # --- перехват API
    def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kw):
        return self._record("send", chat_id, text, reply_markup)

    def edit_message_text(self, text, chat_id=None, message_id=None, parse_mode=None, reply_markup=None, **kw):
        return self._record("edit", chat_id, text, reply_markup, message_id=message_id)

    def edit_message_reply_markup(self, chat_id=None, message_id=None, reply_markup=None, **kw):
        return self._record("edit_markup", chat_id, None, reply_markup, message_id=message_id)

    def answer_callback_query(self, callback_query_id, text=None, **kw):
        return True

    def set_chat_menu_button(self, chat_id=None, menu_button=None, **kw):
        self.menu_buttons[chat_id] = menu_button
        return True

    def send_chat_action(self, chat_id, action, **kw):
        return True

    def delete_message(self, chat_id, message_id, **kw):
        return True

    files = {}

    def get_file(self, file_id):
        return SimpleNamespace(file_path=file_id)

    def download_file(self, file_path):
        return self.files[file_path]

    def send_document(self, chat_id, document, caption=None, reply_markup=None, **kw):
        f = getattr(document, 'file', document)
        data = f.getvalue() if hasattr(f, 'getvalue') else f.read()
        self.documents.append(SimpleNamespace(chat_id=chat_id, data=data, name=getattr(document, 'file_name', None)))
        return self._record("document", chat_id, caption, reply_markup)

    # --- отправка апдейтов
    def _process(self, update):
        bot.process_new_updates([types.Update.de_json(update)])

    def send(self, chat_id, text):
        start = len(self.calls)
        self._process({"update_id": next(_ids), "message": {
            "message_id": next(_ids), "date": 0, "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "T"}, "text": text}})
        return self.calls[start:]

    NO_OWNER = object()

    def contact(self, chat_id, phone, owner=None):
        start = len(self.calls)
        self._process({"update_id": next(_ids), "message": {
            "message_id": next(_ids), "date": 0, "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "T"},
            "contact": {"phone_number": phone, "first_name": "T",
                        **({} if owner is self.NO_OWNER else {"user_id": owner or chat_id})}}})
        return self.calls[start:]

    def document(self, chat_id, data, file_name):
        file_id = f"f{next(_ids)}"
        self.files[file_id] = data
        start = len(self.calls)
        self._process({"update_id": next(_ids), "message": {
            "message_id": next(_ids), "date": 0, "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "T"},
            "document": {"file_id": file_id, "file_unique_id": file_id, "file_name": file_name, "file_size": len(data)}}})
        return self.calls[start:]

    def press(self, chat_id, data, message_id=1):
        start = len(self.calls)
        self._process({"update_id": next(_ids), "callback_query": {
            "id": str(next(_ids)), "chat_instance": "x", "data": data,
            "from": {"id": chat_id, "is_bot": False, "first_name": "T"},
            "message": {"message_id": message_id, "date": 0, "chat": {"id": chat_id, "type": "private"}, "text": "m"}}})
        return self.calls[start:]

    # --- разбор ответов
    def last(self, chat_id):
        return [c for c in self.calls if c.chat_id == chat_id][-1]

    def texts(self, calls):
        return "\n".join(c.text or "" for c in calls)


def reply_buttons(call):
    markup = call.markup
    if not isinstance(markup, types.ReplyKeyboardMarkup):
        return []
    return [b['text'] if isinstance(b, dict) else b.text for row in markup.keyboard for b in row]


def inline_buttons(calls):
    result = {}
    for call in calls:
        if isinstance(call.markup, types.InlineKeyboardMarkup):
            for row in call.markup.keyboard:
                for b in row:
                    result[b.text] = b.callback_data
    return result


def db_query(sql, params=()):
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.with_rows else None
        conn.commit()
        cur.close()
        return rows
    finally:
        conn.close()


def _reset_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    cur.execute("SHOW TABLES")
    for (table,) in cur.fetchall():
        cur.execute(f"DROP TABLE `{table}`")
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()
    cur.close()
    conn.close()


@pytest.fixture(scope="session")
def fake():
    tg = FakeTelegram()
    for name in ("send_message", "edit_message_text", "edit_message_reply_markup", "answer_callback_query",
                 "send_chat_action", "delete_message", "send_document", "get_file", "download_file", "set_chat_menu_button"):
        setattr(bot, name, getattr(tg, name))
    bot.threaded = False
    from database import init_pool
    init_pool()
    _reset_db()
    main.prepare_database()
    main.register_handlers()
    return tg


@pytest.fixture
def tg(fake):
    """Чистые данные перед каждым тестом: пользователи трёх ролей и несколько товаров."""
    for table in ("returns", "trade_ins", "buybacks", "cash_withdrawals", "sales", "products", "users", "stores"):
        db_query(f"DELETE FROM `{table}`")
    db_query("INSERT INTO stores (id, name, sort_order) VALUES (1, 'ЦУМ', 0), (2, 'Отау Сауда', 1)")
    user_data.clear()
    user_state.clear()
    fake.calls.clear()
    fake.documents.clear()
    main.prepare_database()  # создаст админа из ADMIN_PHONE
    db_query("INSERT INTO users (phone_number, role, name, store_id, is_active) VALUES "
             "('+77010000002', 'cashier', 'Кассир', 1, 1), ('77010000003', 'manager', 'Менеджер', 1, 1)")
    db_query("""INSERT INTO products (article_number, store_id, metal_type, product_type, model, weight, price_per_gram,
                price, min_price, arrival_date, quantity, track_stock) VALUES
             ('A100', 1, 'золото', 'кольцо', 'Кольцо Классика', 2.5, 40000, 150000, 120000, '2026-10-01', 3, 1),
             ('A200', 2, 'серебро', 'цепь', 'Цепь Бисмарк', 10, 1000, 20000, 15000, '2026-10-01', 1, 1),
             ('A300', 1, 'золото', 'серьги', 'Серьги Капля', 3, 40000, 90000, 80000, '2026-10-01', 0, 0)""")
    return fake


def login(tg, chat_id):
    calls = tg.contact(chat_id, PHONES[chat_id])
    assert "Здравствуйте" in tg.texts(calls), tg.texts(calls)
    return calls
