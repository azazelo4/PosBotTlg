"""Внесение денег в кассу: страница веб-админки, отчёты, касса ПК, фискализация, миграция enum."""
import io
from decimal import Decimal

import openpyxl

from conftest import ADMIN, MANAGER, db_query
from core.database import create_cash_deposit_record, create_cash_withdrawal_record, get_period_operations, record_receipt
from test_stores import _web, _csrf
from utils.common import day_bounds, today
from utils.report_generator import build_summary, summary_text


def _pid(article):
    return db_query("SELECT id FROM products WHERE article_number=%s", (article,))[0]['id']


def _uid(role):
    return db_query("SELECT id FROM users WHERE role=%s", (role,))[0]['id']


def test_deposit_page_for_manager(tg):
    manager = _web(tg, MANAGER)
    page = manager.get("/cash").text
    assert "Внесение денег в кассу" in page and "Операций пока нет" in page
    csrf = _csrf(manager, "/cash")
    assert "больше нуля" in manager.post("/cash", data={"csrf": csrf, "store_id": 1, "amount": "0"}).text
    assert "Выберите магазин" in manager.post("/cash", data={"csrf": csrf, "store_id": "", "amount": "1000"}).text
    assert manager.post("/cash", data={"store_id": 1, "amount": "1000"}).status_code == 400          # без CSRF
    r = manager.post("/cash", data={"csrf": csrf, "store_id": 1, "amount": "20 000", "comment": "размен"})
    assert "Внесение №1: 20 000 ₸" in r.text and "+20 000 ₸" in r.text and "размен" in r.text
    d = db_query("SELECT * FROM cash_deposits")[0]
    assert d['amount'] == 20000 and d['store_id'] == 1 and d['comment'] == "размен"
    assert 'href="/cash"' in _web(tg, ADMIN).get("/").text                                       # пункт меню


def test_deposit_in_reports_and_kassa_shift(tg):
    uid = _uid('admin')
    record_receipt([(_pid("A100"), 150000)], uid, "Наличные")
    assert create_cash_withdrawal_record(100000, "в банк", uid, 1)[0]
    assert create_cash_deposit_record(Decimal(30000), "размен", uid, 1)[0]
    assert not create_cash_deposit_record(Decimal(-5), "", uid, 1)[0]
    s = build_summary(get_period_operations(*day_bounds(today(), today())))
    assert s['deposits_count'] == 1 and s['deposits_total'] == 30000
    assert s['cash_balance'] == 150000 - 100000 + 30000
    text = summary_text(s, today(), today())
    assert "Внесение в кассу: 1 раз на 30 000 ₸" in text and "<b>80 000 ₸</b>" in text
    manager = _web(tg, MANAGER)
    page = manager.get("/reports").text
    assert "Внесение в кассу" in page and "внесено 30 000 ₸" in page
    wb = openpyxl.load_workbook(io.BytesIO(manager.get("/reports.xlsx").content))
    assert list(wb["Внесение"].values)[1][2] == 30000
    # касса на ПК: итоги смены и журнал
    from web import kassa
    from fastapi.testclient import TestClient
    from web.app import app
    ops = get_period_operations(*day_bounds(today(), today()), store_id=1)
    journal = kassa._journal(ops)
    assert any(o["kind"] == "Внесение" and o["amount"] == 30000 and o["what"] == "размен" for o in journal)
    assert TestClient(app).get("/kassa/api/shift").status_code in (401, 403)


def test_old_fiscal_enum_is_extended(tg):
    from core.database import get_connection
    from core.schema import ensure_schema
    db_query("ALTER TABLE fiscal_receipts MODIFY COLUMN kind enum('sale','return','withdrawal','z_report') NOT NULL")
    conn = get_connection()
    try:
        ensure_schema(conn)
    finally:
        conn.close()
    col = db_query("SELECT COLUMN_TYPE AS t FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                   "AND TABLE_NAME='fiscal_receipts' AND COLUMN_NAME='kind'")[0]['t']
    assert "'deposit'" in (col.decode() if isinstance(col, bytes) else col)
