"""Аналитика продаж (#29): сводка со сравнениями, структура, скидки, время покупок, ABC, склад, металл, маржа."""
import io
from datetime import datetime, timedelta
from decimal import Decimal

import openpyxl

import analytics
from fastapi.testclient import TestClient

from conftest import ADMIN, MANAGER, db_query
from database import record_receipt, create_return_record, create_buyback_record
from test_stores import _web
from utils.common import today
from web.app import app


def _pid(article):
    return db_query("SELECT id FROM products WHERE article_number=%s", (article,))[0]['id']


def _uid(role):
    return db_query("SELECT id FROM users WHERE role=%s", (role,))[0]['id']


def _sell(items, role, payment, when):
    ok, r = record_receipt(items, _uid(role), payment)
    assert ok, r
    db_query("UPDATE receipts SET created_at=%s WHERE id=%s", (when, r['receipt_id']))
    db_query("UPDATE sales SET sale_date=%s WHERE receipt_id=%s", (when, r['receipt_id']))
    return r


def _at(days_ago, hour):
    return datetime.combine(today() - timedelta(days=days_ago), datetime.min.time()).replace(hour=hour)


def _period(days=30, **kw):
    return analytics.Filters(today() - timedelta(days=days - 1), today(), **kw)


def _setup(tg):
    db_query("UPDATE products SET quantity=10, cost_price=100000 WHERE article_number='A100'")
    db_query("UPDATE products SET quantity=5 WHERE article_number='A200'")
    a100, a200 = _pid("A100"), _pid("A200")
    # текущий период: 3 чека
    _sell([(a100, 150000), (a100, 135000)], 'cashier', "Kaspi", _at(1, 11))           # скидка 15 000 на второй
    _sell([(a200, 20000)], 'manager', "Наличные", _at(2, 18))
    r = _sell([(a100, 150000)], 'cashier', [("Наличные", 50000), ("Kaspi", 100000)], _at(3, 11))
    # возвращённая продажа в выручку не входит
    back = _sell([(a100, 140000)], 'cashier', "Наличные", _at(4, 15))
    ok, _ = create_return_record(back['sale_ids'][0], "A100", 140000, "Наличные", _uid('admin'), 1)
    assert ok
    # прошлый период (31–60 дней назад) и прошлый год
    _sell([(a100, 150000)], 'cashier', "Kaspi", _at(40, 12))
    _sell([(a100, 150000)], 'cashier', "Kaspi", _at(365 + 5, 12))
    _sell([(a200, 20000)], 'cashier', "Kaspi", _at(365 + 5, 13))
    return r


def test_summary_and_comparisons(tg):
    _setup(tg)
    data = analytics.build(_period())
    s = data['summary']
    assert s['revenue'] == Decimal(455000) and s['qty'] == 4 and s['receipts'] == 3 and s['returned'] == 1
    assert s['avg_check'] == Decimal(455000) / 3
    assert s['discount'] == Decimal(15000) and round(s['discounted_share']) == 25
    # себестоимость есть у трёх A100: маржа (435 000 − 300 000)
    assert s['margin'] == Decimal(135000) and round(s['cost_coverage']) == 75
    assert data['previous']['revenue'] == Decimal(150000) and data['year']['revenue'] == Decimal(170000)
    assert round(data['compare']['revenue']['previous']) == 203 and round(data['compare']['receipts']['year']) == 50
    assert data['returns']['n'] == 1 and data['returns']['total'] == Decimal(140000)
    t = data['trend']
    assert t['kind'] == "day" and len(t['labels']) == 30 and sum(t['revenue']) == Decimal(455000)
    assert sum(t['previous']) == Decimal(150000) and sum(t['receipts']) == 3


def test_structure_payments_sellers_heatmap(tg):
    _setup(tg)
    data = analytics.build(_period())
    st = data['structure']
    assert [(g['name'], g['count']) for g in st['types']] == [("кольцо", 3), ("цепь", 1)]
    # смешанный чек раскладывается на части по способам оплаты
    pays = {p['name']: p['revenue'] for p in st['payments']}
    assert pays == {"Kaspi": Decimal(385000), "Наличные": Decimal(70000)}
    sellers = {p['name']: p for p in data['sellers']}
    assert sellers["Кассир"]['revenue'] == Decimal(435000) and sellers["Кассир"]['receipts'] == 2
    assert sellers["Менеджер"]['discount'] == 0
    h = data['heatmap']
    assert h['max'] == 1
    wd = _at(1, 11).weekday()
    assert h['cells'][wd][h['hours'].index(11)] == 1
    assert sum(map(sum, h['cells'])) == 3


def test_filters_type_and_seller(tg):
    _setup(tg)
    by_type = analytics.build(_period(product_type="цепь"))
    assert by_type['summary']['revenue'] == Decimal(20000) and by_type['metal']['buybacks'] == []
    by_seller = analytics.build(_period(user_id=_uid('manager')))
    assert by_seller['summary']['receipts'] == 1
    by_store = analytics.build(_period(store_id=2))
    assert by_store['summary']['revenue'] == Decimal(20000)


def test_abc_stale_turnover_metal(tg):
    _setup(tg)
    db_query("""INSERT INTO products (article_number, store_id, metal_type, product_type, model, weight, price_per_gram,
                price, min_price, arrival_date, quantity, track_stock) VALUES
             ('OLD1', 1, 'золото', 'брошь', 'Брошь Старая', 4, 40000, 160000, 150000, %s, 2, 1)""",
             (today() - timedelta(days=200),))
    create_buyback_record(None, 80000, "Наличные", _uid('cashier'), 1, "лом", "золото", 4.0)
    data = analytics.build(_period())
    abc = data['abc']
    assert [a['article'] for a in abc['top']] == ["A100", "A200"]
    assert abc['top'][0]['abc'] == "A" and abc['top'][1]['abc'] == "C"     # A100 даёт 96% выручки
    stale = data['stock']['stale']
    assert [p['article_number'] for p in stale] == ["OLD1"] and stale[0]['value'] == Decimal(320000)
    turnover = {t['name']: t for t in data['stock']['turnover']}
    assert turnover['брошь']['days_of_stock'] is None and turnover['кольцо']['sold'] == 3
    metal = {(m['metal']): m for m in data['metal']['sold']}
    assert metal['золото']['grams'] == 7.5 and metal['серебро']['grams'] == 10
    assert data['metal']['buybacks'][0]['per_gram'] == Decimal(20000)
    assert data['margin'][0]['name'] == "кольцо" and data['margin'][0]['margin'] == Decimal(135000)


def test_long_period_buckets():
    f = analytics.Filters(today() - timedelta(days=199), today())
    assert analytics.trend(f, [])['kind'] == "week"
    f = analytics.Filters(today() - timedelta(days=500), today())
    assert analytics.trend(f, [])['kind'] == "month"
    assert analytics.Filters(today(), today() - timedelta(days=5)).days == 6       # перепутанные даты


def test_web_page_and_excel(tg):
    _setup(tg)
    anon = TestClient(app, base_url="https://testserver")
    assert anon.get("/analytics", follow_redirects=False).status_code in (302, 303, 401)
    manager = _web(tg, MANAGER)
    page = manager.get("/analytics")
    assert page.status_code == 200, page.text
    for text in ("Аналитика продаж", "455 000 ₸", "Продавцы", "Когда покупают", "ABC-анализ", "Залежавшийся товар",
                 "Металл", "Маржа", "/static/chart.umd.min.js", 'id="chart-data"'):
        assert text in page.text, text
    assert manager.get("/static/chart.umd.min.js").status_code == 200
    page = manager.get("/analytics", params={"type": "цепь", "seller": "999999", "metal": "платина"})
    assert page.status_code == 200 and "20 000 ₸" in page.text
    x = manager.get("/analytics.xlsx", params={"start": (today() - timedelta(days=29)).isoformat()})
    assert x.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames[:3] == ["Сводка", "Динамика", "Структура"] and "ABC" in wb.sheetnames
    assert wb["Сводка"]["B8"].value == 455000
    admin = _web(tg, ADMIN)
    assert 'href="/analytics"' in admin.get("/").text
