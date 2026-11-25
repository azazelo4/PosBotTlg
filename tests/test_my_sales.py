"""Продавец видит свои продажи за выбранный период — в боте («Отчеты») и в кассе на ПК («Мои продажи»)."""
import io

import openpyxl

from conftest import ADMIN, CASHIER, MANAGER, login, reply_buttons, db_query
from test_kassa import client, kassa_login  # noqa: F401  (фикстура client)
from test_stores import _sell
from utils.common import today


def test_cashier_report_shows_only_own_operations(tg):
    login(tg, CASHIER)
    login(tg, MANAGER)
    _sell(tg, CASHIER, "A100", 150000, "Kaspi")
    _sell(tg, MANAGER, "A100", 140000)                         # чужая продажа
    assert "Мои продажи: за какой период" in tg.texts(tg.send(CASHIER, "Отчеты"))
    text = tg.texts(tg.send(CASHIER, "Сегодня"))               # без вопроса о магазине
    assert "Мои продажи за" in text and "Кассир" in text
    assert "Продажи: 1 шт. на <b>150 000 ₸</b>" in text and "По магазинам" not in text
    wb = openpyxl.load_workbook(io.BytesIO(tg.documents[-1].data))
    rows = list(wb["Продажи"].iter_rows(min_row=2, values_only=True))
    assert len(rows) == 1 and wb["Сводка"]["A3"].value == "Продавец"
    # менеджер по-прежнему видит отчёт по магазину целиком
    tg.send(MANAGER, "Отчеты")
    tg.send(MANAGER, "Сегодня")
    assert "Продажи: 2 шт. на <b>290 000 ₸</b>" in tg.texts(tg.send(MANAGER, "Все магазины"))


def test_pc_kassa_button_in_cash_menu(tg):
    login(tg, CASHIER)
    buttons = reply_buttons(tg.send(CASHIER, "Касса")[-1])
    assert "💻 Касса на ПК" in buttons
    assert "/kassa" in tg.texts(tg.send(CASHIER, "💻 Касса на ПК"))


def test_kassa_my_sales(tg, client):  # noqa: F811
    kassa_login(tg, client)
    _sell(tg, CASHIER, "A100", 150000)
    login(tg, ADMIN)
    _sell(tg, ADMIN, "A100", 140000)
    d = today().isoformat()
    s = client.get(f"/kassa/api/my-sales?start={d}&end={d}").json()
    assert s["sales_count"] == 1 and s["sales_total"] == 150000 and len(s["journal"]) == 1
    assert s["by_payment"] == {"Наличные": {"count": 1, "total": 150000}}
    assert client.get("/kassa/api/my-sales?start=2026-02-01&end=2026-01-01").status_code == 400
    assert client.get("/kassa/api/my-sales?start=abc").status_code == 400
    x = client.get(f"/kassa/api/my-sales.xlsx?start={d}&end={d}")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert "Мои продажи" in client.get("/kassa").text
