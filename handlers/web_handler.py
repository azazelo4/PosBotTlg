"""Вход в веб-админку по одноразовой ссылке и кнопка кассы-приложения (Telegram Mini App)."""
import logging

from telebot import types

from buttons import BTN_WEB, get_keyboard_by_role
import config
from handlers.common import is_button
from state import bot, user_data, cancel_current_operation
from web.security import issue_login_link

logger = logging.getLogger(__name__)

APP_BUTTON = "📷 Касса"


def set_app_menu_button(chat_id):
    """Кнопка Mini App рядом с полем ввода. Без адреса веб-админки ничего не делает."""
    if not config.WEB_URL:
        return
    try:
        bot.set_chat_menu_button(chat_id, types.MenuButtonWebApp(APP_BUTTON, types.WebAppInfo(f"{config.WEB_URL}/app")))
    except Exception as e:
        logger.warning("Chat %s: не удалось установить кнопку Mini App: %s", chat_id, e)


def on_web_admin(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    link = issue_login_link(user_data[chat_id]['id'])
    if not link:
        bot.send_message(chat_id, "Не удалось создать ссылку для входа. Попробуйте ещё раз.", reply_markup=get_keyboard_by_role(chat_id))
        return
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Открыть админку", url=link))
    bot.send_message(chat_id, "Ссылка для входа в веб-админку. Одноразовая, действует 10 минут. "
                              "Удобнее открыть на компьютере — скопируйте её долгим нажатием.", reply_markup=kb)


def on_app(message):
    chat_id = message.chat.id
    if not config.WEB_URL:
        bot.send_message(chat_id, "Касса-приложение не настроена (не задан адрес веб-админки в настройках).")
        return
    set_app_menu_button(chat_id)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Открыть кассу", web_app=types.WebAppInfo(f"{config.WEB_URL}/app")))
    bot.send_message(chat_id, "Касса со сканером QR-кодов ценников. Также она всегда доступна "
                              f"по кнопке «{APP_BUTTON}» рядом с полем ввода.", reply_markup=kb)


def register_web_handlers():
    if not config.WEB_URL:
        return
    from auth import has_role
    bot.register_message_handler(on_web_admin, func=is_button(BTN_WEB, 'admin', 'manager'))
    bot.register_message_handler(on_app, commands=['app'], func=lambda m: has_role(m.chat.id))
