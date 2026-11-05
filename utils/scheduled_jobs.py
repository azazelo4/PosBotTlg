"""Задачи по расписанию: ежедневный отчёт и уведомление о низком остатке."""
import io
import logging

from telebot import types

import config
from database import get_low_stock_products, get_chat_ids_by_roles
from state import bot
from utils.common import today, h
from utils.report_generator import generate_period_report

logger = logging.getLogger(__name__)

MAX_MESSAGE = 3800


def _send_safe(chat_id, text, **kwargs):
    try:
        bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as e:
        logger.warning("Не удалось отправить сообщение в чат %s: %s", chat_id, e)
        return False


def send_daily_report():
    """Отчёт за сегодня — в REPORT_CHAT_ID или всем активным администраторам."""
    day = today()
    recipients = [config.REPORT_CHAT_ID] if config.REPORT_CHAT_ID else (get_chat_ids_by_roles(['admin']) or [])
    if not recipients:
        logger.warning("Ежедневный отчёт: нет получателей (REPORT_CHAT_ID не задан, администраторы ещё не входили в бота).")
        return
    report = generate_period_report(day, day)
    if report is None:
        logger.error("Ежедневный отчёт не сформирован: ошибка БД.")
        return
    text, excel, filename = report
    data = excel.getvalue()
    for chat_id in recipients:
        try:
            bot.send_message(chat_id, "📊 Ежедневный отчёт\n" + text, parse_mode='HTML')
            bot.send_document(chat_id, types.InputFile(io.BytesIO(data), file_name=filename))
            logger.info("Ежедневный отчёт отправлен в чат %s", chat_id)
        except Exception as e:
            logger.warning("Ежедневный отчёт не доставлен в чат %s: %s", chat_id, e)


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
