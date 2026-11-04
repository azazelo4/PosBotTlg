"""Продажа: артикул → сумма → оплата → подтверждение."""
import logging

from buttons import create_cancel_markup, create_confirmation_markup, create_payment_type_markup, PAYMENT_TYPES, BTN_CONFIRM
from database import get_product_by_article, record_sale
from handlers.common import (CASH_ROLES, in_state, is_button, user_ids, cash_menu, register_simple_cancel,
                             product_line, ask_money, read_money)
from state import bot, user_state, get_op, start_op, end_op, cancel_current_operation
from utils.common import money, h

logger = logging.getLogger(__name__)

ARTICLE, PRICE, PAYMENT, CONFIRM = "SALES_ARTICLE", "SALES_PRICE", "SALES_PAYMENT", "SALES_CONFIRM"


def start(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, ARTICLE)
    bot.send_message(chat_id, "Продажа. Введите артикул товара:", reply_markup=create_cancel_markup())


def on_article(message):
    chat_id, article = message.chat.id, (message.text or '').strip()
    product = get_product_by_article(article)
    if not product:
        bot.send_message(chat_id, f"Артикул <code>{h(article)}</code> не найден. Введите другой или нажмите «Отмена».",
                         parse_mode='HTML', reply_markup=create_cancel_markup())
        return
    if (product.get('quantity') or 0) <= 0:
        bot.send_message(chat_id, f"Товара {product_line(product)} нет в наличии. Введите другой артикул.",
                         parse_mode='HTML', reply_markup=create_cancel_markup())
        return
    op = get_op(chat_id)
    op['product'] = product
    user_state[chat_id] = PRICE
    ask_money(chat_id,
              f"{product_line(product)}\nМагазин: {h(product.get('Store'))}\n"
              f"Цена: <b>{money(product['price'])}</b> (минимум {money(product['min_price'])})\n"
              f"Остаток: {product['quantity']} шт.\n\nВведите сумму продажи или нажмите кнопку с ценой:",
              suggestion=product['price'])


def on_price(message):
    chat_id = message.chat.id
    op = get_op(chat_id)
    price = read_money(message)
    if price is None:
        bot.send_message(chat_id, "Введите сумму числом, например 150000.", reply_markup=create_cancel_markup())
        return
    min_price = op['product']['min_price'] or 0
    if price < min_price:
        bot.send_message(chat_id, f"Сумма {money(price)} ниже минимальной цены {money(min_price)}. Введите другую сумму.",
                         reply_markup=create_cancel_markup())
        return
    op['price'] = price
    user_state[chat_id] = PAYMENT
    bot.send_message(chat_id, "Способ оплаты:", reply_markup=create_payment_type_markup())


def on_payment(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    if text not in PAYMENT_TYPES:
        bot.send_message(chat_id, "Выберите способ оплаты кнопкой.", reply_markup=create_payment_type_markup())
        return
    op = get_op(chat_id)
    op['payment'] = text
    user_state[chat_id] = CONFIRM
    bot.send_message(chat_id,
                     f"<b>Подтвердите продажу</b>\n{product_line(op['product'])}\n"
                     f"Сумма: <b>{money(op['price'])}</b>\nОплата: {h(text)}",
                     parse_mode='HTML', reply_markup=create_confirmation_markup())


def on_confirm(message):
    chat_id = message.chat.id
    if message.text != BTN_CONFIRM:
        bot.send_message(chat_id, "Нажмите «Подтвердить» или «Отмена».", reply_markup=create_confirmation_markup())
        return
    op = get_op(chat_id)
    user_id, _ = user_ids(chat_id)
    product = op['product']
    ok, result = record_sale(product['id'], product['article_number'], user_id, op['price'], op['payment'])
    end_op(chat_id)
    if ok:
        logger.info("Chat %s: продажа #%s, %s за %s", chat_id, result, product['article_number'], op['price'])
        cash_menu(chat_id, f"✅ Продажа №{result} оформлена: {money(op['price'])}, {op['payment']}.")
    else:
        cash_menu(chat_id, f"❌ Продажа не оформлена: {result}")


def register_sales_handlers():
    register_simple_cancel("SALES_", "Продажа")
    bot.register_message_handler(start, func=is_button("Продажа", *CASH_ROLES))
    bot.register_message_handler(on_article, func=in_state(ARTICLE))
    bot.register_message_handler(on_price, func=in_state(PRICE))
    bot.register_message_handler(on_payment, func=in_state(PAYMENT))
    bot.register_message_handler(on_confirm, func=in_state(CONFIRM))
