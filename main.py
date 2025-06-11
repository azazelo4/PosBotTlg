import logging
from telebot import types
from buttons import (
    create_cash_operations_buttons, create_staff_management_buttons, 
    create_admin_buttons, create_cashier_buttons, create_manager_buttons, # Added manager
    create_stock_management_buttons # Added stock
)
from state import bot, user_data, user_state
from handlers.report_handler import register_report_handlers, report_handler as global_report_handler_instances
from handlers.start_handler import register_start_handlers
from handlers.help_handler import register_help_handlers
from handlers.sales_handler import register_sales_handlers, sale_handler as global_sale_handler_instances
from handlers.buyback_handler import register_buyback_handlers
from handlers.return_handler import register_return_handlers
from handlers.tradein_handler import register_tradein_handlers
from handlers.stock_handler import register_stock_handlers # Placeholder for when stock_handler.py is created
from database import get_all_users, change_user_role_function
from user_management import configure_user_management, UserManagement

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("app_debug.log", mode='a'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

user_management_instance_global: UserManagement = None

def get_keyboard_by_role(chat_id):
    user_info = user_data.get(chat_id)
    if not user_info or 'role' not in user_info or user_info.get('is_active') == 0:
        logger.warning(f"Role/active status issue for chat_id {chat_id}. User_data: {user_info}")
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add(types.KeyboardButton("Поделиться номером", request_contact=True))
        return kb

    role = user_info['role']
    if role == 'cashier': return create_cashier_buttons()
    elif role == 'admin': return create_admin_buttons()
    elif role == 'manager': return create_manager_buttons() # New role
    else:
        logger.warning(f"Unknown role '{role}' for chat_id {chat_id}.")
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add(types.KeyboardButton("Поделиться номером (смена роли?)", request_contact=True))
        return kb

@bot.message_handler(func=lambda message: message.text == "Касса")
def handle_cash_operations_menu(message):
    # ... (remains the same)
    chat_id = message.chat.id
    current_user_data = user_data.get(chat_id)
    if current_user_data and current_user_data.get('is_active', 0) == 1 and current_user_data.get('role') in ['admin', 'cashier', 'manager']: # Manager can also access Касса
        logger.info(f"Chat {chat_id} (Role: {current_user_data.get('role')}): Accessing Cash Operations Menu.")
        bot.send_message(chat_id, "Меню кассовых операций. Выберите действие:", reply_markup=create_cash_operations_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Cash Operations Menu. User data: {current_user_data}")
        bot.send_message(chat_id, "Доступ запрещен. Пожалуйста, авторизуйтесь через /start.", reply_markup=get_keyboard_by_role(chat_id))


# New handler for "Склад" button
@bot.message_handler(func=lambda message: message.text == "Склад")
def handle_stock_menu(message: types.Message):
    chat_id = message.chat.id
    current_user = user_data.get(chat_id)
    if current_user and current_user.get('is_active') and current_user.get('role') in ['admin', 'manager']:
        logger.info(f"Chat {chat_id} (Role: {current_user.get('role')}): Accessing Stock Management Menu.")
        # Clear any previous stock FSM state if re-entering
        current_main_state = user_state.get(chat_id)
        if current_main_state and current_main_state.startswith("STOCK_"):
            user_state.pop(chat_id, None)
            # also clear specific data from user_data[chat_id]['stock_op_data'] if used
            if chat_id in user_data and 'stock_op_data' in user_data[chat_id]:
                del user_data[chat_id]['stock_op_data']
            logger.debug(f"Chat {chat_id}: Cleared previous stock operation state.")
        bot.send_message(chat_id, "Меню управления складом:", reply_markup=create_stock_management_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access to Stock menu. User: {current_user}")
        bot.send_message(chat_id, "Доступ к складу запрещен.", reply_markup=get_keyboard_by_role(chat_id))


@bot.message_handler(func=lambda message: message.text == "Назад")
def go_back_to_main_menu(message: types.Message):
    # ... (remains largely the same, ensure USER_MANAGEMENT_USER_LIST and USER_MANAGEMENT_ACTION_SELECT are handled)
    chat_id = message.chat.id
    user = user_data.get(chat_id)
    current_main_state = user_state.get(chat_id) 

    logger.info(f"Chat {chat_id}: 'Назад' pressed. Current global state: {current_main_state}")

    if current_main_state:
        if current_main_state in ['USER_MANAGEMENT_USER_LIST', 'USER_MANAGEMENT_ACTION_SELECT'] and user_management_instance_global:
            logger.info(f"Chat {chat_id}: 'Назад' from user management inline flow. Returning to staff menu.")
            user_state.pop(chat_id, None) 
            # Try to delete the inline message if we had its ID
            # For now, just send the staff menu.
            bot.send_message(chat_id, "Операция с правами отменена/завершена.", reply_markup=create_staff_management_buttons())
            return
        # Add similar block for STOCK states if "Назад" should go to Stock Menu not Main Menu
        elif current_main_state.startswith("STOCK_"): # Example state prefix for stock operations
             # from handlers.stock_handler import cancel_stock_operation # Assuming it exists
             # cancel_stock_operation(chat_id) 
             # For now, generic cancel:
             user_state.pop(chat_id, None)
             if chat_id in user_data and 'stock_op_data' in user_data[chat_id]:
                del user_data[chat_id]['stock_op_data']
             bot.send_message(chat_id, "Операция со складом отменена.", reply_markup=create_stock_management_buttons()) # To stock menu
             return
        else:
            handle_global_cancel(message, called_from_back=True)
            return

    if not user or user.get('is_active', 0) == 0:
        # ... (no change)
        logger.info(f"Chat {chat_id}: 'Назад' pressed by non-authorized/inactive user.")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
        keyboard.add(share_contact_button)
        bot.send_message(chat_id, "Вы не авторизованы. Пожалуйста, пройдите авторизацию через /start.", reply_markup=keyboard)
        return

    logger.info(f"Chat {chat_id} (Role: {user.get('role')}): Returning to main menu.")
    bot.send_message(chat_id, "Возвращаемся в главное меню.", reply_markup=get_keyboard_by_role(chat_id))


@bot.message_handler(func=lambda message: message.text == "Персонал")
def handle_staff_menu_entry(message):
    # ... (remains the same, ensure check for UM_ states is robust)
    chat_id = message.chat.id
    current_user_data = user_data.get(chat_id)
    if current_user_data and current_user_data.get('is_active', 0) == 1 and current_user_data.get('role') == 'admin':
        logger.info(f"Chat {chat_id}: Admin accessing Staff Menu.")
        current_main_state = user_state.get(chat_id)
        if current_main_state and (current_main_state.startswith("UM_") or current_main_state.startswith("USER_MANAGEMENT")):
            user_state.pop(chat_id, None)
            if user_management_instance_global and chat_id in user_management_instance_global.current_operation_data: # Clear temp data
                del user_management_instance_global.current_operation_data[chat_id]
            logger.debug(f"Chat {chat_id}: Cleared previous user management state '{current_main_state}'.")
        bot.send_message(chat_id, "Меню управления персоналом. Выберите действие:", reply_markup=create_staff_management_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Staff Menu. User data: {current_user_data}")
        bot.send_message(chat_id, "Доступ запрещен. Команда доступна только администраторам.", reply_markup=get_keyboard_by_role(chat_id))


@bot.message_handler(func=lambda message: message.text == "Права" and \
                                   user_data.get(message.chat.id, {}).get('role') == 'admin' and \
                                   user_data.get(message.chat.id, {}).get('is_active', 0) == 1)
def handle_rights_button(message: types.Message):
    # ... (remains the same)
    chat_id = message.chat.id
    logger.info(f"Chat {chat_id}: 'Права' button pressed by admin.")
    if user_management_instance_global:
        user_management_instance_global.handle_role_management(chat_id)
    else:
        logger.error(f"Chat {chat_id}: user_management_instance_global is None. Cannot handle 'Права'.")
        bot.send_message(chat_id, "Ошибка: Модуль управления персоналом не инициализирован.")


@bot.message_handler(func=lambda message: message.text == "Отмена")
def handle_global_cancel(message: types.Message, called_from_back=False):
    # ... (Ensure STOCK_ states are handled, similar to UM_ states, returning to stock menu) ...
    chat_id = message.chat.id
    current_op_state = user_state.get(chat_id) 
    action_source = "'Отмена'" if not called_from_back else "'Назад' (приравнивается к отмене)"
    logger.info(f"Chat {chat_id}: {action_source} pressed. Current global state: {current_op_state}")

    operation_cancelled_by_specific_handler = False
    final_reply_markup = get_keyboard_by_role(chat_id)

    if not current_op_state:
        # ... (no change)
        if not called_from_back: 
            bot.send_message(chat_id, "Нет активных операций для отмены.", reply_markup=final_reply_markup)
        else: 
            logger.info(f"Chat {chat_id}: 'Назад' pressed from non-FSM screen, going to main menu (handled by go_back_to_main_menu).")
            if not called_from_back:
                 bot.send_message(chat_id, "Возвращаемся в главное меню.", reply_markup=final_reply_markup)
        return

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
    elif current_op_state.startswith("SALES_"):
        if chat_id in global_sale_handler_instances:
            global_sale_handler_instances[chat_id].cancel_sale()
        else: 
            user_state.pop(chat_id, None)
            if chat_id in user_data and 'sales_data' in user_data[chat_id]: del user_data[chat_id]['sales_data']
            bot.send_message(chat_id, "Продажа отменена.", reply_markup=final_reply_markup)
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("REPORT_"):
        if chat_id in global_report_handler_instances:
            global_report_handler_instances[chat_id].cancel_operation()
        else: 
            user_state.pop(chat_id, None)
            bot.send_message(chat_id, "Генерация отчета отменена.", reply_markup=final_reply_markup)
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("UM_") or current_op_state.startswith("USER_MANAGEMENT"):
        user_state.pop(chat_id, None) 
        if user_management_instance_global and chat_id in user_management_instance_global.current_operation_data:
            del user_management_instance_global.current_operation_data[chat_id]
        logger.info(f"Chat {chat_id}: User management operation ('{current_op_state}') cancelled.")
        bot.send_message(chat_id, "Операция с персоналом отменена.", reply_markup=create_staff_management_buttons())
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("STOCK_"): # New for stock operations
        user_state.pop(chat_id, None)
        if chat_id in user_data and 'stock_op_data' in user_data[chat_id]:
            del user_data[chat_id]['stock_op_data']
        # from handlers.stock_handler import stock_op_instances # If stock_handler uses instance dict
        # if chat_id in stock_op_instances: del stock_op_instances[chat_id]
        logger.info(f"Chat {chat_id}: Stock operation ('{current_op_state}') cancelled.")
        bot.send_message(chat_id, "Операция со складом отменена.", reply_markup=create_stock_management_buttons()) # To stock menu
        operation_cancelled_by_specific_handler = True
    
    if not operation_cancelled_by_specific_handler and current_op_state:
        user_state.pop(chat_id, None)
        if chat_id in user_data and 'operation_data' in user_data[chat_id]:
            del user_data[chat_id]['operation_data']
        logger.info(f"Chat {chat_id}: Generic operation state '{current_op_state}' cancelled.")
        bot.send_message(chat_id, "Операция отменена.", reply_markup=final_reply_markup)
    

def main():
    global user_management_instance_global
    logger.info("Bot starting...")
    register_start_handlers()
    register_help_handlers()
    user_management_instance_global = configure_user_management(bot, user_data, get_all_users, change_user_role_function)
    register_sales_handlers(bot, user_data, user_state)
    register_report_handlers(bot, user_data, user_state)
    register_buyback_handlers()
    register_return_handlers()
    register_tradein_handlers()
    # from handlers.stock_handler import register_stock_handlers # Uncomment when created
    # register_stock_handlers(bot, user_data, user_state)      # Uncomment when created

    logger.info("Bot polling started...")
    try:
        bot.polling(none_stop=True, interval=0, timeout=30)
    except Exception as e:
        logger.critical(f"Bot polling crashed: {e}", exc_info=True)
    finally:
        logger.info("Bot polling stopped.")

if __name__ == '__main__':
    main()