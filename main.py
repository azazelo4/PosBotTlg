import logging
from telebot import types
from buttons import create_cash_operations_buttons, create_staff_management_buttons, create_admin_buttons, create_cashier_buttons
from state import bot, user_data, user_state 
from handlers.report_handler import register_report_handlers
from handlers.start_handler import register_start_handlers # Now this import should work
from handlers.help_handler import register_help_handlers # Import for help handler
from handlers.sales_handler import register_sales_handlers
from handlers.buyback_handler import register_buyback_handlers
from handlers.return_handler import register_return_handlers
from handlers.tradein_handler import register_tradein_handlers
from database import get_all_users, change_user_role_function, deactivate_user_in_db
from user_management import configure_user_management

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

def get_keyboard_by_role(chat_id):
    user_info = user_data.get(chat_id)
    if not user_info or 'role' not in user_info or user_info.get('is_active') == 0: # Check is_active too
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
        # Provide a way to re-auth if role is weird
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

    # Clear any ongoing operation state and associated data
    # It's safer to call specific cancel functions if they exist, as they might do more cleanup
    if current_state:
        if current_state.startswith("BUYBACK_"):
            from handlers.buyback_handler import cancel_buyback
            cancel_buyback(chat_id) # This will send its own "Отменена" message and set keyboard
            return # cancel_buyback already handles the reply and state cleanup
        elif current_state.startswith("RETURN_"):
            from handlers.return_handler import cancel_return
            cancel_return(chat_id)
            return
        elif current_state.startswith("TRADEIN_"):
            from handlers.tradein_handler import cancel_tradein
            cancel_tradein(chat_id)
            return
        elif current_state.startswith("SALES_") or current_state.startswith("SaleState"): # For sales_handler
            # sales_handler might need a specific cancel function or a more direct state reset
            if chat_id in user_data and 'sales_data' in user_data[chat_id]:
                del user_data[chat_id]['sales_data']
            if chat_id in user_data and 'sale_handler' in user_data[chat_id]: # If sales_handler stores instance in user_data
                 del user_data[chat_id]['sale_handler']
            if 'sale_handler' in globals() and chat_id in sale_handler: # from sales_handler.py
                 del sale_handler[chat_id]
            user_state.pop(chat_id, None)
            # General message for other cancellations via "Назад"
            bot.send_message(chat_id, "Текущая операция прервана.", reply_markup=get_keyboard_by_role(chat_id))

        elif current_state.startswith("REPORT_"):
             # report_handler needs a reset mechanism too
            if 'report_handler' in globals() and chat_id in report_handler:
                report_handler[chat_id].reset() # Assuming Report class has a reset method
                del report_handler[chat_id]
            user_state.pop(chat_id, None)
            bot.send_message(chat_id, "Генерация отчета прервана.", reply_markup=get_keyboard_by_role(chat_id))
        
        elif current_state in ['ADDING_USER', 'DELETING_USER', 'USER_MANAGEMENT'] and 'user_management_instance' in globals():
            # User management states, potentially reset its internal state
            # This might need user_management_instance.cancel_operation(chat_id) or similar
            user_state.pop(chat_id, None) # Basic state pop
            bot.send_message(chat_id, "Операция с персоналом прервана.", reply_markup=create_staff_management_buttons()) # Go back to staff menu
            return # Don't go to main menu from staff sub-operations

        else: # Generic state pop for unhandled cases by 'Назад'
            user_state.pop(chat_id, None)
            # Fall through to send main menu

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
        bot.send_message(chat_id, "Меню управления персоналом. Выберите действие:", reply_markup=create_staff_management_buttons())
    else:
        logger.warning(f"Chat {chat_id}: Unauthorized access attempt to Staff Menu. User data: {current_user_data}")
        bot.send_message(chat_id, "Доступ запрещен. Команда доступна только администраторам.", reply_markup=get_keyboard_by_role(chat_id))

@bot.message_handler(func=lambda message: message.text == "Отмена")
def handle_global_cancel(message: types.Message):
    chat_id = message.chat.id
    current_op_state = user_state.get(chat_id)
    logger.info(f"Chat {chat_id}: 'Отмена' pressed. Current state: {current_op_state}")

    if not current_op_state:
        bot.send_message(chat_id, "Нет активных операций для отмены.", reply_markup=get_keyboard_by_role(chat_id))
        return

    # Prioritize specific cancel handlers
    if current_op_state.startswith("BUYBACK_"):
        from handlers.buyback_handler import cancel_buyback
        cancel_buyback(chat_id)
    elif current_op_state.startswith("RETURN_"):
        from handlers.return_handler import cancel_return
        cancel_return(chat_id)
    elif current_op_state.startswith("TRADEIN_"):
        from handlers.tradein_handler import cancel_tradein
        cancel_tradein(chat_id)
    elif current_op_state.startswith("SALES_") or current_op_state.startswith("SaleState"): # For sales_handler
        if 'sale_handler' in globals() and chat_id in sale_handler:
            sale_handler[chat_id].reset()
            del sale_handler[chat_id]
        user_state.pop(chat_id, None)
        if chat_id in user_data and 'sales_data' in user_data[chat_id]: del user_data[chat_id]['sales_data']
        bot.send_message(chat_id, "Продажа отменена.", reply_markup=get_keyboard_by_role(chat_id))
    elif current_op_state.startswith("REPORT_"):
        if 'report_handler' in globals() and chat_id in report_handler:
            report_handler[chat_id].reset()
            del report_handler[chat_id]
        user_state.pop(chat_id, None)
        bot.send_message(chat_id, "Генерация отчета отменена.", reply_markup=get_keyboard_by_role(chat_id))
    elif current_op_state in ['ADDING_USER', 'DELETING_USER', 'USER_MANAGEMENT']: # User management states
        user_state.pop(chat_id, None) # Basic state pop
        # user_management_instance might need a specific cancel method
        bot.send_message(chat_id, "Операция с персоналом отменена.", reply_markup=create_staff_management_buttons()) # Back to staff menu
    else:
        # Generic cancel if no specific handler matched the state
        user_state.pop(chat_id, None)
        # Clean up any generic operation data if a pattern is established
        # e.g., if user_data[chat_id].pop('current_operation_data', None)
        bot.send_message(chat_id, "Операция отменена.", reply_markup=get_keyboard_by_role(chat_id))


def main():
    logger.info("Bot starting...")
    
    register_start_handlers() 
    register_help_handlers() # Register help handlers
    
    user_management_instance = configure_user_management(bot, user_data, get_all_users, change_user_role_function)

    register_sales_handlers(bot, user_data) # Pass bot and user_data if sales_handler expects them
    register_report_handlers(bot, user_data) # Pass bot and user_data
    register_buyback_handlers()
    register_return_handlers()
    register_tradein_handlers()
    
    logger.info("Bot polling started...")
    try:
        bot.polling(none_stop=True, interval=0, timeout=30) # Increased timeout
    except Exception as e:
        logger.critical(f"Bot polling crashed: {e}", exc_info=True)
    finally:
        logger.info("Bot polling stopped.")

if __name__ == '__main__':
    main()