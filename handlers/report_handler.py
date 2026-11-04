"""Отчёты по кассе за выбранный период."""
import logging
from datetime import timedelta

from telebot import types

from buttons import create_cancel_markup, create_choice_markup
from handlers.common import CASH_ROLES, in_state, is_button, cash_menu, register_simple_cancel
from state import bot, user_state, get_op, start_op, end_op, cancel_current_operation
from utils.common import today, parse_date
from utils.report_generator import generate_period_report

logger = logging.getLogger(__name__)

CHOOSE, CUSTOM_START, CUSTOM_END = "REPORT_CHOOSE", "REPORT_CUSTOM_START", "REPORT_CUSTOM_END"
PERIODS = ["Сегодня", "Вчера", "Эта неделя", "Этот месяц", "Прошлый месяц", "Другой период"]


def period_dates(name, ref=None):
    d = ref or today()
    if name == "Сегодня":
        return d, d
    if name == "Вчера":
        y = d - timedelta(days=1)
        return y, y
    if name == "Эта неделя":
        return d - timedelta(days=d.weekday()), d
    if name == "Этот месяц":
        return d.replace(day=1), d
    if name == "Прошлый месяц":
        last = d.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    return None


def start(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, CHOOSE)
    bot.send_message(chat_id, "За какой период нужен отчёт?", reply_markup=create_choice_markup(PERIODS, 2))


def on_choose(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    if text == "Другой период":
        user_state[chat_id] = CUSTOM_START
        bot.send_message(chat_id, "Дата начала (ДД.ММ.ГГГГ):", reply_markup=create_cancel_markup())
        return
    dates = period_dates(text)
    if not dates:
        bot.send_message(chat_id, "Выберите период кнопкой.", reply_markup=create_choice_markup(PERIODS, 2))
        return
    send_report(chat_id, *dates)


def on_custom_start(message):
    chat_id = message.chat.id
    start_date = parse_date(message.text)
    if not start_date:
        bot.send_message(chat_id, "Не понял дату. Формат: ДД.ММ.ГГГГ, например 01.11.2026.", reply_markup=create_cancel_markup())
        return
    if start_date > today():
        bot.send_message(chat_id, "Дата начала не может быть в будущем.", reply_markup=create_cancel_markup())
        return
    get_op(chat_id)['start'] = start_date
    user_state[chat_id] = CUSTOM_END
    bot.send_message(chat_id, f"Начало: {start_date:%d.%m.%Y}. Дата окончания (ДД.ММ.ГГГГ) или «Сегодня»:",
                     reply_markup=create_choice_markup(["Сегодня"], 1))


def on_custom_end(message):
    chat_id = message.chat.id
    start_date = get_op(chat_id)['start']
    end_date = today() if (message.text or '').strip() == "Сегодня" else parse_date(message.text)
    if not end_date:
        bot.send_message(chat_id, "Не понял дату. Формат: ДД.ММ.ГГГГ.", reply_markup=create_choice_markup(["Сегодня"], 1))
        return
    if end_date < start_date:
        bot.send_message(chat_id, "Дата окончания раньше даты начала. Введите снова.", reply_markup=create_choice_markup(["Сегодня"], 1))
        return
    send_report(chat_id, start_date, end_date)


def send_report(chat_id, start_date, end_date):
    end_op(chat_id)
    bot.send_chat_action(chat_id, 'upload_document')
    try:
        report = generate_period_report(start_date, end_date)
    except Exception:
        logger.exception("Chat %s: ошибка формирования отчёта", chat_id)
        report = None
    if report is None:
        cash_menu(chat_id, "❌ Не удалось сформировать отчёт: ошибка базы данных. Попробуйте позже.")
        return
    text, excel, filename = report
    bot.send_message(chat_id, text, parse_mode='HTML')
    bot.send_document(chat_id, types.InputFile(excel, file_name=filename), caption="Подробности по всем операциям — в файле.",
                      reply_markup=None)
    cash_menu(chat_id, "Выберите следующее действие:")


def register_report_handlers():
    register_simple_cancel("REPORT_", "Подготовка отчёта")
    bot.register_message_handler(start, func=is_button("Отчеты", *CASH_ROLES))
    bot.register_message_handler(on_choose, func=in_state(CHOOSE))
    bot.register_message_handler(on_custom_start, func=in_state(CUSTOM_START))
    bot.register_message_handler(on_custom_end, func=in_state(CUSTOM_END))
