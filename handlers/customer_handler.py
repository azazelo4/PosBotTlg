"""Режим «клиент»: электронный чек в Telegram.

Касса показывает QR со ссылкой t.me/<бот>?start=r_<токен чека>. Клиент сканирует его телефоном и жмёт Start —
бот присылает чек и предлагает присылать следующие чеки сюда. Для этого клиент делится номером: это и есть
согласие на хранение номера и истории покупок. Дальше чеки по этому номеру приходят автоматически
(задача send_customer_receipts). Отписаться — /stop.

Сотрудник тоже может быть клиентом: его меню и вход в бота это не затрагивает.
"""
import logging

from telebot import types

from services import customers
from core.auth import has_role
from handlers.buttons import get_keyboard_by_role
from core.database import get_receipt
from core.state import bot, user_state, get_op, start_op, end_op
from utils import receipt_text
from utils.qr import qr_png

logger = logging.getLogger(__name__)

START_PREFIX = "r_"
CB_SUBSCRIBE, CB_NO = "cust_sub:", "cust_no:"
CB_SELLER_QR = "rcpt_qr:"
CONTACT = "CUSTOMER_CONTACT"
BTN_SHARE = "📲 Поделиться номером"
CONSENT = ("Нажимая «Поделиться номером», вы соглашаетесь, что магазин сохранит ваш номер телефона и историю "
           "покупок, чтобы присылать чеки. Отказаться можно в любой момент командой /stop.")


def is_receipt_start(message):
    parts = (message.text or "").split(maxsplit=1)
    return len(parts) == 2 and parts[0].split("@")[0] == "/start" and parts[1].startswith(START_PREFIX)


def _menu(chat_id):
    """Сотруднику — его меню, клиенту — убрать клавиатуру."""
    return get_keyboard_by_role(chat_id) if has_role(chat_id) else types.ReplyKeyboardRemove()


def send_receipt(chat_id, receipt):
    bot.send_message(chat_id, receipt_text.telegram_text(receipt), parse_mode='HTML', disable_web_page_preview=True)


def on_receipt_start(message):
    chat_id = message.chat.id
    token = message.text.split(maxsplit=1)[1][len(START_PREFIX):].strip()
    receipt = get_receipt(token=token) if len(token) == 32 and token.isalnum() else None
    if not receipt:
        bot.send_message(chat_id, "Чек не найден. Попросите продавца показать QR-код ещё раз.", reply_markup=_menu(chat_id))
        return
    send_receipt(chat_id, receipt)
    customers.mark_sent(receipt['id'])
    customer = customers.by_chat(chat_id)
    if customer:
        customers.attach_receipt(receipt['id'], customer['id'])
        bot.send_message(chat_id, "Чек сохранён в вашей истории покупок. Следующие чеки придут сюда сами.",
                         reply_markup=_menu(chat_id))
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📲 Присылать мои чеки сюда", callback_data=CB_SUBSCRIBE + str(receipt['id'])),
           types.InlineKeyboardButton("Нет, спасибо", callback_data=CB_NO + str(receipt['id'])))
    bot.send_message(chat_id, "Присылать сюда чеки следующих покупок в этом магазине? Для этого понадобится ваш номер "
                              "телефона — продавец назовёт его на кассе, и чек придёт сам.", reply_markup=kb)


def on_choice(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)
    receipt_id = int(call.data.split(":", 1)[1])
    if call.data.startswith(CB_NO):
        bot.edit_message_text("Хорошо. Чек выше остаётся у вас в чате.", chat_id, call.message.message_id)
        return
    start_op(chat_id, CONTACT)
    get_op(chat_id)['receipt_id'] = receipt_id
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton(BTN_SHARE, request_contact=True))
    kb.add(types.KeyboardButton("Отмена"))
    bot.send_message(chat_id, CONSENT, reply_markup=kb)


def on_contact(message):
    chat_id = message.chat.id
    contact = message.contact
    if not contact or not message.from_user or contact.user_id != message.from_user.id:
        bot.send_message(chat_id, "Отправьте свой номер кнопкой «Поделиться номером» ниже.")
        return
    receipt_id = get_op(chat_id).get('receipt_id')
    end_op(chat_id)
    ok, result = customers.subscribe(contact.phone_number, chat_id, contact.first_name, receipt_id)
    if not ok:
        bot.send_message(chat_id, f"Не получилось: {result}", reply_markup=_menu(chat_id))
        return
    logger.info("Chat %s: клиент #%s подписался на чеки", chat_id, result)
    bot.send_message(chat_id, "Готово! Чеки следующих покупок придут сюда — достаточно назвать продавцу этот номер. "
                              "Отказаться — /stop.", reply_markup=_menu(chat_id))


def on_cancel_contact(message):
    end_op(message.chat.id)
    bot.send_message(message.chat.id, "Хорошо, номер не сохранён.", reply_markup=_menu(message.chat.id))


def on_stop(message):
    chat_id = message.chat.id
    ok, count = customers.unsubscribe(chat_id)
    text = ("Больше не присылаем сюда чеки. Номер и история покупок остаются у магазина — удалить их можно по просьбе "
            "к магазину.") if ok and count else "Вы не подписаны на чеки."
    bot.send_message(chat_id, text, reply_markup=_menu(chat_id))


def is_customer(message):
    """Сообщение от подписанного клиента, который не сотрудник."""
    return not has_role(message.chat.id) and customers.by_chat(message.chat.id) is not None


def on_customer_message(message):
    bot.send_message(message.chat.id, "Здесь приходят ваши чеки из магазина. Отказаться от них — /stop.",
                     reply_markup=types.ReplyKeyboardRemove())


# ------------------------------------------------------------------ продавцу: QR для клиента

def seller_qr_markup(receipt_id):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🧾 Чек клиенту (QR)", callback_data=CB_SELLER_QR + str(receipt_id)))
    return kb


def on_seller_qr(call):
    """Продавец показывает клиенту QR со своего телефона — клиент сканирует и получает чек в Telegram."""
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)
    if not has_role(chat_id):
        return
    receipt = get_receipt(int(call.data.split(":", 1)[1]))
    from web.kassa import bot_username
    link = receipt_text.bot_link(bot_username(), receipt) if receipt else None
    if not link:
        bot.send_message(chat_id, "Не удалось сделать QR-код. Попробуйте ещё раз.")
        return
    bot.send_photo(chat_id, qr_png(link), caption=f"Чек №{receipt['id']}: покажите QR клиенту — он отсканирует его "
                                                   f"камерой телефона и получит чек в Telegram.\n{link}")


def register_customer_handlers():
    """Регистрировать до обычного /start и входа по номеру."""
    bot.register_message_handler(on_receipt_start, commands=['start'], func=is_receipt_start)
    bot.register_message_handler(on_stop, commands=['stop'])
    bot.register_callback_query_handler(on_choice, func=lambda c: c.data.startswith((CB_SUBSCRIBE, CB_NO)))
    bot.register_callback_query_handler(on_seller_qr, func=lambda c: c.data.startswith(CB_SELLER_QR))
    bot.register_message_handler(on_contact, content_types=['contact'], func=lambda m: user_state.get(m.chat.id) == CONTACT)
    bot.register_message_handler(on_cancel_contact,
                                 func=lambda m: user_state.get(m.chat.id) == CONTACT and m.text in ("Отмена", "/cancel"))


def register_customer_fallback():
    """Перед общим «не понял»: подписанному клиенту — подсказка, а не просьба войти."""
    bot.register_message_handler(on_customer_message, func=is_customer)
