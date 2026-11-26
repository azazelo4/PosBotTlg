"""Запрет продажи ниже минимальной цены — по товару; без запрета продажа дешевле проходит с отметкой."""
import io
import re

import openpyxl

from conftest import ADMIN, CASHIER, MANAGER, login, db_query
from core.database import record_receipt, create_trade_in_record
from test_stores import _web, _csrf, _sell
from utils.report_generator import generate_period_report
from utils.common import today


def _uid(role):
    return db_query("SELECT id FROM users WHERE role=%s", (role,))[0]['id']


def _pid(article):
    return db_query("SELECT id FROM products WHERE article_number=%s", (article,))[0]['id']


def test_default_no_lock_sale_below_min_is_marked(tg):
    assert db_query("SELECT min_price_lock FROM products WHERE article_number='A100'")[0]['min_price_lock'] == 0
    login(tg, CASHIER)
    _sell(tg, CASHIER, "A100", 100000)                                       # мин. 120 000 — продано молча
    sale = db_query("SELECT sold_price, below_min FROM sales")[0]
    assert int(sale['sold_price']) == 100000 and sale['below_min'] == 1
    text, excel, _ = generate_period_report(today(), today())
    assert "Ниже минимальной цены: 1 шт., в сумме на 20 000 ₸ дешевле минимума" in text
    wb = openpyxl.load_workbook(io.BytesIO(excel.getvalue()))
    row = list(wb["Продажи"].iter_rows(min_row=2, values_only=True))[0]
    assert row[-1] == "да (мин. 120000)"


def test_lock_blocks_cashier_not_manager(tg):
    db_query("UPDATE products SET min_price_lock=1 WHERE article_number='A100'")
    ok, msg = record_receipt([(_pid("A100"), 100000)], _uid('cashier'), "Наличные")
    assert not ok and "запрещена продажа ниже минимальной цены" in msg
    ok, r = record_receipt([(_pid("A100"), 100000)], _uid('manager'), "Наличные")   # менеджеру можно
    assert ok and db_query("SELECT below_min FROM sales")[0]['below_min'] == 1
    assert record_receipt([(_pid("A100"), 150000)], _uid('cashier'), "Наличные")[0]  # по цене — можно
    # Trade-In: то же правило
    ok, msg = create_trade_in_record("A100", 100000, 50000, 50000, "Наличные", _uid('cashier'), 1,
                                     old_item_description="кольцо", old_metal_type="золото", old_weight=2)
    assert not ok and "запрещена" in msg
    ok, _ = create_trade_in_record("A100", 100000, 50000, 50000, "Наличные", _uid('admin'), 1,
                                   old_item_description="кольцо", old_metal_type="золото", old_weight=2)
    assert ok and db_query("SELECT below_min FROM trade_ins")[0]['below_min'] == 1


def test_card_and_bulk_toggle(tg):
    client = _web(tg, MANAGER)
    pid = _pid("A100")
    card = client.get(f"/products/{pid}").text
    assert 'name="min_price_lock"' in card
    # массово: выбранные
    csrf = _csrf(client, "/products")
    r = client.post("/products/min-lock", data={"csrf": csrf, "op": "lock", "scope": "ids", "ids": f"{pid}"})
    assert "Запрет продажи ниже минимальной цены включён. Товаров: 1." in r.text
    assert db_query("SELECT min_price_lock FROM products WHERE id=%s", (pid,))[0]['min_price_lock'] == 1
    assert "🔒" in client.get("/products", params={"lock": "on"}).text
    # все по фильтру (магазин ЦУМ): A100 и A300
    r = client.post("/products/min-lock", data={"csrf": csrf, "op": "lock", "scope": "all", "store": "1"})
    locked = {x['article_number'] for x in db_query("SELECT article_number FROM products WHERE min_price_lock=1")}
    assert locked == {"A100", "A300"}
    r = client.post("/products/min-lock", data={"csrf": csrf, "op": "unlock", "scope": "all"})
    assert "снят. Товаров: 3." in r.text
    assert db_query("SELECT COUNT(*) AS n FROM products WHERE min_price_lock=1")[0]['n'] == 0
    assert client.post("/products/min-lock", data={"op": "lock", "scope": "all"}).status_code in (400, 403)
    # флажок в карточке сохраняется
    form = client.get(f"/products/{pid}").text
    fields = dict(re.findall(r'name="(\w+)" value="([^"]*)"', form))
    data = {k: fields.get(k, "") for k in ("article_number", "model", "price", "min_price", "weight", "quantity")}
    data.update(csrf=csrf, store_id="1", product_type="кольцо", metal_type="золото", min_price_lock="on")
    client.post(f"/products/{pid}", data=data)
    assert db_query("SELECT min_price_lock FROM products WHERE id=%s", (pid,))[0]['min_price_lock'] == 1
