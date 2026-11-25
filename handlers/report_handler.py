"""Отчёты по кассе за выбранный период."""
import logging
from datetime import timedelta

from telebot import types

from handlers.buttons import create_cancel_markup, create_choice_markup
from handlers.common import store_names, store_id_by_name, CASH_ROLES, in_state, is_button, cash_menu, register_simple_cancel
from core.state import bot, user_state, user_data, get_op, start_op, end_op, cancel_current_operation
from utils.common import today, parse_date
from utils.report_generator import generate_period_report

logger = logging.getLogger(__name__)

CHOOSE, CUSTOM_START, CUSTOM_END, STORE = "REPORT_CHOOSE", "REPORT_CUSTOM_START", "REPORT_CUSTOM_END", "REPORT_STORE"
ALL_STORES = "Все магазины"
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


def _is_cashier(chat_id):
    return (user_data.get(chat_id) or {}).get('role') == 'cashier'


def start(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, CHOOSE)
    question = ("Мои продажи: за какой период?" if _is_cashier(chat_id) else "За какой период нужен отчёт?")
    bot.send_message(chat_id, question, reply_markup=create_choice_markup(PERIODS, 2))


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
    _ask_store_or_send(chat_id, *dates)


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
    _ask_store_or_send(chat_id, start_date, end_date)


def _ask_store_or_send(chat_id, start_date, end_date):
    if _is_cashier(chat_id):                 # продавец видит свои операции по всем магазинам
        send_report(chat_id, start_date, end_date, own=True)
        return
    names = store_names()
    if len(names) < 2:
        send_report(chat_id, start_date, end_date)
        return
    get_op(chat_id)['dates'] = (start_date, end_date)
    user_state[chat_id] = STORE
    bot.send_message(chat_id, "По какому магазину?", reply_markup=create_choice_markup([ALL_STORES] + names, 2))


def on_store(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    store_id = store_id_by_name(text)
    if not store_id and text != ALL_STORES:
        bot.send_message(chat_id, "Выберите магазин кнопкой.", reply_markup=create_choice_markup([ALL_STORES] + store_names(), 2))
        return
    send_report(chat_id, *get_op(chat_id)['dates'], store_id=store_id, store_name=text if store_id else None)


def send_report(chat_id, start_date, end_date, store_id=None, store_name=None, own=False):
    end_op(chat_id)
    bot.send_chat_action(chat_id, 'upload_document')
    me = user_data.get(chat_id) or {}
    try:
        report = generate_period_report(start_date, end_date, store_id, store_name,
                                        user_id=me.get('id') if own else None,
                                        user_name=" ".join(p for p in (me.get('name'), me.get('surename')) if p) if own else None)
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
    bot.register_message_handler(on_store, func=in_state(STORE))
