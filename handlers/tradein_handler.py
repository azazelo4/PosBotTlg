import logging
from telebot import types
from state import bot, user_data, user_state
from database import get_product_by_article, create_trade_in_record
from buttons import create_cancel_markup, create_payment_type_markup, create_confirmation_markup
# from main import get_keyboard_by_role # Avoid circular import

logger = logging.getLogger(__name__)

# States for trade-in
AWAITING_TRADEIN_OLD_ARTICLE = "AWAITING_TRADEIN_OLD_ARTICLE"
AWAITING_TRADEIN_NEW_ARTICLE = "AWAITING_TRADEIN_NEW_ARTICLE"
AWAITING_TRADEIN_DIFFERENCE = "AWAITING_TRADEIN_DIFFERENCE"
AWAITING_TRADEIN_PAYMENT_TYPE_DIFFERENCE = "AWAITING_TRADEIN_PAYMENT_TYPE_DIFFERENCE"
AWAITING_TRADEIN_CONFIRMATION = "AWAITING_TRADEIN_CONFIRMATION"

def get_main_keyboard_by_role(chat_id):
    # Placeholder
    from main import get_keyboard_by_role
    return get_keyboard_by_role(chat_id)

def start_tradein_handler(message: types.Message):
    chat_id = message.chat.id
    logger.info(f"Chat {chat_id}: Starting trade-in process.")
    user_state[chat_id] = AWAITING_TRADEIN_OLD_ARTICLE
    user_data[chat_id]['tradein_data'] = {}
    bot.send_message(chat_id, "Введите артикул СТАРОГО товара (который сдают):", reply_markup=create_cancel_markup())

def handle_tradein_old_article(message: types.Message):
    chat_id = message.chat.id
    article = message.text.strip()

    if article == "Отмена":
        cancel_tradein(chat_id)
        return

    logger.debug(f"Chat {chat_id}: Received old article for trade-in: {article}")
    product = get_product_by_article(article)

    if product:
        user_data[chat_id]['tradein_data']['old_product'] = product
        user_state[chat_id] = AWAITING_TRADEIN_NEW_ARTICLE
        bot.send_message(chat_id, f"Старый товар: {product.get('model', 'N/A')}. Теперь введите артикул НОВОГО товара (который покупают):", reply_markup=create_cancel_markup())
    else:
        bot.send_message(chat_id, "Старый товар не найден. Попробуйте еще раз или нажмите 'Отмена'.", reply_markup=create_cancel_markup())

def handle_tradein_new_article(message: types.Message):
    chat_id = message.chat.id
    article = message.text.strip()

    if article == "Отмена":
        cancel_tradein(chat_id)
        return

    logger.debug(f"Chat {chat_id}: Received new article for trade-in: {article}")
    product = get_product_by_article(article)

    if product:
        # Check if new product is same as old product
        if product['article_number'] == user_data[chat_id]['tradein_data']['old_product']['article_number']:
            bot.send_message(chat_id, "Новый товар не может быть таким же, как старый. Введите другой артикул или нажмите 'Отмена'.", reply_markup=create_cancel_markup())
            return

        user_data[chat_id]['tradein_data']['new_product'] = product
        user_state[chat_id] = AWAITING_TRADEIN_DIFFERENCE
        bot.send_message(chat_id, f"Новый товар: {product.get('model', 'N/A')}. Введите сумму ДОПЛАТЫ от клиента (если клиент получает деньги, введите отрицательное число, например -100):", reply_markup=create_cancel_markup())
    else:
        bot.send_message(chat_id, "Новый товар не найден. Попробуйте еще раз или нажмите 'Отмена'.", reply_markup=create_cancel_markup())

def handle_tradein_difference(message: types.Message):
    chat_id = message.chat.id
    difference_text = message.text.strip()

    if difference_text == "Отмена":
        cancel_tradein(chat_id)
        return
        
    try:
        difference = float(difference_text)
        user_data[chat_id]['tradein_data']['difference'] = difference
        logger.debug(f"Chat {chat_id}: Received price difference for trade-in: {difference}")

        if difference != 0: # Only ask for payment type if there's a monetary transaction
            user_state[chat_id] = AWAITING_TRADEIN_PAYMENT_TYPE_DIFFERENCE
            bot.send_message(chat_id, f"Доплата: {difference:.2f}. Выберите тип оплаты для этой суммы:", reply_markup=create_payment_type_markup())
        else: # If difference is zero, skip payment type and go to confirmation
            user_data[chat_id]['tradein_data']['payment_type_difference'] = "Нет (доплата 0)"
            proceed_to_tradein_confirmation(chat_id)

    except ValueError:
        logger.warning(f"Chat {chat_id}: Invalid difference format for trade-in: {message.text}")
        bot.send_message(chat_id, "Неверный формат суммы доплаты. Введите число (например, 100.50 или -50) или нажмите 'Отмена'.", reply_markup=create_cancel_markup())

def handle_tradein_payment_type_difference(message: types.Message):
    chat_id = message.chat.id
    payment_type = message.text.strip()

    if payment_type == "Отмена":
        cancel_tradein(chat_id)
        return

    if payment_type not in ["Наличные", "Безналичные"]:
        bot.send_message(chat_id, "Пожалуйста, выберите тип оплаты с помощью кнопок или нажмите 'Отмена'.", reply_markup=create_payment_type_markup())
        return
    
    user_data[chat_id]['tradein_data']['payment_type_difference'] = payment_type
    logger.debug(f"Chat {chat_id}: Received payment type for trade-in difference: {payment_type}")
    proceed_to_tradein_confirmation(chat_id)


def proceed_to_tradein_confirmation(chat_id):
    tradein_data = user_data[chat_id]['tradein_data']
    old_product = tradein_data['old_product']
    new_product = tradein_data['new_product']
    difference = tradein_data['difference']
    payment_type_diff = tradein_data.get('payment_type_difference', "N/A (доплата 0)")


    user_state[chat_id] = AWAITING_TRADEIN_CONFIRMATION
    confirmation_text = (f"Проверьте информацию об обмене (Trade-In):\n\n"
                         f"Сдается товар (старый): {old_product.get('model', 'N/A')} (Арт: {old_product['article_number']})\n"
                         f"Приобретается товар (новый): {new_product.get('model', 'N/A')} (Арт: {new_product['article_number']})\n"
                         f"Доплата от клиента: {difference:.2f}\n"
                         f"Тип оплаты доплаты: {payment_type_diff}\n\n"
                         f"Подтвердите обмен или отмените:")
    bot.send_message(chat_id, confirmation_text, reply_markup=create_confirmation_markup())


def handle_tradein_confirmation(message: types.Message):
    chat_id = message.chat.id
    choice = message.text.strip()

    if choice == "Подтвердить":
        logger.info(f"Chat {chat_id}: Trade-in confirmed.")
        tradein_data = user_data[chat_id]['tradein_data']
        
        db_user_id = user_data[chat_id].get('id')
        store_id = user_data[chat_id].get('store_id')

        if not db_user_id or not store_id:
             logger.error(f"Chat {chat_id}: User ID or Store ID not found for trade-in. User data: {user_data[chat_id]}")
             bot.send_message(chat_id, "Ошибка: Не удалось определить пользователя или магазин. Операция отменена.", reply_markup=get_main_keyboard_by_role(chat_id))
             clean_up_tradein_state(chat_id)
             return

        trade_in_id = create_trade_in_record(
            old_product_article=tradein_data['old_product']['article_number'],
            new_product_article=tradein_data['new_product']['article_number'],
            price_difference=tradein_data['difference'],
            payment_type_difference=tradein_data.get('payment_type_difference', "Нет"),
            user_id=db_user_id,
            store_id=store_id
        )
        if trade_in_id:
            bot.send_message(chat_id, "Обмен (Trade-In) успешно завершен!", reply_markup=get_main_keyboard_by_role(chat_id))
        else:
            bot.send_message(chat_id, "Произошла ошибка при записи обмена. Попробуйте позже.", reply_markup=get_main_keyboard_by_role(chat_id))
            
    elif choice == "Отмена":
        logger.info(f"Chat {chat_id}: Trade-in cancelled at confirmation stage.")
        cancel_tradein(chat_id)
    else:
        bot.send_message(chat_id, "Пожалуйста, используйте кнопки 'Подтвердить' или 'Отмена'.", reply_markup=create_confirmation_markup())
        return

    clean_up_tradein_state(chat_id)

def cancel_tradein(chat_id):
    logger.info(f"Chat {chat_id}: Trade-in operation cancelled by user.")
    bot.send_message(chat_id, "Обмен (Trade-In) отменен.", reply_markup=get_main_keyboard_by_role(chat_id))
    clean_up_tradein_state(chat_id)

def clean_up_tradein_state(chat_id):
    user_state.pop(chat_id, None)
    if chat_id in user_data and 'tradein_data' in user_data[chat_id]:
        del user_data[chat_id]['tradein_data']
    logger.debug(f"Chat {chat_id}: Cleaned up trade-in state.")

def register_tradein_handlers():
    bot.register_message_handler(start_tradein_handler, func=lambda message: message.text == "Trade-In" and user_data.get(message.chat.id, {}).get('role') in ['admin', 'cashier'], pass_bot=False)
    bot.register_message_handler(handle_tradein_old_article, func=lambda message: user_state.get(message.chat.id) == AWAITING_TRADEIN_OLD_ARTICLE, pass_bot=False)
    bot.register_message_handler(handle_tradein_new_article, func=lambda message: user_state.get(message.chat.id) == AWAITING_TRADEIN_NEW_ARTICLE, pass_bot=False)
    bot.register_message_handler(handle_tradein_difference, func=lambda message: user_state.get(message.chat.id) == AWAITING_TRADEIN_DIFFERENCE, pass_bot=False)
    bot.register_message_handler(handle_tradein_payment_type_difference, func=lambda message: user_state.get(message.chat.id) == AWAITING_TRADEIN_PAYMENT_TYPE_DIFFERENCE, pass_bot=False)
    bot.register_message_handler(handle_tradein_confirmation, func=lambda message: user_state.get(message.chat.id) == AWAITING_TRADEIN_CONFIRMATION, pass_bot=False)