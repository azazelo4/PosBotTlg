"""Аналитика продаж (#29): тренды, структура, скидки, продавцы, время покупок, склад, металл, маржа, клиенты.

Одна выборка продаж за период (с фильтрами) — дальше всё считается в Python. Для ювелирного магазина это
тысячи строк в год: выборка занимает миллисекунды, а логику (ABC, раскладка смешанной оплаты, сравнения)
проще проверять тестами.

Продажи берутся по дате продажи, возвращённые позиции в выручку не входят (возвраты считаются отдельно).
Trade-In в продажах не участвует: в нём нет цены по ценнику и чека — он виден в разделе «Металл».
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from core.database import with_connection, _attach_payment_parts
from utils.common import day_bounds, today

STALE_DAYS = 90
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
PER_GRAM_METALS = ("золото", "серебро")
ZERO = Decimal(0)


class Filters:
    """Фильтры страницы: период и необязательные магазин, тип изделия, металл, продавец."""

    def __init__(self, start, end, store_id=None, product_type=None, metal=None, user_id=None):
        if end < start:
            start, end = end, start
        self.start, self.end = start, end
        self.store_id, self.product_type, self.metal, self.user_id = store_id, product_type, metal, user_id

    @property
    def days(self):
        return (self.end - self.start).days + 1

    def shifted(self, start, end):
        return Filters(start, end, self.store_id, self.product_type, self.metal, self.user_id)

    def previous(self):
        """Такой же по длине период прямо перед выбранным."""
        return self.shifted(self.start - timedelta(days=self.days), self.start - timedelta(days=1))

    def last_year(self):
        return self.shifted(_year_back(self.start), _year_back(self.end))

    def query(self):
        """Параметры для ссылок (Excel с теми же фильтрами)."""
        q = {"start": self.start.isoformat(), "end": self.end.isoformat()}
        for key, value in (("store", self.store_id), ("type", self.product_type), ("metal", self.metal),
                           ("seller", self.user_id)):
            if value:
                q[key] = value
        return q


def _year_back(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:                       # 29 февраля
        return d.replace(year=d.year - 1, day=28)


# ------------------------------------------------------------------ выборки

def _where(f, alias="s", date_col="sale_date", product=True, user=True):
    sql, params = [f"{alias}.{date_col} >= %s", f"{alias}.{date_col} < %s"], list(day_bounds(f.start, f.end))
    if f.store_id:
        sql.append(f"{alias}.store_id = %s")
        params.append(f.store_id)
    if product and f.product_type:
        sql.append("p.product_type = %s")
        params.append(f.product_type)
    if product and f.metal:
        sql.append("p.metal_type = %s")
        params.append(f.metal)
    if user and f.user_id:
        sql.append(f"{alias}.user_id = %s")
        params.append(f.user_id)
    return " AND ".join(sql), params


@with_connection
def sales_rows(cursor, f):
    """Продажи за период с фильтрами. Строка — одна штука товара."""
    where, params = _where(f)
    cursor.execute(f"""
        SELECT s.id, s.receipt_id, s.sale_date, s.sold_price, COALESCE(s.list_price, p.price) AS list_price,
               s.payment_type, s.store_id, st.name AS store, s.user_id, u.name AS user_name,
               p.id AS product_id, p.article_number, p.model, p.product_type, p.metal_type, p.fineness, p.weight,
               p.cost_price, r.id AS return_id, rc.customer_id, cu.name AS customer_name, cu.phone AS customer_phone
        FROM sales s
        JOIN products p ON p.id = s.product_id
        LEFT JOIN receipts rc ON rc.id = s.receipt_id
        LEFT JOIN customers cu ON cu.id = rc.customer_id
        LEFT JOIN stores st ON st.id = s.store_id
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN returns r ON r.original_sale_id = s.id
        WHERE {where}
        ORDER BY s.sale_date, s.id""", params)
    rows = cursor.fetchall()
    _attach_payment_parts(cursor, rows)
    return rows


@with_connection
def metal_rows(cursor, f):
    """Скупка и старые изделия из Trade-In за период: металл и граммы (фильтр по типу изделия к ним не относится)."""
    result = {}
    if f.product_type:
        return {"buybacks": [], "trade_ins": []}
    where, params = _where(f, "b", "buyback_date", product=False)
    metal = " AND b.metal_type = %s" if f.metal else ""
    cursor.execute(f"""SELECT b.metal_type AS metal, b.weight, b.buyback_price AS price FROM buybacks b
                       WHERE {where}{metal}""", params + ([f.metal] if f.metal else []))
    result['buybacks'] = cursor.fetchall()
    where, params = _where(f, "t", "trade_in_date", product=False)
    metal = " AND t.old_metal_type = %s" if f.metal else ""
    cursor.execute(f"""SELECT t.old_metal_type AS metal, t.old_weight AS weight, t.old_item_value AS price FROM trade_ins t
                       WHERE {where}{metal}""", params + ([f.metal] if f.metal else []))
    result['trade_ins'] = cursor.fetchall()
    return result


@with_connection
def stock_rows(cursor, f):
    """Текущий остаток (фильтры магазина, типа и металла) с датой последней продажи."""
    sql, params = ["p.quantity > 0", "p.track_stock = 1"], []
    for col, value in (("p.store_id", f.store_id), ("p.product_type", f.product_type), ("p.metal_type", f.metal)):
        if value:
            sql.append(f"{col} = %s")
            params.append(value)
    cursor.execute(f"""
        SELECT p.id, p.article_number, p.model, p.product_type, p.metal_type, p.weight, p.price, p.cost_price,
               p.quantity, p.arrival_date, st.name AS store,
               (SELECT MAX(s.sale_date) FROM sales s WHERE s.product_id = p.id) AS last_sale
        FROM products p LEFT JOIN stores st ON st.id = p.store_id
        WHERE {" AND ".join(sql)}
        ORDER BY p.arrival_date, p.id""", params)
    return cursor.fetchall()


@with_connection
def customer_history(cursor, f):
    """Вся история покупок клиентов из базы до конца периода (фильтр — только магазин): первая и последняя покупка,
    число чеков и сумма без возвращённых позиций. Плюс сколько клиентов в базе на конец периода."""
    end = day_bounds(f.start, f.end)[1]
    store = " AND s.store_id = %s" if f.store_id else ""
    cursor.execute(f"""
        SELECT rc.customer_id AS id, cu.name, cu.phone, cu.birthday, cu.created_at,
               MIN(s.sale_date) AS first_at, MAX(s.sale_date) AS last_at,
               COUNT(DISTINCT s.receipt_id) AS receipts, COUNT(*) AS qty, SUM(s.sold_price) AS total
        FROM sales s
        JOIN receipts rc ON rc.id = s.receipt_id
        JOIN customers cu ON cu.id = rc.customer_id
        LEFT JOIN returns r ON r.original_sale_id = s.id
        WHERE r.id IS NULL AND s.sale_date < %s{store}
        GROUP BY rc.customer_id, cu.name, cu.phone, cu.birthday, cu.created_at""",
                   [end] + ([f.store_id] if f.store_id else []))
    history = {row['id']: row for row in cursor.fetchall()}
    cursor.execute("SELECT COUNT(*) AS n FROM customers WHERE created_at < %s", (end,))
    return {"customers": history, "in_base": cursor.fetchone()['n']}


@with_connection
def returns_count(cursor, f):
    where, params = _where(f, "r", "return_date", product=f.product_type or f.metal)
    join = " JOIN sales s ON s.id = r.original_sale_id JOIN products p ON p.id = s.product_id" \
        if f.product_type or f.metal else ""
    cursor.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(r.return_price), 0) AS total FROM returns r{join} WHERE {where}",
                   params)
    return cursor.fetchone()


# ------------------------------------------------------------------ расчёты

def _d(value):
    return Decimal(str(value or 0))


def _pct(part, whole):
    return float(part) * 100 / float(whole) if whole else None


def _receipt_key(row):
    return ("r", row['receipt_id']) if row.get('receipt_id') else ("s", row['id'])


def _discount(row):
    return max(_d(row['list_price']) - _d(row['sold_price']), ZERO)


def summary(rows):
    """Ключевые цифры по проданным (не возвращённым) позициям."""
    sold = [r for r in rows if not r.get('return_id')]
    revenue = sum((_d(r['sold_price']) for r in sold), ZERO)
    receipts = len({_receipt_key(r) for r in sold})
    discounted = [r for r in sold if _discount(r) > 0]
    discount = sum((_discount(r) for r in sold), ZERO)
    listed = sum((_d(r['list_price']) for r in sold), ZERO)
    with_cost = [r for r in sold if r.get('cost_price') is not None]
    cost_revenue = sum((_d(r['sold_price']) for r in with_cost), ZERO)
    cost = sum((_d(r['cost_price']) for r in with_cost), ZERO)
    return {
        "revenue": revenue, "qty": len(sold), "receipts": receipts,
        "avg_check": revenue / receipts if receipts else ZERO,
        "returned": len(rows) - len(sold),
        "discount": discount, "discount_pct": _pct(discount, listed),
        "discounted_share": _pct(len(discounted), len(sold)),
        "avg_discount": discount / len(discounted) if discounted else ZERO,
        "margin": cost_revenue - cost if with_cost else None,
        "margin_pct": _pct(cost_revenue - cost, cost_revenue) if with_cost else None,
        "cost_coverage": _pct(len(with_cost), len(sold)),
    }


def change(current, previous):
    """Изменение в процентах; None, если сравнивать не с чем."""
    if previous is None or not previous:
        return None
    return (float(current) - float(previous)) * 100 / abs(float(previous))


def _bucket(f):
    if f.days <= 62:
        return "day"
    return "week" if f.days <= 370 else "month"


def _bucket_start(d, kind):
    if kind == "week":
        return d - timedelta(days=d.weekday())
    if kind == "month":
        return d.replace(day=1)
    return d


def _next_bucket(d, kind):
    if kind == "week":
        return d + timedelta(days=7)
    if kind == "month":
        return (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return d + timedelta(days=1)


def _label(d, kind):
    if kind == "month":
        return f"{MONTHS[d.month - 1]} {d.year}"
    return d.strftime("%d.%m")


def trend(f, rows, previous_rows=None):
    """Выручка, чеки и средний чек по дням (недолгий период), неделям или месяцам; рядом — прошлый период."""
    kind = _bucket(f)

    def series(flt, data):
        buckets, d = [], _bucket_start(flt.start, kind)
        while d <= flt.end:
            buckets.append(d)
            d = _next_bucket(d, kind)
        revenue, receipts = defaultdict(lambda: ZERO), defaultdict(set)
        for r in data:
            if r.get('return_id'):
                continue
            b = _bucket_start(r['sale_date'].date(), kind)
            revenue[b] += _d(r['sold_price'])
            receipts[b].add(_receipt_key(r))
        return buckets, [revenue[b] for b in buckets], [len(receipts[b]) for b in buckets]

    buckets, revenue, receipts = series(f, rows)
    result = {"kind": kind, "labels": [_label(b, kind) for b in buckets], "revenue": revenue, "receipts": receipts,
              "avg_check": [rv / n if n else ZERO for rv, n in zip(revenue, receipts)]}
    if previous_rows is not None:
        _, prev, _ = series(f.previous(), previous_rows)
        result["previous"] = (prev + [ZERO] * len(revenue))[:len(revenue)]
    return result


def _group(rows, key):
    acc = defaultdict(lambda: [ZERO, 0])
    for r in rows:
        if r.get('return_id'):
            continue
        k = key(r) or "—"
        acc[k][0] += _d(r['sold_price'])
        acc[k][1] += 1
    total = sum((v[0] for v in acc.values()), ZERO)
    return [{"name": k, "revenue": v[0], "count": v[1], "share": _pct(v[0], total)}
            for k, v in sorted(acc.items(), key=lambda kv: (-kv[1][0], str(kv[0])))]


def payments(rows):
    """Доли способов оплаты по сумме; смешанный чек раскладывается на части."""
    acc = defaultdict(lambda: ZERO)
    for r in rows:
        if r.get('return_id'):
            continue
        parts = r.get('payment_parts')
        if parts:
            for kind, amount in parts:
                acc[kind] += _d(amount)
        else:
            acc[r['payment_type']] += _d(r['sold_price'])
    total = sum(acc.values(), ZERO)
    return [{"name": k, "revenue": v, "share": _pct(v, total)} for k, v in sorted(acc.items(), key=lambda kv: -kv[1])]


def structure(rows):
    return {
        "types": _group(rows, lambda r: r['product_type']),
        "metals": _group(rows, lambda r: r['metal_type']),
        "fineness": _group(rows, lambda r: (f"{r['metal_type']} {r['fineness']}" if r.get('fineness') else None)),
        "stores": _group(rows, lambda r: r.get('store')),
        "payments": payments(rows),
    }


def _people(rows, key):
    acc = defaultdict(list)
    for r in rows:
        if not r.get('return_id'):
            acc[key(r) or "—"].append(r)
    out = []
    for name, items in acc.items():
        s = summary(items)
        out.append({"name": name, **s})
    return sorted(out, key=lambda x: -x['revenue'])


def sellers(rows):
    return _people(rows, lambda r: r.get('user_name'))


def discounts_by_type(rows):
    return _people(rows, lambda r: r['product_type'])


def heatmap(rows):
    """Чеки по дню недели и часу: {'hours': [...], 'cells': [[...] × 7], 'max': n}."""
    seen, cells = set(), defaultdict(int)
    for r in rows:
        key = _receipt_key(r)
        if r.get('return_id') or key in seen:
            continue
        seen.add(key)
        cells[(r['sale_date'].weekday(), r['sale_date'].hour)] += 1
    hours = [h for _, h in cells]
    first, last = (min(hours + [10]), max(hours + [20])) if hours else (10, 20)
    hour_range = list(range(first, last + 1))
    grid = [[cells.get((wd, h), 0) for h in hour_range] for wd in range(7)]
    return {"hours": hour_range, "days": WEEKDAYS, "cells": grid, "max": max(cells.values(), default=0)}


def top_and_abc(rows, top=10):
    """Топ артикулов по выручке и ABC-анализ: A — 80% выручки, B — следующие 15%, C — остальное."""
    acc = {}
    for r in rows:
        if r.get('return_id'):
            continue
        a = acc.setdefault(r['article_number'], {"article": r['article_number'], "model": r['model'],
                                                 "type": r['product_type'], "revenue": ZERO, "count": 0})
        a['revenue'] += _d(r['sold_price'])
        a['count'] += 1
    items = sorted(acc.values(), key=lambda a: (-a['revenue'], a['article']))
    total = sum((a['revenue'] for a in items), ZERO)
    classes = {c: {"count": 0, "revenue": ZERO} for c in "ABC"}
    running = ZERO
    for a in items:
        # класс по доле выручки ДО этого артикула: первый артикул всегда A, даже если он один даёт 90%
        share_before = float(running) * 100 / float(total) if total else 0
        a['abc'] = "A" if share_before < 80 else ("B" if share_before < 95 else "C")
        running += a['revenue']
        a['share'] = _pct(a['revenue'], total)
        classes[a['abc']]['count'] += 1
        classes[a['abc']]['revenue'] += a['revenue']
    for c in classes.values():
        c['share'] = _pct(c['revenue'], total)
    return {"top": items[:top], "all": items, "classes": classes}


def stock(f, rows, stock_items, moment=None):
    """Залежавшийся товар и запас в днях продаж по типам изделий."""
    day = moment or today()
    border = day - timedelta(days=STALE_DAYS)
    stale = [p for p in stock_items
             if p['arrival_date'] and p['arrival_date'] <= border
             and (p['last_sale'] is None or p['last_sale'].date() <= border)]
    for p in stale:
        p['days'] = (day - p['arrival_date']).days
        p['value'] = _d(p['price']) * p['quantity']
    sold = defaultdict(int)
    for r in rows:
        if not r.get('return_id'):
            sold[r['product_type']] += 1
    by_type = defaultdict(lambda: {"quantity": 0, "value": ZERO})
    for p in stock_items:
        t = by_type[p['product_type']]
        t['quantity'] += p['quantity']
        t['value'] += _d(p['price']) * p['quantity']
    turnover = []
    for name in sorted(set(by_type) | set(sold), key=lambda n: -by_type[n]['value']):
        per_day = sold[name] / f.days
        turnover.append({"name": name, "quantity": by_type[name]['quantity'], "value": by_type[name]['value'],
                         "sold": sold[name], "days_of_stock": round(by_type[name]['quantity'] / per_day) if per_day else None})
    return {"stale": stale, "stale_count": len(stale), "stale_value": sum((p['value'] for p in stale), ZERO),
            "stale_days": STALE_DAYS, "turnover": turnover}


def metal(rows, other):
    """Граммы проданного и принятого (скупка, Trade-In) металла и средняя цена за грамм."""
    def block(items, metal_key, price_key):
        acc = defaultdict(lambda: {"grams": 0.0, "amount": ZERO, "count": 0})
        for it in items:
            m = it.get(metal_key)
            if m not in PER_GRAM_METALS or not it.get('weight'):
                continue
            acc[m]['grams'] += float(it['weight'])
            acc[m]['amount'] += _d(it.get(price_key))
            acc[m]['count'] += 1
        return [{"metal": m, "grams": round(v['grams'], 2), "amount": v['amount'], "count": v['count'],
                 "per_gram": v['amount'] / Decimal(str(v['grams'])) if v['grams'] else None}
                for m, v in sorted(acc.items())]

    return {"sold": block([r for r in rows if not r.get('return_id')], 'metal_type', 'sold_price'),
            "buybacks": block(other['buybacks'], 'metal', 'price'),
            "trade_ins": block(other['trade_ins'], 'metal', 'price')}


def margin(rows):
    """Маржа по типам изделий — только позиции с заполненной себестоимостью."""
    acc = defaultdict(lambda: {"revenue": ZERO, "cost": ZERO, "count": 0})
    for r in rows:
        if r.get('return_id') or r.get('cost_price') is None:
            continue
        a = acc[r['product_type']]
        a['revenue'] += _d(r['sold_price'])
        a['cost'] += _d(r['cost_price'])
        a['count'] += 1
    out = [{"name": k, **v, "margin": v['revenue'] - v['cost'], "margin_pct": _pct(v['revenue'] - v['cost'], v['revenue'])}
           for k, v in acc.items()]
    return sorted(out, key=lambda x: -x['margin'])


# ------------------------------------------------------------------ клиенты

# Сегменты по истории покупок (RFM-упрощённо): давность последней покупки и число покупок.
SEGMENTS = (
    ("Постоянные", "3+ покупки, последняя — до 90 дней назад", "Беречь: персональные предложения, ранний доступ к новинкам"),
    ("Растущие", "2 покупки, последняя — до 90 дней назад", "Пригласить ещё раз — до статуса постоянного"),
    ("Новые", "Первая покупка — до 30 дней назад", "Поблагодарить, напомнить о себе через 2–3 недели"),
    ("Разовые", "1 покупка, 31–180 дней назад", "Повод вернуться: чистка изделия, подарок к празднику"),
    ("Под угрозой", "2+ покупки, последняя — 91–180 дней назад", "Позвонить или написать, пока не ушли к конкурентам"),
    ("Уснувшие", "Последняя покупка — больше 180 дней назад", "Акция «вернитесь к нам», поздравление с днём рождения"),
)


def segment_of(receipts, last_at, moment):
    """Сегмент клиента на дату moment по числу покупок и дате последней."""
    if not receipts or not last_at:
        return None
    days = (moment - (last_at.date() if hasattr(last_at, 'date') else last_at)).days
    if days > 180:
        return "Уснувшие"
    if days > 90:
        return "Под угрозой" if receipts >= 2 else "Разовые"
    if receipts >= 3:
        return "Постоянные"
    if receipts == 2:
        return "Растущие"
    return "Новые" if days <= 30 else "Разовые"


def clients(f, rows, history, moment=None):
    """Клиенты из базы: кто покупал в периоде, новые и вернувшиеся, топ по выручке, сегменты, что берут."""
    day = min(moment or today(), f.end)
    known = history['customers']
    sold = [r for r in rows if not r.get('return_id')]
    revenue_all = sum((_d(r['sold_price']) for r in sold), ZERO)
    receipts_all = {_receipt_key(r) for r in sold}
    by = {}
    for r in sold:
        cid = r.get('customer_id')
        if not cid:
            continue
        c = by.setdefault(cid, {"id": cid, "name": r.get('customer_name'), "phone": r.get('customer_phone'),
                                "revenue": ZERO, "receipts": set(), "qty": 0, "types": defaultdict(int), "last": None})
        c['revenue'] += _d(r['sold_price'])
        c['receipts'].add(_receipt_key(r))
        c['qty'] += 1
        c['types'][r['product_type'] or "—"] += 1
        c['last'] = max(c['last'], r['sale_date']) if c['last'] else r['sale_date']

    period_start = day_bounds(f.start, f.end)[0]
    buyers = []
    for c in by.values():
        h = known.get(c['id'], {})
        c['receipts'] = len(c['receipts'])
        c['avg_check'] = c['revenue'] / c['receipts'] if c['receipts'] else ZERO
        c['favorite'] = max(sorted(c['types']), key=lambda t: c['types'][t]) if c['types'] else None
        c['types'] = dict(c['types'])
        c['first_at'] = h.get('first_at')
        c['is_new'] = bool(c['first_at'] and c['first_at'] >= period_start)
        c['total'] = _d(h.get('total'))
        c['total_receipts'] = h.get('receipts') or c['receipts']
        c['segment'] = segment_of(h.get('receipts'), h.get('last_at'), day)
        buyers.append(c)
    buyers.sort(key=lambda c: (-c['revenue'], c['id']))

    known_revenue = sum((c['revenue'] for c in buyers), ZERO)
    known_receipts = sum(c['receipts'] for c in buyers)
    new = [c for c in buyers if c['is_new']]
    back = [c for c in buyers if not c['is_new']]

    segments = {name: {"name": name, "rule": rule, "action": action, "count": 0, "total": ZERO, "receipts": 0}
                for name, rule, action in SEGMENTS}
    everyone = []
    for h in known.values():
        name = segment_of(h['receipts'], h['last_at'], day)
        if not name:
            continue
        seg = segments[name]
        seg['count'] += 1
        seg['total'] += _d(h['total'])
        seg['receipts'] += h['receipts']
        everyone.append({**h, "segment": name, "total": _d(h['total']),
                         "days_since": (day - h['last_at'].date()).days})
    for seg in segments.values():
        seg['avg_check'] = seg['total'] / seg['receipts'] if seg['receipts'] else ZERO
        seg['share'] = _pct(seg['count'], len(everyone))
    everyone.sort(key=lambda c: (-c['total'], c['id']))

    # что берут клиенты из базы — по типам изделий
    types = defaultdict(lambda: {"revenue": ZERO, "count": 0, "people": set()})
    for r in sold:
        if r.get('customer_id'):
            t = types[r['product_type'] or "—"]
            t['revenue'] += _d(r['sold_price'])
            t['count'] += 1
            t['people'].add(r['customer_id'])
    what = [{"name": k, "revenue": v['revenue'], "count": v['count'], "people": len(v['people']),
             "share": _pct(v['revenue'], known_revenue)}
            for k, v in sorted(types.items(), key=lambda kv: (-kv[1]['revenue'], kv[0]))]

    # выручка по периодам: новые клиенты, вернувшиеся, без номера
    kind = _bucket(f)
    buckets, d = [], _bucket_start(f.start, kind)
    while d <= f.end:
        buckets.append(d)
        d = _next_bucket(d, kind)
    series = {k: defaultdict(lambda: ZERO) for k in ("new", "back", "anon")}
    status = {c['id']: ("new" if c['is_new'] else "back") for c in buyers}
    for r in sold:
        series[status.get(r.get('customer_id'), "anon")][_bucket_start(r['sale_date'].date(), kind)] += _d(r['sold_price'])

    return {
        "in_base": history['in_base'],
        "buyers": len(buyers), "new": len(new), "back": len(back),
        "new_revenue": sum((c['revenue'] for c in new), ZERO), "back_revenue": sum((c['revenue'] for c in back), ZERO),
        "revenue": known_revenue, "revenue_share": _pct(known_revenue, revenue_all),
        "receipts": known_receipts, "receipts_share": _pct(known_receipts, len(receipts_all)),
        "avg_check": known_revenue / known_receipts if known_receipts else ZERO,
        "anon_avg_check": ((revenue_all - known_revenue) / (len(receipts_all) - known_receipts)
                           if len(receipts_all) > known_receipts else ZERO),
        "per_buyer": known_revenue / len(buyers) if buyers else ZERO,
        "visits": known_receipts / len(buyers) if buyers else None,
        "repeat_share": _pct(sum(1 for c in buyers if c['total_receipts'] >= 2), len(buyers)),
        "top": buyers[:20], "buyers_list": buyers,
        "segments": list(segments.values()), "everyone": everyone,
        "what": what,
        "trend": {"labels": [_label(b, kind) for b in buckets],
                  **{k: [v[b] for b in buckets] for k, v in series.items()}},
    }


def build(f, moment=None):
    """Все разделы страницы. None — ошибка базы."""
    rows = sales_rows(f)
    prev_rows = sales_rows(f.previous())
    year_rows = sales_rows(f.last_year())
    other = metal_rows(f)
    stock_items = stock_rows(f)
    returned = returns_count(f)
    history = customer_history(f)
    if None in (rows, prev_rows, year_rows, other, stock_items, returned, history):
        return None
    cur, prev, year = summary(rows), summary(prev_rows), summary(year_rows)
    compare = {key: {"previous": change(cur[key], prev[key]), "year": change(cur[key], year[key])}
               for key in ("revenue", "receipts", "avg_check", "qty")}
    return {
        "filters": f, "summary": cur, "previous": prev, "year": year, "compare": compare,
        "returns": returned,
        "trend": trend(f, rows, prev_rows),
        "structure": structure(rows),
        "sellers": sellers(rows),
        "discount_types": discounts_by_type(rows),
        "heatmap": heatmap(rows),
        "abc": top_and_abc(rows),
        "stock": stock(f, rows, stock_items, moment),
        "metal": metal(rows, other),
        "margin": margin(rows),
        "clients": clients(f, rows, history, moment),
    }


def default_period(d=None):
    """По умолчанию — последние 30 дней."""
    d = d or today()
    return d - timedelta(days=29), d

