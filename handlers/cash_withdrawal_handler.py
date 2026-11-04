"""Инкассация: сумма → комментарий → подтверждение."""
import logging

from buttons import create_cancel_markup, create_confirmation_markup, create_skip_cancel_markup, BTN_CONFIRM
from database import create_cash_withdrawal_record
from handlers.common import CASH_ROLES, in_state, is_button, user_ids, cash_menu, register_simple_cancel, read_money, SKIP
from state import bot, user_state, get_op, start_op, end_op, cancel_current_operation
from utils.common import money, h

logger = logging.getLogger(__name__)

AMOUNT, COMMENT, CONFIRM = "WITHDRAWAL_AMOUNT", "WITHDRAWAL_COMMENT", "WITHDRAWAL_CONFIRM"


def start(message):
    chat_id = message.chat.id
    cancel_current_operation(chat_id, notify=False)
    start_op(chat_id, AMOUNT)
    bot.send_message(chat_id, "Инкассация. Введите сумму наличных, изымаемых из кассы:", reply_markup=create_cancel_markup())


def on_amount(message):
    chat_id = message.chat.id
    amount = read_money(message)
    if amount is None:
        bot.send_message(chat_id, "Введите положительную сумму числом.", reply_markup=create_cancel_markup())
        return
    get_op(chat_id)['amount'] = amount
    user_state[chat_id] = COMMENT
    bot.send_message(chat_id, "Комментарий (например, «в банк», «на расходы»):", reply_markup=create_skip_cancel_markup(SKIP))


def on_comment(message):
    chat_id, text = message.chat.id, (message.text or '').strip()
    op = get_op(chat_id)
    op['comment'] = None if text in (SKIP, '-') else text[:500]
    user_state[chat_id] = CONFIRM
    bot.send_message(chat_id,
                     f"<b>Подтвердите инкассацию</b>\nСумма: <b>{money(op['amount'])}</b>\nКомментарий: {h(op['comment'] or '—')}",
                     parse_mode='HTML', reply_markup=create_confirmation_markup())


def on_confirm(message):
    chat_id = message.chat.id
    if message.text != BTN_CONFIRM:
        bot.send_message(chat_id, "Нажмите «Подтвердить» или «Отмена».", reply_markup=create_confirmation_markup())
        return
    op = get_op(chat_id)
    user_id, store_id = user_ids(chat_id)
    ok, result = create_cash_withdrawal_record(op['amount'], op['comment'], user_id, store_id)
    end_op(chat_id)
    if ok:
        logger.info("Chat %s: инкассация #%s на %s", chat_id, result, op['amount'])
        cash_menu(chat_id, f"✅ Инкассация №{result} на {money(op['amount'])} зарегистрирована.")
    else:
        cash_menu(chat_id, f"❌ Инкассация не зарегистрирована: {result}")


def register_withdrawal_handlers():
    register_simple_cancel("WITHDRAWAL_", "Инкассация")
    bot.register_message_handler(start, func=is_button("Инкассация", *CASH_ROLES))
    bot.register_message_handler(on_amount, func=in_state(AMOUNT))
    bot.register_message_handler(on_comment, func=in_state(COMMENT))
    bot.register_message_handler(on_confirm, func=in_state(CONFIRM))
