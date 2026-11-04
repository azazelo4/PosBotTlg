"""Отчёт по кассе за период: сводка в чат + Excel со всеми операциями."""
import io
import logging
from collections import defaultdict
from decimal import Decimal

import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter

from buttons import PAYMENT_TYPES
from database import get_period_operations
from utils.common import day_bounds, money, h

logger = logging.getLogger(__name__)

D0 = Decimal(0)


def _d(value):
    return Decimal(str(value)) if value is not None else D0


def build_summary(ops):
    """Считает итоги. Движение денег по способам оплаты: + поступления, − выплаты."""
    sales = ops['sales']
    returns, buybacks, trade_ins, withdrawals = ops['returns'], ops['buybacks'], ops['trade_ins'], ops['withdrawals']

    flow = defaultdict(lambda: D0)
    sales_by_payment = defaultdict(lambda: [0, D0])
    for s in sales:
        flow[s['payment_type']] += _d(s['sold_price'])
        sales_by_payment[s['payment_type']][0] += 1
        sales_by_payment[s['payment_type']][1] += _d(s['sold_price'])
    for r in returns:
        flow[r['payment_type']] -= _d(r['return_price'])
    for b in buybacks:
        flow[b['payment_type']] -= _d(b['buyback_price'])
    tradein_in = tradein_out = D0
    for t in trade_ins:
        diff = _d(t['price_difference'])
        if diff and t['payment_type_difference']:
            flow[t['payment_type_difference']] += diff
        if diff > 0:
            tradein_in += diff
        else:
            tradein_out -= diff
    withdrawn = sum((_d(w['amount']) for w in withdrawals), D0)

    sales_total = sum((_d(s['sold_price']) for s in sales), D0)
    returns_total = sum((_d(r['return_price']) for r in returns), D0)
    return {
        'sales_count': len(sales),
        'sales_total': sales_total,
        'sales_avg': (sales_total / len(sales)) if sales else D0,
        'sales_by_payment': dict(sales_by_payment),
        'returns_count': len(returns),
        'returns_total': returns_total,
        'net_sales': sales_total - returns_total,
        'buybacks_count': len(buybacks),
        'buybacks_total': sum((_d(b['buyback_price']) for b in buybacks), D0),
        'trade_ins_count': len(trade_ins),
        'tradein_in': tradein_in,
        'tradein_out': tradein_out,
        'withdrawals_count': len(withdrawals),
        'withdrawals_total': withdrawn,
        'flow': dict(flow),
        'cash_balance': flow.get('Наличные', D0) - withdrawn,
    }


def summary_text(summary, start, end):
    period = start.strftime('%d.%m.%Y') if start == end else f"{start:%d.%m.%Y} — {end:%d.%m.%Y}"
    lines = [f"<b>Отчёт за {period}</b>", ""]
    lines.append(f"Продажи: {summary['sales_count']} шт. на <b>{money(summary['sales_total'])}</b>")
    for pt, (cnt, total) in sorted(summary['sales_by_payment'].items()):
        lines.append(f"  • {h(pt)}: {cnt} шт., {money(total)}")
    if summary['returns_count']:
        lines.append(f"Возвраты: {summary['returns_count']} шт. на {money(summary['returns_total'])}")
        lines.append(f"Продажи за вычетом возвратов: <b>{money(summary['net_sales'])}</b>")
    if summary['buybacks_count']:
        lines.append(f"Скупка: {summary['buybacks_count']} шт. на {money(summary['buybacks_total'])}")
    if summary['trade_ins_count']:
        lines.append(f"Trade-In: {summary['trade_ins_count']} шт., доплаты клиентов {money(summary['tradein_in'])}, "
                     f"выплаты клиентам {money(summary['tradein_out'])}")
    if summary['withdrawals_count']:
        lines.append(f"Инкассация: {summary['withdrawals_count']} раз на {money(summary['withdrawals_total'])}")
    lines += ["", "<b>Движение денег</b>"]
    for pt in PAYMENT_TYPES + sorted(set(summary['flow']) - set(PAYMENT_TYPES)):
        if pt in summary['flow'] or pt in PAYMENT_TYPES:
            lines.append(f"  • {h(pt)}: {money(summary['flow'].get(pt, D0))}")
    lines.append(f"Наличные в кассе за период (после инкассации): <b>{money(summary['cash_balance'])}</b>")
    return "\n".join(lines)


HEADER_FONT = Font(bold=True)
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")


def _sheet(wb, title, headers, rows):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    for row in rows:
        ws.append([_cell(v) for v in row])
    if not rows:
        ws.append(["Нет операций за период"])
    for i, header in enumerate(headers, start=1):
        width = max([len(str(header))] + [len(str(_cell(r[i - 1]) or '')) for r in rows[:200]] + [8])
        ws.column_dimensions[get_column_letter(i)].width = min(width + 2, 45)
    ws.freeze_panes = "A2"
    return ws


def _cell(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


def build_excel(ops, summary, start, end):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Сводка"
    period = f"{start:%d.%m.%Y}" if start == end else f"{start:%d.%m.%Y} — {end:%d.%m.%Y}"
    rows = [
        ("Период", period),
        (),
        ("Продажи, шт.", summary['sales_count']),
        ("Продажи, сумма", summary['sales_total']),
        ("Средний чек", summary['sales_avg'].quantize(Decimal('1'))),
        ("Возвраты, шт.", summary['returns_count']),
        ("Возвраты, сумма", summary['returns_total']),
        ("Продажи за вычетом возвратов", summary['net_sales']),
        ("Скупка, шт.", summary['buybacks_count']),
        ("Скупка, сумма", summary['buybacks_total']),
        ("Trade-In, шт.", summary['trade_ins_count']),
        ("Trade-In: доплаты клиентов", summary['tradein_in']),
        ("Trade-In: выплаты клиентам", summary['tradein_out']),
        ("Инкассация, сумма", summary['withdrawals_total']),
        (),
        ("Продажи по способу оплаты", "Количество", "Сумма"),
    ]
    rows += [(pt, cnt, total) for pt, (cnt, total) in sorted(summary['sales_by_payment'].items())]
    rows += [(), ("Движение денег по способу оплаты", "Сумма")]
    rows += [(pt, summary['flow'].get(pt, D0)) for pt in sorted(set(PAYMENT_TYPES) | set(summary['flow']))]
    rows += [("Наличные в кассе после инкассации", summary['cash_balance'])]
    for row in rows:
        ws.append([_cell(v) for v in row])
    for row in ws.iter_rows():
        if row and row[0].value in ("Продажи по способу оплаты", "Движение денег по способу оплаты", "Период"):
            for cell in row:
                cell.font = HEADER_FONT
    ws.column_dimensions['A'].width = 38
    ws.column_dimensions['B'].width = 18
    ws.column_dimensions['C'].width = 18

    _sheet(wb, "Продажи",
           ["№", "Дата", "Артикул", "Тип", "Модель", "Размер", "Металл", "Вес, г", "Цена по прайсу",
            "Сумма продажи", "Оплата", "Магазин", "Продавец", "Возврат"],
           [(s['id'], s['sale_date'], s['article_number'], s['product_type'], s['model'], s['size'], s['metal_type'],
             s['weight'], s['list_price'], s['sold_price'], s['payment_type'], s['store'], s['user_name'],
             f"№{s['return_id']}" if s['return_id'] else "") for s in ops['sales']])
    _sheet(wb, "Возвраты",
           ["№", "Дата", "Продажа №", "Дата продажи", "Артикул", "Модель", "Сумма продажи", "Сумма возврата", "Выплата", "Сотрудник"],
           [(r['id'], r['return_date'], r['original_sale_id'], r['sale_date'], r['product_article'], r['model'],
             r['sold_price'], r['return_price'], r['payment_type'], r['user_name']) for r in ops['returns']])
    _sheet(wb, "Скупка",
           ["№", "Дата", "Артикул", "Описание", "Металл", "Вес, г", "Сумма", "Выплата", "Сотрудник"],
           [(b['id'], b['buyback_date'], b['product_article'] or "", b['description'], b['metal_type'], b['weight'],
             b['buyback_price'], b['payment_type'], b['user_name']) for b in ops['buybacks']])
    _sheet(wb, "Trade-In",
           ["№", "Дата", "Сдано: артикул", "Сдано: описание", "Металл", "Вес, г", "Оценка", "Новый: артикул",
            "Новый: модель", "Цена нового", "Доплата (+ клиент, − мы)", "Оплата доплаты", "Сотрудник"],
           [(t['id'], t['trade_in_date'], t['old_product_article'] or "", t['old_item_description'], t['old_metal_type'],
             t['old_weight'], t['old_item_value'], t['new_product_article'], t['new_model'], t['new_sale_price'],
             t['price_difference'], t['payment_type_difference'], t['user_name']) for t in ops['trade_ins']])
    _sheet(wb, "Инкассация",
           ["№", "Дата", "Сумма", "Комментарий", "Сотрудник"],
           [(w['id'], w['withdrawal_date'], w['amount'], w['comment'], w['user_name']) for w in ops['withdrawals']])

    for sheet in wb.worksheets[1:]:
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                if hasattr(cell.value, 'hour'):
                    cell.number_format = 'DD.MM.YYYY HH:MM'

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def generate_period_report(start, end):
    """Возвращает (текст сводки, Excel BytesIO, имя файла) или None при ошибке БД."""
    start_dt, end_dt = day_bounds(start, end)
    ops = get_period_operations(start_dt, end_dt)
    if ops is None:
        return None
    summary = build_summary(ops)
    excel = build_excel(ops, summary, start, end)
    name = f"report_{start:%Y%m%d}.xlsx" if start == end else f"report_{start:%Y%m%d}_{end:%Y%m%d}.xlsx"
    return summary_text(summary, start, end), excel, name
