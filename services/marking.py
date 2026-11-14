"""Учёт кодов маркировки: приёмка, списание, сверка витрины.

Продажа, возврат и Trade-In по коду — в core/database.py (в тех же транзакциях, что и сами операции).
Передача кодов в ОФД/ИС МПТ будет вместе с фискализацией (#17); сейчас это внутренний поштучный учёт.
"""
import logging

from core.database import with_connection, with_transaction, BusinessError, SELLABLE_CODE, CODE_STATUS_TITLES
from utils import mark_codes
from utils.common import now

logger = logging.getLogger(__name__)

MAX_CODES_PER_BATCH = 2000


@with_transaction
def accept_codes(cursor, product_id, raw_codes, user_id, add_to_stock=True):
    """Приёмка кодов к товару. Товар становится маркируемым; при add_to_stock остаток растёт на число новых кодов.

    Возвращает (True, {'added', 'duplicates': [...], 'foreign': [...], 'invalid': [...]}).
    """
    raw_codes = [c for c in raw_codes if str(c).strip()]
    if not raw_codes:
        raise BusinessError("Отсканируйте или вставьте хотя бы один код.")
    if len(raw_codes) > MAX_CODES_PER_BATCH:
        raise BusinessError(f"За один раз — не больше {MAX_CODES_PER_BATCH} кодов.")
    cursor.execute("SELECT id, article_number, store_id FROM products WHERE id = %s FOR UPDATE", (product_id,))
    product = cursor.fetchone()
    if not product:
        raise BusinessError("Товар не найден.")
    added, duplicates, foreign, invalid, seen = 0, [], [], [], set()
    moment = now()
    for raw in raw_codes:
        if not mark_codes.is_valid(raw):
            invalid.append(mark_codes.short(raw))
            continue
        k = mark_codes.key(raw)
        if k in seen:
            continue
        seen.add(k)
        cursor.execute("SELECT c.product_id, p.article_number FROM product_codes c JOIN products p ON p.id = c.product_id "
                       "WHERE c.code_key = %s", (k,))
        existing = cursor.fetchone()
        if existing:
            (duplicates if existing['product_id'] == product_id else foreign).append(
                mark_codes.short(raw) + ("" if existing['product_id'] == product_id else f" → {existing['article_number']}"))
            continue
        cursor.execute("""INSERT INTO product_codes (code, code_key, product_id, store_id, status, created_at, created_by,
                                                     updated_at) VALUES (%s, %s, %s, %s, 'in_stock', %s, %s, %s)""",
                       (mark_codes.clean(raw), k, product_id, product['store_id'], moment, user_id, moment))
        added += 1
    cursor.execute("UPDATE products SET is_marked = 1" + (", quantity = quantity + %s" if add_to_stock else "") +
                   " WHERE id = %s", ((added, product_id) if add_to_stock else (product_id,)))
    logger.info("Маркировка: принято %s кодов к товару %s (%s)", added, product['article_number'], user_id)
    return True, {"added": added, "duplicates": duplicates, "foreign": foreign, "invalid": invalid}


@with_connection
def marked_products(cursor, store_id=None):
    """Маркируемые товары с числом кодов по статусам и расхождением с остатком."""
    flt, params = ("AND p.store_id = %s", (store_id,)) if store_id else ("", ())
    cursor.execute(f"""
        SELECT p.id, p.article_number, p.model, p.quantity, st.name AS store,
               SUM(c.status IN ('in_stock', 'returned')) AS in_stock,
               SUM(c.status = 'sold') AS sold, SUM(c.status = 'written_off') AS written_off
        FROM products p
        LEFT JOIN stores st ON st.id = p.store_id
        LEFT JOIN product_codes c ON c.product_id = p.id
        WHERE p.is_marked = 1 {flt}
        GROUP BY p.id, p.article_number, p.model, p.quantity, st.name
        ORDER BY st.name, p.article_number""", params)
    rows = cursor.fetchall()
    for r in rows:
        for k in ("in_stock", "sold", "written_off"):
            r[k] = int(r[k] or 0)
        r['without_code'] = max((r['quantity'] or 0) - r['in_stock'], 0)
        r['extra_codes'] = max(r['in_stock'] - (r['quantity'] or 0), 0)
    return rows


@with_connection
def product_codes(cursor, product_id, limit=500):
    cursor.execute("""SELECT c.*, s.receipt_id, u.name AS created_by_name FROM product_codes c
                      LEFT JOIN sales s ON s.id = c.sale_id LEFT JOIN users u ON u.id = c.created_by
                      WHERE c.product_id = %s
                      ORDER BY FIELD(c.status, 'in_stock', 'returned', 'sold', 'written_off'), c.id DESC LIMIT %s""",
                   (product_id, limit))
    rows = cursor.fetchall()
    for r in rows:
        r['short'] = mark_codes.short(r['code'])
        r['status_title'] = CODE_STATUS_TITLES[r['status']]
    return rows


@with_transaction
def write_off(cursor, code_id, user_id, reason, reduce_stock=True):
    """Списание кода (брак, утеря, ошибка приёмки). Остаток товара уменьшается на 1."""
    reason = (reason or "").strip()[:200]
    if len(reason) < 3:
        raise BusinessError("Укажите причину списания.")
    cursor.execute("SELECT c.*, p.quantity, p.article_number FROM product_codes c JOIN products p ON p.id = c.product_id "
                   "WHERE c.id = %s FOR UPDATE", (code_id,))
    code = cursor.fetchone()
    if not code:
        raise BusinessError("Код не найден.")
    if code['status'] not in SELLABLE_CODE:
        raise BusinessError(f"Код уже {CODE_STATUS_TITLES[code['status']]}.")
    cursor.execute("UPDATE product_codes SET status = 'written_off', note = %s, updated_at = %s WHERE id = %s",
                   (f"Списан: {reason}", now(), code_id))
    if reduce_stock and (code['quantity'] or 0) > 0:
        cursor.execute("UPDATE products SET quantity = quantity - 1 WHERE id = %s", (code['product_id'],))
    logger.info("Маркировка: списан код %s товара %s (%s): %s", code_id, code['article_number'], user_id, reason)
    return True, code['product_id']


@with_connection
def reconcile(cursor, raw_codes, store_id=None):
    """Сверка витрины: отсканированные коды против кодов «на складе».

    Возвращает {'ok': [...], 'missing': [...], 'unknown': [...], 'not_sellable': [...], 'other_store': [...]}.
    """
    keys, unknown = {}, []
    for raw in raw_codes:
        if mark_codes.is_valid(raw):
            keys[mark_codes.key(raw)] = raw
        elif str(raw).strip():
            unknown.append({"short": mark_codes.short(raw), "why": "не похоже на код маркировки"})
    found = {}
    key_list = list(keys)
    for i in range(0, len(key_list), 500):
        part = key_list[i:i + 500]
        cursor.execute(f"""SELECT c.*, p.article_number, p.model, p.store_id AS product_store, st.name AS store
                           FROM product_codes c JOIN products p ON p.id = c.product_id
                           LEFT JOIN stores st ON st.id = p.store_id
                           WHERE c.code_key IN ({",".join(["%s"] * len(part))})""", part)
        found.update({r['code_key']: r for r in cursor.fetchall()})
    result = {"ok": [], "missing": [], "unknown": unknown, "not_sellable": [], "other_store": []}
    for k, raw in keys.items():
        row = found.get(k)
        if not row:
            result['unknown'].append({"short": mark_codes.short(raw), "why": "не принят на склад"})
            continue
        item = {"short": mark_codes.short(row['code']), "article": row['article_number'], "model": row['model'],
                "store": row['store'], "status": CODE_STATUS_TITLES[row['status']], "id": row['id']}
        if row['status'] not in SELLABLE_CODE:
            result['not_sellable'].append(item)
        elif store_id and row['product_store'] != store_id:
            result['other_store'].append(item)
        else:
            result['ok'].append(item)
    flt, params = ("AND p.store_id = %s", (store_id,)) if store_id else ("", ())
    cursor.execute(f"""SELECT c.id, c.code, c.code_key, p.article_number, p.model, st.name AS store
                       FROM product_codes c JOIN products p ON p.id = c.product_id LEFT JOIN stores st ON st.id = p.store_id
                       WHERE c.status IN ('in_stock', 'returned') {flt} ORDER BY p.article_number""", params)
    for row in cursor.fetchall():
        if row['code_key'] not in keys:
            result['missing'].append({"short": mark_codes.short(row['code']), "article": row['article_number'],
                                      "model": row['model'], "store": row['store'], "id": row['id']})
    return result
