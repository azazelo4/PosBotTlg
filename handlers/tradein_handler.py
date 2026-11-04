"""Trade-In: сдаваемое изделие и его оценка → новый товар и его цена → доплата → подтверждение.

Доплата считается автоматически: цена нового − оценка старого.
Положительная — платит клиент, отрицательная — выплачиваем клиенту.
"""
import logging

from buttons import create_cancel_markup, create_confirmation_markup, create_payment_type_markup, PAYMENT_TYPES, BTN_CONFIRM
from database import get_product_by_article, create_trade_in_record
from handlers.common import (CASH_ROLES, CustomerItemIntake, in_state, is_button, user_ids, cash_menu,
                             register_simple_cancel, product_line, ask_money, read_money)
from state import bot, user_state, get_op, start_op, end_op, cancel_current_operation
from utils.common import money, h

logger = logging.getLogger(__name__)

OLD_VALUE, NEW_ARTICLE, NEW_PRICE, PAYMENT, CONFIRM = (
    "TRADEIN_OLD_VALUE", "TRADEIN_NEW_ARTICLE", "TRADEIN_NEW_PRICE", "TRADEIN_PAYMENT", "TRADEIN_CONFIRM")


def _after_item(chat_id):
    item = get_op(chat_id)['item']
    user_state[chat_id] = OLD_VALUE
    ask_money(chat_id, f"Сдаваемое изделие: {item['title']}\n\nВо сколько оцениваем его (зачёт в счёт нового)?")


intake = CustomerItemIntake("TRADEIN", _after_item)


def start(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, intake.ARTICLE)
    intake.start(chat_id, "Trade-In. Шаг 1: изделие, которое сдаёт клиент.")


def on_old_value(message):
    chat_id = message.chat.id
    value = read_money(message)
    if value is None:
        bot.send_message(chat_id, "Введите оценку числом, например 60000.", reply_markup=create_cancel_markup())
        return
    get_op(chat_id)['old_value'] = value
    user_state[chat_id] = NEW_ARTICLE
    bot.send_message(chat_id, "Шаг 2: введите артикул нового товара, который получает клиент:", reply_markup=create_cancel_markup())


def on_new_article(message):
    chat_id, article = message.chat.id, (message.text or '').strip()
    op = get_op(chat_id)
    product = get_product_by_article(article)
    if not product:
        bot.send_message(chat_id, f"Артикул <code>{h(article)}</code> не найден. Введите другой.",
                         parse_mode='HTML', reply_markup=create_cancel_markup())
        return
    if product['article_number'] == op['item'].get('article'):
        bot.send_message(chat_id, "Новый товар не может совпадать со сдаваемым. Введите другой артикул.", reply_markup=create_cancel_markup())
        return
    if (product.get('quantity') or 0) <= 0:
        bot.send_message(chat_id, f"Товара {product_line(product)} нет в наличии. Введите другой артикул.",
                         parse_mode='HTML', reply_markup=create_cancel_markup())
        return
    op['new_product'] = product
    user_state[chat_id] = NEW_PRICE
    ask_money(chat_id, f"Новый товар: {product_line(product)}\n"
                       f"Цена: <b>{money(product['price'])}</b> (минимум {money(product['min_price'])})\n\n"
                       "Введите цену нового товара или нажмите кнопку с ценой:",
              suggestion=product['price'])


def on_new_price(message):
    chat_id = message.chat.id
    op = get_op(chat_id)
    price = read_money(message)
    if price is None:
        bot.send_message(chat_id, "Введите цену числом.", reply_markup=create_cancel_markup())
        return
    min_price = op['new_product']['min_price'] or 0
    if price < min_price:
        bot.send_message(chat_id, f"Цена {money(price)} ниже минимальной {money(min_price)}. Введите другую.", reply_markup=create_cancel_markup())
        return
    op['new_price'] = price
    op['difference'] = price - op['old_value']
    if op['difference'] == 0:
        op['payment'] = None
        _ask_confirm(chat_id)
        return
    user_state[chat_id] = PAYMENT
    if op['difference'] > 0:
        text = f"Клиент доплачивает <b>{money(op['difference'])}</b>. Способ оплаты:"
    else:
        text = f"Клиенту выплачиваем <b>{money(-op['difference'])}</b>. Способ выплаты:"
    bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=create_payment_type_markup())


def on_payment(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    if text not in PAYMENT_TYPES:
        bot.send_message(chat_id, "Выберите способ оплаты кнопкой.", reply_markup=create_payment_type_markup())
        return
    get_op(chat_id)['payment'] = text
    _ask_confirm(chat_id)


def _ask_confirm(chat_id):
    op = get_op(chat_id)
    user_state[chat_id] = CONFIRM
    diff = op['difference']
    if diff > 0:
        diff_text = f"Доплата клиента: <b>{money(diff)}</b>, {h(op['payment'])}"
    elif diff < 0:
        diff_text = f"Выплата клиенту: <b>{money(-diff)}</b>, {h(op['payment'])}"
    else:
        diff_text = "Без доплаты"
    bot.send_message(chat_id,
                     f"<b>Подтвердите Trade-In</b>\n"
                     f"Сдаёт: {op['item']['title']} — оценка {money(op['old_value'])}\n"
                     f"Получает: {product_line(op['new_product'])} — {money(op['new_price'])}\n{diff_text}",
                     parse_mode='HTML', reply_markup=create_confirmation_markup())


def on_confirm(message):
    chat_id = message.chat.id
    if message.text != BTN_CONFIRM:
        bot.send_message(chat_id, "Нажмите «Подтвердить» или «Отмена».", reply_markup=create_confirmation_markup())
        return
    op = get_op(chat_id)
    item = op['item']
    user_id, store_id = user_ids(chat_id)
    ok, result = create_trade_in_record(
        new_product_article=op['new_product']['article_number'], new_sale_price=op['new_price'],
        old_item_value=op['old_value'], price_difference=op['difference'], payment_type_difference=op['payment'],
        user_id=user_id, store_id=store_id, old_product_article=item.get('article'),
        old_item_description=item.get('description'), old_metal_type=item.get('metal_type'), old_weight=item.get('weight'))
    end_op(chat_id)
    if ok:
        logger.info("Chat %s: trade-in #%s, доплата %s", chat_id, result, op['difference'])
        cash_menu(chat_id, f"✅ Trade-In №{result} оформлен.")
    else:
        cash_menu(chat_id, f"❌ Trade-In не оформлен: {result}")


def register_tradein_handlers():
    register_simple_cancel("TRADEIN_", "Операция Trade-In")
    bot.register_message_handler(start, func=is_button("Trade-In", *CASH_ROLES))
    intake.register()
    bot.register_message_handler(on_old_value, func=in_state(OLD_VALUE))
    bot.register_message_handler(on_new_article, func=in_state(NEW_ARTICLE))
    bot.register_message_handler(on_new_price, func=in_state(NEW_PRICE))
    bot.register_message_handler(on_payment, func=in_state(PAYMENT))
    bot.register_message_handler(on_confirm, func=in_state(CONFIRM))
