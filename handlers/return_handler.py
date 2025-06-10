import logging
from telebot import types
from state import bot, user_data, user_state
from database import get_sale_and_product_details_by_sale_id, create_return_record
from buttons import create_cancel_markup, create_payment_type_markup, create_confirmation_markup
# from main import get_keyboard_by_role # Avoid circular import

logger = logging.getLogger(__name__)

# States for return
AWAITING_RETURN_ORDER_ID = "AWAITING_RETURN_ORDER_ID"
AWAITING_RETURN_PRICE = "AWAITING_RETURN_PRICE" # Typically fixed to sale price or less
AWAITING_RETURN_PAYMENT_TYPE = "AWAITING_RETURN_PAYMENT_TYPE"
AWAITING_RETURN_CONFIRMATION = "AWAITING_RETURN_CONFIRMATION"

def get_main_keyboard_by_role(chat_id):
    # Placeholder for modular keyboard retrieval
    from main import get_keyboard_by_role
    return get_keyboard_by_role(chat_id)

def start_return_handler(message: types.Message):
    chat_id = message.chat.id
    logger.info(f"Chat {chat_id}: Starting return process.")
    user_state[chat_id] = AWAITING_RETURN_ORDER_ID
    user_data[chat_id]['return_data'] = {}
    bot.send_message(chat_id, "Введите ID заказа для возврата:", reply_markup=create_cancel_markup())

def handle_return_order_id(message: types.Message):
    chat_id = message.chat.id
    order_id_text = message.text.strip()

    if order_id_text == "Отмена":
        cancel_return(chat_id)
        return

    try:
        order_id = int(order_id_text)
        logger.debug(f"Chat {chat_id}: Received order ID for return: {order_id}")
        sale_details = get_sale_and_product_details_by_sale_id(order_id)

        if sale_details:
            user_data[chat_id]['return_data']['sale_details'] = sale_details
            user_state[chat_id] = AWAITING_RETURN_PRICE # Or directly to payment type if price is fixed
            # Default return price to original sold price
            user_data[chat_id]['return_data']['price'] = sale_details['sold_price'] 
            bot.send_message(chat_id, 
                             f"Найден заказ: Товар {sale_details.get('product_model', 'N/A')} (Арт: {sale_details['article_number']}), Продано за: {sale_details['sold_price']:.2f}.\n"
                             f"Сумма к возврату по умолчанию: {sale_details['sold_price']:.2f}. Введите сумму возврата (не может быть больше цены продажи) или нажмите 'Отмена'.", 
                             reply_markup=create_cancel_markup())
        else:
            bot.send_message(chat_id, "Заказ не найден. Попробуйте еще раз или нажмите 'Отмена'.", reply_markup=create_cancel_markup())
    except ValueError:
        bot.send_message(chat_id, "ID заказа должен быть числом. Попробуйте еще раз или нажмите 'Отмена'.", reply_markup=create_cancel_markup())


def handle_return_price(message: types.Message):
    chat_id = message.chat.id
    price_text = message.text.strip()

    if price_text == "Отмена":
        cancel_return(chat_id)
        return

    try:
        price = float(price_text)
        sale_details = user_data[chat_id]['return_data']['sale_details']
        if not (0 < price <= sale_details['sold_price']):
            bot.send_message(chat_id, f"Сумма возврата должна быть положительной и не больше цены продажи ({sale_details['sold_price']:.2f}). Введите корректную сумму или нажмите 'Отмена'.", reply_markup=create_cancel_markup())
            return
        
        user_data[chat_id]['return_data']['price'] = price
        user_state[chat_id] = AWAITING_RETURN_PAYMENT_TYPE
        logger.debug(f"Chat {chat_id}: Received return price: {price}")
        bot.send_message(chat_id, "Выберите тип оплаты для возврата:", reply_markup=create_payment_type_markup())
    except ValueError:
        logger.warning(f"Chat {chat_id}: Invalid price format for return: {message.text}")
        bot.send_message(chat_id, "Неверный формат суммы. Введите число (например, 100.50) или нажмите 'Отмена'.", reply_markup=create_cancel_markup())


def handle_return_payment_type(message: types.Message):
    chat_id = message.chat.id
    payment_type = message.text.strip()

    if payment_type == "Отмена":
        cancel_return(chat_id)
        return

    if payment_type not in ["Наличные", "Безналичные"]:
        bot.send_message(chat_id, "Пожалуйста, выберите тип оплаты с помощью кнопок или нажмите 'Отмена'.", reply_markup=create_payment_type_markup())
        return

    user_data[chat_id]['return_data']['payment_type'] = payment_type
    logger.debug(f"Chat {chat_id}: Received payment type for return: {payment_type}")

    sale_details = user_data[chat_id]['return_data']['sale_details']
    price = user_data[chat_id]['return_data']['price']

    user_state[chat_id] = AWAITING_RETURN_CONFIRMATION
    confirmation_text = (f"Проверьте информацию о возврате:\n\n"
                         f"ID Заказа: {sale_details['sale_id']}\n"
                         f"Товар: {sale_details.get('product_model', 'N/A')} (Арт: {sale_details['article_number']})\n"
                         f"Сумма возврата: {price:.2f}\n"
                         f"Тип оплаты: {payment_type}\n\n"
                         f"Подтвердите возврат или отмените:")
    bot.send_message(chat_id, confirmation_text, reply_markup=create_confirmation_markup())

def handle_return_confirmation(message: types.Message):
    chat_id = message.chat.id
    choice = message.text.strip()

    if choice == "Подтвердить":
        logger.info(f"Chat {chat_id}: Return confirmed.")
        return_data = user_data[chat_id]['return_data']
        sale_details = return_data['sale_details']
        
        db_user_id = user_data[chat_id].get('id')
        store_id = user_data[chat_id].get('store_id')

        if not db_user_id or not store_id:
             logger.error(f"Chat {chat_id}: User ID or Store ID not found for return. User data: {user_data[chat_id]}")
             bot.send_message(chat_id, "Ошибка: Не удалось определить пользователя или магазин. Операция отменена.", reply_markup=get_main_keyboard_by_role(chat_id))
             clean_up_return_state(chat_id)
             return

        return_id = create_return_record(sale_details['sale_id'], 
                                         sale_details['article_number'], 
                                         return_data['price'], 
                                         return_data['payment_type'], 
                                         db_user_id,
                                         store_id)
        if return_id:
            bot.send_message(chat_id, "Возврат успешно завершен!", reply_markup=get_main_keyboard_by_role(chat_id))
        else:
            bot.send_message(chat_id, "Произошла ошибка при записи возврата. Попробуйте позже.", reply_markup=get_main_keyboard_by_role(chat_id))
    
    elif choice == "Отмена":
        logger.info(f"Chat {chat_id}: Return cancelled at confirmation stage.")
        cancel_return(chat_id)
    else:
        bot.send_message(chat_id, "Пожалуйста, используйте кнопки 'Подтвердить' или 'Отмена'.", reply_markup=create_confirmation_markup())
        return

    clean_up_return_state(chat_id)

def cancel_return(chat_id):
    logger.info(f"Chat {chat_id}: Return operation cancelled by user.")
    bot.send_message(chat_id, "Возврат отменен.", reply_markup=get_main_keyboard_by_role(chat_id))
    clean_up_return_state(chat_id)

def clean_up_return_state(chat_id):
    user_state.pop(chat_id, None)
    if chat_id in user_data and 'return_data' in user_data[chat_id]:
        del user_data[chat_id]['return_data']
    logger.debug(f"Chat {chat_id}: Cleaned up return state.")

def register_return_handlers():
    bot.register_message_handler(start_return_handler, func=lambda message: message.text == "Возврат" and user_data.get(message.chat.id, {}).get('role') in ['admin', 'cashier'], pass_bot=False)
    bot.register_message_handler(handle_return_order_id, func=lambda message: user_state.get(message.chat.id) == AWAITING_RETURN_ORDER_ID, pass_bot=False)
    bot.register_message_handler(handle_return_price, func=lambda message: user_state.get(message.chat.id) == AWAITING_RETURN_PRICE, pass_bot=False)
    bot.register_message_handler(handle_return_payment_type, func=lambda message: user_state.get(message.chat.id) == AWAITING_RETURN_PAYMENT_TYPE, pass_bot=False)
    bot.register_message_handler(handle_return_confirmation, func=lambda message: user_state.get(message.chat.id) == AWAITING_RETURN_CONFIRMATION, pass_bot=False)