"""Вход в веб-админку по одноразовой ссылке и кнопка кассы-приложения (Telegram Mini App)."""
import logging

from telebot import types

from handlers.buttons import BTN_WEB, BTN_PC_KASSA, get_keyboard_by_role
from core import config
from handlers.common import is_button
from core.state import bot, user_data, cancel_current_operation
from web.security import issue_login_link
from core.logsafe import describe

logger = logging.getLogger(__name__)

APP_BUTTON = "📷 Касса"


def set_app_menu_button(chat_id):
    """Кнопка Mini App рядом с полем ввода. Mini App открывается только по HTTPS: при адресе
    в локальной сети (http://…) кнопка возвращается к обычной — вдруг раньше был туннель."""
    if not config.WEB_URL:
        return
    url = config.mini_app_url()
    try:
        if url:
            bot.set_chat_menu_button(chat_id, types.MenuButtonWebApp(APP_BUTTON, types.WebAppInfo(url)))
        else:
            bot.set_chat_menu_button(chat_id, types.MenuButtonDefault())
    except Exception as e:
        logger.warning("Chat %s: не удалось установить кнопку Mini App: %s", chat_id, describe(e))


def _is_lan_url():
    return bool(config.WEB_URL) and not str(config.WEB_URL).startswith("https://")


def send_link(chat_id, text, label, url, **kwargs):
    """Сообщение со ссылкой-кнопкой. Telegram не принимает в кнопках некоторые адреса (localhost, адреса
    локальной сети) — тогда ссылка отправляется текстом."""
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(label, url=url))
    try:
        return bot.send_message(chat_id, text, reply_markup=kb, **kwargs)
    except Exception as e:
        logger.info("Chat %s: ссылка не принята кнопкой (%s), отправляю текстом", chat_id, describe(e))
        return bot.send_message(chat_id, f"{text}\n\n{url}", disable_web_page_preview=True, **kwargs)


def on_web_admin(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    link = issue_login_link(user_data[chat_id]['id'])
    if not link:
        bot.send_message(chat_id, "Не удалось создать ссылку для входа. Попробуйте ещё раз.", reply_markup=get_keyboard_by_role(chat_id))
        return
    text = ("Ссылка для входа в веб-админку. Одноразовая, действует 10 минут. "
            "Удобнее открыть на компьютере — скопируйте её долгим нажатием.")
    if _is_lan_url():
        text += "\nАдрес в сети магазина: откроется только с устройства, подключённого к этой сети (Wi-Fi магазина)."
    send_link(chat_id, text, "Открыть админку", link)


def on_pc_kassa(message):
    """Ссылка на кассу в браузере на компьютере магазина. Вход там — по QR-коду на экране кассы."""
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    url = f"{config.WEB_URL}/kassa"
    text = (f"Касса на компьютере: {url}\n\n"
            "1. Откройте этот адрес в браузере на компьютере магазина и добавьте в закладки "
            "(или нажмите кнопку ниже в Telegram на этом компьютере).\n"
            "2. На экране появится QR-код — наведите на него обычную камеру телефона.\n"
            "3. Бот спросит о входе — нажмите «✅ Войти».\n\n"
            "Уходя, нажмите на кассе «Выйти», чтобы следующий не работал под вашим именем.")
    if _is_lan_url():
        text += "\n\nАдрес в сети магазина: откроется только на компьютере, подключённом к этой сети."
    send_link(chat_id, text, "Открыть кассу на ПК", url, disable_web_page_preview=True)


def on_app(message):
    chat_id = message.chat.id
    if not config.WEB_URL:
        bot.send_message(chat_id, "Касса-приложение не настроена (не задан адрес веб-админки в настройках).")
        return
    set_app_menu_button(chat_id)
    url = config.mini_app_url()
    if not url:
        send_link(chat_id, "Касса-приложение в Telegram работает только через интернет-адрес с HTTPS, а бот установлен "
                           "на ПК в магазине. Пользуйтесь кассой на компьютере или кнопками бота. Чтобы включить "
                           "кассу-приложение, администратор подключает туннель (инструкция администратора, "
                           "«Маленькая точка»).", "Касса на компьютере", f"{config.WEB_URL}/kassa")
        return
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Открыть кассу", web_app=types.WebAppInfo(url)))
    bot.send_message(chat_id, "Касса со сканером QR-кодов ценников. Также она всегда доступна "
                              f"по кнопке «{APP_BUTTON}» рядом с полем ввода.", reply_markup=kb)


def register_web_handlers():
    if not config.WEB_URL:
        return
    from core.auth import has_role
    bot.register_message_handler(on_web_admin, func=is_button(BTN_WEB, 'admin', 'manager'))
    bot.register_message_handler(on_pc_kassa, func=is_button(BTN_PC_KASSA, 'admin', 'manager', 'cashier'))
    bot.register_message_handler(on_app, commands=['app'], func=lambda m: has_role(m.chat.id))
