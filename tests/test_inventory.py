"""Штрихкод старого ценника и инвентаризация: скан → итог → применение остатков."""
import io
import re

import openpyxl
from fastapi.testclient import TestClient

from conftest import ADMIN, MANAGER, CASHIER, login, db_query
from core.database import get_product_by_article, record_receipt
from services import inventory
from test_stores import _web, _csrf
from test_web import init_data
from web.app import app

OLD = "2004788000007"           # штрихкод ценника Паломы


def _pid(article):
    return db_query("SELECT id FROM products WHERE article_number=%s", (article,))[0]['id']


def _uid(role):
    return db_query("SELECT id FROM users WHERE role=%s", (role,))[0]['id']


def _xlsx(rows):
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- штрихкод

def test_barcode_lookup_everywhere(tg):
    db_query("UPDATE products SET barcode=%s WHERE article_number='A100'", (OLD,))
    assert get_product_by_article(OLD)['article_number'] == "A100"
    # бот: продажа по штрихкоду
    login(tg, CASHIER)
    tg.send(CASHIER, "Продажа")
    assert "Кольцо Классика" in tg.texts(tg.send(CASHIER, OLD))
    tg.send(CASHIER, "Отмена")
    # «📷 Касса» и касса ПК
    client = TestClient(app, base_url="https://testserver")
    r = client.get("/api/app/product", params={"article": OLD}, headers={"X-Telegram-Init-Data": init_data(CASHIER)})
    assert r.json()["article"] == "A100"
    # возврат: продажу находит и по штрихкоду
    ok, sale = record_receipt([(_pid("A100"), 150000)], _uid('cashier'), "Наличные")
    tg.send(CASHIER, "Возврат")
    assert "Введите сумму возврата" in tg.texts(tg.send(CASHIER, OLD))


def test_barcode_unique_in_form_and_import(tg):
    db_query("UPDATE products SET barcode=%s WHERE article_number='A100'", (OLD,))
    manager = _web(tg, MANAGER)
    csrf = _csrf(manager, "/import")
    data = _xlsx([["Артикул", "Наименование", "Тип", "Металл", "Цена", "Магазин", "Штрихкод"],
                  ["P1", "Кольцо", "кольцо", "золото", 1000, "ЦУМ", 2004789000006],      # число из Excel
                  ["P2", "Серьги", "серьги", "золото", 2000, "ЦУМ", "2004789000006"],    # повтор в файле
                  ["P3", "Цепь", "цепь", "золото", 3000, "ЦУМ", OLD],                      # занят A100
                  ["P4", "Брошь", "брошь", "золото", 4000, "ЦУМ", "A200"],                 # артикул в базе
                  ["P5", "Бусы", "бусы", "золото", 5000, "ЦУМ", "P1"]])                    # артикул в файле
    page = manager.post("/import", data={"csrf": csrf, "store": ""},
                        files={"file": ("c.xlsx", data, "application/octet-stream")}).text
    assert "Штрихкод повторяется" in page
    plan_id = re.search(r'action="/import/([^"]+)"', page).group(1)
    manager.post(f"/import/{plan_id}", data={"csrf": csrf, "mode": "all"})
    codes = {r['article_number']: r['barcode'] for r in db_query("SELECT article_number, barcode FROM products")}
    assert codes["P1"] == "2004789000006" and codes["P2"] is None and codes["P3"] is None and codes["A100"] == OLD
    assert codes["P4"] is None and codes["P5"] is None
    assert get_product_by_article("2004789000006")['article_number'] == "P1"
    # в карточке: штрихкод, совпадающий с чужим артикулом, не сохраняется
    from core.database import update_product
    pid = db_query("SELECT id FROM products WHERE article_number='P2'")[0]['id']
    ok, msg = update_product(pid, {'barcode': 'A200'})
    assert not ok and "артикулом" in msg


# ---------------------------------------------------------------- инвентаризация

def test_inventory_scan_summary_apply(tg):
    db_query("UPDATE products SET barcode=%s WHERE article_number='A300'", (OLD,))
    db_query("UPDATE products SET quantity=0 WHERE article_number='A300'")       # не оприходован в Паломе
    admin = _uid('admin')
    ok, inv = inventory.start(1, admin)                                          # ЦУМ
    assert ok and inventory.start(1, admin) == (True, inv)                       # вторая — та же
    s = lambda code: inventory.scan(inv, code, admin)[1]                         # noqa: E731
    assert s(OLD)['status'] == 'found' and s(OLD)['status'] == 'again'           # A300 по старому штрихкоду
    moved = s("A200")                                                            # числится в Отау Сауда
    assert moved['status'] == 'found' and moved['other_store'] and moved['item']['store'] == "Отау Сауда"
    unknown = s("4600000000000")
    assert unknown['status'] == 'unknown' and unknown['found'] == 2 and unknown['unknown'] == 1
    assert inventory.unscan(inv, unknown['scan_id'])[1]['unknown'] == 0
    s("4600000000001")
    summary = inventory.summary(inv)
    assert summary['found'] == 2 and len(summary['moved']) == 1 and summary['restocked'] == 1    # A200 и так был с остатком
    assert [r['article_number'] for r in summary['missing']] == ["A100"]          # в ЦУМ, остаток 3, не найден
    assert [u['code'] for u in summary['unknown']] == ["4600000000001"]
    ok, result = inventory.apply(inv, admin)
    assert ok and result == {"found": 2, "moved": 1, "zeroed": 1, "unknown": 1}
    rows = {r['article_number']: r for r in db_query("SELECT article_number, quantity, store_id FROM products")}
    assert (rows['A300']['quantity'], rows['A300']['store_id']) == (1, 1)
    assert (rows['A200']['quantity'], rows['A200']['store_id']) == (1, 1)         # перенесён в ЦУМ
    assert rows['A100']['quantity'] == 0
    assert not inventory.scan(inv, "A100", admin)[0]                              # завершена
    assert not inventory.apply(inv, admin)[0]


def test_inventory_sold_after_scan_and_keep_missing(tg):
    admin = _uid('admin')
    ok, inv = inventory.start(1, admin)
    inventory.scan(inv, "A100", admin)
    assert record_receipt([(_pid("A100"), 150000)], admin, "Наличные")[0]       # продали уже после скана
    assert [r['article_number'] for r in inventory.summary(inv)['sold_after']] == ["A100"]
    ok, result = inventory.apply(inv, admin, zero_missing=False)
    assert ok and result['zeroed'] == 0
    assert db_query("SELECT quantity FROM products WHERE article_number='A100'")[0]['quantity'] == 0
    assert not inventory.apply(inventory.start(2, admin)[1], admin)[0]          # пустую применить нельзя


def test_inventory_web_flow_and_access(tg):
    manager = _web(tg, MANAGER)                                                  # менеджер ЦУМ
    page = manager.get("/inventory").text
    assert "Начать или продолжить" in page and "Отау Сауда" not in page          # только свой магазин
    csrf = _csrf(manager, "/inventory")
    r = manager.post("/inventory", data={"csrf": csrf, "store_id": "1"})
    inv = int(re.search(r"/inventory/(\d+)", str(r.url)).group(1))
    assert "Сканируйте изделия подряд" in r.text and "zxing.min.js" in r.text
    h = {"X-CSRF": csrf}
    assert manager.post(f"/api/inventory/{inv}/scan", json={"code": "A100"}).status_code == 400   # без CSRF
    d = manager.post(f"/api/inventory/{inv}/scan", json={"code": "A100"}, headers=h).json()
    assert manager.post(f"/api/inventory/{inv}/scan", json=["A100"], headers=h).status_code == 400  # не объект
    assert d["status"] == "found" and d["found"] == 1 and d["scan_id"]
    summary = manager.get(f"/inventory/{inv}/summary").text
    assert "Найдено изделий" in summary and "Применить" in summary
    r = manager.post(f"/inventory/{inv}/apply", data={"csrf": csrf, "zero_missing": "on"})
    assert "Инвентаризация применена: в наличии 1" in r.text
    # чужой магазин: ни начать, ни открыть
    other = inventory.start(2, _uid('admin'))[1]
    assert manager.get(f"/inventory/{other}").status_code == 403
    assert "Выберите магазин" in manager.post("/inventory", data={"csrf": csrf, "store_id": "2"}).text
    admin = _web(tg, ADMIN)
    assert admin.get(f"/inventory/{other}").status_code == 200
    r = admin.post(f"/inventory/{other}/cancel", data={"csrf": _csrf(admin, '/inventory')})
    assert "отменена" in r.text
