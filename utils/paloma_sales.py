"""Импорт истории продаж из Паломы.

Нужны два отчёта Паломы:
- «Акт реализации» — что продано: по дням, код товара, количество, сумма, возвраты;
- «Отчёт по счетам» — чеки: время, сотрудник, торговый объект, оплата наличными / картой (возврат — сумма с минусом).

Отдельно ни один не годится: в акте нет времени, продавца и оплаты, в отчёте по счетам — изделий. Изделия раскладываются
по чекам того же дня по сумме: чек на 121 000 — изделие за 121 000; чек на 270 000 — два изделия на эту сумму. Что не
сошлось по сумме, раскладывается по оставшимся чекам дня, а цены позиций подгоняются под сумму чека — итоги дня, чеков
и способов оплаты совпадают с Паломой до тенге.

Загруженное помечается source = 'paloma': остатки не меняются, в Webkassa не отправляется, в «наличные в кассе» и
итоги смены сегодняшнего дня не попадает; в отчётах, аналитике и поиске продажи для возврата — как обычные продажи.
"""
import io
import itertools
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal
from html.parser import HTMLParser

import openpyxl

from core.database import with_transaction, with_connection, BusinessError, MIXED_PAYMENT

logger = logging.getLogger(__name__)

SOURCE = 'paloma'
CASH, CARD = "Наличные", "Безналичные"
MONTHS = {m: i + 1 for i, m in enumerate("января февраля марта апреля мая июня июля августа сентября октября ноября "
                                         "декабря".split())}
MAX_SUBSET = 4          # чек из скольких изделий ещё ищем точным подбором суммы
MAX_FILE = 30 * 1024 * 1024
# тип изделия по началу названия Паломы («к-цо …», «с-ги …») — для товаров, которых нет в каталоге
PREFIX_TYPE = [
    (r"^(к-цо|кольцо|кольца|колцо|к-о|к-ко|обр|об-ка)", "кольцо"), (r"^(с-ги|c-ги|серьги|пусеты|сатуар)", "серьги"),
    (r"^(печ-ка|печ-а|печатка)", "печатка"), (r"^цепь", "цепь"), (r"^колье", "колье"),
    (r"^(под-ка|п-ка|подвеска|крест|икона|шарм|мусульм)", "подвеска"), (r"^(браслет|б-т|бр-т)", "браслет"),
    (r"^блезык", "блезык"), (r"^бусы", "бусы"), (r"^брошь", "брошь"), (r"^(бул-ка|булавка)", "булавка"),
    (r"^ложка", "ложка"), (r"^ионизатор", "ионизатор"), (r"^часы", "часы"), (r"^набор", "набор"),
]
WEIGHT = re.compile(r"(?:^|[\s.,(])в\.?\s*(\d+(?:[.,]\d+)?)", re.I)


def guess_product(code, name, price):
    """Карточка товара из строки акта: тип по названию, вес «в 2,05», остаток 0."""
    low = name.lower()
    ptype = next((t for rx, t in PREFIX_TYPE if re.search(rx, low)), None)
    w = WEIGHT.search(name)
    weight = float(w.group(1).replace(",", ".")) if w else 0
    return {"article_number": code, "model": name or code, "product_type": ptype or "кольцо",
            "metal_type": "серебро" if "сер" in low.split()[:3] else "золото",
            "weight": weight if 0 < weight < 500 else 0, "price": price, "min_price": price,
            "commentaries": "Палома: добавлен при импорте продаж" + ("" if ptype else "; тип не распознан — проверьте")}


# ---------------------------------------------------------------- чтение отчётов (HTML «.xls» Паломы или .xlsx)

class _Table(HTMLParser):
    """Строки HTML-таблиц: [(теги ячеек th/td, colspan, rowspan, текст)]."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            a = dict(attrs)
            self._cell = [tag, int(a.get('colspan') or 1), int(a.get('rowspan') or 1), []]

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self._cell is not None and self._row is not None:
            self._cell[3] = re.sub(r"\s+", " ", "".join(self._cell[3]).replace("\xa0", " ")).strip()
            self._row.append(tuple(self._cell))
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[3].append(data)


def _html_rows(text):
    """Таблица → строки значений; двухэтажная шапка (colspan/rowspan) разворачивается в «Продажа / Сумма»."""
    parser = _Table()
    parser.feed(text)
    out, pending = [], {}            # pending: колонка → (оставшиеся строки, текст) для rowspan шапки
    header_top = None
    for row in parser.rows:
        is_header = all(c[0] == 'th' for c in row)
        values, col = [], 0
        cells = list(row)
        while cells or col in pending:
            if col in pending:
                left, text = pending[col]
                values.append(text)
                pending[col] = (left - 1, text)
                if left - 1 <= 0:
                    del pending[col]
                col += 1
                continue
            tag, colspan, rowspan, text = cells.pop(0)
            for k in range(colspan):
                values.append(text if (is_header or k == 0) else "")
                if is_header and rowspan > 1:
                    pending[col] = (rowspan - 1, text)
                col += 1
        if is_header and header_top is not None and len(values) == len(header_top):
            values = [a if a == b else f"{a} / {b}" for a, b in zip(header_top, values)]
            out[-1] = values
            header_top = None
            continue
        header_top = values if is_header else None
        out.append(values)
    return out


def read_rows(data: bytes, filename: str):
    name = (filename or "").lower()
    if len(data) > MAX_FILE:
        raise ValueError("Файл больше 30 МБ.")
    if name.endswith((".xlsx", ".xlsm")):
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = [["" if v is None else v for v in r] for r in wb.active.iter_rows(values_only=True)]
        wb.close()
        return rows
    for enc in ("utf-8", "cp1251"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Не удалось прочитать файл.")
    if "<table" not in text.lower():
        raise ValueError("Это не отчёт Паломы: нужен файл .xls (как выгружает Палома) или .xlsx.")
    return _html_rows(text)


def _num(value):
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    s = re.sub(r"[\s\xa0₸]", "", str(value)).replace(",", ".")
    try:
        return Decimal(s) if s not in ("", "-") else Decimal(0)
    except Exception:
        return Decimal(0)


def _find_header(rows, *names):
    for i, row in enumerate(rows[:40]):
        cells = [str(c).strip().lower() for c in row]
        if all(any(c == n or c.startswith(n) for c in cells) for n in names):
            return i, cells
    return None, None


def _col(cells, *variants):
    for v in variants:
        for i, c in enumerate(cells):
            if c == v:
                return i
    return None


def detect(rows):
    """Какой это отчёт: 'act' (Акт реализации), 'checks' (Отчёт по счетам) или None."""
    if _find_header(rows, "код товара", "наименование")[0] is not None:
        return 'act'
    if _find_header(rows, "дата закрытия", "к оплате")[0] is not None:
        return 'checks'
    return None


def parse_act(rows):
    """Акт реализации → [{day, code, name, qty, sum, ret_qty, ret_sum}]."""
    h, cells = _find_header(rows, "код товара", "наименование")
    c_code, c_name = _col(cells, "код товара"), _col(cells, "наименование")
    c_qty = _col(cells, "кол-во")
    c_sum = _col(cells, "продажа / сумма", "сумма")
    c_rq, c_rs = _col(cells, "возвраты / кол-во"), _col(cells, "возвраты / сумма")
    if None in (c_code, c_name, c_qty, c_sum):
        raise ValueError("В «Акте реализации» не нашёл колонки «Код товара», «Кол-во», «Продажа / Сумма».")
    lines, day = [], None
    for row in rows[h + 1:]:
        row = list(row) + [""] * 30
        text = " ".join(str(c) for c in row[:4] if c)
        m = re.search(r"Дата:\s*(\d{1,2})\s+(\S+)\s+(\d{4})", text)
        if m and m.group(2).lower() in MONTHS:
            day = date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
            continue
        code = str(row[c_code]).strip()
        if not day or not re.fullmatch(r"[0-9A-Za-zА-Яа-я\-_.]{1,64}", code) or code.lower().startswith("итого"):
            continue
        qty, total = int(_num(row[c_qty])), _num(row[c_sum])
        ret_qty = int(_num(row[c_rq])) if c_rq is not None else 0
        ret_sum = _num(row[c_rs]) if c_rs is not None else Decimal(0)
        if qty <= 0 and ret_qty <= 0:
            continue
        lines.append({"day": day, "code": code, "name": str(row[c_name]).strip(), "qty": max(qty, 0), "sum": total,
                      "ret_qty": max(ret_qty, 0), "ret_sum": abs(ret_sum)})
    if not lines:
        raise ValueError("В «Акте реализации» не нашёл продаж.")
    return lines


def _dt(value):
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_checks(rows):
    """Отчёт по счетам → [{no, time, seller, store, total, cash, card}]."""
    h, cells = _find_header(rows, "дата закрытия", "к оплате")
    c = {k: _col(cells, *v) for k, v in {
        "no": ("№ счета",), "seller": ("сотрудник",), "store": ("торговый объект",), "time": ("дата закрытия",),
        "total": ("к оплате",), "cash": ("оплата - наличными",), "card": ("оплата - картой",)}.items()}
    if c["time"] is None or c["total"] is None:
        raise ValueError("В «Отчёте по счетам» не нашёл колонки «Дата закрытия» и «К оплате».")
    checks = []
    for row in rows[h + 1:]:
        row = list(row) + [""] * 20
        moment = _dt(row[c["time"]])
        if not moment:
            continue
        total = _num(row[c["total"]])
        if not total:
            continue
        cash = _num(row[c["cash"]]) if c["cash"] is not None else Decimal(0)
        card = _num(row[c["card"]]) if c["card"] is not None else Decimal(0)
        if cash + card != total:                     # отчёт без разбивки оплаты
            cash, card = Decimal(0), Decimal(0)
        checks.append({"no": str(row[c["no"]]).strip() if c["no"] is not None else "",
                       "time": moment, "seller": str(row[c["seller"]] or "").strip() if c["seller"] is not None else "",
                       "store": str(row[c["store"]] or "").strip() if c["store"] is not None else "",
                       "total": total, "cash": cash, "card": card, "items": [], "exact": False})
    if not checks:
        raise ValueError("В «Отчёте по счетам» не нашёл чеков.")
    return checks


# ---------------------------------------------------------------- раскладка изделий по чекам

def _units(lines):
    """Строки акта → по дням списки единиц [код, цена] (возврат — отрицательная цена). Сумма единиц = сумма строки."""
    days = defaultdict(list)
    for ln in lines:
        for qty, total, sign in ((ln['qty'], ln['sum'], 1), (ln['ret_qty'], ln['ret_sum'], -1)):
            if qty <= 0:
                continue
            each = (total / qty).quantize(Decimal(1))
            prices = [each] * (qty - 1) + [total - each * (qty - 1)]
            days[ln['day']] += [[ln['code'], sign * p] for p in prices]
    return days


def match(lines, checks):
    """Раскладывает единицы акта по чекам (в checks[i]['items'] — [(код, цена)]). Возвращает остаток единиц без чека."""
    units = _units(lines)
    by_day = defaultdict(list)
    for ch in sorted(checks, key=lambda c: c['time']):
        by_day[ch['time'].date()].append(ch)
    leftover = []
    for day in sorted(set(units) | set(by_day)):
        free, day_checks = units.get(day, []), by_day.get(day, [])
        used = [False] * len(free)
        for ch in day_checks:                        # 1) точное совпадение одного изделия
            for i, (code, price) in enumerate(free):
                if not used[i] and price == ch['total']:
                    used[i] = True
                    ch['items'], ch['exact'] = [(code, price)], True
                    break
        for n in range(2, MAX_SUBSET + 1):           # 2) несколько изделий на сумму чека
            for ch in day_checks:
                if ch['items']:
                    continue
                pool = [i for i, u in enumerate(free) if not used[i] and (u[1] > 0) == (ch['total'] > 0)]
                if len(pool) > 40:
                    pool = pool[:40]
                for combo in itertools.combinations(pool, n):
                    if sum(free[i][1] for i in combo) == ch['total']:
                        for i in combo:
                            used[i] = True
                        ch['items'], ch['exact'] = [tuple(free[i]) for i in combo], True
                        break
        rest = [free[i] for i in range(len(free)) if not used[i]]
        open_checks = [ch for ch in day_checks if not ch['items']]
        for sign in (1, -1):                         # 3) остаток — по оставшимся чекам дня, крупные — в крупные
            targets = [ch for ch in open_checks if (ch['total'] > 0) == (sign > 0)]
            pile = sorted([u for u in rest if (u[1] > 0) == (sign > 0)], key=lambda u: -abs(u[1]))
            if not targets:
                leftover += [(day, code, price) for code, price in pile]
                continue
            for ch in sorted(targets, key=lambda c: -abs(c['total'])):   # каждому чеку — хотя бы одно изделие
                if pile:
                    best = min(pile, key=lambda u: abs(abs(u[1]) - abs(ch['total'])))
                    pile.remove(best)
                    ch['items'].append(tuple(best))
            for code, price in pile:
                target = max(targets, key=lambda ch: abs(ch['total']) - abs(sum(p for _, p in ch['items'])))
                target['items'].append((code, price))
            for ch in targets:
                _fit(ch)
    return leftover


def _fit(ch):
    """Цены позиций чека — пропорционально под сумму чека (итог дня и оплаты совпадает с Паломой)."""
    items = ch['items']
    if not items:
        return
    got = sum(p for _, p in items)
    if got == ch['total'] or not got:
        return
    k = ch['total'] / got
    fitted = [(c, (p * k).quantize(Decimal(1))) for c, p in items]
    fitted[-1] = (fitted[-1][0], ch['total'] - sum(p for _, p in fitted[:-1]))
    ch['items'] = fitted


# ---------------------------------------------------------------- план и применение

@dataclass
class SalesPlan:
    checks: list = field(default_factory=list)       # чеки к загрузке (с изделиями)
    period: tuple = (None, None)
    stores: dict = field(default_factory=dict)       # имя в Паломе → (id, имя у нас) или None
    sellers: dict = field(default_factory=dict)      # имя в Паломе → (user_id или None, как будет записан)
    products: dict = field(default_factory=dict)     # код → (id, цена)
    missing: dict = field(default_factory=dict)      # код → {name, units, sum, price} — нет в каталоге
    skipped_days: list = field(default_factory=list)
    leftover: list = field(default_factory=list)
    empty_checks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _store_match(name, stores):
    low = name.lower()
    for st in stores:
        if st['name'].lower() == low:
            return st
    hits = [st for st in stores if st['name'].lower() in low or low in st['name'].lower()]
    return hits[0] if len(hits) == 1 else None


@with_connection
def _lookup(cursor, codes):
    found = {}
    codes = list(codes)
    for i in range(0, len(codes), 500):
        part = codes[i:i + 500]
        marks = ",".join(["%s"] * len(part))
        cursor.execute(f"SELECT id, article_number, barcode, price FROM products WHERE article_number IN ({marks}) "
                       f"OR barcode IN ({marks}) ORDER BY id", part * 2)
        for p in cursor.fetchall():
            for key in (p['article_number'], p['barcode']):
                if key in part and key not in found:
                    found[key] = (p['id'], p['price'])
    cursor.execute("""SELECT DISTINCT DATE(created_at) AS d FROM receipts WHERE source = %s
                      UNION SELECT DISTINCT DATE(return_date) FROM returns WHERE source = %s""", (SOURCE, SOURCE))
    loaded = {r['d'] for r in cursor.fetchall()}
    cursor.execute("SELECT id, name, surename, is_active FROM users ORDER BY is_active DESC, id")
    users = cursor.fetchall()
    cursor.execute("SELECT id, name FROM stores ORDER BY sort_order, id")
    stores = cursor.fetchall()
    return found, loaded, users, stores


def build_plan(files):
    """files: [(bytes, имя файла)] — оба отчёта в любом порядке. Возвращает SalesPlan (errors — если загружать нельзя)."""
    plan = SalesPlan()
    act = checks = None
    for data, name in files:
        try:
            rows = read_rows(data, name)
        except ValueError as e:
            plan.errors.append(f"{name}: {e}")
            continue
        except Exception:
            logger.exception("Импорт продаж: не прочитан %s", name)
            plan.errors.append(f"{name}: не удалось прочитать файл.")
            continue
        kind = detect(rows)
        try:
            if kind == 'act':
                act = parse_act(rows)
            elif kind == 'checks':
                checks = parse_checks(rows)
            else:
                plan.errors.append(f"{name}: это не «Акт реализации» и не «Отчёт по счетам».")
        except ValueError as e:
            plan.errors.append(f"{name}: {e}")
    if act is None:
        plan.errors.append("Нужен «Акт реализации» Паломы — что продано.")
    if checks is None:
        plan.errors.append("Нужен «Отчёт по счетам» Паломы — чеки: время, продавец, оплата.")
    if plan.errors:
        return plan

    found, loaded, users, stores = _lookup({ln['code'] for ln in act}) or ({}, set(), [], [])
    plan.products = found
    days = sorted({ln['day'] for ln in act} | {c['time'].date() for c in checks})
    plan.period = (days[0], days[-1])
    plan.skipped_days = [d for d in days if d in loaded]
    act = [ln for ln in act if ln['day'] not in loaded]
    checks = [c for c in checks if c['time'].date() not in loaded]
    plan.stats['act_total'] = sum(ln['sum'] - ln['ret_sum'] for ln in act)
    plan.stats['checks_total'] = sum(c['total'] for c in checks)
    if plan.stats['act_total'] != plan.stats['checks_total']:
        plan.warnings.append(f"Суммы отчётов не совпадают: акт — {plan.stats['act_total']:,.0f}, чеки — "
                             f"{plan.stats['checks_total']:,.0f}. Проверьте, что оба отчёта за один период.".replace(",", " "))
    plan.leftover = match(act, checks)

    for name in sorted({c['store'] for c in checks}):
        st = _store_match(name, stores) if name else (stores[0] if len(stores) == 1 else None)
        plan.stores[name] = (st['id'], st['name']) if st else None
        if not st:
            plan.errors.append(f"Торговый объект «{name or '—'}» не найден среди магазинов "
                               f"({', '.join(s['name'] for s in stores)}). Переименуйте магазин или добавьте его.")
    for name in sorted({c['seller'] for c in checks}):
        low = name.lower()
        user = next((u for u in users if low and (u['name'].lower() == low or
                                                 f"{u['name']} {u['surename'] or ''}".strip().lower() == low)), None)
        plan.sellers[name] = (user['id'], user['name']) if user else (None, f"{name or 'Продавец'} (Палома)")

    names = {ln['code']: ln['name'] for ln in act}
    for ch in checks:
        for code, price in ch['items']:
            if code not in found:
                m = plan.missing.setdefault(code, {"name": names.get(code, ""), "units": 0, "sum": Decimal(0),
                                                   "price": Decimal(0), "store": ch['store']})
                m['units'] += 1
                m['sum'] += price
                m['price'] = max(m['price'], abs(price))
        if ch['items']:
            plan.checks.append(ch)
        else:
            plan.empty_checks.append(ch)
    sales = [p for ch in plan.checks for _, p in ch['items'] if p > 0]
    rets = [p for ch in plan.checks for _, p in ch['items'] if p < 0]
    plan.stats.update({
        "checks": len([c for c in plan.checks if c['total'] > 0]),
        "return_checks": len([c for c in plan.checks if c['total'] < 0]),
        "exact": sum(1 for c in plan.checks if c['exact']),
        "fitted": sum(1 for c in plan.checks if not c['exact']),
        "sales": len(sales), "sales_sum": sum(sales),
        "returns": len(rets), "returns_sum": -sum(rets),
        "cash": sum(c['cash'] for c in plan.checks), "card": sum(c['card'] for c in plan.checks),
    })
    if len(plan.missing) > max(20, len(names) // 5):
        plan.warnings.append("Больше пятой части проданных изделий нет в каталоге — похоже, каталог ещё не загружен. "
                             "Сначала загрузите каталог (Склад → Импорт каталога), потом продажи: тогда история "
                             "привяжется к полным карточкам товаров.")
    if plan.missing:
        plan.warnings.append(f"{len(plan.missing)} проданных изделий нет в каталоге — они будут добавлены в каталог "
                             "с остатком 0 (тип и вес — по названию, цена — по продаже), чтобы к ним привязалась история.")
    if plan.empty_checks:
        plan.warnings.append(f"{len(plan.empty_checks)} чеков не удалось сопоставить с изделиями — не загрузятся.")
    if plan.leftover:
        plan.warnings.append(f"{len(plan.leftover)} проданных единиц не нашли чека своего дня и не загрузятся.")
    if plan.skipped_days:
        plan.warnings.append(f"Дни, уже загруженные из Паломы ранее, пропущены: {len(plan.skipped_days)}.")
    if not plan.checks:
        plan.errors.append("Загружать нечего.")
    return plan


def _payments(ch):
    """[(способ, сумма > 0)] и способ чека."""
    total = abs(ch['total'])
    parts = [(CASH, abs(ch['cash'])), (CARD, abs(ch['card']))]
    parts = [(k, a) for k, a in parts if a]
    if not parts:
        parts = [(CASH, total)]
    return parts, (parts[0][0] if len(parts) == 1 else MIXED_PAYMENT)


@with_transaction
def apply_plan(cursor, plan: SalesPlan, user_id):
    """Загрузить чеки плана. Продажи и возвраты — source='paloma'; остатки, касса и Webkassa не трогаются."""
    sellers = {}
    for name, (uid, title) in plan.sellers.items():
        if not uid:
            cursor.execute("SELECT id FROM users WHERE name = %s AND phone_number = '' AND is_active = 0 LIMIT 1",
                           (title,))
            row = cursor.fetchone()
            if row:
                uid = row['id']
            else:
                cursor.execute("INSERT INTO users (phone_number, role, name, is_active) VALUES ('', 'cashier', %s, 0)",
                               (title,))
                uid = cursor.lastrowid
        sellers[name] = uid
    created = {"receipts": 0, "sales": 0, "returns": 0, "unlinked_returns": 0, "products": 0}
    products = dict(plan.products)
    for code, m in plan.missing.items():
        cursor.execute("SELECT id, price FROM products WHERE article_number = %s ORDER BY id LIMIT 1", (code,))
        row = cursor.fetchone()
        if not row:
            p = guess_product(code, m['name'], m['price'])
            p.update(store_id=plan.stores[m['store']][0], quantity=0, arrival_date=plan.period[0], track_stock=0)
            cols = ", ".join(f"`{k}`" for k in p)
            cursor.execute(f"INSERT INTO products ({cols}) VALUES ({', '.join(['%s'] * len(p))})", list(p.values()))
            row = {"id": cursor.lastrowid, "price": m['price']}
            created['products'] += 1
        products[code] = (row['id'], row['price'])
    # сначала все продажи, потом возвраты: возврат дня может относиться к продаже, пробитой в тот же день позже
    for ch in sorted(plan.checks, key=lambda c: (c['total'] < 0, c['time'])):
        store_id = plan.stores[ch['store']][0]
        seller = sellers.get(ch['seller'])
        parts, payment = _payments(ch)
        external = f"{ch['time']:%Y-%m-%d %H:%M:%S}#{ch['no']}#{ch['store']}"[:96]
        if ch['total'] > 0:
            items = [(code, price) for code, price in ch['items'] if price > 0]
            cursor.execute("""INSERT IGNORE INTO receipts (store_id, user_id, created_at, total, items_count, payment_type,
                                                          source, external_id)
                              VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                           (store_id, seller, ch['time'], sum(p for _, p in items), len(items), payment, SOURCE, external))
            if not cursor.rowcount:
                continue                             # этот чек уже загружен
            receipt_id = cursor.lastrowid
            for kind, amount in parts:
                cursor.execute("INSERT INTO receipt_payments (receipt_id, payment_type, amount) VALUES (%s, %s, %s)",
                               (receipt_id, kind, amount))
            for code, price in items:
                product_id, list_price = products[code]
                cursor.execute("""INSERT INTO sales (product_id, user_id, sale_date, sold_price, payment_type, store_id,
                                                     receipt_id, list_price, source)
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                               (product_id, seller, ch['time'], price, payment, store_id, receipt_id, list_price, SOURCE))
                created['sales'] += 1
            created['receipts'] += 1
        else:
            kind = max(parts, key=lambda p: p[1])[0]
            for code, price in ch['items']:
                product_id = products[code][0]
                cursor.execute("""SELECT s.id FROM sales s LEFT JOIN returns r ON r.original_sale_id = s.id
                                  WHERE s.product_id = %s AND s.sale_date < %s AND r.id IS NULL
                                  ORDER BY s.sale_date DESC, s.id DESC LIMIT 1 FOR UPDATE""",
                               (product_id, datetime.combine(ch['time'].date(), datetime.min.time()) + timedelta(days=1)))
                # по сумме чек продажи того же дня мог достаться позже возврата — продажа дня подходит целиком
                sale = cursor.fetchone()
                if not sale:
                    created['unlinked_returns'] += 1
                    continue
                cursor.execute("""INSERT INTO returns (original_sale_id, product_article, return_price, payment_type,
                                                       user_id, store_id, return_date, source)
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                               (sale['id'], code, -price, kind, seller or user_id, store_id, ch['time'], SOURCE))
                created['returns'] += 1
    if not created['receipts'] and not created['returns']:
        raise BusinessError("Все чеки уже были загружены раньше.")
    logger.info("Импорт продаж из Паломы пользователем %s: %s", user_id, created)
    return True, created
