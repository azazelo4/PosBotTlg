"""База клиентов и электронный чек (#30): касса ПК, страница чека, бот-клиент, доставка, админка."""
import re

from fastapi.testclient import TestClient

import config
import customers
from conftest import ADMIN, CASHIER, MANAGER, STRANGER, login, db_query, inline_buttons, reply_buttons
from database import record_receipt
from test_kassa import client, kassa_login, post  # noqa: F401  (фикстура)
from test_stores import _web, _csrf
from utils import scheduled_jobs as jobs
from web.app import app

PHONE = "+7 701 555 44 33"


def _pid(article):
    return db_query("SELECT id FROM products WHERE article_number=%s", (article,))[0]['id']


def _uid(role):
    return db_query("SELECT id FROM users WHERE role=%s", (role,))[0]['id']


def _sale(client, phone=PHONE, consent=True, name="Айгерим"):  # noqa: F811
    return post(client, "sale", {"items": [{"article": "A100", "price": 145000}], "payment": "Kaspi",
                                 "customer": {"phone": phone, "name": name, "consent": consent} if phone else None})


def _token(receipt_id):
    return db_query("SELECT public_token FROM receipts WHERE id=%s", (receipt_id,))[0]['public_token']


def test_kassa_sale_with_customer(tg, client):  # noqa: F811
    kassa_login(tg, client)
    r = _sale(client, consent=False)
    assert r.status_code == 400 and "согласие" in r.json()["error"]
    assert _sale(client, phone="123").status_code == 400
    r = _sale(client)
    assert r.status_code == 200, r.text
    rc = r.json()["receipt"]
    assert rc["url"] == f"/r/{_token(rc['id'])}" and "<svg" in rc["qr"]
    assert rc["telegram"] == f"https://t.me/pos_test_bot?start=r_{_token(rc['id'])}"
    assert rc["whatsapp"].startswith("https://wa.me/77015554433?text=") and rc["telegram_auto"] is False
    assert rc["public_url"].endswith(rc["url"]) and "pos.test" in rc["public_url"]
    c = db_query("SELECT * FROM customers")
    assert len(c) == 1 and c[0]['phone'] == "+77015554433" and c[0]['name'] == "Айгерим"
    assert c[0]['consent_source'] == "kassa" and c[0]['consent_by'] == _uid('cashier')
    _sale(client, phone="87015554433", name="")                           # тот же клиент, другой формат номера
    assert db_query("SELECT COUNT(*) AS n FROM receipts WHERE customer_id=%s", (c[0]['id'],))[0]['n'] == 2
    assert _sale(client, phone=None).json()["receipt"]["whatsapp"] is None  # без клиента — QR и печать


def test_public_receipt_page(tg, monkeypatch):
    monkeypatch.setattr(config, "RECEIPT_FOOTER", "Гарантия 12 месяцев")
    ok, r = record_receipt([(_pid("A100"), 140000), (_pid("A100"), 150000)], _uid("admin"), "Наличные")
    web = TestClient(app)
    page = web.get(f"/r/{r['public_token']}")
    assert page.status_code == 200 and page.headers["x-robots-tag"] == "noindex"
    assert f"Чек №{r['receipt_id']}" in page.text and page.text.count("арт. A100") == 2
    assert "скидка" in page.text and "Гарантия 12 месяцев" in page.text and "Наличные" in page.text
    assert web.get("/r/" + "0" * 32).status_code == 404
    assert web.get("/r/short").status_code == 404


def test_customer_subscribes_in_telegram_and_gets_next_receipts(tg, client):  # noqa: F811
    ok, r = record_receipt([(_pid("A100"), 150000)], _uid('admin'), "Kaspi")
    calls = tg.send(STRANGER, f"/start r_{r['public_token']}")
    text = tg.texts(calls)
    assert f"Чек №{r['receipt_id']}" in text and "A100" in text and "150 000 ₸" in text
    calls = tg.press(STRANGER, inline_buttons(calls)["📲 Присылать мои чеки сюда"])
    assert "соглашаетесь" in tg.texts(calls) and "📲 Поделиться номером" in reply_buttons(calls[-1])
    calls = tg.contact(STRANGER, "+77019999999")
    assert "Готово" in tg.texts(calls)
    c = customers.by_chat(STRANGER)
    assert c['phone'] == "+77019999999" and c['consent_source'] == "telegram"
    assert db_query("SELECT customer_id FROM receipts WHERE id=%s", (r['receipt_id'],))[0]['customer_id'] == c['id']
    # следующая покупка: продавец вводит номер на кассе — чек приходит сам, один раз
    kassa_login(tg, client)
    rc = _sale(client, phone="8 701 999 99 99").json()["receipt"]
    assert rc["telegram_auto"] is True
    tg.calls.clear()
    assert jobs.send_customer_receipts() == 1
    assert [c.chat_id for c in tg.calls if c.text and f"Чек №{rc['id']}" in c.text] == [STRANGER]
    assert jobs.send_customer_receipts() == 0
    # посторонний текст от клиента — подсказка, а не «войдите в бота»
    assert "приходят ваши чеки" in tg.texts(tg.send(STRANGER, "привет"))
    assert "Больше не присылаем" in tg.texts(tg.send(STRANGER, "/stop"))
    assert customers.by_chat(STRANGER) is None


def test_receipt_waits_for_fiscal_link(tg, monkeypatch):
    ok, r = record_receipt([(_pid("A100"), 150000)], _uid('admin'), "Kaspi", customer={"phone": PHONE})
    customers.subscribe(PHONE, STRANGER)
    db_query("""INSERT INTO fiscal_receipts (kind, ref_id, store_id, cashbox, total, status, next_try_at, external_number,
                                             created_at, updated_at)
                VALUES ('sale', %s, 1, 'SWK1', 150000, 'pending', NOW(), 'x1', NOW(), NOW())""", (r['receipt_id'],))
    assert jobs.send_customer_receipts() == 0                              # ждём фискальный чек
    db_query("UPDATE fiscal_receipts SET status='done', ticket_url='https://ofd.test/t1' WHERE ref_id=%s", (r['receipt_id'],))
    tg.calls.clear()
    assert jobs.send_customer_receipts() == 1
    assert "https://ofd.test/t1" in tg.texts(tg.calls)


def test_blocked_bot_unsubscribes(tg, monkeypatch):
    from telebot.apihelper import ApiTelegramException
    record_receipt([(_pid("A100"), 150000)], _uid('admin'), "Kaspi", customer={"phone": PHONE})
    customers.subscribe(PHONE, STRANGER)

    def blocked(*a, **kw):
        raise ApiTelegramException("sendMessage", "", {"error_code": 403, "description": "Forbidden: bot was blocked"})

    import handlers.customer_handler as ch
    monkeypatch.setattr(ch.bot, "send_message", blocked)
    assert jobs.send_customer_receipts() == 0
    assert customers.by_chat(STRANGER) is None
    assert db_query("SELECT sent_at FROM receipts")[0]['sent_at'] is not None


def test_bot_seller_shows_qr(tg, monkeypatch):
    import web.kassa as kassa
    monkeypatch.setattr(kassa, "bot_username", lambda: "pos_test_bot")
    login(tg, CASHIER)
    tg.send(CASHIER, "Продажа")
    tg.send(CASHIER, "A100")
    tg.send(CASHIER, "150000")
    tg.send(CASHIER, "Kaspi")
    calls = tg.send(CASHIER, "Подтвердить")
    button = inline_buttons(calls)["🧾 Чек клиенту (QR)"]
    tg.photos.clear()
    calls = tg.press(CASHIER, button)
    assert tg.photos and tg.photos[0].data[:8] == b"\x89PNG\r\n\x1a\n"
    assert re.search(r"t\.me/pos_test_bot\?start=r_[0-9a-f]{32}", tg.texts(calls))


def test_admin_customers(tg):
    record_receipt([(_pid("A100"), 150000)], _uid('admin'), "Kaspi", customer={"phone": PHONE, "name": "Айгерим"})
    cid = db_query("SELECT id FROM customers")[0]['id']
    manager = _web(tg, MANAGER)
    assert "Айгерим" in manager.get("/customers").text
    assert "Айгерим" in manager.get("/customers", params={"q": "555 44"}).text
    assert "Никого не нашли" in manager.get("/customers", params={"q": "Бекзат"}).text
    page = manager.get(f"/customers/{cid}").text
    assert "История покупок" in page and "150 000 ₸" in page and "Удалить клиента" not in page
    csrf = _csrf(manager, f"/customers/{cid}")
    r = manager.post(f"/customers/{cid}", data={"csrf": csrf, "name": "Айгерим К.", "birthday": "12.05.1995", "note": "р. 17"})
    assert "Сохранено" in r.text and "12.05.1995" in r.text
    assert "ДД.ММ.ГГГГ" in manager.post(f"/customers/{cid}", data={"csrf": csrf, "birthday": "май"}).text
    assert manager.post(f"/customers/{cid}/delete", data={"csrf": csrf}).status_code == 403
    admin = _web(tg, ADMIN)
    csrf = _csrf(admin, f"/customers/{cid}")
    r = admin.post(f"/customers/{cid}/delete", data={"csrf": csrf})
    assert "Клиент удалён" in r.text
    assert db_query("SELECT COUNT(*) AS n FROM customers")[0]['n'] == 0
    assert db_query("SELECT customer_id FROM receipts")[0]['customer_id'] is None
