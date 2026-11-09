"""Продажа чеком: артикул → сумма → (ещё товар…) → оплата → подтверждение."""
import logging

from buttons import (create_cancel_markup, create_confirmation_markup, _kb, PAYMENT_TYPES, BTN_CANCEL, BTN_CONFIRM)
from database import record_receipt, MAX_RECEIPT_ITEMS
from handlers.common import (CASH_ROLES, in_state, is_button, user_ids, cash_menu, register_simple_cancel,
                             product_line, ask_money, read_money, resolve_for_sale)
from utils import mark_codes
from state import bot, user_state, get_op, start_op, end_op, cancel_current_operation
from utils.common import money, h

logger = logging.getLogger(__name__)

ARTICLE, PRICE, PAYMENT, CONFIRM = "SALES_ARTICLE", "SALES_PRICE", "SALES_PAYMENT", "SALES_CONFIRM"
BTN_MORE = "➕ Ещё товар"
BTN_DROP_LAST = "↩️ Убрать последний"


def _cart(chat_id):
    return get_op(chat_id).setdefault('items', [])


def _cart_text(items):
    lines = [f"{i}. {product_line(it['product'])}" + (f" · код {h(mark_codes.short(it['code']))}" if it.get('code') else "")
             + f" — <b>{money(it['price'])}</b>" for i, it in enumerate(items, 1)]
    total = sum(it['price'] for it in items)
    return "\n".join(lines) + f"\n\nИтого: <b>{money(total)}</b>" + (f" ({len(items)} поз.)" if len(items) > 1 else "")


def _payment_markup(items):
    rows = [PAYMENT_TYPES, [BTN_MORE]]
    if len(items) > 1:
        rows[1].append(BTN_DROP_LAST)
    return _kb(*rows, [BTN_CANCEL])


def _ask_payment(chat_id):
    items = _cart(chat_id)
    user_state[chat_id] = PAYMENT
    bot.send_message(chat_id, f"<b>Чек</b>\n{_cart_text(items)}\n\nВыберите способ оплаты или добавьте ещё товар:",
                     parse_mode='HTML', reply_markup=_payment_markup(items))


def start(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, ARTICLE)
    bot.send_message(chat_id, "Продажа. Введите артикул товара:", reply_markup=create_cancel_markup())


def on_article(message):
    chat_id = message.chat.id
    product, code = resolve_for_sale(chat_id, message.text)
    if not product:
        return
    if code and any(it.get('code') and mark_codes.key(it['code']) == mark_codes.key(code) for it in _cart(chat_id)):
        bot.send_message(chat_id, "Этот код уже в чеке. Отсканируйте другой.", reply_markup=create_cancel_markup())
        return
    in_cart = sum(1 for it in _cart(chat_id) if it['product']['id'] == product['id'])
    left = (product.get('quantity') or 0) - in_cart
    if left <= 0:
        why = "нет в наличии" if not in_cart else f"больше нет: все {in_cart} шт. уже в чеке"
        bot.send_message(chat_id, f"Товара {product_line(product)} {why}. Введите другой артикул.",
                         parse_mode='HTML', reply_markup=create_cancel_markup())
        return
    op = get_op(chat_id)
    op['product'] = product
    op['code'] = code
    user_state[chat_id] = PRICE
    ask_money(chat_id,
              f"{product_line(product)}\nМагазин: {h(product.get('Store'))}\n"
              f"Цена: <b>{money(product['price'])}</b> (минимум {money(product['min_price'])})\n"
              f"Остаток: {left} шт.\n\nВведите сумму продажи или нажмите кнопку с ценой:",
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
    _cart(chat_id).append({'product': op.pop('product'), 'price': price, 'code': op.pop('code', None)})
    _ask_payment(chat_id)


def on_payment(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    items = _cart(chat_id)
    if text == BTN_MORE:
        if len(items) >= MAX_RECEIPT_ITEMS:
            bot.send_message(chat_id, f"В одном чеке — не больше {MAX_RECEIPT_ITEMS} позиций.",
                             reply_markup=_payment_markup(items))
            return
        user_state[chat_id] = ARTICLE
        bot.send_message(chat_id, f"В чеке {len(items)} поз. Введите артикул следующего товара:",
                         reply_markup=create_cancel_markup())
        return
    if text == BTN_DROP_LAST and len(items) > 1:
        items.pop()
        _ask_payment(chat_id)
        return
    if text not in PAYMENT_TYPES:
        bot.send_message(chat_id, "Выберите способ оплаты кнопкой.", reply_markup=_payment_markup(items))
        return
    op = get_op(chat_id)
    op['payment'] = text
    user_state[chat_id] = CONFIRM
    bot.send_message(chat_id, f"<b>Подтвердите продажу</b>\n{_cart_text(items)}\nОплата: {h(text)}",
                     parse_mode='HTML', reply_markup=create_confirmation_markup())


def on_confirm(message):
    chat_id = message.chat.id
    if message.text != BTN_CONFIRM:
        bot.send_message(chat_id, "Нажмите «Подтвердить» или «Отмена».", reply_markup=create_confirmation_markup())
        return
    op = get_op(chat_id)
    items = _cart(chat_id)
    user_id, _ = user_ids(chat_id)
    ok, result = record_receipt([(it['product']['id'], it['price'], it.get('code')) for it in items], user_id, op['payment'])
    end_op(chat_id)
    if ok:
        logger.info("Chat %s: чек #%s, %s поз. на %s", chat_id, result['receipt_id'], len(items), result['total'])
        what = f"{len(items)} поз., " if len(items) > 1 else ""
        cash_menu(chat_id, f"✅ Продажа оформлена, чек №{result['receipt_id']}: {what}{money(result['total'])}, {op['payment']}.")
        from handlers.customer_handler import seller_qr_markup
        bot.send_message(chat_id, "Электронный чек — в Telegram клиента:", reply_markup=seller_qr_markup(result['receipt_id']))
    else:
        cash_menu(chat_id, f"❌ Продажа не оформлена: {result}")


def register_sales_handlers():
    register_simple_cancel("SALES_", "Продажа")
    bot.register_message_handler(start, func=is_button("Продажа", *CASH_ROLES))
    bot.register_message_handler(on_article, func=in_state(ARTICLE))
    bot.register_message_handler(on_price, func=in_state(PRICE))
    bot.register_message_handler(on_payment, func=in_state(PAYMENT))
    bot.register_message_handler(on_confirm, func=in_state(CONFIRM))
