import mysql.connector
from config import DB_HOST, DB_USER, DB_PASS, DB_NAME
from mysql.connector import Error
import logging


logger = logging.getLogger(__name__)

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
        logger.debug("Database connection established.")
    except Error as e:
        logger.exception(f"The error '{e}' occurred while connecting to the database.")
    return connection

def get_user_by_phone(phone_number):
    logger.debug(f"Fetching user with phone number: {phone_number}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    try:
        query = "SELECT * FROM users WHERE phone_number = %s"
        cursor.execute(query, (phone_number,))
        result = cursor.fetchone()
        if result:
            logger.debug(f"Результат запроса для {phone_number}: {result}")
            return result
        else:
            logger.debug(f"Пользователь с телефоном {phone_number} не найден.")
            return None
    except Exception as e:
        logger.exception("Error executing get_user_by_phone query.")
        return None
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")

def get_all_users(): # Used by UserManagement "Права"
    connection = create_connection()
    if not connection: return []
    cursor = connection.cursor(dictionary=True)
    try:
        # Consider if you want to list ALL users (including inactive for reactivation)
        # or just active ones. For "Права" on active users, is_active = 1 is good.
        query = "SELECT id, phone_number, name, role FROM users WHERE is_active = 1"
        cursor.execute(query)
        users = cursor.fetchall()
        return users
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

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
            logger.debug("Database connection closed for get_product_by_article.")
    return product

def get_sales_summary_data(start_date, end_date):
    """
    Generates a summary of sales data for a given period, including overall totals
    and a breakdown by payment type.
    Returns a dictionary with 'overall_summary' and 'payment_type_breakdown'.
    """
    logger.debug(f"Generating sales summary for {start_date} to {end_date}")
    connection = create_connection()
    if not connection: 
        logger.error("Failed to create database connection for get_sales_summary_data.")
        return None

    cursor = connection.cursor(dictionary=True)
    summary_data = {
        'overall_summary': {},
        'payment_type_breakdown': []
    }
    try:
        # Overall Summary
        overall_query = """
        SELECT
            COUNT(s.id) AS total_sales_count,
            SUM(s.sold_price) AS total_sales_amount,
            AVG(s.sold_price) AS average_sale_price
        FROM sales s
        WHERE s.sale_date BETWEEN %s AND %s;
        """
        cursor.execute(overall_query, (start_date, end_date))
        summary_data['overall_summary'] = cursor.fetchone()
        logger.debug(f"Overall sales summary: {summary_data['overall_summary']}")

        # Payment Type Breakdown
        payment_type_query = """
        SELECT
            s.payment_type,
            COUNT(s.id) AS payment_type_sales_count,
            SUM(s.sold_price) AS payment_type_sales_amount
        FROM sales s
        WHERE s.sale_date BETWEEN %s AND %s
        GROUP BY s.payment_type
        ORDER BY s.payment_type;
        """
        cursor.execute(payment_type_query, (start_date, end_date))
        summary_data['payment_type_breakdown'] = cursor.fetchall()
        logger.debug(f"Payment type breakdown: {summary_data['payment_type_breakdown']}")

    except Error as e:
        logger.exception(f"Error generating sales summary: {e}")
        return None
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed after generating sales summary.")
    return summary_data

def get_returns_summary_data(start_date, end_date):
    """Generates a summary of returns data for a given period."""
    logger.debug(f"Generating returns summary for {start_date} to {end_date}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    summary = {}
    try:
        query = """
        SELECT
            COUNT(id) AS total_returns_count,
            SUM(return_price) AS total_returns_amount
        FROM returns
        WHERE return_date BETWEEN %s AND %s;
        """
        cursor.execute(query, (start_date, end_date))
        summary = cursor.fetchone()
    except Error as e:
        logger.exception(f"Error generating returns summary: {e}")
        return None
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return summary

def get_buybacks_summary_data(start_date, end_date):
    """Generates a summary of buybacks data for a given period."""
    logger.debug(f"Generating buybacks summary for {start_date} to {end_date}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    summary = {}
    try:
        query = """
        SELECT
            COUNT(id) AS total_buybacks_count,
            SUM(buyback_price) AS total_buybacks_amount
        FROM buybacks
        WHERE buyback_date BETWEEN %s AND %s;
        """
        cursor.execute(query, (start_date, end_date))
        summary = cursor.fetchone()
    except Error as e:
        logger.exception(f"Error generating buybacks summary: {e}")
        return None
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return summary

def get_low_stock_products(threshold):
    """
    Fetches products with quantity less than or equal to the threshold
    and where track_stock is enabled.
    """
    logger.debug(f"Fetching products with quantity <= {threshold} and track_stock = 1.")
    connection = create_connection()
    if not connection: return []
    cursor = connection.cursor(dictionary=True)
    low_stock_items = []
    try:
        query = """
        SELECT article_number, model, quantity, Store
        FROM products
        WHERE quantity <= %s AND track_stock = 1
        ORDER BY Store, model;
        """
        cursor.execute(query, (threshold,))
        low_stock_items = cursor.fetchall()
        logger.info(f"Found {len(low_stock_items)} low stock items.")
    except Error as e:
        logger.exception(f"Error fetching low stock products: {e}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return low_stock_items

def find_sales_by_article_and_date(article_number, sale_date):
    """Finds all sales for a given article on a specific date."""
    logger.debug(f"Searching for sales with article '{article_number}' on date '{sale_date}'")
    connection = create_connection()
    if not connection: return []
    cursor = connection.cursor(dictionary=True)
    sales_list = []
    try:
        # This query joins sales and products to find sales by article number and date.
        # It retrieves all necessary details to present to the user or proceed.
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
        WHERE p.article_number = %s AND DATE(s.sale_date) = %s
        ORDER BY s.sale_date ASC
        """
        cursor.execute(query, (article_number, sale_date))
        sales_list = cursor.fetchall()
        logger.info(f"Found {len(sales_list)} sales for article '{article_number}' on {sale_date}.")
    except Error as e:
        logger.error(f"Error finding sales for article {article_number} on {sale_date}: {e}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return sales_list

def get_sale_details_by_id(sale_id):
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
            logger.debug(f"Database connection closed for get_sale_details_by_id (sale_id: {sale_id}).")
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
        logger.warning(f"Product {product_article} quantity not updated (rowcount 0), but an update was expected for change {quantity_change}.")
        raise ValueError(f"Не удалось обновить количество для {product_article}, товар не найден или количество не изменилось.")
    logger.info(f"Product {product_article} quantity successfully updated by {quantity_change} in current transaction. Rowcount: {cursor.rowcount}")
    return True

# Standalone function for updating quantity, creates its own transaction
def update_product_quantity(product_article, quantity_change): 
    logger.debug(f"Standalone update_product_quantity for article: {product_article} by {quantity_change}")
    connection = create_connection()
    if not connection: return False, "Ошибка подключения к БД."
    cursor = connection.cursor(dictionary=True)
    success = False
    message = ""
    try:
        # We need to ensure the product exists, especially for increasing quantity.
        # If a product with article_number does not exist, this update will do nothing (rowcount 0).
        # For increasing stock (e.g. poполнить остатки), we expect the product to exist.
        cursor.execute("SELECT quantity FROM products WHERE article_number = %s FOR UPDATE", (product_article,))
        product_exists = cursor.fetchone()

        if not product_exists:
            logger.warning(f"Product {product_article} not found for stock update.")
            return False, f"Товар с артикулом {product_article} не найден."

        if quantity_change < 0:
            current_quantity = product_exists['quantity']
            if current_quantity is None:
                logger.error(f"Product {product_article} quantity is NULL (standalone).")
                return False, f"У продукта {product_article} не указано количество (NULL)."
            if current_quantity < abs(quantity_change):
                logger.warning(f"Not enough stock for {product_article} (standalone). Current: {current_quantity}")
                return False, f"Недостаточно товара {product_article} на складе ({current_quantity} шт.) для списания {abs(quantity_change)} шт."
        
        query = "UPDATE products SET quantity = quantity + %s WHERE article_number = %s"
        cursor.execute(query, (quantity_change, product_article))
        
        if cursor.rowcount > 0 or quantity_change == 0:
            connection.commit()
            logger.info(f"Product {product_article} quantity updated by {quantity_change} (standalone). Rows affected: {cursor.rowcount}")
            success = True
            message = f"Остаток товара {product_article} изменен на {quantity_change} шт."
        else:
            # This case (rowcount 0 for non-zero change) implies product_article didn't match AFTER the FOR UPDATE lock.
            # Should be rare if product_exists check passed.
            logger.warning(f"Product {product_article} quantity not updated (standalone, rowcount 0 for non-zero change).")
            connection.rollback()
            message = f"Не удалось обновить остаток для {product_article} (изменение не было применено)."
            
    except Error as e:
        logger.error(f"Error in standalone update_product_quantity for {product_article}: {e}")
        if connection.is_connected(): connection.rollback()
        message = f"Ошибка БД при обновлении остатка: {e}"
    except ValueError as ve:
        logger.error(f"ValueError in standalone update_product_quantity for {product_article}: {ve}")
        if connection.is_connected(): connection.rollback()
        message = str(ve) # Use the message from ValueError (e.g., "Not enough stock...")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return success, message


def record_sale(product_id, product_article, user_id, sale_price, payment_type):
    # ... (remains the same, uses _update_product_quantity_with_cursor) ...
    logger.debug(f"Recording sale for product_id: {product_id} (article: {product_article}), user_id: {user_id}, sale_price: {sale_price}, payment_type: {payment_type}")
    connection = create_connection()
    if not connection:
        logger.error("Failed to create database connection for record_sale.")
        return False, "Ошибка подключения к БД."
        
    cursor = connection.cursor(dictionary=True)
    try:
        insert_query = """INSERT INTO sales (product_id, user_id, sale_date, sold_price, payment_type) 
        VALUES (%s, %s, NOW(), %s, %s)"""
        cursor.execute(insert_query, (product_id, user_id, sale_price, payment_type))
        logger.debug(f"Insert into sales executed. Product ID: {product_id}")
        _update_product_quantity_with_cursor(cursor, product_article, -1)
        connection.commit()
        logger.info(f"Sale recorded and product {product_article} quantity updated successfully.")
        return True, "Продажа успешно записана."
    except (Error, ValueError) as e:
        logger.exception(f"Error during record_sale transaction for product article {product_article}. Rolling back.")
        if connection.is_connected():
            connection.rollback()
        return False, f"Ошибка записи продажи: {e}"
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed for record_sale.")

def get_product_article_by_id(product_id):
    # ... (remains the same) ...
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
    # ... (remains the same) ...
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
    # ... (remains the same) ...
    logger.debug(f"Generating report from {start_date} to {end_date}")
    connection = create_connection()
    if not connection: return []
    cursor = connection.cursor() # Using default tuple cursor as report handler expects it
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
                    p.Store as product_store_name -- Use Store from products table
                FROM sales s
                JOIN products p ON s.product_id = p.id
                JOIN users u ON s.user_id = u.id
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

def add_user_to_db(phone_number, role, name, store_id=None): # Added store_id, defaulting to None
    logger.debug(f"Adding user to database with phone_number: {phone_number}, role: {role}, name: {name}, store_id: {store_id}")
    connection = create_connection()
    if not connection: return "Ошибка: не удалось подключиться к базе данных."
    cursor = connection.cursor()
    message = ""
    try:
        # Check if phone number already exists
        select_query_phone = "SELECT id, is_active FROM users WHERE phone_number = %s"
        cursor.execute(select_query_phone, (phone_number,))
        result_phone = cursor.fetchone()

        if result_phone:
            user_id, is_active = result_phone
            if is_active == 0: # User exists but is inactive, reactivate and update
                update_query = "UPDATE users SET is_active = 1, role = %s, name = %s, store_id = %s WHERE id = %s"
                cursor.execute(update_query, (role, name, store_id, user_id))
                message = "Пользователь активирован повторно и данные обновлены."
            else: # User exists and is active
                message = "Пользователь с таким номером телефона уже существует и активен."
                # Optionally, update their details if name/role/store_id differ?
                # For now, just inform.
        else: # New user
            # Default store_id to a sensible value if not provided, e.g., 1, or make it mandatory
            if store_id is None: store_id = 1 # Example default, adjust as needed
            insert_query = "INSERT INTO users (phone_number, role, name, store_id, is_active) VALUES (%s, %s, %s, %s, 1)"
            cursor.execute(insert_query, (phone_number, role, name, store_id))
            message = "Пользователь добавлен успешно."
        connection.commit()
    except Error as e:
        logger.error(f"Database error in add_user_to_db: {e}")
        if connection.is_connected(): connection.rollback()
        message = "Ошибка при работе с базой данных."
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")
    return message

def deactivate_user_in_db(user_id_to_deactivate):
    # ... (remains the same) ...
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
            logger.debug("Database connection closed.")
    return response


def change_user_role_function(user_id, new_role):
    # ... (remains the same) ...
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
            logger.debug("Database connection closed.")

def update_user_phone(user_id, new_phone_number):
    # ... (remains the same) ...
    logger.debug(f"Updating phone for user_id: {user_id} to new_phone: {new_phone_number}")
    connection = create_connection()
    if not connection:
        return False, "Ошибка подключения к БД."
    cursor = connection.cursor(dictionary=True)
    try:
        check_query = "SELECT id, name FROM users WHERE phone_number = %s AND id != %s AND is_active = 1"
        cursor.execute(check_query, (new_phone_number, user_id))
        existing_user = cursor.fetchone()
        if existing_user:
            logger.warning(f"New phone {new_phone_number} already used by user ID {existing_user['id']} ({existing_user['name']}).")
            return False, f"Номер {new_phone_number} уже используется другим активным пользователем ({existing_user['name']})."

        update_query = "UPDATE users SET phone_number = %s WHERE id = %s"
        cursor.execute(update_query, (new_phone_number, user_id))
        if cursor.rowcount == 0:
            connection.rollback()
            logger.warning(f"User ID {user_id} not found for phone update, or phone number unchanged.")
            return False, f"Пользователь ID {user_id} не найден, или номер не изменен."
        
        connection.commit()
        logger.info(f"User ID {user_id} phone updated to {new_phone_number}.")
        return True, f"Номер телефона пользователя ID {user_id} изменен на {new_phone_number}."
    except Error as e:
        logger.exception(f"Database error updating phone for user ID {user_id}.")
        if connection.is_connected(): connection.rollback()
        return False, "Ошибка базы данных при изменении номера."
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")

def create_buyback_record(product_article, price, payment_type, user_id, store_id):
    # ... (remains the same - uses single transaction with _update_product_quantity_with_cursor) ...
    logger.debug(f"Creating buyback record for article: {product_article}, price: {price}, user_id: {user_id}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    buyback_id = None
    try:
        connection.start_transaction()
        query = """
        INSERT INTO buybacks (product_article, buyback_price, payment_type, user_id, store_id, buyback_date)
        VALUES (%s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (product_article, price, payment_type, user_id, store_id))
        buyback_id = cursor.lastrowid
        _update_product_quantity_with_cursor(cursor, product_article, 1)
        connection.commit()
        logger.info(f"Buyback record created with ID: {buyback_id} and stock updated for article {product_article}")
    except (Error, ValueError) as e:
        logger.error(f"Error during buyback transaction for {product_article}: {e}")
        if connection.is_connected(): connection.rollback() 
        buyback_id = None 
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return buyback_id

def create_return_record(sale_id, product_article, return_price, payment_type, user_id, store_id):
    # ... (remains the same - uses single transaction) ...
    logger.debug(f"Creating return record for sale_id: {sale_id}, product_article: {product_article}, price: {return_price}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    return_id = None
    try:
        connection.start_transaction()
        query = """
        INSERT INTO returns (original_sale_id, product_article, return_price, payment_type, user_id, store_id, return_date)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (sale_id, product_article, return_price, payment_type, user_id, store_id))
        return_id = cursor.lastrowid
        _update_product_quantity_with_cursor(cursor, product_article, 1)
        connection.commit()
        logger.info(f"Return record created with ID: {return_id} and stock updated for product {product_article} from sale {sale_id}")
    except (Error, ValueError) as e:
        logger.error(f"Error during return transaction for {product_article}: {e}")
        if connection.is_connected(): connection.rollback()
        return_id = None
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return return_id

def create_trade_in_record(old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id):
    # ... (remains the same - uses single transaction) ...
    logger.debug(f"Creating trade-in: old {old_product_article}, new {new_product_article}, diff {price_difference}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True)
    trade_in_id = None
    try:
        connection.start_transaction()
        query = """
        INSERT INTO trade_ins (old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id, trade_in_date)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id))
        trade_in_id = cursor.lastrowid
        _update_product_quantity_with_cursor(cursor, old_product_article, 1)
        _update_product_quantity_with_cursor(cursor, new_product_article, -1)
        connection.commit()
        logger.info(f"Trade-in record created with ID: {trade_in_id} and stock updated.")
    except (Error, ValueError) as e:
        logger.error(f"Error during trade-in transaction (old: {old_product_article}, new: {new_product_article}): {e}")
        if connection.is_connected(): connection.rollback()
        trade_in_id = None
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return trade_in_id

def create_cash_withdrawal_record(amount, comment, user_id, store_id):
    """
    Records a cash withdrawal (encashment) event in the database.
    """
    logger.debug(f"Creating cash withdrawal record: amount={amount}, user_id={user_id}, store_id={store_id}")
    connection = create_connection()
    if not connection:
        return False, "Ошибка подключения к БД."
    
    cursor = connection.cursor()
    try:
        query = """
        INSERT INTO cash_withdrawals (amount, comment, user_id, store_id, withdrawal_date)
        VALUES (%s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (amount, comment, user_id, store_id))
        connection.commit()
        withdrawal_id = cursor.lastrowid
        logger.info(f"Cash withdrawal record created with ID: {withdrawal_id}")
        return True, "Инкассация успешно зарегистрирована."
    except Error as e:
        logger.exception(f"Database error during cash withdrawal: {e}")
        if connection.is_connected(): connection.rollback()
        return False, f"Ошибка БД при регистрации инкассации: {e}"
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

# --- New functions for Stock Management ---
def add_product_to_db(product_data: dict):
    logger.info(f"Attempting to add new product: {product_data.get('article_number')}")
    connection = create_connection()
    if not connection:
        return False, "Ошибка подключения к БД."
    cursor = connection.cursor(dictionary=True)
    try:
        # Check if article_number already exists
        cursor.execute("SELECT id FROM products WHERE article_number = %s", (product_data['article_number'],))
        if cursor.fetchone():
            logger.warning(f"Product with article_number {product_data['article_number']} already exists.")
            return False, f"Товар с артикулом {product_data['article_number']} уже существует."

        # Ensure all required fields are present and have defaults if necessary
        # Assuming product_data contains all necessary keys from the products table structure
        # Changed 'store_id' to 'Store'
        required_fields = ['Store', 'article_number', 'metal_type', 'product_type', 'model', 'track_stock', # Added track_stock
                           'weight', 'price_per_gram', 'price', 'min_price', 'quantity', 'manufacturer']
        for field in required_fields:
            if field not in product_data or product_data[field] is None:
                # Allow quantity to be 0, but not None for other numeric fields if they are NOT NULL
                if field == 'quantity' and product_data.get(field) is None:
                    product_data[field] = 0 # Default quantity to 0
                elif field not in ['commentaries', 'arrival_date'] : # commentaries and arrival_date can be optional or handled
                    logger.error(f"Missing required field '{field}' for new product {product_data['article_number']}.")
                    return False, f"Отсутствует обязательное поле: {field.replace('Store', 'Магазин')}"
        
        # Set arrival_date to now if not provided
        product_data.setdefault('arrival_date', Error) # Placeholder, will use NOW() in SQL

        cols = []
        vals_placeholder = []
        vals = []
        for key, value in product_data.items():
            if key == 'arrival_date' and value == Error: # Special handling for NOW()
                cols.append(f"`{key}`")
                vals_placeholder.append("NOW()")
            else:
                cols.append(f"`{key}`") # Backticks for safety
                vals_placeholder.append("%s")
                vals.append(value)
        
        query = f"INSERT INTO products ({', '.join(cols)}) VALUES ({', '.join(vals_placeholder)})"
        
        logger.debug(f"Add product query: {query}, values: {vals}")
        cursor.execute(query, vals)
        connection.commit()
        product_id = cursor.lastrowid
        logger.info(f"Product {product_data['article_number']} added successfully with ID {product_id}.")
        return True, f"Товар {product_data['article_number']} ({product_data.get('model', '')}) успешно добавлен."

    except Error as e:
        logger.exception(f"Database error adding product {product_data.get('article_number')}: {e}")
        if connection.is_connected(): connection.rollback()
        return False, f"Ошибка БД при добавлении товара: {e}"
    except Exception as ex: # Catch other errors like KeyError
        logger.exception(f"General error adding product {product_data.get('article_number')}: {ex}")
        if connection.is_connected(): connection.rollback()
        return False, f"Общая ошибка при добавлении товара: {ex}"
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def get_stock_report_data(): # Removed store_id filter
    logger.info(f"Generating stock report data for all stores.")
    connection = create_connection()
    if not connection: return []
    cursor = connection.cursor(dictionary=True) # Return as dicts
    try:
        base_query = """
            SELECT 
                p.article_number, p.product_type, p.model, p.metal_type, p.Store as store_name,
                p.weight, p.quantity, p.price, p.min_price, 
                p.manufacturer, p.arrival_date, p.commentaries
            FROM products p
            ORDER BY p.Store, p.product_type, p.model, p.article_number """
        cursor.execute(base_query)
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

def get_enum_or_distinct_values(table_name, column_name, add_other_option=True):
    logger.debug(f"Fetching ENUM/distinct values for {table_name}.{column_name}")
    connection = create_connection()
    if not connection: return []
    cursor = connection.cursor(dictionary=True) # Use dictionary cursor to get column info by name
    values_list = []
    try:
        # First, try to get ENUM values if the column is ENUM
        query_schema = f"SHOW COLUMNS FROM `{table_name}` LIKE %s"
        cursor.execute(query_schema, (column_name,))
        column_info = cursor.fetchone()

        if column_info and 'Type' in column_info:
            type_def = column_info['Type']
            if type_def.lower().startswith("enum(") and type_def.endswith(")"):
                values_str = type_def[len("enum("):-1]
                values_list = [val.strip("'") for val in values_str.split(',')]
                logger.info(f"Successfully fetched ENUM values for {table_name}.{column_name}: {values_list}")
            else:
                # If not ENUM, fallback to fetching distinct values
                logger.debug(f"{table_name}.{column_name} is not ENUM, fetching distinct values.")
                # Ensure cursor is reset or use a new one if necessary, though it should be fine
                cursor_distinct = connection.cursor() # Default cursor for simple list
                query_distinct = f"SELECT DISTINCT `{column_name}` FROM `{table_name}` WHERE `{column_name}` IS NOT NULL AND `{column_name}` != '' ORDER BY `{column_name}`"
                cursor_distinct.execute(query_distinct)
                results_distinct = cursor_distinct.fetchall()
                values_list = [row[0] for row in results_distinct]
                logger.info(f"Successfully fetched {len(values_list)} distinct values for {table_name}.{column_name}: {values_list}")
                cursor_distinct.close()
        else:
            logger.warning(f"Could not retrieve column info for {table_name}.{column_name}")

        if add_other_option and "Другое" not in values_list:
            values_list.append("Другое")
        
        values_list = sorted(list(set(values_list))) # Ensure uniqueness and sort

    except Error as e:
        logger.exception(f"Error fetching ENUM/distinct values for {table_name}.{column_name}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug(f"Database connection closed after fetching values for {table_name}.{column_name}")
    return values_list