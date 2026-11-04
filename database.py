import logging
import threading
import time
from datetime import timedelta
from functools import wraps

import mysql.connector
from mysql.connector import pooling, Error

from config import DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME
from utils.common import now, today, normalize_phone

logger = logging.getLogger(__name__)

_pool = None
_pool_lock = threading.Lock()


def init_pool(retries=30, delay=2):
    """Создаёт пул соединений, дожидаясь готовности MySQL (контейнер БД стартует дольше бота)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                _pool = pooling.MySQLConnectionPool(
                    pool_name="posbot",
                    pool_size=10,
                    pool_reset_session=True,
                    host=DB_HOST,
                    port=DB_PORT,
                    user=DB_USER,
                    password=DB_PASS,
                    database=DB_NAME,
                    charset="utf8mb4",
                    collation="utf8mb4_general_ci",
                    connection_timeout=10,
                    # Чтение не держит открытую транзакцию (и старый снимок REPEATABLE READ),
                    # даже если сброс сессии пула не сработает.
                    # Записи идут через явный start_transaction() в with_transaction.
                    autocommit=True,
                )
                logger.info("Пул соединений с БД создан.")
                return _pool
            except Error as e:
                last_error = e
                logger.warning("БД недоступна (попытка %s/%s): %s", attempt, retries, e)
                time.sleep(delay)
        raise RuntimeError(f"Не удалось подключиться к БД: {last_error}")


def get_connection():
    return (_pool or init_pool()).get_connection()


def with_connection(func):
    """Запрос без транзакции (SELECT). При ошибке БД возвращает None."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        connection = cursor = None
        try:
            connection = get_connection()
            cursor = connection.cursor(dictionary=True)
            return func(cursor, *args, **kwargs)
        except Error as e:
            logger.exception("Ошибка БД в %s: %s", func.__name__, e)
            return None
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
    return wrapper


class BusinessError(ValueError):
    """Ошибка бизнес-проверки внутри транзакции: текст показывается пользователю."""


def with_transaction(func):
    """Операция в транзакции. Всегда возвращает (успех: bool, сообщение/данные)."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        connection = cursor = None
        try:
            connection = get_connection()
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            result = func(cursor, *args, **kwargs)
            connection.commit()
            return result
        except BusinessError as e:
            if connection is not None:
                connection.rollback()
            logger.warning("Транзакция %s отклонена: %s", func.__name__, e)
            return False, str(e)
        except Error as e:
            if connection is not None:
                connection.rollback()
            logger.exception("Ошибка БД в транзакции %s: %s", func.__name__, e)
            return False, "Ошибка базы данных. Операция не выполнена, попробуйте ещё раз."
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
    return wrapper


# ---------------------------------------------------------------- пользователи

def _phone_variants(phone):
    norm = normalize_phone(phone)
    if not norm:
        return [str(phone)]
    return [norm, norm[1:], '8' + norm[2:]]


@with_connection
def get_user_by_phone(cursor, phone_number):
    variants = _phone_variants(phone_number)
    placeholders = ",".join(["%s"] * len(variants))
    cursor.execute(f"SELECT * FROM users WHERE phone_number IN ({placeholders}) ORDER BY is_active DESC, id LIMIT 1", variants)
    return cursor.fetchone()


@with_connection
def get_user_by_chat_id(cursor, chat_id):
    cursor.execute("SELECT * FROM users WHERE chat_id = %s AND is_active = 1 ORDER BY id LIMIT 1", (chat_id,))
    return cursor.fetchone()


@with_connection
def get_user_by_id(cursor, user_id):
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    return cursor.fetchone()


@with_transaction
def set_user_chat_id(cursor, user_id, chat_id):
    # Один чат — один пользователь: отвязываем чат от других записей
    cursor.execute("UPDATE users SET chat_id = NULL WHERE chat_id = %s AND id != %s", (chat_id, user_id))
    cursor.execute("UPDATE users SET chat_id = %s WHERE id = %s", (chat_id, user_id))
    return True, "ok"


@with_connection
def get_all_users(cursor):
    cursor.execute("SELECT id, phone_number, name, surename, role, chat_id FROM users WHERE is_active = 1 ORDER BY role, name")
    return cursor.fetchall()


@with_connection
def get_chat_ids_by_roles(cursor, roles):
    placeholders = ",".join(["%s"] * len(roles))
    cursor.execute(f"SELECT DISTINCT chat_id FROM users WHERE is_active = 1 AND chat_id IS NOT NULL AND role IN ({placeholders})", list(roles))
    return [row['chat_id'] for row in cursor.fetchall()]


@with_transaction
def add_user_to_db(cursor, phone_number, role, name, store_id=None):
    phone = normalize_phone(phone_number)
    if not phone:
        raise BusinessError("Некорректный номер телефона.")
    variants = _phone_variants(phone)
    placeholders = ",".join(["%s"] * len(variants))
    cursor.execute(f"SELECT id, is_active FROM users WHERE phone_number IN ({placeholders}) FOR UPDATE", variants)
    existing = cursor.fetchone()
    if existing:
        if existing['is_active']:
            raise BusinessError("Пользователь с таким номером уже существует и активен.")
        cursor.execute("UPDATE users SET is_active = 1, role = %s, name = %s, phone_number = %s, store_id = COALESCE(%s, store_id) WHERE id = %s",
                       (role, name, phone, store_id, existing['id']))
        return True, f"Пользователь {name} снова активирован ({role})."
    cursor.execute("INSERT INTO users (phone_number, role, name, store_id, is_active) VALUES (%s, %s, %s, %s, 1)",
                   (phone, role, name, store_id if store_id is not None else 1))
    return True, f"Пользователь {name} ({role}) добавлен."


@with_transaction
def ensure_admin(cursor, phone_number, name):
    """Создаёт или активирует администратора с указанным телефоном."""
    phone = normalize_phone(phone_number)
    if not phone:
        raise BusinessError(f"ADMIN_PHONE некорректен: {phone_number}")
    variants = _phone_variants(phone)
    placeholders = ",".join(["%s"] * len(variants))
    cursor.execute(f"SELECT id, role, is_active FROM users WHERE phone_number IN ({placeholders}) FOR UPDATE", variants)
    user = cursor.fetchone()
    if user is None:
        cursor.execute("INSERT INTO users (phone_number, role, name, store_id, is_active) VALUES (%s, 'admin', %s, 1, 1)", (phone, name))
        return True, "created"
    if user['role'] != 'admin' or not user['is_active']:
        cursor.execute("UPDATE users SET role = 'admin', is_active = 1 WHERE id = %s", (user['id'],))
        return True, "updated"
    return True, "exists"


@with_transaction
def deactivate_user_in_db(cursor, user_id):
    cursor.execute("UPDATE users SET is_active = 0, chat_id = NULL WHERE id = %s AND is_active = 1", (user_id,))
    if cursor.rowcount == 0:
        raise BusinessError("Пользователь не найден или уже деактивирован.")
    return True, "Пользователь деактивирован."


@with_transaction
def change_user_role_function(cursor, user_id, new_role):
    if new_role not in ('admin', 'cashier', 'manager'):
        raise BusinessError("Неизвестная роль.")
    cursor.execute("UPDATE users SET role = %s WHERE id = %s AND is_active = 1", (new_role, user_id))
    if cursor.rowcount == 0:
        cursor.execute("SELECT role FROM users WHERE id = %s AND is_active = 1", (user_id,))
        row = cursor.fetchone()
        if not row:
            raise BusinessError("Пользователь не найден.")
    return True, f"Роль изменена на {new_role}."


@with_transaction
def update_user_phone(cursor, user_id, new_phone_number):
    phone = normalize_phone(new_phone_number)
    if not phone:
        raise BusinessError("Некорректный номер телефона.")
    variants = _phone_variants(phone)
    placeholders = ",".join(["%s"] * len(variants))
    cursor.execute(f"SELECT id FROM users WHERE phone_number IN ({placeholders}) AND id != %s AND is_active = 1", variants + [user_id])
    if cursor.fetchone():
        raise BusinessError(f"Номер {phone} уже используется другим пользователем.")
    # Смена телефона = новый вход: отвязываем старый чат
    cursor.execute("UPDATE users SET phone_number = %s, chat_id = NULL WHERE id = %s", (phone, user_id))
    if cursor.rowcount == 0:
        raise BusinessError("Пользователь не найден.")
    return True, f"Телефон изменён на {phone}."


# ---------------------------------------------------------------- товары

@with_connection
def get_product_by_article(cursor, article_number):
    cursor.execute("SELECT * FROM products WHERE article_number = %s ORDER BY id LIMIT 1", (article_number,))
    return cursor.fetchone()


def _change_quantity(cursor, article, delta):
    cursor.execute("SELECT id, quantity FROM products WHERE article_number = %s ORDER BY id LIMIT 1 FOR UPDATE", (article,))
    row = cursor.fetchone()
    if not row:
        raise BusinessError(f"Товар {article} не найден.")
    if delta < 0 and (row['quantity'] or 0) < -delta:
        raise BusinessError(f"Недостаточно товара {article} на складе (остаток {row['quantity']}).")
    cursor.execute("UPDATE products SET quantity = quantity + %s WHERE id = %s", (delta, row['id']))
    return row['id']


@with_transaction
def update_product_quantity(cursor, product_article, quantity_change):
    _change_quantity(cursor, product_article, quantity_change)
    return True, f"Остаток товара {product_article} изменён на {quantity_change:+d} шт."


@with_transaction
def add_product_to_db(cursor, product_data: dict):
    cursor.execute("SELECT id FROM products WHERE article_number = %s", (product_data['article_number'],))
    if cursor.fetchone():
        raise BusinessError(f"Товар с артикулом {product_data['article_number']} уже существует.")
    data = dict(product_data)
    data.setdefault('arrival_date', today())
    allowed = {'article_number', 'Store', 'metal_type', 'product_type', 'model', 'size', 'weight',
               'price_per_gram', 'price', 'min_price', 'arrival_date', 'quantity', 'manufacturer',
               'commentaries', 'track_stock'}
    data = {k: v for k, v in data.items() if k in allowed}
    cols = ", ".join(f"`{k}`" for k in data)
    placeholders = ", ".join(["%s"] * len(data))
    cursor.execute(f"INSERT INTO products ({cols}) VALUES ({placeholders})", list(data.values()))
    return True, f"Товар {data['article_number']} добавлен (ID {cursor.lastrowid})."


@with_connection
def get_stock_report_data(cursor):
    cursor.execute("""
        SELECT p.article_number, p.product_type, p.model, p.metal_type, p.Store AS store_name, p.size,
               p.weight, p.quantity, p.price, p.min_price, p.manufacturer, p.arrival_date, p.commentaries
        FROM products p
        ORDER BY p.Store, p.product_type, p.model, p.article_number
    """)
    return cursor.fetchall()


@with_connection
def get_low_stock_products(cursor, threshold):
    cursor.execute("""
        SELECT article_number, model, product_type, quantity, Store
        FROM products WHERE quantity <= %s AND track_stock = 1
        ORDER BY Store, quantity, model
    """, (threshold,))
    return cursor.fetchall()


@with_connection
def get_enum_or_distinct_values(cursor, table_name, column_name, add_other_option=True):
    """Значения ENUM колонки (для ENUM «Другое» не добавляется — БД его не примет)."""
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE %s", (column_name,))
    column_info = cursor.fetchone()
    col_type = (column_info or {}).get('Type', '')
    if isinstance(col_type, bytes):
        col_type = col_type.decode()
    if col_type.lower().startswith("enum("):
        return [v.strip("'").replace("''", "'") for v in col_type[len("enum("):-1].split("','")]
    cursor.execute(f"SELECT DISTINCT `{column_name}` AS v FROM `{table_name}` WHERE `{column_name}` IS NOT NULL AND `{column_name}` != '' ORDER BY `{column_name}`")
    values = [row['v'] for row in cursor.fetchall()]
    if add_other_option and "Другое" not in values:
        values.append("Другое")
    return values


# ---------------------------------------------------------------- кассовые операции

@with_transaction
def record_sale(cursor, product_id, product_article, user_id, sale_price, payment_type):
    _change_quantity(cursor, product_article, -1)
    cursor.execute("INSERT INTO sales (product_id, user_id, sale_date, sold_price, payment_type) VALUES (%s, %s, %s, %s, %s)",
                   (product_id, user_id, now(), sale_price, payment_type))
    return True, cursor.lastrowid


@with_connection
def get_sales_for_return(cursor, article_number, days=60):
    """Продажи артикула за последние N дней с отметкой, был ли уже возврат."""
    cursor.execute("""
        SELECT s.id AS sale_id, s.sold_price, s.payment_type AS sale_payment_type, s.sale_date,
               p.id AS product_id, p.article_number, p.model AS product_model, p.product_type,
               r.id AS return_id
        FROM sales s
        JOIN products p ON s.product_id = p.id
        LEFT JOIN returns r ON r.original_sale_id = s.id
        WHERE p.article_number = %s AND s.sale_date >= %s
        ORDER BY s.sale_date DESC
        LIMIT 10
    """, (article_number, now().replace(hour=0, minute=0, second=0) - timedelta(days=days)))
    return cursor.fetchall()


@with_connection
def get_sale_details_by_id(cursor, sale_id):
    cursor.execute("""
        SELECT s.id AS sale_id, s.sold_price, s.payment_type AS sale_payment_type, s.sale_date,
               p.id AS product_id, p.article_number, p.model AS product_model, p.product_type,
               r.id AS return_id
        FROM sales s
        JOIN products p ON s.product_id = p.id
        LEFT JOIN returns r ON r.original_sale_id = s.id
        WHERE s.id = %s
    """, (sale_id,))
    return cursor.fetchone()


@with_transaction
def create_return_record(cursor, sale_id, product_article, return_price, payment_type, user_id, store_id):
    cursor.execute("SELECT s.id, s.sold_price FROM sales s WHERE s.id = %s FOR UPDATE", (sale_id,))
    sale = cursor.fetchone()
    if not sale:
        raise BusinessError("Продажа не найдена.")
    cursor.execute("SELECT id FROM returns WHERE original_sale_id = %s", (sale_id,))
    if cursor.fetchone():
        raise BusinessError("По этой продаже уже оформлен возврат.")
    if return_price <= 0 or return_price > sale['sold_price']:
        raise BusinessError("Сумма возврата должна быть больше нуля и не больше суммы продажи.")
    cursor.execute("""INSERT INTO returns (original_sale_id, product_article, return_price, payment_type, user_id, store_id, return_date)
                      VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                   (sale_id, product_article, return_price, payment_type, user_id, store_id, now()))
    return_id = cursor.lastrowid
    _change_quantity(cursor, product_article, 1)
    return True, return_id


@with_transaction
def create_buyback_record(cursor, product_article, price, payment_type, user_id, store_id,
                          description=None, metal_type=None, weight=None):
    cursor.execute("""INSERT INTO buybacks (product_article, description, metal_type, weight, buyback_price,
                                            payment_type, user_id, store_id, buyback_date)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                   (product_article, description, metal_type, weight, price, payment_type, user_id, store_id, now()))
    buyback_id = cursor.lastrowid
    if product_article:
        # Изделие из нашего ассортимента возвращается на склад
        _change_quantity(cursor, product_article, 1)
    return True, buyback_id


@with_transaction
def create_trade_in_record(cursor, new_product_article, new_sale_price, old_item_value, price_difference,
                           payment_type_difference, user_id, store_id, old_product_article=None,
                           old_item_description=None, old_metal_type=None, old_weight=None):
    _change_quantity(cursor, new_product_article, -1)
    if old_product_article:
        _change_quantity(cursor, old_product_article, 1)
    cursor.execute("""INSERT INTO trade_ins (old_product_article, old_item_description, old_metal_type, old_weight,
                                             old_item_value, new_product_article, new_sale_price, price_difference,
                                             payment_type_difference, user_id, store_id, trade_in_date)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                   (old_product_article, old_item_description, old_metal_type, old_weight, old_item_value,
                    new_product_article, new_sale_price, price_difference, payment_type_difference,
                    user_id, store_id, now()))
    return True, cursor.lastrowid


@with_transaction
def create_cash_withdrawal_record(cursor, amount, comment, user_id, store_id):
    cursor.execute("INSERT INTO cash_withdrawals (amount, comment, user_id, store_id, withdrawal_date) VALUES (%s, %s, %s, %s, %s)",
                   (amount, comment, user_id, store_id, now()))
    return True, cursor.lastrowid


# ---------------------------------------------------------------- отчёты

@with_connection
def get_period_operations(cursor, start_dt, end_dt):
    """Все операции за полуинтервал [start_dt, end_dt) для отчёта."""
    params = (start_dt, end_dt)
    result = {}
    cursor.execute("""
        SELECT s.id, s.sale_date, s.sold_price, s.payment_type,
               p.article_number, p.product_type, p.model, p.size, p.metal_type, p.weight,
               p.price AS list_price, p.Store AS store, u.name AS user_name,
               r.id AS return_id
        FROM sales s
        JOIN products p ON s.product_id = p.id
        LEFT JOIN users u ON s.user_id = u.id
        LEFT JOIN returns r ON r.original_sale_id = s.id
        WHERE s.sale_date >= %s AND s.sale_date < %s
        ORDER BY s.sale_date, s.id""", params)
    result['sales'] = cursor.fetchall()
    cursor.execute("""
        SELECT r.id, r.return_date, r.original_sale_id, r.product_article, p.model, r.return_price,
               r.payment_type, s.sale_date, s.sold_price, u.name AS user_name
        FROM returns r
        LEFT JOIN sales s ON s.id = r.original_sale_id
        LEFT JOIN products p ON p.id = s.product_id
        LEFT JOIN users u ON r.user_id = u.id
        WHERE r.return_date >= %s AND r.return_date < %s
        ORDER BY r.return_date, r.id""", params)
    result['returns'] = cursor.fetchall()
    cursor.execute("""
        SELECT b.id, b.buyback_date, b.product_article, b.description, b.metal_type, b.weight,
               b.buyback_price, b.payment_type, u.name AS user_name
        FROM buybacks b
        LEFT JOIN users u ON b.user_id = u.id
        WHERE b.buyback_date >= %s AND b.buyback_date < %s
        ORDER BY b.buyback_date, b.id""", params)
    result['buybacks'] = cursor.fetchall()
    cursor.execute("""
        SELECT t.id, t.trade_in_date, t.old_product_article, t.old_item_description, t.old_metal_type, t.old_weight,
               t.old_item_value, t.new_product_article,
               (SELECT p.model FROM products p WHERE p.article_number = t.new_product_article ORDER BY p.id LIMIT 1) AS new_model,
               t.new_sale_price,
               t.price_difference, t.payment_type_difference, u.name AS user_name
        FROM trade_ins t
        LEFT JOIN users u ON t.user_id = u.id
        WHERE t.trade_in_date >= %s AND t.trade_in_date < %s
        ORDER BY t.trade_in_date, t.id""", params)
    result['trade_ins'] = cursor.fetchall()
    cursor.execute("""
        SELECT w.id, w.withdrawal_date, w.amount, w.comment, u.name AS user_name
        FROM cash_withdrawals w
        LEFT JOIN users u ON w.user_id = u.id
        WHERE w.withdrawal_date >= %s AND w.withdrawal_date < %s
        ORDER BY w.withdrawal_date, w.id""", params)
    result['withdrawals'] = cursor.fetchall()
    return result
