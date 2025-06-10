import logging
from telebot import types
from state import bot, user_data, user_state # Assuming state.py is in the parent directory or PYTHONPATH is set
from database import get_product_by_article, create_buyback_record
from buttons import create_cancel_markup, create_payment_type_markup, create_confirmation_markup
# Assuming get_keyboard_by_role is accessible, e.g., from main or a utils module
# For now, let's define a placeholder or import if available.
# from main import get_keyboard_by_role # This would be a circular import, handle carefully

# States for buyback
AWAITING_BUYBACK_ARTICLE = "AWAITING_BUYBACK_ARTICLE"
AWAITING_BUYBACK_PRICE = "AWAITING_BUYBACK_PRICE"
AWAITING_BUYBACK_PAYMENT_TYPE = "AWAITING_BUYBACK_PAYMENT_TYPE"
AWAITING_BUYBACK_CONFIRMATION = "AWAITING_BUYBACK_CONFIRMATION"

logger = logging.getLogger(__name__)

def get_main_keyboard_by_role(chat_id):
    # This function needs to be defined globally or passed, temporary placeholder
    # Ideally, import from where it's defined, e.g., a 'keyboards_management.py' or 'main.py'
    # For now, let's assume it returns a generic main menu keyboard if role specific is complex.
    # from main import get_keyboard_by_role # Avoid circular if possible
    # Fallback to a simple message if function not available
    from main import get_keyboard_by_role # Assuming main.py can be imported for this
    return get_keyboard_by_role(chat_id)


def start_buyback_handler(message: types.Message):
    chat_id = message.chat.id
    logger.info(f"Chat {chat_id}: Starting buyback process.")
    user_state[chat_id] = AWAITING_BUYBACK_ARTICLE
    user_data[chat_id]['buyback_data'] = {}
    bot.send_message(chat_id, "Введите артикул товара для скупки:", reply_markup=create_cancel_markup())

def handle_buyback_article(message: types.Message):
    chat_id = message.chat.id
    article = message.text.strip()
    
    if message.text == "Отмена":
        cancel_buyback(chat_id)
        return

    logger.debug(f"Chat {chat_id}: Received article for buyback: {article}")
    product = get_product_by_article(article)

    if product:
        user_data[chat_id]['buyback_data']['product'] = product
        user_state[chat_id] = AWAITING_BUYBACK_PRICE
        bot.send_message(chat_id, f"Найден товар: {product.get('model', product.get('product_type', 'N/A'))}. Введите цену скупки:", reply_markup=create_cancel_markup())
    else:
        bot.send_message(chat_id, "Товар не найден. Попробуйте еще раз или нажмите 'Отмена'.", reply_markup=create_cancel_markup())

def handle_buyback_price(message: types.Message):
    chat_id = message.chat.id
    
    if message.text == "Отмена":
        cancel_buyback(chat_id)
        return

    try:
        price = float(message.text.strip())
        if price <= 0:
            raise ValueError("Price must be positive.")
        user_data[chat_id]['buyback_data']['price'] = price
        user_state[chat_id] = AWAITING_BUYBACK_PAYMENT_TYPE
        logger.debug(f"Chat {chat_id}: Received buyback price: {price}")
        bot.send_message(chat_id, "Выберите тип оплаты:", reply_markup=create_payment_type_markup())
    except ValueError:
        logger.warning(f"Chat {chat_id}: Invalid price format for buyback: {message.text}")
        bot.send_message(chat_id, "Неверный формат цены. Введите положительное число (например, 100.50) или нажмите 'Отмена'.", reply_markup=create_cancel_markup())

def handle_buyback_payment_type(message: types.Message):
    chat_id = message.chat.id
    payment_type = message.text.strip()

    if payment_type == "Отмена":
        cancel_buyback(chat_id)
        return

    if payment_type not in ["Наличные", "Безналичные"]:
        bot.send_message(chat_id, "Пожалуйста, выберите тип оплаты с помощью кнопок или нажмите 'Отмена'.", reply_markup=create_payment_type_markup())
        return

    user_data[chat_id]['buyback_data']['payment_type'] = payment_type
    logger.debug(f"Chat {chat_id}: Received payment type for buyback: {payment_type}")

    product = user_data[chat_id]['buyback_data']['product']
    price = user_data[chat_id]['buyback_data']['price']

    user_state[chat_id] = AWAITING_BUYBACK_CONFIRMATION
    confirmation_text = (f"Проверьте информацию о скупке:\n\n"
                         f"Товар: {product.get('model', product.get('product_type', 'N/A'))} (Арт: {product['article_number']})\n"
                         f"Цена скупки: {price:.2f}\n"
                         f"Тип оплаты: {payment_type}\n\n"
                         f"Подтвердите скупку или отмените:")
    bot.send_message(chat_id, confirmation_text, reply_markup=create_confirmation_markup())

def handle_buyback_confirmation(message: types.Message):
    chat_id = message.chat.id
    choice = message.text.strip()

    if choice == "Подтвердить":
        logger.info(f"Chat {chat_id}: Buyback confirmed.")
        buyback_data = user_data[chat_id]['buyback_data']
        product_article = buyback_data['product']['article_number']
        price = buyback_data['price']
        payment_type = buyback_data['payment_type']
        
        db_user_id = user_data[chat_id].get('id') # Authenticated user's DB ID
        store_id = user_data[chat_id].get('store_id') # User's store ID, ensure this is in user_data

        if not db_user_id or not store_id: # Basic check
             logger.error(f"Chat {chat_id}: User ID or Store ID not found in user_data for buyback. User data: {user_data[chat_id]}")
             bot.send_message(chat_id, "Ошибка: Не удалось определить пользователя или магазин. Операция отменена.", reply_markup=get_main_keyboard_by_role(chat_id))
             clean_up_buyback_state(chat_id)
             return

        buyback_id = create_buyback_record(product_article, price, payment_type, db_user_id, store_id)
        if buyback_id:
            bot.send_message(chat_id, "Скупка успешно завершена!", reply_markup=get_main_keyboard_by_role(chat_id))
        else:
            bot.send_message(chat_id, "Произошла ошибка при записи скупки. Попробуйте позже.", reply_markup=get_main_keyboard_by_role(chat_id))
    elif choice == "Отмена":
        logger.info(f"Chat {chat_id}: Buyback cancelled at confirmation stage.")
        cancel_buyback(chat_id)
    else:
        bot.send_message(chat_id, "Пожалуйста, используйте кнопки 'Подтвердить' или 'Отмена'.", reply_markup=create_confirmation_markup())
        return # Remain in confirmation state

    clean_up_buyback_state(chat_id)

def cancel_buyback(chat_id):
    logger.info(f"Chat {chat_id}: Buyback operation cancelled by user.")
    bot.send_message(chat_id, "Скупка отменена.", reply_markup=get_main_keyboard_by_role(chat_id))
    clean_up_buyback_state(chat_id)

def clean_up_buyback_state(chat_id):
    user_state.pop(chat_id, None)
    if chat_id in user_data and 'buyback_data' in user_data[chat_id]:
        del user_data[chat_id]['buyback_data']
    logger.debug(f"Chat {chat_id}: Cleaned up buyback state.")

def register_buyback_handlers():
    bot.register_message_handler(start_buyback_handler, func=lambda message: message.text == "Скупка" and user_data.get(message.chat.id, {}).get('role') in ['admin', 'cashier'], pass_bot=False)
    bot.register_message_handler(handle_buyback_article, func=lambda message: user_state.get(message.chat.id) == AWAITING_BUYBACK_ARTICLE, pass_bot=False)
    bot.register_message_handler(handle_buyback_price, func=lambda message: user_state.get(message.chat.id) == AWAITING_BUYBACK_PRICE, pass_bot=False)
    bot.register_message_handler(handle_buyback_payment_type, func=lambda message: user_state.get(message.chat.id) == AWAITING_BUYBACK_PAYMENT_TYPE, pass_bot=False)
    bot.register_message_handler(handle_buyback_confirmation, func=lambda message: user_state.get(message.chat.id) == AWAITING_BUYBACK_CONFIRMATION, pass_bot=False)