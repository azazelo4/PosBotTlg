"""Веб-админка и Mini App: вход по ссылке из бота, CSRF, страницы, API кассы с подписью Telegram."""
import hashlib
import hmac
import io
import json
import re
import time
from urllib.parse import urlencode

import openpyxl
import pytest
from telebot import types
from fastapi.testclient import TestClient
from pypdf import PdfReader

from conftest import ADMIN, MANAGER, CASHIER, login, inline_buttons, db_query
from web.app import app
from web.security import issue_login_link

TOKEN = "123456:TEST"


@pytest.fixture
def client(tg):
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def web_login(tg, client, chat_id):
    login(tg, chat_id)
    calls = tg.send(chat_id, "Веб-админка")
    link = next(b.url for c in calls if c.markup and hasattr(c.markup, 'keyboard') for row in c.markup.keyboard for b in row
                if getattr(b, 'url', None))
    r = client.get(link.replace("https://pos.test", ""), follow_redirects=False)
    assert r.status_code == 303, r.text
    return link


def csrf(client, path):
    html = client.get(path).text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def init_data(user_id, token=TOKEN, auth_date=None, tamper=False):
    fields = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAH",
              "user": json.dumps({"id": user_id, "first_name": "T"}, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        fields["user"] = fields["user"].replace(str(user_id), str(user_id + 1))
    return urlencode(fields)


# ---------------------------------------------------------------- вход

def test_login_link_is_one_time_and_role_restricted(tg, client):
    assert client.get("/").status_code == 401
    link = web_login(tg, client, ADMIN)
    page = client.get("/")
    assert page.status_code == 200 and "Сводка" in page.text and "Персонал" in page.text
    # повторно ссылка не работает
    with TestClient(app, base_url="https://testserver") as other:
        assert other.get(link.replace("https://pos.test", "")).status_code == 401
    # кассиру кнопки нет, а ссылка, выданная напрямую, не пускает
    login(tg, CASHIER)
    assert "Не понял" in tg.texts(tg.send(CASHIER, "Веб-админка"))
    cashier_id = db_query("SELECT id FROM users WHERE role='cashier'")[0]['id']
    with TestClient(app, base_url="https://testserver") as other:
        r = other.get(issue_login_link(cashier_id).replace("https://pos.test", ""))
        assert r.status_code == 403


def test_logout_and_deactivated_user_loses_session(tg, client):
    web_login(tg, client, MANAGER)
    assert client.get("/products").status_code == 200
    db_query("UPDATE users SET is_active = 0 WHERE role = 'manager'")
    assert client.get("/products").status_code == 401


def test_csrf_required(tg, client):
    web_login(tg, client, ADMIN)
    r = client.post("/products/new", data={"article_number": "X1"})
    assert r.status_code == 400 and "Форма устарела" in r.text


# ---------------------------------------------------------------- товары

def test_products_list_create_edit(tg, client):
    web_login(tg, client, MANAGER)
    page = client.get("/products?q=A1")
    assert "A100" in page.text and "A200" not in page.text
    token = csrf(client, "/products/new")
    form = {"csrf": token, "article_number": "W1", "store_id": "1", "model": "Кольцо Веб", "product_type": "кольцо",
            "metal_type": "золото", "fineness": "585", "weight": "1,5", "price": "100 000", "min_price": "120000",
            "quantity": "1", "track_stock": "on"}
    r = client.post("/products/new", data=form)
    assert "Минимальная цена не может быть выше цены" in r.text
    form["min_price"] = "90000"
    r = client.post("/products/new", data=form, follow_redirects=False)
    assert r.status_code == 303
    pid = int(r.headers["location"].rsplit("/", 1)[1])
    p = db_query("SELECT * FROM products WHERE id=%s", (pid,))[0]
    assert (p['price'], p['min_price'], p['fineness'], p['track_stock']) == (100000, 90000, '585', 1)
    edit = client.get(f"/products/{pid}")
    assert "Кольцо Веб" in edit.text
    form.update(csrf=csrf(client, f"/products/{pid}"), price="110000", quantity="3", article_number="A100")
    assert "уже занят" in client.post(f"/products/{pid}", data=form).text
    form["article_number"] = "W1"
    client.post(f"/products/{pid}", data=form)
    p = db_query("SELECT * FROM products WHERE id=%s", (pid,))[0]
    assert p['price'] == 110000 and p['quantity'] == 3
    assert client.get("/stock.xlsx").headers["content-type"].startswith("application/vnd.openxmlformats")


def test_import_via_web(tg, client):
    web_login(tg, client, MANAGER)
    wb = openpyxl.Workbook()
    for row in [["Артикул", "Наименование", "Металл", "Вес", "Цена", "Остаток"],
                ["Z1", "Серьги Луна", "Серебро 925", 3, 12000, 4], ["A100", "Кольцо Классика", "", None, 160000, 7]]:
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/import", data={"csrf": csrf(client, "/import"), "store": "1"},
                    files={"file": ("catalog.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 200 and "Предпросмотр" in r.text and "новый" in r.text and "обновится" in r.text
    action = re.search(r'action="(/import/[^"]+)"', r.text).group(1)
    token = re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)
    r = client.post(action, data={"csrf": token, "mode": "all"})
    assert "добавлено 1, обновлено 1" in r.text
    rows = {x['article_number']: x for x in db_query("SELECT * FROM products")}
    assert rows['Z1']['quantity'] == 4 and rows['Z1']['fineness'] == '925' and rows['A100']['price'] == 160000
    assert rows['A100']['weight'] == 2.5  # вес не был указан — не затёрт
    # предпросмотр одноразовый
    assert "устарел" in client.post(action, data={"csrf": token, "mode": "all"}).text


def test_labels_pdf_custom_size(tg, client):
    web_login(tg, client, MANAGER)
    r = client.post("/labels", data={"csrf": csrf(client, "/labels"), "mode": "articles", "articles": "A100 A200",
                                     "copies": "one", "width": "58", "height": "40"})
    assert r.headers["content-type"] == "application/pdf"
    pdf = PdfReader(io.BytesIO(r.content))
    assert len(pdf.pages) == 2 and round(float(pdf.pages[0].mediabox.width) / 72 * 25.4) == 58
    r = client.post("/labels", data={"csrf": csrf(client, "/labels"), "mode": "articles", "articles": "NOPE"})
    assert "не найдено" in r.text


def test_reports_page_and_excel(tg, client):
    login(tg, CASHIER)
    for t in ["Продажа", "A100", "150000", "Kaspi", "Подтвердить"]:
        tg.send(CASHIER, t)
    web_login(tg, client, ADMIN)
    page = client.get("/reports")
    assert "150 000 ₸" in page.text and "Kaspi" in page.text and "Кольцо Классика" in page.text
    dash = client.get("/")
    assert "150 000 ₸" in dash.text
    x = client.get("/reports.xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(x.content))
    assert wb["Продажи"].max_row == 2


def test_staff_admin_only_and_actions(tg, client):
    web_login(tg, client, MANAGER)
    assert client.get("/staff").status_code == 403
    client.cookies.clear()
    web_login(tg, client, ADMIN)
    token = csrf(client, "/staff")
    r = client.post("/staff", data={"csrf": token, "action": "add", "name": "Веб Кассир", "phone": "8 701 777 66 55", "role": "cashier"})
    assert "добавлен" in r.text
    new = db_query("SELECT * FROM users WHERE name='Веб Кассир'")[0]
    assert new['phone_number'] == '+77017776655'
    client.post("/staff", data={"csrf": token, "action": "role", "user_id": new['id'], "role": "manager"})
    assert db_query("SELECT role FROM users WHERE id=%s", (new['id'],))[0]['role'] == 'manager'
    me = db_query("SELECT id FROM users WHERE role='admin'")[0]['id']
    assert "собственную" in client.post("/staff", data={"csrf": token, "action": "deactivate", "user_id": me}).text
    client.post("/staff", data={"csrf": token, "action": "deactivate", "user_id": new['id']})
    assert db_query("SELECT is_active FROM users WHERE id=%s", (new['id'],))[0]['is_active'] == 0


def test_pc_kassa_button_sends_link(tg):
    """Кнопка «💻 Касса на ПК» — ссылка на кассу в браузере и как войти; доступна всем ролям."""
    for chat_id in (CASHIER, MANAGER, ADMIN):
        login(tg, chat_id)
        calls = tg.send(chat_id, "💻 Касса на ПК")
        text = tg.texts(calls)
        assert "https://pos.test/kassa" in text and "камеру телефона" in text and "Выйти" in text
        urls = [b.url for c in calls if isinstance(c.markup, types.InlineKeyboardMarkup) for row in c.markup.keyboard
                for b in row]
        assert urls == ["https://pos.test/kassa"]


# ---------------------------------------------------------------- Mini App

def test_mini_app_sale_flow(tg, client):
    login(tg, CASHIER)
    headers = {"X-Telegram-Init-Data": init_data(CASHIER)}
    assert "Сканировать" in client.get("/app").text
    me = client.get("/api/app/me", headers=headers)
    assert me.status_code == 200 and me.json()["today"] == []
    p = client.get("/api/app/product", params={"article": "A100"}, headers=headers).json()
    assert p["price"] == 150000 and p["quantity"] == 3 and "585" not in p["details"]
    assert client.get("/api/app/product", params={"article": "NOPE"}, headers=headers).status_code == 404
    # сканер камерой читает и штрихкоды старых ценников; EAN бывает дополнен нулями слева
    page = client.get("/app").text
    assert "/static/zxing.min.js" in page and "EAN_13" in page and "CODE_128" in page
    assert client.get("/static/zxing.min.js").status_code == 200
    db_query("UPDATE products SET article_number='12345' WHERE article_number='A200'")
    p2 = client.get("/api/app/product", params={"article": "0000000012345"}, headers=headers).json()
    assert p2["article"] == "12345"
    assert client.get("/api/app/product", params={"article": "000"}, headers=headers).status_code == 404
    r = client.post("/api/app/sale", json={"article": "A100", "price": 100000, "payment": "Kaspi"}, headers=headers)
    assert r.status_code == 400 and "ниже минимальной" in r.json()["error"]
    r = client.post("/api/app/sale", json={"article": "A100", "price": 145000, "payment": "Bitcoin"}, headers=headers)
    assert r.status_code == 400
    r = client.post("/api/app/sale", json={"article": "A100", "price": 145000, "payment": "Kaspi"}, headers=headers)
    assert r.status_code == 200 and r.json()["price"] == 145000
    sale = db_query("SELECT * FROM sales")[0]
    assert sale['sold_price'] == 145000 and sale['payment_type'] == 'Kaspi'
    assert db_query("SELECT quantity FROM products WHERE article_number='A100'")[0]['quantity'] == 2
    assert client.get("/api/app/me", headers=headers).json()["today"][0]["price"] == 145000
    assert client.post("/api/app/sale", json={"article": "A300", "price": 90000, "payment": "Kaspi"},
                       headers=headers).json()["error"] == "Товара нет в наличии."


def test_mini_app_rejects_bad_signature_and_strangers(tg, client):
    login(tg, CASHIER)
    assert client.get("/api/app/me").status_code == 401
    assert client.get("/api/app/me", headers={"X-Telegram-Init-Data": init_data(CASHIER, tamper=True)}).status_code == 401
    assert client.get("/api/app/me", headers={"X-Telegram-Init-Data": init_data(CASHIER, token="1:OTHER")}).status_code == 401
    old = int(time.time()) - 3 * 86400
    assert client.get("/api/app/me", headers={"X-Telegram-Init-Data": init_data(CASHIER, auth_date=old)}).status_code == 401
    # подпись верная, но этот Telegram-аккаунт не вошёл в бота
    assert client.get("/api/app/me", headers={"X-Telegram-Init-Data": init_data(999999)}).status_code == 403
