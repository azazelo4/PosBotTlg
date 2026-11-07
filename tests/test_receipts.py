"""Чеки из нескольких позиций: касса на ПК, бот, отчёты, себестоимость."""
from conftest import CASHIER, MANAGER, login, db_query, reply_buttons
from test_kassa import client, kassa_login, post  # noqa: F401  (фикстура)
from test_stores import _web, _csrf

from database import record_receipt, get_receipt
from utils.common import now, today, day_bounds
from database import get_period_operations
from utils.report_generator import build_summary


def _add(article, store=1, qty=2, price=50000, min_price=45000):
    db_query("""INSERT INTO products (article_number, store_id, metal_type, product_type, model, weight, price_per_gram,
                price, min_price, arrival_date, quantity, track_stock) VALUES
             (%s, %s, 'серебро', 'серьги', 'Серьги Луна', 3, 1000, %s, %s, '2026-10-01', %s, 1)""",
             (article, store, price, min_price, qty))
    return db_query("SELECT id FROM products WHERE article_number=%s", (article,))[0]['id']


def _admin_id():
    return db_query("SELECT id FROM users WHERE role='admin'")[0]['id']


def test_receipt_rules_in_db(tg):
    a100 = db_query("SELECT id FROM products WHERE article_number='A100'")[0]['id']
    a200 = db_query("SELECT id FROM products WHERE article_number='A200'")[0]['id']
    b1 = _add("B1", qty=1)
    uid = _admin_id()
    assert record_receipt([], uid, "Kaspi") == (False, "Чек пуст — добавьте товар.")
    ok, msg = record_receipt([(b1, 50000), (b1, 50000)], uid, "Kaspi")
    assert not ok and "Недостаточно товара B1" in msg                      # одна штука — дважды не продать
    ok, msg = record_receipt([(a100, 150000), (a200, 20000)], uid, "Kaspi")
    assert not ok and "одного магазина" in msg
    ok, msg = record_receipt([(a100, 100000)], uid, "Kaspi")
    assert not ok and "ниже минимальной" in msg
    assert db_query("SELECT COUNT(*) AS n FROM sales")[0]['n'] == 0      # ничего не записалось

    ok, r = record_receipt([(a100, 150000), (a100, 140000), (b1, 50000)], uid, "Наличные")
    assert ok and r['total'] == 340000 and len(r['sale_ids']) == 3
    receipt = get_receipt(r['receipt_id'])
    assert (receipt['items_count'], receipt['total'], receipt['store']) == (3, 340000, "ЦУМ")
    assert len(receipt['public_token']) == 32
    assert [i['list_price'] for i in receipt['items']] == [150000, 150000, 50000]
    assert db_query("SELECT quantity FROM products WHERE id=%s", (a100,))[0]['quantity'] == 1
    assert db_query("SELECT quantity FROM products WHERE id=%s", (b1,))[0]['quantity'] == 0

    # средний чек — по чекам, а не по позициям
    s = build_summary(get_period_operations(*day_bounds(today(), today())))
    assert (s['sales_count'], s['receipts_count'], s['sales_avg']) == (3, 1, 340000)


def test_kassa_cart_sale(tg, client):  # noqa: F811
    _add("B2", qty=5)
    kassa_login(tg, client)
    r = post(client, "sale", {"items": [{"article": "A100", "price": 145000}, {"article": "B2", "price": 50000},
                                        {"article": "B2", "price": "48 000"}], "payment": "Kaspi"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"].startswith(f"Чек №{body['id']}: 3 поз., 243 000 ₸, Kaspi")
    assert db_query("SELECT COUNT(*) AS n FROM sales WHERE receipt_id=%s", (body['id'],))[0]['n'] == 3
    # одиночная продажа старым форматом — тоже чек
    r = post(client, "sale", {"article": "A100", "price": 150000, "payment": "Наличные"})
    assert r.status_code == 200 and "Кольцо Классика" in r.json()["text"]
    # ошибки: пустой чек, цена ниже минимальной, нет в наличии
    assert "Чек пуст" in post(client, "sale", {"items": [], "payment": "Kaspi"}).json()["error"]
    assert "минимальной" in post(client, "sale", {"items": [{"article": "B2", "price": 1000}], "payment": "Kaspi"}).json()["error"]
    assert "нет в наличии" in post(client, "sale", {"items": [{"article": "A300", "price": 90000}], "payment": "Kaspi"}).json()["error"]
    shift = client.get("/kassa/api/shift").json()
    assert (shift["sales_count"], shift["receipts_count"]) == (4, 2)
    sales = [o for o in shift["journal"] if o["kind"] == "Продажа"]
    assert len(sales) == 2 and any("3 поз." in o["what"] and o["amount"] == 243000 for o in sales)


def test_bot_cart_sale(tg):
    _add("B3", qty=1)
    login(tg, CASHIER)
    tg.send(CASHIER, "Продажа")
    tg.send(CASHIER, "A100")
    calls = tg.send(CASHIER, "150000")
    assert "➕ Ещё товар" in reply_buttons(calls[-1])
    assert "Введите артикул следующего" in tg.texts(tg.send(CASHIER, "➕ Ещё товар"))
    tg.send(CASHIER, "B3")
    tg.send(CASHIER, "47000")
    tg.send(CASHIER, "➕ Ещё товар")
    assert "больше нет" in tg.texts(tg.send(CASHIER, "B3"))               # единственная штука уже в чеке
    tg.send(CASHIER, "A100")
    calls = tg.send(CASHIER, "150000")
    assert "347 000 ₸" in tg.texts(calls) and "3 поз." in tg.texts(calls)
    calls = tg.send(CASHIER, "↩️ Убрать последний")
    assert "197 000 ₸" in tg.texts(calls)
    tg.send(CASHIER, "Безналичные")
    calls = tg.send(CASHIER, "Подтвердить")
    assert "чек №" in tg.texts(calls) and "2 поз., 197 000 ₸" in tg.texts(calls)
    rows = db_query("SELECT receipt_id, sold_price, list_price, payment_type FROM sales ORDER BY id")
    assert len({r['receipt_id'] for r in rows}) == 1 and [r['sold_price'] for r in rows] == [150000, 47000]
    assert rows[1]['list_price'] == 50000 and rows[0]['payment_type'] == "Безналичные"


def test_cost_price_in_form_and_import(tg):
    client = _web(tg, MANAGER)
    pid = db_query("SELECT id FROM products WHERE article_number='A100'")[0]['id']
    page = client.get(f"/products/{pid}")
    assert 'name="cost_price"' in page.text
    form = {"csrf": _csrf(client, f"/products/{pid}"), "article_number": "A100", "store_id": "1", "model": "Кольцо Классика",
            "product_type": "кольцо", "metal_type": "золото", "weight": "2.5", "price": "150000", "min_price": "120000",
            "cost_price": "110 000", "quantity": "3"}
    r = client.post(f"/products/{pid}", data=form)
    assert r.status_code in (200, 303), r.text
    assert db_query("SELECT cost_price FROM products WHERE id=%s", (pid,))[0]['cost_price'] == 110000

    from utils.catalog_import import build_plan, apply_plan
    csv = "Артикул;Тип;Металл;Цена;Себестоимость;Магазин\nA100;кольцо;золото;150000;;ЦУМ\nC1;серьги;серебро;80000;61000;ЦУМ\n".encode()
    plan = build_plan(csv, "catalog.csv")
    assert not plan.errors, plan.errors
    assert apply_plan(plan)[0]
    rows = {r['article_number']: r['cost_price'] for r in db_query("SELECT article_number, cost_price FROM products")}
    assert rows['C1'] == 61000 and rows['A100'] == 110000                # пустая ячейка не затирает
