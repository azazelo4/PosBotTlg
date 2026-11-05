"""Ценники: выбор товаров → количество этикеток → PDF под размер этикетки."""
import io
import logging
import re
from datetime import timedelta

from telebot import types

from buttons import create_cancel_markup, create_choice_markup, create_stock_management_buttons
import config
from database import get_products_by_articles, get_products_for_labels
from handlers.common import store_names, store_id_by_name, STOCK_ROLES, in_state, is_button, register_simple_cancel
from state import bot, user_state, get_op, start_op, end_op, cancel_current_operation
from utils.common import today, now, h
from utils.labels import generate_labels_pdf

logger = logging.getLogger(__name__)

MODE, ARTICLES, STORE, COPIES = "LABELS_MODE", "LABELS_ARTICLES", "LABELS_STORE", "LABELS_COPIES"
BY_ARTICLES, TODAY, WEEK, ALL_IN_STOCK = "По артикулам", "Поступления сегодня", "Поступления за 7 дней", "Все в наличии"
ONE, BY_STOCK = "По 1 на артикул", "По количеству на складе"
ALL_STORES = "Все магазины"


def stock_menu(chat_id, text):
    bot.send_message(chat_id, text, reply_markup=create_stock_management_buttons())


def start(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, MODE)
    bot.send_message(chat_id, f"Ценники {config.LABEL_WIDTH_MM:g}×{config.LABEL_HEIGHT_MM:g} мм. Для каких товаров?",
                     reply_markup=create_choice_markup([BY_ARTICLES, TODAY, WEEK, ALL_IN_STOCK], 2))


def on_mode(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    op = get_op(chat_id)
    if text == BY_ARTICLES:
        user_state[chat_id] = ARTICLES
        bot.send_message(chat_id, "Введите артикулы через пробел, запятую или с новой строки:", reply_markup=create_cancel_markup())
    elif text in (TODAY, WEEK):
        op['filter'] = {'arrived_from': today() - timedelta(days=0 if text == TODAY else 6)}
        _ask_copies(chat_id)
    elif text == ALL_IN_STOCK:
        user_state[chat_id] = STORE
        bot.send_message(chat_id, "Какой магазин?", reply_markup=create_choice_markup(store_names() + [ALL_STORES], 2))
    else:
        bot.send_message(chat_id, "Выберите вариант кнопкой.", reply_markup=create_choice_markup([BY_ARTICLES, TODAY, WEEK, ALL_IN_STOCK], 2))


def on_articles(message):
    chat_id = message.chat.id
    articles = list(dict.fromkeys(a for a in re.split(r"[\s,;]+", message.text or '') if a))
    if not articles:
        bot.send_message(chat_id, "Не вижу артикулов. Введите хотя бы один.", reply_markup=create_cancel_markup())
        return
    found = get_products_by_articles(articles[:500]) or []
    missing = [a for a in articles if a not in {p['article_number'] for p in found}]
    if not found:
        bot.send_message(chat_id, "Ни один артикул не найден. Проверьте и введите снова.", reply_markup=create_cancel_markup())
        return
    op = get_op(chat_id)
    op['products'] = found
    if missing:
        bot.send_message(chat_id, "Не найдены: " + ", ".join(h(a) for a in missing[:30]) + ("…" if len(missing) > 30 else ""),
                         parse_mode='HTML')
    _ask_copies(chat_id)


def on_store(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    store_id = store_id_by_name(text)
    if not store_id and text != ALL_STORES:
        bot.send_message(chat_id, "Выберите магазин кнопкой.", reply_markup=create_choice_markup(store_names() + [ALL_STORES], 2))
        return
    get_op(chat_id)['filter'] = {'store': store_id}
    _ask_copies(chat_id)


def _ask_copies(chat_id):
    user_state[chat_id] = COPIES
    bot.send_message(chat_id, "Сколько этикеток печатать?", reply_markup=create_choice_markup([ONE, BY_STOCK], 1))


def on_copies(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    if text not in (ONE, BY_STOCK):
        bot.send_message(chat_id, "Выберите вариант кнопкой.", reply_markup=create_choice_markup([ONE, BY_STOCK], 1))
        return
    op = get_op(chat_id)
    products = op.get('products')
    if products is None:
        f = op.get('filter', {})
        products = get_products_for_labels(store=f.get('store'), arrived_from=f.get('arrived_from'), only_in_stock=True)
    end_op(chat_id)
    if products is None:
        stock_menu(chat_id, "❌ Ошибка базы данных.")
        return
    if not products:
        stock_menu(chat_id, "Подходящих товаров в наличии нет.")
        return
    try:
        pdf, count = generate_labels_pdf(products, copies="stock" if text == BY_STOCK else "one")
    except Exception:
        logger.exception("Chat %s: ошибка генерации ценников", chat_id)
        stock_menu(chat_id, "❌ Не удалось сформировать ценники.")
        return
    bot.send_document(chat_id, types.InputFile(io.BytesIO(pdf), file_name=f"labels_{now():%Y%m%d_%H%M}.pdf"),
                      caption=f"Ценников: {count}. Печать: масштаб 100 %, размер бумаги {config.LABEL_WIDTH_MM:g}×{config.LABEL_HEIGHT_MM:g} мм.")
    stock_menu(chat_id, "Выберите следующее действие:")


def register_labels_handlers():
    register_simple_cancel("LABELS_", "Печать ценников", menu=stock_menu)
    bot.register_message_handler(start, func=is_button("Ценники", *STOCK_ROLES))
    bot.register_message_handler(on_mode, func=in_state(MODE))
    bot.register_message_handler(on_articles, func=in_state(ARTICLES))
    bot.register_message_handler(on_store, func=in_state(STORE))
    bot.register_message_handler(on_copies, func=in_state(COPIES))
