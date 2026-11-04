import logging

from telebot import types

from auth import authorize_user, login, drop_session
from buttons import main_menu_for_role, create_share_contact_markup, ROLE_TITLES
from state import bot, cancel_current_operation

logger = logging.getLogger(__name__)


def start_command_handler(message: types.Message):
    chat_id = message.chat.id
    logger.info("Chat %s: /start", chat_id)
    cancel_current_operation(chat_id, notify=False)
    drop_session(chat_id)
    bot.send_message(chat_id, "Поделитесь номером телефона для авторизации:", reply_markup=create_share_contact_markup())


def contact_handler(message: types.Message):
    chat_id = message.chat.id
    contact = message.contact
    if contact is None:
        return
    # Принимаем только собственный контакт пользователя, а не пересланный чужой
    if contact.user_id and message.from_user and contact.user_id != message.from_user.id:
        bot.send_message(chat_id, "Отправьте свой номер кнопкой «Поделиться номером».", reply_markup=create_share_contact_markup())
        return

    user, error = authorize_user(contact.phone_number)
    if error:
        logger.warning("Chat %s: отказ в авторизации: %s", chat_id, error)
        bot.send_message(chat_id, error, reply_markup=create_share_contact_markup())
        return

    login(chat_id, user)
    logger.info("Chat %s: вошёл пользователь id=%s, роль %s", chat_id, user['id'], user['role'])
    name = " ".join(p for p in (user.get('name'), user.get('surename')) if p)
    bot.send_message(chat_id, f"Здравствуйте, {name}!\nВы вошли как {ROLE_TITLES[user['role']]}. Справка — /help.",
                     reply_markup=main_menu_for_role(user['role']))


def register_start_handlers():
    bot.register_message_handler(start_command_handler, commands=['start'])
    bot.register_message_handler(contact_handler, content_types=['contact'])
