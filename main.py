import logging
from telebot import types
from apscheduler.schedulers.background import BackgroundScheduler
from buttons import (
    create_cash_operations_buttons, create_staff_management_buttons, 
    create_admin_buttons, create_cashier_buttons, create_manager_buttons,
    create_stock_management_buttons
)
from state import bot, user_data, user_state
from handlers.report_handler import register_report_handlers, report_handler as global_report_handler_instances
from handlers.start_handler import register_start_handlers
from handlers.help_handler import register_help_handlers
from handlers.sales_handler import register_sales_handlers, sale_handler as global_sale_handler_instances
from handlers.buyback_handler import register_buyback_handlers # Corrected import based on your file name
from handlers.return_handler import register_return_handlers # Corrected import
from handlers.tradein_handler import register_tradein_handlers # Corrected import
from handlers.stock_handler import register_stock_handlers, stock_operations_handler # Added stock_handler import
from database import get_all_users, change_user_role_function # Keep these
from utils.report_generator import generate_full_sales_report_excel # Import for scheduled reports
from utils.stock_notifier import send_low_stock_notification # New import for stock notifications
from config import DAILY_REPORT_TIME, REPORT_CHAT_ID # Import new config variables
from user_management import configure_user_management, UserManagement

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("app_debug.log", mode='a'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

user_management_instance_global: UserManagement = None
scheduler = BackgroundScheduler()

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
    elif role == 'manager': return create_manager_buttons()
    else:
        logger.warning(f"Unknown role '{role}' for chat_id {chat_id}.")
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add(types.KeyboardButton("Поделиться номером (смена роли?)", request_contact=True))
        return kb

@bot.message_handler(func=lambda message: message.text == "Касса")
def handle_cash_operations_menu(message):
    chat_id = message.chat.id
    current_user_data = user_data.get(chat_id)
    if current_user_data and current_user_data.get('is_active', 0) == 1 and current_user_data.get('role') in ['admin', 'cashier', 'manager']:
        logger.info(f"Chat {chat_id} (Role: {current_user_data.get('role')}): Accessing Cash Operations Menu.")
        bot.send_message(chat_id, "Меню кассовых операций. Выберите действие:", reply_markup=create_cash_operations_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Cash Operations Menu. User data: {current_user_data}")
        bot.send_message(chat_id, "Доступ запрещен. Пожалуйста, авторизуйтесь через /start.", reply_markup=get_keyboard_by_role(chat_id))


@bot.message_handler(func=lambda message: message.text == "Склад")
def handle_stock_menu(message: types.Message):
    chat_id = message.chat.id
    current_user = user_data.get(chat_id)
    if current_user and current_user.get('is_active') and current_user.get('role') in ['admin', 'manager']:
        logger.info(f"Chat {chat_id} (Role: {current_user.get('role')}): Accessing Stock Management Menu.")
        
        # Clear any previous stock FSM state if re-entering
        current_main_state = user_state.get(chat_id)
        if current_main_state and current_main_state.startswith("STOCK_"):
            if chat_id in stock_operations_handler:
                stock_operations_handler[chat_id].reset_and_remove() # Use instance method to clean up
            else: # Fallback if instance was somehow lost
                user_state.pop(chat_id, None)
            logger.debug(f"Chat {chat_id}: Cleared previous stock operation state '{current_main_state}' before entering stock menu.")
        
        bot.send_message(chat_id, "Меню управления складом:", reply_markup=create_stock_management_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access to Stock menu. User: {current_user}")
        bot.send_message(chat_id, "Доступ к складу запрещен.", reply_markup=get_keyboard_by_role(chat_id))


@bot.message_handler(func=lambda message: message.text == "Назад")
def go_back_to_main_menu(message: types.Message):
    chat_id = message.chat.id
    user = user_data.get(chat_id)
    current_main_state = user_state.get(chat_id) 

    logger.info(f"Chat {chat_id}: 'Назад' pressed. Current global state: {current_main_state}")

    if current_main_state:
        if current_main_state in ['USER_MANAGEMENT_USER_LIST', 'USER_MANAGEMENT_ACTION_SELECT', 
                                  'USER_MANAGEMENT_ROLE_SELECT', 'USER_MANAGEMENT_DEACTIVATE_CONFIRM',
                                  'UM_AWAITING_ADD_USER_DETAILS', 'UM_AWAITING_NEW_PHONE_FOR_USER'] and user_management_instance_global: # Added more UM states
            logger.info(f"Chat {chat_id}: 'Назад' from user management flow ('{current_main_state}'). Returning to staff menu.")
            # Let the "Отмена" logic or specific UM cancel handle state cleanup
            # user_management_instance_global.cancel_um_operation_and_return_to_staff_menu(chat_id, "Операция отменена, возврат в меню персонала.")
            # For "Назад", it's better to just go to the staff menu directly after cleaning state.
            if chat_id in user_management_instance_global.current_operation_data:
                del user_management_instance_global.current_operation_data[chat_id]
            user_state.pop(chat_id, None)
            bot.send_message(chat_id, "Возврат в меню управления персоналом.", reply_markup=create_staff_management_buttons())
            return
        elif current_main_state.startswith("STOCK_"):
             logger.info(f"Chat {chat_id}: 'Назад' from stock operation ('{current_main_state}'). Returning to stock menu.")
             if chat_id in stock_operations_handler:
                 stock_operations_handler[chat_id].reset_and_remove(message_text="Операция отменена. Возврат в меню склада.", reply_markup=create_stock_management_buttons())
             else: # Fallback
                 user_state.pop(chat_id, None)
                 bot.send_message(chat_id, "Операция отменена. Возврат в меню склада.", reply_markup=create_stock_management_buttons())
             return
        else: # For cash operations, "Назад" usually means cancel current step and return to cash ops menu
            # This is handled by "Отмена" now, so if state is not UM or STOCK, it means main menu from cash ops.
            # However, if inside a cash op FSM, "Отмена" should be used.
            # "Назад" from cash_ops_menu itself should go to main_menu.
            # Let's assume this "Назад" is from cash_ops_menu going to main_menu, or after a generic cancel.
            logger.info(f"Chat {chat_id}: 'Назад' from cash operations menu or after a generic cancel. Clearing state {current_main_state} and going to main menu.")
            user_state.pop(chat_id, None) # Clear any residual state
            # Fall through to send main menu

    if not user or user.get('is_active', 0) == 0:
        logger.info(f"Chat {chat_id}: 'Назад' pressed by non-authorized/inactive user.")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
        keyboard.add(share_contact_button)
        bot.send_message(chat_id, "Вы не авторизованы. Пожалуйста, пройдите авторизацию через /start.", reply_markup=keyboard)
        return

    logger.info(f"Chat {chat_id} (Role: {user.get('role')}): Returning to main menu via 'Назад'.")
    bot.send_message(chat_id, "Возвращаемся в главное меню.", reply_markup=get_keyboard_by_role(chat_id))


@bot.message_handler(func=lambda message: message.text == "Персонал")
def handle_staff_menu_entry(message):
    chat_id = message.chat.id
    current_user_data = user_data.get(chat_id)
    if current_user_data and current_user_data.get('is_active', 0) == 1 and current_user_data.get('role') == 'admin':
        logger.info(f"Chat {chat_id}: Admin accessing Staff Menu.")
        current_main_state = user_state.get(chat_id)
        # Clear previous UM state if re-entering "Персонал" menu

def send_daily_sales_report():
    """
    Generates and sends a daily sales report for yesterday to a predefined chat ID.
    This function is intended to be run by the scheduler.
    """
    logger.info("Attempting to send daily sales report.")
    today = date.today()
    yesterday = today - timedelta(days=1)
    
    try:
        report_buffer = generate_full_sales_report_excel(yesterday, yesterday)
        filename = f"daily_sales_report_{yesterday.strftime('%Y%m%d')}.xlsx"
        
        if REPORT_CHAT_ID:
            bot.send_document(REPORT_CHAT_ID, report_buffer, visible_file_name=filename, caption=f"Ежедневный отчет о продажах за {yesterday.strftime('%Y-%m-%d')}.")
            logger.info(f"Daily sales report for {yesterday.strftime('%Y-%m-%d')} sent successfully to chat ID {REPORT_CHAT_ID}.")
        else:
            logger.warning("REPORT_CHAT_ID is not configured. Daily report not sent.")
    except Exception as e:
        logger.exception(f"Error sending daily sales report for {yesterday.strftime('%Y-%m-%d')}: {e}")

        if current_main_state and (current_main_state.startswith("UM_") or current_main_state.startswith("USER_MANAGEMENT")):
            user_state.pop(chat_id, None)
            if user_management_instance_global and chat_id in user_management_instance_global.current_operation_data:
                del user_management_instance_global.current_operation_data[chat_id]
            logger.debug(f"Chat {chat_id}: Cleared previous user management state '{current_main_state}' before entering staff menu.")
        bot.send_message(chat_id, "Меню управления персоналом. Выберите действие:", reply_markup=create_staff_management_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Staff Menu. User data: {current_user_data}")
        bot.send_message(chat_id, "Доступ запрещен. Команда доступна только администраторам.", reply_markup=get_keyboard_by_role(chat_id))


@bot.message_handler(func=lambda message: message.text == "Права" and \
                                   user_data.get(message.chat.id, {}).get('role') == 'admin' and \
                                   user_data.get(message.chat.id, {}).get('is_active', 0) == 1)
def handle_rights_button(message: types.Message):
    chat_id = message.chat.id
    logger.info(f"Chat {chat_id}: 'Права' button pressed by admin.")
    if user_management_instance_global:
        user_management_instance_global.handle_role_management(chat_id) # This initiates the inline keyboard flow
    else:
        logger.error(f"Chat {chat_id}: user_management_instance_global is None. Cannot handle 'Права'.")
        bot.send_message(chat_id, "Ошибка: Модуль управления персоналом не инициализирован.")


@bot.message_handler(func=lambda message: message.text == "Отмена")
def handle_global_cancel(message: types.Message, called_from_back=False):
    chat_id = message.chat.id
    current_op_state = user_state.get(chat_id) 
    action_source = "'Отмена'" if not called_from_back else "'Назад' (приравнивается к отмене)"
    logger.info(f"Chat {chat_id}: {action_source} pressed. Current global state: {current_op_state}")

    operation_cancelled_by_specific_handler = False
    
    # Determine the correct main menu or section menu based on context
    # Default to main role-based keyboard
    final_reply_markup = get_keyboard_by_role(chat_id) 

    if not current_op_state:
        if not called_from_back: 
            bot.send_message(chat_id, "Нет активных операций для отмены.", reply_markup=final_reply_markup)
        # If called_from_back and no state, go_back_to_main_menu handles it.
        return

    # Specific handlers take precedence
    if current_op_state.startswith("BUYBACK_"):
        from handlers.buyback_handler import cancel_buyback # Local import to avoid circular at top
        cancel_buyback(chat_id) # This will send its own reply_markup
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
            global_sale_handler_instances[chat_id].cancel_sale() # Sends its own markup
        else: # Fallback if instance lost
            user_state.pop(chat_id, None)
            if chat_id in user_data and 'sales_data' in user_data[chat_id]: del user_data[chat_id]['sales_data']
            bot.send_message(chat_id, "Продажа отменена.", reply_markup=create_cash_operations_buttons())
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("REPORT_"):
        if chat_id in global_report_handler_instances:
            global_report_handler_instances[chat_id].cancel_operation() # Sends its own markup
        else: # Fallback
            user_state.pop(chat_id, None)
            bot.send_message(chat_id, "Генерация отчета отменена.", reply_markup=create_cash_operations_buttons())
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("UM_") or current_op_state.startswith("USER_MANAGEMENT"):
        if user_management_instance_global:
            # UM's cancel method handles state and reply_markup
            user_management_instance_global.cancel_um_operation_and_return_to_staff_menu(chat_id)
        else: # Fallback
            user_state.pop(chat_id, None)
            bot.send_message(chat_id, "Операция с персоналом отменена.", reply_markup=create_staff_management_buttons())
        operation_cancelled_by_specific_handler = True
    elif current_op_state.startswith("STOCK_"):
        if chat_id in stock_operations_handler:
            # StockOperation's reset_and_remove handles state and reply_markup
            stock_operations_handler[chat_id].reset_and_remove(message_text="Операция со складом отменена.", reply_markup=create_stock_management_buttons())
        else: # Fallback
            user_state.pop(chat_id, None)
            if chat_id in user_data and 'stock_op_data' in user_data[chat_id]: del user_data[chat_id]['stock_op_data']
            bot.send_message(chat_id, "Операция со складом отменена.", reply_markup=create_stock_management_buttons())
        operation_cancelled_by_specific_handler = True
    
    # If no specific handler dealt with it, and state was present, do a generic cleanup
    if not operation_cancelled_by_specific_handler and current_op_state:
        user_state.pop(chat_id, None)
        # Clean up any generic operation data if your design uses it
        if chat_id in user_data and 'operation_data' in user_data[chat_id]: 
            del user_data[chat_id]['operation_data']
        logger.info(f"Chat {chat_id}: Generic operation state '{current_op_state}' cancelled by global handler.")
        bot.send_message(chat_id, "Операция отменена.", reply_markup=final_reply_markup) # final_reply_markup is main role keyboard
    

def main():
    global user_management_instance_global
    logger.info("Bot starting...")
    register_start_handlers()
    register_help_handlers()
    
    # User Management needs to be configured before other handlers that might depend on user roles
    user_management_instance_global = configure_user_management(bot, user_data, get_all_users, change_user_role_function)
    
    # Register other handlers
    register_sales_handlers(bot, user_data, user_state) # Pass bot, user_data, user_state
    register_report_handlers(bot, user_data, user_state) # Pass bot, user_data, user_state
    register_buyback_handlers() 
    register_return_handlers()
    register_tradein_handlers()
    register_stock_handlers(bot, user_data, user_state) # Pass bot, user_data, user_state

    # Schedule daily report
    if DAILY_REPORT_TIME and REPORT_CHAT_ID:
        hour, minute = map(int, DAILY_REPORT_TIME.split(':'))
        scheduler.add_job(send_daily_sales_report, 'cron', hour=hour, minute=minute)
        scheduler.start()
        logger.info(f"Daily sales report scheduled for {DAILY_REPORT_TIME} to chat ID {REPORT_CHAT_ID}.")
    else:
        logger.warning("Daily report scheduling skipped: DAILY_REPORT_TIME or REPORT_CHAT_ID not configured.")

    # Schedule low stock notification
    # Assuming you want to check for low stock once a day, e.g., at 9:00 AM
    # You can adjust the hour and minute as needed.
    if LOW_STOCK_NOTIFICATION_CHAT_ID and LOW_STOCK_THRESHOLD is not None:
        scheduler.add_job(send_low_stock_notification, 'cron', hour=9, minute=0)
        logger.info(f"Low stock notification scheduled for 09:00 daily to chat ID {LOW_STOCK_NOTIFICATION_CHAT_ID}.")
    else:
        logger.warning("Low stock notification scheduling skipped: LOW_STOCK_NOTIFICATION_CHAT_ID or LOW_STOCK_THRESHOLD not configured.")

    scheduler.start() # Start scheduler after all jobs are added.

    logger.info("Bot polling started...")
    try:
        bot.polling(none_stop=True, interval=0, timeout=30)
    except Exception as e:
        logger.critical(f"Bot polling crashed: {e}", exc_info=True)
    finally:
        logger.info("Bot polling stopped.")

if __name__ == '__main__':
    main()