"""Возврат: артикул → выбор продажи → сумма → способ выплаты → подтверждение."""
import logging

from telebot import types

from buttons import create_cancel_markup, create_confirmation_markup, create_payment_type_markup, PAYMENT_TYPES, BTN_CONFIRM
from database import get_sales_for_return, get_sale_details_by_id, create_return_record
from handlers.common import CASH_ROLES, in_state, is_button, user_ids, cash_menu, register_simple_cancel, ask_money, read_money
from state import bot, user_state, get_op, start_op, end_op, cancel_current_operation
from utils.common import money, h

logger = logging.getLogger(__name__)

ARTICLE, PICK, PRICE, PAYMENT, CONFIRM = "RETURN_ARTICLE", "RETURN_PICK", "RETURN_PRICE", "RETURN_PAYMENT", "RETURN_CONFIRM"
CB_PICK = "ret_pick_"


def start(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, ARTICLE)
    bot.send_message(chat_id, "Возврат. Введите артикул возвращаемого товара:", reply_markup=create_cancel_markup())


def on_article(message):
    chat_id, article = message.chat.id, (message.text or '').strip()
    sales = get_sales_for_return(article)
    if sales is None:
        bot.send_message(chat_id, "Ошибка базы данных. Попробуйте ещё раз.", reply_markup=create_cancel_markup())
        return
    available = [s for s in sales if not s['return_id']]
    if not available:
        note = " Все продажи этого артикула за 60 дней уже возвращены." if sales else ""
        bot.send_message(chat_id, f"Продаж артикула <code>{h(article)}</code> для возврата не найдено.{note}\n"
                                  "Введите другой артикул или нажмите «Отмена».",
                         parse_mode='HTML', reply_markup=create_cancel_markup())
        return
    if len(available) == 1:
        _select_sale(chat_id, available[0])
        return
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for s in available:
        label = f"№{s['sale_id']} · {s['sale_date']:%d.%m %H:%M} · {money(s['sold_price'])} · {s['sale_payment_type']}"
        keyboard.add(types.InlineKeyboardButton(label, callback_data=f"{CB_PICK}{s['sale_id']}"))
    user_state[chat_id] = PICK
    bot.send_message(chat_id, f"Найдено продаж: {len(available)}. Выберите, какую возвращаем:", reply_markup=keyboard)


def on_pick(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)
    if user_state.get(chat_id) != PICK:
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
        return
    sale = get_sale_details_by_id(int(call.data[len(CB_PICK):]))
    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    if not sale or sale['return_id']:
        bot.send_message(chat_id, "Эта продажа недоступна для возврата. Введите артикул заново.", reply_markup=create_cancel_markup())
        user_state[chat_id] = ARTICLE
        return
    _select_sale(chat_id, sale)


def on_pick_text(message):
    bot.send_message(message.chat.id, "Выберите продажу кнопкой в сообщении выше или нажмите «Отмена».", reply_markup=create_cancel_markup())


def _select_sale(chat_id, sale):
    get_op(chat_id)['sale'] = sale
    user_state[chat_id] = PRICE
    ask_money(chat_id,
              f"Продажа №{sale['sale_id']} от {sale['sale_date']:%d.%m.%Y %H:%M}\n"
              f"{h(sale['product_model'])}, арт. <code>{h(sale['article_number'])}</code>\n"
              f"Сумма продажи: <b>{money(sale['sold_price'])}</b>, оплата: {h(sale['sale_payment_type'])}\n\n"
              "Введите сумму возврата или нажмите кнопку с полной суммой:",
              suggestion=sale['sold_price'])


def on_price(message):
    chat_id = message.chat.id
    op = get_op(chat_id)
    price = read_money(message)
    sold = op['sale']['sold_price']
    if price is None or price > sold:
        bot.send_message(chat_id, f"Сумма возврата должна быть больше нуля и не больше {money(sold)}.", reply_markup=create_cancel_markup())
        return
    op['price'] = price
    user_state[chat_id] = PAYMENT
    bot.send_message(chat_id, f"Способ выплаты клиенту (при продаже: {op['sale']['sale_payment_type']}):",
                     reply_markup=create_payment_type_markup())


def on_payment(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    if text not in PAYMENT_TYPES:
        bot.send_message(chat_id, "Выберите способ выплаты кнопкой.", reply_markup=create_payment_type_markup())
        return
    op = get_op(chat_id)
    op['payment'] = text
    user_state[chat_id] = CONFIRM
    sale = op['sale']
    bot.send_message(chat_id,
                     f"<b>Подтвердите возврат</b>\nПродажа №{sale['sale_id']}: {h(sale['product_model'])}, "
                     f"арт. <code>{h(sale['article_number'])}</code>\n"
                     f"К выплате: <b>{money(op['price'])}</b>, {h(text)}\nТовар вернётся на склад.",
                     parse_mode='HTML', reply_markup=create_confirmation_markup())


def on_confirm(message):
    chat_id = message.chat.id
    if message.text != BTN_CONFIRM:
        bot.send_message(chat_id, "Нажмите «Подтвердить» или «Отмена».", reply_markup=create_confirmation_markup())
        return
    op = get_op(chat_id)
    user_id, store_id = user_ids(chat_id)
    sale = op['sale']
    ok, result = create_return_record(sale['sale_id'], sale['article_number'], op['price'], op['payment'], user_id, store_id)
    end_op(chat_id)
    if ok:
        logger.info("Chat %s: возврат #%s по продаже #%s", chat_id, result, sale['sale_id'])
        cash_menu(chat_id, f"✅ Возврат №{result} оформлен: {money(op['price'])}, {op['payment']}.")
    else:
        cash_menu(chat_id, f"❌ Возврат не оформлен: {result}")


def register_return_handlers():
    register_simple_cancel("RETURN_", "Операция возврата")
    bot.register_message_handler(start, func=is_button("Возврат", *CASH_ROLES))
    bot.register_message_handler(on_article, func=in_state(ARTICLE))
    bot.register_message_handler(on_pick_text, func=in_state(PICK))
    bot.register_message_handler(on_price, func=in_state(PRICE))
    bot.register_message_handler(on_payment, func=in_state(PAYMENT))
    bot.register_message_handler(on_confirm, func=in_state(CONFIRM))
    bot.register_callback_query_handler(on_pick, func=lambda c: c.data.startswith(CB_PICK))
