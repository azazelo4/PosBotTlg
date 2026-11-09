"""Задачи по расписанию: ежедневный отчёт, уведомление о низком остатке, связь с Telegram.

ПК в магазине может быть выключен в момент отчёта, поэтому отправленный день отмечается в job_runs,
а при включении (и затем каждые полчаса) бот досылает отчёты за пропущенные дни с операциями.
"""
import io
import logging
import threading
from datetime import time as dtime, timedelta

from telebot import types

import config
from database import (get_low_stock_products, get_chat_ids_by_roles, get_period_operations, get_job_run,
                      mark_job_run, get_user_by_id)
from state import bot
from utils.common import today, now, h, day_bounds, money
from utils.report_generator import generate_period_report

DAILY_REPORT_JOB = "daily_report"
TELEGRAM_JOB = "telegram"
CATCH_UP_DAYS = 7
_report_lock = threading.Lock()   # плановый отчёт и досылка не должны отправить один день дважды

logger = logging.getLogger(__name__)

MAX_MESSAGE = 3800


def _send_safe(chat_id, text, **kwargs):
    try:
        bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as e:
        logger.warning("Не удалось отправить сообщение в чат %s: %s", chat_id, e)
        return False


def send_daily_report(day=None, late=False):
    """Отчёт за день (по умолчанию сегодня) — в REPORT_CHAT_ID или всем активным администраторам.
    Возвращает True, если отчёт доставлен хотя бы одному получателю."""
    day = day or today()
    recipients = [config.REPORT_CHAT_ID] if config.REPORT_CHAT_ID else (get_chat_ids_by_roles(['admin']) or [])
    if not recipients:
        logger.warning("Ежедневный отчёт: нет получателей (REPORT_CHAT_ID не задан, администраторы ещё не входили в бота).")
        return False
    report = generate_period_report(day, day)
    if report is None:
        logger.error("Ежедневный отчёт не сформирован: ошибка БД.")
        return False
    text, excel, filename = report
    data = excel.getvalue()
    title = (f"📊 Отчёт за {day:%d.%m.%Y} (не был отправлен вовремя: бот был выключен)" if late
             else "📊 Ежедневный отчёт")
    delivered = False
    for chat_id in recipients:
        try:
            bot.send_message(chat_id, title + "\n" + text, parse_mode='HTML')
            bot.send_document(chat_id, types.InputFile(io.BytesIO(data), file_name=filename))
            delivered = True
            logger.info("Отчёт за %s отправлен в чат %s", day, chat_id)
        except Exception as e:
            logger.warning("Отчёт за %s не доставлен в чат %s: %s", day, chat_id, e)
    if delivered:
        mark_job_run(DAILY_REPORT_JOB, day)
    return delivered


def _already_sent(day):
    last = get_job_run(DAILY_REPORT_JOB)
    return bool(last and last.get('last_for_date') and last['last_for_date'] >= day)


def scheduled_daily_report():
    """Задача по расписанию: отчёт за сегодня, если его ещё не дослали."""
    with _report_lock:
        if not _already_sent(today()):
            send_daily_report()


def _due_day(report_time, moment):
    """Последний день, отчёт за который уже должен был уйти."""
    return moment.date() if moment.time() >= dtime(*report_time) else moment.date() - timedelta(days=1)


def catch_up_daily_reports(report_time, moment=None):
    """Дослать отчёты за дни, пропущенные из-за выключенного бота (не больше CATCH_UP_DAYS, только дни с операциями).

    При самом первом запуске просто отмечает «вчера»: досылать за время до установки нечего.
    Возвращает список дней, за которые отчёт отправлен."""
    with _report_lock:
        return _catch_up(report_time, moment or now())


def _catch_up(report_time, moment):
    due = _due_day(report_time, moment)
    last = get_job_run(DAILY_REPORT_JOB)
    if last is None or not last.get('last_for_date'):
        last_day = moment.date() - timedelta(days=1)
        mark_job_run(DAILY_REPORT_JOB, last_day)
    else:
        last_day = last['last_for_date']
    sent = []
    day = max(last_day + timedelta(days=1), due - timedelta(days=CATCH_UP_DAYS - 1))
    while day <= due:
        ops = get_period_operations(*day_bounds(day, day))
        if ops is None:
            break                                    # БД недоступна — попробуем в следующий раз
        if any(ops.values()):
            if not send_daily_report(day, late=True):
                break                                # нет связи — следующие дни тоже не отправятся
            sent.append(day)
        else:
            mark_job_run(DAILY_REPORT_JOB, day)      # пустой день: отчёт не нужен
        day += timedelta(days=1)
    return sent


def telegram_heartbeat():
    """Проверка связи с Telegram: отметка для /health и `posbot status`."""
    try:
        bot.get_me()
    except Exception as e:
        logger.warning("Нет связи с Telegram: %s", e)
        return False
    mark_job_run(TELEGRAM_JOB)
    return True


def send_low_stock_notification():
    products = get_low_stock_products(config.LOW_STOCK_THRESHOLD)
    if not products:
        logger.info("Низких остатков нет.")
        return
    recipients = get_chat_ids_by_roles(['admin', 'manager']) or []
    if not recipients:
        logger.warning("Уведомление о низком остатке: нет получателей.")
        return
    lines = [f"⚠️ <b>Низкий остаток</b> (порог {config.LOW_STOCK_THRESHOLD} шт.)", ""]
    for p in products:
        lines.append(f"• {h(p['model'])} ({h(p['product_type'])}), арт. <code>{h(p['article_number'])}</code> — "
                     f"<b>{p['quantity']}</b> шт., {h(p['Store'])}")
    chunks, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > MAX_MESSAGE:
            chunks.append(current)
            current = ""
        current += line + "\n"
    chunks.append(current)
    for chat_id in recipients:
        for chunk in chunks:
            _send_safe(chat_id, chunk, parse_mode='HTML')
    logger.info("Уведомление о низком остатке (%s позиций) отправлено %s получателям", len(products), len(recipients))


# ---------------------------------------------------------------- фискализация

def _notify_fiscal_done(rec, data):
    """Продавцу — ссылка на чек ОФД, чтобы переслать покупателю."""
    url = data.get("TicketUrl")
    if rec['kind'] not in ('sale', 'return') or not url or not rec.get('user_id'):
        return
    user = get_user_by_id(rec['user_id'])
    if not user or not user.get('chat_id'):
        return
    what = f"чек №{rec['ref_id']}" if rec['kind'] == 'sale' else f"возврат №{rec['ref_id']}"
    offline = " (касса в автономном режиме, в ОФД уйдёт позже)" if data.get("OfflineMode") else ""
    _send_safe(user['chat_id'], f"🧾 Фискальный чек: {what}, {money(rec['total'])}{offline}\n{url}\n"
                                "Ссылку можно переслать покупателю.", disable_web_page_preview=True)


def _notify_fiscal_error(rec, error):
    """Администраторам и менеджерам: Webkassa отклонил операцию — нужно вмешательство."""
    from fiscal import KIND_TITLES
    text = (f"⚠️ <b>Чек не пробит в Webkassa</b>\n{KIND_TITLES.get(rec['kind'], rec['kind'])} №{rec['ref_id']}, "
            f"{money(rec['total']) if rec.get('total') is not None else ''}\n{h(error)}\n\n"
            "Веб-админка → «Фискализация»: «Повторить» или «Оформлен вручную».")
    for chat_id in get_chat_ids_by_roles(['admin', 'manager']) or []:
        _send_safe(chat_id, text, parse_mode='HTML')


def fiscal_tick():
    """Задача планировщика: отправить очередь чеков в Webkassa."""
    from fiscal import process_queue
    return process_queue(notify=_notify_fiscal_done, on_error=_notify_fiscal_error)


# ---------------------------------------------------------------- электронный чек клиенту

def send_customer_receipts():
    """Чеки клиентам, подписанным в Telegram: по номеру, который продавец ввёл на кассе."""
    import customers
    from telebot.apihelper import ApiTelegramException
    from handlers.customer_handler import send_receipt
    from database import get_receipt
    sent = 0
    for row in customers.pending_deliveries() or []:
        receipt = get_receipt(row['id'])
        if not receipt:
            continue
        try:
            send_receipt(row['chat_id'], receipt)
        except ApiTelegramException as e:
            if e.error_code == 403:                  # клиент заблокировал бота — больше не пишем
                customers.unsubscribe(row['chat_id'])
                customers.mark_sent(row['id'])
            logger.warning("Чек №%s клиенту не доставлен: %s", row['id'], e)
            continue
        except Exception as e:
            logger.warning("Чек №%s клиенту не доставлен: %s", row['id'], e)
            continue
        customers.mark_sent(row['id'])
        sent += 1
    return sent
