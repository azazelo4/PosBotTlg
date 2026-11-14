"""Аналитика по клиентам: кто, сколько и что покупает; сегменты; профиль в карточке; демо-клиенты."""
import io
from datetime import date, timedelta
from decimal import Decimal

import openpyxl

from services import analytics
from services import customers
from services import demo_data
from conftest import ADMIN, MANAGER, db_query
from test_analytics import _pid, _at, _period, _sell
from test_stores import _web, _csrf
from utils.common import today



def _buy(items, when, customer=None, payment="Kaspi"):
    r = _sell(items, 'cashier', payment, when)
    if customer:
        cid = db_query("SELECT id FROM customers WHERE phone=%s", (customer,))
        if not cid:
            db_query("""INSERT INTO customers (phone, name, consent_at, consent_source, created_at, updated_at)
                        VALUES (%s, %s, NOW(), 'kassa', NOW(), NOW())""", (customer, {"+77011112233": "Анна"}.get(customer)))
            cid = db_query("SELECT id FROM customers WHERE phone=%s", (customer,))
        db_query("UPDATE receipts SET customer_id=%s WHERE id=%s", (cid[0]['id'], r['receipt_id']))
    return r


def _setup():
    db_query("UPDATE products SET quantity=20")
    a100, a200 = _pid("A100"), _pid("A200")
    anna, bek = "+77011112233", "+77022223344"
    # Анна: постоянная — покупала полгода назад, 2 месяца назад и дважды в этом периоде
    _buy([(a100, 150000)], _at(200, 12), anna)
    _buy([(a200, 20000)], _at(60, 12), anna)
    _buy([(a100, 150000)], _at(10, 12), anna)
    _buy([(a200, 20000)], _at(2, 15), anna)
    # Бек: новый, одна покупка в периоде
    _buy([(a100, 140000)], _at(5, 11), bek)
    # без номера
    _buy([(a200, 20000)], _at(3, 11))
    # Вера: уснувшая (год назад), в периоде не покупала
    _buy([(a200, 20000)], _at(300, 11), "+77033334455")
    return anna, bek


def test_clients_summary_top_and_segments(tg):
    _setup()
    cl = analytics.build(_period())['clients']
    assert cl['in_base'] == 3 and cl['buyers'] == 2 and cl['new'] == 1 and cl['back'] == 1
    assert cl['revenue'] == Decimal(310000) and round(cl['revenue_share']) == 94          # 310 из 330 тыс.
    assert cl['receipts'] == 3 and cl['anon_avg_check'] == Decimal(20000)
    assert round(cl['avg_check']) == 103333 and cl['per_buyer'] == Decimal(155000)
    anna, bek = cl['top']
    assert anna['name'] == "Анна" and anna['revenue'] == Decimal(170000) and anna['receipts'] == 2
    assert anna['favorite'] == "кольцо" and anna['total'] == Decimal(340000) and anna['total_receipts'] == 4
    assert anna['segment'] == "Постоянные" and not anna['is_new']
    assert bek['is_new'] and bek['segment'] == "Новые" and bek['phone'] == "+77022223344"
    seg = {g['name']: g['count'] for g in cl['segments']}
    assert seg == {"Постоянные": 1, "Растущие": 0, "Новые": 1, "Разовые": 0, "Под угрозой": 0, "Уснувшие": 1}
    what = {t['name']: (t['people'], t['count']) for t in cl['what']}
    assert what == {"кольцо": (2, 2), "цепь": (1, 1)}
    t = cl['trend']
    assert sum(t['new']) == Decimal(140000) and sum(t['back']) == Decimal(170000) and sum(t['anon']) == Decimal(20000)


def test_segments_rules():
    day = date(2026, 6, 30)
    seg = lambda n, ago: analytics.segment_of(n, day - timedelta(days=ago), day)  # noqa: E731
    assert seg(5, 10) == "Постоянные" and seg(2, 89) == "Растущие" and seg(1, 30) == "Новые"
    assert seg(1, 31) == "Разовые" and seg(1, 150) == "Разовые" and seg(3, 120) == "Под угрозой"
    assert seg(9, 181) == "Уснувшие" and seg(0, 1) is None


def test_clients_store_filter_and_past_period(tg):
    _setup()
    # за период 70–40 дней назад Анна — единственная, и по истории на конец периода у неё 2 покупки
    f = analytics.Filters(today() - timedelta(days=70), today() - timedelta(days=40))
    cl = analytics.build(f)['clients']
    assert cl['buyers'] == 1 and cl['top'][0]['segment'] == "Растущие" and cl['top'][0]['total_receipts'] == 2
    by_store = analytics.build(_period(store_id=2))['clients']                # A200 — во втором магазине
    assert by_store['buyers'] == 1 and by_store['top'][0]['revenue'] == Decimal(20000)


def test_page_excel_and_customer_card(tg):
    anna, _ = _setup()
    manager = _web(tg, MANAGER)
    page = manager.get("/analytics").text
    for text in ('id="clients"', "Топ клиентов", "Сегменты базы", "Что берут клиенты", "ch-clients", "Анна", "Постоянные"):
        assert text in page, text
    wb = openpyxl.load_workbook(io.BytesIO(manager.get("/analytics.xlsx").content))
    assert {"Клиенты за период", "Сегменты клиентов", "Что берут клиенты"} <= set(wb.sheetnames)
    rows = list(wb["Клиенты за период"].values)
    assert rows[1][:4] == ("Анна", "+77011112233", 170000, 2)
    assert len(list(wb["Сегменты клиентов"].values)) == 4                     # заголовок + 3 клиента
    cid = db_query("SELECT id FROM customers WHERE phone=%s", (anna,))[0]['id']
    pr = customers.get(cid)['profile']
    assert pr['receipts'] == 4 and pr['total'] == 340000 and pr['segment'] == "Постоянные"
    assert pr['types'] == [("кольцо", 2), ("цепь", 2)] and pr['interval'] == 67
    card = manager.get(f"/customers/{cid}").text
    assert "Чаще берёт: кольцо (2), цепь (2)" in card and "Средний чек" in card


def test_empty_base_hint(tg):
    page = _web(tg, MANAGER).get("/analytics").text
    assert "В базе пока нет клиентов" in page


def test_demo_customers_add_and_remove(tg):
    admin = _web(tg, ADMIN)
    csrf = _csrf(admin, "/settings")
    r = admin.post("/settings/demo", data={"action": "add", "csrf": csrf})
    assert "клиентов с историей покупок за год" in r.text
    counts = demo_data.demo_counts()
    total = sum(p[1] for p in demo_data.PERSONAS)
    assert counts['customers'] == total
    demo = db_query("SELECT * FROM customers WHERE consent_source='demo'")
    assert all(c['phone'].startswith("+7000000") and c['chat_id'] is None for c in demo)
    assert db_query("SELECT COUNT(*) AS n FROM receipts WHERE customer_id IS NOT NULL AND created_at < NOW() - INTERVAL 200 DAY")[0]['n']
    # у товаров дата поступления раньше первой продажи
    assert not db_query("""SELECT p.id FROM products p JOIN sales s ON s.product_id = p.id
                           WHERE p.arrival_date > DATE(s.sale_date)""")
    # у каждого демо-клиента есть покупка без возврата — иначе он выпадает из покупателей
    assert not db_query("""SELECT c.id FROM customers c WHERE c.consent_source='demo' AND NOT EXISTS (
                               SELECT 1 FROM receipts r JOIN sales s ON s.receipt_id = r.id
                               LEFT JOIN returns rt ON rt.original_sale_id = s.id
                               WHERE r.customer_id = c.id AND rt.id IS NULL)""")
    cl = analytics.build(analytics.Filters(today() - timedelta(days=364), today()))['clients']
    seg = {g['name']: g['count'] for g in cl['segments']}
    assert all(seg[name] for name in ("Постоянные", "Новые", "Уснувшие", "Под угрозой")), seg
    assert cl['buyers'] == total and cl['in_base'] == total
    assert customers.upcoming_birthdays(7)                                   # есть дни рождения на неделе
    page = admin.get("/settings").text
    assert "Добавить демо-клиентов" not in page and "+7 000 000" in page

    # демо удалили, а клиента сотрудник успел пробить на настоящий товар — чек остаётся без привязки
    real = _buy([(_pid("A100"), 150000)], _at(1, 12), demo[0]['phone'])
    r = admin.post("/settings/demo", data={"action": "remove", "csrf": csrf})
    assert f"{total} клиентов" in r.text
    assert not db_query("SELECT id FROM customers WHERE consent_source='demo'")
    assert db_query("SELECT customer_id FROM receipts WHERE id=%s", (real['receipt_id'],))[0]['customer_id'] is None


def test_demo_customers_for_existing_demo(tg):
    """Демо добавили до появления клиентов — клиентов можно добавить отдельной кнопкой."""
    admin = _web(tg, ADMIN)
    csrf = _csrf(admin, "/settings")
    assert "нужны демо-товары" in admin.post("/settings/demo", data={"action": "customers", "csrf": csrf}).text
    assert demo_data.seed_demo()[0]
    db_query("UPDATE receipts SET customer_id=NULL")
    db_query("DELETE FROM customers WHERE consent_source='demo'")
    page = admin.get("/settings").text
    assert "Добавить демо-клиентов с покупками" in page
    r = admin.post("/settings/demo", data={"action": "customers", "csrf": csrf})
    assert "Добавлены демо-клиенты" in r.text
    assert "уже добавлены" in admin.post("/settings/demo", data={"action": "customers", "csrf": csrf}).text
    assert demo_data.demo_counts()['customers'] == sum(p[1] for p in demo_data.PERSONAS)
