from telebot import types
from state import bot, user_data # Removed user_state import here, will be passed to register function
import io
from datetime import date, timedelta, datetime # Added datetime for strptime
import logging
import openpyxl
# from enum import Enum # Not strictly needed if ReportState strings are used
from database import generate_report
from buttons import create_cancel_markup, create_cash_operations_buttons # For returning to a relevant menu

logger = logging.getLogger(__name__)

report_handler = {} # This will store Report instances per chat_id

# States for the Report class internal FSM
REPORT_STATE_IDLE = "IDLE"
REPORT_STATE_AWAITING_TYPE = "REPORT_AWAITING_TYPE"
REPORT_STATE_AWAITING_CUSTOM_START_DATE = "REPORT_AWAITING_CUSTOM_START_DATE"
REPORT_STATE_AWAITING_CUSTOM_END_DATE = "REPORT_AWAITING_CUSTOM_END_DATE"


class Report:
    def __init__(self, chat_id, global_user_state_dict):
        self.chat_id = chat_id
        self.global_user_state = global_user_state_dict # Reference to the main user_state dictionary
        self.today = date.today()
        self.start_date_val = None
        self.end_date_val = None
        self.current_internal_state = REPORT_STATE_IDLE # Internal state for the report object
        self.reset() # Initialize properly

    def reset(self):
        logger.debug(f"Chat {self.chat_id}: Resetting Report instance state.")
        self.start_date_val = None
        self.end_date_val = None
        self.current_internal_state = REPORT_STATE_IDLE
        # Crucially, remove this chat_id from the global user_state if it was set by this handler
        if self.global_user_state.get(self.chat_id, "").startswith("REPORT_"):
            self.global_user_state.pop(self.chat_id, None)
        # Also remove from the global report_handler dictionary upon true reset/completion
        if self.chat_id in report_handler:
             del report_handler[self.chat_id]


    def start_report_generation(self):
        logger.info(f"Chat {self.chat_id}: Starting report generation process.")
        self.current_internal_state = REPORT_STATE_AWAITING_TYPE
        self.global_user_state[self.chat_id] = "REPORT_AWAITING_TYPE" # Set global state

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        button_daily = types.KeyboardButton("Ежедневный")
        button_weekly = types.KeyboardButton("Еженедельный")
        button_monthly = types.KeyboardButton("Ежемесячный")
        button_custom = types.KeyboardButton("Другой")
        button_cancel = types.KeyboardButton("Отмена")
        # Removed "Назад" from here, "Отмена" should take them to main menu
        keyboard.add(button_daily, button_weekly, button_monthly, button_custom, button_cancel)
        bot.send_message(self.chat_id, "Выберите тип отчета:", reply_markup=keyboard)

    def handle_message(self, message: types.Message):
        text = message.text.strip()
        logger.debug(f"Chat {self.chat_id}: Report handler processing text: '{text}', current internal state: {self.current_internal_state}")

        if text == "Отмена":
            self.cancel_operation()
            return

        if self.current_internal_state == REPORT_STATE_AWAITING_TYPE:
            if text in ["Ежедневный", "Еженедельный", "Ежемесячный"]:
                self.generate_and_send_report(text)
            elif text == "Другой":
                self.current_internal_state = REPORT_STATE_AWAITING_CUSTOM_START_DATE
                self.global_user_state[self.chat_id] = "REPORT_AWAITING_CUSTOM_START_DATE"
                bot.send_message(self.chat_id, "Введите дату начала отчета в формате ГГГГ-ММ-ДД:", reply_markup=create_cancel_markup())
            else:
                bot.send_message(self.chat_id, "Пожалуйста, выберите тип отчета с помощью кнопок.", reply_markup=self._get_type_selection_keyboard())

        elif self.current_internal_state == REPORT_STATE_AWAITING_CUSTOM_START_DATE:
            try:
                self.start_date_val = datetime.strptime(text, "%Y-%m-%d").date()
                self.current_internal_state = REPORT_STATE_AWAITING_CUSTOM_END_DATE
                self.global_user_state[self.chat_id] = "REPORT_AWAITING_CUSTOM_END_DATE"
                bot.send_message(self.chat_id, f"Дата начала: {self.start_date_val.strftime('%Y-%m-%d')}. Введите дату окончания отчета (ГГГГ-ММ-ДД):", reply_markup=create_cancel_markup())
            except ValueError:
                bot.send_message(self.chat_id, "Неверный формат даты. Пожалуйста, введите дату начала в формате ГГГГ-ММ-ДД:", reply_markup=create_cancel_markup())

        elif self.current_internal_state == REPORT_STATE_AWAITING_CUSTOM_END_DATE:
            try:
                self.end_date_val = datetime.strptime(text, "%Y-%m-%d").date()
                if self.end_date_val < self.start_date_val:
                    bot.send_message(self.chat_id, "Дата окончания не может быть раньше даты начала. Введите корректную дату окончания:", reply_markup=create_cancel_markup())
                    return
                self.generate_and_send_report("Другой") # "Другой" indicates custom dates
            except ValueError:
                bot.send_message(self.chat_id, "Неверный формат даты. Пожалуйста, введите дату окончания в формате ГГГГ-ММ-ДД:", reply_markup=create_cancel_markup())
        else:
             logger.warning(f"Chat {self.chat_id}: Report handler in unexpected state: {self.current_internal_state}")
             self.cancel_operation() # Reset if in weird state

    def _get_type_selection_keyboard(self):
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        button_daily = types.KeyboardButton("Ежедневный")
        button_weekly = types.KeyboardButton("Еженедельный")
        button_monthly = types.KeyboardButton("Ежемесячный")
        button_custom = types.KeyboardButton("Другой")
        button_cancel = types.KeyboardButton("Отмена")
        keyboard.add(button_daily, button_weekly, button_monthly, button_custom, button_cancel)
        return keyboard

    def get_report_dates(self, report_type_str):
        start_dt, end_dt = None, None
        if report_type_str == "Ежедневный":
            start_dt = self.today - timedelta(days=0) # Report for today
            end_dt = self.today
        elif report_type_str == "Еженедельный":
            start_dt = self.today - timedelta(days=self.today.weekday() + 7) # Start of last week (Mon)
            end_dt = start_dt + timedelta(days=6) # End of last week (Sun)
            # Or if "last 7 days":
            # start_dt = self.today - timedelta(days=6)
            # end_dt = self.today
        elif report_type_str == "Ежемесячный":
            first_day_current_month = self.today.replace(day=1)
            end_dt = first_day_current_month - timedelta(days=1) # Last day of previous month
            start_dt = end_dt.replace(day=1) # First day of previous month
        elif report_type_str == "Другой": # Custom
            start_dt = self.start_date_val
            end_dt = self.end_date_val
        return start_dt, end_dt

    def generate_excel_report_file(self, report_data_list):
        wb = openpyxl.Workbook()
        sheet = wb.active
        headers = ['Тип товара', 'Модель', 'Продавец', 'Магазин', 'Сумма продажи', 'Дата продажи', 'Тип расчета']
        sheet.append(headers)
        
        if not report_data_list: # Handle empty report data
            sheet.append(["Нет данных за выбранный период."])
        else:
            for row_tuple in report_data_list:
                # Ensure row_tuple has the correct number of elements matching headers
                # Convert datetime objects to string for Excel
                processed_row = list(row_tuple)
                if len(processed_row) > 5 and isinstance(processed_row[5], datetime): # Assuming sale_date is at index 5
                    processed_row[5] = processed_row[5].strftime('%Y-%m-%d %H:%M:%S')
                elif len(processed_row) > 5 and isinstance(processed_row[5], date):
                     processed_row[5] = processed_row[5].strftime('%Y-%m-%d')

                sheet.append(processed_row)
        
        report_buffer = io.BytesIO()
        wb.save(report_buffer)
        report_buffer.seek(0)
        return report_buffer

    def generate_and_send_report(self, report_type_text):
        logger.info(f"Chat {self.chat_id}: Generating '{report_type_text}' report.")
        s_date, e_date = self.get_report_dates(report_type_text)

        if s_date is None or e_date is None:
            logger.error(f"Chat {self.chat_id}: Could not determine dates for report type '{report_type_text}'.")
            bot.send_message(self.chat_id, "Не удалось определить даты для отчета. Попробуйте снова.", reply_markup=create_cash_operations_buttons())
            self.reset()
            return

        bot.send_message(self.chat_id, f"Генерирую отчет за период с {s_date.strftime('%Y-%m-%d')} по {e_date.strftime('%Y-%m-%d')}...", reply_markup=types.ReplyKeyboardRemove())
        
        try:
            db_report_data = generate_report(s_date, e_date) # This is a list of tuples
            if db_report_data is None: # Check if generate_report explicitly returns None on error
                logger.error(f"Chat {self.chat_id}: Database query for report returned None.")
                bot.send_message(self.chat_id, "Ошибка при получении данных из базы. Отчет не может быть создан.", reply_markup=create_cash_operations_buttons())
                self.reset()
                return

            excel_file_buffer = self.generate_excel_report_file(db_report_data)
            filename = f"report_{s_date.strftime('%Y%m%d')}_{e_date.strftime('%Y%m%d')}.xlsx"
            
            bot.send_document(self.chat_id, excel_file_buffer, visible_file_name=filename, caption="Ваш отчет готов.")
            bot.send_message(self.chat_id, "Выберите следующее действие:",reply_markup=create_cash_operations_buttons()) # Return to cash ops menu

        except Exception as e:
            logger.exception(f"Chat {self.chat_id}: Error during report generation or sending.")
            bot.send_message(self.chat_id, "Произошла ошибка при создании отчета. Пожалуйста, попробуйте позже.", reply_markup=create_cash_operations_buttons())
        finally:
            self.reset()


    def cancel_operation(self):
        logger.info(f"Chat {self.chat_id}: Report generation cancelled by user.")
        bot.send_message(self.chat_id, "Генерация отчета отменена.", reply_markup=create_cash_operations_buttons()) # Back to cash ops
        self.reset()


def register_report_handlers(bot_instance, global_user_data_dict, global_user_state_dict):
    # bot_instance is already the global bot from state.py, so direct use is fine.
    # global_user_data_dict is user_data from state.py
    # global_user_state_dict is user_state from state.py

    @bot.message_handler(func=lambda msg: msg.text == 'Отчеты' and \
                                       global_user_data_dict.get(msg.chat.id, {}).get('is_active', 0) == 1 and \
                                       global_user_data_dict.get(msg.chat.id, {}).get('role') in ['admin', 'cashier'])
    def reports_entry_point(message: types.Message):
        chat_id = message.chat.id
        logger.debug(f"Chat {chat_id}: 'Отчеты' button pressed.")
        if chat_id not in report_handler or report_handler[chat_id].current_internal_state == REPORT_STATE_IDLE:
            report_handler[chat_id] = Report(chat_id, global_user_state_dict)
        report_handler[chat_id].start_report_generation()

    @bot.message_handler(func=lambda msg: global_user_state_dict.get(msg.chat.id, "").startswith("REPORT_"))
    def handle_report_steps(message: types.Message):
        chat_id = message.chat.id
        if chat_id in report_handler:
            report_instance = report_handler[chat_id]
            report_instance.handle_message(message)
        else:
            logger.warning(f"Chat {chat_id}: Received message for report step, but no Report instance found. State: {global_user_state_dict.get(chat_id)}. Resetting.")
            global_user_state_dict.pop(chat_id, None)
            # Potentially send to main menu or cash ops menu
            from main import get_keyboard_by_role # Careful with this import
            bot.send_message(chat_id, "Произошла ошибка в процессе отчета. Пожалуйста, начните заново.", reply_markup=get_keyboard_by_role(chat_id))