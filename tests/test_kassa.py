"""Касса для ПК: вход по QR с подтверждением в боте и кассовые операции через веб."""
import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN, CASHIER, MANAGER, STRANGER, login, inline_buttons, db_query
from web import kassa
from web.app import app

H = {"X-Kassa": "1"}


@pytest.fixture
def client(tg, monkeypatch):
    monkeypatch.setattr(kassa, "bot_username", lambda: "pos_test_bot")
    db_query("DELETE FROM kassa_logins")
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def start_login(client):
    r = client.post("/kassa/api/login", json={}, headers={**H, "User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["link"].startswith("https://t.me/pos_test_bot?start=k_") and "<svg" in data["qr"]
    return data["link"].split("start=", 1)[1]


def kassa_login(tg, client, chat_id=CASHIER):
    login(tg, chat_id)
    payload = start_login(client)
    assert client.get("/kassa/api/login").json() == {"status": "pending"}
    calls = tg.send(chat_id, f"/start {payload}")
    text = tg.texts(calls)
    assert "Вход на кассу ПК" in text and "Windows · Chrome" in text, text
    buttons = inline_buttons(calls)
    calls = tg.press(chat_id, buttons["✅ Войти"])
    assert "Касса на компьютере открыта" in tg.texts(calls)
    assert client.get("/kassa/api/login").json() == {"status": "approved"}
    return payload


def post(client, path, body):
    return client.post("/kassa/api/" + path, json=body, headers=H)


# ---------------------------------------------------------------- вход

def test_page_and_api_need_login(tg, client):
    page = client.get("/kassa")
    assert page.status_code == 200 and "Вход на кассу" in page.text
    r = client.get("/kassa/api/me")
    assert r.status_code == 401 and "QR" in r.json()["error"]
    assert post(client, "sale", {"article": "A100", "price": 150000, "payment": "Kaspi"}).status_code == 401


def test_qr_login_flow(tg, client):
    payload = kassa_login(tg, client)
    me = client.get("/kassa/api/me").json()
    assert me["name"] == "Кассир" and me["fixed_store"] and me["store_id"] == 1
    # одноразовый: повторный скан того же QR не проходит
    assert "устарел или уже использован" in tg.texts(tg.send(CASHIER, f"/start {payload}"))
    # сессия бота не сброшена командой /start k_…
    assert tg.texts(tg.send(CASHIER, "Касса"))
    # выход
    assert post(client, "logout", {}).status_code == 200
    assert client.get("/kassa/api/me").status_code == 401


def test_login_denied_and_requires_bot_login(tg, client):
    payload = start_login(client)
    # не вошёл в бота (или посторонний): подтвердить нельзя
    assert "сначала войдите в бота" in tg.texts(tg.send(STRANGER, f"/start {payload}"))
    login(tg, CASHIER)
    calls = tg.send(CASHIER, f"/start {payload}")
    tg.press(CASHIER, inline_buttons(calls)["❌ Это не я"])
    assert client.get("/kassa/api/login").json() == {"status": "denied"}
    assert client.get("/kassa/api/me").status_code == 401


def test_login_only_for_browser_that_showed_qr(tg, client):
    payload = start_login(client)
    login(tg, CASHIER)
    tg.press(CASHIER, inline_buttons(tg.send(CASHIER, f"/start {payload}"))["✅ Войти"])
    with TestClient(app, base_url="https://testserver") as other:
        assert other.get("/kassa/api/login").json() == {"status": "expired"}
        assert other.get("/kassa/api/me").status_code == 401
    assert client.get("/kassa/api/login").json() == {"status": "approved"}


def test_expired_qr(tg, client):
    payload = start_login(client)
    db_query("UPDATE kassa_logins SET expires_at = expires_at - INTERVAL 1 HOUR")
    login(tg, CASHIER)
    assert "устарел" in tg.texts(tg.send(CASHIER, f"/start {payload}"))
    assert client.get("/kassa/api/login").json() == {"status": "expired"}


def test_deactivated_user_loses_kassa(tg, client):
    kassa_login(tg, client)
    assert client.get("/kassa/api/me").status_code == 200
    db_query("UPDATE users SET is_active = 0 WHERE phone_number = '+77010000002'")
    assert client.get("/kassa/api/me").status_code == 401


def test_post_requires_kassa_header(tg, client):
    kassa_login(tg, client)
    r = client.post("/kassa/api/sale", json={"article": "A100", "price": 150000, "payment": "Kaspi"})
    assert r.status_code == 400
    assert not db_query("SELECT * FROM sales")


# ---------------------------------------------------------------- операции

def test_sale(tg, client):
    kassa_login(tg, client)
    assert client.get("/kassa/api/product", params={"article": "A100"}).json()["price"] == 150000
    r = post(client, "sale", {"article": "A100", "price": "100 000", "payment": "Kaspi"})
    assert r.status_code == 400 and "ниже минимальной" in r.json()["error"]
    assert post(client, "sale", {"article": "A100", "price": 140000, "payment": "Bitcoin"}).status_code == 400
    assert post(client, "sale", {"article": "A300", "price": 90000, "payment": "Kaspi"}).json()["error"] == "Товара A300 нет в наличии."
    r = post(client, "sale", {"article": "A100", "price": "140 000", "payment": "Kaspi"})
    assert r.status_code == 200 and "140 000 ₸" in r.json()["text"]
    sale = db_query("SELECT s.*, u.name FROM sales s JOIN users u ON u.id = s.user_id")[0]
    assert sale['sold_price'] == 140000 and sale['name'] == "Кассир" and sale['store_id'] == 1
    assert db_query("SELECT quantity FROM products WHERE article_number='A100'")[0]['quantity'] == 2


def test_return(tg, client):
    kassa_login(tg, client)
    post(client, "sale", {"article": "A100", "price": 150000, "payment": "Наличные"})
    sales = client.get("/kassa/api/sales-for-return", params={"article": "A100"}).json()
    assert len(sales) == 1 and not sales[0]["returned"]
    sale_id = sales[0]["sale_id"]
    assert post(client, "return", {"sale_id": sale_id, "price": 160000, "payment": "Наличные"}).status_code == 400
    r = post(client, "return", {"sale_id": sale_id, "price": 150000, "payment": "Наличные"})
    assert r.status_code == 200, r.text
    assert client.get("/kassa/api/sales-for-return", params={"article": "A100"}).json()[0]["returned"]
    assert post(client, "return", {"sale_id": sale_id, "price": 150000, "payment": "Наличные"}).status_code == 400
    assert db_query("SELECT quantity FROM products WHERE article_number='A100'")[0]['quantity'] == 3


def test_buyback_our_and_foreign(tg, client):
    kassa_login(tg, client)
    r = post(client, "buyback", {"item": {"article": "A200"}, "price": 10000, "payment": "Наличные"})
    assert r.status_code == 200, r.text
    assert db_query("SELECT quantity FROM products WHERE article_number='A200'")[0]['quantity'] == 2
    assert post(client, "buyback", {"item": {"metal": "платина", "weight": 3}, "price": 5000, "payment": "Наличные"}).status_code == 400
    assert post(client, "buyback", {"item": {"metal": "золото", "weight": "abc"}, "price": 5000, "payment": "Наличные"}).status_code == 400
    r = post(client, "buyback", {"item": {"metal": "золото", "weight": "3,45", "description": "кольцо 585"},
                                 "price": 70000, "payment": "Kaspi"})
    assert r.status_code == 200, r.text
    rows = db_query("SELECT * FROM buybacks ORDER BY id")
    assert rows[0]['store_id'] == 2  # своё изделие — магазин товара
    assert rows[1]['store_id'] == 1 and float(rows[1]['weight']) == 3.45 and rows[1]['description'] == "кольцо 585"


def test_buyback_foreign_needs_store_for_unattached_admin(tg, client):
    kassa_login(tg, client, ADMIN)  # админ не закреплён за магазином, магазинов два
    me = client.get("/kassa/api/me").json()
    assert not me["fixed_store"] and me["store_id"] is None and len(me["stores"]) == 2
    body = {"item": {"metal": "серебро", "weight": 10}, "price": 3000, "payment": "Наличные"}
    r = post(client, "buyback", body)
    assert r.status_code == 400 and "магазин" in r.json()["error"]
    assert post(client, "store", {"store_id": 999}).status_code == 400
    assert post(client, "store", {"store_id": 2}).status_code == 200
    assert post(client, "buyback", body).status_code == 200
    assert db_query("SELECT store_id FROM buybacks")[0]['store_id'] == 2
    assert client.get("/kassa/api/shift").json()["store"] == "Отау Сауда"


def test_cashier_cannot_change_fixed_store(tg, client):
    kassa_login(tg, client)
    assert post(client, "store", {"store_id": 2}).status_code == 400


def test_tradein(tg, client):
    kassa_login(tg, client, MANAGER)
    body = {"item": {"metal": "золото", "weight": 2}, "old_value": 60000, "new_article": "A100",
            "new_price": 150000, "payment": None}
    r = post(client, "tradein", body)
    assert r.status_code == 400 and "способ оплаты" in r.json()["error"]
    assert post(client, "tradein", {**body, "new_article": "A300", "payment": "Kaspi"}).status_code == 400
    assert post(client, "tradein", {**body, "item": {"article": "A100"}, "payment": "Kaspi"}).status_code == 400
    r = post(client, "tradein", {**body, "payment": "Kaspi"})
    assert r.status_code == 200 and "доплата клиента 90 000 ₸" in r.json()["text"]
    t = db_query("SELECT * FROM trade_ins")[0]
    assert t['price_difference'] == 90000 and t['payment_type_difference'] == "Kaspi"
    # без доплаты способ оплаты не нужен
    r = post(client, "tradein", {**body, "old_value": 150000, "new_article": "A100"})
    assert r.status_code == 200 and "без доплаты" in r.json()["text"]


def test_withdrawal_and_shift(tg, client):
    kassa_login(tg, client)
    post(client, "sale", {"article": "A100", "price": 150000, "payment": "Наличные"})
    post(client, "sale", {"article": "A100", "price": 140000, "payment": "Kaspi"})
    assert post(client, "withdrawal", {"amount": 0}).status_code == 400
    r = post(client, "withdrawal", {"amount": "50 000", "comment": "в банк"})
    assert r.status_code == 200, r.text
    s = client.get("/kassa/api/shift").json()
    assert s["store"] == "ЦУМ" and s["sales_count"] == 2 and s["sales_total"] == 290000
    assert s["cash_balance"] == 100000 and s["withdrawals_total"] == 50000
    assert [o["kind"] for o in s["journal"]].count("Продажа") == 2
    assert any(o["kind"] == "Инкассация" and o["amount"] == -50000 and o["what"] == "в банк" for o in s["journal"])


def test_scanned_product_code_from_label(tg, client):
    """Длинный артикул на ценнике заменяется штрихкодом «#<id>» — касса находит по нему товар."""
    kassa_login(tg, client)
    product_id = db_query("SELECT id FROM products WHERE article_number='A100'")[0]['id']
    p = client.get("/kassa/api/product", params={"article": f"#{product_id}"}).json()
    assert p["article"] == "A100"
    r = post(client, "sale", {"article": f"#{product_id}", "price": 150000, "payment": "Kaspi"})
    assert r.status_code == 200, r.text
    sales = client.get("/kassa/api/sales-for-return", params={"article": f"#{product_id}"}).json()
    assert len(sales) == 1 and sales[0]["article"] == "A100"
    assert client.get("/kassa/api/product", params={"article": "#999999"}).status_code == 404


def test_cashier_sells_only_own_store(tg, client):
    """Кассир ЦУМ: товар Отау Сауда не находится для продажи и не продаётся (проверка и в записи чека)."""
    kassa_login(tg, client)
    r = client.get("/kassa/api/product", params={"article": "A200", "sale": 1})
    assert r.status_code == 400 and "Отау Сауда" in r.json()["error"]
    assert client.get("/kassa/api/product", params={"article": "A200"}).status_code == 200   # просто посмотреть можно
    r = post(client, "sale", {"items": [{"article": "A200", "price": 20000}], "payment": "Наличные"})
    assert r.status_code == 400 and "Отау Сауда" in r.text
    assert db_query("SELECT COUNT(*) AS n FROM sales")[0]['n'] == 0
