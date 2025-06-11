# --- START OF FILE database.py ---

import mysql.connector
from config import DB_HOST, DB_USER, DB_PASS, DB_NAME
from mysql.connector import Error
import logging
import re # Для нормализации номера

logger = logging.getLogger(__name__)

# Поместим функцию нормализации сюда для простоты, или импортируем из user_management
# Если импортировать: from user_management import normalize_phone_number
# Но для самодостаточности модуля database.py, лучше иметь свою или скопировать.
# ВАЖНО: Убедитесь, что эта функция идентична той, что в user_management.py или импортируется оттуда.
def _normalize_phone_for_db(phone_str_raw: str) -> str | None:
    """
    Нормализует номер телефона к формату +7XXXXXXXXXXX (11 цифр после +7).
    Агрессивно очищает все нецифровые символы.
    """
    if not phone_str_raw: 
        logger.debug("DB Normalizer: Получена пустая строка для нормализации.")
        return None

    # Шаг 1: Удаляем ВСЕ, что не является цифрой
    digits_only = re.sub(r'\D', '', phone_str_raw)
    logger.debug(f"DB Normalizer: phone_str_raw='{phone_str_raw}', digits_only='{digits_only}'")

    if not digits_only:
        logger.debug(f"DB Normalizer: После очистки в '{phone_str_raw}' не осталось цифр.")
        return None

    # Шаг 2: Приводим к формату +7XXXXXXXXXXX (11 цифр после +7)
    # Это соответствует вашему формату в БД '+77775295987'
    
    # Если номер начинается с '8' и имеет 12 цифр (8 + 11 цифр номера)
    # Например, "877775295987" -> "+77775295987"
    if len(digits_only) == 12 and digits_only.startswith('8'):
        normalized_phone = '+7' + digits_only[1:]
        logger.debug(f"DB Normalizer: '{phone_str_raw}' (digits: {digits_only}) -> {normalized_phone} (сценарий 8XXXXXXXXXXX)")
        return normalized_phone

    # Если номер начинается с '7' и имеет 12 цифр (7 как код страны + 11 цифр номера)
    # Например, "777775295987" (часто приходит от API, если номер уже был с +7) -> "+77775295987"
    if len(digits_only) == 12 and digits_only.startswith('7'):
        normalized_phone = '+7' + digits_only[1:]
        logger.debug(f"DB Normalizer: '{phone_str_raw}' (digits: {digits_only}) -> {normalized_phone} (сценарий 7XXXXXXXXXXX)")
        return normalized_phone
        
    # Если номер имеет 11 цифр (предполагаем, что это сам номер без кода страны 7/8)
    # Например, "77775295987" (если пользователь ввел без 8 или +7 в РФ/КЗ) -> "+77775295987"
    if len(digits_only) == 11:
        normalized_phone = '+7' + digits_only
        logger.debug(f"DB Normalizer: '{phone_str_raw}' (digits: {digits_only}) -> {normalized_phone} (сценарий XXXXXXXXXXX -> 11 цифр)")
        return normalized_phone

    logger.warning(f"DB Normalizer: Не удалось нормализовать '{phone_str_raw}' (цифры: '{digits_only}') к целевому формату +7XXXXXXXXXXX (11 цифр после +7).")
    return None

# Ваша функция get_user_by_phone должна использовать _normalize_phone_for_db
def get_user_by_phone(phone_number_raw):
    normalized_phone = _normalize_phone_for_db(phone_number_raw) # Используем обновленную функцию
    if not normalized_phone:
        # Лог уже будет внутри _normalize_phone_for_db или здесь можно добавить
        logger.debug(f"Поиск пользователя: не удалось нормализовать номер: '{phone_number_raw}'")
        return None
        
    # Этот лог останется полезным
    logger.debug(f"Fetching user with normalized phone number: {normalized_phone} (raw: {phone_number_raw})")
    
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    try:
        query = "SELECT * FROM users WHERE phone_number = %s"
        cursor.execute(query, (normalized_phone,))
        result = cursor.fetchone()
        if result:
            logger.debug(f"Результат запроса для {normalized_phone}: {result}")
            return result
        else:
            logger.debug(f"Пользователь с нормализованным телефоном {normalized_phone} не найден.")
            return None
    except Exception as e:
        logger.exception(f"Error executing get_user_by_phone query for {normalized_phone}.")
        return None
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

# Create a database connection
def create_connection():
    connection = None
    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        # logger.debug("Database connection established.") # Слишком частое сообщение
    except Error as e:
        logger.exception(f"The error '{e}' occurred while connecting to the database.")
    return connection

def get_user_by_phone(phone_number_raw):
    normalized_phone = _normalize_phone_for_db(phone_number_raw)
    if not normalized_phone:
        logger.debug(f"Поиск пользователя: невалидный или ненормализуемый номер: {phone_number_raw}")
        return None
        
    logger.debug(f"Fetching user with normalized phone number: {normalized_phone} (raw: {phone_number_raw})")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    try:
        # Предполагается, что в БД номера хранятся в нормализованном виде (+7XXXXXXXXXX)
        query = "SELECT * FROM users WHERE phone_number = %s"
        cursor.execute(query, (normalized_phone,))
        result = cursor.fetchone()
        if result:
            logger.debug(f"Результат запроса для {normalized_phone}: {result}")
            return result
        else:
            logger.debug(f"Пользователь с нормализованным телефоном {normalized_phone} не найден.")
            return None
    except Exception as e:
        logger.exception(f"Error executing get_user_by_phone query for {normalized_phone}.")
        return None
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            # logger.debug("Database connection closed.")

def get_all_users(): # Used by UserManagement "Права"
    connection = create_connection()
    if not connection: return []
    cursor = connection.cursor(dictionary=True)
    try:
        query = "SELECT id, phone_number, name, role FROM users WHERE is_active = 1"
        cursor.execute(query)
        users = cursor.fetchall()
        return users
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

# ... (остальные функции get_product_by_article, get_store_by_id и т.д. остаются без изменений) ...
# ... (record_sale, get_product_article_by_id, get_min_price, generate_report - без изменений) ...

def add_user_to_db(phone_number_raw, role, name, store_id=None):
    normalized_phone = _normalize_phone_for_db(phone_number_raw)
    if not normalized_phone:
        return f"Ошибка: неверный формат номера телефона '{phone_number_raw}'. Не удалось нормализовать."

    logger.debug(f"Adding user to database with normalized_phone: {normalized_phone}, role: {role}, name: {name}, store_id: {store_id}")
    connection = create_connection()
    if not connection: return "Ошибка: не удалось подключиться к базе данных."
    cursor = connection.cursor()
    message = ""
    try:
        # Проверка существующего пользователя по нормализованному номеру
        select_query_phone = "SELECT id, is_active FROM users WHERE phone_number = %s"
        cursor.execute(select_query_phone, (normalized_phone,))
        result_phone = cursor.fetchone()

        if result_phone:
            user_id, is_active = result_phone
            if is_active == 0: 
                update_query = "UPDATE users SET is_active = 1, role = %s, name = %s, store_id = %s WHERE id = %s"
                cursor.execute(update_query, (role, name, store_id, user_id))
                message = f"Пользователь с номером {normalized_phone} ({name}) активирован повторно и данные обновлены."
            else: 
                message = f"Пользователь с номером {normalized_phone} ({name}) уже существует и активен."
        else: 
            if store_id is None: store_id = 1 
            insert_query = "INSERT INTO users (phone_number, role, name, store_id, is_active) VALUES (%s, %s, %s, %s, 1)"
            cursor.execute(insert_query, (normalized_phone, role, name, store_id))
            message = f"Пользователь {name} с номером {normalized_phone} добавлен успешно."
        connection.commit()
    except Error as e:
        logger.error(f"Database error in add_user_to_db for {normalized_phone}: {e}")
        if connection.is_connected(): connection.rollback()
        message = f"Ошибка при работе с базой данных для номера {normalized_phone}."
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return message

def deactivate_user_in_db(user_id_to_deactivate):
    logger.debug(f"Deactivating user with ID: {user_id_to_deactivate}")
    connection = create_connection()
    if not connection: return "Ошибка: не удалось подключиться к базе данных."
    cursor = connection.cursor()
    response = ""
    try:
        select_query = "SELECT phone_number FROM users WHERE id = %s AND is_active = 1"
        cursor.execute(select_query, (user_id_to_deactivate,))
        result = cursor.fetchone()
        if result:
            update_query = "UPDATE users SET is_active = 0 WHERE id = %s"
            cursor.execute(update_query, (user_id_to_deactivate,))
            connection.commit()
            response = f"Пользователь ID {user_id_to_deactivate} деактивирован."
            logger.info(response)
        else:
            response = f"Пользователь ID {user_id_to_deactivate} не найден или уже деактивирован."
            logger.warning(response)
    except Error as e:
        logger.error(f"Database error in deactivate_user_in_db for ID {user_id_to_deactivate}: {e}")
        if connection.is_connected(): connection.rollback()
        response = "Ошибка при работе с базой данных во время деактивации."
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return response


def change_user_role_function(user_id, new_role):
    logger.debug(f"Changing role for user_id: {user_id} to new_role: {new_role}")
    connection = create_connection()
    if not connection:
        logger.error("Failed to create DB connection for change_user_role.")
        return False, "Ошибка подключения к БД."
    cursor = connection.cursor()
    try:
        query = "UPDATE users SET role = %s WHERE id = %s"
        cursor.execute(query, (new_role, user_id))
        connection.commit()
        logger.info(f"User ID {user_id} role updated to {new_role}.")
        return True, f"Роль пользователя ID {user_id} изменена на {new_role}."
    except Exception as e:
        logger.exception(f"Ошибка при изменении роли пользователя {user_id}.")
        if connection.is_connected(): connection.rollback()
        return False, "Ошибка при изменении роли в БД."
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def update_user_phone(user_id, new_phone_number_raw):
    normalized_new_phone = _normalize_phone_for_db(new_phone_number_raw)
    if not normalized_new_phone:
        return False, f"Ошибка: неверный формат нового номера телефона '{new_phone_number_raw}'. Не удалось нормализовать."

    logger.debug(f"Updating phone for user_id: {user_id} to normalized_new_phone: {normalized_new_phone}")
    connection = create_connection()
    if not connection:
        return False, "Ошибка подключения к БД."
    cursor = connection.cursor(dictionary=True)
    try:
        # Проверяем, не занят ли новый нормализованный номер другим активным пользователем
        check_query = "SELECT id, name FROM users WHERE phone_number = %s AND id != %s AND is_active = 1"
        cursor.execute(check_query, (normalized_new_phone, user_id))
        existing_user = cursor.fetchone()
        if existing_user:
            logger.warning(f"New normalized phone {normalized_new_phone} already used by user ID {existing_user['id']} ({existing_user['name']}).")
            return False, f"Номер {normalized_new_phone} уже используется другим активным пользователем ({existing_user['name']})."

        update_query = "UPDATE users SET phone_number = %s WHERE id = %s"
        cursor.execute(update_query, (normalized_new_phone, user_id))
        if cursor.rowcount == 0:
            connection.rollback() # Откатываем, если ничего не изменилось (хотя это маловероятно, если ID верный)
            logger.warning(f"User ID {user_id} not found for phone update, or phone number unchanged.")
            return False, f"Пользователь ID {user_id} не найден, или номер не изменен."
        
        connection.commit()
        logger.info(f"User ID {user_id} phone updated to {normalized_new_phone}.")
        return True, f"Номер телефона пользователя ID {user_id} изменен на {normalized_new_phone}."
    except Error as e:
        logger.exception(f"Database error updating phone for user ID {user_id} to {normalized_new_phone}.")
        if connection.is_connected(): connection.rollback()
        return False, "Ошибка базы данных при изменении номера."
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

# ... (остальные функции: create_buyback_record, create_return_record, create_trade_in_record, stock management - БЕЗ ИЗМЕНЕНИЙ, если они не работают с phone_number напрямую) ...
# ВАЖНО: _update_product_quantity_with_cursor, update_product_quantity, record_sale и другие, не связанные с users.phone_number, не требуют изменений.
# Также функции для работы с товарами (add_product_to_db, get_stock_report_data) не меняются.
# _update_product_quantity_with_cursor, update_product_quantity - без изменений
# record_sale - без изменений
# get_product_article_by_id - без изменений
# get_min_price - без изменений
# generate_report - без изменений
# create_buyback_record, create_return_record, create_trade_in_record - без изменений
# add_product_to_db, get_stock_report_data - без изменений

# Оставим только измененные функции и те, что нужны для контекста
# (Копирую только измененные и те, что вокруг них для понятности структуры)

def get_product_by_article(article_number):
    logger.debug(f"Fetching product with article number: {article_number}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    product = None
    try:
        query = "SELECT * FROM products WHERE article_number = %s"
        cursor.execute(query, (article_number,))
        product = cursor.fetchone()
        if product:
            logger.info(f"Продукт найден: {product}")
        else:
            logger.warning(f"Продукт с артикулом {article_number} не найден.")
    except Exception as e:
        logger.exception(f"Ошибка при выполнении запроса get_product_by_article (артикул: {article_number}).")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            # logger.debug("Database connection closed for get_product_by_article.") # Слишком частое
    return product

def get_store_by_id(store_id):
    logger.debug(f"Fetching store with ID: {store_id}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    store = None
    query = "SELECT * FROM stores WHERE id = %s"
    try:
        cursor.execute(query, (store_id,))
        store = cursor.fetchone()
        logger.debug(f"Query executed: {query} with store_id={store_id}, result: {store}")
    except Error as e:
        logger.error(f"Error fetching store by ID: {e}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            # logger.debug("Database connection closed.")
    return store
    
def get_sale_and_product_details_by_sale_id(sale_id):
    logger.debug(f"Fetching sale and product details for sale_id: {sale_id}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    details = None
    try:
        query = """
        SELECT 
            s.id as sale_id, 
            s.sold_price, 
            s.payment_type as sale_payment_type, 
            s.sale_date,
            p.id as product_id, 
            p.article_number, 
            p.model as product_model, 
            p.product_type
        FROM sales s
        JOIN products p ON s.product_id = p.id
        WHERE s.id = %s
        """
        cursor.execute(query, (sale_id,))
        details = cursor.fetchone()
        if details:
            logger.info(f"Sale and product details found for sale_id {sale_id}: {details}")
        else:
            logger.warning(f"No sale found with sale_id {sale_id}")
    except Error as e:
        logger.error(f"Error fetching sale details for sale_id {sale_id}: {e}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            # logger.debug(f"Database connection closed for get_sale_and_product_details_by_sale_id (sale_id: {sale_id}).")
    return details

def _update_product_quantity_with_cursor(cursor, product_article, quantity_change):
    logger.debug(f"Attempting to update quantity for product article: {product_article} by {quantity_change} using provided cursor.")
    if quantity_change < 0: # Check stock only if decreasing
        cursor.execute("SELECT quantity FROM products WHERE article_number = %s FOR UPDATE", (product_article,))
        current_quantity_result = cursor.fetchone()
        if not current_quantity_result:
             logger.error(f"Product {product_article} not found during quantity check in _update_product_quantity_with_cursor.")
             raise ValueError(f"Продукт {product_article} не найден для обновления количества.")
        current_quantity = current_quantity_result.get('quantity')
        if current_quantity is None:
            logger.error(f"Product {product_article} quantity is NULL in _update_product_quantity_with_cursor.")
            raise ValueError(f"У продукта {product_article} не указано количество (NULL).")
        if current_quantity < abs(quantity_change):
            logger.warning(f"Not enough stock for {product_article} to decrease by {abs(quantity_change)}. Current: {current_quantity}")
            raise ValueError(f"Недостаточно товара {product_article} на складе.")

    query = "UPDATE products SET quantity = quantity + %s WHERE article_number = %s"
    cursor.execute(query, (quantity_change, product_article))
    if cursor.rowcount == 0 and quantity_change !=0 :
        # Это может случиться, если товар был удален между FOR UPDATE и UPDATE, но это крайне маловероятно
        # или если quantity_change == 0, но это проверяется выше.
        # Более вероятно, если продукт не найден ИЗНАЧАЛЬНО (для quantity_change > 0), но это должно было бы отсечься на этапе поиска товара.
        logger.warning(f"Product {product_article} quantity not updated (rowcount 0), but an update was expected for change {quantity_change}.")
        # Не бросаем ошибку здесь, если quantity_change > 0, так как это может быть добавление к несуществующему товару,
        # что должно обрабатываться логикой выше (например, товар должен быть сначала создан).
        # Однако, для buyback/return/trade-in, товар должен существовать.
        # Для record_sale (уменьшение), проверка на существование и количество уже есть.
        # Для _update_product_quantity_with_cursor, если quantity_change > 0, предполагается, что товар есть.
        # Если его нет, rowcount будет 0, что может быть проблемой.
        # Давайте пересмотрим: _update_product_quantity_with_cursor должна предполагать, что товар существует.
        if quantity_change > 0: # Если увеличиваем количество, а товар не найден (rowcount 0)
             cursor.execute("SELECT id FROM products WHERE article_number = %s", (product_article,))
             if not cursor.fetchone():
                 logger.error(f"Product {product_article} not found for quantity increase in _update_product_quantity_with_cursor.")
                 raise ValueError(f"Продукт {product_article} не найден для увеличения количества.")

    logger.info(f"Product {product_article} quantity successfully updated by {quantity_change} in current transaction. Rowcount: {cursor.rowcount}")
    return True
    
def update_product_quantity(product_article, quantity_change, store_id_context=None): # Added store_id_context for logging
    logger.debug(f"Standalone update_product_quantity for article: {product_article} by {quantity_change} (Store context: {store_id_context})")
    connection = create_connection()
    if not connection: return False, "Ошибка подключения к БД."
    cursor = connection.cursor(dictionary=True)
    success = False
    message = ""
    try:
        connection.start_transaction() # Используем транзакцию
        cursor.execute("SELECT quantity, store_id FROM products WHERE article_number = %s FOR UPDATE", (product_article,))
        product_data = cursor.fetchone()

        if not product_data:
            logger.warning(f"Product {product_article} not found for stock update.")
            connection.rollback()
            return False, f"Товар с артикулом {product_article} не найден."
        
        # Проверка принадлежности к магазину, если это менеджер
        # Это должно быть в логике stock_handler, а не здесь, т.к. эта функция более общая.
        # if store_id_context and product_data.get('store_id') != store_id_context:
        #    logger.warning(f"Product {product_article} (store {product_data.get('store_id')}) does not belong to manager's store {store_id_context}.")
        #    connection.rollback()
        #    return False, f"Товар {product_article} не принадлежит вашему магазину."


        if quantity_change < 0: # Уменьшение количества
            current_quantity = product_data['quantity']
            if current_quantity is None: # Хотя NOT NULL в БД должен это предотвращать
                logger.error(f"Product {product_article} quantity is NULL (standalone).")
                connection.rollback()
                return False, f"У продукта {product_article} не указано количество (NULL)."
            if current_quantity < abs(quantity_change):
                logger.warning(f"Not enough stock for {product_article} (standalone). Current: {current_quantity}, trying to remove: {abs(quantity_change)}")
                connection.rollback()
                return False, f"Недостаточно товара {product_article} на складе ({current_quantity} шт.) для списания {abs(quantity_change)} шт."
        
        # Для увеличения количества (quantity_change > 0) дополнительных проверок на количество не нужно.
        
        query = "UPDATE products SET quantity = quantity + %s WHERE article_number = %s"
        cursor.execute(query, (quantity_change, product_article))
        
        if cursor.rowcount > 0:
            connection.commit()
            logger.info(f"Product {product_article} quantity updated by {quantity_change} (standalone). Rows affected: {cursor.rowcount}")
            success = True
            new_quantity = (product_data['quantity'] or 0) + quantity_change
            message = f"Остаток товара {product_article} изменен на {quantity_change} шт. Новый остаток: {new_quantity} шт."
        elif quantity_change == 0: # Если изменение 0, то rowcount может быть 0, но это успех
            connection.commit() # или rollback(), если мы не хотим коммитить пустые транзакции
            success = True
            message = f"Количество товара {product_article} не изменилось (изменение 0)."
        else:
            # Сюда мы не должны попасть, если SELECT FOR UPDATE нашел товар
            logger.error(f"Product {product_article} quantity not updated despite existing (standalone, rowcount 0 for non-zero change).")
            connection.rollback()
            message = f"Не удалось обновить остаток для {product_article} (изменение не было применено, rowcount 0)."
            
    except Error as e:
        logger.error(f"Error in standalone update_product_quantity for {product_article}: {e}")
        if connection.is_connected(): connection.rollback()
        message = f"Ошибка БД при обновлении остатка: {e}"
    except ValueError as ve: # Это из _update_product_quantity_with_cursor, но здесь своя логика
        logger.error(f"ValueError in standalone update_product_quantity for {product_article}: {ve}")
        if connection.is_connected(): connection.rollback()
        message = str(ve) 
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return success, message

def record_sale(product_id, product_article, store_id, user_id, sale_price, payment_type):
    logger.debug(f"Recording sale for product_id: {product_id} (article: {product_article}), store_id: {store_id}, user_id: {user_id}, sale_price: {sale_price}, payment_type: {payment_type}")
    connection = create_connection()
    if not connection:
        logger.error("Failed to create database connection for record_sale.")
        return False, "Ошибка подключения к БД для записи продажи."
        
    cursor = connection.cursor(dictionary=True)
    try:
        connection.start_transaction()
        # Проверка наличия товара и достаточного количества перед продажей
        # Эта логика теперь внутри _update_product_quantity_with_cursor
        
        _update_product_quantity_with_cursor(cursor, product_article, -1) # Уменьшаем количество

        insert_query = """INSERT INTO sales (product_id, store_id, user_id, sale_date, sold_price, payment_type) 
        VALUES (%s, %s, %s, NOW(), %s, %s)"""
        cursor.execute(insert_query, (product_id, store_id, user_id, sale_price, payment_type))
        logger.debug(f"Insert into sales executed. Product ID: {product_id}")
        
        connection.commit()
        logger.info(f"Sale recorded and product {product_article} quantity updated successfully.")
        return True, "Продажа успешно зарегистрирована."
    except ValueError as ve: # Ошибки типа "недостаточно товара"
        logger.warning(f"ValueError during record_sale for {product_article}: {ve}")
        if connection.is_connected(): connection.rollback()
        return False, str(ve)
    except Error as e: # Ошибки MySQL
        logger.exception(f"MySQL Error during record_sale transaction for product article {product_article}. Rolling back.")
        if connection.is_connected(): connection.rollback()
        return False, f"Ошибка БД при записи продажи: {e}"
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            # logger.debug("Database connection closed for record_sale.")


def get_product_article_by_id(product_id):
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor()
    article = None
    try:
        query = "SELECT article_number FROM products WHERE id = %s"
        cursor.execute(query, (product_id,))
        result = cursor.fetchone()
        if result:
            article = result[0]
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return article

def get_min_price(product_article):
    logger.debug(f"Fetching minimum price for product article: {product_article}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    min_price = None
    try:
        query = "SELECT min_price FROM products WHERE article_number = %s"
        cursor.execute(query, (product_article,))
        result = cursor.fetchone()
        if result:
            min_price = result['min_price']
    except Error as e:
        logger.error(f"Error fetching min price for {product_article}: {e}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            # logger.debug("Database connection closed.")
    return min_price

def generate_report(start_date, end_date):
    logger.debug(f"Generating report from {start_date} to {end_date}")
    connection = create_connection()
    if not connection: return []
    cursor = connection.cursor() 
    report_data = []
    try:
        query = """
                SELECT 
                    s.id as sale_id, 
                    s.sale_date, 
                    s.sold_price, 
                    s.payment_type,
                    p.article_number, 
                    p.product_type, 
                    p.model, 
                    p.metal_type,
                    p.weight,
                    p.price_per_gram,
                    p.price as product_original_price, 
                    p.manufacturer,
                    p.commentaries,
                    u.name as user_name, 
                    st.name as store_name
                FROM sales s
                JOIN products p ON s.product_id = p.id
                JOIN users u ON s.user_id = u.id
                JOIN stores st ON s.store_id = st.id
                WHERE s.sale_date BETWEEN %s AND %s
                ORDER BY s.sale_date, s.id;
                """
        # logger.debug(f"Executing report query: {query}")
        cursor.execute(query, (start_date, end_date))
        report_data = cursor.fetchall()
        # logger.debug(f"Report data retrieved (count: {len(report_data)}): {report_data[:2]}...")
    except Exception as e:
        logger.exception("Error generating report.")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            # logger.debug("Database connection closed after generating report.")
    return report_data


def create_buyback_record(product_article, price, payment_type, user_id, store_id):
    logger.debug(f"Creating buyback record for article: {product_article}, price: {price}, user_id: {user_id}")
    connection = create_connection()
    if not connection: return None, "Ошибка подключения к БД для скупки."
    cursor = connection.cursor(dictionary=True)
    buyback_id = None
    try:
        connection.start_transaction()
        # Проверка существования товара перед увеличением количества
        cursor.execute("SELECT id FROM products WHERE article_number = %s FOR UPDATE", (product_article,))
        if not cursor.fetchone():
            logger.error(f"Product {product_article} not found for buyback quantity increase.")
            connection.rollback()
            return None, f"Товар {product_article} не найден в базе для скупки."

        _update_product_quantity_with_cursor(cursor, product_article, 1) # Увеличиваем количество

        query = """
        INSERT INTO buybacks (product_article, buyback_price, payment_type, user_id, store_id, buyback_date)
        VALUES (%s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (product_article, price, payment_type, user_id, store_id))
        buyback_id = cursor.lastrowid
        
        connection.commit()
        logger.info(f"Buyback record created with ID: {buyback_id} and stock updated for article {product_article}")
        return buyback_id, "Скупка успешно зарегистрирована."
    except ValueError as ve:
        logger.warning(f"ValueError during buyback for {product_article}: {ve}")
        if connection.is_connected(): connection.rollback() 
        return None, str(ve)
    except Error as e:
        logger.error(f"Error during buyback transaction for {product_article}: {e}")
        if connection.is_connected(): connection.rollback() 
        return None, f"Ошибка БД при скупке: {e}"
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()


def create_return_record(sale_id, product_article, return_price, payment_type, user_id, store_id):
    logger.debug(f"Creating return record for sale_id: {sale_id}, product_article: {product_article}, price: {return_price}")
    connection = create_connection()
    if not connection: return None, "Ошибка подключения к БД для возврата."
    cursor = connection.cursor(dictionary=True)
    return_id = None
    try:
        connection.start_transaction()
        # Проверка существования товара перед увеличением количества
        cursor.execute("SELECT id FROM products WHERE article_number = %s FOR UPDATE", (product_article,))
        if not cursor.fetchone():
            logger.error(f"Product {product_article} not found for return quantity increase.")
            connection.rollback()
            return None, f"Товар {product_article} не найден в базе для возврата."

        _update_product_quantity_with_cursor(cursor, product_article, 1) # Увеличиваем количество

        query = """
        INSERT INTO returns (original_sale_id, product_article, return_price, payment_type, user_id, store_id, return_date)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (sale_id, product_article, return_price, payment_type, user_id, store_id))
        return_id = cursor.lastrowid
        
        connection.commit()
        logger.info(f"Return record created with ID: {return_id} and stock updated for product {product_article} from sale {sale_id}")
        return return_id, "Возврат успешно зарегистрирован."
    except ValueError as ve:
        logger.warning(f"ValueError during return for {product_article}: {ve}")
        if connection.is_connected(): connection.rollback()
        return None, str(ve)
    except Error as e:
        logger.error(f"Error during return transaction for {product_article}: {e}")
        if connection.is_connected(): connection.rollback()
        return None, f"Ошибка БД при возврате: {e}"
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()


def create_trade_in_record(old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id):
    logger.debug(f"Creating trade-in: old {old_product_article}, new {new_product_article}, diff {price_difference}")
    connection = create_connection()
    if not connection: return None, "Ошибка подключения к БД для Trade-In."
    cursor = connection.cursor(dictionary=True)
    trade_in_id = None
    try:
        connection.start_transaction()
        # Проверка существования товаров
        cursor.execute("SELECT id FROM products WHERE article_number = %s FOR UPDATE", (old_product_article,))
        if not cursor.fetchone():
            logger.error(f"Old product {old_product_article} not found for trade-in.")
            connection.rollback()
            return None, f"Сдаваемый товар {old_product_article} не найден."
        
        cursor.execute("SELECT id, quantity FROM products WHERE article_number = %s FOR UPDATE", (new_product_article,))
        new_product_db = cursor.fetchone()
        if not new_product_db:
            logger.error(f"New product {new_product_article} not found for trade-in.")
            connection.rollback()
            return None, f"Приобретаемый товар {new_product_article} не найден."
        if new_product_db['quantity'] < 1:
            logger.warning(f"New product {new_product_article} out of stock for trade-in.")
            connection.rollback()
            return None, f"Приобретаемый товар {new_product_article} отсутствует на складе."

        _update_product_quantity_with_cursor(cursor, old_product_article, 1) # Старый товар на склад
        _update_product_quantity_with_cursor(cursor, new_product_article, -1) # Новый товар со склада
        
        query = """
        INSERT INTO trade_ins (old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id, trade_in_date)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id))
        trade_in_id = cursor.lastrowid
        
        connection.commit()
        logger.info(f"Trade-in record created with ID: {trade_in_id} and stock updated.")
        return trade_in_id, "Trade-In успешно зарегистрирован."
    except ValueError as ve: # Ошибки от _update_product_quantity_with_cursor
        logger.warning(f"ValueError during trade-in (old: {old_product_article}, new: {new_product_article}): {ve}")
        if connection.is_connected(): connection.rollback()
        return None, str(ve)
    except Error as e:
        logger.error(f"Error during trade-in transaction (old: {old_product_article}, new: {new_product_article}): {e}")
        if connection.is_connected(): connection.rollback()
        return None, f"Ошибка БД при Trade-In: {e}"
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def add_product_to_db(product_data: dict):
    logger.info(f"Attempting to add new product: {product_data.get('article_number')}")
    connection = create_connection()
    if not connection:
        return False, "Ошибка подключения к БД."
    cursor = connection.cursor(dictionary=True)
    try:
        connection.start_transaction() # Используем транзакцию
        cursor.execute("SELECT id FROM products WHERE article_number = %s", (product_data['article_number'],))
        if cursor.fetchone():
            logger.warning(f"Product with article_number {product_data['article_number']} already exists.")
            connection.rollback()
            return False, f"Товар с артикулом {product_data['article_number']} уже существует."

        required_fields = ['store_id', 'article_number', 'metal_type', 'product_type', 'model', 
                           'weight', 'price_per_gram', 'price', 'min_price', 'quantity', 'manufacturer']
        for field in required_fields:
            if field not in product_data: # Позволяем None для manufacturer и commentaries
                if field not in ['manufacturer', 'commentaries'] or product_data.get(field) is None:
                    if field == 'quantity' and product_data.get(field) is None:
                        product_data[field] = 0 
                    elif field not in ['commentaries', 'manufacturer']:
                        logger.error(f"Missing required field '{field}' or it is None for new product {product_data['article_number']}.")
                        connection.rollback()
                        return False, f"Отсутствует обязательное поле или оно не заполнено: {field}"
        
        product_data.setdefault('arrival_date', Error) 

        cols = []
        vals_placeholder = []
        vals = []
        for key, value in product_data.items():
            if key == 'arrival_date' and value == Error: 
                cols.append(f"`{key}`")
                vals_placeholder.append("NOW()")
            else:
                cols.append(f"`{key}`") 
                vals_placeholder.append("%s")
                vals.append(value)
        
        query = f"INSERT INTO products ({', '.join(cols)}) VALUES ({', '.join(vals_placeholder)})"
        
        logger.debug(f"Add product query: {query}, values: {vals}")
        cursor.execute(query, vals)
        product_id = cursor.lastrowid
        connection.commit()
        logger.info(f"Product {product_data['article_number']} added successfully with ID {product_id}.")
        return True, f"Товар {product_data['article_number']} ({product_data.get('model', '')}) успешно добавлен."

    except Error as e:
        logger.exception(f"Database error adding product {product_data.get('article_number')}: {e}")
        if connection.is_connected(): connection.rollback()
        return False, f"Ошибка БД при добавлении товара: {e}"
    except Exception as ex: 
        logger.exception(f"General error adding product {product_data.get('article_number')}: {ex}")
        if connection.is_connected(): connection.rollback()
        return False, f"Общая ошибка при добавлении товара: {ex}"
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def get_stock_report_data(store_id=None): 
    logger.info(f"Generating stock report data. Store filter: {store_id}")
    connection = create_connection()
    if not connection: return []
    cursor = connection.cursor(dictionary=True) 
    try:
        base_query = """
            SELECT 
                p.article_number, p.product_type, p.model, p.metal_type, 
                p.weight, p.quantity, p.price, p.min_price, 
                p.manufacturer, s.name as store_name, p.arrival_date, p.commentaries
            FROM products p
            LEFT JOIN stores s ON p.store_id = s.id
        """
        params = []
        if store_id:
            base_query += " WHERE p.store_id = %s"
            params.append(store_id)
        
        base_query += " ORDER BY p.product_type, p.model, p.article_number"

        cursor.execute(base_query, params)
        stock_data = cursor.fetchall()
        logger.info(f"Stock report data fetched. Count: {len(stock_data)}")
        return stock_data
    except Error as e:
        logger.exception(f"Database error generating stock report: {e}")
        return []
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
# --- END OF FILE database.py ---