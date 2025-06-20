# Create this file: NewVM/handlers/stock_handler.py

import logging
from telebot import types
from state import bot, user_data, user_state as global_main_user_state # type: ignore
from database import add_product_to_db, get_stock_report_data, update_product_quantity, get_product_by_article, get_enum_or_distinct_values
from buttons import create_cancel_markup, create_stock_management_buttons, create_confirmation_markup
import io
import openpyxl
from datetime import datetime, date # Ensure date is also imported

logger = logging.getLogger(__name__)

stock_operations_handler = {} # Stores StockOperation instances {chat_id: instance}

# Define FSM States for stock operations
STOCK_STATE_IDLE = "STOCK_IDLE" # Should not be set in global_main_user_state

# Add Product States
STOCK_STATE_AWAITING_PRODUCT_TYPE_CHOICE = "STOCK_AWAITING_PRODUCT_TYPE_CHOICE"
STOCK_STATE_AWAITING_METAL_TYPE_CHOICE = "STOCK_AWAITING_METAL_TYPE_CHOICE"
STOCK_STATE_ADD_PRODUCT_ARTICLE = "STOCK_ADD_PRODUCT_ARTICLE"
STOCK_STATE_ADD_PRODUCT_TYPE = "STOCK_ADD_PRODUCT_TYPE"
STOCK_STATE_ADD_PRODUCT_MODEL = "STOCK_ADD_PRODUCT_MODEL"
STOCK_STATE_ADD_PRODUCT_METAL_TYPE = "STOCK_ADD_PRODUCT_METAL_TYPE"
STOCK_STATE_ADD_PRODUCT_WEIGHT = "STOCK_ADD_PRODUCT_WEIGHT"
STOCK_STATE_ADD_PRODUCT_PRICE_PER_GRAM = "STOCK_ADD_PRODUCT_PRICE_PER_GRAM"
STOCK_STATE_ADD_PRODUCT_PRICE = "STOCK_ADD_PRODUCT_PRICE"
STOCK_STATE_ADD_PRODUCT_MIN_PRICE = "STOCK_ADD_PRODUCT_MIN_PRICE"
STOCK_STATE_ADD_PRODUCT_QUANTITY = "STOCK_ADD_PRODUCT_QUANTITY"
STOCK_STATE_ADD_PRODUCT_MANUFACTURER = "STOCK_ADD_PRODUCT_MANUFACTURER"
STOCK_STATE_ADD_PRODUCT_COMMENTARIES = "STOCK_ADD_PRODUCT_COMMENTARIES"
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
        self.user_db_data = user_db_data_ref # This is user_data[chat_id]
        self.current_internal_state = STOCK_STATE_IDLE
        self.product_data_buffer = {}
        self.restock_data = {}
        logger.debug(f"Chat {self.chat_id}: StockOperation instance created. User DB data from main: {self.user_db_data}")


    def _set_global_state(self, state_value):
        self.current_internal_state = state_value
        self.global_user_state[self.chat_id] = state_value
        logger.debug(f"Chat {self.chat_id}: StockOperation state changed to {state_value}")

    def reset_and_remove(self, message_text=None, reply_markup=None):
        logger.info(f"Chat {self.chat_id}: Resetting StockOperation instance and states. Message: {message_text}")
        self.current_internal_state = STOCK_STATE_IDLE
        self.product_data_buffer.clear()
        self.restock_data.clear()
        
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
        self.product_data_buffer = {} 
        
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
            # STOCK_STATE_ADD_PRODUCT_TYPE is now handled by _prompt_for_product_type and its callback
            STOCK_STATE_ADD_PRODUCT_MODEL: self._process_add_model,
            # STOCK_STATE_ADD_PRODUCT_METAL_TYPE is now handled by _prompt_for_metal_type and its callback
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
        existing_product = get_product_by_article(text)
        if existing_product:
            bot.send_message(self.chat_id, f"Товар с артикулом {text} ({existing_product.get('model', '')}) уже существует. Введите другой или 'Отмена'.", reply_markup=create_cancel_markup())
            return
        self.product_data_buffer['article_number'] = text
        self._prompt_for_product_type()
    
    def _prompt_for_product_type(self):
        self._set_global_state(STOCK_STATE_AWAITING_PRODUCT_TYPE_CHOICE)
        
        product_types_from_db = get_enum_or_distinct_values('products', 'product_type', add_other_option=True)
        # Ensure "Другое" is always an option and avoid duplicates if it's already in DB
        choices = product_types_from_db # get_enum_or_distinct_values already handles "Другое" and sorting

        if not choices: # Should not happen if "Другое" is always added, but as a safeguard
            bot.send_message(self.chat_id, "Не удалось загрузить типы товаров. Попробуйте позже или отмените операцию.", reply_markup=create_cancel_markup())
            return
            
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(text=ptype, callback_data=f"stock_add_prodtype_{ptype}") for ptype in choices]
        keyboard.add(*buttons)
        # Add a cancel inline button as well, or rely on the ReplyKeyboard "Отмена"
        # keyboard.add(types.InlineKeyboardButton(text="Отменить ввод", callback_data="stock_add_cancel_inline"))
        bot.send_message(self.chat_id, "Шаг 2/11: Выберите Тип товара:", reply_markup=keyboard)
        # Send cancel reply keyboard as well, in case user wants to use that
        bot.send_message(self.chat_id, "Или нажмите 'Отмена' на клавиатуре ниже, чтобы прервать добавление.", reply_markup=create_cancel_markup())


    def handle_product_type_choice_callback(self, call: types.CallbackQuery):
        chat_id = call.message.chat.id
        product_type = call.data.split("stock_add_prodtype_")[-1]
        
        bot.answer_callback_query(call.id)
        bot.delete_message(chat_id, call.message.message_id) # Remove the inline keyboard message
        # Potentially remove the "Или нажмите 'Отмена'..." message too if we can get its ID or send it differently

        self.product_data_buffer['product_type'] = product_type
        logger.info(f"Chat {chat_id}: Product type selected: {product_type}")
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_MODEL, "Шаг 3/11: Модель/Название товара:")

    def _process_add_model(self, text):
        if not text: bot.send_message(self.chat_id, "Модель не может быть пустой.",reply_markup=create_cancel_markup()); return
        self.product_data_buffer['model'] = text
        self._prompt_for_metal_type()

    def _prompt_for_metal_type(self):
        self._set_global_state(STOCK_STATE_AWAITING_METAL_TYPE_CHOICE)
        
        metal_types_from_db = get_enum_or_distinct_values('products', 'metal_type', add_other_option=True)
        choices = metal_types_from_db # get_enum_or_distinct_values already handles "Другое" and sorting

        if not choices:
            bot.send_message(self.chat_id, "Не удалось загрузить типы металлов. Попробуйте позже или отмените операцию.", reply_markup=create_cancel_markup())
            return

        keyboard = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(text=mtype, callback_data=f"stock_add_metaltype_{mtype}") for mtype in choices]
        keyboard.add(*buttons)
        bot.send_message(self.chat_id, "Шаг 4/11: Выберите Тип металла:", reply_markup=keyboard)
        bot.send_message(self.chat_id, "Или нажмите 'Отмена' на клавиатуре ниже.", reply_markup=create_cancel_markup())

    def handle_metal_type_choice_callback(self, call: types.CallbackQuery):
        bot.answer_callback_query(call.id)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        metal_type = call.data.split("stock_add_metaltype_")[-1]
        self.product_data_buffer['metal_type'] = metal_type
        logger.info(f"Chat {call.message.chat.id}: Metal type selected: {metal_type}")
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
        if min_price is None or min_price < 0: bot.send_message(self.chat_id, "Мин. цена должна быть неотрицательным числом.",reply_markup=create_cancel_markup()); return
        if min_price > self.product_data_buffer.get('price', float('inf')):
             bot.send_message(self.chat_id, "Мин. цена не может быть больше цены продажи.",reply_markup=create_cancel_markup()); return
        self.product_data_buffer['min_price'] = min_price
        self._prompt_next_add_step(STOCK_STATE_ADD_PRODUCT_QUANTITY, "Шаг 9/11: Начальное количество на складе:")

    def _process_add_quantity(self, text):
        quantity = parse_int_input(text)
        if quantity is None or quantity < 0: bot.send_message(self.chat_id, "Количество должно быть целым неотрицательным числом.",reply_markup=create_cancel_markup()); return
        self.product_data_buffer['quantity'] = quantity
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
        confirm_text += "\nДобавить этот товар на склад?"
        bot.send_message(self.chat_id, confirm_text, reply_markup=create_confirmation_markup())

    def _process_add_confirm(self, text):
        if text == "Подтвердить":
            # arrival_date is handled by DB function using NOW() if not passed
            success, db_msg = add_product_to_db(self.product_data_buffer)
            # No message_text here, db_msg is sent, then reset sends final menu
            self.reset_and_remove(message_text=db_msg, reply_markup=create_stock_management_buttons())
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
            
            manager_store_id = self.user_db_data.get('store_id')
            if self.user_db_data.get('role') == 'manager' and product.get('store_id') != manager_store_id:
                bot.send_message(self.chat_id, f"Товар {text} ({product.get('model','')}) не принадлежит вашему магазину ({manager_store_id}). Операция невозможна.", reply_markup=create_cancel_markup())
                # Stay in this state or reset? For now, stay, user can try another article or cancel.
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
            new_total = self.restock_data.get('current_quantity', 0) + qty_to_add
            confirm_text = f"Пополнить остаток {self.restock_data['product_name']} (Арт: {self.restock_data['article_number']}) на {qty_to_add} шт.?\nНовый остаток будет: {new_total}"
            bot.send_message(self.chat_id, confirm_text, reply_markup=create_confirmation_markup())
        
        elif self.current_internal_state == STOCK_STATE_RESTOCK_CONFIRM:
            if text == "Подтвердить":
                article = self.restock_data['article_number']
                qty = self.restock_data['quantity_to_add']
                store_id_context = self.user_db_data.get('store_id') 
                success, db_msg = update_product_quantity(article, qty, store_id_context)
                self.reset_and_remove(message_text=db_msg, reply_markup=create_stock_management_buttons())
            elif text == "Отмена":
                self.reset_and_remove("Пополнение остатков отменено.")
            else:
                bot.send_message(self.chat_id, "Используйте кнопки 'Подтвердить' или 'Отмена'.", reply_markup=create_confirmation_markup())


    # --- Stock Report Flow ---
    def generate_stock_report(self):
        logger.info(f"Chat {self.chat_id}: Generating stock report.")
        store_id_filter = None
        user_role = self.user_db_data.get('role')
        
        if user_role == 'manager':
            store_id_filter = self.user_db_data.get('store_id')
            if not store_id_filter:
                logger.warning(f"Chat {self.chat_id}: Manager {self.user_db_data.get('id')} has no store_id. Cannot filter stock report.")
                self.reset_and_remove(message_text="Ошибка: не определен ваш магазин для отчета.", reply_markup=create_stock_management_buttons())
                return
        
        bot.send_message(self.chat_id, f"Генерирую отчет об остатках {'для вашего магазина' if store_id_filter else 'по всем магазинам'}...", reply_markup=types.ReplyKeyboardRemove())
        try:
            stock_items = get_stock_report_data(store_id_filter)
            if not stock_items:
                self.reset_and_remove(message_text="Нет данных для отчета об остатках.", reply_markup=create_stock_management_buttons())
                return

            wb = openpyxl.Workbook()
            sheet = wb.active
            headers = [
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
                    item.get('manufacturer', ''), item.get('store_name', ''), 
                    arrival_date_str,
                    item.get('commentaries', '')
                ]
                # Convert None to empty string for Excel
                row_data = ["" if v is None else v for v in row_data]
                sheet.append(row_data)
            
            report_buffer = io.BytesIO()
            wb.save(report_buffer)
            report_buffer.seek(0)
            filename = f"stock_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            bot.send_document(self.chat_id, report_buffer, visible_file_name=filename, caption="Отчет об остатках готов.")
            # Message and buttons are sent by reset_and_remove after this.
        except Exception as e:
            logger.exception(f"Chat {self.chat_id}: Error generating stock report: {e}")
            bot.send_message(self.chat_id, "Ошибка при создании отчета об остатках.")
        finally:
            self.reset_and_remove(reply_markup=create_stock_management_buttons()) # Ensure menu returns


def register_stock_handlers(bot_instance_main, global_user_data_ref, global_user_state_ref):
    
    @bot.message_handler(func=lambda msg: msg.text == "Добавить товар" and \
                         global_user_data_ref.get(msg.chat.id,{}).get('role') in ['admin','manager'] and \
                         global_user_data_ref.get(msg.chat.id,{}).get('is_active'))
    def initiate_add_product(message: types.Message):
        chat_id = message.chat.id
        logger.info(f"Chat {chat_id}: 'Добавить товар' selected by {global_user_data_ref.get(chat_id,{}).get('role')}.")
        if chat_id in stock_operations_handler: stock_operations_handler[chat_id].reset_and_remove()
        stock_operations_handler[chat_id] = StockOperation(chat_id, global_user_state_ref, global_user_data_ref[chat_id])
        stock_operations_handler[chat_id].start_add_product()

    @bot.message_handler(func=lambda msg: msg.text == "Отчет об остатках" and \
                         global_user_data_ref.get(msg.chat.id,{}).get('role') in ['admin','manager'] and \
                         global_user_data_ref.get(msg.chat.id,{}).get('is_active'))
    def initiate_stock_report(message: types.Message):
        chat_id = message.chat.id
        logger.info(f"Chat {chat_id}: 'Отчет об остатках' selected by {global_user_data_ref.get(chat_id,{}).get('role')}.")
        if chat_id in stock_operations_handler : stock_operations_handler[chat_id].reset_and_remove()
        
        # global_main_user_state.pop(chat_id, None) # Clear any FSM state before direct action
        # For direct actions like reports, ensure no FSM state is lingering for this user for stock ops
        current_global_state = global_user_state_ref.get(chat_id, "")
        if current_global_state.startswith("STOCK_"):
            global_user_state_ref.pop(chat_id, None)

        stock_instance = StockOperation(chat_id, global_user_state_ref, global_user_data_ref[chat_id])
        stock_instance.generate_stock_report() # This method now calls reset_and_remove internally

    @bot.message_handler(func=lambda msg: msg.text == "Пополнить остатки" and \
                         global_user_data_ref.get(msg.chat.id,{}).get('role') in ['admin','manager'] and \
                         global_user_data_ref.get(msg.chat.id,{}).get('is_active'))
    def initiate_restock_product(message: types.Message):
        chat_id = message.chat.id
        logger.info(f"Chat {chat_id}: 'Пополнить остатки' selected by {global_user_data_ref.get(chat_id,{}).get('role')}.")
        if chat_id in stock_operations_handler: stock_operations_handler[chat_id].reset_and_remove()
        stock_operations_handler[chat_id] = StockOperation(chat_id, global_user_state_ref, global_user_data_ref[chat_id])
        stock_operations_handler[chat_id].start_restock_product()

    @bot.message_handler(func=lambda msg: global_user_state_ref.get(msg.chat.id, "").startswith("STOCK_"))
    def handle_stock_fsm_messages(message: types.Message):
        chat_id = message.chat.id
        if chat_id in stock_operations_handler:
            instance = stock_operations_handler[chat_id]
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
        else: 
            logger.warning(f"Chat {chat_id}: Message for STOCK_ state but no handler instance. State: {global_user_state_ref.get(chat_id)}. Resetting state.")
            global_user_state_ref.pop(chat_id, None)
            bot.send_message(chat_id, "Произошла ошибка. Пожалуйста, начните операцию со складом заново.", reply_markup=create_stock_management_buttons())

    @bot.callback_query_handler(func=lambda call: call.data.startswith("stock_add_prodtype_"))
    def cb_product_type_select(call: types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id in stock_operations_handler and \
           stock_operations_handler[chat_id].current_internal_state == STOCK_STATE_AWAITING_PRODUCT_TYPE_CHOICE:
            stock_operations_handler[chat_id].handle_product_type_choice_callback(call)
        else:
            bot.answer_callback_query(call.id, "Истек срок действия выбора или операция отменена.")
            bot.delete_message(chat_id, call.message.message_id) # Clean up the old keyboard

    @bot.callback_query_handler(func=lambda call: call.data.startswith("stock_add_metaltype_"))
    def cb_metal_type_select(call: types.CallbackQuery):
        chat_id = call.message.chat.id
        if chat_id in stock_operations_handler and \
           stock_operations_handler[chat_id].current_internal_state == STOCK_STATE_AWAITING_METAL_TYPE_CHOICE:
            stock_operations_handler[chat_id].handle_metal_type_choice_callback(call)
        else:
            bot.answer_callback_query(call.id, "Истек срок действия выбора или операция отменена.")
            bot.delete_message(chat_id, call.message.message_id) # Clean up

    logger.info("Stock handlers registered.")