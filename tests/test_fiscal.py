"""Фискализация через Webkassa (#17): очередь, отправка, повторы, ошибки, возврат по основанию, смены.

Webkassa подменяется заглушкой на уровне HTTP (opener клиента), формат ответов — как в документации интегратора v4.
"""
import json
import urllib.error

import pytest

import config
import fiscal
import marking
from conftest import ADMIN, CASHIER, MANAGER, login, db_query
from database import record_receipt, create_return_record, create_cash_withdrawal_record
from test_stores import _web, _csrf
from utils import scheduled_jobs as jobs
from utils.webkassa import WebkassaClient

REG_NUMBER = "600400012345"


class FakeWebkassa:
    """Заглушка API: записывает запросы и отвечает по сценарию."""

    def __init__(self):
        self.requests = []
        self.down = False              # нет связи
        self.reject = None             # (код, текст) — отклонить следующий чек
        self.expire_token_once = False
        self.check_number = 1000
        self.no_license = False        # как на новой тестовой кассе devkkm: код 10
        self.no_shift = False

    def __call__(self, req, timeout=None):
        path = req.full_url.split("webkassa.test", 1)[1]
        body = json.loads(req.data.decode("utf-8"))
        self.requests.append((path, body, dict(req.header_items())))
        if self.down:
            raise urllib.error.URLError("Network is unreachable")
        if path == "/api/v4/Authorize":
            return self._reply({"Data": {"Token": "tok-%d" % len(self.requests)}})
        if self.expire_token_once:
            self.expire_token_once = False
            return self._reply({"Errors": [{"Code": 2, "Text": "Срок действия сессии истек"}]})
        if self.no_license and path in ("/api/v4/check", "/api/v4/MoneyOperation"):
            return self._reply({"Errors": [{"Code": 10, "Text": "Касса не имеет активной лицензии"}]})
        if self.no_shift and path in ("/api/v4/XReport", "/api/v4/ZReport"):
            return self._reply({"Errors": [{"Code": 13, "Text": "На кассе отсутствуют смены"}]})
        if self.reject:
            code, text = self.reject
            self.reject = None
            return self._reply({"Errors": [{"Code": code, "Text": text}]})
        if path == "/api/v4/check":
            self.check_number += 1
            total = sum(p["Sum"] for p in body["Payments"])
            return self._reply({"Data": {
                "CheckNumber": str(self.check_number), "DateTime": "09.11.2026 10:00:00", "OfflineMode": False,
                "Cashbox": {"UniqueNumber": body["CashboxUniqueNumber"], "RegistrationNumber": REG_NUMBER},
                "TicketUrl": f"https://devkkm.webkassa.kz/Ticket?chb={body['CashboxUniqueNumber']}&n={self.check_number}",
                "Total": total, "ShiftNumber": 7}})
        if path == "/api/v4/MoneyOperation":
            return self._reply({"Data": {"Sum": body["Sum"], "OfflineMode": False}})
        if path in ("/api/v4/XReport", "/api/v4/ZReport"):
            return self._reply({"Data": {"ShiftNumber": 7, "SumInCashbox": 150000, "DocumentCount": 3,
                                         "Sell": {"Count": 2, "Taken": 300000}}})
        raise AssertionError(f"неожиданный запрос {path}")

    @staticmethod
    def _reply(payload):
        return _Resp(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def paths(self):
        return [p for p, _, _ in self.requests]

    def checks(self):
        return [b for p, b, _ in self.requests if p == "/api/v4/check"]


class _Resp:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def wk(tg, monkeypatch):
    fake = FakeWebkassa()
    for key, value in (("FISCAL_ENABLED", True), ("WEBKASSA_URL", "https://webkassa.test"), ("WEBKASSA_LOGIN", "kassir@shop.kz"),
                       ("WEBKASSA_PASSWORD", "secret"), ("WEBKASSA_API_KEY", "KEY-1"), ("FISCAL_VAT_PERCENT", 0.0),
                       ("FISCAL_KASPI_MOBILE", True)):
        monkeypatch.setattr(config, key, value)
    monkeypatch.setattr(fiscal, "_client", WebkassaClient("https://webkassa.test", "kassir@shop.kz", "secret", "KEY-1",
                                                          opener=fake))
    db_query("UPDATE stores SET cashbox = 'SWK00030001' WHERE id = 1")
    return fake


def _pid(article):
    return db_query("SELECT id FROM products WHERE article_number=%s", (article,))[0]['id']


def _uid(role):
    return db_query("SELECT id FROM users WHERE role=%s", (role,))[0]['id']


def _queue():
    return db_query("SELECT * FROM fiscal_receipts ORDER BY id")


def _due_now():
    db_query("UPDATE fiscal_receipts SET next_try_at = NOW() - INTERVAL 1 DAY WHERE status = 'pending'")


def test_nothing_queued_when_off_or_without_cashbox(tg, monkeypatch):
    uid = _uid('admin')
    assert record_receipt([(_pid("A100"), 150000)], uid, "Kaspi")[0]
    monkeypatch.setattr(config, "FISCAL_ENABLED", True)
    assert record_receipt([(_pid("A100"), 150000)], uid, "Kaspi")[1]['fiscal'] is False   # у магазина нет кассы
    assert _queue() == []


def test_sale_is_fiscalized_and_seller_gets_link(wk, tg):
    login(tg, CASHIER)
    ok, r = record_receipt([(_pid("A100"), 150000), (_pid("A100"), 140000)], _uid('cashier'), "Kaspi")
    assert ok and r['fiscal'] is True
    assert [q['status'] for q in _queue()] == ['pending']
    tg.calls.clear()
    assert jobs.fiscal_tick() == {'done': 1, 'retry': 0, 'error': 0}
    assert wk.paths() == ["/api/v4/Authorize", "/api/v4/check"]
    _, auth, headers = wk.requests[0]
    assert auth == {"Login": "kassir@shop.kz", "Password": "secret"} and headers.get("X-api-key") == "KEY-1"
    body = wk.checks()[0]
    assert body["Token"] and body["CashboxUniqueNumber"] == "SWK00030001" and body["OperationType"] == 2
    assert [p["Price"] for p in body["Positions"]] == [150000, 140000]
    assert body["Positions"][0]["TaxType"] == 0 and body["Positions"][0]["UnitCode"] == 796
    assert "A100" in body["Positions"][0]["PositionName"] and "Mark" not in body["Positions"][0]
    assert body["Payments"] == [{"Sum": 290000, "PaymentType": 4}] and body["RoundType"] == 0
    assert body["ExternalCheckNumber"] == _queue()[0]['external_number']
    rec = _queue()[0]
    assert rec['status'] == 'done' and rec['check_number'] == "1001" and "Ticket" in rec['ticket_url']
    msg = [c for c in tg.calls if c.chat_id == CASHIER and c.text and "Фискальный чек" in c.text]
    assert msg and rec['ticket_url'] in msg[0].text
    assert jobs.fiscal_tick() == {'done': 0, 'retry': 0, 'error': 0}           # повторно не отправляется


def test_cash_payment_vat_and_mark_code(wk, tg, monkeypatch):
    monkeypatch.setattr(config, "FISCAL_VAT_PERCENT", 12.0)
    monkeypatch.setattr(config, "MARKING_ENABLED", True)
    code = "0104601234567890215abcdefghijkl\x1d93Ab+/"
    marking.accept_codes(_pid("A100"), [code], _uid('admin'), add_to_stock=False)
    assert record_receipt([(_pid("A100"), 134400, code)], _uid('admin'), "Наличные")[0]
    fiscal.process_queue()
    pos = wk.checks()[0]["Positions"][0]
    assert pos["Mark"] == code and pos["TaxType"] == 100 and pos["TaxPercent"] == 12.0 and pos["Tax"] == 14400.0
    assert wk.checks()[0]["Payments"][0]["PaymentType"] == 0


def test_no_connection_retries_later_in_order(wk, tg):
    uid = _uid('admin')
    record_receipt([(_pid("A100"), 150000)], uid, "Безналичные")
    record_receipt([(_pid("A100"), 140000)], uid, "Наличные")
    wk.down = True
    assert fiscal.process_queue() == {'done': 0, 'retry': 1, 'error': 0}      # второй чек кассы ждёт первый
    first, second = _queue()
    assert first['status'] == 'pending' and first['attempts'] == 1 and "нет связи" in first['error']
    assert first['next_try_at'] > first['created_at'] and second['attempts'] == 0
    assert fiscal.process_queue()['retry'] == 0                                 # пауза ещё не прошла
    wk.down = False
    _due_now()
    assert fiscal.process_queue()['done'] == 2
    assert [b["Payments"][0]["PaymentType"] for b in wk.checks()] == [1, 0]


def test_expired_token_reauthorizes(wk, tg):
    record_receipt([(_pid("A100"), 150000)], _uid('admin'), "Kaspi")
    fiscal.process_queue()
    record_receipt([(_pid("A100"), 150000)], _uid('admin'), "Kaspi")
    wk.expire_token_once = True
    assert fiscal.process_queue()['done'] == 1
    assert wk.paths().count("/api/v4/Authorize") == 2


def test_rejected_check_needs_attention(wk, tg):
    login(tg, ADMIN)
    login(tg, MANAGER)
    record_receipt([(_pid("A100"), 150000)], _uid('admin'), "Kaspi")
    wk.reject = (9, "Смена превысила 24 часа")
    tg.calls.clear()
    assert jobs.fiscal_tick()['error'] == 1
    rec = _queue()[0]
    assert rec['status'] == 'error' and "24 часа" in rec['error']
    alerts = [c for c in tg.calls if c.text and "Чек не пробит" in c.text]
    assert {c.chat_id for c in alerts} == {ADMIN, MANAGER}
    assert fiscal.retry(rec['id'])[0] and fiscal.process_queue()['done'] == 1
    record_receipt([(_pid("A100"), 150000)], _uid('admin'), "Kaspi")
    wk.reject = (9, "ошибка")
    fiscal.process_queue()
    rec = _queue()[-1]
    assert not fiscal.mark_manual(rec['id'], _uid('admin'), "")[0]
    assert fiscal.mark_manual(rec['id'], _uid('admin'), "чек 5512 в кабинете")[0]
    assert _queue()[-1]['status'] == 'manual'


def test_return_refers_to_original_check(wk, tg):
    uid = _uid('admin')
    ok, r = record_receipt([(_pid("A100"), 150000)], uid, "Kaspi")
    ok, return_id = create_return_record(r['sale_ids'][0], "A100", 150000, "Kaspi", uid, 1)
    assert ok
    assert fiscal.process_queue()['done'] == 2                                  # сначала продажа, затем возврат
    body = wk.checks()[1]
    assert body["OperationType"] == 3 and body["Positions"][0]["Price"] == 150000
    basis = body["returnBasisDetails"]
    assert basis["checkNumber"] == "1001" and basis["registrationNumber"] == REG_NUMBER
    assert basis["total"] == 150000 and basis["isOffline"] is False and basis["dateTime"]


def test_return_of_sale_before_fiscalization(wk, tg, monkeypatch):
    uid = _uid('admin')
    monkeypatch.setattr(config, "FISCAL_ENABLED", False)
    ok, r = record_receipt([(_pid("A100"), 150000)], uid, "Kaspi")
    monkeypatch.setattr(config, "FISCAL_ENABLED", True)
    create_return_record(r['sale_ids'][0], "A100", 150000, "Kaspi", uid, 1)
    assert fiscal.process_queue()['error'] == 1
    assert "кабинете Webkassa" in _queue()[0]['error'] and wk.checks() == []


def test_withdrawal_and_shift_close(wk, tg):
    uid = _uid('admin')
    assert create_cash_withdrawal_record(50000, "инкассация", uid, 1)[0]
    record_receipt([(_pid("A100"), 150000)], uid, "Наличные")
    ok, msg = fiscal.z_report(1, uid)
    assert not ok and "непробитые чеки" in msg                                 # пока очередь не отправлена
    assert fiscal.process_queue()['done'] == 2
    money_op = [b for p, b, _ in wk.requests if p == "/api/v4/MoneyOperation"][0]
    assert money_op["OperationType"] == 1 and money_op["Sum"] == 50000 and money_op["CashboxUniqueNumber"] == "SWK00030001"
    ok, data = fiscal.x_report(1)
    assert ok and data["SumInCashbox"] == 150000
    ok, data = fiscal.z_report(1, uid)
    assert ok and _queue()[-1]['kind'] == 'z_report' and _queue()[-1]['ref_id'] == 7
    assert ("Смена №", "7") in [tuple(x) for x in fiscal.report_lines(data)]
    assert not fiscal.z_report(2, uid)[0]                                      # у второго магазина кассы нет


def test_admin_page(wk, tg):
    record_receipt([(_pid("A100"), 150000)], _uid('admin'), "Kaspi")
    manager = _web(tg, MANAGER)
    page = manager.get("/fiscal").text
    assert "Фискализация (Webkassa)" in page and "ожидает" in page and "SWK00030001" in page
    csrf = _csrf(manager, "/fiscal")
    assert manager.post("/fiscal/cashbox", data={"csrf": csrf, "store_id": 2, "cashbox": "X"}).status_code == 403
    r = manager.post("/fiscal/report", data={"csrf": csrf, "store_id": 1, "kind": "z"})
    assert "непробитые чеки" in r.text
    fiscal.process_queue()
    r = manager.post("/fiscal/report", data={"csrf": csrf, "store_id": 1, "kind": "x"})
    assert "X-отчёт" in r.text and "Наличных в кассе" in r.text
    assert "Все чеки пробиты" in manager.get("/fiscal").text
    admin = _web(tg, ADMIN)
    csrf = _csrf(admin, "/fiscal")
    r = admin.post("/fiscal/cashbox", data={"csrf": csrf, "store_id": 2, "cashbox": "swk00030002"})
    assert "SWK00030002" in r.text
    assert db_query("SELECT cashbox FROM stores WHERE id = 2")[0]['cashbox'] == "SWK00030002"


def test_cashbox_without_license_waits_and_alerts_once(wk, tg):
    login(tg, ADMIN)
    uid = _uid('admin')
    record_receipt([(_pid("A100"), 150000)], uid, "Kaspi")
    record_receipt([(_pid("A100"), 140000)], uid, "Kaspi")
    wk.no_license = True
    tg.calls.clear()
    assert jobs.fiscal_tick() == {'done': 0, 'retry': 1, 'error': 0}      # второй чек кассы ждёт за первым
    assert [q['status'] for q in _queue()] == ['pending', 'pending']
    assert "лицензии" in _queue()[0]['error'] and "уйдут сами" in _queue()[0]['error']
    _due_now()
    jobs.fiscal_tick()
    alerts = [c for c in tg.calls if c.text and "не принимает чеки" in c.text]
    assert len(alerts) == 1                                                # напоминание — не чаще раза в час
    wk.no_license = False                                                  # лицензию активировали в кабинете
    _due_now()
    assert fiscal.process_queue()['done'] == 2


def test_known_errors_get_clear_messages(wk, tg):
    wk.no_shift = True
    ok, msg = fiscal.x_report(1)
    assert not ok and "Смена не открыта" in msg
    wk.no_shift = False
    create_cash_withdrawal_record(50000, "инкассация", _uid('admin'), 1)
    wk.reject = (8, "В кассе недостаточно наличности для проведения операции (В кассе: 0.000)")
    fiscal.process_queue()
    rec = _queue()[0]
    assert rec['status'] == 'error' and "оформите изъятие в кабинете" in rec['error']
