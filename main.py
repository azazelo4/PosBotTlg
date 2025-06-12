import logging
from telebot import types
<<<<<<< HEAD
from buttons import create_cash_operations_buttons, create_staff_management_buttons, create_admin_buttons, create_cashier_buttons
=======
from buttons import (
    create_cash_operations_buttons, create_staff_management_buttons, 
    create_admin_buttons, create_cashier_buttons, create_manager_buttons, # Added manager
    create_stock_management_buttons # Added stock
)
>>>>>>> parent of 071d4d7 (1234у)
from state import bot, user_data, user_state

# Corrected import paths for handlers based on typical project structure
# Assuming handlers are in a directory named 'handlers' at the same level as main.py or one level down.
# Based on your provided file paths like "NewVM/handlers/stock_handler.py",
# the import should be from 'handlers.module_name'.

# If 'NewVM' is a package and main.py is inside it:
# from .handlers.report_handler import register_report_handlers, report_handler as global_report_handler_instances
# from .handlers.start_handler import register_start_handlers
# ... and so on.

# If main.py is at the root and 'handlers' is a subdirectory:
from handlers.report_handler import register_report_handlers, report_handler as global_report_handler_instances
from handlers.start_handler import register_start_handlers
from handlers.help_handler import register_help_handlers
from handlers.sales_handler import register_sales_handlers, sale_handler as global_sale_handler_instances
from handlers.buyback_handler import register_buyback_handlers
from handlers.return_handler import register_return_handlers
from handlers.tradein_handler import register_tradein_handlers
<<<<<<< HEAD
=======
from handlers.stock_handler import register_stock_handlers # Placeholder for when stock_handler.py is created
>>>>>>> parent of 071d4d7 (1234у)
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

def get_keyboard_by_role(chat_id: int) -> types.ReplyKeyboardMarkup | types.ReplyKeyboardRemove:
    user_info = user_data.get(chat_id)
<<<<<<< HEAD
    if not user_info or user_info.get('is_active') == 0:
        logger.warning(f"get_keyboard_by_role: No user_info or inactive user for chat_id {chat_id}. User_data extract: {str(user_info)[:100]}")
=======
    if not user_info or 'role' not in user_info or user_info.get('is_active') == 0:
        logger.warning(f"Role not found or user inactive for chat_id {chat_id} in get_keyboard_by_role. User_data: {user_info}")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
        keyboard.add(share_contact_button)
        return keyboard

    role = user_info['role']
<<<<<<< HEAD
    if role == 'cashier':
        return create_cashier_buttons()
    elif role == 'admin':
        return create_admin_buttons()
=======
    if role == 'cashier': return create_cashier_buttons()
    elif role == 'admin': return create_admin_buttons()
    elif role == 'manager': return create_manager_buttons() # New role
>>>>>>> parent of 071d4d7 (1234у)
    else:
        logger.warning(f"Unknown role '{role}' for chat_id {chat_id} in get_keyboard_by_role.")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером (смена роли?)", request_contact=True)
        keyboard.add(share_contact_button)
        return keyboard

<<<<<<< HEAD

@bot.message_handler(func=lambda message: message.text == "Касса")
def handle_cash_operations_menu(message: types.Message):
    chat_id = message.chat.id
    current_user_data = user_data.get(chat_id)
    if current_user_data and current_user_data.get('is_active') and \
       current_user_data.get('role') in ['admin', 'cashier', 'manager']: # Manager might also need cash ops
=======
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

=======
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)

@bot.message_handler(func=lambda message: message.text == "Касса")
def handle_cash_operations_menu(message):
    # ... (remains the same)
    chat_id = message.chat.id
    current_user_data = user_data.get(chat_id)
<<<<<<< HEAD
    if current_user_data and current_user_data.get('is_active', 0) == 1 and current_user_data.get('role') in ['admin', 'cashier']:
=======
    if current_user_data and current_user_data.get('is_active', 0) == 1 and current_user_data.get('role') in ['admin', 'cashier', 'manager']: # Manager can also access Касса
>>>>>>> parent of 071d4d7 (1234у)
        logger.info(f"Chat {chat_id} (Role: {current_user_data.get('role')}): Accessing Cash Operations Menu.")
        # Clear any FSM state if re-entering "Касса" menu directly
        current_fsm_state = user_state.get(chat_id)
        if current_fsm_state and not current_fsm_state.startswith("UM_") and not current_fsm_state.startswith("STOCK_"):
            # This logic is tricky. If a cash operation was pending, "Касса" might intend to restart.
            # However, "Отмена" is the more explicit way.
            # For now, let's assume pressing "Касса" while in a cash FSM means the user wants to see the cash menu again,
            # but it doesn't automatically cancel the ongoing FSM. "Отмена" should handle that.
            pass
        bot.send_message(chat_id, "Меню кассовых операций. Выберите действие:", reply_markup=create_cash_operations_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Cash Operations Menu. User data: {current_user_data}")
        bot.send_message(chat_id, "Доступ запрещен. Пожалуйста, авторизуйтесь через /start.", reply_markup=get_keyboard_by_role(chat_id))


<<<<<<< HEAD
=======
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


>>>>>>> parent of 071d4d7 (1234у)
@bot.message_handler(func=lambda message: message.text == "Назад")
def go_back_to_main_menu(message: types.Message):
    # ... (remains largely the same, ensure USER_MANAGEMENT_USER_LIST and USER_MANAGEMENT_ACTION_SELECT are handled)
    chat_id = message.chat.id
<<<<<<< HEAD
    user_info = user_data.get(chat_id)
    current_fsm_state = user_state.get(chat_id)
=======
    user = user_data.get(chat_id)
    current_main_state = user_state.get(chat_id) # This is the global state from state.py

    logger.info(f"Chat {chat_id}: 'Назад' pressed. Current global state: {current_main_state}")

    # If "Назад" is pressed during an operation, it should cancel it.
    if current_main_state:
<<<<<<< HEAD
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
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
            return
=======
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
>>>>>>> parent of 071d4d7 (1234у)

    # If no specific state, or after cancellation brought us here with no state.
    if not user or user.get('is_active', 0) == 0:
        # ... (no change)
        logger.info(f"Chat {chat_id}: 'Назад' pressed by non-authorized/inactive user.")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
        keyboard.add(share_contact_button)
        bot.send_message(chat_id, "Вы не авторизованы. Пожалуйста, пройдите авторизацию через /start.", reply_markup=keyboard)
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        return

<<<<<<< HEAD
    logger.info(f"Chat {chat_id} (Role: {user_info.get('role')}): Returning to main menu via 'Назад'.")
=======
    logger.info(f"Chat {chat_id} (Role: {user.get('role')}): Returning to main menu.")
>>>>>>> parent of 071d4d7 (1234у)
    bot.send_message(chat_id, "Возвращаемся в главное меню.", reply_markup=get_keyboard_by_role(chat_id))


@bot.message_handler(func=lambda message: message.text == "Персонал")
<<<<<<< HEAD
def handle_staff_menu_entry(message): # Renamed to avoid conflict if UserManagement also has this
=======
def handle_staff_menu_entry(message):
    # ... (remains the same, ensure check for UM_ states is robust)
>>>>>>> parent of 071d4d7 (1234у)
    chat_id = message.chat.id
    current_user_data = user_data.get(chat_id)
    if current_user_data and current_user_data.get('is_active') and current_user_data.get('role') == 'admin':
        logger.info(f"Chat {chat_id}: Admin accessing Staff Menu.")
        # Clear any previous staff-related FSM state when re-entering "Персонал" menu
        current_main_state = user_state.get(chat_id)
<<<<<<< HEAD
        if current_main_state and (current_main_state.startswith("UM_") or current_main_state == 'USER_MANAGEMENT'):
            user_state.pop(chat_id, None)
            logger.debug(f"Chat {chat_id}: Cleared previous user management state '{current_main_state}'.")
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
=======
        if current_main_state and (current_main_state.startswith("UM_") or current_main_state.startswith("USER_MANAGEMENT")):
            user_state.pop(chat_id, None)
            if user_management_instance_global and chat_id in user_management_instance_global.current_operation_data: # Clear temp data
                del user_management_instance_global.current_operation_data[chat_id]
            logger.debug(f"Chat {chat_id}: Cleared previous user management state '{current_main_state}'.")
>>>>>>> parent of 071d4d7 (1234у)
        bot.send_message(chat_id, "Меню управления персоналом. Выберите действие:", reply_markup=create_staff_management_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Staff Menu. User data: {current_user_data}")
        bot.send_message(chat_id, "Доступ запрещен. Команда доступна только администраторам.", reply_markup=get_keyboard_by_role(chat_id))

# Explicit handler for "Права" button in main.py
@bot.message_handler(func=lambda message: message.text == "Права" and \
                                   user_data.get(message.chat.id, {}).get('role') == 'admin' and \
                                   user_data.get(message.chat.id, {}).get('is_active'))
def handle_rights_button(message: types.Message):
    # ... (remains the same)
    chat_id = message.chat.id
    logger.info(f"Chat {chat_id}: 'Права' button pressed by admin.")
    if user_management_instance_global:
        user_management_instance_global.handle_role_management(chat_id)
    else:
        logger.error(f"Chat {chat_id}: user_management_instance_global is None. Cannot handle 'Права'.")
        bot.send_message(chat_id, "Ошибка: Модуль управления персоналом не инициализирован.", reply_markup=create_staff_management_buttons())


@bot.message_handler(func=lambda message: message.text == "Отмена")
<<<<<<< HEAD
<<<<<<< HEAD
def handle_global_cancel(message: types.Message):
    chat_id = message.chat.id
    current_fsm_state = user_state.get(chat_id)
    user_info = user_data.get(chat_id)
    role = user_info.get('role') if user_info else None
=======
def handle_global_cancel(message: types.Message, called_from_back=False):
    # ... (Ensure STOCK_ states are handled, similar to UM_ states, returning to stock menu) ...
    chat_id = message.chat.id
=======
def handle_global_cancel(message: types.Message, called_from_back=False):
    chat_id = message.chat.id
>>>>>>> parent of effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
    current_op_state = user_state.get(chat_id) # Global state from state.py
    action_source = "'Отмена'" if not called_from_back else "'Назад' (приравнивается к отмене)"
    logger.info(f"Chat {chat_id}: {action_source} pressed. Current global state: {current_op_state}")
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)

<<<<<<< HEAD
    logger.info(f"Chat {chat_id}: 'Отмена' pressed. Current FSM state: {current_fsm_state}. Role: {role}")

    operation_cancelled_msg = "Операция отменена."
    # Determine the correct "menu" keyboard to return to
    # Default to main role-based keyboard
    final_reply_markup = get_keyboard_by_role(chat_id)

<<<<<<< HEAD
    if not current_fsm_state:
        bot.send_message(chat_id, "Нет активных операций для отмены.", reply_markup=final_reply_markup)
        return

    # Specific handlers take precedence and manage their own state cleanup and reply markup
    if current_fsm_state.startswith("BUYBACK_"):
=======
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
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        from handlers.buyback_handler import cancel_buyback
        cancel_buyback(chat_id) # Sends its own reply (main menu for role)
        return
    elif current_fsm_state.startswith("RETURN_"):
=======
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
>>>>>>> parent of 071d4d7 (1234у)
        from handlers.return_handler import cancel_return
        cancel_return(chat_id) # Sends its own reply
        return
    elif current_fsm_state.startswith("TRADEIN_"):
        from handlers.tradein_handler import cancel_tradein
<<<<<<< HEAD
        cancel_tradein(chat_id) # Sends its own reply
        return
    elif current_fsm_state.startswith("SALES_"):
        if chat_id in global_sale_handler_instances:
            global_sale_handler_instances[chat_id].cancel_sale() # Sends its own reply (cash ops menu)
        else: # Fallback if instance lost
            user_state.pop(chat_id, None); user_data.get(chat_id, {}).pop('sales_data', None)
            bot.send_message(chat_id, operation_cancelled_msg, reply_markup=create_cash_operations_buttons())
        return
    elif current_fsm_state.startswith("REPORT_"):
        if chat_id in global_report_handler_instances:
            global_report_handler_instances[chat_id].cancel_operation() # Sends its own reply (cash ops menu)
        else: # Fallback
            user_state.pop(chat_id, None)
            bot.send_message(chat_id, operation_cancelled_msg, reply_markup=create_cash_operations_buttons())
        return
    elif current_fsm_state.startswith("UM_") or current_fsm_state.startswith("USER_MANAGEMENT_"):
        if user_management_instance_global:
            user_management_instance_global.cancel_um_operation_and_return_to_staff_menu(chat_id) # Sends staff menu
        else: # Fallback
            user_state.pop(chat_id, None)
            bot.send_message(chat_id, operation_cancelled_msg, reply_markup=create_staff_management_buttons())
        return
    elif current_fsm_state.startswith("STOCK_"):
        if chat_id in global_stock_ops_handler_instances:
            global_stock_ops_handler_instances[chat_id].reset_and_remove(message_text=operation_cancelled_msg, reply_markup=create_stock_management_buttons())
        else: # Fallback
            user_state.pop(chat_id, None); user_data.get(chat_id, {}).pop('stock_op_data', None)
            bot.send_message(chat_id, operation_cancelled_msg, reply_markup=create_stock_management_buttons())
        return

    # If no specific handler dealt with it, but a state was present (shouldn't happen if all FSMs covered)
    logger.warning(f"Chat {chat_id}: 'Отмена' with unhandled FSM state '{current_fsm_state}'. Generic cleanup.")
    user_state.pop(chat_id, None)
    if chat_id in user_data and 'operation_data' in user_data[chat_id]: # Generic temp data
        del user_data[chat_id]['operation_data']
    bot.send_message(chat_id, operation_cancelled_msg, reply_markup=final_reply_markup)

=======
        cancel_tradein(chat_id)
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("SALES_"): # Note: SaleState enum uses "SALES_" prefix
        if chat_id in global_sale_handler_instances:
<<<<<<< HEAD
            global_sale_handler_instances[chat_id].cancel_sale() # This should clear global state and its own dict entry
        else: # Fallback if instance missing
            user_state.pop(chat_id, None)
=======
            global_sale_handler_instances[chat_id].cancel_sale()
        else: 
            user_state.pop(chat_id, None)
            if chat_id in user_data and 'sales_data' in user_data[chat_id]: del user_data[chat_id]['sales_data']
>>>>>>> parent of 071d4d7 (1234у)
            bot.send_message(chat_id, "Продажа отменена.", reply_markup=final_reply_markup)
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("REPORT_"):
        if chat_id in global_report_handler_instances:
<<<<<<< HEAD
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
=======
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
>>>>>>> parent of 071d4d7 (1234у)
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
<<<<<<< HEAD

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
    register_buyback_handlers() # These use global state.py bot, user_data, user_state
    register_return_handlers()
    register_tradein_handlers()
=======
    user_management_instance_global = configure_user_management(bot, user_data, get_all_users, change_user_role_function)
    register_sales_handlers(bot, user_data, user_state)
    register_report_handlers(bot, user_data, user_state)
    register_buyback_handlers()
    register_return_handlers()
    register_tradein_handlers()
    # from handlers.stock_handler import register_stock_handlers # Uncomment when created
    # register_stock_handlers(bot, user_data, user_state)      # Uncomment when created
>>>>>>> parent of 071d4d7 (1234у)

    logger.info("All handlers registered. Bot polling started...")
    try:
        bot.polling(none_stop=True, interval=0, timeout=30)
    except Exception as e:
        logger.critical(f"Bot polling crashed: {e}", exc_info=True)
    finally:
        logger.info("Bot polling stopped.")

if __name__ == '__main__':
    main()