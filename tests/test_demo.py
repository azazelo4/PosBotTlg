"""Демо-данные: мастер настройки, добавление и удаление в «Настройках»."""
from conftest import ADMIN, MANAGER, db_query
from test_setup import fresh, _setup_form  # noqa: F401  (фикстура)
from test_stores import _web, _csrf

from datetime import timedelta

import demo_data
from database import record_sale
from utils.common import now


def _demo_products():
    return db_query("SELECT * FROM products WHERE article_number LIKE 'DEMO-%'")


def test_setup_wizard_adds_demo(fresh):  # noqa: F811
    r = _setup_form(fresh, demo="1")
    assert r.status_code == 200 and "Добавлены демо-данные" in r.text, r.text
    products = _demo_products()
    assert len(products) == 2 * len(demo_data.CATALOG)                 # каталог в обоих магазинах
    assert {p['store_id'] for p in products} == {s['id'] for s in db_query("SELECT id FROM stores")}
    assert all(p['quantity'] >= 1 and p['min_price'] <= p['price'] for p in products)
    sales = db_query("SELECT * FROM sales")
    assert len(sales) > 20 and all(s['sale_date'] <= now() for s in sales)
    assert db_query("SELECT COUNT(*) AS n FROM returns")[0]['n'] == 2
    assert db_query("SELECT COUNT(*) AS n FROM buybacks WHERE description LIKE 'Демо: %'")[0]['n'] == 6
    assert db_query("SELECT COUNT(*) AS n FROM cash_withdrawals")[0]['n'] > 0
    # остаток = поступило − продано + возвращено (продажи не уводят его в минус)
    # клиентские покупки старше двух недель — история, остаток они не трогают
    border = now() - timedelta(days=14)
    for p in products[:5]:
        sold = db_query("SELECT COUNT(*) AS n FROM sales WHERE product_id = %s AND sale_date >= %s",
                        (p['id'], border))[0]['n']
        returned = db_query("""SELECT COUNT(*) AS n FROM returns r JOIN sales s ON s.id = r.original_sale_id
                               WHERE s.product_id = %s""", (p['id'],))[0]['n']
        start = next(c[8] for c in demo_data.CATALOG if c[1] == p['model']) + 2
        assert p['quantity'] == start - sold + returned


def test_setup_without_demo_adds_nothing(fresh):  # noqa: F811
    assert _setup_form(fresh).status_code == 200
    assert not _demo_products() and not demo_data.has_demo()


def test_settings_add_and_remove_demo(tg):
    manager = _web(tg, MANAGER)
    assert manager.post("/settings/demo", data={"action": "add", "csrf": "x"}).status_code == 403

    admin = _web(tg, ADMIN)
    page = admin.get("/settings").text
    assert "Добавить демо-данные" in page
    csrf = _csrf(admin, "/settings")
    assert admin.post("/settings/demo", data={"action": "add", "csrf": "wrong"}).status_code == 400
    r = admin.post("/settings/demo", data={"action": "add", "csrf": csrf})
    assert "Добавлены демо-данные" in r.text and "Удалить демо-данные" in r.text
    assert "уже добавлены" in admin.post("/settings/demo", data={"action": "add", "csrf": csrf}).text
    assert "В системе демо-данные" in admin.get("/").text                # предупреждение на сводке

    # сотрудник потренировался: продал демо-товар; и настоящая продажа рядом
    demo = _demo_products()[0]
    admin_id = db_query("SELECT id FROM users WHERE role='admin'")[0]['id']
    assert record_sale(demo['id'], demo['article_number'], admin_id, demo['price'], "Kaspi")[0]
    real = db_query("SELECT * FROM products WHERE article_number='A100'")[0]
    assert record_sale(real['id'], 'A100', admin_id, real['price'], "Наличные")[0]
    db_query("INSERT INTO buybacks (description, metal_type, weight, buyback_price, payment_type, user_id, buyback_date) "
             "VALUES ('кольцо клиента', 'золото', 2, 50000, 'Наличные', %s, NOW())", (admin_id,))

    counts = demo_data.demo_counts()
    assert counts['products'] == 2 * len(demo_data.CATALOG) and counts['operations'] > 20
    r = admin.post("/settings/demo", data={"action": "remove", "csrf": csrf})
    assert "Демо-данные удалены" in r.text and "Добавить демо-данные" in r.text
    assert not _demo_products() and demo_data.demo_counts() == {"products": 0, "operations": 0, "customers": 0}
    # настоящее не тронуто
    assert [s['product_id'] for s in db_query("SELECT product_id FROM sales")] == [real['id']]
    assert db_query("SELECT description FROM buybacks")[0]['description'] == "кольцо клиента"
    assert db_query("SELECT COUNT(*) AS n FROM products")[0]['n'] == 3
    assert "В системе демо-данные" not in admin.get("/").text
    assert admin.post("/settings/demo", data={"action": "nope", "csrf": csrf}).status_code == 400
