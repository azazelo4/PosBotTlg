<<<<<<< HEAD
# Create this file: NewVM/handlers/stock_handler.py

=======
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
import logging
from telebot import types
from state import bot, user_data, user_state as global_main_user_state
from database import add_product_to_db, get_stock_report_data, update_product_quantity, get_product_by_article
from buttons import create_cancel_markup, create_stock_management_buttons, create_confirmation_markup
import io
import openpyxl
from datetime import datetime, date # Ensure date is also imported

logger = logging.getLogger(__name__)

stock_operations_handler = {} # Stores StockOperation instances {chat_id: instance}

# Define FSM States for stock operations
STOCK_STATE_IDLE = "STOCK_IDLE" # Should not be set in global_main_user_state

# Add Product States
STOCK_STATE_ADD_PRODUCT_ARTICLE = "STOCK_ADD_PRODUCT_ARTICLE"
STOCK_STATE_ADD_PRODUCT_TYPE = "STOCK_ADD_PRODUCT_TYPE"
STOCK_STATE_ADD_PRODUCT_MODEL = "STOCK_ADD_PRODUCT_MODEL"
STOCK_STATE_ADD_PRODUCT_METAL_TYPE = "STOCK_ADD_PRODUCT_METAL_TYPE"
STOCK_STATE_ADD_PRODUCT_WEIGHT = "STOCK_ADD_PRODUCT_WEIGHT"
STOCK_STATE_ADD_PRODUCT_PRICE_PER_GRAM = "STOCK_ADD_PRODUCT_PRICE_PER_GRAM"
<<<<<<< HEAD
STOCK_STATE_ADD_PRODUCT_PRICE = "STOCK_ADD_PRODUCT_PRICE"
=======
STOCK_STATE_ADD_PRODUCT_PRICE = "STOCK_ADD_PRODUCT_PRICE" # Calculated or input? For now, let's assume input.
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
STOCK_STATE_ADD_PRODUCT_MIN_PRICE = "STOCK_ADD_PRODUCT_MIN_PRICE"
STOCK_STATE_ADD_PRODUCT_QUANTITY = "STOCK_ADD_PRODUCT_QUANTITY"
STOCK_STATE_ADD_PRODUCT_MANUFACTURER = "STOCK_ADD_PRODUCT_MANUFACTURER"
STOCK_STATE_ADD_PRODUCT_COMMENTARIES = "STOCK_ADD_PRODUCT_COMMENTARIES"
<<<<<<< HEAD
=======
# store_id is taken from manager's profile, arrival_date will be NOW()
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
STOCK_STATE_ADD_PRODUCT_CONFIRM = "STOCK_ADD_PRODUCT_CONFIRM"

# Restock States
STOCK_STATE_RESTOCK_ARTICLE = "STOCK_RESTOCK_ARTICLE"
STOCK_STATE_RESTOCK_QUANTITY = "STOCK_RESTOCK_QUANTITY"
STOCK_STATE_RESTOCK_CONFIRM = "STOCK_RESTOCK_CONFIRM"

# Helper to parse float, returns None on error
def parse_float_input(text_input):
    try:
        return float(text_input.replace(',', '.'))
    except ValueError:
        return None

# Helper to parse int, returns None on error
def parse_int_input(text_input):
    try:
        return int(text_input)
    except ValueError:
        return None

class StockOperation:
    def __init__(self, chat_id, global_user_state_ref, user_db_data_ref):
        self.chat_id = chat_id
        self.global_user_state = global_user_state_ref
<<<<<<< HEAD
        self.user_db_data = user_db_data_ref # This is user_data[chat_id]
        self.current_internal_state = STOCK_STATE_IDLE
        self.product_data_buffer = {}
        self.restock_data = {}
        logger.debug(f"Chat {self.chat_id}: StockOperation instance created. User DB data from main: {self.user_db_data}")
=======
        self.user_db_data = user_db_data_ref
        self.current_internal_state = STOCK_STATE_IDLE # Internal FSM state for this operation instance
        self.product_data_buffer = {}
        self.restock_data = {}
        logger.debug(f"Chat {self.chat_id}: StockOperation instance created. User DB data: {self.user_db_data}")
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)


    def _set_global_state(self, state_value):
        self.current_internal_state = state_value
        self.global_user_state[self.chat_id] = state_value
        logger.debug(f"Chat {self.chat_id}: StockOperation state changed to {state_value}")

    def reset_and_remove(self, message_text=None, reply_markup=None):
        logger.info(f"Chat {self.chat_id}: Resetting StockOperation instance and states. Message: {message_text}")
        self.current_internal_state = STOCK_STATE_IDLE
        self.product_data_buffer.clear()
        self.restock_data.clear()
        
<<<<<<< HEAD
=======
        # Clear global state if it was set by this handler
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        current_global_state = self.global_user_state.get(self.chat_id, "")
        if current_global_state.startswith("STOCK_"):
            self.global_user_state.pop(self.chat_id, None)
            logger.debug(f"Chat {self.chat_id}: Cleared global state '{current_global_state}'.")
        
        if self.chat_id in stock_operations_handler:
            del stock_operations_handler[self.chat_id]
            logger.debug(f"Chat {self.chat_id}: Removed StockOperation instance from handler dict.")

        if message_text:
            final_markup = reply_markup if reply_markup else create_stock_management_buttons()
            bot.send_message(self.chat_id, message_text, reply_markup=final_markup)

    # --- Add Product Flow ---
    def start_add_product(self):
        logger.info(f"Chat {self.chat_id}: Starting 'add product' flow.")
<<<<<<< HEAD
        self.product_data_buffer = {} 
=======
        self.product_data_buffer = {} # Clear previous buffer
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        
        store_id = self.user_db_data.get('store_id')
        if not store_id:
            logger.error(f"Chat {self.chat_id}: User {self.user_db_data.get('id')} has no store_id. Cannot add product.")
            self.reset_and_remove("Ошибка: не определен магазин для добавления товара. Операция отменена.")
            return
        self.product_data_buffer['store_id'] = store_id
        
        self._set_global_state(STOCK_STATE_ADD_PRODUCT_ARTICLE)
        bot.send_message(self.chat_id, "Шаг 1/11: Введите Артикул нового товара:", reply_markup=create_cancel_markup())

    def handle_add_product_message(self, message: types.Message):
        text = message.text.strip()
        
        if text == "Отмена":
            self.reset_and_remove("Добавление товара отменено."); return

        state_actions = {
            STOCK_STATE_ADD_PRODUCT_ARTICLE: self._process_add_article,
            STOCK_STATE_ADD_PRODUCT_TYPE: self._process_add_product_type,
            STOCK_STATE_ADD_PRODUCT_MODEL: self._process_add_model,
            STOCK_STATE_ADD_PRODUCT_METAL_TYPE: self._process_add_metal_type,
            STOCK_STATE_ADD_PRODUCT_WEIGHT: self._process_add_weight,
            STOCK_STATE_ADD_PRODUCT_PRICE_PER_GRAM: self._process_add_price_per_gram,
            STOCK_STATE_ADD_PRODUCT_PRICE: self._process_add_price,
            STOCK_STATE_ADD_PRODUCT_MIN_PRICE: self._process_add_min_price,
            STOCK_STATE_ADD_PRODUCT_QUANTITY: self._process_add_quantity,
            STOCK_STATE_ADD_PRODUCT_MANUFACTURER: self._process_add_manufacturer,
            STOCK_STATE_ADD_PRODUCT_COMMENTARIES: self._process_add_commentaries,
            STOCK_STATE_ADD_PRODUCT_CONFIRM: self._process_add_confirm
        }

        action = state_actions.get(self.current_internal_state)
        if action:
            action(text)
        else:
            logger.warning(f"Chat {self.chat_id}: Unknown state in handle_add_product_message: {self.current_internal_state}")
            self.reset_and_remove("Произошла ошибка в процессе добавления товара. Операция отменена.")

    def _prompt_next_add_step(self, next_state, prompt_message):
        self._set_global_state(next_state)
        bot.send_message(self.chat_id, prompt_message, reply_markup=create_cancel_markup())

    def _process_add_article(self, text):
        if not text: bot.send_message(self.chat_id, "Артикул не может быть пустым.",reply_markup=create_cancel_markup()); return
<<<<<<< HEAD
        existing_product = get_product_by_article(text)
        if existing_product:
            bot.send_message(self.chat_id, f"Товар с артикулом {text} ({existing_product.get('model', '')}) уже существует. Введите другой или 'Отмена'.", reply_markup=create_cancel_markup())
            return
=======
        # Check if article exists (optional, add_product_to_db handles it but early check is user-friendly)
        # existing_product = get_product_by_article(text)
        # if existing_product:
        #     bot.send_message(self.chat_id, f"Товар с артикулом {text} уже существует. Введите другой или 'Отмена'.", reply_markup=create_cancel_markup())
        #     return
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        self.product_data_buffer['article_number'] = text
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_TYPE, "Шаг 2/11: Тип товара (напр., кольцо, цепь):")
    
    def _process_add_product_type(self, text):
        if not text: bot.send_message(self.chat_id, "Тип товара не может быть пустым.",reply_markup=create_cancel_markup()); return
        self.product_data_buffer['product_type'] = text
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_MODEL, "Шаг 3/11: Модель/Название товара:")

    def _process_add_model(self, text):
        if not text: bot.send_message(self.chat_id, "Модель не может быть пустой.",reply_markup=create_cancel_markup()); return
        self.product_data_buffer['model'] = text
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_METAL_TYPE, "Шаг 4/11: Тип металла (напр., Золото 585, Серебро 925):")

    def _process_add_metal_type(self, text):
        if not text: bot.send_message(self.chat_id, "Тип металла не может быть пустым.",reply_markup=create_cancel_markup()); return
        self.product_data_buffer['metal_type'] = text
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_WEIGHT, "Шаг 5/11: Вес (в граммах, например, 2.53):")

    def _process_add_weight(self, text):
        weight = parse_float_input(text)
        if weight is None or weight <= 0: bot.send_message(self.chat_id, "Вес должен быть положительным числом.",reply_markup=create_cancel_markup()); return
        self.product_data_buffer['weight'] = weight
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_PRICE_PER_GRAM, "Шаг 6/11: Цена за грамм (если применимо, иначе 0):")

    def _process_add_price_per_gram(self, text):
        ppg = parse_float_input(text)
        if ppg is None or ppg < 0: bot.send_message(self.chat_id, "Цена за грамм должна быть неотрицательным числом.",reply_markup=create_cancel_markup()); return
        self.product_data_buffer['price_per_gram'] = ppg
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_PRICE, "Шаг 7/11: Цена продажи товара (полная):")

    def _process_add_price(self, text):
        price = parse_float_input(text)
        if price is None or price <= 0: bot.send_message(self.chat_id, "Цена продажи должна быть положительным числом.",reply_markup=create_cancel_markup()); return
        self.product_data_buffer['price'] = price
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_MIN_PRICE, "Шаг 8/11: Минимальная цена продажи:")

    def _process_add_min_price(self, text):
        min_price = parse_float_input(text)
<<<<<<< HEAD
=======
        # Min price can be <= price, but not negative.
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        if min_price is None or min_price < 0: bot.send_message(self.chat_id, "Мин. цена должна быть неотрицательным числом.",reply_markup=create_cancel_markup()); return
        if min_price > self.product_data_buffer.get('price', float('inf')):
             bot.send_message(self.chat_id, "Мин. цена не может быть больше цены продажи.",reply_markup=create_cancel_markup()); return
        self.product_data_buffer['min_price'] = min_price
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_QUANTITY, "Шаг 9/11: Начальное количество на складе:")

    def _process_add_quantity(self, text):
        quantity = parse_int_input(text)
        if quantity is None or quantity < 0: bot.send_message(self.chat_id, "Количество должно быть целым неотрицательным числом.",reply_markup=create_cancel_markup()); return
        self.product_data_buffer['quantity'] = quantity
<<<<<<< HEAD
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_MANUFACTURER, "Шаг 10/11: Производитель (если есть, иначе введите '-' ):")

    def _process_add_manufacturer(self, text):
        self.product_data_buffer['manufacturer'] = text if text != '-' else None # Store None if '-'
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_COMMENTARIES, "Шаг 11/11: Комментарии (если есть, иначе введите '-' ):")

    def _process_add_commentaries(self, text):
        self.product_data_buffer['commentaries'] = text if text != '-' else None # Store None if '-'
        self._set_global_state(STOCK_STATE_ADD_PRODUCT_CONFIRM)
        confirm_text = "Проверьте данные нового товара:\n"
        # Ensure all required fields for DB are present, even if None
        expected_keys = ['store_id', 'article_number', 'metal_type', 'product_type', 'model', 
                           'weight', 'price_per_gram', 'price', 'min_price', 'quantity', 
                           'manufacturer', 'commentaries']
        for key in expected_keys:
            val = self.product_data_buffer.get(key)
            display_key = key.replace('_', ' ').capitalize()
            if key == 'store_id': display_key = 'ID Магазина'
            confirm_text += f"{display_key}: {val if val is not None else 'N/A'}\n"
=======
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_MANUFACTURER, "Шаг 10/11: Производитель (если есть, иначе пропустить '-' ):")

    def _process_add_manufacturer(self, text):
        self.product_data_buffer['manufacturer'] = text if text != '-' else ''
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_COMMENTARIES, "Шаг 11/11: Комментарии (если есть, иначе пропустить '-' ):")

    def _process_add_commentaries(self, text):
        self.product_data_buffer['commentaries'] = text if text != '-' else ''
        # All fields collected, move to confirmation
        self._set_global_state(STOCK_STATE_ADD_PRODUCT_CONFIRM)
        confirm_text = "Проверьте данные нового товара:\n"
        for key, val in self.product_data_buffer.items():
            # Simple pretty print for keys
            display_key = key.replace('_', ' ').capitalize()
            if key == 'store_id': display_key = 'ID Магазина'
            confirm_text += f"{display_key}: {val}\n"
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        confirm_text += "\nДобавить этот товар на склад?"
        bot.send_message(self.chat_id, confirm_text, reply_markup=create_confirmation_markup())

    def _process_add_confirm(self, text):
        if text == "Подтвердить":
<<<<<<< HEAD
            # arrival_date is handled by DB function using NOW() if not passed
            success, db_msg = add_product_to_db(self.product_data_buffer)
            # No message_text here, db_msg is sent, then reset sends final menu
            self.reset_and_remove(message_text=db_msg, reply_markup=create_stock_management_buttons())
=======
            # arrival_date will be set to NOW() by the database query if not provided
            # self.product_data_buffer['arrival_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S') # Or let DB handle
            success, db_msg = add_product_to_db(self.product_data_buffer)
            bot.send_message(self.chat_id, db_msg)
            self.reset_and_remove(reply_markup=create_stock_management_buttons())
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        elif text == "Отмена":
            self.reset_and_remove("Добавление товара отменено.")
        else:
            bot.send_message(self.chat_id, "Используйте кнопки 'Подтвердить' или 'Отмена'.", reply_markup=create_confirmation_markup())


    # --- Restock Flow ---
    def start_restock_product(self):
        logger.info(f"Chat {self.chat_id}: Starting 'restock product' flow.")
        self.restock_data = {}
        self._set_global_state(STOCK_STATE_RESTOCK_ARTICLE)
        bot.send_message(self.chat_id, "Введите Артикул товара для пополнения остатков:", reply_markup=create_cancel_markup())

    def handle_restock_message(self, message: types.Message):
        text = message.text.strip()
        
        if text == "Отмена":
            self.reset_and_remove("Пополнение остатков отменено."); return

        if self.current_internal_state == STOCK_STATE_RESTOCK_ARTICLE:
            product = get_product_by_article(text)
            if not product:
                bot.send_message(self.chat_id, f"Товар с артикулом {text} не найден. Попробуйте снова или 'Отмена'.", reply_markup=create_cancel_markup())
                return
<<<<<<< HEAD
            
            manager_store_id = self.user_db_data.get('store_id')
            if self.user_db_data.get('role') == 'manager' and product.get('store_id') != manager_store_id:
                bot.send_message(self.chat_id, f"Товар {text} ({product.get('model','')}) не принадлежит вашему магазину ({manager_store_id}). Операция невозможна.", reply_markup=create_cancel_markup())
                # Stay in this state or reset? For now, stay, user can try another article or cancel.
=======
            # Check if product belongs to manager's store if role is manager
            manager_store_id = self.user_db_data.get('store_id')
            if self.user_db_data.get('role') == 'manager' and product.get('store_id') != manager_store_id:
                bot.send_message(self.chat_id, f"Товар {text} не принадлежит вашему магазину. Операция невозможна.", reply_markup=create_cancel_markup())
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
                return

            self.restock_data['article_number'] = text
            self.restock_data['product_name'] = product.get('model', text)
            self.restock_data['current_quantity'] = product.get('quantity', 0)
            self._set_global_state(STOCK_STATE_RESTOCK_QUANTITY)
            bot.send_message(self.chat_id, f"Товар: {self.restock_data['product_name']} (Арт: {text}, тек. остаток: {self.restock_data['current_quantity']}).\nВведите количество для пополнения (целое положит. число):", reply_markup=create_cancel_markup())

        elif self.current_internal_state == STOCK_STATE_RESTOCK_QUANTITY:
            qty_to_add = parse_int_input(text)
            if qty_to_add is None or qty_to_add <= 0:
                bot.send_message(self.chat_id, "Количество должно быть целым положительным числом.", reply_markup=create_cancel_markup()); return
            self.restock_data['quantity_to_add'] = qty_to_add
            self._set_global_state(STOCK_STATE_RESTOCK_CONFIRM)
<<<<<<< HEAD
            new_total = self.restock_data.get('current_quantity', 0) + qty_to_add
            confirm_text = f"Пополнить остаток {self.restock_data['product_name']} (Арт: {self.restock_data['article_number']}) на {qty_to_add} шт.?\nНовый остаток будет: {new_total}"
=======
            confirm_text = f"Пополнить остаток {self.restock_data['product_name']} (Арт: {self.restock_data['article_number']}) на {qty_to_add} шт.?\nНовый остаток будет: {self.restock_data['current_quantity'] + qty_to_add}"
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
            bot.send_message(self.chat_id, confirm_text, reply_markup=create_confirmation_markup())
        
        elif self.current_internal_state == STOCK_STATE_RESTOCK_CONFIRM:
            if text == "Подтвердить":
                article = self.restock_data['article_number']
                qty = self.restock_data['quantity_to_add']
<<<<<<< HEAD
                store_id_context = self.user_db_data.get('store_id') 
                success, db_msg = update_product_quantity(article, qty, store_id_context)
                self.reset_and_remove(message_text=db_msg, reply_markup=create_stock_management_buttons())
=======
                store_id_context = self.user_db_data.get('store_id') # For logging/context in update_product_quantity
                success, db_msg = update_product_quantity(article, qty, store_id_context) # Pass store_id for context
                bot.send_message(self.chat_id, db_msg) # db_msg now comes from update_product_quantity
                self.reset_and_remove(reply_markup=create_stock_management_buttons())
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
            elif text == "Отмена":
                self.reset_and_remove("Пополнение остатков отменено.")
            else:
                bot.send_message(self.chat_id, "Используйте кнопки 'Подтвердить' или 'Отмена'.", reply_markup=create_confirmation_markup())


<<<<<<< HEAD
    # --- Stock Report Flow ---
=======
    # --- Stock Report Flow (Direct action, no FSM needed here for simple version) ---
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
    def generate_stock_report(self):
        logger.info(f"Chat {self.chat_id}: Generating stock report.")
        store_id_filter = None
        user_role = self.user_db_data.get('role')
        
        if user_role == 'manager':
            store_id_filter = self.user_db_data.get('store_id')
            if not store_id_filter:
                logger.warning(f"Chat {self.chat_id}: Manager {self.user_db_data.get('id')} has no store_id. Cannot filter stock report.")
<<<<<<< HEAD
                self.reset_and_remove(message_text="Ошибка: не определен ваш магазин для отчета.", reply_markup=create_stock_management_buttons())
                return
=======
                bot.send_message(self.chat_id, "Ошибка: не определен ваш магазин для отчета.", reply_markup=create_stock_management_buttons())
                self.reset_and_remove()
                return
        # Admin sees all stores (store_id_filter remains None)
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        
        bot.send_message(self.chat_id, f"Генерирую отчет об остатках {'для вашего магазина' if store_id_filter else 'по всем магазинам'}...", reply_markup=types.ReplyKeyboardRemove())
        try:
            stock_items = get_stock_report_data(store_id_filter)
            if not stock_items:
<<<<<<< HEAD
                self.reset_and_remove(message_text="Нет данных для отчета об остатках.", reply_markup=create_stock_management_buttons())
=======
                bot.send_message(self.chat_id, "Нет данных для отчета об остатках.", reply_markup=create_stock_management_buttons())
                self.reset_and_remove()
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
                return

            wb = openpyxl.Workbook()
            sheet = wb.active
            headers = [
<<<<<<< HEAD
                'Артикул', 'Тип товара', 'Модель', 'Тип металла', 'Вес (г)', 
                'Остаток (шт)', 'Цена (прод.)', 'Мин. Цена (прод.)', 'Производитель', 'Магазин',
                'Дата поступл.', 'Комментарии'
            ]
            sheet.append(headers)
            for item in stock_items:
                arrival_date_str = item.get('arrival_date').strftime('%Y-%m-%d %H:%M:%S') if isinstance(item.get('arrival_date'), (date, datetime)) else str(item.get('arrival_date', ''))

                row_data = [
                    item.get('article_number', ''), item.get('product_type', ''), item.get('model', ''),
                    item.get('metal_type', ''), 
                    item.get('weight'), # Already float/decimal from DB
                    item.get('quantity'), # Already int
                    item.get('price'), # Already float/decimal
                    item.get('min_price'), # Already float/decimal
=======
                'Артикул', 'Тип товара', 'Модель', 'Тип металла', 'Вес', 
                'Остаток', 'Цена', 'Мин. Цена', 'Производитель', 'Магазин',
                'Дата поступления', 'Комментарии'
            ]
            sheet.append(headers)
            for item in stock_items:
                arrival_date_str = item.get('arrival_date').strftime('%Y-%m-%d') if isinstance(item.get('arrival_date'), (date, datetime)) else ''
                row_data = [
                    item.get('article_number', ''), item.get('product_type', ''), item.get('model', ''),
                    item.get('metal_type', ''), 
                    float(item.get('weight', 0)) if item.get('weight') is not None else '', 
                    int(item.get('quantity', 0)) if item.get('quantity') is not None else '',
                    float(item.get('price', 0)) if item.get('price') is not None else '', 
                    float(item.get('min_price', 0)) if item.get('min_price') is not None else '', 
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
                    item.get('manufacturer', ''), item.get('store_name', ''), 
                    arrival_date_str,
                    item.get('commentaries', '')
                ]
<<<<<<< HEAD
                # Convert None to empty string for Excel
                row_data = ["" if v is None else v for v in row_data]
=======
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
                sheet.append(row_data)
            
            report_buffer = io.BytesIO()
            wb.save(report_buffer)
            report_buffer.seek(0)
<<<<<<< HEAD
            filename = f"stock_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            bot.send_document(self.chat_id, report_buffer, visible_file_name=filename, caption="Отчет об остатках готов.")
            # Message and buttons are sent by reset_and_remove after this.
=======
            filename = f"stock_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
            bot.send_document(self.chat_id, report_buffer, visible_file_name=filename, caption="Отчет об остатках готов.")
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        except Exception as e:
            logger.exception(f"Chat {self.chat_id}: Error generating stock report: {e}")
            bot.send_message(self.chat_id, "Ошибка при создании отчета об остатках.")
        finally:
<<<<<<< HEAD
            self.reset_and_remove(reply_markup=create_stock_management_buttons()) # Ensure menu returns


def register_stock_handlers(bot_instance_main, global_user_data_ref, global_user_state_ref):
=======
            # Send stock menu buttons after report or error
            self.reset_and_remove(reply_markup=create_stock_management_buttons())


def register_stock_handlers(bot_instance_main, global_user_data_ref, global_user_state_ref):
    # bot_instance_main is already the global bot from state.py
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
    
    @bot.message_handler(func=lambda msg: msg.text == "Добавить товар" and \
                         global_user_data_ref.get(msg.chat.id,{}).get('role') in ['admin','manager'] and \
                         global_user_data_ref.get(msg.chat.id,{}).get('is_active'))
    def initiate_add_product(message: types.Message):
        chat_id = message.chat.id
<<<<<<< HEAD
        logger.info(f"Chat {chat_id}: 'Добавить товар' selected by {global_user_data_ref.get(chat_id,{}).get('role')}.")
=======
        logger.info(f"Chat {chat_id}: 'Добавить товар' selected.")
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        if chat_id in stock_operations_handler: stock_operations_handler[chat_id].reset_and_remove()
        stock_operations_handler[chat_id] = StockOperation(chat_id, global_user_state_ref, global_user_data_ref[chat_id])
        stock_operations_handler[chat_id].start_add_product()

    @bot.message_handler(func=lambda msg: msg.text == "Отчет об остатках" and \
                         global_user_data_ref.get(msg.chat.id,{}).get('role') in ['admin','manager'] and \
                         global_user_data_ref.get(msg.chat.id,{}).get('is_active'))
    def initiate_stock_report(message: types.Message):
        chat_id = message.chat.id
<<<<<<< HEAD
        logger.info(f"Chat {chat_id}: 'Отчет об остатках' selected by {global_user_data_ref.get(chat_id,{}).get('role')}.")
        if chat_id in stock_operations_handler : stock_operations_handler[chat_id].reset_and_remove()
        
        # global_main_user_state.pop(chat_id, None) # Clear any FSM state before direct action
        # For direct actions like reports, ensure no FSM state is lingering for this user for stock ops
        current_global_state = global_user_state_ref.get(chat_id, "")
        if current_global_state.startswith("STOCK_"):
            global_user_state_ref.pop(chat_id, None)

        stock_instance = StockOperation(chat_id, global_user_state_ref, global_user_data_ref[chat_id])
        stock_instance.generate_stock_report() # This method now calls reset_and_remove internally
=======
        logger.info(f"Chat {chat_id}: 'Отчет об остатках' selected.")
        if chat_id in stock_operations_handler : stock_operations_handler[chat_id].reset_and_remove() # Reset any previous op
        # Create a temporary instance just for this action, it will self-destruct
        stock_instance = StockOperation(chat_id, global_user_state_ref, global_user_data_ref[chat_id])
        global_main_user_state.pop(chat_id, None) # Ensure no FSM state is active
        stock_instance.generate_stock_report()
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)

    @bot.message_handler(func=lambda msg: msg.text == "Пополнить остатки" and \
                         global_user_data_ref.get(msg.chat.id,{}).get('role') in ['admin','manager'] and \
                         global_user_data_ref.get(msg.chat.id,{}).get('is_active'))
    def initiate_restock_product(message: types.Message):
        chat_id = message.chat.id
<<<<<<< HEAD
        logger.info(f"Chat {chat_id}: 'Пополнить остатки' selected by {global_user_data_ref.get(chat_id,{}).get('role')}.")
=======
        logger.info(f"Chat {chat_id}: 'Пополнить остатки' selected.")
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        if chat_id in stock_operations_handler: stock_operations_handler[chat_id].reset_and_remove()
        stock_operations_handler[chat_id] = StockOperation(chat_id, global_user_state_ref, global_user_data_ref[chat_id])
        stock_operations_handler[chat_id].start_restock_product()

    @bot.message_handler(func=lambda msg: global_user_state_ref.get(msg.chat.id, "").startswith("STOCK_"))
    def handle_stock_fsm_messages(message: types.Message):
        chat_id = message.chat.id
        if chat_id in stock_operations_handler:
            instance = stock_operations_handler[chat_id]
<<<<<<< HEAD
            # Integrity check: global state must match instance's expectation
            if global_user_state_ref.get(chat_id) == instance.current_internal_state:
                if instance.current_internal_state.startswith("STOCK_ADD_"):
                    instance.handle_add_product_message(message)
                elif instance.current_internal_state.startswith("STOCK_RESTOCK_"):
                    instance.handle_restock_message(message)
                else:
                    logger.warning(f"Chat {chat_id}: STOCK_ state {global_user_state_ref.get(chat_id)} but internal state {instance.current_internal_state} not matching ADD or RESTOCK prefix.")
                    instance.reset_and_remove("Произошла внутренняя ошибка состояния (1). Операция отменена.")
            else:
                logger.error(f"Chat {chat_id}: State Mismatch! Global: {global_user_state_ref.get(chat_id)}, Instance: {instance.current_internal_state}. Resetting.")
                instance.reset_and_remove("Произошла внутренняя ошибка состояния (2). Операция отменена.")
=======
            # Route based on the instance's internal state, which should match global state prefix
            if instance.current_internal_state.startswith("STOCK_ADD_"):
                instance.handle_add_product_message(message)
            elif instance.current_internal_state.startswith("STOCK_RESTOCK_"):
                instance.handle_restock_message(message)
            else:
                logger.warning(f"Chat {chat_id}: STOCK_ state {global_user_state_ref.get(chat_id)} but internal state {instance.current_internal_state} not matching ADD or RESTOCK prefix.")
                instance.reset_and_remove("Произошла внутренняя ошибка состояния. Операция отменена.")
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
        else: 
            logger.warning(f"Chat {chat_id}: Message for STOCK_ state but no handler instance. State: {global_user_state_ref.get(chat_id)}. Resetting state.")
            global_user_state_ref.pop(chat_id, None)
            bot.send_message(chat_id, "Произошла ошибка. Пожалуйста, начните операцию со складом заново.", reply_markup=create_stock_management_buttons())

    logger.info("Stock handlers registered.")