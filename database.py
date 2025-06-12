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

def get_all_users():
    connection = create_connection()
    if not connection:
        logger.error("Failed to create database connection for get_all_users.")
        return []
    cursor = connection.cursor(dictionary=True)
    try:
        query = "SELECT id, phone_number, name, role FROM users WHERE is_active = 1" # Only active users
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
            logger.debug("Database connection closed.")
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
            logger.debug(f"Database connection closed for get_sale_and_product_details_by_sale_id (sale_id: {sale_id}).")
    return details


def _update_product_quantity_with_cursor(cursor, product_article, quantity_change):
    logger.debug(f"Attempting to update quantity for product article: {product_article} by {quantity_change} using provided cursor.")
    if quantity_change < 0:
        cursor.execute("SELECT quantity FROM products WHERE article_number = %s FOR UPDATE", (product_article,))
        current_quantity_result = cursor.fetchone() # Will be dict or None
        if not current_quantity_result:
             logger.error(f"Product {product_article} not found during quantity check in _update_product_quantity_with_cursor.")
             raise ValueError(f"Product {product_article} not found for quantity update.")
        current_quantity = current_quantity_result.get('quantity')
        if current_quantity is None:
            logger.error(f"Product {product_article} quantity is NULL in _update_product_quantity_with_cursor.")
            raise ValueError(f"Product {product_article} has NULL quantity.")
        if current_quantity < abs(quantity_change):
            logger.warning(f"Not enough stock for {product_article} to decrease by {abs(quantity_change)}. Current: {current_quantity}")
            raise ValueError(f"Not enough stock for {product_article}.")

    query = "UPDATE products SET quantity = quantity + %s WHERE article_number = %s"
    cursor.execute(query, (quantity_change, product_article))
    if cursor.rowcount == 0 and quantity_change !=0 :
        logger.warning(f"Product {product_article} quantity not updated (rowcount 0), but an update was expected for change {quantity_change}.")
        raise ValueError(f"Failed to update quantity for {product_article}, product not found or no change occurred when one was expected.")
    logger.info(f"Product {product_article} quantity successfully updated by {quantity_change} in current transaction. Rowcount: {cursor.rowcount}")
    return True

def update_product_quantity(product_article, quantity_change):
    logger.debug(f"Standalone update_product_quantity for article: {product_article} by {quantity_change}")
    connection = create_connection()
    if not connection: return False
    cursor = connection.cursor(dictionary=True)
    success = False
    try:
        if quantity_change < 0:
            cursor.execute("SELECT quantity FROM products WHERE article_number = %s FOR UPDATE", (product_article,))
            current_quantity_result = cursor.fetchone()
            if not current_quantity_result:
                logger.error(f"Product {product_article} not found for standalone quantity update.")
                return False
            current_quantity = current_quantity_result['quantity']
            if current_quantity is None:
                logger.error(f"Product {product_article} quantity is NULL (standalone).")
                return False
            if current_quantity < abs(quantity_change):
                logger.warning(f"Not enough stock for {product_article} (standalone). Current: {current_quantity}")
                return False

        query = "UPDATE products SET quantity = quantity + %s WHERE article_number = %s"
        cursor.execute(query, (quantity_change, product_article))
        
        if cursor.rowcount > 0 or quantity_change == 0:
            connection.commit()
            logger.info(f"Product {product_article} quantity updated by {quantity_change} (standalone). Rows affected: {cursor.rowcount}")
            success = True
        else:
            logger.warning(f"Product {product_article} quantity not updated (standalone, rowcount 0 for non-zero change).")
            connection.rollback()

    except Error as e:
        logger.error(f"Error in standalone update_product_quantity for {product_article}: {e}")
        if connection.is_connected(): connection.rollback()
    except ValueError as ve:
        logger.error(f"ValueError in standalone update_product_quantity for {product_article}: {ve}")
        if connection.is_connected(): connection.rollback()
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return success

def record_sale(product_id, product_article, store_id, user_id, sale_price, payment_type):
    logger.debug(f"Recording sale for product_id: {product_id} (article: {product_article}), store_id: {store_id}, user_id: {user_id}, sale_price: {sale_price}, payment_type: {payment_type}")
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
    connection = create_connection()
    if not connection:
        return "Ошибка: не удалось подключиться к базе данных."

<<<<<<< HEAD
    cursor = connection.cursor()
    message = ""
    try:
        select_query_phone = "SELECT id, is_active FROM users WHERE phone_number = %s"
        cursor.execute(select_query_phone, (normalized_phone,))
=======
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
            logger.debug("Database connection closed.")
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
        logger.debug(f"Executing report query: {query}")
        cursor.execute(query, (start_date, end_date))
        report_data = cursor.fetchall()
        logger.debug(f"Report data retrieved (count: {len(report_data)}): {report_data[:2]}...")
    except Exception as e:
        logger.exception("Error generating report.")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed after generating report.")
    return report_data

def add_user_to_db(phone_number, role, name):
    logger.debug(f"Adding user to database with phone_number: {phone_number}, role: {role}, name: {name}")
    connection = create_connection()
    if not connection: return "Ошибка: не удалось подключиться к базе данных."
    cursor = connection.cursor()
    message = ""
    try:
        select_query = "SELECT id, is_active FROM users WHERE phone_number = %s"
        cursor.execute(select_query, (phone_number,))
        result = cursor.fetchone()

        if result:
            user_id, is_active = result
            if is_active == 0:
                update_query = "UPDATE users SET is_active = 1, role = %s, name = %s WHERE id = %s"
                cursor.execute(update_query, (role, name, user_id))
                message = "Пользователь активирован повторно."
            else:
                message = "Пользователь уже существует и активен."
        else:
            insert_query = "INSERT INTO users (phone_number, role, name, is_active) VALUES (%s, %s, %s, 1)"
            cursor.execute(insert_query, (phone_number, role, name))
            message = "Пользователь добавлен успешно."
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
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

def deactivate_user_in_db(user_id_to_deactivate): # Changed to accept user_id
    logger.debug(f"Deactivating user with ID: {user_id_to_deactivate}")
    connection = create_connection()
    if not connection:
        return "Ошибка: не удалось подключиться к базе данных."

    cursor = connection.cursor()
    response = ""
    try:
        # Check if user exists and is active
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
        # Return a status or raise a specific error
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

<<<<<<< HEAD
def update_user_phone(user_id: int, new_phone_number_raw: str) -> tuple[bool, str]:
    normalized_new_phone = _normalize_phone_for_db(new_phone_number_raw)
    if not normalized_new_phone:
        return False, f"Ошибка: неверный формат нового номера телефона '{new_phone_number_raw}'. Не удалось нормализовать."

    logger.debug(f"Updating phone for user_id: {user_id} to normalized_new_phone: {normalized_new_phone}")
=======
def update_user_phone(user_id, new_phone_number):
    logger.debug(f"Updating phone for user_id: {user_id} to new_phone: {new_phone_number}")
>>>>>>> effa315 (Implement stock management functionality with add and restock product flows, including state management and stock report generation.)
    connection = create_connection()
    if not connection:
        return False, "Ошибка подключения к БД."

    cursor = connection.cursor(dictionary=True)
    try:
        # Check if the new phone number is already in use by another active user
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
            logger.debug("Database connection closed.")


def create_buyback_record(product_article, price, payment_type, user_id, store_id):
    logger.debug(f"Creating buyback record for article: {product_article}, price: {price}, user_id: {user_id}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True) # Using dictionary cursor
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
        buyback_id = None
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

<<<<<<< HEAD
def create_trade_in_record(old_product_article: str, new_product_article: str, price_difference: float, payment_type_difference: str, user_id: int, store_id: int) -> tuple[int | None, str]:
=======
def create_trade_in_record(old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id):
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
        trade_in_id = None
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return trade_in_id