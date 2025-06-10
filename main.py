import logging
from telebot import types
from buttons import create_cash_operations_buttons, create_staff_management_buttons, create_admin_buttons, create_cashier_buttons
from state import bot, user_data, user_state
from handlers.report_handler import register_report_handlers, report_handler as global_report_handler_instances # Import the instance dict
from handlers.start_handler import register_start_handlers
from handlers.help_handler import register_help_handlers
from handlers.sales_handler import register_sales_handlers, sale_handler as global_sale_handler_instances # Import the instance dict
from handlers.buyback_handler import register_buyback_handlers
from handlers.return_handler import register_return_handlers
from handlers.tradein_handler import register_tradein_handlers
from database import get_all_users, change_user_role_function # Removed deactivate_user_in_db as it's not directly used here
from user_management import configure_user_management, UserManagement # For type hinting or direct calls if needed

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app_debug.log", mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# This global instance will be set by configure_user_management
user_management_instance_global: UserManagement = None

def get_keyboard_by_role(chat_id):
    user_info = user_data.get(chat_id)
    if not user_info or 'role' not in user_info or user_info.get('is_active') == 0:
        logger.warning(f"Role not found or user inactive for chat_id {chat_id} in get_keyboard_by_role. User_data: {user_info}")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
        keyboard.add(share_contact_button)
        return keyboard

    role = user_info['role']
    if role == 'cashier':
        return create_cashier_buttons()
    elif role == 'admin':
        return create_admin_buttons()
    else:
        logger.warning(f"Unknown role '{role}' for chat_id {chat_id} in get_keyboard_by_role.")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером (смена роли?)", request_contact=True)
        keyboard.add(share_contact_button)
        return keyboard


@bot.message_handler(func=lambda message: message.text == "Касса")
def handle_cash_operations_menu(message):
    chat_id = message.chat.id
    current_user_data = user_data.get(chat_id)
    if current_user_data and current_user_data.get('is_active', 0) == 1 and current_user_data.get('role') in ['admin', 'cashier']:
        logger.info(f"Chat {chat_id} (Role: {current_user_data.get('role')}): Accessing Cash Operations Menu.")
        bot.send_message(chat_id, "Меню кассовых операций. Выберите действие:", reply_markup=create_cash_operations_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Cash Operations Menu. User data: {current_user_data}")
        bot.send_message(chat_id, "Доступ запрещен. Пожалуйста, авторизуйтесь через /start.", reply_markup=get_keyboard_by_role(chat_id))


@bot.message_handler(func=lambda message: message.text == "Назад")
def go_back_to_main_menu(message: types.Message):
    chat_id = message.chat.id
    user = user_data.get(chat_id)
    current_state = user_state.get(chat_id)

    logger.info(f"Chat {chat_id}: 'Назад' pressed. Current state: {current_state}")

    # If 'Назад' is pressed during a specific operation, it should behave like 'Отмена' for that operation
    if current_state:
        # Call the global cancel handler to ensure consistent state clearing
        handle_global_cancel(message, called_from_back=True)
        return # handle_global_cancel will send the appropriate message and keyboard

    # If no specific state, just go to main menu based on role
    if not user or user.get('is_active', 0) == 0:
        logger.info(f"Chat {chat_id}: 'Назад' pressed by non-authorized/inactive user.")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
        keyboard.add(share_contact_button)
        bot.send_message(chat_id, "Вы не авторизованы. Пожалуйста, пройдите авторизацию через /start.", reply_markup=keyboard)
        return

    logger.info(f"Chat {chat_id} (Role: {user.get('role')}): Returning to main menu.")
    bot.send_message(chat_id, "Возвращаемся в главное меню.", reply_markup=get_keyboard_by_role(chat_id))


@bot.message_handler(func=lambda message: message.text == "Персонал")
def handle_staff_menu(message):
    chat_id = message.chat.id
    current_user_data = user_data.get(chat_id)
    if current_user_data and current_user_data.get('is_active', 0) == 1 and current_user_data.get('role') == 'admin':
        logger.info(f"Chat {chat_id}: Admin accessing Staff Menu.")
        # Clear any sub-states of user management if user clicks "Персонал" again
        if user_state.get(chat_id) in ['ADDING_USER', 'DELETING_USER', 'USER_MANAGEMENT']:
            user_state.pop(chat_id, None)
            if user_management_instance_global: # and hasattr(user_management_instance_global, 'reset_state_for_chat'):
                 # user_management_instance_global.reset_state_for_chat(chat_id) # Ideal
                 # Fallback if UserManagement doesn't have a specific reset
                 if chat_id in user_management_instance_global.user_state:
                     del user_management_instance_global.user_state[chat_id]


        bot.send_message(chat_id, "Меню управления персоналом. Выберите действие:", reply_markup=create_staff_management_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Staff Menu. User data: {current_user_data}")
        bot.send_message(chat_id, "Доступ запрещен. Команда доступна только администраторам.", reply_markup=get_keyboard_by_role(chat_id))

# Universal cancel handler
@bot.message_handler(func=lambda message: message.text == "Отмена")
def handle_global_cancel(message: types.Message, called_from_back=False):
    chat_id = message.chat.id
    current_op_state = user_state.get(chat_id)
    action_source = "'Отмена'" if not called_from_back else "'Назад' (приравнивается к отмене)"
    logger.info(f"Chat {chat_id}: {action_source} pressed. Current state: {current_op_state}")

    operation_cancelled = False
    final_reply_markup = get_keyboard_by_role(chat_id) # Default to main menu

    if not current_op_state and not called_from_back:
        bot.send_message(chat_id, "Нет активных операций для отмены.", reply_markup=final_reply_markup)
        return
    if not current_op_state and called_from_back: # 'Назад' pressed from a non-FSM screen
        logger.info(f"Chat {chat_id}: 'Назад' pressed from non-FSM screen, going to main menu.")
        bot.send_message(chat_id, "Возвращаемся в главное меню.", reply_markup=final_reply_markup)
        return


    # Specific handlers first
    if current_op_state:
        if current_op_state.startswith("BUYBACK_"):
            from handlers.buyback_handler import cancel_buyback
            cancel_buyback(chat_id) # This sends its own message and markup
            operation_cancelled = True
        elif current_op_state.startswith("RETURN_"):
            from handlers.return_handler import cancel_return
            cancel_return(chat_id)
            operation_cancelled = True
        elif current_op_state.startswith("TRADEIN_"):
            from handlers.tradein_handler import cancel_tradein
            cancel_tradein(chat_id)
            operation_cancelled = True
        elif current_op_state.startswith("SALES_") or current_op_state.startswith("SaleState"):
            if chat_id in global_sale_handler_instances:
                global_sale_handler_instances[chat_id].reset()
                del global_sale_handler_instances[chat_id]
            user_state.pop(chat_id, None) # Ensure generic state is also popped
            if chat_id in user_data and 'sales_data' in user_data[chat_id]: del user_data[chat_id]['sales_data']
            logger.info(f"Chat {chat_id}: Sales operation cancelled.")
            bot.send_message(chat_id, "Продажа отменена.", reply_markup=final_reply_markup)
            operation_cancelled = True
        elif current_op_state.startswith("REPORT_"):
            if chat_id in global_report_handler_instances:
                global_report_handler_instances[chat_id].reset()
                del global_report_handler_instances[chat_id]
            user_state.pop(chat_id, None)
            logger.info(f"Chat {chat_id}: Report generation cancelled.")
            bot.send_message(chat_id, "Генерация отчета отменена.", reply_markup=final_reply_markup)
            operation_cancelled = True
        elif current_op_state in ['ADDING_USER', 'DELETING_USER', 'USER_MANAGEMENT']:
            user_state.pop(chat_id, None)
            # Reset internal state of UserManagement if it maintains one for the chat_id
            if user_management_instance_global and hasattr(user_management_instance_global, 'user_state') and chat_id in user_management_instance_global.user_state:
                 del user_management_instance_global.user_state[chat_id]
            logger.info(f"Chat {chat_id}: User management operation cancelled.")
            bot.send_message(chat_id, "Операция с персоналом отменена.", reply_markup=create_staff_management_buttons()) # Back to staff menu
            operation_cancelled = True
        
        # Fallback for any other state not explicitly handled above
        if not operation_cancelled and current_op_state:
            user_state.pop(chat_id, None)
            # Clean up generic 'operation_data' if it exists
            if chat_id in user_data and 'operation_data' in user_data[chat_id]:
                del user_data[chat_id]['operation_data']
            logger.info(f"Chat {chat_id}: Generic operation state '{current_op_state}' cancelled.")
            bot.send_message(chat_id, "Операция отменена.", reply_markup=final_reply_markup)
            operation_cancelled = True

    if not operation_cancelled and called_from_back:
        # This means 'Назад' was pressed, no specific FSM was active, so just go to main menu.
        logger.info(f"Chat {chat_id}: 'Назад' pressed, no active FSM. Returning to main menu.")
        bot.send_message(chat_id, "Возвращаемся в главное меню.", reply_markup=final_reply_markup)


def main():
    global user_management_instance_global

    logger.info("Bot starting...")

    register_start_handlers()
    register_help_handlers()

    user_management_instance_global = configure_user_management(bot, user_data, get_all_users, change_user_role_function)

    register_sales_handlers(bot, user_data)
    register_report_handlers(bot, user_data, user_state) # Pass user_state to report_handler if it needs to set global states
    register_buyback_handlers()
    register_return_handlers()
    register_tradein_handlers()

    logger.info("Bot polling started...")
    try:
        bot.polling(none_stop=True, interval=0, timeout=30)
    except Exception as e:
        logger.critical(f"Bot polling crashed: {e}", exc_info=True)
    finally:
        logger.info("Bot polling stopped.")

if __name__ == '__main__':
    main()