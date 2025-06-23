import logging
from datetime import datetime
from state import bot # Assuming bot instance is available globally
from database import get_low_stock_products, get_notification_recipient_chat_ids
from config import LOW_STOCK_THRESHOLD

logger = logging.getLogger(__name__)

def send_low_stock_notification():
    logger.info("Checking for low stock products to send notification.")
    
    recipient_chat_ids = get_notification_recipient_chat_ids()
    if not recipient_chat_ids:
        logger.warning("No chat IDs configured to receive low stock notifications. Skipping notification.")
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
            
            for chat_id in recipient_chat_ids:
                try:
                    bot.send_message(
                        chat_id,
                        notification_message,
                        parse_mode='Markdown'
                    )
                    logger.info(f"Low stock notification sent to chat ID {chat_id}.")
                except Exception as e:
                    logger.error(f"Failed to send low stock notification to chat ID {chat_id}: {e}")
        else:
            logger.info("No low stock products found. No notification sent.")

    except Exception as e:
        logger.exception(f"Error sending low stock notification: {e}")