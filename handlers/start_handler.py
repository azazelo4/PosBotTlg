import telebot
from telebot import types
from auth import authorize_user # authorize_user теперь ожидает сырой номер
from buttons import create_cashier_buttons, create_admin_buttons, create_manager_buttons # Добавил manager_buttons если есть
from state import bot, user_data 
import logging

logger = logging.getLogger(__name__)


def start_command_handler(message: types.Message):
    chat_id = message.chat.id
    logger.info(f"Chat {chat_id}: /start command received.")
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True) 
    button_phone = types.KeyboardButton(text="Поделиться номером", request_contact=True)
    keyboard.add(button_phone)
    bot.send_message(chat_id, "Пожалуйста, поделитесь своим номером телефона для авторизации:", reply_markup=keyboard)

def contact_handler(message: types.Message):
    chat_id = message.chat.id
    if message.contact is None:
        logger.warning(f"Chat {chat_id}: Received message in contact_handler without contact attribute.")
        bot.send_message(chat_id, "Не удалось получить номер телефона. Пожалуйста, попробуйте снова нажать на кнопку 'Поделиться номером'.")
        return

    phone_number_from_contact = message.contact.phone_number # Это "сырой" номер из контакта
    logger.info(f"Chat {chat_id}: Contact received, raw phone number: {phone_number_from_contact}")

    remove_markup = types.ReplyKeyboardRemove()
    bot.send_message(chat_id, f"Спасибо! Ваш номер {phone_number_from_contact} получен. Обрабатываю...", reply_markup=remove_markup)

    try:
        # authorize_user ожидает "сырой" номер и сам (через get_user_by_phone) его нормализует
        db_user_details, error_message = authorize_user(phone_number_from_contact)

        if error_message:
            logger.warning(f"Chat {chat_id}: Authorization error for {phone_number_from_contact}: {error_message}")
            bot.send_message(chat_id, error_message)
            start_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            button_phone = types.KeyboardButton(text="Поделиться номером", request_contact=True)
            start_keyboard.add(button_phone)
            bot.send_message(chat_id, "Пожалуйста, попробуйте авторизоваться снова.", reply_markup=start_keyboard)

        elif db_user_details:
            # db_user_details['phone_number'] должен быть уже нормализованным номером из БД
            user_data[chat_id] = {
                'id': db_user_details.get('id'),
                'store_id': db_user_details.get('store_id'),
                'name': db_user_details.get('name'),
                # 'surename': db_user_details.get('surename'), # Если есть поле surename
                'role': db_user_details.get('role'),
                'phone_number': db_user_details.get('phone_number'), # Сохраняем нормализованный номер
                'is_active': db_user_details.get('is_active', 1)
            }
            logger.info(f"Chat {chat_id}: User {user_data[chat_id]['phone_number']} (DB ID: {user_data[chat_id]['id']}) authorized. Role: {user_data[chat_id]['role']}.")

            reply_text = f"Здравствуйте, {user_data[chat_id].get('name', '')}!\n" # Убрал surename для простоты
            reply_text += f"Вы авторизованы как {user_data[chat_id]['role']}."
            
            final_keyboard = None
            if user_data[chat_id]['role'] == 'cashier':
                final_keyboard = create_cashier_buttons()
            elif user_data[chat_id]['role'] == 'admin':
                final_keyboard = create_admin_buttons()
            elif user_data[chat_id]['role'] == 'manager': # Пример для менеджера
                 final_keyboard = create_manager_buttons() # Убедитесь, что эта функция есть в buttons.py
            else:
                logger.warning(f"Chat {chat_id}: User {user_data[chat_id]['phone_number']} has unknown role: {user_data[chat_id]['role']}")
                final_keyboard = types.ReplyKeyboardRemove()
                reply_text = f"Здравствуйте! Ваша роль '{user_data[chat_id]['role']}' не позволяет использовать этого бота."
                # Не удаляем user_data, но is_active=0 или неизвестная роль не даст доступа к функциям

            bot.send_message(chat_id, reply_text, reply_markup=final_keyboard)
        else:
            logger.error(f"Chat {chat_id}: Authorization for {phone_number_from_contact} failed (no user_data, no error_message).")
            bot.send_message(chat_id, "Произошла неизвестная ошибка при авторизации. Попробуйте еще раз.")
            start_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            button_phone = types.KeyboardButton(text="Поделиться номером", request_contact=True)
            start_keyboard.add(button_phone)
            bot.send_message(chat_id, "Пожалуйста, попробуйте авторизоваться снова.", reply_markup=start_keyboard)

    except Exception as e:
        logger.exception(f"Chat {chat_id}: Exception during contact handling for {phone_number_from_contact}.")
        bot.send_message(chat_id, "Произошла серьезная ошибка при обработке вашего запроса. Пожалуйста, сообщите администратору.")


def register_start_handlers():
    bot.register_message_handler(start_command_handler, commands=['start'], pass_bot=False)
    bot.register_message_handler(contact_handler, content_types=['contact'], pass_bot=False)
    logger.info("Start and contact handlers registered.")