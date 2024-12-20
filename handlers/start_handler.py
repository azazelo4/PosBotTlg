import telebot
from telebot import types
from config import TOKEN
from auth import authorize_user
from buttons import create_cashier_buttons, create_admin_buttons
import logging

logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TOKEN)
user_data = {}

@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    button_phone = telebot.types.KeyboardButton(text="Поделиться номером", request_contact=True)
    keyboard.add(button_phone)
    bot.send_message(chat_id, "Пожалуйста, поделитесь своим номером телефона для авторизации:", reply_markup=keyboard)

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    chat_id = message.chat.id
    phone_number = message.contact.phone_number

    try:
        user_data_dict, error = authorize_user(phone_number)

        if error:
            bot.send_message(chat_id, error)
        elif user_data_dict:
            user_data[chat_id] = user_data_dict

            if user_data_dict['role'] == 'cashier':
                keyboard = create_cashier_buttons()
            elif user_data_dict['role'] == 'admin':
                keyboard = create_admin_buttons()

            bot.send_message(
                chat_id,
                f"Здравствуйте, {user_data_dict['name']}! Вы авторизованы как {user_data_dict['role']}. Для справки введите команду /help.",
                reply_markup=keyboard
            )
        else:
            bot.send_message(chat_id, "Произошла ошибка при авторизации. Попробуйте еще раз.")
    except Exception as e:
        logger.exception("Ошибка при авторизации пользователя.")
        bot.send_message(chat_id, "Произошла ошибка при авторизации. Попробуйте еще раз.")