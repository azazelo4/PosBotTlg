"""Магазины как справочник (#14): магазин операций, выбор магазина, отчёты по магазинам, управление."""
import io
import re

import openpyxl
from fastapi.testclient import TestClient
from pypdf import PdfReader

from conftest import ADMIN, MANAGER, CASHIER, login, reply_buttons, inline_buttons, db_query
from web.app import app


def _sell(tg, chat_id, article, price, payment="Наличные"):
    for t in ["Продажа", article, str(price), payment, "Подтвердить"]:
        calls = tg.send(chat_id, t)
    assert "✅" in tg.texts(calls), tg.texts(calls)


def test_operations_get_the_right_store(tg):
    login(tg, CASHIER)                        # кассир привязан к ЦУМ (store_id=1)
    _sell(tg, CASHIER, "A100", 150000)        # свой товар
    assert db_query("SELECT store_id FROM sales")[0]['store_id'] == 1
    # товар другого магазина кассир не продаёт
    tg.send(CASHIER, "Продажа")
    text = tg.texts(tg.send(CASHIER, "A200"))
    assert "числится в магазине «Отау Сауда»" in text and "менеджер меняет магазин" in text
    tg.send(CASHIER, "Отмена")
    # администратор («все магазины») продаёт — продажа на магазин товара
    login(tg, ADMIN)
    _sell(tg, ADMIN, "A200", 20000)
    sale2 = db_query("SELECT id, store_id FROM sales WHERE store_id = 2")[0]
    assert sale2['store_id'] == 2
    # возврат продажи другого магазина кассир не оформляет, администратор — да
    tg.send(CASHIER, "Возврат")
    assert "возврат оформляет сотрудник этого магазина" in tg.texts(tg.send(CASHIER, "A200"))
    tg.send(CASHIER, "Отмена")
    for t in ["Возврат", "A200", "20000", "Наличные", "Подтвердить"]:
        tg.send(ADMIN, t)
    assert db_query("SELECT store_id FROM returns")[0]['store_id'] == 2  # магазин продажи
    for t in ["Скупка", "A100", "1000", "Наличные", "Подтвердить"]:
        tg.send(CASHIER, t)
    assert db_query("SELECT store_id FROM buybacks")[0]['store_id'] == 1  # своё изделие — магазин товара
    for t in ["Trade-In", "Без артикула", "золото", "1", "Пропустить", "5000"]:
        tg.send(CASHIER, t)
    assert "числится в магазине «Отау Сауда»" in tg.texts(tg.send(CASHIER, "A200"))  # новое изделие — чужое
    for t in ["A100", "150000", "Наличные", "Подтвердить"]:
        tg.send(CASHIER, t)
    assert db_query("SELECT store_id FROM trade_ins")[0]['store_id'] == 1  # магазин нового товара
    for t in ["Инкассация", "1000", "Пропустить", "Подтвердить"]:
        tg.send(CASHIER, t)
    assert db_query("SELECT store_id FROM cash_withdrawals")[0]['store_id'] == 1  # магазин кассира, без вопроса


def test_foreign_store_blocked_in_transaction(tg):
    """Проверка в самой записи операции — обходной путь (касса ПК, Mini App, прямой вызов) тоже не пройдёт."""
    from core.database import record_receipt, create_return_record, create_trade_in_record
    uid = {r['role']: r['id'] for r in db_query("SELECT id, role FROM users")}
    a200 = db_query("SELECT id FROM products WHERE article_number='A200'")[0]['id']
    ok, msg = record_receipt([(a200, 20000)], uid['cashier'], "Наличные")
    assert not ok and "Отау Сауда" in msg
    ok, msg = create_trade_in_record("A200", 20000, 5000, 15000, "Наличные", uid['manager'], 1)
    assert not ok and "Отау Сауда" in msg
    assert db_query("SELECT quantity FROM products WHERE id=%s", (a200,))[0]['quantity'] == 1   # откат: остаток цел
    ok, r = record_receipt([(a200, 20000)], uid['admin'], "Наличные")
    assert ok
    ok, msg = create_return_record(r['sale_ids'][0], "A200", 20000, "Наличные", uid['cashier'], 1)
    assert not ok and "возврат оформляет" in msg
    db_query("UPDATE users SET store_id = 2 WHERE role='cashier'")                 # перевели в Отау Сауда
    assert create_return_record(r['sale_ids'][0], "A200", 20000, "Наличные", uid['cashier'], 2)[0]


def test_return_finds_own_store_sale_behind_foreign_ones(tg):
    """Ревью Graphify (PR #53): изделие перевозили — 10 свежих продаж в другом магазине не прячут старую продажу своего."""
    from core.database import record_receipt
    uid = {r['role']: r['id'] for r in db_query("SELECT id, role FROM users")}
    a100 = db_query("SELECT id FROM products WHERE article_number='A100'")[0]['id']
    db_query("UPDATE products SET quantity = 20 WHERE id = %s", (a100,))
    ok, own = record_receipt([(a100, 150000)], uid['cashier'], "Наличные")             # ЦУМ
    assert ok
    db_query("UPDATE sales SET sale_date = NOW() - INTERVAL 5 DAY WHERE id = %s", (own['sale_ids'][0],))
    db_query("UPDATE products SET store_id = 2 WHERE id = %s", (a100,))                  # перевезли в Отау Сауда
    for _ in range(10):
        assert record_receipt([(a100, 150000)], uid['admin'], "Наличные")[0]
    login(tg, CASHIER)
    tg.send(CASHIER, "Возврат")
    text = tg.texts(tg.send(CASHIER, "A100"))
    assert f"Продажа №{own['sale_ids'][0]}" in text and "Введите сумму возврата" in text


def test_employee_without_store_is_asked(tg):
    login(tg, ADMIN)                          # администратор без магазина
    calls = tg.send(ADMIN, "Инкассация")
    assert "какого магазина" in tg.texts(calls) and reply_buttons(calls[-1])[:2] == ["ЦУМ", "Отау Сауда"]
    assert "кнопкой" in tg.texts(tg.send(ADMIN, "Склад-3"))
    for t in ["Отау Сауда", "5000", "Пропустить", "Подтвердить"]:
        tg.send(ADMIN, t)
    assert db_query("SELECT store_id FROM cash_withdrawals")[0]['store_id'] == 2
    for t in ["Скупка", "Без артикула", "серебро", "2"]:
        tg.send(ADMIN, t)
    assert "В каком магазине скупка" in tg.texts(tg.send(ADMIN, "Пропустить"))
    for t in ["ЦУМ", "3000", "Kaspi", "Подтвердить"]:
        tg.send(ADMIN, t)
    assert db_query("SELECT store_id FROM buybacks")[0]['store_id'] == 1


def test_single_store_is_never_asked(tg):
    db_query("UPDATE stores SET is_active = 0 WHERE id = 2")
    login(tg, ADMIN)
    calls = tg.send(ADMIN, "Инкассация")
    assert "Введите сумму" in tg.texts(calls)
    for t in ["5000", "Пропустить", "Подтвердить"]:
        tg.send(ADMIN, t)
    assert db_query("SELECT store_id FROM cash_withdrawals")[0]['store_id'] == 1
    tg.send(ADMIN, "Отчеты")
    assert "Отчёт за" in tg.texts(tg.send(ADMIN, "Сегодня"))  # без вопроса о магазине


def test_report_for_one_store(tg, unbound_staff):
    login(tg, CASHIER)
    _sell(tg, CASHIER, "A100", 150000, "Kaspi")
    _sell(tg, CASHIER, "A200", 20000)
    tg.send(CASHIER, "Отчеты")
    tg.send(CASHIER, "Сегодня")
    calls = tg.send(CASHIER, "Отау Сауда")
    text = tg.texts(calls)
    assert "· Отау Сауда" in text and "Продажи: 1 шт. на <b>20 000 ₸</b>" in text and "По магазинам" not in text
    wb = openpyxl.load_workbook(io.BytesIO(tg.documents[-1].data))
    rows = list(wb["Продажи"].iter_rows(min_row=2, values_only=True))
    assert len(rows) == 1 and rows[0][3] == "A200" and rows[0][12] == "Отау Сауда"
    assert wb["Сводка"]["B2"].value == "Отау Сауда"


def test_bot_change_employee_store(tg):
    login(tg, ADMIN)
    cashier = db_query("SELECT id FROM users WHERE role='cashier'")[0]['id']
    tg.press(ADMIN, f"um_user_{cashier}")
    buttons = inline_buttons(tg.press(ADMIN, f"um_store_{cashier}"))
    assert set(buttons) >= {"ЦУМ", "Отау Сауда", "Все магазины"}
    assert "✅" in tg.texts(tg.press(ADMIN, buttons["Отау Сауда"]))
    assert db_query("SELECT store_id FROM users WHERE id=%s", (cashier,))[0]['store_id'] == 2
    tg.press(ADMIN, buttons["Все магазины"])
    assert db_query("SELECT store_id FROM users WHERE id=%s", (cashier,))[0]['store_id'] is None


def _web(tg, chat_id):
    login(tg, chat_id)
    calls = tg.send(chat_id, "Веб-админка")
    link = next(b.url for c in calls if c.markup and hasattr(c.markup, 'keyboard') for row in c.markup.keyboard
                for b in row if getattr(b, 'url', None))
    client = TestClient(app, base_url="https://testserver")
    client.get(link.replace("https://pos.test", ""), follow_redirects=False)
    return client


def _csrf(client, path):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def test_web_manage_stores_and_rename(tg):
    manager = _web(tg, MANAGER)
    assert manager.get("/stores").status_code == 403
    client = _web(tg, ADMIN)
    token = _csrf(client, "/stores")
    r = client.post("/stores", data={"csrf": token, "action": "add", "name": "ТРЦ Хан Шатыр", "address": "пр. Туран 37"})
    assert "добавлен" in r.text
    assert "уже есть" in client.post("/stores", data={"csrf": token, "action": "add", "name": "ЦУМ"}).text
    new_id = db_query("SELECT id FROM stores WHERE name='ТРЦ Хан Шатыр'")[0]['id']
    # новый магазин сразу доступен в боте
    login(tg, ADMIN)
    tg.send(ADMIN, "Ценники")
    tg.send(ADMIN, "Все в наличии")
    assert "ТРЦ Хан Шатыр" in tg.texts(tg.calls[-1:]) or "ТРЦ Хан Шатыр" in reply_buttons(tg.calls[-1])
    tg.send(ADMIN, "Отмена")
    # переименование: товары и ценники идут с новым названием
    client.post("/stores", data={"csrf": token, "store_id": "1", "name": "ЦУМ Астана", "is_active": "on"})
    assert db_query("SELECT p.article_number FROM products p JOIN stores s ON s.id = p.store_id "
                    "WHERE s.name='ЦУМ Астана'") != []
    r = client.post("/labels", data={"csrf": _csrf(client, "/labels"), "mode": "articles", "articles": "A100"})
    assert "ЦУМ Астана" in PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
    # отключить последний активный нельзя
    client.post("/stores", data={"csrf": token, "store_id": str(new_id), "name": "ТРЦ Хан Шатыр"})
    client.post("/stores", data={"csrf": token, "store_id": "2", "name": "Отау Сауда"})
    r = client.post("/stores", data={"csrf": token, "store_id": "1", "name": "ЦУМ Астана"})
    assert "последний активный" in r.text


def test_web_staff_store_and_reports_filter(tg, unbound_staff):
    login(tg, CASHIER)
    _sell(tg, CASHIER, "A100", 150000)
    _sell(tg, CASHIER, "A200", 20000)
    client = _web(tg, ADMIN)
    token = _csrf(client, "/staff")
    client.post("/staff", data={"csrf": token, "action": "add", "name": "Новый Кассир", "phone": "+77011112233",
                                "role": "cashier", "store_id": "2"})
    new = db_query("SELECT * FROM users WHERE name='Новый Кассир'")[0]
    assert new['store_id'] == 2
    client.post("/staff", data={"csrf": token, "action": "store", "user_id": new['id'], "store_id": ""})
    assert db_query("SELECT store_id FROM users WHERE id=%s", (new['id'],))[0]['store_id'] is None
    page = client.get("/reports?store=2").text
    assert "20 000 ₸" in page and "Кольцо Классика" not in page and "По магазинам" not in page
    page = client.get("/reports").text
    assert "По магазинам" in page and "Кольцо Классика" in page
    assert "Сегодня по магазинам" in client.get("/").text
    x = client.get("/reports.xlsx?store=1")
    rows = list(openpyxl.load_workbook(io.BytesIO(x.content))["Продажи"].iter_rows(min_row=2, values_only=True))
    assert [r[3] for r in rows] == ["A100"]


def test_import_store_column_matches_case_insensitive(tg):
    login(tg, MANAGER)
    tg.send(MANAGER, "Импорт каталога")
    tg.send(MANAGER, "Магазин указан в файле")
    wb = openpyxl.Workbook()
    for row in [["Артикул", "Наименование", "Металл", "Цена", "Магазин"],
                ["S1", "Кольцо Тест", "золото", 1000, "отау сауда"], ["S2", "Кольцо Тест", "золото", 1000, "Нет такого"]]:
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    text = tg.texts(tg.document(MANAGER, buf.getvalue(), "s.xlsx"))
    assert "магазин «Нет такого» не найден (есть: ЦУМ, Отау Сауда)" in text
    tg.send(MANAGER, "Загрузить только новые")
    assert db_query("SELECT store_id FROM products WHERE article_number='S1'")[0]['store_id'] == 2
