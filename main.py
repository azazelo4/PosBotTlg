import logging
from telebot import types
from buttons import create_cash_operations_buttons, create_staff_management_buttons, create_admin_buttons, create_cashier_buttons
from state import bot, user_data, user_state
from handlers.report_handler import register_report_handlers, report_handler as global_report_handler_instances
from handlers.start_handler import register_start_handlers
from handlers.help_handler import register_help_handlers
from handlers.sales_handler import register_sales_handlers, sale_handler as global_sale_handler_instances
from handlers.buyback_handler import register_buyback_handlers
from handlers.return_handler import register_return_handlers
from handlers.tradein_handler import register_tradein_handlers
from database import get_all_users, change_user_role_function
from user_management import configure_user_management, UserManagement # Import UserManagement for type hint

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

user_management_instance_global: UserManagement = None # Hinting the type

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
    current_main_state = user_state.get(chat_id) # This is the global state from state.py

    logger.info(f"Chat {chat_id}: 'Назад' pressed. Current global state: {current_main_state}")

    # If "Назад" is pressed during an operation, it should cancel it.
    if current_main_state:
        # Check if the state belongs to UserManagement's inline keyboard flow
        if current_main_state == 'USER_MANAGEMENT' and user_management_instance_global:
            logger.info(f"Chat {chat_id}: 'Назад' pressed during USER_MANAGEMENT state. Clearing inline keyboard and returning to staff menu.")
            # Try to remove the inline keyboard message if possible (might need message_id storage)
            # For now, just send the staff menu.
            user_state.pop(chat_id, None) # Clear the 'USER_MANAGEMENT' state
            bot.send_message(chat_id, "Операция с правами отменена.", reply_markup=create_staff_management_buttons())
            return
        else:
            # For other states, defer to global cancel
            handle_global_cancel(message, called_from_back=True)
            return

    # If no specific state, or after cancellation brought us here with no state.
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
def handle_staff_menu_entry(message): # Renamed to avoid conflict if UserManagement also has this
    chat_id = message.chat.id
    current_user_data = user_data.get(chat_id)
    if current_user_data and current_user_data.get('is_active', 0) == 1 and current_user_data.get('role') == 'admin':
        logger.info(f"Chat {chat_id}: Admin accessing Staff Menu.")
        # Clear any previous staff-related FSM state when re-entering "Персонал" menu
        current_main_state = user_state.get(chat_id)
        if current_main_state and (current_main_state.startswith("UM_") or current_main_state == 'USER_MANAGEMENT'):
            user_state.pop(chat_id, None)
            logger.debug(f"Chat {chat_id}: Cleared previous user management state '{current_main_state}'.")
        bot.send_message(chat_id, "Меню управления персоналом. Выберите действие:", reply_markup=create_staff_management_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Staff Menu. User data: {current_user_data}")
        bot.send_message(chat_id, "Доступ запрещен. Команда доступна только администраторам.", reply_markup=get_keyboard_by_role(chat_id))

# Explicit handler for "Права" button in main.py
@bot.message_handler(func=lambda message: message.text == "Права" and \
                                   user_data.get(message.chat.id, {}).get('role') == 'admin' and \
                                   user_data.get(message.chat.id, {}).get('is_active', 0) == 1)
def handle_rights_button(message: types.Message):
    chat_id = message.chat.id
    logger.info(f"Chat {chat_id}: 'Права' button pressed by admin.")
    if user_management_instance_global:
        user_management_instance_global.handle_role_management(chat_id)
    else:
        logger.error(f"Chat {chat_id}: user_management_instance_global is None. Cannot handle 'Права'.")
        bot.send_message(chat_id, "Ошибка: Модуль управления персоналом не инициализирован.")


@bot.message_handler(func=lambda message: message.text == "Отмена")
def handle_global_cancel(message: types.Message, called_from_back=False):
    chat_id = message.chat.id
    current_op_state = user_state.get(chat_id) # Global state from state.py
    action_source = "'Отмена'" if not called_from_back else "'Назад' (приравнивается к отмене)"
    logger.info(f"Chat {chat_id}: {action_source} pressed. Current global state: {current_op_state}")

    operation_cancelled_by_specific_handler = False
    final_reply_markup = get_keyboard_by_role(chat_id)

    if not current_op_state:
        if not called_from_back: # Explicit "Отмена" with no state
            bot.send_message(chat_id, "Нет активных операций для отмены.", reply_markup=final_reply_markup)
        else: # "Назад" with no state
            logger.info(f"Chat {chat_id}: 'Назад' pressed from non-FSM screen, going to main menu (handled by go_back_to_main_menu).")
            # go_back_to_main_menu will handle sending main menu if it called this
            if not called_from_back: # Should not happen if logic is correct, but safeguard
                 bot.send_message(chat_id, "Возвращаемся в главное меню.", reply_markup=final_reply_markup)
        return

    # --- Specific operation cancellations ---
    if current_op_state.startswith("BUYBACK_"):
        from handlers.buyback_handler import cancel_buyback
        cancel_buyback(chat_id)
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("RETURN_"):
        from handlers.return_handler import cancel_return
        cancel_return(chat_id)
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("TRADEIN_"):
        from handlers.tradein_handler import cancel_tradein
        cancel_tradein(chat_id)
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("SALES_"): # Note: SaleState enum uses "SALES_" prefix
        if chat_id in global_sale_handler_instances:
            global_sale_handler_instances[chat_id].cancel_sale() # This should clear global state and its own dict entry
        else: # Fallback if instance missing
            user_state.pop(chat_id, None)
            bot.send_message(chat_id, "Продажа отменена.", reply_markup=final_reply_markup)
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("REPORT_"):
        if chat_id in global_report_handler_instances:
            global_report_handler_instances[chat_id].cancel_operation() # This clears global state and its dict entry
        else: # Fallback
            user_state.pop(chat_id, None)
            bot.send_message(chat_id, "Генерация отчета отменена.", reply_markup=final_reply_markup)
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("UM_") or current_op_state == 'USER_MANAGEMENT':
        user_state.pop(chat_id, None) # Clear global state
        # user_management_instance_global internal state is not used for FSM anymore
        logger.info(f"Chat {chat_id}: User management operation ('{current_op_state}') cancelled.")
        bot.send_message(chat_id, "Операция с персоналом отменена.", reply_markup=create_staff_management_buttons())
        operation_cancelled_by_specific_handler = True
    
    # --- Fallback if no specific handler matched but state existed ---
    if not operation_cancelled_by_specific_handler and current_op_state:
        user_state.pop(chat_id, None)
        if chat_id in user_data and 'operation_data' in user_data[chat_id]: # Generic cleanup
            del user_data[chat_id]['operation_data']
        logger.info(f"Chat {chat_id}: Generic operation state '{current_op_state}' cancelled.")
        bot.send_message(chat_id, "Операция отменена.", reply_markup=final_reply_markup)
    
    # If called from "Назад" and an operation was cancelled, the specific cancel function already replied.
    # If "Отмена" was pressed and an operation was cancelled, specific cancel function replied.
    # If "Отмена" was pressed, no state, already handled.


def main():
    global user_management_instance_global

    logger.info("Bot starting...")

    register_start_handlers()
    register_help_handlers()

    # Configure UserManagement and store the instance globally
    # This instance will be used by the "Права" button handler in main.py
    # and its callback handlers will be registered on the shared `bot`.
    user_management_instance_global = configure_user_management(
        bot, 
        user_data, 
        get_all_users, 
        change_user_role_function
    )

    register_sales_handlers(bot, user_data, user_state)
    register_report_handlers(bot, user_data, user_state)
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