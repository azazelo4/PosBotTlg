"""Инвентаризация: в магазине сканируют все изделия на витрине и в сейфе (старые ценники, новые ценники, коды
маркировки или артикул вручную). Каждый скан сразу сохраняется — сеанс можно продолжить с другого устройства
или после перерыва. Итог: найденные изделия получают остаток 1 и этот магазин (так исправляются перевезённые),
ненайденные изделия магазина — остаток 0.

Изделие, проданное уже после того, как его отсканировали, при применении остаток не получает.
"""
import json
import logging

from core.database import (with_connection, with_transaction, BusinessError, get_product_by_article, find_by_mark_code,
                           own_store)
from utils import mark_codes
from utils.common import now

logger = logging.getLogger(__name__)

MAX_CODE = 128
LIST_LIMIT = 300


def can_use_store(user, store_id):
    """Менеджер, закреплённый за магазином, проводит инвентаризацию только своего магазина."""
    store = own_store(user)
    return store is None or store == store_id


@with_transaction
def start(cursor, store_id, user_id):
    """Открыть инвентаризацию магазина. Если уже идёт — вернуть её (сканируют вместе). (True, id)."""
    cursor.execute("SELECT id FROM stores WHERE id = %s", (store_id,))
    if not cursor.fetchone():
        raise BusinessError("Выберите магазин.")
    cursor.execute("SELECT id FROM inventories WHERE store_id = %s AND status = 'open' ORDER BY id LIMIT 1 FOR UPDATE",
                   (store_id,))
    row = cursor.fetchone()
    if row:
        return True, row['id']
    cursor.execute("INSERT INTO inventories (store_id, user_id, status, started_at) VALUES (%s, %s, 'open', %s)",
                   (store_id, user_id, now()))
    logger.info("Инвентаризация #%s магазина %s начата пользователем %s", cursor.lastrowid, store_id, user_id)
    return True, cursor.lastrowid


@with_connection
def get(cursor, inventory_id):
    cursor.execute("""SELECT i.*, st.name AS store, u.name AS user_name, f.name AS finished_name,
                             (SELECT COUNT(*) FROM inventory_scans s WHERE s.inventory_id = i.id
                                                                        AND s.product_id IS NOT NULL) AS found,
                             (SELECT COUNT(*) FROM inventory_scans s WHERE s.inventory_id = i.id
                                                                        AND s.product_id IS NULL) AS unknown
                      FROM inventories i LEFT JOIN stores st ON st.id = i.store_id
                      LEFT JOIN users u ON u.id = i.user_id LEFT JOIN users f ON f.id = i.finished_by
                      WHERE i.id = %s""", (inventory_id,))
    row = cursor.fetchone()
    if row and row.get('result'):
        row['result'] = json.loads(row['result'])
    return row


@with_connection
def recent(cursor, store_id=None, limit=20):
    cursor.execute("""SELECT i.id, i.status, i.started_at, i.finished_at, st.name AS store, u.name AS user_name,
                             (SELECT COUNT(*) FROM inventory_scans s WHERE s.inventory_id = i.id
                                                                        AND s.product_id IS NOT NULL) AS found
                      FROM inventories i LEFT JOIN stores st ON st.id = i.store_id LEFT JOIN users u ON u.id = i.user_id
                      WHERE %s IS NULL OR i.store_id = %s
                      ORDER BY i.status = 'open' DESC, i.id DESC LIMIT %s""", (store_id, store_id, limit))
    return cursor.fetchall()


def _resolve(code):
    """Код со скана → товар или None. Код маркировки, артикул, «#id» с ценника, штрихкод старого ценника."""
    if mark_codes.looks_like_code(code):
        found = find_by_mark_code(code)
        if found:
            return found['product']
    product = get_product_by_article(code)
    if not product and code.isdigit() and code.lstrip("0") and code.lstrip("0") != code:
        product = get_product_by_article(code.lstrip("0"))          # EAN, дополненный нулями слева
    return product


def _item(product):
    details = " · ".join(str(x) for x in (
        f"{product['weight']:g} г" if product.get('weight') else None,
        f"р. {product['size']}" if product.get('size') else None) if x)
    return {"article": product['article_number'], "model": product.get('model') or product.get('product_type') or '',
            "details": details, "store": product.get('Store'), "store_id": product.get('store_id')}


@with_transaction
def scan(cursor, inventory_id, raw_code, user_id):
    """Скан одного кода. (True, {'status': found|again|unknown, 'other_store': bool, 'item': {...}, 'found', 'unknown'})."""
    code = "".join(str(raw_code or "").split())[:MAX_CODE]
    if not code:
        raise BusinessError("Пустой код.")
    cursor.execute("SELECT id, store_id, status FROM inventories WHERE id = %s FOR UPDATE", (inventory_id,))
    inv = cursor.fetchone()
    if not inv or inv['status'] != 'open':
        raise BusinessError("Инвентаризация уже завершена — начните новую.")
    product = _resolve(code)
    moment = now()
    scan_id = None
    if product:
        cursor.execute("SELECT id FROM inventory_scans WHERE inventory_id = %s AND product_id = %s",
                       (inventory_id, product['id']))
        status = 'again' if cursor.fetchone() else 'found'
        if status == 'found':
            cursor.execute("""INSERT INTO inventory_scans (inventory_id, product_id, code, user_id, scanned_at)
                              VALUES (%s, %s, %s, %s, %s)""", (inventory_id, product['id'], code, user_id, moment))
            scan_id = cursor.lastrowid
        item = _item(product)
        other = bool(product.get('store_id')) and product['store_id'] != inv['store_id']
    else:
        status, other = 'unknown', False
        item = {"code": mark_codes.short(code) if mark_codes.looks_like_code(code) else code}
        cursor.execute("""INSERT INTO inventory_scans (inventory_id, product_id, code, user_id, scanned_at)
                          VALUES (%s, NULL, %s, %s, %s)""", (inventory_id, code, user_id, moment))
        scan_id = cursor.lastrowid
    cursor.execute("SELECT SUM(product_id IS NOT NULL) AS found, SUM(product_id IS NULL) AS unknown "
                   "FROM inventory_scans WHERE inventory_id = %s", (inventory_id,))
    counts = cursor.fetchone()
    return True, {"status": status, "other_store": other, "item": item, "scan_id": scan_id,
                  "found": int(counts['found'] or 0), "unknown": int(counts['unknown'] or 0)}


@with_transaction
def unscan(cursor, inventory_id, scan_id):
    """Убрать ошибочный скан (не то изделие, чужая витрина)."""
    cursor.execute("SELECT status FROM inventories WHERE id = %s FOR UPDATE", (inventory_id,))
    inv = cursor.fetchone()
    if not inv or inv['status'] != 'open':
        raise BusinessError("Инвентаризация уже завершена.")
    cursor.execute("DELETE FROM inventory_scans WHERE id = %s AND inventory_id = %s", (scan_id, inventory_id))
    removed = cursor.rowcount
    cursor.execute("SELECT SUM(product_id IS NOT NULL) AS found, SUM(product_id IS NULL) AS unknown "
                   "FROM inventory_scans WHERE inventory_id = %s", (inventory_id,))
    counts = cursor.fetchone()
    return True, {"removed": removed, "found": int(counts['found'] or 0), "unknown": int(counts['unknown'] or 0)}


@with_connection
def scans(cursor, inventory_id, limit=30):
    cursor.execute("""SELECT s.id, s.code, s.scanned_at, s.product_id, p.article_number, p.model, p.weight, p.size,
                             p.store_id, st.name AS store, u.name AS user_name
                      FROM inventory_scans s LEFT JOIN products p ON p.id = s.product_id
                      LEFT JOIN stores st ON st.id = p.store_id LEFT JOIN users u ON u.id = s.user_id
                      WHERE s.inventory_id = %s ORDER BY s.id DESC LIMIT %s""", (inventory_id, limit))
    return cursor.fetchall()


@with_connection
def summary(cursor, inventory_id):
    """Что изменится при применении: найдено, перенос из других магазинов, неизвестные коды, не найдено, продано
    после скана."""
    cursor.execute("SELECT * FROM inventories WHERE id = %s", (inventory_id,))
    inv = cursor.fetchone()
    if not inv:
        return None
    store = inv['store_id']
    cursor.execute("""SELECT p.id, p.article_number, p.model, p.weight, p.size, p.quantity, p.store_id, st.name AS store,
                             (SELECT COUNT(*) FROM sales sa LEFT JOIN returns r ON r.original_sale_id = sa.id
                              WHERE sa.product_id = p.id AND sa.sale_date >= s.scanned_at AND r.id IS NULL) AS sold_after
                      FROM inventory_scans s JOIN products p ON p.id = s.product_id
                      LEFT JOIN stores st ON st.id = p.store_id
                      WHERE s.inventory_id = %s ORDER BY p.article_number""", (inventory_id,))
    found = cursor.fetchall()
    cursor.execute("""SELECT code, COUNT(*) AS n, MAX(scanned_at) AS last FROM inventory_scans
                      WHERE inventory_id = %s AND product_id IS NULL GROUP BY code ORDER BY last DESC""", (inventory_id,))
    unknown = cursor.fetchall()
    cursor.execute("""SELECT p.id, p.article_number, p.model, p.weight, p.size, p.quantity FROM products p
                      WHERE p.store_id = %s AND p.quantity > 0
                        AND NOT EXISTS (SELECT 1 FROM inventory_scans s WHERE s.inventory_id = %s AND s.product_id = p.id)
                      ORDER BY p.article_number""", (store, inventory_id))
    missing = cursor.fetchall()
    return {
        "found": len(found),
        "moved": [r for r in found if r['store_id'] != store],
        "restocked": sum(1 for r in found if (r['quantity'] or 0) < 1 and not r['sold_after']),
        "sold_after": [r for r in found if r['sold_after']],
        "unknown": unknown,
        "missing": missing,
    }


@with_transaction
def apply(cursor, inventory_id, user_id, zero_missing=True):
    """Применить итог. Найденные: магазин инвентаризации, остаток 1 (0 — если после скана продано). Ненайденные
    товары магазина с остатком: 0 (если zero_missing). (True, итог)."""
    cursor.execute("SELECT * FROM inventories WHERE id = %s FOR UPDATE", (inventory_id,))
    inv = cursor.fetchone()
    if not inv or inv['status'] != 'open':
        raise BusinessError("Инвентаризация уже завершена.")
    store = inv['store_id']
    cursor.execute("SELECT COUNT(*) AS n FROM inventory_scans WHERE inventory_id = %s AND product_id IS NOT NULL",
                   (inventory_id,))
    found = cursor.fetchone()['n']
    if not found:
        raise BusinessError("Ничего не отсканировано — применять нечего.")
    cursor.execute("""SELECT COUNT(*) AS n FROM inventory_scans s JOIN products p ON p.id = s.product_id
                      WHERE s.inventory_id = %s AND (p.store_id IS NULL OR p.store_id != %s)""", (inventory_id, store))
    moved = cursor.fetchone()['n']
    cursor.execute("""UPDATE products p JOIN inventory_scans s ON s.product_id = p.id AND s.inventory_id = %s
                      SET p.store_id = %s,
                          p.quantity = GREATEST(0, 1 - (SELECT COUNT(*) FROM sales sa
                                                        LEFT JOIN returns r ON r.original_sale_id = sa.id
                                                        WHERE sa.product_id = p.id AND sa.sale_date >= s.scanned_at
                                                          AND r.id IS NULL))""", (inventory_id, store))
    zeroed = 0
    if zero_missing:
        cursor.execute("""UPDATE products p SET p.quantity = 0
                          WHERE p.store_id = %s AND p.quantity > 0
                            AND NOT EXISTS (SELECT 1 FROM inventory_scans s WHERE s.inventory_id = %s
                                                                             AND s.product_id = p.id)""",
                       (store, inventory_id))
        zeroed = cursor.rowcount
    cursor.execute("SELECT COUNT(DISTINCT code) AS n FROM inventory_scans WHERE inventory_id = %s AND product_id IS NULL",
                   (inventory_id,))
    unknown = cursor.fetchone()['n']
    result = {"found": found, "moved": moved, "zeroed": zeroed, "unknown": unknown}
    cursor.execute("UPDATE inventories SET status = 'applied', finished_at = %s, finished_by = %s, result = %s WHERE id = %s",
                   (now(), user_id, json.dumps(result), inventory_id))
    logger.info("Инвентаризация #%s применена пользователем %s: %s", inventory_id, user_id, result)
    return True, result


@with_transaction
def cancel(cursor, inventory_id, user_id):
    cursor.execute("UPDATE inventories SET status = 'cancelled', finished_at = %s, finished_by = %s "
                   "WHERE id = %s AND status = 'open'", (now(), user_id, inventory_id))
    if not cursor.rowcount:
        raise BusinessError("Инвентаризация уже завершена.")
    return True, None
