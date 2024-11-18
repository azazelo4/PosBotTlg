from enum import Enum
from telebot import types
from database import get_product_by_article, record_sale
import logging
import re

logger = logging.getLogger(__name__)

def parse_price(price_str):
    # Удаляем все символы, кроме цифр, точек и запятых
    cleaned_price = re.sub(r"[^\d.,]", "", price_str)

    # Если запятая используется как десятичный разделитель
    if ',' in cleaned_price and '.' in cleaned_price:
        # Если есть и `,` и `.`, предполагаем, что `,` — десятичный разделитель
        cleaned_price = cleaned_price.replace('.', '').replace(',', '.')
    elif cleaned_price.count(',') == 1 and cleaned_price.count('.') == 0:
        # Если есть только одна запятая, заменяем её на точку
        cleaned_price = cleaned_price.replace(',', '.')
    else:
        # Удаляем все точки как разделители тысяч
        cleaned_price = cleaned_price.replace('.', '')

    try:
        return float(cleaned_price)
    except ValueError:
        return None  # Возвращаем None, если не удалось преобразовать

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
                # Пытаемся получить продукт по артикулу
                self.product = get_product_by_article(self.article)
                if self.product:
                    # Проверка на наличие минимальной цены
                    self.min_price = parse_price(self.product['min_price'])
                    logger.info(f"Товар найден: {self.product['product_type']}, минимальная цена: {self.min_price}")

                    if self.min_price is None:
                        logger.error("Ошибка: минимальная цена не может быть обработана.")
                        return "Ошибка данных: некорректное значение минимальной цены."
                    
                    # Возвращаем успешное сообщение
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
                # Логируем исключение с подробной информацией
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
            if message.text == "Подтвердить":
                #store_id = self.user_data.get('store_id')
                record_sale(self.product['id'], store_id, user_id, self.sale_price, self.payment_type)
                self.reset()
                response = "Продажа подтверждена."
            else:
                self.reset()
                return None
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
