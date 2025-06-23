import telebot
from telebot import types
# from config import TOKEN # TOKEN is used by state.py to initialize bot
from auth import authorize_user
from buttons import create_cashier_buttons, create_admin_buttons # These are main menu buttons 
from state import bot, user_data # Import the shared bot instance and user_data
import logging

logger = logging.getLogger(__name__)

# No local bot = telebot.TeleBot(TOKEN) here

def start_command_handler(message: types.Message):
    chat_id = message.chat.id
    logger.info(f"Chat {chat_id}: /start command received.")
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True) # Use one_time_keyboard
    button_phone = types.KeyboardButton(text="Поделиться номером", request_contact=True)
    keyboard.add(button_phone)
    bot.send_message(chat_id, "Пожалуйста, поделитесь своим номером телефона для авторизации:", reply_markup=keyboard)

def contact_handler(message: types.Message):
    chat_id = message.chat.id
    if message.contact is None:
        logger.warning(f"Chat {chat_id}: Received message in contact_handler without contact attribute.")
        # Optionally, resend the request for contact if appropriate
        bot.send_message(chat_id, "Не удалось получить номер телефона. Пожалуйста, попробуйте снова нажать на кнопку 'Поделиться номером'.")
        return

    phone_number = message.contact.phone_number
    logger.info(f"Chat {chat_id}: Contact received, phone number: {phone_number}")

    # Remove the ReplyKeyboardRemove markup after contact is shared
    remove_markup = types.ReplyKeyboardRemove()
    bot.send_message(chat_id, f"Спасибо! Ваш номер {phone_number} получен. Обрабатываю...", reply_markup=remove_markup)


    try:
        db_user_details, error_message = authorize_user(phone_number)

        if error_message:
            logger.warning(f"Chat {chat_id}: Authorization error for {phone_number}: {error_message}")
            bot.send_message(chat_id, error_message)
             # After an error, good to offer to share contact again or /start
            start_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            button_phone = types.KeyboardButton(text="Поделиться номером", request_contact=True)
            start_keyboard.add(button_phone)
            bot.send_message(chat_id, "Пожалуйста, попробуйте авторизоваться снова.", reply_markup=start_keyboard)

        elif db_user_details:
            # Store all relevant user details from DB into user_data
            user_data[chat_id] = {
                'id': db_user_details.get('id'), # Crucial: Ensure 'id' is the user's DB primary key
                'store_id': db_user_details.get('store_id'), # Crucial: Ensure 'store_id' is present
                'name': db_user_details.get('name'),
                'surename': db_user_details.get('surename'), # Note: typo 'surename', usually 'surname'
                'role': db_user_details.get('role'),
                'phone_number': phone_number, # Store the phone number used for auth
                'is_active': db_user_details.get('is_active', 1) # Default to active if not specified
            }
            logger.info(f"Chat {chat_id}: User {phone_number} (DB ID: {user_data[chat_id]['id']}) authorized successfully. Role: {user_data[chat_id]['role']}.")

            # --- NEW: Update user's chat_id in DB ---
            # from database import update_user_chat_id # Local import to avoid circular dependency
            # success, msg = update_user_chat_id(user_data[chat_id]['id'], chat_id)
            # if not success:
            #     logger.warning(f"Chat {chat_id}: Failed to update chat_id for user {user_data[chat_id]['id']}: {msg}")
            # --- END NEW ---
            reply_text = f"Здравствуйте, {user_data[chat_id].get('name', '')} {user_data[chat_id].get('surename', '')}!\n"
            reply_text += f"Вы авторизованы как {user_data[chat_id]['role']}."
            # reply_text += "\nДля справки введите команду /help." # Moved help to a separate command

            if user_data[chat_id]['role'] == 'cashier':
                keyboard = create_cashier_buttons()
            elif user_data[chat_id]['role'] == 'admin':
                keyboard = create_admin_buttons()
            else:
                logger.warning(f"Chat {chat_id}: User {phone_number} has unknown role: {user_data[chat_id]['role']}")
                keyboard = types.ReplyKeyboardRemove() # Or a "no access" keyboard
                reply_text = f"Здравствуйте! Ваша роль '{user_data[chat_id]['role']}' не позволяет использовать этого бота."
                # Clear user_data if role is invalid to prevent further access
                # del user_data[chat_id] # Or mark as inactive in memory

            bot.send_message(chat_id, reply_text, reply_markup=keyboard)
        else:
            # This case should ideally be covered by authorize_user returning an error_message
            logger.error(f"Chat {chat_id}: Authorization for {phone_number} failed with no specific error message but no user_data either.")
            bot.send_message(chat_id, "Произошла неизвестная ошибка при авторизации. Попробуйте еще раз.")
            # Offer to share contact again
            start_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            button_phone = types.KeyboardButton(text="Поделиться номером", request_contact=True)
            start_keyboard.add(button_phone)
            bot.send_message(chat_id, "Пожалуйста, попробуйте авторизоваться снова.", reply_markup=start_keyboard)

    except Exception as e:
        logger.exception(f"Chat {chat_id}: Exception during contact handling for {phone_number}.")
        bot.send_message(chat_id, "Произошла серьезная ошибка при обработке вашего запроса. Пожалуйста, сообщите администратору.")


def register_start_handlers():
    # Note: bot is imported from state.py
    bot.register_message_handler(start_command_handler, commands=['start'], pass_bot=False)
    bot.register_message_handler(contact_handler, content_types=['contact'], pass_bot=False)
    logger.info("Start and contact handlers registered.")