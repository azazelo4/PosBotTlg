import telebot
import logging
import re
from enum import Enum
from telebot import types
from database import get_product_by_article, record_sale # record_sale now expects product_article
from buttons import create_cashier_buttons, create_admin_buttons, create_cancel_markup, create_confirmation_markup, create_payment_type_markup # Added more specific markups
from state import user_data, bot as global_bot # Assuming bot is imported from state for sending messages

logger = logging.getLogger(__name__)

# This dictionary will store Sale instances, keyed by chat_id
sale_handler = {}


def parse_price(price_str):
    if price_str is None:
        return None
    if not isinstance(price_str, str):
        price_str = str(price_str)
    # Improved regex to handle various formats like "1,000.50" or "1.000,50" or just "1000.50" / "1000,50"
    # Remove all thousands separators ('.' or ',' not followed by exactly 2 digits if it's the decimal sep)
    # This is tricky with regex alone for international formats. A library might be better for robust parsing.
    # Simple approach: remove all dots, then replace comma with dot. Or vice-versa if comma is thousands.
    # Assuming comma is decimal separator if it's the only one or last one.
    
    # Let's try to remove all non-digit chars except the last comma or dot
    temp_price = re.sub(r"[^\d,.]", "", price_str)
    
    # If both . and , exist, assume . is thousands and , is decimal (European style)
    if '.' in temp_price and ',' in temp_price:
        if temp_price.rfind('.') < temp_price.rfind(','): #
            temp_price = temp_price.replace('.', '') # Remove thousands separator
            temp_price = temp_price.replace(',', '.') # Convert decimal comma to dot
        else: # Assume , is thousands and . is decimal (US style)
            temp_price = temp_price.replace(',', '')
    elif ',' in temp_price: # Only comma exists, assume it's decimal
        temp_price = temp_price.replace(',', '.')
    # If only dot exists, it's assumed to be decimal already.
    # If neither, it's an integer.

    try:
        # Final cleanup for multiple dots if any survived (shouldn't with above logic but as safeguard)
        if temp_price.count('.') > 1:
            parts = temp_price.split('.')
            temp_price = parts[0] + '.' + "".join(parts[1:])

        return float(temp_price)
    except ValueError:
        logger.warning(f"Could not parse price string: {price_str} -> {temp_price}")
        return None


class SaleState(Enum):
    WAITING_ARTICLE = "SALES_WAITING_ARTICLE"
    WAITING_SALE_PRICE = "SALES_WAITING_SALE_PRICE"
    WAITING_PAYMENT_TYPE = "SALES_WAITING_PAYMENT_TYPE"
    CONFIRM_SALE = "SALES_CONFIRM_SALE"
    IDLE = "SALES_IDLE"


class Sale:
    def __init__(self, chat_id, user_specific_data, global_user_state_dict):
        self.chat_id = chat_id
        self.state = SaleState.IDLE
        self.article_text = None
        self.sale_price_val = None
        self.payment_type_text = None
        self.product_info = None # This will store the dict from get_product_by_article
        self.min_price_val = None
        self.user_db_data = user_specific_data # e.g., {'id': DB_USER_ID, 'store_id': ..., 'role': ...}
        self.global_user_state = global_user_state_dict # Main user_state dict from state.py
        self.reset()

    def start_new_sale(self):
        logger.info(f"Chat {self.chat_id}: Starting new sale process.")
        self.state = SaleState.WAITING_ARTICLE
        self.global_user_state[self.chat_id] = SaleState.WAITING_ARTICLE.value # Set global state
        global_bot.send_message(self.chat_id, "Введите артикул товара:", reply_markup=create_cancel_markup())


    def process_message(self, message: types.Message):
        text = message.text.strip()
        logger.debug(f"Chat {self.chat_id}: Sale processing text '{text}', current state: {self.state.value}")

        if text == 'Отмена':
            self.cancel_sale()
            return

        if self.state == SaleState.WAITING_ARTICLE:
            self.handle_article(text)
        elif self.state == SaleState.WAITING_SALE_PRICE:
            self.handle_sale_price(text)
        elif self.state == SaleState.WAITING_PAYMENT_TYPE:
            self.handle_payment_type(text)
        elif self.state == SaleState.CONFIRM_SALE:
            self.handle_confirmation(text)
        else:
            logger.warning(f"Chat {self.chat_id}: Sale handler in unexpected state {self.state.value}")
            self.cancel_sale()


    def handle_article(self, article_text: str):
        self.article_text = article_text
        self.product_info = get_product_by_article(self.article_text)

        if self.product_info:
            self.min_price_val = parse_price(self.product_info.get('min_price')) # min_price from DB
            if self.min_price_val is None:
                logger.error(f"Chat {self.chat_id}: Min price for article {self.article_text} is invalid or missing in DB: {self.product_info.get('min_price')}")
                global_bot.send_message(self.chat_id, "Ошибка: не удалось определить минимальную цену товара. Продажа отменена.", reply_markup=get_keyboard_by_role(self.chat_id, self.user_db_data.get('role')))
                self.reset()
                return

            if self.product_info.get('quantity', 0) <= 0:
                logger.warning(f"Chat {self.chat_id}: Article {self.article_text} found but quantity is {self.product_info.get('quantity', 0)}.")
                global_bot.send_message(self.chat_id, f"Товар {self.product_info.get('model', self.article_text)} закончился на складе. Продажа невозможна.", reply_markup=create_cancel_markup())
                # Don't reset state yet, user might want to enter another article or cancel
                self.state = SaleState.WAITING_ARTICLE # Remain in article state
                self.global_user_state[self.chat_id] = SaleState.WAITING_ARTICLE.value
                return

            response = f"""Артикул найден: {self.product_info.get('model', self.article_text)}
Тип: {self.product_info.get('product_type', 'N/A')}
Цена: {self.product_info.get('price', 'N/A')} (Мин: {self.min_price_val})
Остаток: {self.product_info.get('quantity', 'N/A')}
Введите сумму продажи:"""
            self.state = SaleState.WAITING_SALE_PRICE
            self.global_user_state[self.chat_id] = SaleState.WAITING_SALE_PRICE.value
            global_bot.send_message(self.chat_id, response, reply_markup=create_cancel_markup())
        else:
            global_bot.send_message(self.chat_id, "Артикул не найден. Пожалуйста, введите корректный артикул или 'Отмена'.", reply_markup=create_cancel_markup())

    def handle_sale_price(self, price_text: str):
        parsed_price = parse_price(price_text)
        if parsed_price is None:
            global_bot.send_message(self.chat_id, "Некорректный формат суммы. Введите число (например, 15000.50) или 'Отмена'.", reply_markup=create_cancel_markup())
            return

        self.sale_price_val = parsed_price
        if self.sale_price_val < self.min_price_val:
            global_bot.send_message(self.chat_id, f"Сумма продажи ({self.sale_price_val}) не может быть меньше минимальной цены ({self.min_price_val}). Введите корректную сумму или 'Отмена'.", reply_markup=create_cancel_markup())
            return
        
        self.state = SaleState.WAITING_PAYMENT_TYPE
        self.global_user_state[self.chat_id] = SaleState.WAITING_PAYMENT_TYPE.value
        global_bot.send_message(self.chat_id, "Выберите тип оплаты:", reply_markup=create_payment_type_markup())


    def handle_payment_type(self, payment_type_text: str):
        if payment_type_text not in ["Наличные", "Безналичные", "Kaspi"]: # Make sure Kaspi is handled or removed
            global_bot.send_message(self.chat_id, "Пожалуйста, выберите тип оплаты с помощью кнопок или 'Отмена'.", reply_markup=create_payment_type_markup())
            return

        self.payment_type_text = payment_type_text
        self.state = SaleState.CONFIRM_SALE
        self.global_user_state[self.chat_id] = SaleState.CONFIRM_SALE.value
        
        confirmation_details = f"""Подтвердите продажу:
Товар: {self.product_info.get('model', self.article_text)} (Арт: {self.product_info['article_number']})
Сумма: {self.sale_price_val:.2f}
Тип оплаты: {self.payment_type_text}"""
        global_bot.send_message(self.chat_id, confirmation_details, reply_markup=create_confirmation_markup())

    def handle_confirmation(self, confirmation_text: str):
        if confirmation_text == "Подтвердить":
            user_db_id = self.user_db_data.get('id')
            store_db_id = self.user_db_data.get('store_id')

            if not user_db_id or not store_db_id:
                logger.error(f"Chat {self.chat_id}: Missing user_id or store_id for sale confirmation. User data: {self.user_db_data}")
                global_bot.send_message(self.chat_id, "Ошибка: не удалось определить пользователя или магазин. Продажа отменена.", reply_markup=get_keyboard_by_role(self.chat_id, self.user_db_data.get('role')))
                self.reset()
                return

            success = record_sale(
                product_id=self.product_info['id'],
                product_article=self.product_info['article_number'], # Pass article for the new record_sale
                store_id=store_db_id,
                user_id=user_db_id,
                sale_price=self.sale_price_val,
                payment_type=self.payment_type_text
            )

            if success:
                global_bot.send_message(self.chat_id, "Продажа успешно завершена!", reply_markup=get_keyboard_by_role(self.chat_id, self.user_db_data.get('role')))
            else:
                global_bot.send_message(self.chat_id, "Произошла ошибка при записи продажи. Пожалуйста, попробуйте позже или обратитесь к администратору.", reply_markup=get_keyboard_by_role(self.chat_id, self.user_db_data.get('role')))
            self.reset()
        elif confirmation_text == "Отмена": # "Отменить" from confirmation markup
            self.cancel_sale()
        else:
            global_bot.send_message(self.chat_id, "Пожалуйста, используйте кнопки 'Подтвердить' или 'Отмена'.", reply_markup=create_confirmation_markup())


    def cancel_sale(self):
        logger.info(f"Chat {self.chat_id}: Sale operation cancelled by user or system.")
        global_bot.send_message(self.chat_id, "Продажа отменена.", reply_markup=get_keyboard_by_role(self.chat_id, self.user_db_data.get('role')))
        self.reset()

    def reset(self):
        logger.debug(f"Chat {self.chat_id}: Resetting Sale instance state.")
        self.state = SaleState.IDLE
        self.article_text = None
        self.sale_price_val = None
        self.payment_type_text = None
        self.product_info = None
        self.min_price_val = None
        # Clear the global state if it was set by this sales instance
        if self.global_user_state.get(self.chat_id, "").startswith("SALES_"):
            self.global_user_state.pop(self.chat_id, None)
        # Remove from the global sale_handler dictionary upon true reset
        if self.chat_id in sale_handler: # Check existence before deleting
            del sale_handler[self.chat_id]


# This function needs to be imported or defined in main.py and passed if preferred
def get_keyboard_by_role(chat_id, role): # Role passed for clarity
    # from main import get_keyboard_by_role as main_get_kb_by_role # Avoid if possible
    # return main_get_kb_by_role(chat_id)
    # Simplified direct usage for now:
    if role == 'cashier':
        return create_cashier_buttons()
    elif role == 'admin':
        return create_admin_buttons()
    else: # Fallback, should not happen for authorized users
        return types.ReplyKeyboardRemove()


def register_sales_handlers(bot_instance_arg, global_user_data_dict_arg, global_user_state_dict_arg=None):
    # bot_instance_arg is the shared bot from state.py (passed as global_bot for clarity within Sale class)
    # global_user_data_dict_arg is user_data from state.py
    # global_user_state_dict_arg is user_state from state.py (passed to Sale constructor)

    @global_bot.message_handler(func=lambda msg: msg.text == 'Продажа' and \
                                   global_user_data_dict_arg.get(msg.chat.id, {}).get('is_active', 0) == 1 and \
                                   global_user_data_dict_arg.get(msg.chat.id, {}).get('role') in ['admin', 'cashier'])
    def entry_point_sale(message: types.Message):
        chat_id = message.chat.id
        logger.debug(f"Chat {chat_id}: 'Продажа' button pressed.")
        
        # If a sale is already in progress for this chat, reset it or notify user.
        if chat_id in sale_handler and sale_handler[chat_id].state != SaleState.IDLE:
            logger.warning(f"Chat {chat_id}: Sale already in progress (state: {sale_handler[chat_id].state.value}). Resetting old sale.")
            sale_handler[chat_id].reset() # This will also remove it from sale_handler dict

        # Create a new Sale instance
        sale_handler[chat_id] = Sale(chat_id, global_user_data_dict_arg[chat_id], global_user_state_dict_arg)
        sale_handler[chat_id].start_new_sale()

    @global_bot.message_handler(func=lambda msg: global_user_state_dict_arg.get(msg.chat.id, "").startswith("SALES_") and \
                                   global_user_state_dict_arg.get(msg.chat.id) != SaleState.IDLE.value)
    def handle_all_sale_steps(message: types.Message):
        chat_id = message.chat.id
        if chat_id in sale_handler:
            sale_instance = sale_handler[chat_id]
            # Ensure the instance's state matches the global state for safety
            if sale_instance.state.value == global_user_state_dict_arg.get(chat_id):
                sale_instance.process_message(message)
            else:
                logger.error(f"Chat {chat_id}: Mismatch between Sale instance state ({sale_instance.state.value}) and global state ({global_user_state_dict_arg.get(chat_id)}). Resetting sale.")
                sale_instance.cancel_sale() # This will reset states and the instance
        else:
            logger.warning(f"Chat {chat_id}: Received message for sales step, but no Sale instance found. Global state: {global_user_state_dict_arg.get(chat_id)}. Resetting global state.")
            global_user_state_dict_arg.pop(chat_id, None)
            role = global_user_data_dict_arg.get(chat_id, {}).get('role')
            global_bot.send_message(chat_id, "Произошла ошибка в процессе продажи. Пожалуйста, начните заново.", reply_markup=get_keyboard_by_role(chat_id, role))