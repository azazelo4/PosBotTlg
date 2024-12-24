import telebot
import logging
import re
from enum import Enum
from telebot import types
from database import get_product_by_article, record_sale
from buttons import create_cashier_buttons, create_admin_buttons
from state import user_data

logger = logging.getLogger(__name__)

def parse_price(price_str):
    # 1) Защита от None
    if price_str is None:
        return None
    
    # 2) Приводим к строке, если это число
    if not isinstance(price_str, str):
        price_str = str(price_str)

    cleaned_price = re.sub(r"[^\d.,]", "", price_str)
    if ',' in cleaned_price and '.' in cleaned_price:
        cleaned_price = cleaned_price.replace('.', '').replace(',', '.')
    elif cleaned_price.count(',') == 1 and cleaned_price.count('.') == 0:
        cleaned_price = cleaned_price.replace(',', '.')
    else:
        cleaned_price = cleaned_price.replace('.', '')

    try:
        return float(cleaned_price)
    except ValueError:
        return None

class SaleState(Enum):
    WAITING_ARTICLE = 1
    WAITING_SALE_PRICE = 2
    WAITING_PAYMENT_TYPE = 3
    CONFIRM_SALE = 4

class Sale:
    def __init__(self, user_data):
        self.state = SaleState.WAITING_ARTICLE
        self.article = None
        self.sale_price = None
        self.payment_type = None
        self.product = None
        self.min_price = None
        self.user_data = user_data

    def process_waiting_article(self, message, user_data):
        logger.debug(f"Обработка артикула: {message.text} для пользователя: {user_data}")
        if message.text == 'Отмена':
            self.reset()
            logger.info("Процесс продажи отменен пользователем.")
            return None
        else:
            self.article = message.text
            try:
                self.product = get_product_by_article(self.article)
                if self.product:
                    self.min_price = parse_price(self.product['min_price'])
                    logger.info(f"Товар найден: {self.product['product_type']}, минимальная цена: {self.min_price}")
                    if self.min_price is None:
                        logger.error("Ошибка: минимальная цена не может быть обработана.")
                        return "Ошибка данных: некорректное значение минимальной цены."
                    response = f"""
                    Артикул найден. \n
                    Изделие: {self.product['product_type']}\n
                    Металл: {self.product['metal_type']}\n
                    Модель: {self.product['model']}\n
                    Вес: {self.product['weight']}\n
                    Размер: {self.product['размер']}.\n
                    Остаток: {self.product['quantity']}\n
                    Цена: {self.product['price']}\n
                    Введите сумму продажи:"""
                    self.state = SaleState.WAITING_SALE_PRICE
                else:
                    logger.warning("Артикул не найден.")
                    response = "Артикул не найден. Пожалуйста, введите корректный артикул:"
            except Exception as e:
                logger.exception(f"Ошибка при обработке артикула: {self.article}")
                response = "Произошла ошибка. Попробуйте еще раз."
        return response

    def process_waiting_sale_price(self, message, user_data):
        if message.text == 'Отмена':
            self.reset()
            return None
        else:
            try:
                self.sale_price = int(message.text)
            except ValueError:
                return "Пожалуйста, введите корректное числовое значение суммы продажи.", None
            if self.sale_price >= int(self.min_price):
                keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
                button_cash = types.KeyboardButton("Нал")
                button_non_cash = types.KeyboardButton("Безнал")
                button_non_cash_kaspi = types.KeyboardButton("Kaspi")
                button_cancel = types.KeyboardButton("Отмена")
                keyboard.add(button_cash, button_non_cash, button_non_cash_kaspi, button_cancel)
                response = "Выберите тип расчета:"
                self.state = SaleState.WAITING_PAYMENT_TYPE
            else:
                response = f"Сумма продажи не может быть меньше минимальной цены ({self.min_price}). Введите корректную сумму продажи:"
                keyboard = None
            return response, keyboard

    def process_waiting_payment_type(self, message, user_data):
        if message.text == 'Отмена':
            self.reset()
            return None
        else:
            self.payment_type = message.text
            keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
            button_confirm = types.KeyboardButton("Подтвердить")
            button_cancel = types.KeyboardButton("Отменить")
            keyboard.add(button_confirm, button_cancel)
            response = f"Артикул: {self.article}\nСумма продажи: {self.sale_price}\nТип расчета: {self.payment_type}\nПодтвердить или отменить?"
            self.state = SaleState.CONFIRM_SALE
        return response, keyboard
    
    def process_confirm_sale(self, message, user_id):
        user_id = self.user_data.get('id', None)
        store_id = self.user_data.get('store_id')
        if user_id is not None:
            try:
                if message.text == "Подтвердить":
                    record_sale(self.product['id'], store_id, user_id, self.sale_price, self.payment_type)
                    self.reset()
                    response = "Продажа подтверждена."
                else:
                    self.reset()
                    return None
            except Exception as e:
                logger.exception("Ошибка при подтверждении продажи.")
                response = "Произошла ошибка при подтверждении продажи. Попробуйте еще раз."
        else:
            response = "Ошибка: пользователь не авторизован."
        return response

    def process_step(self, message, user_data):
        if self.state == SaleState.WAITING_ARTICLE:
            return self.process_waiting_article(message, user_data)
        elif self.state == SaleState.WAITING_SALE_PRICE:
            return self.process_waiting_sale_price(message, user_data)
        elif self.state == SaleState.WAITING_PAYMENT_TYPE:
            return self.process_waiting_payment_type(message, user_data)
        elif self.state == SaleState.CONFIRM_SALE:
            return self.process_confirm_sale(message, user_data)

    def reset(self):
        self.state = SaleState.WAITING_ARTICLE
        self.article = None
        self.sale_price = None
        self.payment_type = None
        self.product = None
        self.min_price = None

sale_handler = {}

def register_sales_handlers(bot_instance, global_user_data):
    global bot
    bot = bot_instance
    
    @bot.message_handler(func=lambda m: sale_handler.get(m.chat.id) is None and m.text == 'Продажа')
    def start_sale(message):
        chat_id = message.chat.id
        if chat_id in global_user_data:
            sale_handler[chat_id] = Sale(global_user_data[chat_id])
            bot.send_message(chat_id, "Введите артикул товара:")
        else:
            bot.send_message(chat_id, "Вы не авторизованы. Пожалуйста, поделитесь своим номером телефона для авторизации, введя команду /start.")
    
    def process_sale_step(message, chat_id, user_data_dict):
        sale = sale_handler.get(chat_id, None)
        try:
            result = sale.process_step(message, user_data_dict)
            if result is None or (sale.state == SaleState.WAITING_ARTICLE and result == "Продажа подтверждена."):
                sale.reset()
                del sale_handler[chat_id]
                keyboard = get_keyboard_by_role(chat_id)
                bot.send_message(chat_id, "Продажа отменена." if result is None else result, reply_markup=keyboard)
            else:
                if isinstance(result, tuple):
                    response, keyboard = result
                else:
                    response, keyboard = result, get_keyboard_by_role(chat_id)
                bot.send_message(chat_id, response, reply_markup=keyboard)
        except Exception as e:
            logger.exception("Ошибка при обработке шага продажи.")
            bot.send_message(chat_id, "Произошла ошибка при обработке продажи. Попробуйте еще раз.")

    @bot.message_handler(func=lambda message: sale_handler.get(message.chat.id) is not None and sale_handler[message.chat.id].state == SaleState.WAITING_ARTICLE)
    def process_article(message):
        chat_id = message.chat.id
        process_sale_step(message, chat_id, global_user_data.get(chat_id))

    @bot.message_handler(func=lambda message: sale_handler.get(message.chat.id) is not None and sale_handler[message.chat.id].state == SaleState.WAITING_SALE_PRICE)
    def process_sale_price(message):
        chat_id = message.chat.id
        process_sale_step(message, chat_id, global_user_data.get(chat_id))

    @bot.message_handler(func=lambda message: sale_handler.get(message.chat.id) is not None and sale_handler[message.chat.id].state == SaleState.WAITING_PAYMENT_TYPE)
    def process_payment_type(message):
        chat_id = message.chat.id
        process_sale_step(message, chat_id, global_user_data.get(chat_id))

    @bot.message_handler(func=lambda message: sale_handler.get(message.chat.id) is not None and sale_handler[message.chat.id].state == SaleState.CONFIRM_SALE)
    def confirm_sale(message):
        chat_id = message.chat.id
        process_sale_step(message, chat_id, global_user_data.get(chat_id))

def get_keyboard_by_role(chat_id):
    role = user_data[chat_id]['role']
    if role == 'cashier':
        return create_cashier_buttons()
    elif role == 'admin':
        return create_admin_buttons()
    else:
        return None