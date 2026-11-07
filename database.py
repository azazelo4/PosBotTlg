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
    cursor.execute("SELECT u.*, st.name AS store FROM users u LEFT JOIN stores st ON st.id = u.store_id WHERE u.id = %s", (user_id,))
    return cursor.fetchone()


@with_transaction
def set_user_chat_id(cursor, user_id, chat_id):
    # Один чат — один пользователь: отвязываем чат от других записей
    cursor.execute("UPDATE users SET chat_id = NULL WHERE chat_id = %s AND id != %s", (chat_id, user_id))
    cursor.execute("UPDATE users SET chat_id = %s WHERE id = %s", (chat_id, user_id))
    return True, "ok"


@with_connection
def get_all_users(cursor):
    cursor.execute("SELECT u.id, u.phone_number, u.name, u.surename, u.role, u.chat_id, u.store_id, st.name AS store "
                   "FROM users u LEFT JOIN stores st ON st.id = u.store_id WHERE u.is_active = 1 ORDER BY u.role, u.name")
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
                   (phone, role, name, store_id))
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
        cursor.execute("INSERT INTO users (phone_number, role, name, is_active) VALUES (%s, 'admin', %s, 1)", (phone, name))
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


@with_transaction
def set_user_store(cursor, user_id, store_id):
    if store_id is not None:
        cursor.execute("SELECT id FROM stores WHERE id = %s AND is_active = 1", (store_id,))
        if not cursor.fetchone():
            raise BusinessError("Магазин не найден.")
    cursor.execute("UPDATE users SET store_id = %s WHERE id = %s AND is_active = 1", (store_id, user_id))
    return True, "Магазин сотрудника изменён."


# ---------------------------------------------------------------- магазины

@with_connection
def get_stores(cursor, active_only=True):
    cursor.execute("SELECT * FROM stores" + (" WHERE is_active = 1" if active_only else "") + " ORDER BY sort_order, name")
    return cursor.fetchall()


@with_connection
def get_store_counts(cursor):
    """store_id -> число товаров (для админки)"""
    cursor.execute("SELECT store_id, COUNT(*) AS c FROM products GROUP BY store_id")
    return {r['store_id']: r['c'] for r in cursor.fetchall()}


@with_transaction
def create_store(cursor, name, address=None):
    name = (name or '').strip()[:100]
    if len(name) < 2:
        raise BusinessError("Название магазина — не короче 2 символов.")
    cursor.execute("SELECT id FROM stores WHERE name = %s", (name,))
    if cursor.fetchone():
        raise BusinessError(f"Магазин «{name}» уже есть.")
    cursor.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM stores")
    cursor.execute("INSERT INTO stores (name, address, sort_order) VALUES (%s, %s, %s)",
                   (name, (address or '').strip()[:255] or None, cursor.fetchone()['n']))
    return True, f"Магазин «{name}» добавлен."


@with_transaction
def update_store(cursor, store_id, name, address=None, is_active=True):
    name = (name or '').strip()[:100]
    if len(name) < 2:
        raise BusinessError("Название магазина — не короче 2 символов.")
    cursor.execute("SELECT id FROM stores WHERE name = %s AND id != %s", (name, store_id))
    if cursor.fetchone():
        raise BusinessError(f"Магазин «{name}» уже есть.")
    if not is_active:
        cursor.execute("SELECT COUNT(*) AS c FROM stores WHERE is_active = 1 AND id != %s", (store_id,))
        if cursor.fetchone()['c'] == 0:
            raise BusinessError("Нельзя отключить последний активный магазин.")
    cursor.execute("UPDATE stores SET name = %s, address = %s, is_active = %s WHERE id = %s",
                   (name, (address or '').strip()[:255] or None, 1 if is_active else 0, store_id))
    if cursor.rowcount == 0:
        cursor.execute("SELECT id FROM stores WHERE id = %s", (store_id,))
        if not cursor.fetchone():
            raise BusinessError("Магазин не найден.")
    return True, f"Магазин «{name}» сохранён."


# ---------------------------------------------------------------- товары

@with_connection
def get_product_by_article(cursor, article_number):
    cursor.execute("SELECT p.*, st.name AS Store FROM products p LEFT JOIN stores st ON st.id = p.store_id WHERE p.article_number = %s ORDER BY p.id LIMIT 1", (article_number,))
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


PRODUCT_FIELDS = ('article_number', 'store_id', 'metal_type', 'product_type', 'model', 'size', 'fineness', 'inserts',
                  'weight', 'price_per_gram', 'price', 'min_price', 'arrival_date', 'quantity', 'manufacturer',
                  'commentaries', 'track_stock')


@with_transaction
def add_product_to_db(cursor, product_data: dict):
    cursor.execute("SELECT id FROM products WHERE article_number = %s", (product_data['article_number'],))
    if cursor.fetchone():
        raise BusinessError(f"Товар с артикулом {product_data['article_number']} уже существует.")
    data = dict(product_data)
    data.setdefault('arrival_date', today())
    data = {k: v for k, v in data.items() if k in PRODUCT_FIELDS}
    cols = ", ".join(f"`{k}`" for k in data)
    placeholders = ", ".join(["%s"] * len(data))
    cursor.execute(f"INSERT INTO products ({cols}) VALUES ({placeholders})", list(data.values()))
    return True, f"Товар {data['article_number']} добавлен (ID {cursor.lastrowid})."


@with_connection
def get_stock_report_data(cursor):
    cursor.execute("""
        SELECT p.article_number, p.product_type, p.model, p.metal_type, st.name AS store_name, p.store_id, p.size,
               p.fineness, p.inserts,
               p.weight, p.quantity, p.price, p.min_price, p.manufacturer, p.arrival_date, p.commentaries
        FROM products p LEFT JOIN stores st ON st.id = p.store_id
        ORDER BY st.sort_order, st.name, p.product_type, p.model, p.article_number
    """)
    return cursor.fetchall()


@with_connection
def get_low_stock_products(cursor, threshold):
    cursor.execute("""
        SELECT p.article_number, p.model, p.product_type, p.quantity, st.name AS Store
        FROM products p LEFT JOIN stores st ON st.id = p.store_id WHERE p.quantity <= %s AND p.track_stock = 1
        ORDER BY st.sort_order, p.quantity, p.model
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


@with_connection
def search_products(cursor, query=None, store=None, in_stock=None, limit=50, offset=0):
    """Поиск товаров для админки. Возвращает (строки, всего)."""
    where, params = [], []
    if query:
        where.append("(p.article_number LIKE %s OR p.model LIKE %s)")
        params += [f"%{query}%", f"%{query}%"]
    if store:
        where.append("p.store_id = %s")
        params.append(store)
    if in_stock is True:
        where.append("p.quantity > 0")
    elif in_stock is False:
        where.append("p.quantity <= 0")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    cursor.execute(f"SELECT COUNT(*) AS c FROM products p {clause}", params)
    total = cursor.fetchone()['c']
    cursor.execute(f"SELECT p.*, st.name AS Store FROM products p LEFT JOIN stores st ON st.id = p.store_id {clause} ORDER BY p.arrival_date DESC, p.id DESC LIMIT %s OFFSET %s",
                   params + [int(limit), int(offset)])
    return cursor.fetchall(), total


@with_connection
def get_product_by_id(cursor, product_id):
    cursor.execute("SELECT p.*, st.name AS Store FROM products p LEFT JOIN stores st ON st.id = p.store_id WHERE p.id = %s", (product_id,))
    return cursor.fetchone()


@with_connection
def get_products_by_articles(cursor, articles):
    if not articles:
        return []
    placeholders = ",".join(["%s"] * len(articles))
    cursor.execute(f"SELECT p.*, st.name AS Store FROM products p LEFT JOIN stores st ON st.id = p.store_id WHERE p.article_number IN ({placeholders})", list(articles))
    rows = {r['article_number']: r for r in cursor.fetchall()}
    return [rows[a] for a in articles if a in rows]


@with_connection
def get_products_for_labels(cursor, store=None, arrived_from=None, only_in_stock=True):
    where, params = [], []
    if store:
        where.append("p.store_id = %s")
        params.append(store)
    if arrived_from:
        where.append("p.arrival_date >= %s")
        params.append(arrived_from)
    if only_in_stock:
        where.append("p.quantity > 0")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    cursor.execute(f"SELECT p.*, st.name AS Store FROM products p LEFT JOIN stores st ON st.id = p.store_id {clause} ORDER BY st.sort_order, p.product_type, p.article_number", params)
    return cursor.fetchall()


@with_transaction
def update_product(cursor, product_id, data: dict):
    data = {k: v for k, v in data.items() if k in PRODUCT_FIELDS}
    if not data:
        raise BusinessError("Нет данных для сохранения.")
    if 'article_number' in data:
        cursor.execute("SELECT id FROM products WHERE article_number = %s AND id != %s", (data['article_number'], product_id))
        if cursor.fetchone():
            raise BusinessError(f"Артикул {data['article_number']} уже занят другим товаром.")
    if data.get('min_price') is not None and data.get('price') is not None and data['min_price'] > data['price']:
        raise BusinessError("Минимальная цена не может быть выше цены.")
    sets = ", ".join(f"`{k}` = %s" for k in data)
    cursor.execute(f"UPDATE products SET {sets} WHERE id = %s", list(data.values()) + [product_id])
    cursor.execute("SELECT id FROM products WHERE id = %s", (product_id,))
    if not cursor.fetchone():
        raise BusinessError("Товар не найден.")
    return True, "Товар сохранён."


@with_transaction
def import_products(cursor, rows, update_existing=True):
    """Загружает товары: новые добавляет, существующие (по артикулу) обновляет."""
    created = updated = skipped = 0
    for row in rows:
        data = {k: v for k, v in row.items() if k in PRODUCT_FIELDS}
        cursor.execute("SELECT id FROM products WHERE article_number = %s ORDER BY id LIMIT 1 FOR UPDATE", (data['article_number'],))
        existing = cursor.fetchone()
        if existing:
            if not update_existing:
                skipped += 1
                continue
            fields = {k: v for k, v in data.items() if k != 'article_number' and v is not None}
            if fields:
                sets = ", ".join(f"`{k}` = %s" for k in fields)
                cursor.execute(f"UPDATE products SET {sets} WHERE id = %s", list(fields.values()) + [existing['id']])
                # новая цена могла стать ниже прежней минимальной
                cursor.execute("UPDATE products SET min_price = LEAST(min_price, price) WHERE id = %s", (existing['id'],))
            updated += 1
        else:
            data.setdefault('arrival_date', today())
            cols = ", ".join(f"`{k}`" for k in data)
            cursor.execute(f"INSERT INTO products ({cols}) VALUES ({', '.join(['%s'] * len(data))})", list(data.values()))
            created += 1
    return True, {'created': created, 'updated': updated, 'skipped': skipped}


@with_connection
def get_today_sales_by_user(cursor, user_id, start_dt, end_dt):
    cursor.execute("""
        SELECT s.id, s.sale_date, s.sold_price, s.payment_type, p.article_number, p.model
        FROM sales s JOIN products p ON p.id = s.product_id
        WHERE s.user_id = %s AND s.sale_date >= %s AND s.sale_date < %s
        ORDER BY s.sale_date DESC""", (user_id, start_dt, end_dt))
    return cursor.fetchall()


# ---------------------------------------------------------------- вход в веб-админку

@with_transaction
def create_login_token(cursor, user_id, token_hash, expires_at):
    cursor.execute("DELETE FROM web_login_tokens WHERE expires_at < %s OR used_at IS NOT NULL", (now(),))
    cursor.execute("INSERT INTO web_login_tokens (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                   (token_hash, user_id, expires_at))
    return True, "ok"


@with_transaction
def consume_login_token(cursor, token_hash):
    """Одноразовый токен: возвращает (True, user_id) или (False, причина)."""
    cursor.execute("SELECT user_id, expires_at, used_at FROM web_login_tokens WHERE token_hash = %s FOR UPDATE", (token_hash,))
    row = cursor.fetchone()
    if not row or row['used_at'] is not None or row['expires_at'] < now():
        raise BusinessError("Ссылка для входа недействительна или устарела. Запросите новую в боте.")
    cursor.execute("UPDATE web_login_tokens SET used_at = %s WHERE token_hash = %s", (now(), token_hash))
    return True, row['user_id']


# ---------------------------------------------------------------- вход на кассу ПК

@with_transaction
def create_kassa_login(cursor, token_hash, expires_at, client_info=None):
    cursor.execute("DELETE FROM kassa_logins WHERE expires_at < %s", (now() - timedelta(days=1),))
    cursor.execute("INSERT INTO kassa_logins (token_hash, client_info, created_at, expires_at) VALUES (%s, %s, %s, %s)",
                   (token_hash, (client_info or "")[:255] or None, now(), expires_at))
    return True, "ok"


@with_connection
def get_kassa_login(cursor, token_hash):
    cursor.execute("SELECT * FROM kassa_logins WHERE token_hash = %s", (token_hash,))
    return cursor.fetchone()


@with_transaction
def decide_kassa_login(cursor, token_hash, user_id, approve):
    """Сотрудник в боте подтверждает или отклоняет вход. (True, запись) или (False, причина)."""
    cursor.execute("SELECT * FROM kassa_logins WHERE token_hash = %s FOR UPDATE", (token_hash,))
    row = cursor.fetchone()
    if not row or row['expires_at'] < now():
        raise BusinessError("QR-код устарел. Обновите страницу кассы на компьютере и отсканируйте новый.")
    if row['status'] != 'pending':
        raise BusinessError("Этот QR-код уже использован. Обновите страницу кассы и отсканируйте новый.")
    status = 'approved' if approve else 'denied'
    cursor.execute("UPDATE kassa_logins SET status = %s, user_id = %s, decided_at = %s WHERE token_hash = %s",
                   (status, user_id, now(), token_hash))
    row.update(status=status, user_id=user_id)
    return True, row


@with_transaction
def consume_kassa_login(cursor, token_hash):
    """Браузер забирает подтверждённый вход (один раз). (True, user_id) или (False, статус)."""
    cursor.execute("SELECT status, user_id, expires_at FROM kassa_logins WHERE token_hash = %s FOR UPDATE", (token_hash,))
    row = cursor.fetchone()
    if not row:
        return False, 'expired'
    if row['status'] == 'approved':
        cursor.execute("UPDATE kassa_logins SET status = 'used' WHERE token_hash = %s", (token_hash,))
        return True, row['user_id']
    if row['status'] == 'pending' and row['expires_at'] < now():
        return False, 'expired'
    return False, 'used' if row['status'] == 'used' else row['status']


# ---------------------------------------------------------------- кассовые операции

@with_transaction
def record_sale(cursor, product_id, product_article, user_id, sale_price, payment_type):
    _change_quantity(cursor, product_article, -1)
    cursor.execute("INSERT INTO sales (product_id, user_id, sale_date, sold_price, payment_type, store_id) "
                   "SELECT %s, %s, %s, %s, %s, store_id FROM products WHERE id = %s",
                   (product_id, user_id, now(), sale_price, payment_type, product_id))
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
    cursor.execute("SELECT s.id, s.sold_price, s.store_id FROM sales s WHERE s.id = %s FOR UPDATE", (sale_id,))
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
                   (sale_id, product_article, return_price, payment_type, user_id, sale['store_id'] or store_id, now()))
    return_id = cursor.lastrowid
    _change_quantity(cursor, product_article, 1)
    return True, return_id


@with_transaction
def create_buyback_record(cursor, product_article, price, payment_type, user_id, store_id,
                          description=None, metal_type=None, weight=None):
    if product_article:
        # своё изделие — магазин, к которому оно относится
        cursor.execute("SELECT store_id FROM products WHERE article_number = %s ORDER BY id LIMIT 1", (product_article,))
        row = cursor.fetchone()
        if row and row['store_id']:
            store_id = row['store_id']
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
    new_id = _change_quantity(cursor, new_product_article, -1)
    cursor.execute("SELECT store_id FROM products WHERE id = %s", (new_id,))
    store_id = cursor.fetchone()['store_id'] or store_id  # магазин нового товара
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
def get_period_operations(cursor, start_dt, end_dt, store_id=None):
    """Все операции за полуинтервал [start_dt, end_dt) для отчёта; store_id — фильтр по магазину.

    Магазин операции: продажа — магазин товара на момент продажи, возврат — магазин продажи,
    trade-in — магазин нового товара, скупка и инкассация — магазин сотрудника (или выбранный).
    """
    result = {}

    def run(sql, alias):
        flt = f" AND {alias}.store_id = %s" if store_id else ""
        cursor.execute(sql.replace("{STORE_FILTER}", flt), (start_dt, end_dt) + ((store_id,) if store_id else ()))
        return cursor.fetchall()

    result['sales'] = run("""
        SELECT s.id, s.sale_date, s.sold_price, s.payment_type, s.store_id, st.name AS store,
               p.article_number, p.product_type, p.model, p.size, p.metal_type, p.weight,
               p.price AS list_price, u.name AS user_name, r.id AS return_id
        FROM sales s
        JOIN products p ON s.product_id = p.id
        LEFT JOIN stores st ON st.id = s.store_id
        LEFT JOIN users u ON s.user_id = u.id
        LEFT JOIN returns r ON r.original_sale_id = s.id
        WHERE s.sale_date >= %s AND s.sale_date < %s{STORE_FILTER}
        ORDER BY s.sale_date, s.id""", "s")
    result['returns'] = run("""
        SELECT r.id, r.return_date, r.original_sale_id, r.product_article, p.model, r.return_price,
               r.payment_type, r.store_id, st.name AS store, s.sale_date, s.sold_price, u.name AS user_name
        FROM returns r
        LEFT JOIN sales s ON s.id = r.original_sale_id
        LEFT JOIN products p ON p.id = s.product_id
        LEFT JOIN stores st ON st.id = r.store_id
        LEFT JOIN users u ON r.user_id = u.id
        WHERE r.return_date >= %s AND r.return_date < %s{STORE_FILTER}
        ORDER BY r.return_date, r.id""", "r")
    result['buybacks'] = run("""
        SELECT b.id, b.buyback_date, b.product_article, b.description, b.metal_type, b.weight,
               b.buyback_price, b.payment_type, b.store_id, st.name AS store, u.name AS user_name
        FROM buybacks b
        LEFT JOIN stores st ON st.id = b.store_id
        LEFT JOIN users u ON b.user_id = u.id
        WHERE b.buyback_date >= %s AND b.buyback_date < %s{STORE_FILTER}
        ORDER BY b.buyback_date, b.id""", "b")
    result['trade_ins'] = run("""
        SELECT t.id, t.trade_in_date, t.old_product_article, t.old_item_description, t.old_metal_type, t.old_weight,
               t.old_item_value, t.new_product_article,
               (SELECT p.model FROM products p WHERE p.article_number = t.new_product_article ORDER BY p.id LIMIT 1) AS new_model,
               t.new_sale_price, t.price_difference, t.payment_type_difference, t.store_id, st.name AS store,
               u.name AS user_name
        FROM trade_ins t
        LEFT JOIN stores st ON st.id = t.store_id
        LEFT JOIN users u ON t.user_id = u.id
        WHERE t.trade_in_date >= %s AND t.trade_in_date < %s{STORE_FILTER}
        ORDER BY t.trade_in_date, t.id""", "t")
    result['withdrawals'] = run("""
        SELECT w.id, w.withdrawal_date, w.amount, w.comment, w.store_id, st.name AS store, u.name AS user_name
        FROM cash_withdrawals w
        LEFT JOIN stores st ON st.id = w.store_id
        LEFT JOIN users u ON w.user_id = u.id
        WHERE w.withdrawal_date >= %s AND w.withdrawal_date < %s{STORE_FILTER}
        ORDER BY w.withdrawal_date, w.id""", "w")
    return result
