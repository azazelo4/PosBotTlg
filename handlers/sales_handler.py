import telebot
import logging
import re
from enum import Enum
from telebot import types
from database import get_product_by_article, record_sale
from buttons import create_cashier_buttons, create_admin_buttons, create_cancel_markup, create_confirmation_markup 
# create_payment_type_markup is too generic, sales needs its own with Kaspi
from state import user_data, bot as global_bot, user_state as global_user_state_main # Renamed for clarity

logger = logging.getLogger(__name__)

sale_handler = {} # This will store Sale instances, keyed by chat_id


def parse_price(price_str):
    if price_str is None:
        return None
    if not isinstance(price_str, str):
        price_str = str(price_str)
    temp_price = re.sub(r"[^\d,.]", "", price_str)
    if '.' in temp_price and ',' in temp_price:
        if temp_price.rfind('.') < temp_price.rfind(','):
            temp_price = temp_price.replace('.', '')
            temp_price = temp_price.replace(',', '.')
        else:
            temp_price = temp_price.replace(',', '')
    elif ',' in temp_price:
        temp_price = temp_price.replace(',', '.')
    try:
        if temp_price.count('.') > 1:
            parts = temp_price.split('.')
            temp_price = parts[0] + '.' + "".join(parts[1:])
        return float(temp_price)
    except ValueError:
        logger.warning(f"Could not parse price string: {price_str} -> {temp_price}")
        return None

# Specific payment keyboard for sales including Kaspi
def create_sales_payment_type_markup():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    button_cash = types.KeyboardButton("Наличные")
    button_non_cash = types.KeyboardButton("Безналичные")
    button_kaspi = types.KeyboardButton("Kaspi") # Added Kaspi
    button_cancel = types.KeyboardButton("Отмена")
    keyboard.add(button_cash, button_non_cash, button_kaspi) # Kaspi on its own row or with others
    keyboard.add(button_cancel)
    return keyboard


class SaleState(Enum):
    WAITING_ARTICLE = "SALES_WAITING_ARTICLE"
    WAITING_SALE_PRICE = "SALES_WAITING_SALE_PRICE"
    WAITING_PAYMENT_TYPE = "SALES_WAITING_PAYMENT_TYPE"
    CONFIRM_SALE = "SALES_CONFIRM_SALE"
    IDLE = "SALES_IDLE"


class Sale:
    def __init__(self, chat_id, user_specific_data, global_user_state_ref):
        self.chat_id = chat_id
        self.current_internal_state = SaleState.IDLE # Internal state for this Sale instance
        self.article_text = None
        self.sale_price_val = None
        self.payment_type_text = None
        self.product_info = None
        self.min_price_val = None
        self.user_db_data = user_specific_data
        self.global_user_state = global_user_state_ref # Reference to the main user_state dict
        # self.reset() # Reset is called when new sale starts or on cancel/completion

    def start_new_sale(self):
        logger.info(f"Chat {self.chat_id}: Starting new sale process.")
        self.current_internal_state = SaleState.WAITING_ARTICLE
        self.global_user_state[self.chat_id] = SaleState.WAITING_ARTICLE.value
        global_bot.send_message(self.chat_id, "Введите артикул товара:", reply_markup=create_cancel_markup())


    def process_message(self, message: types.Message):
        text = message.text.strip()
        # Ensure global state matches instance state before processing
        # This is a safeguard, usually they should be in sync if state transitions are managed correctly.
        if self.global_user_state.get(self.chat_id) != self.current_internal_state.value:
            logger.warning(f"Chat {self.chat_id}: State mismatch! Instance: {self.current_internal_state.value}, Global: {self.global_user_state.get(self.chat_id)}. Attempting to sync or reset.")
            # Prefer resetting if states are out of sync to avoid undefined behavior
            self.cancel_sale("Обнаружена ошибка состояния. Продажа отменена.")
            return

        logger.debug(f"Chat {self.chat_id}: Sale processing text '{text}', current state: {self.current_internal_state.value}")

        if text == 'Отмена': # This check is now primary
            self.cancel_sale()
            return

        if self.current_internal_state == SaleState.WAITING_ARTICLE:
            self.handle_article(text)
        elif self.current_internal_state == SaleState.WAITING_SALE_PRICE:
            self.handle_sale_price(text)
        elif self.current_internal_state == SaleState.WAITING_PAYMENT_TYPE:
            self.handle_payment_type(text)
        elif self.current_internal_state == SaleState.CONFIRM_SALE:
            self.handle_confirmation(text)
        else:
            logger.warning(f"Chat {self.chat_id}: Sale handler in unexpected state {self.current_internal_state.value}")
            self.cancel_sale("Неожиданное состояние. Продажа отменена.")


    def handle_article(self, article_text: str):
        self.article_text = article_text
        self.product_info = get_product_by_article(self.article_text)

        if self.product_info:
            self.min_price_val = parse_price(self.product_info.get('min_price'))
            if self.min_price_val is None:
                logger.error(f"Chat {self.chat_id}: Min price for article {self.article_text} is invalid: {self.product_info.get('min_price')}")
                global_bot.send_message(self.chat_id, "Ошибка: не удалось определить мин. цену. Продажа отменена.", reply_markup=get_keyboard_by_role(self.chat_id, self.user_db_data.get('role')))
                self.reset_and_remove_instance()
                return

            if self.product_info.get('quantity', 0) <= 0:
                logger.warning(f"Chat {self.chat_id}: Article {self.article_text} out of stock.")
                global_bot.send_message(self.chat_id, f"Товар {self.product_info.get('model', self.article_text)} закончился. Введите другой артикул или 'Отмена'.", reply_markup=create_cancel_markup())
                # self.current_internal_state remains WAITING_ARTICLE
                return

            response = f"""Артикул найден: {self.product_info.get('model', self.article_text)}
Тип: {self.product_info.get('product_type', 'N/A')}
Цена: {self.product_info.get('price', 'N/A')} (Мин: {self.min_price_val})
Остаток: {self.product_info.get('quantity', 'N/A')}
Введите сумму продажи:"""
            self.current_internal_state = SaleState.WAITING_SALE_PRICE
            self.global_user_state[self.chat_id] = SaleState.WAITING_SALE_PRICE.value
            global_bot.send_message(self.chat_id, response, reply_markup=create_cancel_markup())
        else:
            global_bot.send_message(self.chat_id, "Артикул не найден. Пожалуйста, введите корректный артикул или 'Отмена'.", reply_markup=create_cancel_markup())

    def handle_sale_price(self, price_text: str):
        parsed_price = parse_price(price_text)
        if parsed_price is None:
            global_bot.send_message(self.chat_id, "Некорректный формат суммы. Введите число (e.g., 15000.50) или 'Отмена'.", reply_markup=create_cancel_markup())
            return

        self.sale_price_val = parsed_price
        if self.sale_price_val < self.min_price_val:
            global_bot.send_message(self.chat_id, f"Сумма ({self.sale_price_val}) < мин. цены ({self.min_price_val}). Введите сумму или 'Отмена'.", reply_markup=create_cancel_markup())
            return
        
        self.current_internal_state = SaleState.WAITING_PAYMENT_TYPE
        self.global_user_state[self.chat_id] = SaleState.WAITING_PAYMENT_TYPE.value
        global_bot.send_message(self.chat_id, "Выберите тип оплаты:", reply_markup=create_sales_payment_type_markup()) # Use specific markup

    def handle_payment_type(self, payment_type_text: str):
        if payment_type_text not in ["Наличные", "Безналичные", "Kaspi"]:
            global_bot.send_message(self.chat_id, "Пожалуйста, выберите тип оплаты кнопками или 'Отмена'.", reply_markup=create_sales_payment_type_markup())
            return

        self.payment_type_text = payment_type_text
        self.current_internal_state = SaleState.CONFIRM_SALE
        self.global_user_state[self.chat_id] = SaleState.CONFIRM_SALE.value
        
        confirmation_details = f"""Подтвердите продажу:
Товар: {self.product_info.get('model', self.article_text)} (Арт: {self.product_info['article_number']})
Сумма: {self.sale_price_val:.2f}
Тип оплаты: {self.payment_type_text}"""
        global_bot.send_message(self.chat_id, confirmation_details, reply_markup=create_confirmation_markup())

    def handle_confirmation(self, confirmation_text: str):
        if confirmation_text == "Подтвердить":
            user_db_id = self.user_db_data.get('id')
            # store_db_id is removed from record_sale call as sales.store_id is removed

            if not user_db_id: # Check only user_db_id now
                logger.error(f"Chat {self.chat_id}: Missing user ID. User data: {self.user_db_data}")
                global_bot.send_message(self.chat_id, "Ошибка: нет данных пользователя. Продажа отменена.", reply_markup=get_keyboard_by_role(self.chat_id, self.user_db_data.get('role')))
                self.reset_and_remove_instance() # Ensure cleanup
                return

            success, db_msg = record_sale( # Capture the message
                product_id=self.product_info['id'],
                product_article=self.product_info['article_number'],
                user_id=user_db_id,
                sale_price=self.sale_price_val,
                payment_type=self.payment_type_text
            )

            if success:
                global_bot.send_message(self.chat_id, "Продажа успешно завершена!", reply_markup=get_keyboard_by_role(self.chat_id, self.user_db_data.get('role')))
            else: # Use the message from the database function
                global_bot.send_message(self.chat_id, db_msg, reply_markup=get_keyboard_by_role(self.chat_id, self.user_db_data.get('role')))
            self.reset_and_remove_instance() # Ensure cleanup regardless of success
        elif confirmation_text == "Отмена": 
            self.cancel_sale()
        else:
            global_bot.send_message(self.chat_id, "Пожалуйста, 'Подтвердить' или 'Отмена'.", reply_markup=create_confirmation_markup())

    def cancel_sale(self, message="Продажа отменена."):
        logger.info(f"Chat {self.chat_id}: Sale operation cancelled by user or system.")
        global_bot.send_message(self.chat_id, message, reply_markup=get_keyboard_by_role(self.chat_id, self.user_db_data.get('role')))
        self.reset_and_remove_instance()

    def reset_and_remove_instance(self):
        logger.debug(f"Chat {self.chat_id}: Resetting Sale instance and removing from handler.")
        self.current_internal_state = SaleState.IDLE
        self.article_text = None
        self.sale_price_val = None
        self.payment_type_text = None
        self.product_info = None
        self.min_price_val = None
        if self.global_user_state.get(self.chat_id, "").startswith("SALES_"):
            self.global_user_state.pop(self.chat_id, None)
        if self.chat_id in sale_handler:
            del sale_handler[self.chat_id]


def get_keyboard_by_role(chat_id, role):
    if role == 'cashier':
        return create_cashier_buttons()
    elif role == 'admin':
        return create_admin_buttons()
    else:
        return types.ReplyKeyboardRemove()


def register_sales_handlers(bot_instance_arg, global_user_data_dict_arg, global_user_state_dict_arg):
    # global_user_state_dict_arg is user_state from state.py
    
    @global_bot.message_handler(func=lambda msg: msg.text == 'Продажа' and \
                                   global_user_data_dict_arg.get(msg.chat.id, {}).get('is_active', 0) == 1 and \
                                   global_user_data_dict_arg.get(msg.chat.id, {}).get('role') in ['admin', 'cashier'])
    def entry_point_sale(message: types.Message):
        chat_id = message.chat.id
        logger.debug(f"Chat {chat_id}: 'Продажа' button pressed.")
        
        if chat_id in sale_handler and sale_handler[chat_id].current_internal_state != SaleState.IDLE:
            logger.warning(f"Chat {chat_id}: Sale already in progress (state: {sale_handler[chat_id].current_internal_state.value}). Resetting old sale.")
            sale_handler[chat_id].reset_and_remove_instance() 

        sale_handler[chat_id] = Sale(chat_id, global_user_data_dict_arg[chat_id], global_user_state_dict_arg)
        sale_handler[chat_id].start_new_sale()

    @global_bot.message_handler(func=lambda msg: global_user_state_dict_arg.get(msg.chat.id, "").startswith("SALES_") and \
                                   global_user_state_dict_arg.get(msg.chat.id) != SaleState.IDLE.value)
    def handle_all_sale_steps(message: types.Message):
        chat_id = message.chat.id
        if chat_id in sale_handler:
            sale_instance = sale_handler[chat_id]
            # Check if the instance's internal state matches the global state.
            # This is a crucial check for FSM integrity.
            if sale_instance.current_internal_state.value == global_user_state_dict_arg.get(chat_id):
                sale_instance.process_message(message)
            else:
                logger.error(f"Chat {chat_id}: State mismatch! Instance: {sale_instance.current_internal_state.value}, Global: {global_user_state_dict_arg.get(chat_id)}. Resetting sale.")
                sale_instance.cancel_sale("Обнаружена ошибка состояния. Продажа отменена.")
        else: # Instance was lost or never created, but global state indicates sales.
            logger.warning(f"Chat {chat_id}: Msg for sales step, but no Sale instance. Global state: {global_user_state_dict_arg.get(chat_id)}. Resetting global state.")
            global_user_state_dict_arg.pop(chat_id, None)
            role = global_user_data_dict_arg.get(chat_id, {}).get('role')
            global_bot.send_message(chat_id, "Ошибка в процессе продажи. Начните заново.", reply_markup=get_keyboard_by_role(chat_id, role))