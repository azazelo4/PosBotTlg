import mysql.connector
from config import DB_HOST, DB_USER, DB_PASS, DB_NAME
from mysql.connector import Error
import logging
import re # Для нормализации номера
from datetime import date, datetime # Added for type hints and usage in add_product_to_db

logger = logging.getLogger(__name__)

# Функция нормализации номера телефона
def _normalize_phone_for_db(phone_str_raw: str) -> str | None:
    """
    Нормализует номер телефона к формату +7XXXXXXXXXXX (11 цифр после +7).
    Агрессивно очищает все нецифровые символы.
    """
    if not phone_str_raw:
        logger.debug("DB Normalizer: Получена пустая строка для нормализации.")
        return None

    digits_only = re.sub(r'\D', '', phone_str_raw)
    logger.debug(f"DB Normalizer: phone_str_raw='{phone_str_raw}', digits_only='{digits_only}'")

    if not digits_only:
        logger.debug(f"DB Normalizer: После очистки в '{phone_str_raw}' не осталось цифр.")
        return None

    if len(digits_only) == 12 and digits_only.startswith('8'):
        normalized_phone = '+7' + digits_only[1:]
        logger.debug(f"DB Normalizer: '{phone_str_raw}' (digits: {digits_only}) -> {normalized_phone} (сценарий 8XXXXXXXXXXX)")
        return normalized_phone

    if len(digits_only) == 12 and digits_only.startswith('7'):
        normalized_phone = '+7' + digits_only[1:]
        logger.debug(f"DB Normalizer: '{phone_str_raw}' (digits: {digits_only}) -> {normalized_phone} (сценарий 7XXXXXXXXXXX)")
        return normalized_phone

    if len(digits_only) == 11:
        normalized_phone = '+7' + digits_only
        logger.debug(f"DB Normalizer: '{phone_str_raw}' (digits: {digits_only}) -> {normalized_phone} (сценарий XXXXXXXXXXX -> 11 цифр)")
        return normalized_phone

    logger.warning(f"DB Normalizer: Не удалось нормализовать '{phone_str_raw}' (цифры: '{digits_only}') к целевому формату +7XXXXXXXXXXX (11 цифр после +7).")
    return None

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
    except Error as e:
        logger.exception(f"DATABASE CONNECTION ERROR: The error '{e}' occurred while connecting to the database.")
    return connection

# Получение пользователя по номеру телефона
def get_user_by_phone(phone_number_raw: str):
    normalized_phone = _normalize_phone_for_db(phone_number_raw)
    if not normalized_phone:
        logger.debug(f"Поиск пользователя: невалидный или ненормализуемый номер: '{phone_number_raw}'")
        return None

    logger.debug(f"Fetching user with normalized phone number: {normalized_phone} (raw: {phone_number_raw})")
    connection = create_connection()
    if not connection:
        logger.error("Failed to create database connection for get_user_by_phone.")
        return None

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
    except Error as e: # Changed from Exception to Error for more specific MySQL errors
        logger.exception(f"Error executing get_user_by_phone query for {normalized_phone}.")
        return None
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()

def get_all_users(): # Used by UserManagement "Права"
    connection = create_connection()
    if not connection:
        logger.error("Failed to create database connection for get_all_users.")
        return []
    cursor = connection.cursor(dictionary=True)
    try:
        query = "SELECT id, phone_number, name, role FROM users WHERE is_active = 1"
        cursor.execute(query)
        users = cursor.fetchall()
        return users
    except Error as e:
        logger.exception("Error executing get_all_users query.")
        return []
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()

def add_user_to_db(phone_number_raw: str, role: str, name: str, store_id: int | None = None) -> str:
    normalized_phone = _normalize_phone_for_db(phone_number_raw)
    if not normalized_phone:
        return f"Ошибка: неверный формат номера телефона '{phone_number_raw}'. Не удалось нормализовать."

    logger.debug(f"Adding user to database with normalized_phone: {normalized_phone}, role: {role}, name: {name}, store_id: {store_id}")
    connection = create_connection()
    if not connection:
        return "Ошибка: не удалось подключиться к базе данных."

    cursor = connection.cursor()
    message = ""
    try:
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
            if store_id is None: store_id = 1 # Default store_id if not provided
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

def deactivate_user_in_db(user_id_to_deactivate: int) -> str:
    logger.debug(f"Deactivating user with ID: {user_id_to_deactivate}")
    connection = create_connection()
    if not connection:
        return "Ошибка: не удалось подключиться к базе данных."

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

def change_user_role_function(user_id: int, new_role: str) -> tuple[bool, str]:
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
        if cursor.rowcount > 0:
            logger.info(f"User ID {user_id} role updated to {new_role}.")
            return True, f"Роль пользователя ID {user_id} изменена на {new_role}."
        else:
            logger.warning(f"User ID {user_id} not found or role unchanged for new_role {new_role}.")
            return False, f"Пользователь ID {user_id} не найден или роль уже была {new_role}."
    except Error as e:
        logger.exception(f"Ошибка при изменении роли пользователя {user_id}.")
        if connection.is_connected(): connection.rollback()
        return False, "Ошибка при изменении роли в БД."
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def update_user_phone(user_id: int, new_phone_number_raw: str) -> tuple[bool, str]:
    normalized_new_phone = _normalize_phone_for_db(new_phone_number_raw)
    if not normalized_new_phone:
        return False, f"Ошибка: неверный формат нового номера телефона '{new_phone_number_raw}'. Не удалось нормализовать."

    logger.debug(f"Updating phone for user_id: {user_id} to normalized_new_phone: {normalized_new_phone}")
    connection = create_connection()
    if not connection:
        return False, "Ошибка подключения к БД."

    cursor = connection.cursor(dictionary=True)
    try:
        check_query = "SELECT id, name FROM users WHERE phone_number = %s AND id != %s AND is_active = 1"
        cursor.execute(check_query, (normalized_new_phone, user_id))
        existing_user = cursor.fetchone()
        if existing_user:
            logger.warning(f"New normalized phone {normalized_new_phone} already used by user ID {existing_user['id']} ({existing_user['name']}).")
            return False, f"Номер {normalized_new_phone} уже используется другим активным пользователем ({existing_user['name']})."

        update_query = "UPDATE users SET phone_number = %s WHERE id = %s"
        cursor.execute(update_query, (normalized_new_phone, user_id))
        if cursor.rowcount == 0:
            connection.rollback()
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

def get_product_by_article(article_number: str):
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
            logger.info(f"Продукт найден: {product.get('model', article_number)}")
        else:
            logger.warning(f"Продукт с артикулом {article_number} не найден.")
    except Error as e:
        logger.exception(f"Ошибка при выполнении запроса get_product_by_article (артикул: {article_number}).")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return product

def get_store_by_id(store_id: int):
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
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return store

def get_sale_and_product_details_by_sale_id(sale_id: int):
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
            logger.info(f"Sale and product details found for sale_id {sale_id}: {details.get('product_model', details.get('article_number'))}")
        else:
            logger.warning(f"No sale found with sale_id {sale_id}")
    except Error as e:
        logger.error(f"Error fetching sale details for sale_id {sale_id}: {e}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return details

def _update_product_quantity_with_cursor(cursor, product_article: str, quantity_change: int) -> bool:
    logger.debug(f"Attempting to update quantity for product article: {product_article} by {quantity_change} using provided cursor.")
    # Fetch current quantity and lock the row
    cursor.execute("SELECT quantity FROM products WHERE article_number = %s FOR UPDATE", (product_article,))
    current_quantity_result = cursor.fetchone()

    if not current_quantity_result:
        logger.error(f"Product {product_article} not found during quantity check in _update_product_quantity_with_cursor.")
        raise ValueError(f"Продукт {product_article} не найден для обновления количества.")

    current_quantity = current_quantity_result.get('quantity')
    if current_quantity is None: # Should be prevented by NOT NULL constraint
        logger.error(f"Product {product_article} quantity is NULL in _update_product_quantity_with_cursor.")
        raise ValueError(f"У продукта {product_article} не указано количество (NULL).")

    if quantity_change < 0 and current_quantity < abs(quantity_change):
        logger.warning(f"Not enough stock for {product_article} to decrease by {abs(quantity_change)}. Current: {current_quantity}")
        raise ValueError(f"Недостаточно товара {product_article} на складе ({current_quantity} шт.) для списания {abs(quantity_change)} шт.")

    query = "UPDATE products SET quantity = quantity + %s WHERE article_number = %s"
    cursor.execute(query, (quantity_change, product_article))

    if cursor.rowcount == 0 and quantity_change != 0:
        # This case should ideally not be reached if FOR UPDATE found the product
        logger.error(f"Product {product_article} quantity not updated (rowcount 0) despite existing. Change: {quantity_change}")
        raise ValueError(f"Не удалось обновить количество для продукта {product_article} (rowcount 0).")

    logger.info(f"Product {product_article} quantity successfully updated by {quantity_change} in current transaction. Rowcount: {cursor.rowcount}")
    return True

def update_product_quantity(product_article: str, quantity_change: int, store_id_context: int | None = None) -> tuple[bool, str]:
    logger.debug(f"Standalone update_product_quantity for article: {product_article} by {quantity_change} (Store context: {store_id_context})")
    connection = create_connection()
    if not connection: return False, "Ошибка подключения к БД."
    cursor = connection.cursor(dictionary=True)
    success = False
    message = ""
    try:
        connection.start_transaction()
        # The logic with FOR UPDATE and checks is now inside _update_product_quantity_with_cursor
        _update_product_quantity_with_cursor(cursor, product_article, quantity_change)
        # Fetch new quantity for message after successful update
        cursor.execute("SELECT quantity FROM products WHERE article_number = %s", (product_article,))
        new_quantity_data = cursor.fetchone()
        new_quantity = new_quantity_data['quantity'] if new_quantity_data else "неизвестно"

        connection.commit()
        success = True
        message = f"Остаток товара {product_article} изменен на {quantity_change:+} шт. Новый остаток: {new_quantity} шт."
        logger.info(message)

    except ValueError as ve: # Catch errors from _update_product_quantity_with_cursor
        logger.warning(f"ValueError in standalone update_product_quantity for {product_article}: {ve}")
        if connection.is_connected(): connection.rollback()
        message = str(ve)
    except Error as e:
        logger.error(f"Error in standalone update_product_quantity for {product_article}: {e}")
        if connection.is_connected(): connection.rollback()
        message = f"Ошибка БД при обновлении остатка: {e}"
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return success, message

def record_sale(product_id: int, product_article: str, store_id: int, user_id: int, sale_price: float, payment_type: str) -> tuple[bool, str]:
    logger.debug(f"Recording sale for product_id: {product_id} (article: {product_article}), store_id: {store_id}, user_id: {user_id}, sale_price: {sale_price}, payment_type: {payment_type}")
    connection = create_connection()
    if not connection:
        logger.error("Failed to create database connection for record_sale.")
        return False, "Ошибка подключения к БД для записи продажи."

    cursor = connection.cursor(dictionary=True)
    try:
        connection.start_transaction()
        _update_product_quantity_with_cursor(cursor, product_article, -1) # Уменьшаем количество

        insert_query = """INSERT INTO sales (product_id, store_id, user_id, sale_date, sold_price, payment_type)
        VALUES (%s, %s, %s, NOW(), %s, %s)"""
        cursor.execute(insert_query, (product_id, store_id, user_id, sale_price, payment_type))
        logger.debug(f"Insert into sales executed. Product ID: {product_id}")

        connection.commit()
        logger.info(f"Sale recorded and product {product_article} quantity updated successfully.")
        return True, "Продажа успешно зарегистрирована."
    except ValueError as ve:
        logger.warning(f"ValueError during record_sale for {product_article}: {ve}")
        if connection.is_connected(): connection.rollback()
        return False, str(ve)
    except Error as e:
        logger.exception(f"MySQL Error during record_sale transaction for product article {product_article}. Rolling back.")
        if connection.is_connected(): connection.rollback()
        return False, f"Ошибка БД при записи продажи: {e}"
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def get_product_article_by_id(product_id: int) -> str | None:
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor() # No need for dictionary=True if only one column
    article = None
    try:
        query = "SELECT article_number FROM products WHERE id = %s"
        cursor.execute(query, (product_id,))
        result = cursor.fetchone()
        if result:
            article = result[0]
    except Error as e:
        logger.exception(f"Error fetching article for product_id {product_id}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return article

def get_min_price(product_article: str) -> float | None:
    logger.debug(f"Fetching minimum price for product article: {product_article}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    min_price = None
    try:
        query = "SELECT min_price FROM products WHERE article_number = %s"
        cursor.execute(query, (product_article,))
        result = cursor.fetchone()
        if result and result['min_price'] is not None:
            min_price = float(result['min_price'])
    except Error as e:
        logger.error(f"Error fetching min price for {product_article}: {e}")
    except (ValueError, TypeError) as ve:
        logger.error(f"Error converting min_price for {product_article}: {ve}. Value: {result.get('min_price') if result else 'N/A'}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return min_price

def generate_report(start_date: date, end_date: date) -> list:
    logger.debug(f"Generating report from {start_date} to {end_date}")
    connection = create_connection()
    if not connection: return []
    # cursor = connection.cursor() # Original code, will return tuples
    cursor = connection.cursor(dictionary=True) # Changed to dictionary for easier access in report_handler
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
                WHERE DATE(s.sale_date) BETWEEN %s AND %s
                ORDER BY s.sale_date, s.id;
                """
        # Ensure end_date includes the whole day if it's a date object
        # The query uses DATE(s.sale_date) so direct date comparison is fine.
        cursor.execute(query, (start_date, end_date))
        report_data = cursor.fetchall()
    except Error as e:
        logger.exception("Error generating report.")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return report_data

def create_buyback_record(product_article: str, price: float, payment_type: str, user_id: int, store_id: int) -> tuple[int | None, str]:
    logger.debug(f"Creating buyback record for article: {product_article}, price: {price}, user_id: {user_id}")
    connection = create_connection()
    if not connection: return None, "Ошибка подключения к БД для скупки."
    cursor = connection.cursor(dictionary=True)
    buyback_id = None
    try:
        connection.start_transaction()
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

def create_return_record(sale_id: int, product_article: str, return_price: float, payment_type: str, user_id: int, store_id: int) -> tuple[int | None, str]:
    logger.debug(f"Creating return record for sale_id: {sale_id}, product_article: {product_article}, price: {return_price}")
    connection = create_connection()
    if not connection: return None, "Ошибка подключения к БД для возврата."
    cursor = connection.cursor(dictionary=True)
    return_id = None
    try:
        connection.start_transaction()
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

def create_trade_in_record(old_product_article: str, new_product_article: str, price_difference: float, payment_type_difference: str, user_id: int, store_id: int) -> tuple[int | None, str]:
    logger.debug(f"Creating trade-in: old {old_product_article}, new {new_product_article}, diff {price_difference}")
    connection = create_connection()
    if not connection: return None, "Ошибка подключения к БД для Trade-In."
    cursor = connection.cursor(dictionary=True)
    trade_in_id = None
    try:
        connection.start_transaction()
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
    except ValueError as ve:
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

def add_product_to_db(product_data: dict) -> tuple[bool, str]:
    logger.info(f"Attempting to add new product: {product_data.get('article_number')}")
    connection = create_connection()
    if not connection:
        return False, "Ошибка подключения к БД."
    cursor = connection.cursor(dictionary=True)
    try:
        connection.start_transaction()
        cursor.execute("SELECT id FROM products WHERE article_number = %s", (product_data['article_number'],))
        if cursor.fetchone():
            logger.warning(f"Product with article_number {product_data['article_number']} already exists.")
            connection.rollback()
            return False, f"Товар с артикулом {product_data['article_number']} уже существует."

        # Ensure all required fields for DB are present
        required_fields_for_db = ['store_id', 'article_number', 'metal_type', 'product_type', 'model',
                                  'weight', 'price', 'min_price', 'quantity']
        # Optional fields: 'price_per_gram', 'manufacturer', 'commentaries', 'arrival_date'

        for field in required_fields_for_db:
            if field not in product_data or product_data[field] is None:
                # Allow quantity to be 0 if None, but other required fields must be present
                if field == 'quantity' and product_data.get(field) is None:
                    product_data[field] = 0
                else:
                    logger.error(f"Missing or None value for required field '{field}' for new product {product_data['article_number']}.")
                    connection.rollback()
                    return False, f"Отсутствует обязательное поле или оно не заполнено: {field}"
        
        # Prepare for insert, handling optional fields and arrival_date default
        cols = []
        vals_placeholder = []
        vals_list = []

        # Define all possible columns in DB order or as desired
        db_columns = ['store_id', 'article_number', 'metal_type', 'product_type', 'model',
                      'weight', 'price_per_gram', 'price', 'min_price', 'quantity',
                      'manufacturer', 'commentaries', 'arrival_date']

        for col_name in db_columns:
            if col_name == 'arrival_date':
                if product_data.get(col_name) is None: # Use NOW() if not provided
                    cols.append(f"`{col_name}`")
                    vals_placeholder.append("NOW()")
                else: # If provided, use it
                    cols.append(f"`{col_name}`")
                    vals_placeholder.append("%s")
                    vals_list.append(product_data[col_name])
            elif col_name in product_data:
                cols.append(f"`{col_name}`")
                vals_placeholder.append("%s")
                vals_list.append(product_data[col_name])
            # If a column isn't in product_data and isn't arrival_date, it might be nullable or have a DB default.
            # For this example, we assume all defined db_columns either exist in product_data,
            # are arrival_date, or are otherwise handled by DB (e.g. nullable, default value).

        query = f"INSERT INTO products ({', '.join(cols)}) VALUES ({', '.join(vals_placeholder)})"

        logger.debug(f"Add product query: {query}, values: {vals_list}")
        cursor.execute(query, vals_list)
        product_id = cursor.lastrowid
        connection.commit()
        logger.info(f"Product {product_data['article_number']} added successfully with ID {product_id}.")
        return True, f"Товар {product_data.get('article_number')} ({product_data.get('model', '')}) успешно добавлен."

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

def get_stock_report_data(store_id: int | None = None) -> list:
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
        if store_id is not None:
            base_query += " WHERE p.store_id = %s"
            params.append(store_id)

        base_query += " ORDER BY p.product_type, p.model, p.article_number"

        cursor.execute(base_query, params if params else None)
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
