from telebot import types
from state import bot, user_data
import io
from datetime import date, timedelta, datetime
import logging
import openpyxl
from database import generate_report
from buttons import create_cancel_markup, create_cash_operations_buttons
# import mysql.connector # Not strictly needed if we don't use mysql.connector.custom specific types

logger = logging.getLogger(__name__)

report_handler = {}

REPORT_STATE_IDLE = "IDLE"
REPORT_STATE_AWAITING_TYPE = "REPORT_AWAITING_TYPE"
REPORT_STATE_AWAITING_CUSTOM_START_DATE = "REPORT_AWAITING_CUSTOM_START_DATE"
REPORT_STATE_AWAITING_CUSTOM_END_DATE = "REPORT_AWAITING_CUSTOM_END_DATE"


class Report:
    def __init__(self, chat_id, global_user_state_dict):
        self.chat_id = chat_id
        self.global_user_state = global_user_state_dict
        self.today = date.today()
        self.start_date_val = None
        self.end_date_val = None
        self.current_internal_state = REPORT_STATE_IDLE
        # self.reset() # reset is called by the entry point if needed, or on explicit cancel/completion

    def reset(self):
        logger.debug(f"Chat {self.chat_id}: Resetting Report instance state.")
        self.start_date_val = None
        self.end_date_val = None
        self.current_internal_state = REPORT_STATE_IDLE
        if self.global_user_state.get(self.chat_id, "").startswith("REPORT_"):
            self.global_user_state.pop(self.chat_id, None)
        if self.chat_id in report_handler:
             del report_handler[self.chat_id]


    def start_report_generation(self):
        logger.info(f"Chat {self.chat_id}: Starting report generation process.")
        self.current_internal_state = REPORT_STATE_AWAITING_TYPE
        self.global_user_state[self.chat_id] = "REPORT_AWAITING_TYPE"

        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        button_daily = types.KeyboardButton("Ежедневный")
        button_weekly = types.KeyboardButton("Еженедельный")
        button_monthly = types.KeyboardButton("Ежемесячный")
        button_custom = types.KeyboardButton("Другой")
        button_cancel = types.KeyboardButton("Отмена")
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
                self.generate_and_send_report("Другой")
            except ValueError:
                bot.send_message(self.chat_id, "Неверный формат даты. Пожалуйста, введите дату окончания в формате ГГГГ-ММ-ДД:", reply_markup=create_cancel_markup())
        else:
             logger.warning(f"Chat {self.chat_id}: Report handler in unexpected state: {self.current_internal_state}")
             self.cancel_operation()

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
            start_dt = self.today
            end_dt = self.today
        elif report_type_str == "Еженедельный":
            start_dt = self.today - timedelta(days=self.today.weekday())
            end_dt = start_dt + timedelta(days=6)
        elif report_type_str == "Ежемесячный":
            start_dt = self.today.replace(day=1)
            next_month = self.today.replace(day=28) + timedelta(days=4)
            end_dt = next_month - timedelta(days=next_month.day)
        elif report_type_str == "Другой":
            start_dt = self.start_date_val
            end_dt = self.end_date_val
        return start_dt, end_dt

    def generate_excel_report_file(self, report_data_list):
        wb = openpyxl.Workbook()
        sheet = wb.active
        headers = [
            'ID Продажи', 'Дата продажи', 'Сумма продажи', 'Тип расчета',
            'Артикул', 'Тип товара', 'Модель', 'Тип металла',
            'Вес', 'Цена за грамм', 'Цена товара (перв.)',
            'Производитель', 'Комментарии',
            'Продавец', 'Магазин товара' # 'Магазин пользователя' removed
        ]
        sheet.append(headers)

        if not report_data_list:
            sheet.append(["Нет данных за выбранный период."])
        else:
            for row_tuple in report_data_list:
                processed_row = list(row_tuple)

                # Format datetime objects (assuming sale_date is at index 1 based on query order)
                if len(processed_row) > 1:
                    if isinstance(processed_row[1], datetime):
                        processed_row[1] = processed_row[1].strftime('%Y-%m-%d %H:%M:%S') # Assuming sale_date is the second column
                    elif isinstance(processed_row[1], date):
                         processed_row[1] = processed_row[1].strftime('%Y-%m-%d')

                # Convert non-standard types (like Decimal) to float or string, and None to empty string
                for i, cell_value in enumerate(processed_row):
                    # Check for Decimal type more generally or rely on database returning standard types
                    # For a generic Decimal check (from any library, like Python's decimal.Decimal or a DB driver's):
                    if hasattr(cell_value, 'as_tuple') and hasattr(cell_value, 'quantize'): # Heuristic for Decimal
                        processed_row[i] = float(cell_value)
                    elif not isinstance(cell_value, (str, int, float, bool, date, datetime)) and cell_value is not None:
                        # If it's not a standard type openpyxl likes, convert to string as a fallback
                        logger.debug(f"Converting non-standard type {type(cell_value)} to string for Excel: {cell_value}")
                        processed_row[i] = str(cell_value)
                    # product_store_name is now from p.Store
                    elif cell_value is None: # Convert None to empty string for Excel
                        processed_row[i] = ""
                
                if len(processed_row) == len(headers):
                    sheet.append(processed_row)
                else:
                    logger.warning(f"Chat {self.chat_id}: Mismatch between data row length ({len(processed_row)}) and header length ({len(headers)}). Row: {processed_row}")
                    sheet.append(processed_row[:len(headers)])


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
            db_report_data = generate_report(s_date, e_date)
            if db_report_data is None:
                logger.error(f"Chat {self.chat_id}: Database query for report returned None.")
                bot.send_message(self.chat_id, "Ошибка при получении данных из базы. Отчет не может быть создан.", reply_markup=create_cash_operations_buttons())
                self.reset()
                return

            excel_file_buffer = self.generate_excel_report_file(db_report_data)
            filename = f"report_{s_date.strftime('%Y%m%d')}_{e_date.strftime('%Y%m%d')}.xlsx"

            bot.send_document(self.chat_id, excel_file_buffer, visible_file_name=filename, caption="Ваш отчет готов. Выберите следующее действие:", reply_markup=create_cash_operations_buttons())

        except Exception as e:
            logger.exception(f"Chat {self.chat_id}: Error during report generation or sending.")
            bot.send_message(self.chat_id, "Произошла ошибка при создании отчета. Пожалуйста, попробуйте позже.", reply_markup=create_cash_operations_buttons())
        finally:
            self.reset()


    def cancel_operation(self):
        logger.info(f"Chat {self.chat_id}: Report generation cancelled by user.")
        bot.send_message(self.chat_id, "Генерация отчета отменена.", reply_markup=create_cash_operations_buttons())
        self.reset()


def register_report_handlers(bot_instance, global_user_data_dict, global_user_state_dict):

    @bot.message_handler(func=lambda msg: msg.text == 'Отчеты' and \
                                       global_user_data_dict.get(msg.chat.id, {}).get('is_active', 0) == 1 and \
                                       global_user_data_dict.get(msg.chat.id, {}).get('role') in ['admin', 'cashier'])
    def reports_entry_point(message: types.Message):
        chat_id = message.chat.id
        logger.debug(f"Chat {chat_id}: 'Отчеты' button pressed.")

        if chat_id in report_handler and report_handler[chat_id].current_internal_state != REPORT_STATE_IDLE:
            logger.warning(f"Chat {chat_id}: Report generation already in progress. State: {report_handler[chat_id].current_internal_state}. Resetting previous.")
            report_handler[chat_id].reset()

        report_handler[chat_id] = Report(chat_id, global_user_state_dict)
        report_handler[chat_id].start_report_generation()

    @bot.message_handler(func=lambda msg: global_user_state_dict.get(msg.chat.id, "").startswith("REPORT_"))
    def handle_report_steps(message: types.Message):
        chat_id = message.chat.id
        if chat_id in report_handler:
            report_instance = report_handler[chat_id]
            report_instance.handle_message(message)
        else:
            logger.warning(f"Chat {chat_id}: Received message for report step, but no Report instance found. State: {global_user_state_dict.get(chat_id)}. Resetting state.")
            global_user_state_dict.pop(chat_id, None)
            from main import get_keyboard_by_role
            bot.send_message(chat_id, "Произошла ошибка в процессе отчета. Пожалуйста, начните заново.", reply_markup=get_keyboard_by_role(chat_id))