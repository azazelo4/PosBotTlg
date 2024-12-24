import telebot
from telebot import types
from config import TOKEN
from buttons import create_cash_operations_buttons, create_staff_management_buttons
from report import Report
from handlers.start_handler import bot, user_data
from handlers.sales_handler import register_sales_handlers
from handlers.help_handler import help_command
import io
from datetime import date
import logging

logger = logging.getLogger(__name__)

def get_keyboard_by_role(chat_id):
    role = user_data[chat_id]['role']
    if role == 'cashier':
        return create_cash_operations_buttons()
    elif role == 'admin':
        return create_staff_management_buttons()
    else:
        return None

@bot.message_handler(func=lambda message: message.text == "Касса")
def handle_cash_operations_menu(message):
    chat_id = message.chat.id
    if user_data.get(chat_id) and user_data[chat_id]['role'] in ['admin', 'cashier']:
        bot.send_message(chat_id, "Меню кассовых операций. Выберите действие:", reply_markup=create_cash_operations_buttons())
    else:
        bot.send_message(chat_id, "Доступ запрещен.")

@bot.message_handler(func=lambda message: message.text == "Назад")
def go_back_to_main_menu(message):
    chat_id = message.chat.id

    user = user_data.get(chat_id)
    if not user or user.get('is_active') == 0:
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
        keyboard.add(share_contact_button)

        bot.send_message(chat_id, "Вы не авторизованы или ваш аккаунт деактивирован. Пожалуйста, поделитесь своим номером для авторизации.", reply_markup=keyboard)
        return

    bot.send_message(chat_id, "Возвращаемся в главное меню.", reply_markup=get_keyboard_by_role(chat_id))

@bot.message_handler(func=lambda message: message.text == "Персонал")
def handle_staff_menu(message):
    chat_id = message.chat.id
    if user_data.get(chat_id) and user_data[chat_id]['role'] == 'admin':
        bot.send_message(chat_id, "Меню управления персоналом. Выберите действие:", reply_markup=create_staff_management_buttons())
    else:
        bot.send_message(chat_id, "Доступ запрещен. Команда доступна только администраторам.")

#################----------- REPORT -----------#################
report_handler = {} 

@bot.message_handler(func=lambda message: message.text == 'Отчеты')
def handle_report(message):
    chat_id = message.chat.id
    if chat_id in user_data:
        if chat_id not in report_handler:
            report_handler[chat_id] = Report()
        report = report_handler[chat_id]
        
        if message.text == "Отчеты" or report.report_type is not None:
            response = report.process_message(message, chat_id)
            if response:
                if isinstance(response, tuple):
                    report_file, report_message, report_keyboard = response
                    if report_file is not None:
                        with io.BytesIO(report_file.getvalue()) as file:
                            filename = f"report_{date.today().isoformat()}.xlsx"
                            bot.send_document(message.chat.id, file, filename = filename)
                    if report_keyboard:
                        bot.send_message(message.chat.id, report_message, reply_markup=report_keyboard)
                    else:
                        bot.send_message(message.chat.id, report_message)
                else:
                    bot.send_message(message.chat.id, response)
        else:
            bot.send_message(chat_id, "Введите команду, например /start или Отчеты.")
    else:
        bot.send_message(chat_id, "Вы не авторизованы. Пожалуйста, поделитесь своим номером телефона для авторизации, введя команду /start.")

#################----------- USER ADD AND REMOVE -----------#################

@bot.message_handler(func=lambda message: message.text == "Персонал")
def handle_rights_menu(message):
    handle_role_management(message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("select_user_"))
def callback_select_user(call):
    handle_select_user(call)

@bot.callback_query_handler(func=lambda call: call.data.startswith("change_role_"))
def callback_change_role(call):
    handle_change_role(call)

#################----------- RIGTHS -----------#################

#################----------- ADD GOODS -----------#################

#################----------- REVISION -----------#################

#################----------- END -----------#################

if __name__ == '__main__':
    register_sales_handlers(bot, user_data)
    help_command(user_data)
    bot.polling(none_stop=True)
