import logging
from telebot import types
from buttons import create_cash_operations_buttons, create_staff_management_buttons, create_admin_buttons, create_cashier_buttons # Added cashier buttons
from state import bot, user_data, user_state # Import from state.py
from handlers.report_handler import register_report_handlers
# Ensure start_handler is structured to not redefine bot, user_data, or uses them from state.py
from handlers.start_handler import register_start_handlers # Assuming start_handler has a registration function
from handlers.sales_handler import register_sales_handlers
from handlers.buyback_handler import register_buyback_handlers
from handlers.return_handler import register_return_handlers
from handlers.tradein_handler import register_tradein_handlers
from database import get_all_users, change_user_role_function, deactivate_user_in_db # Added deactivate_user_in_db
from user_management import (
    configure_user_management,
    # UserManagement # Not directly used here if configure_user_management handles all
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app_debug.log", mode='a'), # Append mode
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# This function is used by other handlers, ensure it's robust
def get_keyboard_by_role(chat_id):
    # Ensure user_data[chat_id] and user_data[chat_id]['role'] exist
    user_info = user_data.get(chat_id)
    if not user_info or 'role' not in user_info:
        logger.warning(f"Role not found for chat_id {chat_id} in get_keyboard_by_role. User_data: {user_info}")
        # Fallback to a default or request authorization
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
        keyboard.add(share_contact_button)
        return keyboard # Or None, or raise an error

    role = user_info['role']
    if role == 'cashier':
        return create_cashier_buttons() # create_cash_operations_buttons() is for inside "Касса"
    elif role == 'admin':
        return create_admin_buttons()
    else:
        logger.warning(f"Unknown role '{role}' for chat_id {chat_id} in get_keyboard_by_role.")
        # Fallback for unknown but authenticated roles
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        keyboard.add(types.KeyboardButton("Помощь"))
        return keyboard


@bot.message_handler(func=lambda message: message.text == "Касса")
def handle_cash_operations_menu(message):
    chat_id = message.chat.id
    # Check if user is in user_data and has an authorized role
    current_user_data = user_data.get(chat_id)
    if current_user_data and current_user_data.get('is_active', False) and current_user_data.get('role') in ['admin', 'cashier']:
        logger.info(f"Chat {chat_id} (Role: {current_user_data.get('role')}): Accessing Cash Operations Menu.")
        bot.send_message(chat_id, "Меню кассовых операций. Выберите действие:", reply_markup=create_cash_operations_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Cash Operations Menu. User data: {current_user_data}")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
        keyboard.add(share_contact_button)
        bot.send_message(chat_id, "Доступ запрещен. Пожалуйста, авторизуйтесь.", reply_markup=keyboard)


@bot.message_handler(func=lambda message: message.text == "Назад")
def go_back_to_main_menu(message):
    chat_id = message.chat.id
    user = user_data.get(chat_id)
    
    # Clear any ongoing operation state
    if user_state.get(chat_id):
        logger.info(f"Chat {chat_id}: 'Назад' pressed during state {user_state[chat_id]}. Clearing state.")
        # Specific cleanup functions for ongoing operations might be needed here if 'Назад' is a hard reset
        # For now, just clear the generic state. Specific flows should use "Отмена".
        if user_state[chat_id].startswith("SALES_") and 'sales_data' in user_data[chat_id]: del user_data[chat_id]['sales_data']
        if user_state[chat_id].startswith("BUYBACK_") and 'buyback_data' in user_data[chat_id]: del user_data[chat_id]['buyback_data']
        if user_state[chat_id].startswith("RETURN_") and 'return_data' in user_data[chat_id]: del user_data[chat_id]['return_data']
        if user_state[chat_id].startswith("TRADEIN_") and 'tradein_data' in user_data[chat_id]: del user_data[chat_id]['tradein_data']
        if user_state[chat_id].startswith("REPORT_") and 'report_data' in user_data[chat_id]: del user_data[chat_id]['report_data']
        # TODO: Add similar for user management states if they use user_data sub-dictionaries
        user_state.pop(chat_id, None)

    if not user or user.get('is_active') == 0:
        logger.info(f"Chat {chat_id}: 'Назад' pressed by non-authorized/inactive user.")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
        keyboard.add(share_contact_button)
        bot.send_message(chat_id, "Вы не авторизованы или ваш аккаунт деактивирован. Пожалуйста, поделитесь своим номером для авторизации.", reply_markup=keyboard)
        return
    
    logger.info(f"Chat {chat_id} (Role: {user.get('role')}): Returning to main menu.")
    bot.send_message(chat_id, "Возвращаемся в главное меню.", reply_markup=get_keyboard_by_role(chat_id))


@bot.message_handler(func=lambda message: message.text == "Персонал")
def handle_staff_menu(message):
    chat_id = message.chat.id
    current_user_data = user_data.get(chat_id)
    if current_user_data and current_user_data.get('is_active', False) and current_user_data.get('role') == 'admin':
        logger.info(f"Chat {chat_id}: Admin accessing Staff Menu.")
        bot.send_message(chat_id, "Меню управления персоналом. Выберите действие:", reply_markup=create_staff_management_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Staff Menu. User data: {current_user_data}")
        bot.send_message(chat_id, "Доступ запрещен. Команда доступна только администраторам.", reply_markup=get_keyboard_by_role(chat_id))

# Global "Отмена" handler for operations in progress
@bot.message_handler(func=lambda message: message.text == "Отмена")
def handle_global_cancel(message: types.Message):
    chat_id = message.chat.id
    current_op_state = user_state.get(chat_id)
    logger.debug(f"Chat {chat_id}: 'Отмена' pressed. Current state: {current_op_state}")

    # Check if in a specific operation state from any of the handlers
    # This list should be comprehensive of all states that can be cancelled.
    cancellable_prefixes = ["SALES_", "BUYBACK_", "RETURN_", "TRADEIN_", "REPORT_", "ADDUSER_", "DELUSER_", "CHANGEROLE_"] # Add user management prefixes

    cancelled = False
    for prefix in cancellable_prefixes:
        if current_op_state and current_op_state.startswith(prefix):
            # Clean up specific data if necessary
            if prefix == "BUYBACK_" and 'buyback_data' in user_data.get(chat_id, {}):
                from handlers.buyback_handler import cancel_buyback
                cancel_buyback(chat_id) # Uses its own cleanup and message
                cancelled = True
                break
            elif prefix == "RETURN_" and 'return_data' in user_data.get(chat_id, {}):
                from handlers.return_handler import cancel_return
                cancel_return(chat_id)
                cancelled = True
                break
            elif prefix == "TRADEIN_" and 'tradein_data' in user_data.get(chat_id, {}):
                from handlers.tradein_handler import cancel_tradein
                cancel_tradein(chat_id)
                cancelled = True
                break
            # Add more specific cancellations if needed (e.g., sales, reports)
            # Generic cleanup for other states if not handled by specific cancel functions
            else:
                 user_state.pop(chat_id, None)
                 # Generic data cleanup if a pattern is established, e.g. user_data[chat_id].pop('operation_specific_data', None)
                 bot.send_message(chat_id, "Операция отменена.", reply_markup=get_keyboard_by_role(chat_id))
                 cancelled = True
                 break
    
    if not cancelled:
        # If "Отмена" is pressed but not in a known cancellable state, treat as "Назад" or main menu
        logger.debug(f"Chat {chat_id}: 'Отмена' pressed, no active cancellable state found. Treating as 'Назад'.")
        go_back_to_main_menu(message)


# User Management handlers from user_management.py should be primarily used.
# The following are stubs if configure_user_management doesn't register them directly via bot instance.
# It's better if UserManagement class registers its own message handlers.
# For now, assuming configure_user_management sets up necessary callbacks or next_step_handlers.
# If UserManagement needs explicit message handlers here, they need to be more integrated.

# Example of how UserManagement might be triggered if it doesn't self-register text commands
# These might conflict if UserManagement itself registers handlers for these texts.
# @bot.message_handler(func=lambda message: message.text == "Добавить пользователя")
# def handle_add_user_proxy(message):
#     # Delegate to UserManagement instance if needed, or ensure UserManagement handles this text
#     # user_management_instance.trigger_add_user_flow(message) # Hypothetical
#     pass

# @bot.message_handler(func=lambda message: message.text == "Удалить пользователя")
# def handle_delete_user_proxy(message):
#     # Delegate
#     pass

# @bot.message_handler(func=lambda message: message.text == "Права")
# def handle_role_management_proxy(message):
#     # Delegate
#     pass


def main():
    logger.info("Bot starting...")
    
    # Initialize and register handlers from different modules
    register_start_handlers() # Handles /start and authorization
    
    # User management configuration
    # The UserManagement class in user_management.py seems to register its own handlers.
    # So, direct message_handler for "Добавить пользователя", etc., in main.py might be redundant
    # or should be removed if UserManagement handles them.
    # The functions get_all_users, change_user_role_function are passed.
    user_management_instance = configure_user_management(bot, user_data, get_all_users, change_user_role_function)
    # The callback_query_handlers related to user management are registered within configure_user_management or UserManagement class.

    register_sales_handlers()
    register_report_handlers()
    register_buyback_handlers()
    register_return_handlers()
    register_tradein_handlers()
    
    logger.info("Bot polling started...")
    try:
        bot.polling(none_stop=True, interval=0, timeout=20)
    except Exception as e:
        logger.critical(f"Bot polling crashed: {e}", exc_info=True)
    finally:
        logger.info("Bot polling stopped.")

if __name__ == '__main__':
    main()