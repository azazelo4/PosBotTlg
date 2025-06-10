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

# Get a user by their phone number
def get_user_by_phone(phone_number):
    logger.debug(f"Fetching user with phone number: {phone_number}")
    connection = create_connection()
    cursor = connection.cursor()
    try:
        # Выполняем запрос, получая все столбцы
        query = "SELECT * FROM users WHERE phone_number = %s"
        cursor.execute(query, (phone_number,))
        logger.debug(f"Query executed: {query} with phone_number={phone_number}")
        result = cursor.fetchone()

        if result:
            # Извлекаем имена столбцов из cursor.description
            columns = [desc[0] for desc in cursor.description]
            user_data_dict = dict(zip(columns, result))  # Преобразуем результат в словарь
            logger.debug(f"Результат запроса для {phone_number}: {user_data_dict}")
            return user_data_dict  # Возвращаем словарь
        else:
            logger.debug(f"Пользователь с телефоном {phone_number} не найден.")
            return None
    except Exception as e:
        logger.exception("Error executing get_user_by_phone query.")
        return None
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")

def get_all_users():
    connection = create_connection()
    cursor = connection.cursor(dictionary=True) # Use dictionary cursor for easier access
    try:
        query = "SELECT id, phone_number, name, role FROM users WHERE is_active = 1"
        cursor.execute(query)
        users = cursor.fetchall()
        return users # Returns a list of dictionaries
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()

# Get a product by its article number
def get_product_by_article(article_number):
    logger.debug(f"Fetching product with article number: {article_number}")
    connection = create_connection()
    cursor = connection.cursor(buffered=True)
    product = None
    try:
        logger.debug(f"Выполнение запроса для поиска продукта с артикулом: {article_number}")
        query = "SELECT * FROM products WHERE article_number = %s" # Changed LIKE to =
        cursor.execute(query, (article_number,))
        logger.debug(f"Query executed: {query} with article_number={article_number}")
        result = cursor.fetchone()
        
        if result:
            columns = [desc[0] for desc in cursor.description] # Get column names
            product = dict(zip(columns, result))
            logger.info(f"Продукт найден: {product}")
        else:
            logger.warning(f"Продукт с артикулом {article_number} не найден.")
    except Exception as e:
        logger.exception("Ошибка при выполнении запроса к базе данных.")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")
    return product


# Get a store by its ID
def get_store_by_id(store_id):
    logger.debug(f"Fetching store with ID: {store_id}")
    connection = create_connection()
    cursor = connection.cursor()
    store = None
    query = "SELECT * FROM stores WHERE id = %s"
    try:
        cursor.execute(query, (store_id,))
        logger.debug(f"Query executed: {query} with store_id={store_id}")
        store = cursor.fetchone()
    except Error as e:
        logger.error(f"Error fetching store by ID: {e}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")
    return store

# Record sale
def record_sale(product_id, store_id, user_id, sale_price, payment_type):
    logger.debug(f"Recording sale for product_id: {product_id}, store_id: {store_id}, user_id: {user_id}, sale_price: {sale_price}, payment_type: {payment_type}")
    connection = create_connection()
    cursor = connection.cursor()
    try:
        insert_query = """INSERT INTO sales (product_id, store_id, user_id, sale_date, sold_price, payment_type) 
        VALUES (%s, %s, %s, NOW(), %s, %s)"""
        cursor.execute(insert_query, (product_id, store_id, user_id, sale_price, payment_type))
        logger.debug(f"Insert query executed: {insert_query} with values ({product_id}, {store_id}, {user_id}, {sale_price}, {payment_type})")
        
        product = get_product_by_article(get_product_article_by_id(product_id)) # Need to get article to update quantity
        if product:
             update_product_quantity(product['article_number'], -1) # Decrease quantity by 1

        connection.commit()
    except Exception as e:
        logger.exception("Ошибка при записи продажи в базу данных.")
        if connection:
            connection.rollback()
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")

def get_product_article_by_id(product_id):
    connection = create_connection()
    cursor = connection.cursor()
    article = None
    try:
        query = "SELECT article_number FROM products WHERE id = %s"
        cursor.execute(query, (product_id,))
        result = cursor.fetchone()
        if result:
            article = result[0]
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return article

# Get the minimum price of a product
def get_min_price(product_article): # Changed to accept article
    logger.debug(f"Fetching minimum price for product article: {product_article}")
    connection = create_connection()
    cursor = connection.cursor()
    min_price = None
    try:
        query = "SELECT min_price FROM products WHERE article_number = %s"
        cursor.execute(query, (product_article,))
        logger.debug(f"Query executed: {query} with product_article={product_article}")
        result = cursor.fetchone()
        if result:
            min_price = result[0]
    except Error as e:
        logger.error(f"Error fetching min price: {e}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")
    return min_price

# Get report
def generate_report(start_date, end_date):
    logger.debug(f"Generating report from {start_date} to {end_date}")
    connection = create_connection()
    cursor = connection.cursor()
    report_data = []
    try:
        query = """
                SELECT p.product_type, p.model, u.name as user_name, st.name as store_name, s.sold_price, s.sale_date, s.payment_type
                FROM sales s
                JOIN products p ON s.product_id = p.id
                JOIN users u ON s.user_id = u.id
                JOIN stores st ON s.store_id = st.id
                WHERE s.sale_date BETWEEN %s AND %s
                GROUP BY s.id, p.product_type, p.model, u.name, st.name, s.sold_price, s.sale_date, s.payment_type;
                """ # Added aliases and GROUP BY s.id for distinct sales
        logger.debug(f"Executing report query: {query}")
        cursor.execute(query, (start_date, end_date))
        report_data = cursor.fetchall() # Assuming default tuple rows
        # If dictionary cursor is preferred for reports:
        # report_data = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
        logger.debug(f"Report data retrieved: {report_data}")
    except Exception as e:
        logger.exception("Error generating report.")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed after generating report.")
    return report_data


# Функция для добавления пользователя в базу данных
def add_user_to_db(phone_number, role, name):
    logger.debug(f"Adding user to database with phone_number: {phone_number}, role: {role}, name: {name}")
    connection = create_connection()
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
        if connection: connection.rollback()
        message = "Ошибка при работе с базой данных."
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")
    return message

# Функция для удаления пользователя из базы данных
def deactivate_user_in_db(phone_number):
    logger.debug(f"Deactivating user with phone_number: {phone_number}")
    connection = create_connection()
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
        if connection: connection.rollback()
        response = "Ошибка при работе с базой данных."
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")
    return response

# Define change_user_role_function
def change_user_role_function(user_id, new_role):
    logger.debug(f"Changing role for user_id: {user_id} to new_role: {new_role}")
    connection = create_connection()
    cursor = connection.cursor()
    try:
        query = "UPDATE users SET role = %s WHERE id = %s"
        cursor.execute(query, (new_role, user_id))
        connection.commit()
        logger.info(f"User ID {user_id} role updated to {new_role}.")
    except Exception as e:
        logger.exception("Ошибка при изменении роли пользователя.")
        if connection: connection.rollback()
        raise e # Re-raise after logging and rollback
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
            logger.debug("Database connection closed.")

# --- Functions for Buyback, Return, Trade-in ---

def update_product_quantity(product_article, quantity_change):
    logger.debug(f"Updating quantity for product article: {product_article} by {quantity_change}")
    connection = create_connection()
    cursor = connection.cursor()
    success = False
    try:
        # Check current quantity to prevent negative stock if decreasing
        if quantity_change < 0:
            cursor.execute("SELECT quantity FROM products WHERE article_number = %s", (product_article,))
            current_quantity = cursor.fetchone()
            if current_quantity and current_quantity[0] < abs(quantity_change):
                logger.warning(f"Not enough stock for {product_article} to decrease by {abs(quantity_change)}. Current: {current_quantity[0]}")
                return False # Or raise an error

        query = "UPDATE products SET quantity = quantity + %s WHERE article_number = %s"
        cursor.execute(query, (quantity_change, product_article))
        connection.commit()
        logger.info(f"Product {product_article} quantity updated by {quantity_change}. Rows affected: {cursor.rowcount}")
        success = cursor.rowcount > 0
    except Error as e:
        logger.error(f"Error updating product quantity for {product_article}: {e}")
        if connection: connection.rollback()
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return success

def create_buyback_record(product_article, price, payment_type, user_id, store_id):
    logger.debug(f"Creating buyback record for article: {product_article}, price: {price}, user_id: {user_id}")
    connection = create_connection()
    cursor = connection.cursor()
    buyback_id = None
    try:
        # Assuming a 'buybacks' table: id, product_article, buyback_price, payment_type, buyback_date, user_id, store_id
        # And that product_article is a foreign key or just the article string
        query = """
        INSERT INTO buybacks (product_article, buyback_price, payment_type, user_id, store_id, buyback_date)
        VALUES (%s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (product_article, price, payment_type, user_id, store_id))
        buyback_id = cursor.lastrowid
        
        # Increase product quantity
        if not update_product_quantity(product_article, 1):
            logger.error(f"Failed to update quantity for buyback of {product_article}. Rolling back buyback record.")
            connection.rollback()
            return None

        connection.commit()
        logger.info(f"Buyback record created with ID: {buyback_id} for article {product_article}")
    except Error as e:
        logger.error(f"Error creating buyback record for {product_article}: {e}")
        if connection: connection.rollback()
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return buyback_id

def get_sale_and_product_details_by_sale_id(sale_id):
    logger.debug(f"Fetching sale and product details for sale_id: {sale_id}")
    connection = create_connection()
    cursor = connection.cursor(dictionary=True) # Use dictionary cursor
    details = None
    try:
        # Assuming sales.id is the order_id
        query = """
        SELECT s.id as sale_id, s.sold_price, s.payment_type as sale_payment_type, s.sale_date,
               p.id as product_id, p.article_number, p.model as product_model, p.product_type
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
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return details

def create_return_record(sale_id, product_article, return_price, payment_type, user_id, store_id):
    logger.debug(f"Creating return record for sale_id: {sale_id}, product_article: {product_article}, price: {return_price}")
    connection = create_connection()
    cursor = connection.cursor()
    return_id = None
    try:
        # Assuming a 'returns' table: id, original_sale_id, product_article, return_price, payment_type, return_date, user_id, store_id
        query = """
        INSERT INTO returns (original_sale_id, product_article, return_price, payment_type, user_id, store_id, return_date)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (sale_id, product_article, return_price, payment_type, user_id, store_id))
        return_id = cursor.lastrowid

        if not update_product_quantity(product_article, 1): # Increase stock
            logger.error(f"Failed to update quantity for return of {product_article}. Rolling back return record.")
            connection.rollback()
            return None
            
        connection.commit()
        logger.info(f"Return record created with ID: {return_id} for product {product_article} from sale {sale_id}")
    except Error as e:
        logger.error(f"Error creating return record for {product_article}: {e}")
        if connection: connection.rollback()
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return return_id

def create_trade_in_record(old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id):
    logger.debug(f"Creating trade-in: old {old_product_article}, new {new_product_article}, diff {price_difference}")
    connection = create_connection()
    cursor = connection.cursor()
    trade_in_id = None
    try:
        # Assuming 'trade_ins' table: id, old_product_article, new_product_article, price_difference, payment_type_difference, trade_in_date, user_id, store_id
        query = """
        INSERT INTO trade_ins (old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id, trade_in_date)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """
        cursor.execute(query, (old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id))
        trade_in_id = cursor.lastrowid

        # Update stock for old product (returned)
        if not update_product_quantity(old_product_article, 1):
             logger.error(f"Failed to update quantity for trade-in (old item: {old_product_article}). Rolling back trade-in.")
             connection.rollback()
             return None
        # Update stock for new product (sold)
        if not update_product_quantity(new_product_article, -1):
            logger.error(f"Failed to update quantity for trade-in (new item: {new_product_article}). Rolling back trade-in and old item stock.")
            # Attempt to roll back the first stock update if possible, or handle inconsistency
            update_product_quantity(old_product_article, -1) # Revert old product stock
            connection.rollback()
            return None
            
        connection.commit()
        logger.info(f"Trade-in record created with ID: {trade_in_id}")
    except Error as e:
        logger.error(f"Error creating trade-in record: {e}")
        if connection: connection.rollback()
        # Potentially revert stock changes if partially successful before error
        # This part can be complex to make fully atomic without 2PC
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()
    return trade_in_id

# Ensure tables 'buybacks', 'returns', 'trade_ins' exist in your DB with appropriate columns.
# Example DDL (adjust as needed):
"""
CREATE TABLE IF NOT EXISTS buybacks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    product_article VARCHAR(255) NOT NULL,
    buyback_price DECIMAL(10, 2) NOT NULL,
    payment_type VARCHAR(50),
    user_id INT,
    store_id INT,
    buyback_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (store_id) REFERENCES stores(id)
    -- FOREIGN KEY (product_article) REFERENCES products(article_number) -- If articles are unique and you want FK constraint
);

CREATE TABLE IF NOT EXISTS returns (
    id INT AUTO_INCREMENT PRIMARY KEY,
    original_sale_id INT NOT NULL,
    product_article VARCHAR(255) NOT NULL,
    return_price DECIMAL(10, 2) NOT NULL,
    payment_type VARCHAR(50),
    user_id INT,
    store_id INT,
    return_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (original_sale_id) REFERENCES sales(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (store_id) REFERENCES stores(id)
);

CREATE TABLE IF NOT EXISTS trade_ins (
    id INT AUTO_INCREMENT PRIMARY KEY,
    old_product_article VARCHAR(255) NOT NULL,
    new_product_article VARCHAR(255) NOT NULL,
    price_difference DECIMAL(10, 2) NOT NULL, -- Positive if customer pays, negative if bot pays
    payment_type_difference VARCHAR(50), -- How the difference was settled
    user_id INT,
    store_id INT,
    trade_in_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (store_id) REFERENCES stores(id)
);
"""