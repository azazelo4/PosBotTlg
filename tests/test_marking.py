"""Маркировка: коды DataMatrix — приёмка, продажа, возврат, Trade-In, списание, сверка."""
import itertools

import pytest
from fastapi.testclient import TestClient

from core import config
from services import marking
from conftest import CASHIER, MANAGER, login, db_query
from core.database import record_receipt, create_return_record, create_trade_in_record
from test_kassa import client, kassa_login, post  # noqa: F401  (фикстура)
from test_stores import _web, _csrf
from test_web import init_data
from utils import mark_codes
from web.app import app

_serial = itertools.count(1)


def code(gtin="04601234567890"):
    """Код как у маркированных товаров: 01 GTIN 21 серийный <GS> 93 крипто-хвост."""
    return f"01{gtin}21SN{next(_serial):011d}\x1d93Ab+/"


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(config, "MARKING_ENABLED", True)


def _pid(article):
    return db_query("SELECT id FROM products WHERE article_number=%s", (article,))[0]['id']


def _admin():
    return db_query("SELECT id FROM users WHERE role='admin'")[0]['id']


def _qty(article):
    return db_query("SELECT quantity FROM products WHERE article_number=%s", (article,))[0]['quantity']


def _status(raw):
    return db_query("SELECT status FROM product_codes WHERE code_key=%s", (mark_codes.key(raw),))[0]['status']


def test_code_normalization():
    raw = "0104601234567890215abcdefghijkl\x1d93Ab+/"
    assert mark_codes.key("]d2" + raw) == mark_codes.key(raw.replace("\x1d", "")) == mark_codes.key(raw.replace("\x1d", "<GS>"))
    assert mark_codes.clean("]d2" + raw.replace("\x1d", "<GS>")) == raw
    assert mark_codes.looks_like_code(raw) and not mark_codes.looks_like_code("A100")
    assert not mark_codes.is_valid("кириллица-кириллица-кириллица")
    assert mark_codes.short(raw) == "04601234567890 · 5abcdefghijkl"
    assert mark_codes.short(raw.replace("\x1d", "")) == "04601234567890 · 5abcdefghijkl"   # без GS — 13 знаков
    assert "…" in mark_codes.short("X" * 40)
    assert mark_codes.split_codes(" a \n\nb\tc ") == ["a", "b", "c"]


def test_accept_codes(tg):
    c1, c2, c3 = code(), code(), code()
    ok, r = marking.accept_codes(_pid("A100"), [c1, c2, c2, "мусор"], _admin())
    assert ok and r['added'] == 2 and r['invalid'] == ["мусор"]
    assert _qty("A100") == 5                                               # 3 + 2 поступило
    assert db_query("SELECT is_marked FROM products WHERE article_number='A100'")[0]['is_marked'] == 1
    ok, r = marking.accept_codes(_pid("A100"), [c1, c3], _admin(), add_to_stock=False)
    assert r['added'] == 1 and len(r['duplicates']) == 1 and _qty("A100") == 5
    ok, r = marking.accept_codes(_pid("A200"), [c1.replace("\x1d", "")], _admin())   # тот же код без GS
    assert r['added'] == 0 and "A100" in r['foreign'][0]
    stats = {p['article_number']: p for p in marking.marked_products()}
    assert stats['A100']['in_stock'] == 3 and stats['A100']['without_code'] == 2


def test_sale_return_and_tradein_by_code(tg, enabled):
    c1, c2, other = code(), code(), code("04600000000001")
    marking.accept_codes(_pid("A100"), [c1, c2], _admin())
    marking.accept_codes(_pid("A200"), [other], _admin(), add_to_stock=False)
    uid, a100 = _admin(), _pid("A100")
    ok, msg = record_receipt([(a100, 150000)], uid, "Kaspi")
    assert not ok and "отсканируйте код маркировки" in msg                 # без кода нельзя
    ok, msg = record_receipt([(a100, 150000, other)], uid, "Kaspi")
    assert not ok and "другому товару" in msg
    ok, msg = record_receipt([(a100, 150000, c1), (a100, 150000, c1.replace("\x1d", ""))], uid, "Kaspi")
    assert not ok and "уже есть в этом чеке" in msg
    ok, r = record_receipt([(a100, 150000, c1)], uid, "Kaspi")
    assert ok and _status(c1) == "sold"
    sale = db_query("SELECT * FROM sales WHERE id=%s", (r['sale_ids'][0],))[0]
    assert sale['mark_code_id'] == db_query("SELECT id FROM product_codes WHERE code_key=%s", (mark_codes.key(c1),))[0]['id']
    ok, msg = record_receipt([(a100, 150000, c1)], uid, "Kaspi")
    assert not ok and "уже продан" in msg
    # возврат возвращает код в оборот — его можно продать снова
    assert create_return_record(sale['id'], "A100", 150000, "Kaspi", uid, 1)[0]
    assert _status(c1) == "returned"
    assert record_receipt([(a100, 150000, c1)], uid, "Наличные")[0] and _status(c1) == "sold"
    # Trade-In маркируемого товара — тоже по коду
    kw = dict(new_product_article="A100", new_sale_price=150000, old_item_value=50000, price_difference=100000,
              payment_type_difference="Kaspi", user_id=uid, store_id=1, old_metal_type="золото", old_weight=2)
    ok, msg = create_trade_in_record(**kw)
    assert not ok and "отсканируйте код" in msg
    ok, tid = create_trade_in_record(**kw, new_mark_code=c2)
    assert ok and _status(c2) == "sold"
    assert db_query("SELECT note FROM product_codes WHERE code_key=%s", (mark_codes.key(c2),))[0]['note'] == f"Trade-In №{tid}"


def test_marking_off_keeps_old_behaviour(tg):
    c1 = code()
    marking.accept_codes(_pid("A100"), [c1], _admin())
    assert record_receipt([(_pid("A100"), 150000)], _admin(), "Kaspi")[0]   # учёт выключен — продаётся как раньше
    assert _status(c1) == "in_stock"


def test_kassa_pc_cart_with_codes(tg, client, enabled):  # noqa: F811
    c1, c2 = code(), code()
    marking.accept_codes(_pid("A100"), [c1, c2], _admin())
    kassa_login(tg, client)
    p = client.get("/kassa/api/product", params={"article": "A100", "sale": 1}).json()
    assert p["marked"] is True and "code" not in p
    p = client.get("/kassa/api/product", params={"article": "]d2" + c1, "sale": 1}).json()
    assert p["article"] == "A100" and mark_codes.key(p["code"]) == mark_codes.key(c1)
    r = post(client, "sale", {"items": [{"article": "A100", "price": 150000}], "payment": "Kaspi"})
    assert "отсканируйте код" in r.json()["error"]
    r = post(client, "sale", {"items": [{"article": "A100", "price": 150000, "code": c1},
                                        {"article": "A100", "price": 145000, "code": c2}], "payment": "Kaspi"})
    assert r.status_code == 200, r.text
    r = client.get("/kassa/api/product", params={"article": c1, "sale": 1})
    assert r.status_code == 400 and "уже продан" in r.json()["error"]
    unknown = code("04699999999999")
    assert "не найден" in client.get("/kassa/api/product", params={"article": unknown}).json()["error"]


def test_bot_sale_with_code(tg, enabled):
    c1 = code()
    marking.accept_codes(_pid("A100"), [c1], _admin())
    login(tg, CASHIER)
    tg.send(CASHIER, "Продажа")
    assert "маркируемый товар" in tg.texts(tg.send(CASHIER, "A100"))
    calls = tg.send(CASHIER, c1.replace("\x1d", ""))                      # код из приложения-сканера, без GS
    assert "150 000 ₸" in tg.texts(calls)
    tg.send(CASHIER, "150000")
    tg.send(CASHIER, "Kaspi")
    assert "чек №" in tg.texts(tg.send(CASHIER, "Подтвердить"))
    assert _status(c1) == "sold"
    tg.send(CASHIER, "Продажа")
    assert "уже продан" in tg.texts(tg.send(CASHIER, c1))


def test_mini_app_refuses_marked_product(tg, enabled):
    marking.accept_codes(_pid("A100"), [code()], _admin())
    login(tg, CASHIER)
    headers = {"X-Telegram-Init-Data": init_data(CASHIER)}
    c = TestClient(app, base_url="https://testserver")
    r = c.get("/api/app/product", params={"article": "A100"}, headers=headers)
    assert r.status_code == 400 and "кассе ПК" in r.json()["error"]
    r = c.post("/api/app/sale", json={"article": "A100", "price": 150000, "payment": "Kaspi"}, headers=headers)
    assert r.status_code == 400


def test_admin_accept_write_off_reconcile(tg, enabled):
    web = _web(tg, MANAGER)
    assert "Приёмка кодов" in web.get("/marking").text
    csrf = _csrf(web, "/marking")
    c1, c2, c3 = code(), code(), code()
    r = web.post("/marking/accept", data={"csrf": csrf, "article": "A100", "codes": f"{c1}\n{c2}\n\n{c3}\nмусор",
                                          "add_to_stock": "on"})
    assert r.status_code == 200 and "Принято кодов: 3" in r.text and "Не похожи" in r.text
    assert _qty("A100") == 6
    assert "не найден" in web.post("/marking/accept", data={"csrf": csrf, "article": "NOPE", "codes": c1}).text
    page = web.get(f"/marking/product/{_pid('A100')}").text
    code_id = db_query("SELECT id FROM product_codes WHERE code_key=%s", (mark_codes.key(c3),))[0]['id']
    assert "Списать" in page
    assert "причину" in web.post("/marking/write-off", data={"csrf": csrf, "code_id": code_id, "reason": ""}).text
    r = web.post("/marking/write-off", data={"csrf": csrf, "code_id": code_id, "reason": "утеря"})
    assert "Код списан" in r.text and _status(c3) == "written_off" and _qty("A100") == 5
    # продали c2, на витрине нашли c1, c2 (уже продан) и чужой код; c1 есть, c3 списан — не ищем
    record_receipt([(_pid("A100"), 150000, c2)], _admin(), "Kaspi")
    c4 = code()
    marking.accept_codes(_pid("A100"), [c4], _admin())
    r = web.post("/marking/reconcile", data={"csrf": csrf, "store": "1", "codes": f"{c1}\n{c2}\n{code('04688888888888')}"})
    assert r.status_code == 200
    result = marking.reconcile([c1, c2, code("04688888888888")], 1)
    assert len(result['ok']) == 1 and len(result['not_sellable']) == 1 and len(result['unknown']) == 1
    assert [m['short'] for m in result['missing']] == [mark_codes.short(c4)]
    assert "Нет на витрине" in r.text and "Уже проданы" in r.text


def test_bot_foreign_store_code_rejected_at_scan(tg, enabled):
    """Ревью PR #53: код маркировки товара другого магазина отклоняется сразу при скане, а не на «Подтвердить»."""
    c = code()
    marking.accept_codes(_pid("A200"), [c], _admin(), add_to_stock=False)       # A200 — магазин Отау Сауда
    login(tg, CASHIER)                                                         # кассир ЦУМ
    tg.send(CASHIER, "Продажа")
    text = tg.texts(tg.send(CASHIER, c))
    assert "числится в магазине «Отау Сауда»" in text and "Отсканируйте другой код" in text
    assert _status(c) == 'in_stock'
