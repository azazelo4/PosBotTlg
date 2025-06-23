import logging
from datetime import datetime
from state import bot # Assuming bot instance is available globally
from database import get_low_stock_products
from config import LOW_STOCK_THRESHOLD, LOW_STOCK_NOTIFICATION_CHAT_ID

logger = logging.getLogger(__name__)

def send_low_stock_notification():
    logger.info("Checking for low stock products to send notification.")
    if not LOW_STOCK_NOTIFICATION_CHAT_ID:
        logger.warning("LOW_STOCK_NOTIFICATION_CHAT_ID is not configured. Low stock notifications will not be sent.")
        return

    # Ensure LOW_STOCK_THRESHOLD is an integer
    threshold = int(LOW_STOCK_THRESHOLD) if LOW_STOCK_THRESHOLD is not None else 0

    try:
        low_stock_products = get_low_stock_products(threshold)

        if low_stock_products:
            notification_message = "🚨 *Уведомление о низком остатке товаров:*\n\n"
            for product in low_stock_products:
                notification_message += (
                    f"*{product['model']}* (Арт: `{product['article_number']}`)\n"
                    f"  Остаток: `{product['quantity']}` шт. (Магазин: {product['Store']})\n"
                )
            notification_message += f"\n_Порог низкого остатка: {threshold} шт._"
            
            bot.send_message(
                LOW_STOCK_NOTIFICATION_CHAT_ID,
                notification_message,
                parse_mode='Markdown'
            )
            logger.info(f"Low stock notification sent to {LOW_STOCK_NOTIFICATION_CHAT_ID}.")
        else:
            logger.info("No low stock products found. No notification sent.")

    except Exception as e:
        logger.exception(f"Error sending low stock notification: {e}")
        if LOW_STOCK_NOTIFICATION_CHAT_ID:
            bot.send_message(LOW_STOCK_NOTIFICATION_CHAT_ID, f"Произошла ошибка при проверке низкого остатка: {e}")