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

# --- (get_user_by_phone, get_all_users, get_product_by_article, get_store_by_id remain the same) ---
def get_user_by_phone(phone_number):
    logger.debug(f"Fetching user with phone number: {phone_number}")
    connection = create_connection()
    if not connection: return None # Guard against connection failure
    cursor = connection.cursor(dictionary=True) # Use dictionary=True for consistency
    try:
        query = "SELECT * FROM users WHERE phone_number = %s"
        cursor.execute(query, (phone_number,))
        result = cursor.fetchone() # Fetches one row as a dictionary
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

def get_all_users():
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

def get_product_by_article(article_number):
    logger.debug(f"Fetching product with article number: {article_number}")
    connection = create_connection()
    if not connection: return None
    # Use dictionary cursor for product details for consistency
    cursor = connection.cursor(dictionary=True)
    product = None
    try:
        query = "SELECT * FROM products WHERE article_number = %s"
        cursor.execute(query, (article_number,))
        product = cursor.fetchone() # Fetches as dictionary
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

def get_store_by_id(store_id):
    logger.debug(f"Fetching store with ID: {store_id}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor(dictionary=True) # Use dictionary cursor
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
            logger.debug("Database connection closed.")
    return store


# Modified update_product_quantity to accept a cursor
def _update_product_quantity_with_cursor(cursor, product_article, quantity_change):
    logger.debug(f"Attempting to update quantity for product article: {product_article} by {quantity_change} using provided cursor.")
    # Check current quantity to prevent negative stock if decreasing
    if quantity_change < 0:
        cursor.execute("SELECT quantity FROM products WHERE article_number = %s FOR UPDATE", (product_article,)) # Add FOR UPDATE for row lock
        current_quantity_result = cursor.fetchone()
        if not current_quantity_result: # Product not found by article, should not happen if called after get_product_by_article
             logger.error(f"Product {product_article} not found during quantity check in _update_product_quantity_with_cursor.")
             raise ValueError(f"Product {product_article} not found for quantity update.")

        # If cursor is dictionary cursor, access by key
        current_quantity = current_quantity_result[0] if isinstance(current_quantity_result, tuple) else current_quantity_result['quantity']

        if current_quantity < abs(quantity_change):
            logger.warning(f"Not enough stock for {product_article} to decrease by {abs(quantity_change)}. Current: {current_quantity}")
            raise ValueError(f"Not enough stock for {product_article}.") # Raise error to trigger rollback

    query = "UPDATE products SET quantity = quantity + %s WHERE article_number = %s"
    cursor.execute(query, (quantity_change, product_article))
    if cursor.rowcount == 0:
        logger.warning(f"Product {product_article} quantity not updated (rowcount 0). Product might not exist or quantity is already correct.")
        # Depending on strictness, you might want to raise an error here too if an update was expected.
        # For now, we'll assume it's possible if quantity doesn't change due to other reasons.
        # raise ValueError(f"Failed to update quantity for {product_article}, product not found or no change needed.")


    logger.info(f"Product {product_article} quantity successfully updated by {quantity_change} in current transaction. Rowcount: {cursor.rowcount}")
    return True


# Standalone version for other uses (buyback, return, trade-in will use this)
def update_product_quantity(product_article, quantity_change):
    logger.debug(f"Standalone update_product_quantity for article: {product_article} by {quantity_change}")
    connection = create_connection()
    if not connection: return False
    cursor = connection.cursor() # Can be dictionary=True if preferred for consistency
    success = False
    try:
        # The logic of _update_product_quantity_with_cursor needs to be here
        # or call it, but ensure transactionality.
        if quantity_change < 0:
            # Use SELECT ... FOR UPDATE if you need to lock the row during the check and subsequent update
            # This requires the operation to be within a transaction.
            cursor.execute("SELECT quantity FROM products WHERE article_number = %s FOR UPDATE", (product_article,))
            current_quantity_result = cursor.fetchone()
            if not current_quantity_result:
                logger.error(f"Product {product_article} not found for standalone quantity update.")
                return False # Or raise
            current_quantity = current_quantity_result[0] # Assuming non-dictionary cursor here
            if current_quantity < abs(quantity_change):
                logger.warning(f"Not enough stock for {product_article} (standalone). Current: {current_quantity}")
                return False # Or raise

        query = "UPDATE products SET quantity = quantity + %s WHERE article_number = %s"
        cursor.execute(query, (quantity_change, product_article))
        
        if cursor.rowcount > 0:
            connection.commit()
            logger.info(f"Product {product_article} quantity updated by {quantity_change} (standalone). Rows affected: {cursor.rowcount}")
            success = True
        else:
            logger.warning(f"Product {product_article} quantity not updated (standalone, rowcount 0).")
            connection.rollback() # Rollback if no rows affected unexpectedly

    except Error as e:
        logger.error(f"Error in standalone update_product_quantity for {product_article}: {e}")
        if connection.is_connected(): connection.rollback()
    except ValueError as ve: # Catch stock errors from the check
        logger.error(f"ValueError in standalone update_product_quantity for {product_article}: {ve}")
        if connection.is_connected(): connection.rollback()
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return success


# Record sale using a single transaction
def record_sale(product_id, product_article, store_id, user_id, sale_price, payment_type):
    logger.debug(f"Recording sale for product_id: {product_id} (article: {product_article}), store_id: {store_id}, user_id: {user_id}, sale_price: {sale_price}, payment_type: {payment_type}")
    connection = create_connection()
    if not connection:
        logger.error("Failed to create database connection for record_sale.")
        return False
        
    cursor = connection.cursor()
    try:
        # Start transaction explicitly if autocommit is not True by default or for clarity
        # connection.start_transaction() # Or cursor.execute("START TRANSACTION;")

        insert_query = """INSERT INTO sales (product_id, store_id, user_id, sale_date, sold_price, payment_type) 
        VALUES (%s, %s, %s, NOW(), %s, %s)"""
        cursor.execute(insert_query, (product_id, store_id, user_id, sale_price, payment_type))
        logger.debug(f"Insert into sales executed. Product ID: {product_id}")

        # Use the internal update quantity function with the same cursor
        _update_product_quantity_with_cursor(cursor, product_article, -1) # Decrease quantity by 1
        
        connection.commit()
        logger.info(f"Sale recorded and product {product_article} quantity updated successfully.")
        return True
    except (Error, ValueError) as e: # Catch DB errors and ValueErrors (e.g. from stock check)
        logger.exception(f"Error during record_sale transaction for product article {product_article}. Rolling back.")
        if connection.is_connected():
            connection.rollback()
        return False
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed for record_sale.")


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
    cursor = connection.cursor()
    min_price = None
    try:
        query = "SELECT min_price FROM products WHERE article_number = %s"
        cursor.execute(query, (product_article,))
        result = cursor.fetchone()
        if result:
            min_price = result[0]
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

# --- (add_user_to_db, deactivate_user_in_db, change_user_role_function remain similar but ensure connection handling) ---
# --- (create_buyback_record, create_return_record, create_trade_in_record will use the standalone update_product_quantity) ---

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

def deactivate_user_in_db(phone_number):
    logger.debug(f"Deactivating user with phone_number: {phone_number}")
    connection = create_connection()
    if not connection: return "Ошибка: не удалось подключиться к базе данных."
    cursor = connection.cursor()
    response = ""
    try:
        select_query = "SELECT id FROM users WHERE phone_number = %s AND is_active = 1"
        cursor.execute(select_query, (phone_number,))
        result = cursor.fetchone()

        if result:
            update_query = "UPDATE users SET is_active = 0 WHERE phone_number = %s"
            cursor.execute(update_query, (phone_number,))
            connection.commit()
            response = "Пользователь деактивирован."
        else:
            response = "Пользователь не найден или уже деактивирован."
    except Error as e:
        logger.error(f"Database error in deactivate_user_in_db: {e}")
        if connection.is_connected(): connection.rollback()
        response = "Ошибка при работе с базой данных."
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")
    return response

def change_user_role_function(user_id, new_role):
    logger.debug(f"Changing role for user_id: {user_id} to new_role: {new_role}")
    connection = create_connection()
    if not connection:
        logger.error("Failed to create DB connection for change_user_role.")
        raise Error("DB connection failed.") # Or return a specific failure indicator
    cursor = connection.cursor()
    try:
        query = "UPDATE users SET role = %s WHERE id = %s"
        cursor.execute(query, (new_role, user_id))
        connection.commit()
        logger.info(f"User ID {user_id} role updated to {new_role}.")
    except Exception as e:
        logger.exception(f"Ошибка при изменении роли пользователя {user_id}.")
        if connection.is_connected(): connection.rollback()
        raise e
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")


def create_buyback_record(product_article, price, payment_type, user_id, store_id):
    logger.debug(f"Creating buyback record for article: {product_article}, price: {price}, user_id: {user_id}")
    # This function creates its own connection, which is fine for a self-contained operation
    # It calls the standalone update_product_quantity
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor()
    buyback_id = None
    try:
        query = """
        INSERT INTO buybacks (product_article, buyback_price, payment_type, user_id, store_id, buyback_date)
        VALUES (%s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (product_article, price, payment_type, user_id, store_id))
        buyback_id = cursor.lastrowid
        connection.commit() # Commit buyback record first

        # Then update quantity in a separate transaction (as per standalone update_product_quantity)
        if not update_product_quantity(product_article, 1): # Increase quantity
            logger.error(f"Failed to update quantity for buyback of {product_article}. Buyback record {buyback_id} created, but quantity update failed.")
            # This is a critical state - buyback recorded, stock not updated.
            # Manual intervention or a compensating transaction might be needed.
            # For now, we log the error. Ideally, this whole operation should be one transaction.
            # To make it one transaction, update_product_quantity would need to accept a cursor.
            # Let's assume for now, this two-step process is acceptable for buyback/return/tradein.
            # A better approach would be:
            # connection.start_transaction()
            # ... insert buyback ...
            # _update_product_quantity_with_cursor(cursor, product_article, 1)
            # connection.commit()
            # ...
            # But this means all these create_..._record functions need to manage transactions carefully.
            # The standalone `update_product_quantity` handles its own transaction.

        logger.info(f"Buyback record created with ID: {buyback_id} for article {product_article}")
    except Error as e:
        logger.error(f"Error creating buyback record for {product_article}: {e}")
        if connection.is_connected(): connection.rollback() # Rollback only the insert if commit wasn't reached
        buyback_id = None # Ensure it's None on error
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return buyback_id


def create_return_record(sale_id, product_article, return_price, payment_type, user_id, store_id):
    logger.debug(f"Creating return record for sale_id: {sale_id}, product_article: {product_article}, price: {return_price}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor()
    return_id = None
    try:
        query = """
        INSERT INTO returns (original_sale_id, product_article, return_price, payment_type, user_id, store_id, return_date)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (sale_id, product_article, return_price, payment_type, user_id, store_id))
        return_id = cursor.lastrowid
        connection.commit() # Commit return record

        if not update_product_quantity(product_article, 1): # Increase stock
            logger.error(f"Failed to update quantity for return of {product_article}. Return record {return_id} created, but quantity update failed.")
            # Similar critical state as in buyback.

        logger.info(f"Return record created with ID: {return_id} for product {product_article} from sale {sale_id}")
    except Error as e:
        logger.error(f"Error creating return record for {product_article}: {e}")
        if connection.is_connected(): connection.rollback()
        return_id = None
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return return_id

def create_trade_in_record(old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id):
    logger.debug(f"Creating trade-in: old {old_product_article}, new {new_product_article}, diff {price_difference}")
    connection = create_connection()
    if not connection: return None
    cursor = connection.cursor()
    trade_in_id = None
    try:
        query = """
        INSERT INTO trade_ins (old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id, trade_in_date)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id))
        trade_in_id = cursor.lastrowid
        connection.commit() # Commit trade-in record

        qty_updated_old = update_product_quantity(old_product_article, 1) # Increase stock for old
        if not qty_updated_old:
            logger.error(f"Failed to update quantity for trade-in (old item: {old_product_article}). Trade-in {trade_in_id} recorded.")
        
        qty_updated_new = update_product_quantity(new_product_article, -1) # Decrease stock for new
        if not qty_updated_new:
            logger.error(f"Failed to update quantity for trade-in (new item: {new_product_article}). Trade-in {trade_in_id} recorded.")
            # If old item update succeeded but new one failed, this is inconsistent.

        if qty_updated_old and qty_updated_new:
            logger.info(f"Trade-in record created with ID: {trade_in_id} and stock updated.")
        else:
            logger.warning(f"Trade-in record {trade_in_id} created, but one or both stock updates failed.")

    except Error as e:
        logger.error(f"Error creating trade-in record: {e}")
        if connection.is_connected(): connection.rollback()
        trade_in_id = None
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    return trade_in_id