import mysql.connector
from mysql.connector import pooling, Error
from config import DB_HOST, DB_USER, DB_PASS, DB_NAME
import logging
from functools import wraps

logger = logging.getLogger(__name__)

# 1. Создаем пул соединений один раз при старте приложения
try:
    connection_pool = pooling.MySQLConnectionPool(
        pool_name="mypool",
        pool_size=10,  # Увеличил размер пула для большей параллельности
        pool_reset_session=True,
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        # Устанавливаем таймауты для предотвращения "зависших" соединений
        connection_timeout=10 
    )
    logger.info("Database connection pool created successfully.")
except Error as e:
    logger.exception(f"FATAL: Error creating connection pool: {e}")
    # Если пул не создан, приложение не сможет работать с БД.
    connection_pool = None

# 2. Создаем декораторы для управления соединениями

def with_connection(func):
    """Декоратор для выполнения запросов без транзакции (в основном SELECT)."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not connection_pool:
            logger.error("Connection pool is not available.")
            return None
        
        connection = None
        try:
            connection = connection_pool.get_connection()
            cursor = connection.cursor(dictionary=True)
            result = func(cursor, *args, **kwargs)
            return result
        except Error as e:
            logger.exception(f"Database error in {func.__name__}: {e}")
            # В зависимости от функции, можно возвращать разные значения по умолчанию
            return None
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close() # Для пула .close() возвращает соединение обратно
    return wrapper

def with_transaction(func):
    """Декоратор для выполнения операций внутри транзакции (INSERT, UPDATE, DELETE)."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not connection_pool:
            logger.error("Connection pool is not available for transaction.")
            return False, "Нет подключения к БД."

        connection = None
        try:
            connection = connection_pool.get_connection()
            cursor = connection.cursor(dictionary=True)
            # Явно передаем курсор в функцию, транзакцией управляем здесь
            result = func(cursor, *args, **kwargs)
            connection.commit()
            logger.info(f"Transaction in {func.__name__} committed successfully.")
            return result
        except (Error, ValueError) as e:
            logger.exception(f"Error in transaction {func.__name__}. Rolling back. Error: {e}")
            if connection:
                connection.rollback()
            # Возвращаем кортеж с ошибкой, как в оригинальном коде
            return False, f"Ошибка: {e}"
        finally:
            if connection and connection.is_connected():
                cursor.close()
                connection.close()
    return wrapper

# 3. Переписанные функции с использованием декораторов

@with_connection
def get_user_by_phone(cursor, phone_number):
    logger.debug(f"Fetching user with phone number: {phone_number}")
    cursor.execute("SELECT * FROM users WHERE phone_number = %s", (phone_number,))
    return cursor.fetchone()

@with_connection
def get_all_users(cursor):
    logger.debug("Fetching all active users.")
    cursor.execute("SELECT id, phone_number, name, role FROM users WHERE is_active = 1")
    return cursor.fetchall()

@with_connection
def get_product_by_article(cursor, article_number):
    logger.debug(f"Fetching product with article number: {article_number}")
    cursor.execute("SELECT * FROM products WHERE article_number = %s", (article_number,))
    return cursor.fetchone()

@with_connection
def get_sales_summary_data(cursor, start_date, end_date):
    logger.debug(f"Generating sales summary for {start_date} to {end_date}")
    summary_data = {}
    
    overall_query = """
    SELECT COUNT(id) AS total_sales_count, SUM(sold_price) AS total_sales_amount, AVG(sold_price) AS average_sale_price
    FROM sales WHERE sale_date BETWEEN %s AND %s;
    """
    cursor.execute(overall_query, (start_date, end_date))
    summary_data['overall_summary'] = cursor.fetchone()

    payment_type_query = """
    SELECT payment_type, COUNT(id) AS payment_type_sales_count, SUM(sold_price) AS payment_type_sales_amount
    FROM sales WHERE sale_date BETWEEN %s AND %s
    GROUP BY payment_type ORDER BY payment_type;
    """
    cursor.execute(payment_type_query, (start_date, end_date))
    summary_data['payment_type_breakdown'] = cursor.fetchall()
    
    return summary_data

@with_connection
def get_returns_summary_data(cursor, start_date, end_date):
    logger.debug(f"Generating returns summary for {start_date} to {end_date}")
    query = """
    SELECT COUNT(id) AS total_returns_count, SUM(return_price) AS total_returns_amount
    FROM returns WHERE return_date BETWEEN %s AND %s;
    """
    cursor.execute(query, (start_date, end_date))
    return cursor.fetchone()

@with_connection
def get_buybacks_summary_data(cursor, start_date, end_date):
    logger.debug(f"Generating buybacks summary for {start_date} to {end_date}")
    query = """
    SELECT COUNT(id) AS total_buybacks_count, SUM(buyback_price) AS total_buybacks_amount
    FROM buybacks WHERE buyback_date BETWEEN %s AND %s;
    """
    cursor.execute(query, (start_date, end_date))
    return cursor.fetchone()

@with_connection
def get_low_stock_products(cursor, threshold):
    logger.debug(f"Fetching products with quantity <= {threshold} and track_stock = 1.")
    query = """
    SELECT article_number, model, quantity, Store
    FROM products WHERE quantity <= %s AND track_stock = 1
    ORDER BY Store, model;
    """
    cursor.execute(query, (threshold,))
    return cursor.fetchall()

@with_connection
def find_sales_by_article_and_date(cursor, article_number, sale_date):
    logger.debug(f"Searching for sales with article '{article_number}' on date '{sale_date}'")
    query = """
    SELECT s.id as sale_id, s.sold_price, s.payment_type as sale_payment_type, s.sale_date,
           p.id as product_id, p.article_number, p.model as product_model, p.product_type
    FROM sales s JOIN products p ON s.product_id = p.id
    WHERE p.article_number = %s AND DATE(s.sale_date) = %s
    ORDER BY s.sale_date ASC
    """
    cursor.execute(query, (article_number, sale_date))
    return cursor.fetchall()

@with_connection
def get_sale_details_by_id(cursor, sale_id):
    logger.debug(f"Fetching sale and product details for sale_id: {sale_id}")
    query = """
    SELECT s.id as sale_id, s.sold_price, s.payment_type as sale_payment_type, s.sale_date,
           p.id as product_id, p.article_number, p.model as product_model, p.product_type
    FROM sales s JOIN products p ON s.product_id = p.id
    WHERE s.id = %s
    """
    cursor.execute(query, (sale_id,))
    return cursor.fetchone()

# Вспомогательная функция, остается без изменений, декоратор не нужен
def _update_product_quantity_with_cursor(cursor, product_article, quantity_change):
    logger.debug(f"Updating quantity for {product_article} by {quantity_change} in transaction.")
    if quantity_change < 0:
        cursor.execute("SELECT quantity FROM products WHERE article_number = %s FOR UPDATE", (product_article,))
        result = cursor.fetchone()
        if not result:
            raise ValueError(f"Продукт {product_article} не найден.")
        current_quantity = result.get('quantity')
        if current_quantity is None:
            raise ValueError(f"У продукта {product_article} не указано количество.")
        if current_quantity < abs(quantity_change):
            raise ValueError(f"Недостаточно товара {product_article} на складе.")

    cursor.execute("UPDATE products SET quantity = quantity + %s WHERE article_number = %s", (quantity_change, product_article))
    if cursor.rowcount == 0 and quantity_change != 0:
        raise ValueError(f"Не удалось обновить количество для {product_article}.")
    logger.info(f"Product {product_article} quantity updated by {quantity_change}.")
    return True

@with_transaction
def update_product_quantity(cursor, product_article, quantity_change):
    logger.debug(f"Standalone update for article: {product_article} by {quantity_change}")
    _update_product_quantity_with_cursor(cursor, product_article, quantity_change)
    return True, f"Остаток товара {product_article} изменен на {quantity_change} шт."

@with_transaction
def record_sale(cursor, product_id, product_article, user_id, sale_price, payment_type):
    logger.debug(f"Recording sale for product_id: {product_id}")
    insert_query = "INSERT INTO sales (product_id, user_id, sale_date, sold_price, payment_type) VALUES (%s, %s, NOW(), %s, %s)"
    cursor.execute(insert_query, (product_id, user_id, sale_price, payment_type))
    _update_product_quantity_with_cursor(cursor, product_article, -1)
    return True, "Продажа успешно записана."

@with_connection
def get_product_article_by_id(cursor, product_id):
    cursor.execute("SELECT article_number FROM products WHERE id = %s", (product_id,))
    result = cursor.fetchone()
    return result['article_number'] if result else None

@with_connection
def get_min_price(cursor, product_article):
    logger.debug(f"Fetching minimum price for product article: {product_article}")
    cursor.execute("SELECT min_price FROM products WHERE article_number = %s", (product_article,))
    result = cursor.fetchone()
    return result['min_price'] if result else None

@with_connection
def generate_report(cursor, start_date, end_date):
    # Используем обычный курсор, если обработчик отчета ожидает кортежи
    # Для этого можно было бы создать отдельный декоратор, но для простоты оставим так:
    # В данном случае, вернем dict, т.к. это более универсально.
    logger.debug(f"Generating report from {start_date} to {end_date}")
    query = """
            SELECT s.id as sale_id, s.sale_date, s.sold_price, s.payment_type,
                   p.article_number, p.product_type, p.model, p.size, p.metal_type,
                   p.weight, p.price_per_gram, p.price as product_original_price, 
                   p.manufacturer, p.commentaries, u.name as user_name,
                   p.Store as product_store_name
            FROM sales s
            JOIN products p ON s.product_id = p.id
            JOIN users u ON s.user_id = u.id
            WHERE s.sale_date BETWEEN %s AND %s
            ORDER BY s.sale_date, s.id;
            """
    cursor.execute(query, (start_date, end_date))
    return cursor.fetchall()

@with_transaction
def add_user_to_db(cursor, phone_number, role, name, store_id=None):
    logger.debug(f"Adding/updating user: {phone_number}")
    cursor.execute("SELECT id, is_active FROM users WHERE phone_number = %s", (phone_number,))
    result_phone = cursor.fetchone()

    if result_phone:
        user_id, is_active = result_phone['id'], result_phone['is_active']
        if is_active == 0:
            cursor.execute("UPDATE users SET is_active = 1, role = %s, name = %s, store_id = %s WHERE id = %s", (role, name, store_id, user_id))
            return True, "Пользователь активирован повторно и данные обновлены."
        else:
            return False, "Пользователь с таким номером телефона уже существует и активен."
    else:
        if store_id is None: store_id = 1 # Значение по умолчанию
        cursor.execute("INSERT INTO users (phone_number, role, name, store_id, is_active) VALUES (%s, %s, %s, %s, 1)", (phone_number, role, name, store_id))
        return True, "Пользователь добавлен успешно."

@with_transaction
def deactivate_user_in_db(cursor, user_id_to_deactivate):
    logger.debug(f"Deactivating user with ID: {user_id_to_deactivate}")
    cursor.execute("UPDATE users SET is_active = 0 WHERE id = %s AND is_active = 1", (user_id_to_deactivate,))
    if cursor.rowcount > 0:
        return True, f"Пользователь ID {user_id_to_deactivate} деактивирован."
    else:
        return False, f"Пользователь ID {user_id_to_deactivate} не найден или уже деактивирован."

@with_transaction
def change_user_role_function(cursor, user_id, new_role):
    logger.debug(f"Changing role for user_id: {user_id} to new_role: {new_role}")
    cursor.execute("UPDATE users SET role = %s WHERE id = %s", (new_role, user_id))
    return True, f"Роль пользователя ID {user_id} изменена на {new_role}."

@with_transaction
def update_user_phone(cursor, user_id, new_phone_number):
    logger.debug(f"Updating phone for user_id: {user_id} to new_phone: {new_phone_number}")
    cursor.execute("SELECT id, name FROM users WHERE phone_number = %s AND id != %s AND is_active = 1", (new_phone_number, user_id))
    existing_user = cursor.fetchone()
    if existing_user:
        return False, f"Номер {new_phone_number} уже используется другим пользователем."

    cursor.execute("UPDATE users SET phone_number = %s WHERE id = %s", (new_phone_number, user_id))
    if cursor.rowcount == 0:
        return False, f"Пользователь ID {user_id} не найден."
    return True, f"Номер телефона пользователя ID {user_id} изменен."

@with_transaction
def create_buyback_record(cursor, product_article, price, payment_type, user_id, store_id):
    logger.debug(f"Creating buyback record for article: {product_article}")
    query = "INSERT INTO buybacks (product_article, buyback_price, payment_type, user_id, store_id, buyback_date) VALUES (%s, %s, %s, %s, %s, NOW())"
    cursor.execute(query, (product_article, price, payment_type, user_id, store_id))
    buyback_id = cursor.lastrowid
    _update_product_quantity_with_cursor(cursor, product_article, 1)
    return True, f"Выкуп создан с ID: {buyback_id}"

@with_transaction
def create_return_record(cursor, sale_id, product_article, return_price, payment_type, user_id, store_id):
    logger.debug(f"Creating return record for sale_id: {sale_id}")
    query = "INSERT INTO returns (original_sale_id, product_article, return_price, payment_type, user_id, store_id, return_date) VALUES (%s, %s, %s, %s, %s, %s, NOW())"
    cursor.execute(query, (sale_id, product_article, return_price, payment_type, user_id, store_id))
    return_id = cursor.lastrowid
    _update_product_quantity_with_cursor(cursor, product_article, 1)
    return True, f"Возврат создан с ID: {return_id}"

@with_transaction
def create_trade_in_record(cursor, old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id):
    logger.debug(f"Creating trade-in: old {old_product_article}, new {new_product_article}")
    query = "INSERT INTO trade_ins (old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id, trade_in_date) VALUES (%s, %s, %s, %s, %s, %s, NOW())"
    cursor.execute(query, (old_product_article, new_product_article, price_difference, payment_type_difference, user_id, store_id))
    trade_in_id = cursor.lastrowid
    _update_product_quantity_with_cursor(cursor, old_product_article, 1)
    _update_product_quantity_with_cursor(cursor, new_product_article, -1)
    return True, f"Trade-in создан с ID: {trade_in_id}"

@with_transaction
def create_cash_withdrawal_record(cursor, amount, comment, user_id, store_id):
    logger.debug(f"Creating cash withdrawal record: amount={amount}")
    query = "INSERT INTO cash_withdrawals (amount, comment, user_id, store_id, withdrawal_date) VALUES (%s, %s, %s, %s, NOW())"
    cursor.execute(query, (amount, comment, user_id, store_id))
    return True, "Инкассация успешно зарегистрирована."

@with_transaction
def add_product_to_db(cursor, product_data: dict):
    logger.info(f"Attempting to add new product: {product_data.get('article_number')}")
    cursor.execute("SELECT id FROM products WHERE article_number = %s", (product_data['article_number'],))
    if cursor.fetchone():
        raise ValueError(f"Товар с артикулом {product_data['article_number']} уже существует.")

    # Динамическое формирование запроса
    cols = [f"`{k}`" for k in product_data.keys()]
    vals_placeholder = ['%s'] * len(product_data)
    vals = list(product_data.values())
    
    query = f"INSERT INTO products ({', '.join(cols)}) VALUES ({', '.join(vals_placeholder)})"
    
    cursor.execute(query, vals)
    product_id = cursor.lastrowid
    return True, f"Товар {product_data.get('article_number')} успешно добавлен с ID {product_id}."

@with_connection
def get_stock_report_data(cursor):
    logger.info("Generating stock report data for all stores.")
    query = """
        SELECT p.article_number, p.product_type, p.model, p.metal_type, p.Store as store_name,
               p.weight, p.quantity, p.price, p.min_price, p.manufacturer, 
               p.arrival_date, p.commentaries
        FROM products p
        ORDER BY p.Store, p.product_type, p.model, p.article_number
    """
    cursor.execute(query)
    return cursor.fetchall()

@with_connection
def get_enum_or_distinct_values(cursor, table_name, column_name, add_other_option=True):
    logger.debug(f"Fetching ENUM/distinct values for {table_name}.{column_name}")
    
    # Попытка получить значения ENUM из схемы
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE %s", (column_name,))
    column_info = cursor.fetchone()

    values_list = []
    if column_info and 'Type' in column_info and column_info['Type'].lower().startswith("enum("):
        type_def = column_info['Type']
        values_str = type_def[len("enum("):-1]
        values_list = [val.strip("'") for val in values_str.split(',')]
    else:
        # Если не ENUM, получаем уникальные значения
        # Используем новый курсор без dict=True для простоты
        distinct_cursor = cursor.connection.cursor()
        distinct_cursor.execute(f"SELECT DISTINCT `{column_name}` FROM `{table_name}` WHERE `{column_name}` IS NOT NULL AND `{column_name}` != '' ORDER BY `{column_name}`")
        values_list = [row[0] for row in distinct_cursor.fetchall()]
        distinct_cursor.close()

    if add_other_option and "Другое" not in values_list:
        values_list.append("Другое")
    
    return sorted(list(set(values_list)))
