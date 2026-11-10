"""Выгрузка страницы «Аналитика» в Excel: лист на каждый раздел, с теми же фильтрами."""
import io
from decimal import Decimal

import openpyxl

from utils.report_generator import _sheet, _cell, HEADER_FONT


def _r(value, digits=1):
    return round(value, digits) if value is not None else None


def _money(value):
    return Decimal(str(value)).quantize(Decimal('1')) if value is not None else None


def build_analytics_excel(data, filter_titles):
    s, cmp_ = data['summary'], data['compare']
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Сводка"
    rows = [(title, value) for title, value in filter_titles] + [
        (),
        ("Показатель", "Значение", "К прошлому периоду, %", "К прошлому году, %"),
        ("Выручка", s['revenue'], _r(cmp_['revenue']['previous']), _r(cmp_['revenue']['year'])),
        ("Чеков", s['receipts'], _r(cmp_['receipts']['previous']), _r(cmp_['receipts']['year'])),
        ("Средний чек", _money(s['avg_check']), _r(cmp_['avg_check']['previous']), _r(cmp_['avg_check']['year'])),
        ("Продано, шт.", s['qty'], _r(cmp_['qty']['previous']), _r(cmp_['qty']['year'])),
        ("Возвращено из проданных, шт.", s['returned']),
        ("Скидки, сумма", s['discount']),
        ("Скидки, % от цены по ценнику", _r(s['discount_pct'])),
        ("Продаж со скидкой, %", _r(s['discounted_share'])),
        ("Средняя скидка", _money(s['avg_discount'])),
        ("Маржа (где есть себестоимость)", s['margin']),
        ("Маржа, %", _r(s['margin_pct'])),
        ("Продаж с себестоимостью, %", _r(s['cost_coverage'])),
    ]
    for row in rows:
        ws.append([_cell(v) for v in row])
    for row in ws.iter_rows():
        if row and row[0].value == "Показатель":
            for cell in row:
                cell.font = HEADER_FONT
    for col, width in zip("ABCD", (34, 18, 22, 22)):
        ws.column_dimensions[col].width = width

    t = data['trend']
    _sheet(wb, "Динамика", ["Период", "Выручка", "Чеков", "Средний чек", "Прошлый период, выручка"],
           [(label, rev, n, _money(avg), prev) for label, rev, n, avg, prev
            in zip(t['labels'], t['revenue'], t['receipts'], t['avg_check'], t.get('previous', [None] * len(t['labels'])))])

    st = data['structure']
    rows = []
    for title, key in (("Тип изделия", "types"), ("Металл", "metals"), ("Проба", "fineness"), ("Магазин", "stores"),
                       ("Оплата", "payments")):
        rows += [(title, g['name'], g.get('count'), g['revenue'], _r(g['share'])) for g in st[key]]
    _sheet(wb, "Структура", ["Разрез", "Значение", "Шт.", "Выручка", "Доля, %"], rows)

    _sheet(wb, "Продавцы", ["Продавец", "Выручка", "Чеков", "Средний чек", "Шт.", "Скидки", "Скидка, %",
                            "Со скидкой, %", "Средняя скидка"],
           [(p['name'], p['revenue'], p['receipts'], _money(p['avg_check']), p['qty'], p['discount'],
             _r(p['discount_pct']), _r(p['discounted_share']), _money(p['avg_discount'])) for p in data['sellers']])
    _sheet(wb, "Скидки по типам", ["Тип изделия", "Шт.", "Скидки", "Скидка, %", "Со скидкой, %", "Средняя скидка"],
           [(p['name'], p['qty'], p['discount'], _r(p['discount_pct']), _r(p['discounted_share']),
             _money(p['avg_discount'])) for p in data['discount_types']])

    h = data['heatmap']
    _sheet(wb, "Когда покупают", ["День"] + [f"{hour}:00" for hour in h['hours']],
           [(day, *cells) for day, cells in zip(h['days'], h['cells'])])

    _sheet(wb, "ABC", ["Артикул", "Модель", "Тип", "Шт.", "Выручка", "Доля, %", "Класс"],
           [(a['article'], a['model'], a['type'], a['count'], a['revenue'], _r(a['share']), a['abc'])
            for a in data['abc']['all']])

    stock = data['stock']
    _sheet(wb, "Залежавшийся товар", ["Артикул", "Модель", "Тип", "Магазин", "Поступил", "Дней на складе",
                                      "Последняя продажа", "Остаток", "На сумму"],
           [(p['article_number'], p['model'], p['product_type'], p['store'], p['arrival_date'], p['days'],
             p['last_sale'], p['quantity'], p['value']) for p in stock['stale']])
    _sheet(wb, "Оборачиваемость", ["Тип изделия", "Остаток, шт.", "Остаток по ценнику", "Продано за период, шт.",
                                   "Запас, дней продаж"],
           [(t['name'], t['quantity'], t['value'], t['sold'], t['days_of_stock']) for t in stock['turnover']])

    m = data['metal']
    rows = []
    for title, key in (("Продано", "sold"), ("Скупка", "buybacks"), ("Принято в Trade-In", "trade_ins")):
        rows += [(title, x['metal'], x['count'], x['grams'], x['amount'], _money(x['per_gram'])) for x in m[key]]
    _sheet(wb, "Металл", ["Операция", "Металл", "Шт.", "Граммы", "Сумма", "Цена за грамм"], rows)

    _sheet(wb, "Маржа", ["Тип изделия", "Шт.", "Выручка", "Себестоимость", "Маржа", "Маржа, %"],
           [(x['name'], x['count'], x['revenue'], x['cost'], x['margin'], _r(x['margin_pct'])) for x in data['margin']])

    for sheet in wb.worksheets[1:]:
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if hasattr(cell.value, 'hour'):
                    cell.number_format = 'DD.MM.YYYY HH:MM'
                elif hasattr(cell.value, 'day'):
                    cell.number_format = 'DD.MM.YYYY'
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
