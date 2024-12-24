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
    except Error as e:
        logger.exception(f"The error '{e}' occurred")
    return connection

# Get a user by their phone number
def get_user_by_phone(phone_number):
    connection = create_connection()
    cursor = connection.cursor()
    try:
        # Выполняем запрос, получая все столбцы
        query = "SELECT * FROM users WHERE phone_number = %s"
        cursor.execute(query, (phone_number,))
        result = cursor.fetchone()

        if result:
            # Извлекаем имена столбцов из cursor.description
            columns = [desc[0] for desc in cursor.description]
            user_data = dict(zip(columns, result))  # Преобразуем результат в словарь
            logger.debug(f"Результат запроса для {phone_number}: {user_data}")
            return user_data  # Возвращаем словарь
        else:
            logger.debug(f"Пользователь с телефоном {phone_number} не найден.")
            return None
    except Exception as e:
        logger.exception("Ошибка при выполнении запроса к базе данных.")
        return None
    finally:
        cursor.close()
        connection.close()

def get_all_users():
    connection = create_connection()
    cursor = connection.cursor()
    try:
        query = "SELECT phone_number, name, role FROM users WHERE is_active = 1"
        cursor.execute(query)
        result = cursor.fetchall()
        users = [{"id": row[0], "name": row[1], "role": row[2]} for row in result]
        return users
    finally:
        cursor.close()
        connection.close()

# Get a product by its article number
def get_product_by_article(article_number):
    connection = create_connection()
    cursor = connection.cursor(buffered=True)
    try:
        logger.debug(f"Выполнение запроса для поиска продукта с артикулом: {article_number}")
        query = "SELECT * FROM products WHERE article_number LIKE %s"
        cursor.execute(query, (article_number,))
        result = cursor.fetchone()
        
        if result:
            columns = ["id", "store_id", "article_number", "metal_type", "product_type", "model", "weight", "price_per_gram", "price", "min_price", "arrival_date", "quantity", "manufacturer", "commentaries"]
            product = dict(zip(columns, result))
            logger.info(f"Продукт найден: {product}")
            return product
        else:
            logger.warning(f"Продукт с артикулом {article_number} не найден.")
            return None
    except Exception as e:
        logger.exception("Ошибка при выполнении запроса к базе данных.")
        return None
    finally:
        cursor.close()
        connection.close()


# Get a store by its ID
def get_store_by_id(store_id):
    connection = create_connection()
    cursor = connection.cursor()
    query = "SELECT * FROM stores WHERE id = %s"
    cursor.execute(query, (store_id,))
    store = cursor.fetchone()
    cursor.close()
    connection.close()
    return store

# Record sale
def record_sale(product_id, store_id, user_id, sale_price, payment_type):
    connection = create_connection()
    cursor = connection.cursor()
    try:
        insert_query = """INSERT INTO sales (product_id, store_id, user_id, sale_date, sold_price, payment_type) 
        VALUES (%s, %s, %s, NOW(), %s, %s)"""
        cursor.execute(insert_query, (product_id, store_id, user_id, sale_price, payment_type))
        update_query = """UPDATE products SET quantity = quantity - 1 WHERE id = %s"""
        cursor.execute(update_query, (product_id,))
        connection.commit()
    except Exception as e:
        logger.exception("Ошибка при записи продажи в базу данных.")
    finally:
        cursor.close()
        connection.close()

# Get the minimum price of a product
def get_min_price(product):
    connection = create_connection()
    cursor = connection.cursor()
    query = "SELECT min_price FROM products WHERE id = %s"
    cursor.execute(query, (product[0],))
    min_price = None
    result = cursor.fetchone()
    if result:
        min_price = result[0]
    cursor.close()
    connection.close()
    return min_price

# Get report
def generate_report(start_date, end_date):
    connection = create_connection()
    cursor = connection.cursor()
    query = f"""
            SELECT products.product_type, users.name, stores.name, sales.sold_price, sales.sale_date, sales.payment_type
            FROM sales
            JOIN products ON sales.product_id = products.id
            JOIN users ON sales.user_id = users.id
            JOIN stores ON sales.store_id = stores.id
            WHERE sales.sale_date BETWEEN '{start_date}' AND '{end_date}'
            GROUP BY sales.sale_date, sales.product_id, sales.user_id, sales.sold_price, products.product_type, users.name, sales.payment_type;
            """
    cursor.execute(query)
    report_data = cursor.fetchall()
    cursor.close()
    connection.close()
    return report_data

# Update User
# #def update_user(user_id, role):
#     connection = create_connection()
#     cursor = connection.cursor()
#        query = f"""
#           UPDATE `users` SET
#           `store_id` = '%s',
#           `phone_number` = '%s',
#           `role` = %s,
#           `name` = '%s'
#           WHERE `id` = '%s';
#           """
#         cursor.execute(query)
#         cursor.close()
#         connection.close()


# Функция для добавления пользователя в базу данных
def add_user_to_db(phone_number, role, name):
    connection = create_connection()
    cursor = connection.cursor()

    # Проверка, существует ли пользователь с таким номером телефона
    select_query = "SELECT id, is_active FROM users WHERE phone_number = %s"
    cursor.execute(select_query, (phone_number,))
    result = cursor.fetchone()

    if result:
        user_id, is_active = result
        if is_active == 0:
            # Если пользователь существует, но неактивен, активируем его
            update_query = "UPDATE users SET is_active = 1, role = %s, name = %s WHERE id = %s"
            cursor.execute(update_query, (role, name, user_id))
            connection.commit()
            cursor.close()
            connection.close()
            return "Пользователь активирован повторно."
        else:
            cursor.close()
            connection.close()
            return "Пользователь уже существует и активен."
    else:
        # Добавление нового пользователя, если он не существует
        insert_query = "INSERT INTO users (phone_number, role, name, is_active) VALUES (%s, %s, %s, 1)"
        cursor.execute(insert_query, (phone_number, role, name))
        connection.commit()
        cursor.close()
        connection.close()
        return "Пользователь добавлен успешно."

# Функция для удаления пользователя из базы данных
def deactivate_user_in_db(phone_number):
    connection = create_connection()
    cursor = connection.cursor()

    # Проверка, существует ли пользователь с таким номером телефона
    select_query = "SELECT id FROM users WHERE phone_number = %s AND is_active = 1"
    cursor.execute(select_query, (phone_number,))
    result = cursor.fetchone()

    if result:
        # Деактивация пользователя
        update_query = "UPDATE users SET is_active = 0 WHERE phone_number = %s"
        cursor.execute(update_query, (phone_number,))
        connection.commit()
        response = "Пользователь деактивирован."
    else:
        response = "Пользователь не найден или уже деактивирован."

    cursor.close()
    connection.close()
    return response

def get_deactivated_users():
    connection = create_connection()
    cursor = connection.cursor()
    try:
        # Получаем список пользователей, у которых is_active = 0
        query = "SELECT id FROM users WHERE is_active = 0"
        cursor.execute(query)
        result = cursor.fetchall()
        return [row[0] for row in result]  # Возвращаем список ID деактивированных пользователей
    finally:
        cursor.close()
        connection.close()

def check_role_changes():
    connection = create_connection()
    cursor = connection.cursor()
    try:
        # Запрашиваем текущие данные пользователей
        query = "SELECT id, role FROM users"
        cursor.execute(query)
        current_roles = {row[0]: row[1] for row in cursor.fetchall()}  # Формат: {id: role}

        # Проверяем изменения в ролях
        global role_cache
        for user_id, current_role in current_roles.items():
            if user_id in role_cache:
                # Если роль изменилась, обновляем user_data
                if role_cache[user_id] != current_role:
                    role_cache[user_id] = current_role
                    for chat_id, user in user_data.items():
                        if user['id'] == user_id:
                            user['role'] = current_role
                            bot.send_message(
                                chat_id,
                                f"Ваша роль была изменена на '{current_role}'."
                            )
            else:
                # Если пользователь новый, добавляем в кэш
                role_cache[user_id] = current_role

        # Удаляем пользователей из кэша, которых больше нет в базе
        for user_id in list(role_cache.keys()):
            if user_id not in current_roles:
                del role_cache[user_id]

    finally:
        cursor.close()
        connection.close()

# Define change_user_role_function
def change_user_role_function(user_id, new_role):
    connection = create_connection()
    cursor = connection.cursor()
    try:
        query = "UPDATE users SET role = %s WHERE id = %s"
        cursor.execute(query, (new_role, user_id))
        connection.commit()
        logger.info(f"User ID {user_id} role updated to {new_role}.")
    except Exception as e:
        logger.exception("Ошибка при изменении роли пользователя.")
        raise e
    finally:
        cursor.close()
        connection.close()

