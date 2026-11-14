"""Смешанная оплата одного чека (#31): часть одним способом, часть другим."""
from decimal import Decimal

from fastapi.testclient import TestClient

from core import config
from services import fiscal
from conftest import CASHIER, login, db_query
from core.database import record_receipt, get_receipt, get_period_operations, MIXED_PAYMENT
from test_fiscal import wk  # noqa: F401  (фикстура)
from test_kassa import client, kassa_login, post  # noqa: F401  (фикстура)
from utils.common import today, day_bounds, money
from utils.report_generator import build_summary
from web.app import app


def _pid(article):
    return db_query("SELECT id FROM products WHERE article_number=%s", (article,))[0]['id']


def _uid(role='admin'):
    return db_query("SELECT id FROM users WHERE role=%s", (role,))[0]['id']


def _summary():
    return build_summary(get_period_operations(*day_bounds(today(), today())))


def test_split_receipt_and_money_flow(tg):
    a100 = _pid("A100")
    ok, msg = record_receipt([(a100, 150000)], _uid(), [("Наличные", 100000), ("Kaspi", 40000)])
    assert not ok and "не равны итогу" in msg
    ok, msg = record_receipt([(a100, 150000)], _uid(), [("Наличные", 100000), ("Наличные", 50000)])
    assert not ok and "дважды" in msg
    ok, r = record_receipt([(a100, 150000), (a100, 140000)], _uid(), [("Наличные", 200000), ("Kaspi", 90000)])
    assert ok and r['payment'] == f"Наличные {money(200000)} + Kaspi {money(90000)}"
    assert db_query("SELECT payment_type FROM receipts")[0]['payment_type'] == MIXED_PAYMENT
    assert [(p['payment_type'], int(p['amount'])) for p in db_query("SELECT * FROM receipt_payments ORDER BY id")] == \
        [("Наличные", 200000), ("Kaspi", 90000)]
    receipt = get_receipt(r['receipt_id'])
    assert receipt['payment_label'] == r['payment'] and len(receipt['payments']) == 2
    s = _summary()
    assert s['flow'] == {"Наличные": Decimal(200000), "Kaspi": Decimal(90000)}      # по оплатам, а не по позициям
    assert s['cash_balance'] == Decimal(200000) and s['sales_total'] == Decimal(290000) and s['receipts_count'] == 1
    # обычный чек рядом считается как раньше
    record_receipt([(a100, 150000)], _uid(), "Kaspi")
    assert _summary()['flow'] == {"Наличные": Decimal(200000), "Kaspi": Decimal(240000)}


def test_kassa_pc_split(tg, client):  # noqa: F811
    kassa_login(tg, client)
    body = {"items": [{"article": "A100", "price": 150000}],
            "payments": [{"type": "Наличные", "amount": 50000}, {"type": "Безналичные", "amount": 90000}]}
    r = post(client, "sale", body)
    assert r.status_code == 400 and "не равны итогу" in r.json()["error"]
    body["payments"][1]["amount"] = 100000
    r = post(client, "sale", body)
    assert r.status_code == 200 and "Наличные 50 000 ₸ + Безналичные 100 000 ₸" in r.json()["text"]
    assert post(client, "sale", {"items": [{"article": "A100", "price": 150000}],
                                 "payments": [{"type": "Биткоин", "amount": 150000}]}).status_code == 400
    shift = client.get("/kassa/api/shift").json()
    assert "Наличные 50 000 ₸ + Безналичные 100 000 ₸" in str(shift)
    page = TestClient(app).get(f"/r/{db_query('SELECT public_token FROM receipts')[0]['public_token']}").text
    assert "Наличные 50 000 ₸ + Безналичные 100 000 ₸" in page


def test_bot_split(tg):
    login(tg, CASHIER)
    tg.send(CASHIER, "Продажа")
    tg.send(CASHIER, "A100")
    tg.send(CASHIER, "150000")
    assert "Первый способ" in tg.texts(tg.send(CASHIER, "➗ Разделить оплату"))
    tg.send(CASHIER, "Наличные")
    assert "меньше итога" in tg.texts(tg.send(CASHIER, "150000"))
    calls = tg.send(CASHIER, "100000")
    assert "Остаток" in tg.texts(calls) and "50 000 ₸" in tg.texts(calls)
    assert "второй способ" in tg.texts(tg.send(CASHIER, "Наличные"))            # тот же способ нельзя
    calls = tg.send(CASHIER, "Kaspi")
    assert "Наличные 100 000 ₸ + Kaspi 50 000 ₸" in tg.texts(calls)
    assert "Наличные 100 000 ₸ + Kaspi 50 000 ₸" in tg.texts(tg.send(CASHIER, "Подтвердить"))
    assert _summary()['flow'] == {"Наличные": Decimal(100000), "Kaspi": Decimal(50000)}


def test_fiscal_check_has_all_payments(wk, tg, monkeypatch):  # noqa: F811
    a100 = _pid("A100")
    record_receipt([(a100, 150000)], _uid(), [("Наличные", 100000), ("Kaspi", 50000)])
    fiscal.process_queue()
    assert wk.checks()[0]["Payments"] == [{"Sum": 100000.0, "PaymentType": 0}, {"Sum": 50000.0, "PaymentType": 4}]
    # Kaspi как карта: «Безналичные» и Kaspi — один вид оплаты в чеке, суммы складываются
    monkeypatch.setattr(config, "FISCAL_KASPI_MOBILE", False)
    record_receipt([(a100, 150000)], _uid(), [("Безналичные", 100000), ("Kaspi", 50000)])
    fiscal.process_queue()
    assert wk.checks()[1]["Payments"] == [{"Sum": 150000.0, "PaymentType": 1}]


def test_report_excel_shows_split(tg):
    import io
    import openpyxl
    from utils.report_generator import generate_period_report
    record_receipt([(_pid("A100"), 150000)], _uid(), [("Наличные", 100000), ("Kaspi", 50000)])
    text, excel, _ = generate_period_report(today(), today())
    assert "Наличные" in text and "100 000 ₸" in text and "Kaspi" in text and "Смешанная" not in text
    wb = openpyxl.load_workbook(io.BytesIO(excel.getvalue()))
    cells = [str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row]
    assert any("Наличные 100 000 ₸ + Kaspi 50 000 ₸" in c for c in cells)
