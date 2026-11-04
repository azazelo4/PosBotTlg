import io
from decimal import Decimal

import openpyxl

from conftest import ADMIN, CASHIER, MANAGER, STRANGER, PHONES, login, reply_buttons, inline_buttons, db_query
from state import user_data, user_state


# ---------------------------------------------------------------- вход и сессии

def test_login_menus_for_all_roles(tg):
    assert reply_buttons(login(tg, ADMIN)[-1]) == ["Касса", "Склад", "Персонал"]
    assert reply_buttons(login(tg, CASHIER)[-1]) == ["Касса"]
    assert reply_buttons(login(tg, MANAGER)[-1]) == ["Касса", "Склад"]


def test_login_rejects_unknown_and_foreign_contact(tg):
    assert "не найден" in tg.texts(tg.contact(STRANGER, PHONES[STRANGER]))
    calls = tg.contact(STRANGER, PHONES[ADMIN], owner=999)  # чужой пересланный контакт
    assert "свой номер" in tg.texts(calls)
    assert STRANGER not in user_data


def test_unauthorized_user_cannot_use_menu(tg):
    calls = tg.send(STRANGER, "Касса")
    assert "поделитесь номером" in tg.texts(calls).lower()
    login(tg, CASHIER)
    assert "Не понял" in tg.texts(tg.send(CASHIER, "Склад"))  # склад кассиру недоступен


def test_session_restored_after_restart(tg):
    login(tg, CASHIER)
    user_data.clear()  # перезапуск бота
    calls = tg.send(CASHIER, "Касса")
    assert reply_buttons(calls[-1])[:2] == ["Продажа", "Возврат"]


# ---------------------------------------------------------------- продажа

def test_sale_full_flow_with_kaspi(tg):
    login(tg, CASHIER)
    tg.send(CASHIER, "Касса")
    tg.send(CASHIER, "Продажа")
    assert "нет в наличии" in tg.texts(tg.send(CASHIER, "A300"))
    calls = tg.send(CASHIER, "A100")
    assert "150 000 ₸" in reply_buttons(calls[-1])
    assert "ниже минимальной" in tg.texts(tg.send(CASHIER, "100000"))
    tg.send(CASHIER, "150 000 ₸")
    tg.send(CASHIER, "Kaspi")
    calls = tg.send(CASHIER, "Подтвердить")
    assert "✅ Продажа" in tg.texts(calls)
    sale = db_query("SELECT * FROM sales")[0]
    assert sale['payment_type'] == 'Kaspi' and sale['sold_price'] == 150000
    assert db_query("SELECT quantity FROM products WHERE article_number='A100'")[0]['quantity'] == 2
    assert CASHIER not in user_state


def test_sale_reports_db_failure_instead_of_success(tg):
    login(tg, CASHIER)
    tg.send(CASHIER, "Продажа")
    tg.send(CASHIER, "A200")
    tg.send(CASHIER, "20000")
    tg.send(CASHIER, "Наличные")
    db_query("UPDATE products SET quantity = 0 WHERE article_number='A200'")  # кто-то продал раньше
    calls = tg.send(CASHIER, "Подтвердить")
    assert "❌" in tg.texts(calls) and "Недостаточно" in tg.texts(calls)
    assert db_query("SELECT COUNT(*) c FROM sales")[0]['c'] == 0


# ---------------------------------------------------------------- отмена (#5)

def test_cancel_works_in_every_flow(tg):
    login(tg, ADMIN)
    starts = {"Продажа": "Продажа отменена", "Возврат": "возврата отменена", "Скупка": "Скупка отменена",
              "Trade-In": "Trade-In отменена", "Инкассация": "Инкассация отменена", "Отчеты": "отчёта отменена",
              "Добавить товар": "складом отменена", "Пополнить остатки": "складом отменена",
              "Добавить пользователя": "персоналом отменена"}
    for button, expected in starts.items():
        tg.send(ADMIN, button)
        assert user_state.get(ADMIN), button
        calls = tg.send(ADMIN, "Отмена")
        assert expected in tg.texts(calls), (button, tg.texts(calls))
        assert ADMIN not in user_state
        assert 'op' not in user_data[ADMIN]
    assert "Нет активной операции" in tg.texts(tg.send(ADMIN, "Отмена"))


def test_menu_button_interrupts_operation(tg):
    login(tg, ADMIN)
    tg.send(ADMIN, "Скупка")
    tg.send(ADMIN, "Назад")
    assert ADMIN not in user_state


# ---------------------------------------------------------------- возврат

def _sell(tg, chat_id, article, price, payment="Наличные"):
    tg.send(chat_id, "Продажа")
    tg.send(chat_id, article)
    tg.send(chat_id, str(price))
    tg.send(chat_id, payment)
    assert "✅" in tg.texts(tg.send(chat_id, "Подтвердить"))


def test_return_single_sale_and_no_double_return(tg):
    login(tg, CASHIER)
    _sell(tg, CASHIER, "A100", 140000, "Безналичные")
    tg.send(CASHIER, "Возврат")
    calls = tg.send(CASHIER, "A100")
    assert "140 000 ₸" in reply_buttons(calls[-1])
    assert "не больше" in tg.texts(tg.send(CASHIER, "150000"))
    tg.send(CASHIER, "140 000 ₸")
    tg.send(CASHIER, "Безналичные")
    assert "✅ Возврат" in tg.texts(tg.send(CASHIER, "Подтвердить"))
    assert db_query("SELECT quantity FROM products WHERE article_number='A100'")[0]['quantity'] == 3
    tg.send(CASHIER, "Возврат")
    assert "уже возвращены" in tg.texts(tg.send(CASHIER, "A100"))


def test_return_pick_from_several_sales(tg):
    login(tg, CASHIER)
    _sell(tg, CASHIER, "A100", 150000)
    _sell(tg, CASHIER, "A100", 130000, "Kaspi")
    tg.send(CASHIER, "Возврат")
    buttons = inline_buttons(tg.send(CASHIER, "A100"))
    assert len(buttons) == 2
    target = next(data for text, data in buttons.items() if "130 000" in text)
    calls = tg.press(CASHIER, target)
    assert "Kaspi" in tg.texts(calls)
    tg.send(CASHIER, "50000")
    tg.send(CASHIER, "Kaspi")
    tg.send(CASHIER, "Подтвердить")
    r = db_query("SELECT * FROM returns")[0]
    assert r['return_price'] == 50000 and r['payment_type'] == 'Kaspi'
    sold = db_query("SELECT id FROM sales WHERE sold_price=130000")[0]['id']
    assert r['original_sale_id'] == sold


# ---------------------------------------------------------------- скупка

def test_buyback_own_article_returns_to_stock(tg):
    login(tg, CASHIER)
    tg.send(CASHIER, "Скупка")
    tg.send(CASHIER, "A200")
    tg.send(CASHIER, "12000")
    tg.send(CASHIER, "Наличные")
    assert "✅ Скупка" in tg.texts(tg.send(CASHIER, "Подтвердить"))
    assert db_query("SELECT quantity FROM products WHERE article_number='A200'")[0]['quantity'] == 2
    assert db_query("SELECT product_article FROM buybacks")[0]['product_article'] == 'A200'


def test_buyback_without_article(tg):
    login(tg, CASHIER)
    tg.send(CASHIER, "Скупка")
    tg.send(CASHIER, "Без артикула")
    assert "Выберите металл" in tg.texts(tg.send(CASHIER, "платина"))
    tg.send(CASHIER, "золото")
    assert "положительным" in tg.texts(tg.send(CASHIER, "abc"))
    tg.send(CASHIER, "3,45")
    tg.send(CASHIER, "Кольцо 585, без камней")
    tg.send(CASHIER, "85 000")
    tg.send(CASHIER, "Kaspi")
    tg.send(CASHIER, "Подтвердить")
    b = db_query("SELECT * FROM buybacks")[0]
    assert b['product_article'] is None and b['metal_type'] == 'золото' and abs(b['weight'] - 3.45) < 1e-6
    assert b['buyback_price'] == 85000 and b['payment_type'] == 'Kaspi' and 'без камней' in b['description']


# ---------------------------------------------------------------- trade-in

def test_tradein_client_pays_difference(tg):
    login(tg, CASHIER)
    tg.send(CASHIER, "Trade-In")
    tg.send(CASHIER, "Без артикула")
    tg.send(CASHIER, "золото")
    tg.send(CASHIER, "2")
    tg.send(CASHIER, "Пропустить")
    tg.send(CASHIER, "60000")
    assert "нет в наличии" in tg.texts(tg.send(CASHIER, "A300"))
    tg.send(CASHIER, "A100")
    calls = tg.send(CASHIER, "150 000 ₸")
    assert "доплачивает" in tg.texts(calls) and "90 000 ₸" in tg.texts(calls)
    tg.send(CASHIER, "Наличные")
    assert "✅ Trade-In" in tg.texts(tg.send(CASHIER, "Подтвердить"))
    t = db_query("SELECT * FROM trade_ins")[0]
    assert t['price_difference'] == 90000 and t['old_item_value'] == 60000 and t['new_sale_price'] == 150000
    assert db_query("SELECT quantity FROM products WHERE article_number='A100'")[0]['quantity'] == 2


def test_tradein_shop_pays_client_and_old_item_to_stock(tg):
    login(tg, CASHIER)
    tg.send(CASHIER, "Trade-In")
    tg.send(CASHIER, "A100")          # клиент сдаёт наше кольцо
    tg.send(CASHIER, "30000")
    assert "совпадать" in tg.texts(tg.send(CASHIER, "A100"))
    tg.send(CASHIER, "A200")
    calls = tg.send(CASHIER, "20000")
    assert "выплачиваем" in tg.texts(calls) and "10 000 ₸" in tg.texts(calls)
    tg.send(CASHIER, "Наличные")
    tg.send(CASHIER, "Подтвердить")
    t = db_query("SELECT * FROM trade_ins")[0]
    assert t['price_difference'] == -10000 and t['old_product_article'] == 'A100'
    q = {r['article_number']: r['quantity'] for r in db_query("SELECT article_number, quantity FROM products")}
    assert q['A100'] == 4 and q['A200'] == 0


# ---------------------------------------------------------------- инкассация

def test_withdrawal(tg):
    login(tg, MANAGER)
    tg.send(MANAGER, "Инкассация")
    assert "положительную" in tg.texts(tg.send(MANAGER, "-5"))
    tg.send(MANAGER, "50000")
    tg.send(MANAGER, "в банк")
    assert "✅ Инкассация" in tg.texts(tg.send(MANAGER, "Подтвердить"))
    w = db_query("SELECT * FROM cash_withdrawals")[0]
    assert w['amount'] == Decimal('50000.00') and w['comment'] == 'в банк' and w['store_id'] == 1


# ---------------------------------------------------------------- отчёты (#7)

def test_report_today_includes_all_operations(tg):
    login(tg, CASHIER)
    _sell(tg, CASHIER, "A100", 150000, "Наличные")
    _sell(tg, CASHIER, "A200", 20000, "Kaspi")
    tg.send(CASHIER, "Возврат"); tg.send(CASHIER, "A200"); tg.send(CASHIER, "20000"); tg.send(CASHIER, "Kaspi"); tg.send(CASHIER, "Подтвердить")
    tg.send(CASHIER, "Скупка"); tg.send(CASHIER, "Без артикула"); tg.send(CASHIER, "серебро"); tg.send(CASHIER, "5")
    tg.send(CASHIER, "Пропустить"); tg.send(CASHIER, "3000"); tg.send(CASHIER, "Наличные"); tg.send(CASHIER, "Подтвердить")
    tg.send(CASHIER, "Инкассация"); tg.send(CASHIER, "100000"); tg.send(CASHIER, "Пропустить"); tg.send(CASHIER, "Подтвердить")

    tg.send(CASHIER, "Отчеты")
    calls = tg.send(CASHIER, "Сегодня")
    text = tg.texts(calls)
    assert "Продажи: 2 шт. на <b>170 000 ₸</b>" in text
    assert "Возвраты: 1 шт. на 20 000 ₸" in text
    assert "Скупка: 1 шт. на 3 000 ₸" in text
    assert "Инкассация: 1 раз на 100 000 ₸" in text
    # наличные: +150000 продажа −3000 скупка −100000 инкассация
    assert "после инкассации): <b>47 000 ₸</b>" in text

    wb = openpyxl.load_workbook(io.BytesIO(tg.documents[-1].data))
    assert wb.sheetnames == ["Сводка", "Продажи", "Возвраты", "Скупка", "Trade-In", "Инкассация"]
    sales_rows = list(wb["Продажи"].iter_rows(min_row=2, values_only=True))
    assert len(sales_rows) == 2 and {r[2] for r in sales_rows} == {"A100", "A200"}
    assert any(r[13] for r in sales_rows)  # отметка о возврате
    assert wb["Возвраты"].max_row == 2 and wb["Скупка"].max_row == 2


def test_report_custom_period_validation(tg):
    login(tg, ADMIN)
    tg.send(ADMIN, "Отчеты")
    tg.send(ADMIN, "Другой период")
    assert "Не понял" in tg.texts(tg.send(ADMIN, "32.13.2026"))
    tg.send(ADMIN, "01.10.2026")
    assert "раньше" in tg.texts(tg.send(ADMIN, "01.09.2026"))
    calls = tg.send(ADMIN, "Сегодня")
    assert "Отчёт за 01.10.2026" in tg.texts(calls)
    assert tg.documents


def test_daily_report_and_low_stock_jobs(tg):
    from utils.scheduled_jobs import send_daily_report, send_low_stock_notification
    login(tg, ADMIN)
    login(tg, MANAGER)
    tg.calls.clear()
    send_daily_report()
    assert [d.chat_id for d in tg.documents] == [ADMIN]
    send_low_stock_notification()
    low = [c for c in tg.calls if c.text and "Низкий остаток" in c.text]
    assert {c.chat_id for c in low} == {ADMIN, MANAGER}
    assert "A200" in low[0].text and "A300" not in low[0].text  # A300 без отслеживания


# ---------------------------------------------------------------- склад

def test_add_product_full_flow(tg):
    login(tg, MANAGER)
    tg.send(MANAGER, "Склад")
    tg.send(MANAGER, "Добавить товар")
    assert "уже есть" in tg.texts(tg.send(MANAGER, "A100"))
    answers = ["B500", "ЦУМ", "браслет", "Браслет Панцирь", "золото", "Пропустить", "7.8", "Пропустить",
               "350000", "300000", "2", "Пропустить", "Пропустить", "Да"]
    for a in answers:
        calls = tg.send(MANAGER, a)
    assert "Проверьте новый товар" in tg.texts(calls)
    assert "✅" in tg.texts(tg.send(MANAGER, "Подтвердить"))
    p = db_query("SELECT * FROM products WHERE article_number='B500'")[0]
    assert p['quantity'] == 2 and p['price'] == 350000 and p['manufacturer'] is None and p['track_stock'] == 1
    assert p['arrival_date'] is not None


def test_add_product_validates_min_price(tg):
    login(tg, ADMIN)
    tg.send(ADMIN, "Добавить товар")
    for a in ["C1", "ЦУМ", "кольцо", "Тест", "золото", "17", "2", "0", "100000"]:
        tg.send(ADMIN, a)
    assert "не может быть выше" in tg.texts(tg.send(ADMIN, "200000"))


def test_restock_and_stock_report(tg):
    login(tg, ADMIN)
    tg.send(ADMIN, "Пополнить остатки")
    tg.send(ADMIN, "A300")
    tg.send(ADMIN, "5")
    assert "✅" in tg.texts(tg.send(ADMIN, "Подтвердить"))
    assert db_query("SELECT quantity FROM products WHERE article_number='A300'")[0]['quantity'] == 5
    calls = tg.send(ADMIN, "Отчет об остатках")
    assert "ЦУМ: в наличии позиций — 2, всего 8 шт." in tg.texts(calls)
    wb = openpyxl.load_workbook(io.BytesIO(tg.documents[-1].data))
    assert wb.active.max_row == 4


# ---------------------------------------------------------------- персонал

def test_user_management(tg):
    login(tg, ADMIN)
    tg.send(ADMIN, "Персонал")
    tg.send(ADMIN, "Добавить пользователя")
    tg.send(ADMIN, "Айгерим Сапарова")
    assert "Некорректный" in tg.texts(tg.send(ADMIN, "12345"))
    tg.send(ADMIN, "8 701 555 44 33")
    assert "✅" in tg.texts(tg.send(ADMIN, "Кассир"))
    new = db_query("SELECT * FROM users WHERE name='Айгерим Сапарова'")[0]
    assert new['phone_number'] == '+77015554433' and new['role'] == 'cashier'

    # новый кассир входит, админ повышает его до менеджера
    tg.contact(2001, "77015554433")
    assert reply_buttons(tg.last(2001)) == ["Касса"]
    users = inline_buttons(tg.send(ADMIN, "Права"))
    me = next(d for t, d in users.items() if "Админ" in t)
    actions = inline_buttons(tg.press(ADMIN, me))
    assert "Деактивировать" not in actions  # себя деактивировать нельзя
    tg.press(ADMIN, f"um_user_{new['id']}")
    tg.press(ADMIN, f"um_role_{new['id']}")
    assert "✅" in tg.texts(tg.press(ADMIN, f"um_setrole_{new['id']}_manager"))
    assert "Склад" in tg.texts(tg.send(2001, "Склад")) or user_data[2001]['role'] == 'manager'
    assert user_data[2001]['role'] == 'manager'

    # деактивация сразу лишает доступа
    tg.press(ADMIN, f"um_deactok_{new['id']}")
    calls = tg.send(2001, "Касса")
    assert "поделитесь номером" in tg.texts(calls).lower()
    assert "деактивирован" in tg.texts(tg.contact(2001, "77015554433"))


def test_reads_see_fresh_data_on_every_pooled_connection(tg):
    """Регрессия: соединения из пула не должны держать старый снимок данных."""
    from database import get_user_by_phone, change_user_role_function
    user = get_user_by_phone(PHONES[CASHIER])
    for _ in range(25):  # прогреть все соединения пула чтением
        assert get_user_by_phone(PHONES[CASHIER])['role'] == 'cashier'
    ok, _ = change_user_role_function(user['id'], 'manager')
    assert ok
    roles = {get_user_by_phone(PHONES[CASHIER])['role'] for _ in range(25)}
    assert roles == {'manager'}


def test_role_change_applies_right_after_server_boot(tg, monkeypatch):
    """Регрессия: сброс сессии должен работать и в первые минуты после загрузки системы."""
    import auth
    monkeypatch.setattr(auth.time, "monotonic", lambda: 10.0)  # «система загрузилась 10 секунд назад»
    login(tg, CASHIER)
    user_id = user_data[CASHIER]['id']
    db_query("UPDATE users SET role = 'manager' WHERE id = %s", (user_id,))
    auth.invalidate_user_sessions(user_id)
    tg.send(CASHIER, "Склад")
    assert user_data[CASHIER]['role'] == 'manager'
