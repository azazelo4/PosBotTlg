"""Подтверждение входа на кассу ПК.

Касса на компьютере показывает QR со ссылкой t.me/<бот>?start=k_<токен>. Сотрудник сканирует его
телефоном, Telegram открывает бота с командой /start k_<токен>, бот спрашивает «Войти?».
После подтверждения браузер, показавший QR, сам получает вход.
"""
import logging

from telebot import types

from auth import ensure_session
from buttons import ROLE_TITLES
from database import get_kassa_login, decide_kassa_login
from state import bot
from utils.common import h, now
from web.kassa import START_PREFIX, CASH_ROLES, token_hash

logger = logging.getLogger(__name__)

CB_OK, CB_NO = "kassa_ok:", "kassa_no:"


def is_kassa_start(message):
    parts = (message.text or "").split(maxsplit=1)
    return len(parts) == 2 and parts[0].split("@")[0] == "/start" and parts[1].startswith(START_PREFIX)


def on_kassa_start(message):
    chat_id = message.chat.id
    token = message.text.split(maxsplit=1)[1][len(START_PREFIX):].strip()
    session = ensure_session(chat_id)
    if not session or session.get('role') not in CASH_ROLES:
        bot.send_message(chat_id, "Чтобы войти на кассу, сначала войдите в бота: нажмите /start и поделитесь номером. "
                                  "Затем отсканируйте QR-код на компьютере ещё раз.")
        return
    row = get_kassa_login(token_hash(token)) if 10 <= len(token) <= 64 else None
    if not row or row['status'] != 'pending' or row['expires_at'] < now():
        bot.send_message(chat_id, "QR-код устарел или уже использован. Обновите страницу кассы на компьютере "
                                  "и отсканируйте новый.")
        return
    name = " ".join(p for p in (session.get('name'), session.get('surename')) if p)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("✅ Войти", callback_data=CB_OK + token),
           types.InlineKeyboardButton("❌ Это не я", callback_data=CB_NO + token))
    computer = f"\nКомпьютер: {h(row['client_info'])}" if row.get('client_info') else ""
    bot.send_message(chat_id, f"💻 <b>Вход на кассу ПК</b>{computer}\n\n"
                              f"Открыть кассу под вашим именем — {h(name)} ({ROLE_TITLES.get(session['role'], '')})?\n"
                              "Подтверждайте, только если QR-код на экране вашей кассы.",
                     parse_mode='HTML', reply_markup=kb)


def on_decision(call):
    chat_id = call.message.chat.id
    approve = call.data.startswith(CB_OK)
    token = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id)
    session = ensure_session(chat_id)
    if not session or session.get('role') not in CASH_ROLES:
        bot.edit_message_text("Доступ к кассе закрыт. Обратитесь к администратору.", chat_id, call.message.message_id)
        return
    ok, result = decide_kassa_login(token_hash(token), session['id'], approve)
    if not ok:
        bot.edit_message_text(result, chat_id, call.message.message_id)
        return
    logger.info("Chat %s: вход на кассу ПК %s (%s)", chat_id, "подтверждён" if approve else "отклонён",
                result.get('client_info'))
    text = ("✅ Касса на компьютере открыта под вашим именем. Закончили смену — нажмите «Выйти» на кассе."
            if approve else "Вход на кассу отклонён.")
    bot.edit_message_text(text, chat_id, call.message.message_id)


def register_kassa_login_handlers():
    """Регистрировать до общего /start: тот сбрасывает сессию."""
    bot.register_message_handler(on_kassa_start, commands=['start'], func=is_kassa_start)
    bot.register_callback_query_handler(on_decision, func=lambda c: c.data.startswith((CB_OK, CB_NO)))
