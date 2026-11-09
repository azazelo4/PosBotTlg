"""База клиентов и доставка электронного чека в Telegram.

Персональные данные (Закон РК «О персональных данных и их защите»): номер сохраняется только с согласия —
продавец отмечает согласие на кассе, либо клиент сам делится номером в боте (/start r_<токен>).
Удаление клиента — по его просьбе: запись удаляется, чеки остаются без привязки к человеку.
"""
import logging
from datetime import timedelta

from database import with_connection, with_transaction, BusinessError, _upsert_customer
from utils.common import now, normalize_phone

logger = logging.getLogger(__name__)

SEND_WINDOW = timedelta(days=1)          # досылаем чеки не старше суток
FISCAL_WAIT = timedelta(minutes=2)       # ждём фискальный чек, чтобы в сообщении была ссылка ОФД


@with_transaction
def subscribe(cursor, phone, chat_id, name=None, receipt_id=None):
    """Клиент поделился номером в боте: согласие получено, чеки будут приходить в этот чат.
    Привязывает чек, по которому он пришёл (если чек ещё ни к кому не привязан). Возвращает id клиента."""
    if not normalize_phone(phone):
        raise BusinessError("Не удалось распознать номер телефона.")
    customer_id = _upsert_customer(cursor, phone, name, 'telegram')
    moment = now()
    # один чат — один клиент: если этот Telegram раньше был привязан к другому номеру, отвязываем
    cursor.execute("UPDATE customers SET chat_id = NULL, updated_at = %s WHERE chat_id = %s AND id != %s",
                   (moment, chat_id, customer_id))
    cursor.execute("""UPDATE customers SET chat_id = %s, consent_at = %s, consent_source = 'telegram', updated_at = %s
                      WHERE id = %s""", (chat_id, moment, moment, customer_id))
    if receipt_id:
        cursor.execute("UPDATE receipts SET customer_id = %s WHERE id = %s AND customer_id IS NULL",
                       (customer_id, receipt_id))
    return True, customer_id


@with_transaction
def unsubscribe(cursor, chat_id):
    cursor.execute("UPDATE customers SET chat_id = NULL, updated_at = %s WHERE chat_id = %s", (now(), chat_id))
    return True, cursor.rowcount


@with_connection
def by_chat(cursor, chat_id):
    cursor.execute("SELECT * FROM customers WHERE chat_id = %s", (chat_id,))
    return cursor.fetchone()


@with_transaction
def attach_receipt(cursor, receipt_id, customer_id):
    cursor.execute("UPDATE receipts SET customer_id = %s WHERE id = %s AND customer_id IS NULL", (customer_id, receipt_id))
    return True, cursor.rowcount


# ------------------------------------------------------------------ доставка в Telegram

@with_connection
def pending_deliveries(cursor, limit=20):
    """Чеки клиентов, подписанных в Telegram, ещё не отправленные. Если чек фискализируется — ждём ссылку ОФД,
    но не дольше FISCAL_WAIT."""
    moment = now()
    cursor.execute("""SELECT r.id, r.public_token, c.chat_id FROM receipts r
                      JOIN customers c ON c.id = r.customer_id AND c.chat_id IS NOT NULL
                      LEFT JOIN fiscal_receipts f ON f.kind = 'sale' AND f.ref_id = r.id
                      WHERE r.sent_at IS NULL AND r.created_at >= %s
                        AND (f.id IS NULL OR f.status != 'pending' OR r.created_at <= %s)
                      ORDER BY r.id LIMIT %s""", (moment - SEND_WINDOW, moment - FISCAL_WAIT, limit))
    return cursor.fetchall()


@with_transaction
def mark_sent(cursor, receipt_id):
    cursor.execute("UPDATE receipts SET sent_at = %s WHERE id = %s", (now(), receipt_id))
    return True, None


# ------------------------------------------------------------------ админка

@with_connection
def search(cursor, query=None, limit=200):
    params, where = [], ""
    query = (query or "").strip()
    if query:
        digits = "".join(ch for ch in query if ch.isdigit())
        where = "WHERE c.name LIKE %s" + (" OR c.phone LIKE %s" if digits else "")
        params = [f"%{query}%"] + ([f"%{digits[-10:]}%"] if digits else [])
    cursor.execute(f"""SELECT c.*, COUNT(r.id) AS receipts, COALESCE(SUM(r.total), 0) AS spent, MAX(r.created_at) AS last_at
                       FROM customers c LEFT JOIN receipts r ON r.customer_id = c.id
                       {where}
                       GROUP BY c.id ORDER BY last_at IS NULL, last_at DESC, c.id DESC LIMIT %s""", (*params, limit))
    return cursor.fetchall()


@with_connection
def get(cursor, customer_id):
    cursor.execute("SELECT * FROM customers WHERE id = %s", (customer_id,))
    customer = cursor.fetchone()
    if customer:
        cursor.execute("""SELECT r.*, st.name AS store, u.name AS user_name,
                                 (SELECT GROUP_CONCAT(COALESCE(p.model, p.article_number) ORDER BY s.id SEPARATOR ', ')
                                  FROM sales s JOIN products p ON p.id = s.product_id WHERE s.receipt_id = r.id) AS items,
                                 (SELECT COUNT(*) FROM sales s JOIN returns rt ON rt.original_sale_id = s.id
                                  WHERE s.receipt_id = r.id) AS returned
                          FROM receipts r LEFT JOIN stores st ON st.id = r.store_id LEFT JOIN users u ON u.id = r.user_id
                          WHERE r.customer_id = %s ORDER BY r.id DESC""", (customer_id,))
        customer['history'] = cursor.fetchall()
        customer['spent'] = sum(float(r['total'] or 0) for r in customer['history'])
    return customer


@with_transaction
def update(cursor, customer_id, name, birthday, note):
    cursor.execute("UPDATE customers SET name = %s, birthday = %s, note = %s, updated_at = %s WHERE id = %s",
                   ((name or "").strip()[:100] or None, birthday, (note or "").strip()[:255] or None, now(), customer_id))
    if cursor.rowcount == 0:
        cursor.execute("SELECT id FROM customers WHERE id = %s", (customer_id,))
        if not cursor.fetchone():
            raise BusinessError("Клиент не найден.")
    return True, customer_id


@with_transaction
def delete(cursor, customer_id, user_id):
    """Удалить клиента по его просьбе: чеки и продажи остаются, привязка к человеку снимается."""
    cursor.execute("SELECT phone FROM customers WHERE id = %s FOR UPDATE", (customer_id,))
    row = cursor.fetchone()
    if not row:
        raise BusinessError("Клиент не найден.")
    cursor.execute("UPDATE receipts SET customer_id = NULL WHERE customer_id = %s", (customer_id,))
    cursor.execute("DELETE FROM customers WHERE id = %s", (customer_id,))
    logger.info("Клиент %s удалён пользователем %s", customer_id, user_id)
    return True, None
