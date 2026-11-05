import logging
import sys

from apscheduler.schedulers.background import BackgroundScheduler
from telebot import types

import config
from auth import SessionMiddleware, has_role
from buttons import (create_cash_operations_buttons, create_stock_management_buttons, create_staff_management_buttons,
                     get_keyboard_by_role, create_share_contact_markup, BTN_CASH, BTN_STOCK, BTN_STAFF, BTN_BACK, BTN_CANCEL)
from database import init_pool, get_connection, ensure_admin
from schema import ensure_schema
from state import bot, user_state, cancel_current_operation
from utils.common import TZ

logging.basicConfig(
    level=getattr(logging, (config.LOG_LEVEL or "INFO").upper(), logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger("posbot")


# ---------------------------------------------------------------- навигация
# Регистрируются первыми: срабатывают в любом состоянии и прерывают текущую операцию.

def on_cancel(message: types.Message):
    chat_id = message.chat.id
    if not cancel_current_operation(chat_id, notify=True):
        bot.send_message(chat_id, "Нет активной операции.", reply_markup=get_keyboard_by_role(chat_id))


def on_back(message: types.Message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    bot.send_message(chat_id, "Главное меню.", reply_markup=get_keyboard_by_role(chat_id))


def on_cash_menu(message: types.Message):
    cancel_current_operation(message.chat.id, notify=False)
    bot.send_message(message.chat.id, "Кассовые операции:", reply_markup=create_cash_operations_buttons())


def on_stock_menu(message: types.Message):
    cancel_current_operation(message.chat.id, notify=False)
    bot.send_message(message.chat.id, "Управление складом:", reply_markup=create_stock_management_buttons())


def on_staff_menu(message: types.Message):
    cancel_current_operation(message.chat.id, notify=False)
    bot.send_message(message.chat.id, "Управление персоналом:", reply_markup=create_staff_management_buttons())


def on_fallback(message: types.Message):
    """Последний хендлер: ничего не подошло."""
    chat_id = message.chat.id
    if not has_role(chat_id):
        bot.send_message(chat_id, "Чтобы начать работу, поделитесь номером телефона.", reply_markup=create_share_contact_markup())
        return
    if user_state.get(chat_id):
        # Сообщение не того типа (фото, стикер) посреди операции
        bot.send_message(chat_id, "Ожидаю текстовый ответ. Чтобы прервать операцию, нажмите «Отмена».")
        return
    bot.send_message(chat_id, "Не понял команду. Выберите действие в меню.", reply_markup=get_keyboard_by_role(chat_id))


def on_stale_callback(call: types.CallbackQuery):
    bot.answer_callback_query(call.id, "Кнопка устарела.")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass


def register_handlers():
    from handlers.start_handler import register_start_handlers
    from handlers.help_handler import register_help_handlers
    from handlers.sales_handler import register_sales_handlers
    from handlers.return_handler import register_return_handlers
    from handlers.buyback_handler import register_buyback_handlers
    from handlers.tradein_handler import register_tradein_handlers
    from handlers.cash_withdrawal_handler import register_withdrawal_handlers
    from handlers.report_handler import register_report_handlers
    from handlers.stock_handler import register_stock_handlers
    from handlers.labels_handler import register_labels_handlers
    from handlers.import_handler import register_import_handlers
    from handlers.web_handler import register_web_handlers
    from user_management import configure_user_management

    bot.setup_middleware(SessionMiddleware())
    register_start_handlers()
    register_help_handlers()
    bot.register_message_handler(on_cancel, commands=['cancel'])
    bot.register_message_handler(on_cancel, func=lambda m: m.text == BTN_CANCEL)
    bot.register_message_handler(on_back, func=lambda m: m.text == BTN_BACK and has_role(m.chat.id))
    bot.register_message_handler(on_cash_menu, func=lambda m: m.text == BTN_CASH and has_role(m.chat.id))
    bot.register_message_handler(on_stock_menu, func=lambda m: m.text == BTN_STOCK and has_role(m.chat.id, 'admin', 'manager'))
    bot.register_message_handler(on_staff_menu, func=lambda m: m.text == BTN_STAFF and has_role(m.chat.id, 'admin'))

    register_sales_handlers()
    register_return_handlers()
    register_buyback_handlers()
    register_tradein_handlers()
    register_withdrawal_handlers()
    register_report_handlers()
    register_stock_handlers()
    register_labels_handlers()
    register_import_handlers()
    register_web_handlers()
    configure_user_management()

    bot.register_message_handler(on_fallback, content_types=['text', 'photo', 'document', 'sticker', 'voice', 'video', 'audio', 'location'])
    bot.register_callback_query_handler(on_stale_callback, func=lambda c: True)


def _parse_hhmm(value, name):
    if not value:
        return None
    try:
        hour, minute = map(int, value.split(':'))
        assert 0 <= hour < 24 and 0 <= minute < 60
        return hour, minute
    except (ValueError, AssertionError):
        logger.error("%s=%r — неверный формат, ожидается HH:MM. Задача отключена.", name, value)
        return None


def start_scheduler():
    from utils.scheduled_jobs import send_daily_report, send_low_stock_notification
    scheduler = BackgroundScheduler(timezone=TZ)
    report_time = _parse_hhmm(config.DAILY_REPORT_TIME, "DAILY_REPORT_TIME")
    if report_time:
        scheduler.add_job(send_daily_report, 'cron', hour=report_time[0], minute=report_time[1], id='daily_report',
                          misfire_grace_time=3600, coalesce=True)
        logger.info("Ежедневный отчёт: %02d:%02d (%s)", *report_time, config.TIMEZONE)
    stock_time = _parse_hhmm(config.LOW_STOCK_TIME, "LOW_STOCK_TIME")
    if stock_time:
        scheduler.add_job(send_low_stock_notification, 'cron', hour=stock_time[0], minute=stock_time[1], id='low_stock',
                          misfire_grace_time=3600, coalesce=True)
        logger.info("Уведомление о низком остатке: %02d:%02d (%s)", *stock_time, config.TIMEZONE)
    scheduler.start()
    return scheduler


def prepare_database():
    init_pool()
    connection = get_connection()
    try:
        ensure_schema(connection, config.DEFAULT_STORES)
    finally:
        connection.close()
    if config.ADMIN_PHONE:
        ok, result = ensure_admin(config.ADMIN_PHONE, config.ADMIN_NAME)
        if ok and result != "exists":
            logger.info("Администратор %s: %s", config.ADMIN_PHONE, result)
        elif not ok:
            logger.error("ADMIN_PHONE: %s", result)


def main():
    logger.info("Запуск бота...")
    prepare_database()
    register_handlers()
    start_scheduler()
    logger.info("Бот запущен, начинаю опрос Telegram.")
    bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=False)


if __name__ == '__main__':
    main()
