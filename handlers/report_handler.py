from telebot import types
from state import bot, user_data, user_state  # Import necessary state
import io
from datetime import date, timedelta
import logging
import openpyxl
import datetime
from database import generate_report

logger = logging.getLogger(__name__)

report_handler = {}

class Report:
    def __init__(self):
        self.today = datetime.date.today()
        self.start_date = None
        self.end_date = None
        self.report_type = None
    
    def reset(self):
        self.today = datetime.date.today()
        self.start_date = None
        self.end_date = None
        self.report_type = None

    def process_message(self, message, chat_id):
        logger.debug(f"Processing message from chat_id={chat_id}: {message.text}")
        if message.text == 'назад':
            self.reset()
            logger.debug("Resetting report state.")
            return None, None, None
        else:
            if message.text == 'Отчеты':
                keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
                button_daily = types.KeyboardButton("Ежедневный")
                button_weekly = types.KeyboardButton("Еженедельный")
                button_monthly = types.KeyboardButton("Ежемесячный")
                button_custom = types.KeyboardButton("Другой")
                button_cancel = types.KeyboardButton("Отмена")
                button_back = types.KeyboardButton("Назад")
                keyboard.add(button_daily, button_weekly, button_monthly, button_custom, button_cancel, button_back)
                self.report_type = 'start'
                logger.debug("User selected 'Отчеты'. Displaying report type options.")
                return None, "Выберите тип отчета:", keyboard
            elif message.text == 'Отмена':
                self.reset()
                logger.debug("User canceled the report generation.")
                return "Отменено.", None, None
            elif message.text in ["Ежедневный", "Еженедельный", "Ежемесячный"]:
                self.report_type = message.text
                logger.debug(f"Report type selected: {self.report_type}")
                start_date, end_date = self.get_report_dates(self.report_type)
                logger.debug(f"Generating report from {start_date} to {end_date}")
                report_data = generate_report(start_date, end_date)
                wb = self.generate_excel_report(report_data)
                response = "Отчет готов."
                logger.debug("Report generated successfully.")
                self.reset()
                return wb, response, None
            elif message.text == "Другой":
                self.report_type = 'custom_start'
                logger.debug("User selected custom report. Prompting for start date.")
                return "Введите дату начала отчета в формате ГГГГ-ММ-ДД:", None, None
            elif self.report_type == 'custom_start':
                try:
                    self.start_date = datetime.datetime.strptime(message.text, "%Y-%m-%d").date()
                    self.report_type = 'custom_end'
                    logger.debug(f"Start date set to {self.start_date}. Prompting for end date.")
                    return "Введите дату окончания отчета в формате ГГГГ-ММ-ДД:", None, None
                except ValueError:
                    logger.warning(f"Invalid start date format received: {message.text}")
                    return "Неверный формат даты. Пожалуйста, введите дату в формате ГГГГ-ММ-ДД:", None, None
            elif self.report_type == 'custom_end':
                try:
                    self.end_date = datetime.datetime.strptime(message.text, "%Y-%m-%d").date()
                    logger.debug(f"End date set to {self.end_date}. Generating custom report.")
                    report_data = generate_report(self.start_date, self.end_date)
                    wb = self.generate_excel_report(report_data)
                    response = "Отчет готов."
                    self.reset()
                    logger.debug("Custom report generated successfully.")
                    return wb, response, None
                except ValueError:
                    logger.warning(f"Invalid end date format received: {message.text}")
                    return "Неверный формат даты. Пожалуйста, введите дату в формате ГГГГ-ММ-ДД:", None, None
            else:
                logger.error(f"Unexpected state encountered: {self.report_type}")
                return "Неожиданное состояние.", None, None

    def reset(self):
        self.start_date = None
        self.end_date = None
        self.report_type = None

    def get_report_dates(self, report_type):
        if report_type == "Ежедневный":
            start_date = self.today - timedelta(days=1)
            end_date = self.today
        elif report_type == "Еженедельный":
            start_date = self.today - timedelta(days=7)
            end_date = self.today
        elif report_type == "Ежемесячный":
            start_date = self.today - timedelta(days=30)
            end_date = self.today
        elif report_type == "Другой":
            start_date = self.start_date
            end_date = self.end_date
        return start_date, end_date

    def generate_excel_report(self, report_data):
        wb = openpyxl.Workbook()
        sheet = wb.active
        sheet['A1'] = 'Название товара'
        sheet['B1'] = 'Продавец'
        sheet['C1'] = 'Магазин'
        sheet['D1'] = 'Сумма продаж'
        sheet['E1'] = 'Дата продажи'
        sheet['F1'] = 'Тип расчета'
        for i, row in enumerate(report_data):
            sheet.cell(row=i+2, column=1).value = row[0]
            sheet.cell(row=i+2, column=2).value = row[1]
            sheet.cell(row=i+2, column=3).value = row[2]
            sheet.cell(row=i+2, column=4).value = row[3]
            sheet.cell(row=i+2, column=5).value = row[4].strftime('%d.%m.%Y')
            sheet.cell(row=i+2, column=6).value = row[5]
        report_buffer = io.BytesIO()
        wb.save(report_buffer)
        report_buffer.seek(0)  # Move the buffer's pointer to the beginning of the file
        return report_buffer

def register_report_handlers(bot_instance, global_user_data):
    global bot
    bot = bot_instance

    @bot.message_handler(func=lambda message: message.text == 'Отчеты')
    def handle_report(message):
        chat_id = message.chat.id
        if chat_id in user_data:
            if chat_id not in report_handler:
                report_handler[chat_id] = Report()
            report = report_handler[chat_id]
            
            if message.text == "Отчеты" or report.report_type is not None:
                wb, response, keyboard = report.process_message(message, chat_id)
                if wb and isinstance(wb, io.BytesIO):
                    filename = f"report_{date.today().isoformat()}.xlsx"
                    bot.send_document(chat_id, wb, filename=filename)
                if response:
                    if keyboard:
                        bot.send_message(chat_id, response, reply_markup=keyboard)
                    else:
                        bot.send_message(chat_id, response)
        else:
            keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            share_contact_button = types.KeyboardButton("Поделиться номером", request_contact=True)
            keyboard.add(share_contact_button)
            bot.send_message(chat_id, "Доступ запрещен.", reply_markup=keyboard)