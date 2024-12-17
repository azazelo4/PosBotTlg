import telebot
from telebot import types
from config import TOKEN
from auth import authorize_user
from buttons import *
from sale import *
from database import *
from report import Report
import io
from datetime import date
import logging
#import threading
#import time
#from user_management import configure_user_management
#from database import get_all_users, change_user_role

# Настраиваем зависимости
#configure_user_management(get_all_users, change_user_role)

# logging.basicConfig(
#     level=logging.DEBUG,  # Уровень отладки
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#     handlers=[
#         logging.FileHandler("/var/log/pos_bot.log"),  # Путь к файлу логов
#         logging.StreamHandler()  # Вывод в консоль
#     ]
# )

logger = logging.getLogger(__name__)


bot = telebot.TeleBot(TOKEN)

user_data = {}

#################----------- START -----------#################
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    button_phone = telebot.types.KeyboardButton(text="Поделиться номером", request_contact=True)
    keyboard.add(button_phone)
    bot.send_message(chat_id, "Пожалуйста, поделитесь своим номером телефона для авторизации:", reply_markup=keyboard)

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    chat_id = message.chat.id
    phone_number = message.contact.phone_number

    try:
        user_data_dict, error = authorize_user(phone_number)

        if error:
            bot.send_message(chat_id, error)
        elif user_data_dict:
            user_data[chat_id] = user_data_dict

            if user_data_dict['role'] == 'cashier':
                keyboard = create_cashier_buttons()
            elif user_data_dict['role'] == 'admin':
                keyboard = create_admin_buttons()

            bot.send_message(
                chat_id,
                f"Здравствуйте, {user_data_dict['name']}! Вы авторизованы как {user_data_dict['role']}. Для справки введите команду /help.",
                reply_markup=keyboard
            )
        else:
            bot.send_message(chat_id, "Произошла ошибка при авторизации. Попробуйте еще раз.")
    except Exception as e:
        logger.exception("Ошибка при авторизации пользователя.")
        bot.send_message(chat_id, "Произошла ошибка при авторизации. Попробуйте еще раз.")


def get_keyboard_by_role(chat_id):
    role = user_data[chat_id]['role']
    if role == 'cashier':
        return create_cashier_buttons()
    elif role == 'admin':
        return create_admin_buttons()
    else:
        return None


# Обработка кнопки "Касса" для отображения меню кассовых операций
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

    # Проверяем авторизацию пользователя
    user = user_data.get(chat_id)
    if not user or user.get('is_active') == 0:
        # Создаем клавиатуру с кнопкой "Поделиться номером"
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
        keyboard.add(share_contact_button)

        bot.send_message(chat_id, "Вы не авторизованы или ваш аккаунт деактивирован. Пожалуйста, поделитесь своим номером для авторизации.", reply_markup=keyboard)
        return

    # Если пользователь авторизован, возвращаем главное меню
    bot.send_message(chat_id, "Возвращаемся в главное меню.", reply_markup=get_keyboard_by_role(chat_id))

@bot.message_handler(func=lambda message: message.text == "Персонал")
def handle_staff_menu(message):
    chat_id = message.chat.id
    # Проверка, что пользователь — администратор
    if user_data.get(chat_id) and user_data[chat_id]['role'] == 'admin':
        bot.send_message(chat_id, "Меню управления персоналом. Выберите действие:", reply_markup=create_staff_management_buttons())
    else:
        bot.send_message(chat_id, "Доступ запрещен. Команда доступна только администраторам.")

#################----------- HELP -----------#################
@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = """
    Бот в разработке и все здесь только для тестирования
    Доступные команды:
    /start - начать диалог с ботом, запросить номер телефона для авторизации
    /help - отобразить список доступных команд

    Как работать:
    Продажа - начать процесс продажи товара 
    введите тестовый артикул 12345678 или 2-1234, 
    сумму продажи 
    выберите тип оплаты - нал или безнал
    поттвердите продажу выбором потвердить или отменить

    можно нажать отмена на любом шаге.

    Для администраторов:
    (в разработке)
    """

    bot.send_message(message.chat.id, help_text)

#################----------- SALES -----------#################
sale_handler = {}

@bot.message_handler(func=lambda message: sale_handler.get(message.chat.id) is None and message.text == 'Продажа')
def start_sale(message):
    chat_id = message.chat.id
    if chat_id in user_data:
        sale_handler[chat_id] = Sale(user_data[chat_id])
        bot.send_message(chat_id, "Введите артикул товара:")
    else:
        bot.send_message(chat_id, "Вы не авторизованы. Пожалуйста, поделитесь своим номером телефона для авторизации, введя команду /start.")
    
def process_sale_step(message, chat_id, user_data):
    sale = sale_handler.get(chat_id, None)
    try:
        result = sale.process_step(message, user_data)
        if result is None or (sale.state == SaleState.WAITING_ARTICLE and result == "Продажа подтверждена."):
            sale.reset()
            del sale_handler[chat_id]
            keyboard = get_keyboard_by_role(chat_id)
            bot.send_message(chat_id, "Продажа отменена." if result is None else result, reply_markup=keyboard)
        else:
            if isinstance(result, tuple):
                response, keyboard = result
            else:
                response, keyboard = result, get_keyboard_by_role(chat_id)
            bot.send_message(chat_id, response, reply_markup=keyboard)
    except Exception as e:
        logger.exception("Ошибка при обработке шага продажи.")
        bot.send_message(chat_id, "Произошла ошибка при обработке продажи. Попробуйте еще раз.")

@bot.message_handler(func=lambda message: sale_handler.get(message.chat.id) is not None and sale_handler[message.chat.id].state == SaleState.WAITING_ARTICLE)
def process_article(message):
    chat_id = message.chat.id
    process_sale_step(message, chat_id, user_data.get(chat_id))

@bot.message_handler(func=lambda message: sale_handler.get(message.chat.id) is not None and sale_handler[message.chat.id].state == SaleState.WAITING_SALE_PRICE)
def process_sale_price(message):
    chat_id = message.chat.id
    process_sale_step(message, chat_id, user_data.get(chat_id))

@bot.message_handler(func=lambda message: sale_handler.get(message.chat.id) is not None and sale_handler[message.chat.id].state == SaleState.WAITING_PAYMENT_TYPE)
def process_payment_type(message):
    chat_id = message.chat.id
    process_sale_step(message, chat_id, user_data.get(chat_id))

@bot.message_handler(func=lambda message: sale_handler.get(message.chat.id) is not None and sale_handler[message.chat.id].state == SaleState.CONFIRM_SALE)
def confirm_sale(message):
    chat_id = message.chat.id
    process_sale_step(message, chat_id, user_data.get(chat_id))

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

# # Обработчик нажатия на кнопку "Добавить пользователя"
# @bot.message_handler(func=lambda message: message.text == "Добавить пользователя")
# def add_user_button(message):
#     chat_id = message.chat.id
#     if user_data.get(chat_id) and user_data[chat_id]['role'] == 'admin':
#         msg = bot.send_message(chat_id, "Введите данные нового пользователя в формате: номер_телефона роль имя")
#         bot.register_next_step_handler(msg, process_add_user)
#     else:
#         bot.send_message(chat_id, "Команда доступна только администраторам.")

# def process_add_user(message):
#     chat_id = message.chat.id
#     try:
#         if message.text == 'Отмена':
#             return None, go_back_to_main_menu
#         phone_number, role, name = message.text.split()
#         add_user_to_db(phone_number, role, name)
#         bot.send_message(chat_id, f"Пользователь {name} успешно добавлен.")
#     except ValueError:
#         bot.send_message(chat_id, "Ошибка: неверный формат данных. Используйте формат: номер_телефона роль имя.")

# # Обработчик нажатия на кнопку "Удалить пользователя"
# @bot.message_handler(func=lambda message: message.text == "Удалить пользователя")
# def remove_user_button(message):
#     chat_id = message.chat.id
#     if user_data.get(chat_id) and user_data[chat_id]['role'] == 'admin':
#         msg = bot.send_message(chat_id, "Введите номер телефона пользователя для удаления:")
#         bot.register_next_step_handler(msg, process_remove_user)
#     else:
#         bot.send_message(chat_id, "Команда доступна только администраторам.")

# def process_remove_user(message):
#     chat_id = message.chat.id
#     phone_number = message.text
#     deactivate_user_in_db(phone_number)
#     bot.send_message(chat_id, f"Пользователь с номером {phone_number} успешно деактивирован.")

# def check_user_status():
#     while True:
#         # Получаем список деактивированных пользователей
#         deactivated_users = get_deactivated_users()

#         # Удаляем деактивированных пользователей из user_data
#         for chat_id, user in list(user_data.items()):
#             if user['id'] in deactivated_users:
#                 del user_data[chat_id]
#                 bot.send_message(chat_id, "Ваш аккаунт был деактивирован. Пожалуйста, обратитесь к администратору.")
        
#         time.sleep(60)  # Проверяем каждые 60 секунд

# def role_monitor():
#     while True:
#         try:
#             check_role_changes()
#         except Exception as e:
#             print(f"Ошибка при проверке ролей: {e}")
#         time.sleep(60)

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
    bot.polling(none_stop=True)
