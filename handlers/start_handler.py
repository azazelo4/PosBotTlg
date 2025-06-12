import telebot # telebot is not directly used here, types is. Consider removing if not needed elsewhere in future.
from telebot import types
from auth import authorize_user # authorize_user expects raw number
from buttons import create_cashier_buttons, create_admin_buttons, create_manager_buttons
from state import bot, user_data, user_state # user_state is not used in this file
import logging

logger = logging.getLogger(__name__)

# Function to get main keyboard based on role (placeholder, actual definition in main.py)
# This is here for conceptual completeness if this module were standalone.
# In the actual project, main.py's get_keyboard_by_role would be used.
def get_main_keyboard_for_role(chat_id: int) -> types.ReplyKeyboardMarkup | types.ReplyKeyboardRemove:
    """Helper to get the main keyboard, ideally this logic is centralized in main.py"""
    from main import get_keyboard_by_role # Dynamic import to avoid circular dependency
    return get_keyboard_by_role(chat_id)


def start_command_handler(message: types.Message):
    chat_id = message.chat.id
    logger.info(f"Chat {chat_id}: /start command received.")
    user_state.pop(chat_id, None) # Clear any previous FSM state for this user
    # Clean up user_data for this chat_id if they are re-starting
    if chat_id in user_data:
        # Preserve essential data if needed, or clear entirely
        # For a full /start, usually, we re-authenticate, so clearing is often appropriate
        # Be careful if there's ongoing work that shouldn't be lost.
        # For now, let's assume /start means fresh auth.
        # user_data.pop(chat_id, None) # This would log them out completely.
        # Let's keep existing user_data for now, contact_handler will overwrite it on successful auth.
        pass


    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    button_phone = types.KeyboardButton(text="Поделиться номером", request_contact=True)
    keyboard.add(button_phone)
    bot.send_message(chat_id, "Здравствуйте! Пожалуйста, поделитесь своим номером телефона для авторизации:", reply_markup=keyboard)

def contact_handler(message: types.Message):
    chat_id = message.chat.id
    if message.contact is None or not message.contact.phone_number:
        logger.warning(f"Chat {chat_id}: Received message in contact_handler without valid contact.message_id: {message.message_id}")
        bot.send_message(chat_id, "Не удалось получить номер телефона. Пожалуйста, попробуйте снова нажать на кнопку 'Поделиться номером'.")
        # Offer the button again
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        button_phone = types.KeyboardButton(text="Поделиться номером", request_contact=True)
        keyboard.add(button_phone)
        bot.send_message(chat_id, "Нажмите кнопку ниже:",reply_markup=keyboard)
        return

    phone_number_from_contact = message.contact.phone_number # This is the "raw" number from contact
    logger.info(f"Chat {chat_id}: Contact received. Raw phone number: '{phone_number_from_contact}'. User: {message.from_user.username or message.from_user.id}")

    # Remove the "Share contact" keyboard
    remove_markup = types.ReplyKeyboardRemove()
    bot.send_message(chat_id, f"Спасибо! Ваш номер {phone_number_from_contact} получен. Проверяю авторизацию...", reply_markup=remove_markup)

    try:
        # authorize_user expects "raw" number and normalizes it internally via get_user_by_phone
        db_user_details, error_message = authorize_user(phone_number_from_contact)

        if error_message:
            logger.warning(f"Chat {chat_id}: Authorization error for raw phone '{phone_number_from_contact}': {error_message}")
            bot.send_message(chat_id, error_message)
            # Offer /start or the contact button again
            start_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            button_phone_retry = types.KeyboardButton(text="Поделиться номером", request_contact=True)
            start_keyboard.add(button_phone_retry)
            bot.send_message(chat_id, "Пожалуйста, попробуйте авторизоваться снова или обратитесь к администратору.", reply_markup=start_keyboard)

        elif db_user_details:
            # db_user_details['phone_number'] should be the normalized number from DB
            user_data[chat_id] = {
                'id': db_user_details.get('id'),
                'store_id': db_user_details.get('store_id'),
                'name': db_user_details.get('name'),
                # 'surename': db_user_details.get('surename'), # Handle if 'surename' is consistently available
                'role': db_user_details.get('role'),
                'phone_number': db_user_details.get('phone_number'), # Store normalized number
                'is_active': db_user_details.get('is_active', 0) # Default to 0 if not present, though DB should have it
            }
            # Clear any FSM state from a previous session for this user
            user_state.pop(chat_id, None)

            logger.info(f"Chat {chat_id}: User '{user_data[chat_id]['phone_number']}' (DB ID: {user_data[chat_id]['id']}) authorized. Role: '{user_data[chat_id]['role']}', Active: {user_data[chat_id]['is_active']}.")

            if user_data[chat_id]['is_active'] == 0: # Double check, though authorize_user should catch this
                logger.warning(f"Chat {chat_id}: User '{user_data[chat_id]['phone_number']}' is marked inactive in DB but passed authorize_user checks somehow. Denying access.")
                bot.send_message(chat_id, "Ваш аккаунт деактивирован. Пожалуйста, обратитесь к администратору.")
                # Offer /start or the contact button again
                start_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
                button_phone_retry = types.KeyboardButton(text="Поделиться номером", request_contact=True)
                start_keyboard.add(button_phone_retry)
                bot.send_message(chat_id,"Попробуйте войти с другого номера или обратитесь к администратору.",reply_markup=start_keyboard)
                user_data.pop(chat_id, None) # Log them out from bot's perspective
                return

            user_name_display = user_data[chat_id].get('name', 'Пользователь')
            reply_text = f"Здравствуйте, {user_name_display}!\n"
            reply_text += f"Вы авторизованы как {user_data[chat_id]['role']}."

            final_keyboard = get_main_keyboard_for_role(chat_id) # Use the centralized keyboard getter

            if user_data[chat_id]['role'] not in ['cashier', 'admin', 'manager']:
                 logger.warning(f"Chat {chat_id}: User '{user_data[chat_id]['phone_number']}' has unknown/unsupported role: '{user_data[chat_id]['role']}'")
                 reply_text = f"Здравствуйте, {user_name_display}! Ваша роль '{user_data[chat_id]['role']}' не поддерживается в данный момент."
                 # final_keyboard will be ReplyKeyboardRemove due to get_keyboard_by_role's else.
                 # Or, explicitly remove user_data if unsupported roles should not be stored.
                 # user_data.pop(chat_id, None)

            bot.send_message(chat_id, reply_text, reply_markup=final_keyboard)
        else:
            # This case should ideally be covered by error_message from authorize_user
            logger.error(f"Chat {chat_id}: Authorization for raw phone '{phone_number_from_contact}' failed unexpectedly (no db_user_details, no error_message).")
            bot.send_message(chat_id, "Произошла неизвестная ошибка при авторизации. Попробуйте еще раз.")
            start_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            button_phone_retry = types.KeyboardButton(text="Поделиться номером", request_contact=True)
            start_keyboard.add(button_phone_retry)
            bot.send_message(chat_id, "Нажмите кнопку ниже:",reply_markup=start_keyboard)

    except Exception as e:
        logger.exception(f"Chat {chat_id}: Critical exception during contact handling for raw phone '{phone_number_from_contact}'.")
        bot.send_message(chat_id, "Произошла серьезная ошибка при обработке вашего запроса. Пожалуйста, сообщите администратору.")
        # Optionally, offer /start again
        start_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        button_phone_retry = types.KeyboardButton(text="Поделиться номером", request_contact=True)
        start_keyboard.add(button_phone_retry)
        bot.send_message(chat_id, "Попробуйте начать заново:",reply_markup=start_keyboard)


def register_start_handlers():
    # Pass_bot=False is default, explicitly stating it is fine.
    bot.register_message_handler(start_command_handler, commands=['start'], pass_bot=False)
    bot.register_message_handler(contact_handler, content_types=['contact'], pass_bot=False)
    logger.info("Start and contact handlers registered.")