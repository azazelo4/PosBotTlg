"""Точка входа бота.

Порядок запуска:
1. Подключение к БД и миграции схемы (нужны только переменные БД из .env).
2. Чтение настроек из БД. Если токена ещё нет — бот ждёт, пока его зададут в мастере /setup
   (в логах печатается адрес и код настройки), и проверяет раз в 10 секунд.
3. Регистрация хендлеров, расписание, опрос Telegram.

Изменение токена, часового пояса или времени уведомлений в веб-админке перезапускает процесс
(Docker поднимает контейнер заново), остальные настройки подхватываются на лету.
"""
import logging
import os
import sys
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

import config
import settings_store
from database import init_pool, get_connection, ensure_admin
from schema import ensure_schema

logging.basicConfig(
    level=getattr(logging, (config.LOG_LEVEL or "INFO").upper(), logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger("posbot")

SETTINGS_POLL_SECONDS = 30
TOKEN_WAIT_SECONDS = 10
START_DELAY_SECONDS = 20       # досылка отчётов и проверка связи — вскоре после запуска, не задерживая его
HEARTBEAT_MINUTES = 5


def register_handlers():
    from auth import SessionMiddleware, has_role
    from buttons import BTN_CASH, BTN_STOCK, BTN_STAFF, BTN_BACK, BTN_CANCEL
    from state import bot
    from handlers.navigation import (on_cancel, on_back, on_cash_menu, on_stock_menu, on_staff_menu, on_fallback,
                                     on_stale_callback)
    from handlers.kassa_login import register_kassa_login_handlers
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
    register_kassa_login_handlers()  # /start k_<токен> — до обычного /start
    register_start_handlers()
    register_help_handlers()
    # Навигация регистрируется до операций: срабатывает в любом состоянии и прерывает текущую операцию
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


def _restart_process():
    """Выход с кодом 0: Docker (restart: always) поднимет контейнер с новыми настройками."""
    logging.shutdown()
    os._exit(0)


def make_settings_watcher(restart=_restart_process):
    """Задача планировщика: перечитать настройки; при смене «перезапускаемых» — перезапуск."""
    baseline = settings_store.snapshot(settings_store.RESTART_KEYS)

    def watch():
        if not settings_store.reload():
            return
        current = settings_store.snapshot(settings_store.RESTART_KEYS)
        if current != baseline:
            changed = [k for k, a, b in zip(settings_store.RESTART_KEYS, baseline, current) if a != b]
            logger.warning("Изменены настройки %s — перезапускаю бота.", ", ".join(changed))
            restart()
    return watch


def start_scheduler():
    from utils.common import tz
    from utils.scheduled_jobs import (scheduled_daily_report, send_low_stock_notification, catch_up_daily_reports,
                                      telegram_heartbeat)
    scheduler = BackgroundScheduler(timezone=tz())
    soon = datetime.now(tz()) + timedelta(seconds=START_DELAY_SECONDS)
    report_time = _parse_hhmm(config.DAILY_REPORT_TIME, "DAILY_REPORT_TIME")
    if report_time:
        scheduler.add_job(scheduled_daily_report, 'cron', hour=report_time[0], minute=report_time[1], id='daily_report',
                          misfire_grace_time=3600, coalesce=True)
        # ПК мог быть выключен в момент отчёта: досылаем после запуска и потом каждые полчаса (если не было связи)
        scheduler.add_job(catch_up_daily_reports, 'interval', args=[report_time], minutes=30, id='report_catch_up',
                          next_run_time=soon, coalesce=True, max_instances=1)
        logger.info("Ежедневный отчёт: %02d:%02d (%s)", *report_time, config.TIMEZONE)
    stock_time = _parse_hhmm(config.LOW_STOCK_TIME, "LOW_STOCK_TIME")
    if stock_time:
        scheduler.add_job(send_low_stock_notification, 'cron', hour=stock_time[0], minute=stock_time[1], id='low_stock',
                          misfire_grace_time=3600, coalesce=True)
        logger.info("Уведомление о низком остатке: %02d:%02d (%s)", *stock_time, config.TIMEZONE)
    scheduler.add_job(telegram_heartbeat, 'interval', minutes=HEARTBEAT_MINUTES, id='telegram_heartbeat',
                      next_run_time=soon, coalesce=True, max_instances=1)
    scheduler.add_job(make_settings_watcher(), 'interval', seconds=SETTINGS_POLL_SECONDS, id='settings_watch',
                      coalesce=True, max_instances=1)
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
    settings_store.reload()
    settings_store.adopt_existing_install()


def wait_for_token(sleep=time.sleep):
    """Ждёт, пока токен появится в настройках (мастер /setup). Возвращает токен."""
    hinted = False
    while True:
        settings_store.reload()
        if config.TOKEN and settings_store.is_configured():
            return config.TOKEN
        if not hinted:
            settings_store.log_setup_hint()
            logger.info("Бот ожидает настройки: токен Telegram ещё не задан.")
            hinted = True
        sleep(TOKEN_WAIT_SECONDS)


def main():
    logger.info("Запуск бота (версия %s)...", config.VERSION)
    prepare_database()
    wait_for_token()
    from state import bot
    register_handlers()
    start_scheduler()
    logger.info("Бот запущен, начинаю опрос Telegram.")
    bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=False)


if __name__ == '__main__':
    main()
