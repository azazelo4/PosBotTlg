"""Фискализация через Webkassa: очередь чеков, отправка, смены (X/Z), повтор.

Операция (продажа, возврат, изъятие) сохраняется сразу и в той же транзакции ставится в очередь
(database._enqueue_fiscal). Очередь обрабатывает бот (process_queue каждые несколько секунд):
- нет связи или сервер Webkassa недоступен — повтор с нарастающей паузой, запись остаётся «ожидает»;
- Webkassa отклонил чек — «ошибка», текст ошибки виден в админке, там же «Повторить»;
- запросы по одной кассе — строго последовательно, в том числе между ботом и веб-админкой
  (именованная блокировка MySQL на кассу).

Скупка и Trade-In не фискализируются, пока не решено, как их оформлять (см. #17).
"""
import json
import logging
from contextlib import contextmanager
from datetime import timedelta

import config
from database import with_connection, with_transaction, get_connection, BusinessError, get_job_run, mark_job_run
from utils.common import now, money
from utils.webkassa import (WebkassaClient, WebkassaError, WebkassaUnavailable, OPERATION_SALE, OPERATION_SALE_RETURN,
                            CASHBOX_BLOCKING_CODES, NO_SHIFT_CODE, NOT_ENOUGH_CASH_CODE,
                            PAYMENT_CASH, PAYMENT_CARD, PAYMENT_MOBILE, ROUND_KOPECKS, MONEY_WITHDRAWAL, position,
                            return_basis, find_key)

logger = logging.getLogger(__name__)

PAYMENT_TITLES = {PAYMENT_CASH: "наличные", PAYMENT_CARD: "карта", PAYMENT_MOBILE: "мобильный платёж"}
KIND_TITLES = {'sale': "Продажа", 'return': "Возврат", 'withdrawal': "Изъятие (инкассация)", 'z_report': "Закрытие смены"}
STATUS_TITLES = {'pending': "ожидает", 'done': "пробит", 'error': "ошибка", 'manual': "оформлен вручную"}
BATCH = 20
MAX_DELAY = timedelta(minutes=30)
BLOCKED_RETRY = timedelta(minutes=10)     # касса без лицензии / неверный логин: проверяем раз в 10 минут
ALERT_EVERY = timedelta(hours=1)          # и напоминаем об этом не чаще раза в час
LOCK_SECONDS = 60

_client = None


def client():
    """Клиент с текущими настройками; токен переиспользуется, пока настройки не меняются."""
    global _client
    params = (config.WEBKASSA_URL, config.WEBKASSA_LOGIN, config.WEBKASSA_PASSWORD, config.WEBKASSA_API_KEY)
    if _client is None or (_client.base_url, _client.login, _client.password, _client.api_key) != (
            (params[0] or "").rstrip("/"), *params[1:]):
        _client = WebkassaClient(*params)
    return _client


def enabled():
    return bool(config.FISCAL_ENABLED)


@contextmanager
def cashbox_lock(cashbox):
    """Один запрос к кассе в один момент — и в боте, и в веб-админке (требование Webkassa)."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT GET_LOCK(%s, %s)", (f"webkassa:{cashbox}", LOCK_SECONDS))
        got = cur.fetchone()[0]
        if got != 1:
            raise WebkassaUnavailable("касса занята другим запросом, повторю позже")
        yield
    finally:
        try:
            cur.execute("SELECT RELEASE_LOCK(%s)", (f"webkassa:{cashbox}",))
            cur.fetchall()
        finally:
            cur.close()
            conn.close()


def payment_code(payment_type):
    if payment_type == "Наличные":
        return PAYMENT_CASH
    if payment_type == "Kaspi" and config.FISCAL_KASPI_MOBILE:
        return PAYMENT_MOBILE
    return PAYMENT_CARD


def _money(value):
    return round(float(value or 0), 2)


def _position(name, code, price, mark=None):
    return position(name, code, price, mark, vat=float(config.FISCAL_VAT_PERCENT or 0))


def _item_name(row):
    parts = [row.get('product_type'), row.get('model'), row.get('metal_type'), row.get('fineness')]
    name = " ".join(str(p) for p in parts if p) or "Ювелирное изделие"
    return f"{name}, арт. {row['article_number']}"


# ------------------------------------------------------------------ данные операций

@with_connection
def _sale_body(cursor, rec):
    cursor.execute("SELECT * FROM receipts WHERE id = %s", (rec['ref_id'],))
    receipt = cursor.fetchone()
    if not receipt:
        raise WebkassaError(0, f"чек №{rec['ref_id']} не найден в базе")
    cursor.execute("""SELECT s.sold_price, p.article_number, p.model, p.product_type, p.metal_type, p.fineness,
                             mc.code AS mark
                      FROM sales s JOIN products p ON p.id = s.product_id
                      LEFT JOIN product_codes mc ON mc.id = s.mark_code_id
                      WHERE s.receipt_id = %s ORDER BY s.id""", (rec['ref_id'],))
    rows = cursor.fetchall()
    positions = [_position(_item_name(r), r['article_number'], r['sold_price'], r['mark']) for r in rows]
    total = round(sum(p["Price"] for p in positions), 2)
    return {"CashboxUniqueNumber": rec['cashbox'], "OperationType": OPERATION_SALE, "Positions": positions,
            "Payments": [{"Sum": total, "PaymentType": payment_code(receipt['payment_type'])}], "Change": 0,
            "RoundType": ROUND_KOPECKS, "ExternalCheckNumber": rec['external_number']}


@with_connection
def _return_body(cursor, rec):
    cursor.execute("""SELECT r.*, s.receipt_id, p.article_number, p.model, p.product_type, p.metal_type, p.fineness,
                             mc.code AS mark
                      FROM returns r JOIN sales s ON s.id = r.original_sale_id
                      JOIN products p ON p.id = s.product_id
                      LEFT JOIN product_codes mc ON mc.id = s.mark_code_id
                      WHERE r.id = %s""", (rec['ref_id'],))
    ret = cursor.fetchone()
    if not ret:
        raise WebkassaError(0, f"возврат №{rec['ref_id']} не найден в базе")
    cursor.execute("SELECT * FROM fiscal_receipts WHERE kind = 'sale' AND ref_id = %s ORDER BY id DESC LIMIT 1",
                   (ret['receipt_id'],))
    base = cursor.fetchone()
    if base and base['status'] == 'pending':
        raise WebkassaUnavailable("исходный чек продажи ещё не пробит — сначала он")
    if not base or base['status'] != 'done' or not base.get('response'):
        raise WebkassaError(0, "исходная продажа не пробита через бота (была до включения фискализации или "
                               "оформлена вручную) — оформите возврат в кабинете Webkassa и отметьте «оформлен вручную»")
    basis = return_basis(json.loads(base['response']), base['total'], base['check_number'], base['offline'])
    if not basis["registrationNumber"]:
        raise WebkassaError(0, "в ответе на исходный чек нет регистрационного номера кассы — оформите возврат "
                               "в кабинете Webkassa и отметьте «оформлен вручную»")
    pos = _position(_item_name(ret), ret['article_number'], ret['return_price'], ret['mark'])
    return {"CashboxUniqueNumber": rec['cashbox'], "OperationType": OPERATION_SALE_RETURN, "Positions": [pos],
            "Payments": [{"Sum": pos["Price"], "PaymentType": payment_code(ret['payment_type'])}], "Change": 0,
            "RoundType": ROUND_KOPECKS, "ExternalCheckNumber": rec['external_number'], "returnBasisDetails": basis}


def _send(rec):
    """Отправить одну запись очереди. Возвращает ответ Webkassa (Data)."""
    api = client()
    if rec['kind'] == 'sale':
        return api.check(_sale_body(rec))
    if rec['kind'] == 'return':
        return api.check(_return_body(rec))
    if rec['kind'] == 'withdrawal':
        return api.money_operation(rec['cashbox'], MONEY_WITHDRAWAL, _money(rec['total']), rec['external_number'])
    raise WebkassaError(0, f"неизвестный тип операции {rec['kind']}")


# ------------------------------------------------------------------ очередь

@with_connection
def _pending(cursor, limit):
    cursor.execute("SELECT * FROM fiscal_receipts WHERE status = 'pending' ORDER BY id LIMIT %s", (limit,))
    return cursor.fetchall()


@with_transaction
def _save_result(cursor, rec_id, status, data=None, error=None, retry_in=None):
    moment = now()
    data = data or {}
    if status == 'done':
        cursor.execute("""UPDATE fiscal_receipts SET status = 'done', check_number = %s, ticket_url = %s, fiscal_at = %s,
                                 offline = %s, response = %s, error = NULL, attempts = attempts + 1, updated_at = %s
                          WHERE id = %s""",
                       (str(data.get("CheckNumber") or "") or None, data.get("TicketUrl"), moment,
                        1 if data.get("OfflineMode") else 0, json.dumps(data, ensure_ascii=False, default=str), moment,
                        rec_id))
    elif status == 'pending':
        cursor.execute("""UPDATE fiscal_receipts SET error = %s, attempts = attempts + 1, next_try_at = %s, updated_at = %s
                          WHERE id = %s""", (error, moment + retry_in, moment, rec_id))
    else:
        cursor.execute("""UPDATE fiscal_receipts SET status = 'error', error = %s, attempts = attempts + 1, updated_at = %s
                          WHERE id = %s""", (error, moment, rec_id))
    return True, None


def _delay(attempts):
    return min(timedelta(seconds=15 * 2 ** min(attempts, 10)), MAX_DELAY)


def process_queue(limit=BATCH, notify=None, on_error=None):
    """Обработать очередь. notify(rec, data) — после успешной отправки (ссылка на чек продавцу),
    on_error(rec, текст) — когда Webkassa отклонил операцию.
    Возвращает {'done': n, 'retry': n, 'error': n}."""
    stats = {'done': 0, 'retry': 0, 'error': 0}
    if not enabled():
        return stats
    blocked = set()                       # касса, по которой запись отложена, — остальные её записи ждут (порядок)
    moment, sent = now(), 0
    for rec in _pending(limit * 10) or []:
        if rec['cashbox'] in blocked:
            continue
        if rec['next_try_at'] > moment:   # пауза перед повтором: следующие чеки этой кассы не обгоняют его
            blocked.add(rec['cashbox'])
            continue
        if sent >= limit:
            break
        sent += 1
        try:
            with cashbox_lock(rec['cashbox']):
                data = _send(rec)
        except WebkassaUnavailable as e:
            blocked.add(rec['cashbox'])
            _save_result(rec['id'], 'pending', error=str(e), retry_in=_delay(rec['attempts']))
            stats['retry'] += 1
            logger.warning("Фискализация #%s (%s %s): %s — повторю", rec['id'], rec['kind'], rec['ref_id'], e)
            continue
        except WebkassaError as e:
            if e.code in CASHBOX_BLOCKING_CODES:
                # Беда кассы, а не чека: чеки ждут, пока исправят в кабинете Webkassa, и уйдут сами
                blocked.add(rec['cashbox'])
                _save_result(rec['id'], 'pending', error=f"{e} — исправьте в кабинете Webkassa, чеки уйдут сами",
                             retry_in=BLOCKED_RETRY)
                stats['retry'] += 1
                logger.error("Фискализация: касса %s не принимает чеки: %s", rec['cashbox'], e)
                if on_error and _alert_due(rec['cashbox']):
                    try:
                        on_error(rec, f"Касса {rec['cashbox']} не принимает чеки: {e.text}. Исправьте в кабинете "
                                      "Webkassa — чеки уйдут сами.")
                    except Exception as err:
                        logger.warning("Фискализация: уведомление не отправлено: %s", err)
                continue
            if e.code == NOT_ENOUGH_CASH_CODE:
                e = WebkassaError(e.code, e.text + ". В Webkassa наличных меньше, чем в учёте бота "
                                  "(например, продажи до включения фискализации): оформите изъятие в кабинете "
                                  "и отметьте «вручную»")
            _save_result(rec['id'], 'error', error=str(e))
            stats['error'] += 1
            logger.error("Фискализация #%s (%s %s) отклонена: %s", rec['id'], rec['kind'], rec['ref_id'], e)
            if on_error:
                try:
                    on_error(rec, str(e))
                except Exception as err:
                    logger.warning("Фискализация #%s: уведомление об ошибке не отправлено: %s", rec['id'], err)
            continue
        except Exception as e:            # неожиданная ошибка не должна останавливать очередь
            blocked.add(rec['cashbox'])
            logger.exception("Фискализация #%s: непредвиденная ошибка", rec['id'])
            _save_result(rec['id'], 'pending', error=f"внутренняя ошибка: {e}", retry_in=_delay(rec['attempts']))
            stats['retry'] += 1
            continue
        _save_result(rec['id'], 'done', data=data or {})
        stats['done'] += 1
        logger.info("Фискализация #%s (%s %s): чек %s", rec['id'], rec['kind'], rec['ref_id'],
                    (data or {}).get("CheckNumber"))
        if notify:
            try:
                notify(rec, data or {})
            except Exception as e:
                logger.warning("Фискализация #%s: уведомление не отправлено: %s", rec['id'], e)
    return stats


# ------------------------------------------------------------------ админка

@with_connection
def list_receipts(cursor, status=None, store_id=None, limit=200):
    where, params = [], []
    if status == 'problem':
        where.append("f.status IN ('pending', 'error')")
    elif status:
        where.append("f.status = %s")
        params.append(status)
    if store_id:
        where.append("f.store_id = %s")
        params.append(store_id)
    cursor.execute(f"""SELECT f.*, st.name AS store, u.name AS user_name FROM fiscal_receipts f
                       LEFT JOIN stores st ON st.id = f.store_id LEFT JOIN users u ON u.id = f.user_id
                       {"WHERE " + " AND ".join(where) if where else ""}
                       ORDER BY f.id DESC LIMIT %s""", (*params, limit))
    rows = cursor.fetchall()
    for r in rows:
        r['kind_title'] = KIND_TITLES.get(r['kind'], r['kind'])
        r['status_title'] = STATUS_TITLES.get(r['status'], r['status'])
    return rows


@with_connection
def counts(cursor):
    cursor.execute("SELECT status, COUNT(*) AS c FROM fiscal_receipts GROUP BY status")
    return {r['status']: r['c'] for r in cursor.fetchall()}


@with_connection
def cashboxes(cursor):
    """Магазины с кассами и числом непробитых чеков."""
    cursor.execute("""SELECT st.id, st.name, st.cashbox, st.is_active,
                             SUM(f.status = 'pending') AS pending, SUM(f.status = 'error') AS errors
                      FROM stores st LEFT JOIN fiscal_receipts f ON f.store_id = st.id AND f.cashbox = st.cashbox
                      GROUP BY st.id, st.name, st.cashbox, st.is_active, st.sort_order
                      ORDER BY st.sort_order, st.name""")
    rows = cursor.fetchall()
    for r in rows:
        r['pending'], r['errors'] = int(r['pending'] or 0), int(r['errors'] or 0)
    return rows


@with_transaction
def set_cashbox(cursor, store_id, cashbox):
    cashbox = (cashbox or "").strip().upper()[:32] or None
    if cashbox and not cashbox.replace("-", "").isalnum():
        raise BusinessError("Заводской номер кассы (ЗНК) — буквы и цифры, например SWK00031234.")
    cursor.execute("UPDATE stores SET cashbox = %s WHERE id = %s", (cashbox, store_id))
    return True, cashbox


@with_transaction
def retry(cursor, rec_id):
    cursor.execute("""UPDATE fiscal_receipts SET status = 'pending', next_try_at = %s, attempts = 0, updated_at = %s
                      WHERE id = %s AND status IN ('error', 'pending')""", (now(), now(), rec_id))
    if cursor.rowcount == 0:
        raise BusinessError("Этот чек уже пробит или оформлен вручную.")
    return True, rec_id


@with_transaction
def mark_manual(cursor, rec_id, user_id, note):
    note = (note or "").strip()[:200]
    if len(note) < 3:
        raise BusinessError("Укажите, как оформлен чек (например, номер чека в кабинете Webkassa).")
    cursor.execute("""UPDATE fiscal_receipts SET status = 'manual', error = %s, updated_at = %s
                      WHERE id = %s AND status IN ('error', 'pending')""",
                   (f"Оформлен вручную (пользователь {user_id}): {note}", now(), rec_id))
    if cursor.rowcount == 0:
        raise BusinessError("Этот чек уже пробит или оформлен вручную.")
    return True, rec_id


@with_connection
def _store(cursor, store_id):
    cursor.execute("SELECT id, name, cashbox FROM stores WHERE id = %s", (store_id,))
    return cursor.fetchone()


def _report(store_id, kind):
    store = _store(store_id)
    if not store or not store.get('cashbox'):
        return False, "У магазина не указана касса Webkassa."
    try:
        with cashbox_lock(store['cashbox']):
            api = client()
            data = api.x_report(store['cashbox']) if kind == 'x' else api.z_report(store['cashbox'])
    except WebkassaError as e:
        if e.code == NO_SHIFT_CODE:
            return False, "Смена не открыта: она откроется сама с первым чеком."
        return False, str(e)
    except WebkassaUnavailable as e:
        return False, str(e)
    return True, data or {}


def _alert_due(cashbox):
    """Напоминание о неисправной кассе — не чаще ALERT_EVERY."""
    name = f"fiscal_alert:{cashbox}"[:64]
    last = get_job_run(name)
    if last and last.get('last_run_at') and now() - last['last_run_at'] < ALERT_EVERY:
        return False
    mark_job_run(name)
    return True


def x_report(store_id):
    """X-отчёт (без закрытия смены). (True, данные) или (False, текст ошибки)."""
    return _report(store_id, 'x')


def z_report(store_id, user_id=None):
    """Закрытие смены. Нельзя, пока по кассе есть непробитые чеки: они попали бы в следующую смену."""
    store = _store(store_id)
    if not store or not store.get('cashbox'):
        return False, "У магазина не указана касса Webkassa."
    box = next((c for c in cashboxes() or [] if c['id'] == store_id), None)
    if box and (box['pending'] or box['errors']):
        return False, (f"По кассе есть непробитые чеки (ожидают: {box['pending']}, с ошибкой: {box['errors']}). "
                       "Дождитесь отправки, повторите или оформите их вручную, затем закройте смену.")
    ok, data = _report(store_id, 'z')
    if ok:
        _log_z(store_id, store['cashbox'], user_id, data)
    return ok, data


@with_transaction
def _log_z(cursor, store_id, cashbox, user_id, data):
    moment = now()
    cursor.execute("""INSERT INTO fiscal_receipts (kind, ref_id, store_id, cashbox, total, user_id, status, next_try_at,
                                                   external_number, check_number, fiscal_at, response, created_at,
                                                   updated_at)
                      VALUES ('z_report', %s, %s, %s, NULL, %s, 'done', %s, %s, NULL, %s, %s, %s, %s)""",
                   (data.get("ShiftNumber"), store_id, cashbox, user_id, moment,
                    f"pb-z-{cashbox}-{moment:%Y%m%d%H%M%S}", moment, json.dumps(data, ensure_ascii=False, default=str),
                    moment, moment))
    return True, None


@with_connection
def receipt_status(cursor, kind, ref_id):
    """Статус фискализации операции (для журнала и кассы) или None."""
    cursor.execute("SELECT status, check_number, ticket_url, error FROM fiscal_receipts WHERE kind = %s AND ref_id = %s "
                   "ORDER BY id DESC LIMIT 1", (kind, ref_id))
    return cursor.fetchone()


REPORT_FIELDS = (("ReportNumber", "Отчёт №"), ("ShiftNumber", "Смена №"), ("StartOn", "Открыта"), ("ReportOn", "Сформирован"),
                 ("CloseOn", "Закрыта"), ("CashierName", "Кассир"), ("DocumentCount", "Документов"),
                 ("PutMoneySum", "Внесено"), ("TakeMoneySum", "Изъято"), ("SumInCashbox", "Наличных в кассе"))


def report_lines(data):
    """Короткая сводка отчёта для экрана: известные поля и итоги продаж/возвратов, остальное — как есть."""
    data = data or {}
    lines = [(title, data[key]) for key, title in REPORT_FIELDS if data.get(key) not in (None, "")]
    for key, title in (("Sell", "Продажи"), ("ReturnSell", "Возвраты продаж"), ("Buy", "Покупки"),
                       ("ReturnBuy", "Возвраты покупок")):
        block = find_key(data, key)
        if isinstance(block, dict) and (block.get("Count") or block.get("Taken")):
            lines.append((f"{title}: чеков", block.get("Count", 0)))
            lines.append((f"{title}: сумма", block.get("Taken", 0)))
            for pay in block.get("PaymentsByTypesApiModel") or []:
                lines.append((f"{title}: {PAYMENT_TITLES.get(pay.get('Type'), pay.get('Type'))}", pay.get("Sum")))
    if not lines:
        lines = [(k, v) for k, v in data.items() if not isinstance(v, (dict, list))][:15]
    return [[str(k), money(v) if isinstance(v, float) else str(v)] for k, v in lines]
