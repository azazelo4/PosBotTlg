"""Импорт истории продаж из Паломы: «Акт реализации» + «Отчёт по счетам»."""
import io
from datetime import timedelta

import openpyxl

from conftest import ADMIN, MANAGER, db_query
from core.database import get_period_operations, get_sales_for_return
from test_stores import _web, _csrf
from utils import paloma_sales
from utils.common import today, day_bounds

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
          "декабря"]
D1, D2 = today() - timedelta(days=5), today() - timedelta(days=4)


def _ru(d):
    return f"{d.day} {MONTHS[d.month - 1]} {d.year} г."


def _act(days):
    """HTML «.xls» Паломы: двухэтажная шапка, строки-даты с colspan, строки товаров."""
    head = """<table><thead><tr><th rowspan="2">&nbsp;</th><th rowspan="2">№ п/п</th><th rowspan="2">Код товара</th>
      <th rowspan="2">Внешний ID</th><th rowspan="2">Наименование</th><th rowspan="2">Артикул</th><th rowspan="2">Кол-во</th>
      <th colspan="2">Продажа</th><th colspan="3">Розница на конец периода</th><th rowspan="2">Обслуж.</th>
      <th rowspan="2">Скидка</th><th colspan="3">Отказы</th><th colspan="3">Возвраты</th><th colspan="3">Итог</th></tr>
      <tr><th>Цена</th><th>Сумма</th><th>Кол-во</th><th>Цена</th><th>Сумма</th><th>Кол-во</th><th>Цена</th><th>Сумма</th>
      <th>Кол-во</th><th>Цена</th><th>Сумма</th><th>Кол-во</th><th>Цена</th><th>Сумма</th></tr></thead><tbody>"""
    body = ""
    for n, (d, items) in enumerate(days, 1):
        body += f'<tr class="row-group"><td></td><td>{n}.</td><td colspan="30">Дата: {_ru(d)}</td></tr>'
        for code, name, qty, total, rq, rs in items:
            cells = ["&nbsp;", f"{n}.1.", code, "", name, "2-7000", qty, total // max(qty, 1), f"{total:,}".replace(",", " "),
                     0, 0, 0, 0, 0, 0, 0, 0, rq, rs, f"{rs:,}".replace(",", " "), qty - rq, 0, total - rs]
            body += "<tr>" + "".join(f'<td><span class="sum">{c}</span></td>' for c in cells) + "</tr>"
        body += '<tr class="row-resume"><td>&nbsp;</td><td colspan="5">Итого за дату:</td></tr>'
    return (f"<html><body><table><tr><td>Отчет:</td><td>Акт реализации</td></tr></table>{head}{body}"
            "</tbody></table></body></html>").encode()


def _checks(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Отчет:", None, "Отчет по счетам"])
    ws.append([])
    ws.append(["№ п/п", "№ счета", "Сотрудник", "Торговый объект", "Рабочее место", "Стол", "Клиент", "Примечание к счету",
               "Баланс клиента", "Дата открытия", "Дата закрытия", "Гостей", "Без скидки", "Обслуживание", "Скидка",
               "К оплате", "Оплата - Наличными", "Оплата - Картой"])
    for i, (no, seller, store, moment, cash, card) in enumerate(rows, 1):
        ws.append([i, no, seller, store, "Рабочее место", None, "Частное лицо", None, None, moment, moment, 1,
                   cash + card, 0, 0, cash + card, cash, card])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _files():
    act = _act([
        (D1, [("A100", "Кольцо Классика", 1, 121000, 0, 0),
              ("A200", "Цепь Бисмарк", 1, 115000, 0, 0),
              ("A300", "Серьги Капля", 2, 300000, 0, 0)]),
        (D2, [("A100", "Кольцо Классика", 0, 0, 1, 121000),               # возврат кольца, проданного вчера
              ("00009999", "с-ги пусеты клевер с ф. в 2,06 ярко", 1, 152000, 0, 0)]),   # нет в каталоге
    ])
    t = lambda d, hm: f"{d:%Y-%m-%d} {hm}:00"                                       # noqa: E731
    checks = _checks([
        (1, "Кассир", "Магазин ЦУМ", t(D1, "11:00"), 121000, 0),                   # A100
        (2, "Айгуль", "Магазин ЦУМ", t(D1, "12:00"), 0, 265000),                   # A200 + A300
        (3, "Айгуль", "Магазин ЦУМ", t(D1, "13:00"), 50000, 100000),                 # A300, смешанная оплата
        (1, "Айгуль", "Магазин ЦУМ", t(D2, "10:00"), -121000, 0),                    # возврат A100
        (2, "Кассир", "Магазин ЦУМ", t(D2, "15:00"), 152000, 0),                   # новое изделие
    ])
    return [(act, "1795615542.xls"), (checks, "ps.xlsx")]


def test_parse_and_match():
    files = _files()
    act = paloma_sales.parse_act(paloma_sales.read_rows(*files[0]))
    assert [(a['code'], a['qty'], int(a['sum']), a['ret_qty']) for a in act][:3] == [
        ("A100", 1, 121000, 0), ("A200", 1, 115000, 0), ("A300", 2, 300000, 0)]
    checks = paloma_sales.parse_checks(paloma_sales.read_rows(*files[1]))
    assert len(checks) == 5 and checks[2]['cash'] == 50000 and checks[2]['card'] == 100000
    assert paloma_sales.match(act, checks) == []
    assert [sorted(c for c, _ in ch['items']) for ch in checks] == [["A100"], ["A200", "A300"], ["A300"], ["A100"],
                                                                     ["00009999"]]
    assert all(ch['exact'] for ch in checks)


def test_import_sales_history(tg):
    qty_before = {r['article_number']: r['quantity'] for r in db_query("SELECT article_number, quantity FROM products")}
    plan = paloma_sales.build_plan(_files())
    assert not plan.errors, plan.errors
    assert plan.stores == {"Магазин ЦУМ": (1, "ЦУМ")}
    assert plan.sellers["Кассир"][1] == "Кассир" and plan.sellers["Айгуль"] == (None, "Айгуль (Палома)")
    assert plan.stats['checks'] == 4 and plan.stats['returns'] == 1 and list(plan.missing) == ["00009999"]
    admin = db_query("SELECT id FROM users WHERE role='admin'")[0]['id']
    ok, result = paloma_sales.apply_plan(plan, admin)
    assert ok and result == {"receipts": 4, "sales": 5, "returns": 1, "unlinked_returns": 0, "products": 1}, result

    # остатки не меняются (новое изделие — с остатком 0), фискализации нет
    after = {r['article_number']: r['quantity'] for r in db_query("SELECT article_number, quantity FROM products")}
    assert {k: after[k] for k in qty_before} == qty_before and after["00009999"] == 0
    assert db_query("SELECT product_type, weight FROM products WHERE article_number='00009999'")[0] == \
        {"product_type": "серьги", "weight": 2.06}
    assert db_query("SELECT COUNT(*) AS n FROM fiscal_receipts")[0]['n'] == 0
    # чеки: время, продавец, оплата
    rows = db_query("""SELECT r.created_at, r.total, r.payment_type, u.name, u.is_active FROM receipts r
                       JOIN users u ON u.id = r.user_id WHERE r.source = 'paloma' ORDER BY r.created_at""")
    assert [(int(r['total']), r['payment_type'], r['name']) for r in rows] == [
        (121000, "Наличные", "Кассир"), (265000, "Безналичные", "Айгуль (Палома)"),
        (150000, "Смешанная", "Айгуль (Палома)"), (152000, "Наличные", "Кассир")]
    assert rows[1]['is_active'] == 0                                   # «Айгуль (Палома)» — без доступа к боту
    mixed = db_query("""SELECT p.payment_type, p.amount FROM receipt_payments p JOIN receipts r ON r.id = p.receipt_id
                        WHERE r.total = 150000 ORDER BY p.id""")
    assert [(m['payment_type'], int(m['amount'])) for m in mixed] == [("Наличные", 50000), ("Безналичные", 100000)]
    ret = db_query("""SELECT r.return_price, s.sale_date FROM returns r JOIN sales s ON s.id = r.original_sale_id
                      WHERE r.source = 'paloma'""")
    assert len(ret) == 1 and int(ret[0]['return_price']) == 121000 and ret[0]['sale_date'].date() == D1

    # отчёты — как обычные продажи; итоги смены и досылка отчётов — без импорта
    ops = get_period_operations(*day_bounds(D1, D2))
    assert len(ops['sales']) == 5 and len(ops['returns']) == 1
    assert not any(get_period_operations(*day_bounds(D1, D2), include_imported=False).values())
    # продажа из Паломы находится для возврата
    found = get_sales_for_return("A300")
    assert len(found) == 2 and all(f['return_id'] is None for f in found)

    # повторная загрузка — дубли не создаются
    again = paloma_sales.build_plan(_files())
    assert "Загружать нечего." in again.errors
    assert db_query("SELECT COUNT(*) AS n FROM sales")[0]['n'] == 5


def test_sales_import_web(tg):
    manager = _web(tg, MANAGER)
    assert manager.get("/sales-import").status_code == 403
    admin = _web(tg, ADMIN)
    page = admin.get("/sales-import")
    assert page.status_code == 200 and "Акт реализации" in page.text
    csrf = _csrf(admin, "/sales-import")
    (act, an), (checks, cn) = _files()
    r = admin.post("/sales-import", data={"csrf": csrf},
                   files=[("files", (an, act, "application/vnd.ms-excel")), ("files", (cn, checks, "application/octet-stream"))])
    assert "Чеков продажи" in r.text and "Айгуль (Палома)" in r.text and "Будут добавлены в каталог" in r.text
    plan_id = r.text.split('action="/sales-import/')[1].split('"')[0]
    r = admin.post(f"/sales-import/{plan_id}", data={"csrf": csrf})
    assert "Загружено из Паломы: чеков 4, продаж 5, возвратов 1" in r.text
    # один файл — понятная ошибка
    r = admin.post("/sales-import", data={"csrf": csrf}, files=[("files", (cn, checks, "application/octet-stream"))])
    assert "Нужен «Акт реализации»" in r.text
