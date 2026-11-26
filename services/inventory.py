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
    return {"id": product['id'], "article": product['article_number'],
            "model": product.get('model') or product.get('product_type') or '',
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
        cursor.execute("SELECT COUNT(*) AS n FROM product_photos WHERE product_id = %s", (product['id'],))
        item['photos'] = cursor.fetchone()['n']
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
def unscan(cursor, inventory_id, scan_id, user_id=None):
    """Убрать ошибочный скан (не то изделие, чужая витрина). С user_id — только свой скан (продавец)."""
    cursor.execute("SELECT status FROM inventories WHERE id = %s FOR UPDATE", (inventory_id,))
    inv = cursor.fetchone()
    if not inv or inv['status'] != 'open':
        raise BusinessError("Инвентаризация уже завершена.")
    cursor.execute("DELETE FROM inventory_scans WHERE id = %s AND inventory_id = %s AND (%s IS NULL OR user_id = %s)",
                   (scan_id, inventory_id, user_id, user_id))
    removed = cursor.rowcount
    cursor.execute("SELECT SUM(product_id IS NOT NULL) AS found, SUM(product_id IS NULL) AS unknown "
                   "FROM inventory_scans WHERE inventory_id = %s", (inventory_id,))
    counts = cursor.fetchone()
    return True, {"removed": removed, "found": int(counts['found'] or 0), "unknown": int(counts['unknown'] or 0)}


@with_connection
def photo_allowed(cursor, inventory_id, product_id):
    """Фото во время инвентаризации — только к изделию, отсканированному в этой (открытой) инвентаризации."""
    cursor.execute("""SELECT 1 FROM inventory_scans s JOIN inventories i ON i.id = s.inventory_id
                      WHERE s.inventory_id = %s AND s.product_id = %s AND i.status = 'open' LIMIT 1""",
                   (inventory_id, product_id))
    return cursor.fetchone() is not None


@with_connection
def open_list(cursor, store_id=None):
    """Открытые инвентаризации (одного магазина или всех) — к ним присоединяются продавцы."""
    cursor.execute("""SELECT i.id, i.store_id, st.name AS store FROM inventories i LEFT JOIN stores st ON st.id = i.store_id
                      WHERE i.status = 'open' AND (%s IS NULL OR i.store_id = %s) ORDER BY i.id""", (store_id, store_id))
    return cursor.fetchall()


def joinable(user, store_id=None):
    """Открытые инвентаризации, в которые сотрудник может сканировать: своего магазина (закреплён) или выбранного
    на кассе; незакреплённый без выбора — все."""
    own = own_store(user)
    rows = open_list(own or store_id) or []
    return [r for r in rows if not own or r['store_id'] == own]


@with_connection
def pending_invites(cursor):
    """Начатые инвентаризации, о которых ещё не сообщили продавцам магазина: [{id, store, staff: [chat_id…]}]."""
    cursor.execute("""SELECT i.id, i.store_id, i.user_id, st.name AS store FROM inventories i
                      LEFT JOIN stores st ON st.id = i.store_id WHERE i.status = 'open' AND i.notified = 0""")
    rows = cursor.fetchall()
    for r in rows:
        cursor.execute("""SELECT chat_id FROM users WHERE is_active = 1 AND role IN ('cashier', 'manager') AND chat_id > 0
                          AND (store_id = %s OR store_id IS NULL) AND id != %s""", (r['store_id'], r['user_id']))
        r['staff'] = [u['chat_id'] for u in cursor.fetchall()]
    return rows


@with_transaction
def mark_notified(cursor, inventory_id):
    cursor.execute("UPDATE inventories SET notified = 1 WHERE id = %s", (inventory_id,))
    return True, None


@with_connection
def scans(cursor, inventory_id, limit=30, user_id=None):
    cursor.execute("""SELECT s.id, s.code, s.scanned_at, s.product_id, p.article_number, p.model, p.weight, p.size,
                             p.store_id, st.name AS store, u.name AS user_name,
                             (SELECT COUNT(*) FROM product_photos ph WHERE ph.product_id = s.product_id) AS photos
                      FROM inventory_scans s LEFT JOIN products p ON p.id = s.product_id
                      LEFT JOIN stores st ON st.id = p.store_id LEFT JOIN users u ON u.id = s.user_id
                      WHERE s.inventory_id = %s AND (%s IS NULL OR s.user_id = %s)
                      ORDER BY s.id DESC LIMIT %s""", (inventory_id, user_id, user_id, limit))
    return cursor.fetchall()


MARKS = {
    'checked': "проверено",
    'added': "выявлено при инвентаризации",
    'moved': "перемещено при инвентаризации",
    'missing': "не найдено — остаток 0, требует уточнения",
    'resolved': "уточнено после инвентаризации",
}


def suggest_new(code):
    """Неизвестный код → поля нового товара: длинный цифровой код — штрихкод старого ценника, иначе артикул."""
    if code.isdigit() and len(code) >= 8:
        return {"barcode": code}
    return {"article_number": code}


def _classify(row, store):
    """Найденное изделие → (метка, примечание, новый остаток)."""
    qty, sold = row['quantity'] or 0, int(row['sold_after'] or 0)
    if row['store_id'] != store:
        mark, note = 'moved', f"из «{row['store'] or 'без магазина'}»" + (f", было {qty} шт." if qty != 1 else "")
    elif qty < 1:
        mark, note = 'added', "по базе было 0"
    else:
        mark, note = 'checked', (f"по базе было {qty} шт." if qty != 1 else "")
    if sold:
        note = "; ".join(x for x in (note, "продано после скана") if x)
    return mark, note, max(0, 1 - sold)


_FOUND_SQL = """SELECT p.id, p.article_number, p.model, p.weight, p.size, p.quantity, p.store_id, st.name AS store,
                       (SELECT COUNT(*) FROM sales sa LEFT JOIN returns r ON r.original_sale_id = sa.id
                        WHERE sa.product_id = p.id AND sa.sale_date >= s.scanned_at AND r.id IS NULL) AS sold_after
                FROM inventory_scans s JOIN products p ON p.id = s.product_id
                LEFT JOIN stores st ON st.id = p.store_id
                WHERE s.inventory_id = %s ORDER BY p.article_number"""
_MISSING_SQL = """SELECT p.id, p.article_number, p.model, p.weight, p.size, p.quantity FROM products p
                  WHERE p.store_id = %s AND p.quantity > 0
                    AND NOT EXISTS (SELECT 1 FROM inventory_scans s WHERE s.inventory_id = %s AND s.product_id = p.id)
                  ORDER BY p.article_number"""


@with_connection
def summary(cursor, inventory_id):
    """Что изменится при применении — по группам: снижено до 0 (не найдено), добавлено (по базе было 0), перенесено
    из других магазинов, проверено без изменений, нет в базе (предложить добавить), продано после скана."""
    cursor.execute("SELECT * FROM inventories WHERE id = %s", (inventory_id,))
    inv = cursor.fetchone()
    if not inv:
        return None
    store = inv['store_id']
    cursor.execute(_FOUND_SQL, (inventory_id,))
    found = cursor.fetchall()
    groups = {'checked': [], 'added': [], 'moved': [], 'reduced': []}
    for r in found:
        r['mark'], r['note'], r['new_qty'] = _classify(r, store)
        # «проверено», но остаток меняется (по базе было 3 → станет 1, продано после скана → 0) — показать отдельно
        key = 'reduced' if r['mark'] == 'checked' and r['new_qty'] != (r['quantity'] or 0) else r['mark']
        groups[key].append(r)
    cursor.execute("""SELECT code, COUNT(*) AS n, MAX(scanned_at) AS last FROM inventory_scans
                      WHERE inventory_id = %s AND product_id IS NULL GROUP BY code ORDER BY last DESC""", (inventory_id,))
    unknown = cursor.fetchall()
    for u in unknown:
        u['suggest'] = suggest_new(u['code'])
    cursor.execute(_MISSING_SQL, (store, inventory_id))
    missing = cursor.fetchall()
    return {
        "found": len(found),
        "checked": groups['checked'],
        "reduced": groups['reduced'],
        "added": groups['added'],
        "moved": groups['moved'],
        "sold_after": [r for r in found if r['sold_after']],
        "unknown": unknown,
        "missing": missing,
    }


@with_transaction
def apply(cursor, inventory_id, user_id):
    """Применить итог. Найденные: магазин инвентаризации, остаток 1 (0 — если после скана продано) и метка
    «проверено» / «выявлено» / «перемещено». Ненайденные товары магазина с остатком: остаток 0 и метка «не найдено —
    требует уточнения». (True, итог)."""
    cursor.execute("SELECT * FROM inventories WHERE id = %s FOR UPDATE", (inventory_id,))
    inv = cursor.fetchone()
    if not inv or inv['status'] != 'open':
        raise BusinessError("Инвентаризация уже завершена.")
    store, moment = inv['store_id'], now()
    cursor.execute(_FOUND_SQL + " FOR UPDATE", (inventory_id,))
    found = cursor.fetchall()
    if not found:
        raise BusinessError("Ничего не отсканировано — применять нечего.")
    counts = {'checked': 0, 'added': 0, 'moved': 0}
    updates = []
    for r in found:
        mark, note, qty = _classify(r, store)
        counts[mark] += 1
        updates.append((store, qty, mark, moment, inventory_id, note[:255] or None, r['id']))
    cursor.executemany("""UPDATE products SET store_id = %s, quantity = %s, inv_mark = %s, inv_mark_at = %s,
                                              inv_mark_id = %s, inv_mark_note = %s WHERE id = %s""", updates)
    # примечание — до обнуления: в UPDATE MySQL присваивает слева направо
    cursor.execute("""UPDATE products p SET p.inv_mark_note = CONCAT('по базе было ', p.quantity, ' шт.'),
                                            p.quantity = 0, p.inv_mark = 'missing', p.inv_mark_at = %s, p.inv_mark_id = %s
                      WHERE p.store_id = %s AND p.quantity > 0
                        AND NOT EXISTS (SELECT 1 FROM inventory_scans s WHERE s.inventory_id = %s AND s.product_id = p.id)""",
                   (moment, inventory_id, store, inventory_id))
    zeroed = cursor.rowcount
    cursor.execute("SELECT COUNT(DISTINCT code) AS n FROM inventory_scans WHERE inventory_id = %s AND product_id IS NULL",
                   (inventory_id,))
    unknown = cursor.fetchone()['n']
    result = {"found": len(found), **counts, "zeroed": zeroed, "unknown": unknown}
    cursor.execute("UPDATE inventories SET status = 'applied', finished_at = %s, finished_by = %s, result = %s WHERE id = %s",
                   (moment, user_id, json.dumps(result), inventory_id))
    logger.info("Инвентаризация #%s применена пользователем %s: %s", inventory_id, user_id, result)
    return True, result


@with_transaction
def resolve(cursor, product_id, user_name):
    """Менеджер разобрался с ненайденным изделием (нашлось, продано без пробития, списано) — снять «требует уточнения».
    Остаток при этом не меняется: если нашлось, его исправляют в карточке."""
    cursor.execute("""UPDATE products SET inv_mark = 'resolved', inv_mark_at = %s,
                             inv_mark_note = LEFT(CONCAT(COALESCE(CONCAT(inv_mark_note, '; '), ''), 'уточнил(а) ', %s), 255)
                      WHERE id = %s AND inv_mark = 'missing'""", (now(), user_name or "менеджер", product_id))
    if not cursor.rowcount:
        raise BusinessError("Товар не ждёт уточнения.")
    return True, "Отмечено: уточнено."


@with_transaction
def resume(cursor, inventory_id, user_id):
    """Вернуть в работу отменённую инвентаризацию со всеми сканами. Если в магазине уже начали новую — её сканы
    переносятся сюда (повторы не дублируются), а новая закрывается. (True, перенесено сканов)."""
    cursor.execute("SELECT id, store_id, status FROM inventories WHERE id = %s FOR UPDATE", (inventory_id,))
    inv = cursor.fetchone()
    if not inv or inv['status'] != 'cancelled':
        raise BusinessError("Возобновить можно только отменённую инвентаризацию.")
    cursor.execute("SELECT id FROM inventories WHERE store_id = %s AND status = 'open' AND id != %s FOR UPDATE",
                   (inv['store_id'], inventory_id))
    moved = 0
    for other in cursor.fetchall():
        cursor.execute("UPDATE IGNORE inventory_scans SET inventory_id = %s WHERE inventory_id = %s",
                       (inventory_id, other['id']))
        moved += cursor.rowcount
        cursor.execute("DELETE FROM inventory_scans WHERE inventory_id = %s", (other['id'],))
        cursor.execute("UPDATE inventories SET status = 'cancelled', finished_at = %s, finished_by = %s WHERE id = %s",
                       (now(), user_id, other['id']))
    cursor.execute("""UPDATE inventories SET status = 'open', finished_at = NULL, finished_by = NULL, notified = 1
                      WHERE id = %s""", (inventory_id,))
    logger.info("Инвентаризация #%s возобновлена пользователем %s (перенесено сканов: %s)", inventory_id, user_id, moved)
    return True, moved


@with_transaction
def cancel(cursor, inventory_id, user_id):
    cursor.execute("UPDATE inventories SET status = 'cancelled', finished_at = %s, finished_by = %s "
                   "WHERE id = %s AND status = 'open'", (now(), user_id, inventory_id))
    if not cursor.rowcount:
        raise BusinessError("Инвентаризация уже завершена.")
    return True, None
