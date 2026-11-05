"""Отчёт об остатках: Excel + итоги по магазинам (для бота и веб-админки)."""
import io
from collections import defaultdict
from decimal import Decimal

import openpyxl
from openpyxl.styles import Font

HEADERS = ['Магазин', 'Артикул', 'Тип', 'Модель', 'Металл', 'Проба', 'Размер', 'Вставки', 'Вес, г', 'Остаток, шт',
           'Цена', 'Мин. цена', 'Сумма по цене', 'Производитель', 'Дата поступления', 'Комментарий']
WIDTHS = (12, 14, 14, 30, 11, 7, 8, 18, 8, 10, 12, 12, 14, 18, 14, 30)


def stock_totals(items):
    """магазин -> (позиций в наличии, штук, сумма по цене)"""
    totals = defaultdict(lambda: [0, 0, Decimal(0)])
    for it in items:
        qty = it['quantity'] or 0
        t = totals[it['store_name']]
        t[0] += 1 if qty > 0 else 0
        t[1] += qty
        t[2] += Decimal(str(it['price'] or 0)) * qty
    return {k: tuple(v) for k, v in sorted(totals.items())}


def build_stock_excel(items):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Остатки"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for it in items:
        qty = it['quantity'] or 0
        ws.append([it['store_name'], it['article_number'], it['product_type'], it['model'], it['metal_type'],
                   it.get('fineness'), it['size'], it.get('inserts'), it['weight'], qty, float(it['price'] or 0),
                   float(it['min_price'] or 0), float((it['price'] or 0) * qty), it['manufacturer'],
                   it['arrival_date'], it['commentaries']])
    date_col = HEADERS.index('Дата поступления') + 1
    for row in ws.iter_rows(min_row=2, min_col=date_col, max_col=date_col):
        for cell in row:
            cell.number_format = 'DD.MM.YYYY'
    for i, width in enumerate(WIDTHS, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
