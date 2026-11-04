"""Скупка изделия у клиента: изделие → цена → способ выплаты → подтверждение."""
import logging

from buttons import create_cancel_markup, create_confirmation_markup, create_payment_type_markup, PAYMENT_TYPES, BTN_CONFIRM
from database import create_buyback_record
from handlers.common import (CASH_ROLES, CustomerItemIntake, in_state, is_button, user_ids, cash_menu,
                             register_simple_cancel, ask_money, read_money)
from state import bot, user_state, get_op, start_op, end_op, cancel_current_operation
from utils.common import money, h

logger = logging.getLogger(__name__)

PRICE, PAYMENT, CONFIRM = "BUYBACK_PRICE", "BUYBACK_PAYMENT", "BUYBACK_CONFIRM"


def _after_item(chat_id):
    item = get_op(chat_id)['item']
    user_state[chat_id] = PRICE
    note = "\nИзделие вернётся на склад." if item.get('article') else ""
    ask_money(chat_id, f"Изделие: {item['title']}{note}\n\nВведите сумму скупки:")


intake = CustomerItemIntake("BUYBACK", _after_item)


def start(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, intake.ARTICLE)
    intake.start(chat_id, "Скупка изделия у клиента.")


def on_price(message):
    chat_id = message.chat.id
    price = read_money(message)
    if price is None:
        bot.send_message(chat_id, "Введите сумму числом, например 85000.", reply_markup=create_cancel_markup())
        return
    get_op(chat_id)['price'] = price
    user_state[chat_id] = PAYMENT
    bot.send_message(chat_id, "Способ выплаты клиенту:", reply_markup=create_payment_type_markup())


def on_payment(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    if text not in PAYMENT_TYPES:
        bot.send_message(chat_id, "Выберите способ выплаты кнопкой.", reply_markup=create_payment_type_markup())
        return
    op = get_op(chat_id)
    op['payment'] = text
    user_state[chat_id] = CONFIRM
    bot.send_message(chat_id,
                     f"<b>Подтвердите скупку</b>\nИзделие: {op['item']['title']}\n"
                     f"Выплата клиенту: <b>{money(op['price'])}</b>, {h(text)}",
                     parse_mode='HTML', reply_markup=create_confirmation_markup())


def on_confirm(message):
    chat_id = message.chat.id
    if message.text != BTN_CONFIRM:
        bot.send_message(chat_id, "Нажмите «Подтвердить» или «Отмена».", reply_markup=create_confirmation_markup())
        return
    op = get_op(chat_id)
    item = op['item']
    user_id, store_id = user_ids(chat_id)
    ok, result = create_buyback_record(item.get('article'), op['price'], op['payment'], user_id, store_id,
                                       description=item.get('description'), metal_type=item.get('metal_type'),
                                       weight=item.get('weight'))
    end_op(chat_id)
    if ok:
        logger.info("Chat %s: скупка #%s на %s", chat_id, result, op['price'])
        cash_menu(chat_id, f"✅ Скупка №{result} оформлена: {money(op['price'])}, {op['payment']}.")
    else:
        cash_menu(chat_id, f"❌ Скупка не оформлена: {result}")


def register_buyback_handlers():
    register_simple_cancel("BUYBACK_", "Скупка")
    bot.register_message_handler(start, func=is_button("Скупка", *CASH_ROLES))
    intake.register()
    bot.register_message_handler(on_price, func=in_state(PRICE))
    bot.register_message_handler(on_payment, func=in_state(PAYMENT))
    bot.register_message_handler(on_confirm, func=in_state(CONFIRM))
