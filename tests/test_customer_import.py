"""Клиенты: добавление вручную в админке и импорт из Excel/CSV с предпросмотром."""
import io
import re
from datetime import date

import openpyxl

from conftest import MANAGER, CASHIER, db_query
from test_stores import _web, _csrf
from utils.common import NO_YEAR
from utils.customer_import import parse_customers


def _xlsx(rows):
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_add_customer_manually(tg):
    manager = _web(tg, MANAGER)
    assert "Добавить или импортировать" in manager.get("/customers").text
    page = manager.get("/customers/add").text
    assert "Один клиент" in page and "Импорт из файла" in page
    csrf = _csrf(manager, "/customers/add")
    base = {"csrf": csrf, "phone": "8 701 111 22 33", "name": "Айгерим", "birthday": "12.05", "note": "размер 17"}
    assert "согласился на хранение" in manager.post("/customers/add", data=base).text          # без согласия
    assert db_query("SELECT COUNT(*) AS n FROM customers")[0]['n'] == 0
    assert "ДД.ММ" in manager.post("/customers/add", data={**base, "consent": "on", "birthday": "31.02"}).text
    assert "Телефон" in manager.post("/customers/add", data={**base, "consent": "on", "phone": "123"}).text
    r = manager.post("/customers/add", data={**base, "consent": "on", "marketing": "on"})
    assert "Клиент добавлен" in r.text and "добавлен в админке" in r.text
    c = db_query("SELECT * FROM customers")[0]
    assert c['phone'] == "+77011112233" and c['name'] == "Айгерим" and c['birthday'] == date(NO_YEAR, 5, 12)
    assert c['consent_source'] == 'manual' and c['marketing'] == 1 and c['note'] == "размер 17" and c['consent_by']
    # повтор номера — открывается карточка существующего
    r = manager.post("/customers/add", data={**base, "consent": "on", "phone": "+7 (701) 111-22-33"})
    assert "уже есть в базе" in r.text and str(r.url).endswith(f"/customers/{c['id']}")
    assert db_query("SELECT COUNT(*) AS n FROM customers")[0]['n'] == 1


def test_parse_customers_file():
    data = _xlsx([["Список клиентов"], ["ФИО", "Тел.", "Дата рождения", "Примечание", "Рассылка"],
                  ["Анна", 87011234567, date(1990, 3, 8), "VIP", "да"],
                  ["Бек", "+7 (702) 222-33-44", "15.07", None, "нет"],
                  ["Без номера", None, None, None, None],
                  ["Плохой номер", "12345", None, None, None],
                  ["Вера", 7.70333444e10, "32.13.2000", None, None]])
    columns, rows = parse_customers(data, "clients.xlsx")
    assert columns == {'name': "ФИО", 'phone': "Тел.", 'birthday': "Дата рождения", 'note': "Примечание", 'marketing': "Рассылка"}
    a, b, none, bad, v = rows
    assert a['phone'] == "+77011234567" and a['birthday'] == date(1990, 3, 8) and a['marketing'] and a['note'] == "VIP"
    assert b['phone'] == "+77022223344" and b['birthday'] == date(NO_YEAR, 7, 15) and not b['marketing']
    assert none['error'] == "нет телефона" and "не понял номер" in bad['error']
    assert v['phone'] == "+77033344400" and v['birthday'] is None and "дату рождения" in v['warning'] and not v['error']
    csv = "Телефон;Имя\n+77014445566;Дина\n".encode("cp1251")
    assert parse_customers(csv, "c.csv")[1][0]['name'] == "Дина"
    for data, name, text in ((b"x", "c.pdf", ".xlsx или .csv"), (_xlsx([["Имя"], ["Анна"]]), "c.xlsx", "колонку с телефоном")):
        try:
            parse_customers(data, name)
            raise AssertionError("ожидалась ошибка")
        except ValueError as e:
            assert text in str(e)


def test_import_preview_and_apply(tg):
    db_query("""INSERT INTO customers (phone, name, birthday, consent_at, consent_source, created_at, updated_at)
                VALUES ('+77011234567', NULL, NULL, NOW(), 'kassa', NOW(), NOW()),
                       ('+77019998877', 'Старая', '1985-01-01', NOW(), 'kassa', NOW(), NOW())""")
    data = _xlsx([["Телефон", "Имя", "День рождения"],
                  ["87011234567", "Анна", "08.03.1990"],          # есть без имени — дополним
                  ["+77019998877", "Новое имя", "02.02.1999"],     # есть с именем и ДР — без изменений
                  ["+77025556677", "Бек", ""],                     # новый
                  ["8 702 555 66 77", "Бек дубль", ""],            # повтор в файле
                  ["нет", "Ошибка", ""]])
    manager = _web(tg, MANAGER)
    csrf = _csrf(manager, "/customers/add")
    page = manager.post("/customers/import", data={"csrf": csrf},
                        files={"file": ("clients.xlsx", data, "application/octet-stream")}).text
    assert "Импорт клиентов: проверка" in page and "Пока ничего не сохранено" in page
    stats = [int(x) for x in re.findall(r'<div class="stat">(\d+)</div>', page)]
    assert stats == [1, 1, 1, 2]                                   # новые, дополним, без изменений, пропущено
    assert "повторяется в файле" in page and "не понял номер" in page
    assert db_query("SELECT COUNT(*) AS n FROM customers")[0]['n'] == 2
    plan = re.search(r'action="/customers/import/([^"]+)"', page).group(1)
    r = manager.post(f"/customers/import/{plan}", data={"csrf": csrf})                         # без подтверждения
    assert "Подтвердите" in r.text and db_query("SELECT COUNT(*) AS n FROM customers")[0]['n'] == 2
    r = manager.post(f"/customers/import/{plan}", data={"csrf": csrf, "consent": "on"})
    assert "добавлено 1, дополнено 1" in r.text
    rows = {c['phone']: c for c in db_query("SELECT * FROM customers")}
    assert rows['+77011234567']['name'] == "Анна" and rows['+77011234567']['birthday'] == date(1990, 3, 8)
    assert rows['+77019998877']['name'] == "Старая" and rows['+77019998877']['birthday'] == date(1985, 1, 1)
    assert rows['+77025556677']['name'] == "Бек" and rows['+77025556677']['consent_source'] == 'import'
    # план одноразовый
    assert "устарел" in manager.post(f"/customers/import/{plan}", data={"csrf": csrf, "consent": "on"}).text
    # кривой файл — ошибка на той же странице
    page = manager.post("/customers/import", data={"csrf": csrf},
                        files={"file": ("c.xlsx", _xlsx([["Имя"], ["Анна"]]), "application/octet-stream")}).text
    assert "колонку с телефоном" in page
