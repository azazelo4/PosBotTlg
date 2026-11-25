import logging
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps

import mysql.connector
from mysql.connector import pooling, Error

from core import config
from core.config import DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME
from utils import mark_codes
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
    """Товар по артикулу или по коду «#<id>» (штрихкод ценника, если артикул в него не поместился)."""
    product_id = product_id_from_code(article_number)
    if product_id:
        cursor.execute("SELECT p.*, st.name AS Store FROM products p LEFT JOIN stores st ON st.id = p.store_id WHERE p.id = %s", (product_id,))
        row = cursor.fetchone()
        if row:
            return row
    cursor.execute("SELECT p.*, st.name AS Store FROM products p LEFT JOIN stores st ON st.id = p.store_id WHERE p.article_number = %s ORDER BY p.id LIMIT 1", (article_number,))
    row = cursor.fetchone()
    if row is None and article_number:
        # штрихкод старого ценника (из прежней программы): касса сканирует его вместо артикула
        cursor.execute("SELECT p.*, st.name AS Store FROM products p LEFT JOIN stores st ON st.id = p.store_id "
                       "WHERE p.barcode = %s ORDER BY p.id LIMIT 1", (str(article_number).strip(),))
        row = cursor.fetchone()
    return row


@with_connection
def find_by_mark_code(cursor, raw):
    """Код маркировки → {'code': строка product_codes, 'product': товар} или None."""
    if not mark_codes.is_valid(raw):
        return None
    cursor.execute("SELECT * FROM product_codes WHERE code_key = %s", (mark_codes.key(raw),))
    code = cursor.fetchone()
    if not code:
        return None
    cursor.execute("SELECT p.*, st.name AS Store FROM products p LEFT JOIN stores st ON st.id = p.store_id WHERE p.id = %s",
                   (code['product_id'],))
    return {"code": code, "product": cursor.fetchone()}


def product_id_from_code(code):
    """'#123' → 123, иначе None. Такой код печатается штрихкодом вместо длинного артикула."""
    code = str(code or "").strip()
    if len(code) > 1 and code[0] == "#" and code[1:].isdigit() and len(code) <= 10:
        return int(code[1:])
    return None


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
                  'commentaries', 'track_stock', 'cost_price', 'is_marked', 'barcode')


def _check_barcode_free(cursor, barcode, product_id=None):
    """Штрихкод однозначно указывает на товар: один штрихкод — один товар."""
    if not barcode:
        return
    cursor.execute("""SELECT article_number, barcode = %s AS same_barcode FROM products
                      WHERE (barcode = %s OR article_number = %s) AND id != %s LIMIT 1""",
                   (barcode, barcode, barcode, product_id or 0))
    row = cursor.fetchone()
    if row:
        if row['same_barcode']:
            raise BusinessError(f"Штрихкод {barcode} уже у товара {row['article_number']}.")
        raise BusinessError(f"Штрихкод {barcode} совпадает с артикулом другого товара.")


@with_transaction
def add_product_to_db(cursor, product_data: dict):
    cursor.execute("SELECT id FROM products WHERE article_number = %s", (product_data['article_number'],))
    if cursor.fetchone():
        raise BusinessError(f"Товар с артикулом {product_data['article_number']} уже существует.")
    data = dict(product_data)
    data.setdefault('arrival_date', today())
    data = {k: v for k, v in data.items() if k in PRODUCT_FIELDS}
    _check_barcode_free(cursor, data.get('barcode'))
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
def search_products(cursor, query=None, store=None, in_stock=None, limit=50, offset=0, inv_mark=None):
    """Поиск товаров для админки. Возвращает (строки, всего)."""
    where, params = [], []
    if inv_mark:
        where.append("p.inv_mark = %s")
        params.append(inv_mark)
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
def products_by_barcodes(cursor, barcodes):
    if not barcodes:
        return []
    marks = ','.join(['%s'] * len(barcodes))
    cursor.execute(f"SELECT id, article_number, barcode FROM products WHERE barcode IN ({marks}) OR article_number IN ({marks})",
                   list(barcodes) * 2)
    return cursor.fetchall()


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
    if 'barcode' in data:
        _check_barcode_free(cursor, data['barcode'], product_id)
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
        if data.get('barcode') and not (existing and not update_existing):
            _check_barcode_free(cursor, data['barcode'], existing['id'] if existing else None)
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
        WHERE s.user_id = %s AND s.sale_date >= %s AND s.sale_date < %s AND s.source IS NULL
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

MAX_RECEIPT_ITEMS = 50
MIXED_PAYMENT = "Смешанная"      # в receipts/sales.payment_type; суммы по способам — в receipt_payments


def _split_payments(payment, total):
    """Оплата чека → [(способ, сумма), …]. payment — способ строкой (вся сумма) или список пар для смешанной оплаты."""
    if isinstance(payment, str):
        return [(payment, total)]
    parts = []
    for row in payment or []:
        kind, amount = (row.get('type'), row.get('amount')) if isinstance(row, dict) else row
        if amount is None or amount <= 0:
            raise BusinessError("Смешанная оплата: укажите сумму больше нуля для каждого способа.")
        if any(kind == k for k, _ in parts):
            raise BusinessError(f"Смешанная оплата: способ «{kind}» указан дважды.")
        parts.append((kind, amount))
    if not parts:
        raise BusinessError("Выберите способ оплаты.")
    paid = sum(a for _, a in parts)
    if paid != total:
        raise BusinessError(f"Смешанная оплата: суммы по способам ({int(paid)}) не равны итогу чека ({int(total)}).")
    return parts


def payments_label(payments):
    """«Наличные 100 000 ₸ + Kaspi 50 000 ₸» или просто способ, если он один."""
    from utils.common import money
    if len(payments) == 1:
        return payments[0][0]
    return " + ".join(f"{kind} {money(amount)}" for kind, amount in payments)


def _allocate(sales, payments):
    """Раскладывает оплаты чека по его позициям «водопадом»: сумма по каждой позиции и по каждому способу точная.
    Возвращает {id продажи: [(способ, сумма), …]} — для движения денег по способам и магазинам."""
    queue = [[kind, float(amount)] for kind, amount in payments]
    result = {}
    for s in sales:
        need, parts = float(s['sold_price']), []
        while need > 0.004 and queue:
            take = min(need, queue[0][1])
            if take > 0:
                parts.append((queue[0][0], take))
            queue[0][1] -= take
            need -= take
            if queue[0][1] <= 0.004:
                queue.pop(0)
        result[s['id']] = parts
    return result


SELLABLE_CODE = ('in_stock', 'returned')
CODE_STATUS_TITLES = {'in_stock': "на складе", 'sold': "продан", 'returned': "возвращён", 'written_off': "списан"}


def marking_required(product):
    """Маркируемый товар при включённом учёте продаётся только с кодом."""
    return bool(config.MARKING_ENABLED and product and product.get('is_marked'))


def _take_code(cursor, raw, product, taken):
    """Проверяет код маркировки для продажи товара и блокирует его. Возвращает строку product_codes."""
    if not mark_codes.is_valid(raw):
        raise BusinessError(f"{product['article_number']}: код маркировки не распознан — отсканируйте DataMatrix ещё раз.")
    k = mark_codes.key(raw)
    if k in taken:
        raise BusinessError(f"Код {mark_codes.short(raw)} уже есть в этом чеке.")
    cursor.execute("SELECT * FROM product_codes WHERE code_key = %s FOR UPDATE", (k,))
    code = cursor.fetchone()
    if not code:
        raise BusinessError(f"Код {mark_codes.short(raw)} не принят на склад. Примите его в разделе «Маркировка».")
    if code['product_id'] != product['id']:
        raise BusinessError(f"Код {mark_codes.short(raw)} относится к другому товару.")
    if code['status'] not in SELLABLE_CODE:
        raise BusinessError(f"Код {mark_codes.short(raw)} уже {CODE_STATUS_TITLES[code['status']]}.")
    taken.add(k)
    return code


def _enqueue_fiscal(cursor, kind, ref_id, store_id, total, user_id):
    """Поставить операцию в очередь фискализации (в той же транзакции). Только если фискализация включена
    и у магазина указана касса Webkassa. Возвращает id записи очереди или None."""
    if not config.FISCAL_ENABLED or not store_id:
        return None
    cursor.execute("SELECT cashbox FROM stores WHERE id = %s", (store_id,))
    row = cursor.fetchone()
    if not row or not (row.get('cashbox') or '').strip():
        return None
    moment = now()
    # Номер для Webkassa (ExternalCheckNumber): повтор с тем же номером не создаст второй чек
    external = f"pb-{kind}-{ref_id}-{secrets.token_hex(4)}"
    cursor.execute("""INSERT INTO fiscal_receipts (kind, ref_id, store_id, cashbox, total, user_id, status, next_try_at,
                                                   external_number, created_at, updated_at)
                      VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s)""",
                   (kind, ref_id, store_id, row['cashbox'].strip(), total, user_id, moment, external, moment, moment))
    return cursor.lastrowid


def _upsert_customer(cursor, phone, name=None, source='kassa', user_id=None, birthday=None):
    """Клиент по телефону (с согласием на хранение номера). Возвращает id. Имя и день рождения не затираются
    пустыми; известный день рождения с кассы не меняется (правится в карточке клиента)."""
    norm = normalize_phone(phone)
    if not norm:
        raise BusinessError("Телефон клиента: введите номер вида +7 701 123 45 67.")
    name = (name or "").strip()[:100] or None
    moment = now()
    cursor.execute("SELECT id, name, birthday FROM customers WHERE phone = %s FOR UPDATE", (norm,))
    row = cursor.fetchone()
    if row:
        if name and not row['name']:
            cursor.execute("UPDATE customers SET name = %s, updated_at = %s WHERE id = %s", (name, moment, row['id']))
        if birthday and not row['birthday']:
            cursor.execute("UPDATE customers SET birthday = %s, updated_at = %s WHERE id = %s",
                           (birthday, moment, row['id']))
        return row['id']
    cursor.execute("""INSERT INTO customers (phone, name, birthday, consent_at, consent_source, consent_by, created_at,
                                             updated_at)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                   (norm, name, birthday, moment, source, user_id, moment, moment))
    return cursor.lastrowid


# ---------------------------------------------------------------- свой магазин

def foreign_store_error(user, store_id, store_name=None, kind='sale'):
    """Текст отказа, если сотрудник закреплён за магазином, а операция — по товару (продаже) другого магазина.
    None — можно. Без ограничений: администратор, сотрудник «Все магазины», товар без магазина."""
    if not user or user.get('role') == 'admin' or not user.get('store_id') or not store_id or store_id == user['store_id']:
        return None
    where = f"«{store_name}»" if store_name else "другом магазине"
    if kind == 'return':
        return (f"Эта продажа была в магазине {where} — возврат оформляет сотрудник этого магазина "
                "или администратор.")
    return (f"Этот товар числится в магазине {where} — его продаёт продавец этого магазина. Если изделие перевезли, "
            "менеджер меняет магазин в карточке товара.")


def _check_store(cursor, user_id, store_id, kind='sale'):
    """Проверка «свой магазин» в той же транзакции, что и операция: обойти её через другой путь нельзя."""
    if not store_id or not user_id:
        return
    cursor.execute("SELECT u.role, u.store_id, st.name AS store FROM users u LEFT JOIN stores st ON st.id = %s "
                   "WHERE u.id = %s", (store_id, user_id))
    row = cursor.fetchone()
    error = foreign_store_error(row, store_id, row['store'] if row else None, kind)
    if error:
        raise BusinessError(error)


def _record_receipt(cursor, items, user_id, payment_type, store_id=None, customer=None):
    """Чек: позиции [(product_id, цена[, код маркировки]), ...] одним платежом. Позиция — строка sales (одна штука).

    Проверяет остаток (с учётом одинаковых позиций в чеке), минимальную цену и коды маркировки, списывает товар.
    customer — {'phone', 'name'}: клиент дал согласие на хранение номера, чек привязывается к нему.
    Возвращает {'receipt_id', 'sale_ids', 'total', 'fiscal', 'customer_id', 'public_token'}.
    """
    if not items:
        raise BusinessError("Чек пуст — добавьте товар.")
    if len(items) > MAX_RECEIPT_ITEMS:
        raise BusinessError(f"В одном чеке — не больше {MAX_RECEIPT_ITEMS} позиций.")
    items = [(it[0], it[1], it[2] if len(it) > 2 else None) for it in items]
    products, need, codes, taken = {}, {}, [], set()
    for product_id, price, raw_code in items:
        if product_id not in products:
            cursor.execute("SELECT id, article_number, model, price, min_price, quantity, store_id, is_marked "
                           "FROM products WHERE id = %s FOR UPDATE", (product_id,))
            row = cursor.fetchone()
            if not row:
                raise BusinessError("Товар не найден.")
            products[product_id] = row
        p = products[product_id]
        need[product_id] = need.get(product_id, 0) + 1
        if (p['quantity'] or 0) < need[product_id]:
            raise BusinessError(f"Недостаточно товара {p['article_number']} на складе (остаток {p['quantity'] or 0}).")
        if price is None or price <= 0:
            raise BusinessError(f"Укажите сумму для {p['article_number']}.")
        if price < (p['min_price'] or 0):
            raise BusinessError(f"{p['article_number']}: сумма {int(price)} ниже минимальной цены {int(p['min_price'])}.")
        if raw_code:
            codes.append(_take_code(cursor, raw_code, p, taken))
        elif marking_required(p):
            raise BusinessError(f"{p['article_number']}: маркируемый товар — отсканируйте код маркировки DataMatrix.")
        else:
            codes.append(None)
    stores = {p['store_id'] for p in products.values()}
    if len(stores) > 1:
        raise BusinessError("В одном чеке — товары одного магазина.")
    receipt_store = stores.pop() or store_id
    _check_store(cursor, user_id, receipt_store)
    total = sum(price for _, price, _ in items)
    payments = _split_payments(payment_type, total)
    payment_type = payments[0][0] if len(payments) == 1 else MIXED_PAYMENT
    moment = now()
    customer_id = None
    if customer and (customer.get('phone') or '').strip():
        customer_id = _upsert_customer(cursor, customer['phone'], customer.get('name'), 'kassa', user_id,
                                       customer.get('birthday'))
    token = secrets.token_hex(16)
    cursor.execute("""INSERT INTO receipts (store_id, user_id, created_at, total, items_count, payment_type, public_token,
                                            customer_id)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                   (receipt_store, user_id, moment, total, len(items), payment_type, token, customer_id))
    receipt_id = cursor.lastrowid
    for kind, amount in payments:
        cursor.execute("INSERT INTO receipt_payments (receipt_id, payment_type, amount) VALUES (%s, %s, %s)",
                       (receipt_id, kind, amount))
    sale_ids = []
    for (product_id, price, _), code in zip(items, codes):
        p = products[product_id]
        cursor.execute("""INSERT INTO sales (product_id, user_id, sale_date, sold_price, payment_type, store_id,
                                             receipt_id, list_price, mark_code_id)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                       (product_id, user_id, moment, price, payment_type, p['store_id'] or store_id, receipt_id,
                        p['price'], code['id'] if code else None))
        sale_ids.append(cursor.lastrowid)
        if code:
            cursor.execute("UPDATE product_codes SET status = 'sold', sale_id = %s, updated_at = %s WHERE id = %s",
                           (cursor.lastrowid, moment, code['id']))
    for product_id, count in need.items():
        cursor.execute("UPDATE products SET quantity = quantity - %s WHERE id = %s", (count, product_id))
    fiscal_id = _enqueue_fiscal(cursor, 'sale', receipt_id, receipt_store, total, user_id)
    return {"receipt_id": receipt_id, "sale_ids": sale_ids, "total": total, "fiscal": bool(fiscal_id),
            "customer_id": customer_id, "public_token": token, "payment": payments_label(payments)}


@with_transaction
def record_receipt(cursor, items, user_id, payment_type, store_id=None, customer=None):
    """(True, {'receipt_id', 'sale_ids', 'total', …}) или (False, причина)."""
    return True, _record_receipt(cursor, items, user_id, payment_type, store_id, customer)


@with_transaction
def record_sale(cursor, product_id, product_article, user_id, sale_price, payment_type, mark_code=None):
    """Продажа одного товара — чек из одной позиции. Возвращает (True, id продажи)."""
    result = _record_receipt(cursor, [(product_id, sale_price, mark_code)], user_id, payment_type)
    return True, result['sale_ids'][0]


@with_connection
def get_receipt(cursor, receipt_id=None, token=None):
    """Чек с позициями — по id или по публичному токену (электронный чек)."""
    where, value = ("r.public_token = %s", token) if token else ("r.id = %s", receipt_id)
    if value is None:
        return None
    cursor.execute(f"""SELECT r.*, st.name AS store, st.address AS store_address, u.name AS user_name,
                              c.phone AS customer_phone, c.name AS customer_name, c.chat_id AS customer_chat
                       FROM receipts r
                       LEFT JOIN stores st ON st.id = r.store_id LEFT JOIN users u ON u.id = r.user_id
                       LEFT JOIN customers c ON c.id = r.customer_id
                       WHERE {where}""", (value,))
    receipt = cursor.fetchone()
    if receipt:
        cursor.execute("""SELECT s.id AS sale_id, s.sold_price, s.list_price, p.article_number, p.model, p.product_type,
                                 p.metal_type, p.fineness, p.weight, p.size, p.inserts, r.id AS return_id,
                                 r.return_price
                          FROM sales s JOIN products p ON p.id = s.product_id
                          LEFT JOIN returns r ON r.original_sale_id = s.id
                          WHERE s.receipt_id = %s ORDER BY s.id""", (receipt['id'],))
        receipt['items'] = cursor.fetchall()
        cursor.execute("""SELECT status, check_number, ticket_url FROM fiscal_receipts WHERE kind = 'sale' AND ref_id = %s
                          ORDER BY id DESC LIMIT 1""", (receipt['id'],))
        receipt['fiscal'] = cursor.fetchone()
        cursor.execute("SELECT payment_type, amount FROM receipt_payments WHERE receipt_id = %s ORDER BY id", (receipt['id'],))
        # чеки до смешанной оплаты: одна оплата на всю сумму
        receipt['payments'] = ([(r['payment_type'], r['amount']) for r in cursor.fetchall()]
                               or [(receipt['payment_type'], receipt['total'])])
        receipt['payment_label'] = payments_label(receipt['payments'])
    return receipt


def own_store(user):
    """Магазин, которым ограничены операции сотрудника, или None (администратор, «Все магазины»)."""
    if not user or user.get('role') == 'admin':
        return None
    return user.get('store_id') or None


@with_connection
def get_sales_for_return(cursor, article_number, days=60, store_id=None):
    """Продажи артикула за последние N дней с отметкой, был ли уже возврат. store_id — только продажи этого
    магазина (сотрудник, закреплённый за магазином): фильтр до LIMIT, иначе старая продажа своего магазина
    могла не попасть в выдачу из-за более новых продаж того же изделия в другом."""
    product_id = product_id_from_code(article_number)
    if product_id:
        cursor.execute("SELECT article_number FROM products WHERE id = %s", (product_id,))
        row = cursor.fetchone()
        if row:
            article_number = row['article_number']
    else:
        cursor.execute("SELECT id FROM products WHERE article_number = %s LIMIT 1", (article_number,))
        if not cursor.fetchone():
            cursor.execute("SELECT article_number FROM products WHERE barcode = %s ORDER BY id LIMIT 1", (article_number,))
            row = cursor.fetchone()
            if row:                                       # отсканировали штрихкод старого ценника
                article_number = row['article_number']
    cursor.execute(f"""
        SELECT s.id AS sale_id, s.sold_price, s.payment_type AS sale_payment_type, s.sale_date,
               p.id AS product_id, p.article_number, p.model AS product_model, p.product_type,
               r.id AS return_id, s.store_id, st.name AS store
        FROM sales s
        JOIN products p ON s.product_id = p.id
        LEFT JOIN returns r ON r.original_sale_id = s.id
        LEFT JOIN stores st ON st.id = s.store_id
        WHERE p.article_number = %s AND s.sale_date >= %s{" AND (s.store_id = %s OR s.store_id IS NULL)" if store_id else ""}
        ORDER BY s.sale_date DESC
        LIMIT 10
    """, (article_number, now().replace(hour=0, minute=0, second=0) - timedelta(days=days), *((store_id,) if store_id else ())))
    return cursor.fetchall()


@with_connection
def get_sale_details_by_id(cursor, sale_id):
    cursor.execute("""
        SELECT s.id AS sale_id, s.sold_price, s.payment_type AS sale_payment_type, s.sale_date,
               p.id AS product_id, p.article_number, p.model AS product_model, p.product_type,
               r.id AS return_id, s.store_id, st.name AS store
        FROM sales s
        JOIN products p ON s.product_id = p.id
        LEFT JOIN returns r ON r.original_sale_id = s.id
        LEFT JOIN stores st ON st.id = s.store_id
        WHERE s.id = %s
    """, (sale_id,))
    return cursor.fetchone()


@with_transaction
def create_return_record(cursor, sale_id, product_article, return_price, payment_type, user_id, store_id):
    cursor.execute("SELECT s.id, s.sold_price, s.store_id, s.mark_code_id FROM sales s WHERE s.id = %s FOR UPDATE", (sale_id,))
    sale = cursor.fetchone()
    if not sale:
        raise BusinessError("Продажа не найдена.")
    cursor.execute("SELECT id FROM returns WHERE original_sale_id = %s", (sale_id,))
    if cursor.fetchone():
        raise BusinessError("По этой продаже уже оформлен возврат.")
    if return_price <= 0 or return_price > sale['sold_price']:
        raise BusinessError("Сумма возврата должна быть больше нуля и не больше суммы продажи.")
    _check_store(cursor, user_id, sale['store_id'], 'return')
    cursor.execute("""INSERT INTO returns (original_sale_id, product_article, return_price, payment_type, user_id, store_id, return_date)
                      VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                   (sale_id, product_article, return_price, payment_type, user_id, sale['store_id'] or store_id, now()))
    return_id = cursor.lastrowid
    _enqueue_fiscal(cursor, 'return', return_id, sale['store_id'] or store_id, return_price, user_id)
    _change_quantity(cursor, product_article, 1)
    if sale['mark_code_id']:
        # код возвращается в оборот вместе с изделием
        cursor.execute("UPDATE product_codes SET status = 'returned', updated_at = %s WHERE id = %s AND status = 'sold'",
                       (now(), sale['mark_code_id']))
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
                           old_item_description=None, old_metal_type=None, old_weight=None, new_mark_code=None):
    new_id = _change_quantity(cursor, new_product_article, -1)
    cursor.execute("SELECT id, article_number, store_id, is_marked FROM products WHERE id = %s", (new_id,))
    new_product = cursor.fetchone()
    store_id = new_product['store_id'] or store_id  # магазин нового товара
    _check_store(cursor, user_id, new_product['store_id'])
    code = None
    if new_mark_code:
        code = _take_code(cursor, new_mark_code, new_product, set())
    elif marking_required(new_product):
        raise BusinessError(f"{new_product['article_number']}: маркируемый товар — отсканируйте код маркировки DataMatrix.")
    if old_product_article:
        _change_quantity(cursor, old_product_article, 1)
    cursor.execute("""INSERT INTO trade_ins (old_product_article, old_item_description, old_metal_type, old_weight,
                                             old_item_value, new_product_article, new_sale_price, price_difference,
                                             payment_type_difference, user_id, store_id, trade_in_date, new_mark_code_id)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                   (old_product_article, old_item_description, old_metal_type, old_weight, old_item_value,
                    new_product_article, new_sale_price, price_difference, payment_type_difference,
                    user_id, store_id, now(), code['id'] if code else None))
    trade_in_id = cursor.lastrowid
    if code:
        cursor.execute("UPDATE product_codes SET status = 'sold', sale_id = NULL, note = %s, updated_at = %s WHERE id = %s",
                       (f"Trade-In №{trade_in_id}", now(), code['id']))
    return True, trade_in_id


@with_transaction
def create_cash_withdrawal_record(cursor, amount, comment, user_id, store_id):
    cursor.execute("INSERT INTO cash_withdrawals (amount, comment, user_id, store_id, withdrawal_date) VALUES (%s, %s, %s, %s, %s)",
                   (amount, comment, user_id, store_id, now()))
    withdrawal_id = cursor.lastrowid
    _enqueue_fiscal(cursor, 'withdrawal', withdrawal_id, store_id, amount, user_id)
    return True, withdrawal_id


@with_transaction
def create_cash_deposit_record(cursor, amount, comment, user_id, store_id):
    """Внесение наличных в кассу магазина (размен, возврат из банка). Фискализируется как внесение."""
    if not amount or amount <= 0:
        raise BusinessError("Сумма внесения должна быть больше нуля.")
    if not store_id:
        raise BusinessError("Выберите магазин.")
    cursor.execute("SELECT id FROM stores WHERE id = %s", (store_id,))
    if not cursor.fetchone():
        raise BusinessError("Магазин не найден.")
    cursor.execute("INSERT INTO cash_deposits (amount, comment, user_id, store_id, deposit_date) VALUES (%s, %s, %s, %s, %s)",
                   (amount, (comment or "").strip()[:500] or None, user_id, store_id, now()))
    deposit_id = cursor.lastrowid
    _enqueue_fiscal(cursor, 'deposit', deposit_id, store_id, amount, user_id)
    return True, deposit_id


@with_connection
def recent_cash_operations(cursor, limit=30, store_id=None):
    """Последние внесения и инкассации (для страницы «Внесение денег»)."""
    where, params = ("WHERE x.store_id = %s", [store_id]) if store_id else ("", [])
    cursor.execute(f"""
        SELECT * FROM (
          SELECT 'deposit' AS kind, x.id, x.deposit_date AS at, x.amount, x.comment, x.store_id, st.name AS store,
                 u.name AS user_name FROM cash_deposits x
          LEFT JOIN stores st ON st.id = x.store_id LEFT JOIN users u ON u.id = x.user_id {where}
          UNION ALL
          SELECT 'withdrawal', x.id, x.withdrawal_date, x.amount, x.comment, x.store_id, st.name, u.name FROM cash_withdrawals x
          LEFT JOIN stores st ON st.id = x.store_id LEFT JOIN users u ON u.id = x.user_id {where}
        ) ops ORDER BY at DESC, id DESC LIMIT %s""", (*params, *params, limit))
    return cursor.fetchall()


# ---------------------------------------------------------------- отчёты

@with_connection
def get_period_operations(cursor, start_dt, end_dt, store_id=None, include_imported=True, user_id=None):
    """Все операции за полуинтервал [start_dt, end_dt) для отчёта; store_id — фильтр по магазину.
    include_imported=False — без продаж и возвратов, загруженных из прежней программы (итоги смены, касса).
    user_id — только операции этого сотрудника (отчёт продавца «мои продажи»).

    Магазин операции: продажа — магазин товара на момент продажи, возврат — магазин продажи,
    trade-in — магазин нового товара, скупка и инкассация — магазин сотрудника (или выбранный).
    """
    result = {}

    def run(sql, alias):
        flt = f" AND {alias}.store_id = %s" if store_id else ""
        if not include_imported and alias in ("s", "r"):
            flt += f" AND {alias}.source IS NULL"
        if user_id:
            flt += f" AND {alias}.user_id = %s"
        cursor.execute(sql.replace("{STORE_FILTER}", flt),
                       (start_dt, end_dt) + ((store_id,) if store_id else ()) + ((user_id,) if user_id else ()))
        return cursor.fetchall()

    result['sales'] = run("""
        SELECT s.id, s.receipt_id, s.sale_date, s.sold_price, s.payment_type, s.store_id, st.name AS store,
               p.article_number, p.product_type, p.model, p.size, p.metal_type, p.weight,
               COALESCE(s.list_price, p.price) AS list_price, p.cost_price, u.name AS user_name, r.id AS return_id,
               mc.code AS mark_code
        FROM sales s
        JOIN products p ON s.product_id = p.id
        LEFT JOIN product_codes mc ON mc.id = s.mark_code_id
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
    result['deposits'] = run("""
        SELECT d.id, d.deposit_date, d.amount, d.comment, d.store_id, st.name AS store, u.name AS user_name
        FROM cash_deposits d
        LEFT JOIN stores st ON st.id = d.store_id
        LEFT JOIN users u ON d.user_id = u.id
        WHERE d.deposit_date >= %s AND d.deposit_date < %s{STORE_FILTER}
        ORDER BY d.deposit_date, d.id""", "d")
    _attach_payment_parts(cursor, result['sales'])
    return result


def _attach_payment_parts(cursor, sales):
    """Позициям чеков со смешанной оплатой — payment_parts [(способ, сумма)] и payment_label всего чека."""
    receipt_ids = sorted({s['receipt_id'] for s in sales if s['payment_type'] == MIXED_PAYMENT and s.get('receipt_id')})
    for i in range(0, len(receipt_ids), 500):
        chunk = receipt_ids[i:i + 500]
        marks = ",".join(["%s"] * len(chunk))
        cursor.execute(f"SELECT receipt_id, payment_type, amount FROM receipt_payments WHERE receipt_id IN ({marks}) "
                       "ORDER BY id", chunk)
        payments = {}
        for r in cursor.fetchall():
            payments.setdefault(r['receipt_id'], []).append((r['payment_type'], r['amount']))
        # все позиции чека (в отчёт по магазину или дню могла попасть только часть — раскладываем по полному чеку)
        cursor.execute(f"SELECT id, receipt_id, sold_price FROM sales WHERE receipt_id IN ({marks}) ORDER BY id", chunk)
        rows = {}
        for r in cursor.fetchall():
            rows.setdefault(r['receipt_id'], []).append(r)
        parts = {}
        for rid, items in rows.items():
            parts.update(_allocate(items, payments.get(rid, [])))
        for s in sales:
            if s.get('receipt_id') in payments and s['payment_type'] == MIXED_PAYMENT:
                s['payment_parts'] = parts.get(s['id'], [])
                s['payment_label'] = payments_label(payments[s['receipt_id']])


# ---------------------------------------------------------------- отметки фоновых задач

@with_connection
def get_job_run(cursor, name):
    cursor.execute("SELECT * FROM job_runs WHERE name = %s", (name,))
    return cursor.fetchone()


@with_transaction
def mark_job_run(cursor, name, for_date=None):
    """Отметить выполнение задачи. for_date — за какой день (для отчётов); None не затирает прежний."""
    cursor.execute("""INSERT INTO job_runs (name, last_run_at, last_for_date) VALUES (%s, %s, %s)
                      ON DUPLICATE KEY UPDATE last_run_at = VALUES(last_run_at),
                                              last_for_date = COALESCE(VALUES(last_for_date), last_for_date)""",
                   (name, now(), for_date))
    return True, None
