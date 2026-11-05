"""Импорт каталога из Excel/CSV (выгрузка Paloma365 или любая таблица).

Колонки распознаются по заголовкам («Артикул», «Наименование», «Проба», «Остаток»…),
тип изделия и металл приводятся к справочникам, недостающее выводится из названия и пробы.
Сначала строится план (что добавится, что обновится, какие строки с ошибками),
и только после подтверждения план применяется одной транзакцией.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import openpyxl

from config import IMPORT_MIN_PRICE_PERCENT
from database import get_products_by_articles, import_products, get_stores
from schema import PRODUCT_TYPES
from utils.common import parse_money, parse_float, parse_date

MAX_ROWS = 20000

HEADERS = {
    'article_number': ["артикул", "арт", "код", "код товара", "sku", "article", "артикул товара"],
    'model': ["наименование", "название", "модель", "товар", "наименование товара", "name", "номенклатура"],
    'product_type': ["тип", "тип изделия", "категория", "группа", "вид", "вид изделия", "группа товаров"],
    'metal_type': ["металл", "материал", "вид металла"],
    'fineness': ["проба"],
    'weight': ["вес", "вес г", "масса", "вес гр", "вес грамм"],
    'size': ["размер"],
    'inserts': ["вставки", "вставка", "камни", "камень", "вставки камни"],
    'price': ["цена", "цена продажи", "розничная цена", "стоимость", "цена розничная", "цена тг", "цена розн"],
    'min_price': ["мин цена", "минимальная цена", "цена мин", "мин стоимость"],
    'price_per_gram': ["цена за грамм", "цена за г", "цена грамма"],
    'quantity': ["количество", "кол во", "колво", "остаток", "остатки", "qty", "в наличии"],
    'Store': ["магазин", "склад", "точка", "торговая точка"],
    'manufacturer': ["производитель", "поставщик", "бренд", "завод"],
    'commentaries': ["комментарий", "примечание", "описание"],
    'arrival_date': ["дата поступления", "дата прихода", "поступление", "дата"],
}
FIELD_TITLES = {
    'article_number': "артикул", 'model': "название", 'product_type': "тип", 'metal_type': "металл",
    'fineness': "проба", 'weight': "вес", 'size': "размер", 'inserts': "вставки", 'price': "цена",
    'min_price': "мин. цена", 'price_per_gram': "цена за грамм", 'quantity': "остаток", 'Store': "магазин",
    'manufacturer': "производитель", 'commentaries': "комментарий", 'arrival_date': "дата поступления",
}

TYPE_SYNONYMS = {
    'кольца': 'кольцо', 'обручальное кольцо': 'кольцо', 'цепи': 'цепь', 'цепочка': 'цепь', 'цепочки': 'цепь',
    'серьга': 'серьги', 'браслеты': 'браслет', 'подвески': 'подвеска', 'кулон': 'подвеска', 'кулоны': 'подвеска',
    'броши': 'брошь', 'наборы': 'набор', 'комплект': 'набор', 'комплекты': 'набор', 'гарнитур': 'набор',
    'ложки': 'ложка', 'печатки': 'печатка', 'булавки': 'булавка', 'колье': 'колье', 'бусы': 'бусы', 'часы': 'часы',
    'детские серьги': 'детские серьги', 'блезик': 'блезык', 'блезики': 'блезык', 'блезыки': 'блезык',
}
METAL_SYNONYMS = {
    'золото': 'золото', 'золотой': 'золото', 'зол': 'золото', 'au': 'золото', 'gold': 'золото',
    'серебро': 'серебро', 'серебряный': 'серебро', 'сер': 'серебро', 'ag': 'серебро', 'silver': 'серебро',
    'бижутерия': 'бижутерия', 'биж': 'бижутерия', 'сталь': 'бижутерия', 'ювелирный сплав': 'бижутерия',
}
GOLD_FINENESS = {'375', '500', '583', '585', '750', '958', '999'}
SILVER_FINENESS = {'800', '830', '875', '925', '960'}


def _norm(text):
    s = str(text or '').strip().lower().replace('ё', 'е')
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[.,:;_/\\\-№#₸]", " ", s)
    s = re.sub(r"\b(тг|тенге|kzt|шт|гр?)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_HEADER_INDEX = {_norm(alias): key for key, aliases in HEADERS.items() for alias in aliases}


def map_headers(headers):
    """{индекс колонки: поле}; первая подходящая колонка побеждает."""
    mapping, used = {}, set()
    for i, header in enumerate(headers):
        key = _HEADER_INDEX.get(_norm(header))
        if key and key not in used:
            mapping[i] = key
            used.add(key)
    return mapping


def read_table(data: bytes, filename: str):
    """Возвращает (заголовки, строки) из .xlsx или .csv."""
    name = (filename or '').lower()
    if name.endswith('.xlsx') or name.endswith('.xlsm'):
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
    elif name.endswith('.csv') or name.endswith('.txt'):
        for encoding in ('utf-8-sig', 'cp1251'):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("Не удалось прочитать CSV: неизвестная кодировка.")
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=";,\t").delimiter
        except csv.Error:
            delimiter = ';'
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    elif name.endswith('.xls'):
        raise ValueError("Формат .xls не поддерживается. Сохраните файл в Excel как .xlsx и загрузите снова.")
    else:
        raise ValueError("Нужен файл .xlsx или .csv.")
    rows = [r for r in rows if any(c not in (None, '') for c in r)]
    # Заголовок — первая строка, где распознаётся хотя бы артикул и цена
    for i, row in enumerate(rows[:15]):
        fields = set(map_headers(row).values())
        if {'article_number', 'price'} <= fields:
            return row, rows[i + 1:]
    raise ValueError("Не нашёл строку заголовков с колонками «Артикул» и «Цена».")


def _type_from(value, model):
    for candidate in (value, model):
        s = _norm(candidate)
        if not s:
            continue
        if s in PRODUCT_TYPES:
            return s
        if s in TYPE_SYNONYMS:
            return TYPE_SYNONYMS[s]
        for word_count in (2, 1):  # «Детские серьги …», «Кольцо Классика»
            head = " ".join(s.split()[:word_count])
            if head in PRODUCT_TYPES:
                return head
            if head in TYPE_SYNONYMS:
                return TYPE_SYNONYMS[head]
    return None


def _metal_from(value, fineness, model):
    s = _norm(value)
    for word in s.split():
        if word in METAL_SYNONYMS:
            return METAL_SYNONYMS[word]
    if s in METAL_SYNONYMS:
        return METAL_SYNONYMS[s]
    if fineness in GOLD_FINENESS:
        return 'золото'
    if fineness in SILVER_FINENESS:
        return 'серебро'
    m = _norm(model)
    if 'золот' in m:
        return 'золото'
    if 'серебр' in m:
        return 'серебро'
    return None


def _fineness_from(value, metal_value):
    for candidate in (value, metal_value):
        found = re.search(r"\b(\d{3})\b", str(candidate or ''))
        if found:
            return found.group(1)
    return None


def _text(value, limit):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value).strip()
    return s[:limit] if s else None


def _money_value(value):
    if value in (None, ''):
        return None
    v = parse_money(value) if not isinstance(value, (int, float, Decimal)) else Decimal(str(value))
    return v.quantize(Decimal('1')) if v is not None else None


@dataclass
class ImportPlan:
    rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)     # (номер строки в файле, текст)
    warnings: list = field(default_factory=list)
    columns: list = field(default_factory=list)    # распознанные поля
    ignored: list = field(default_factory=list)    # нераспознанные заголовки
    new: int = 0
    existing: int = 0

    def summary(self):
        lines = [f"Распознаны колонки: {', '.join(FIELD_TITLES[c] for c in self.columns)}"]
        if self.ignored:
            lines.append(f"Пропущены колонки: {', '.join(str(c) for c in self.ignored[:15])}")
        lines.append(f"Строк к загрузке: {len(self.rows)} — новых {self.new}, уже есть в базе {self.existing}")
        if self.errors:
            lines.append(f"Строк с ошибками (не загрузятся): {len(self.errors)}")
            lines += [f"  • строка {n}: {msg}" for n, msg in self.errors[:15]]
            if len(self.errors) > 15:
                lines.append(f"  … и ещё {len(self.errors) - 15}")
        lines += [f"⚠️ {w}" for w in self.warnings]
        return "\n".join(lines)


def build_plan(data: bytes, filename: str, default_store=None) -> ImportPlan:
    """default_store — id магазина для строк без колонки «Магазин»."""
    headers, rows = read_table(data, filename)
    mapping = map_headers(headers)
    plan = ImportPlan(columns=list(dict.fromkeys(mapping.values())),
                      ignored=[h for i, h in enumerate(headers) if i not in mapping and h not in (None, '')])
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Слишком много строк ({len(rows)}), максимум {MAX_ROWS}.")
    has_qty = 'quantity' in plan.columns
    has_min = 'min_price' in plan.columns
    if 'Store' not in plan.columns and default_store is None:
        raise ValueError("В файле нет колонки «Магазин». Начните импорт заново и выберите магазин для всех товаров.")
    stores = get_stores() or []
    stores_by_norm = {_norm(st['name']): st['id'] for st in stores}
    store_names = {st['id']: st['name'] for st in stores}
    # Существующие товары: недостающие в файле тип/металл/магазин берутся из базы
    file_articles = list(dict.fromkeys(_text(r[i], 64) for r in rows for i, k in mapping.items()
                                       if k == 'article_number' and i < len(r) and _text(r[i], 64)))
    existing_products = {}
    for i in range(0, len(file_articles), 500):
        for p in get_products_by_articles(file_articles[i:i + 500]) or []:
            existing_products[p['article_number']] = p
    seen = {}
    header_offset = 2  # номер строки в файле для сообщений (примерно, без учёта пустых)
    for idx, raw in enumerate(rows):
        line = idx + header_offset
        row = {key: (raw[i] if i < len(raw) else None) for i, key in mapping.items()}
        article = _text(row.get('article_number'), 64)
        if not article:
            plan.errors.append((line, "нет артикула"))
            continue
        if article in seen:
            plan.errors.append((line, f"артикул {article} повторяется (уже в строке {seen[article]})"))
            continue
        seen[article] = line
        price = _money_value(row.get('price'))
        if price is None or price <= 0:
            plan.errors.append((line, f"{article}: нет цены или она некорректна"))
            continue
        current = existing_products.get(article)
        model = _text(row.get('model'), 255)
        fineness = _fineness_from(row.get('fineness'), row.get('metal_type'))
        product_type = _type_from(row.get('product_type'), model)
        if not product_type and current and not _text(row.get('product_type'), 64):
            product_type = current['product_type']
        if not product_type:
            plan.errors.append((line, f"{article}: тип изделия «{row.get('product_type') or model or ''}» не распознан"))
            continue
        metal = _metal_from(row.get('metal_type'), fineness, model)
        if not metal and current and not _text(row.get('metal_type'), 64):
            metal = current['metal_type']
        if not metal:
            plan.errors.append((line, f"{article}: металл не распознан"))
            continue
        store_text = _text(row.get('Store'), 100)
        if store_text:
            store = stores_by_norm.get(_norm(store_text))
        else:
            store = current['store_id'] if current else default_store
        if not store:
            plan.errors.append((line, f"{article}: магазин «{row.get('Store')}» не найден "
                                      f"(есть: {', '.join(st['name'] for st in stores)})"))
            continue
        min_price = _money_value(row.get('min_price')) if has_min else None
        if min_price is None:
            min_price = (price * Decimal(str(IMPORT_MIN_PRICE_PERCENT)) / 100).quantize(Decimal('1'))
        min_price = min(min_price, price)
        weight = parse_float(row.get('weight')) if row.get('weight') not in (None, '') else None
        if weight is not None and (weight < 0 or weight > 10000):
            plan.errors.append((line, f"{article}: некорректный вес"))
            continue
        quantity = None
        if has_qty:
            q = parse_float(row.get('quantity')) if row.get('quantity') not in (None, '') else 0
            if q is None or q < 0:
                plan.errors.append((line, f"{article}: некорректный остаток"))
                continue
            quantity = int(q)
        arrival = row.get('arrival_date')
        if isinstance(arrival, datetime):
            arrival = arrival.date()
        elif not isinstance(arrival, date):
            arrival = parse_date(str(arrival)) if arrival else None
        given = {k for k, v in row.items() if v not in (None, '') and str(v).strip() != ''}
        plan.rows.append({
            '_given': given,
            'article_number': article, 'model': model or product_type, 'product_type': product_type,
            'metal_type': metal, 'fineness': fineness, 'weight': weight if weight is not None else 0,
            'size': _text(row.get('size'), 32), 'inserts': _text(row.get('inserts'), 255),
            'price': price, 'min_price': min_price,
            'price_per_gram': _money_value(row.get('price_per_gram')) or Decimal(0),
            'quantity': quantity, 'store_id': store, '_store_name': store_names.get(store),
            'manufacturer': _text(row.get('manufacturer'), 255), 'commentaries': _text(row.get('commentaries'), 500),
            'arrival_date': arrival,
            '_weight_given': weight is not None,
        })
    existing = set(existing_products)
    articles = [r['article_number'] for r in plan.rows]
    plan.existing = sum(1 for a in articles if a in existing)
    plan.new = len(articles) - plan.existing
    for r in plan.rows:
        r['_exists'] = r['article_number'] in existing
    if not has_qty:
        plan.warnings.append("В файле нет колонки остатка: новые товары получат остаток 0, у существующих он не изменится.")
    if not has_min:
        plan.warnings.append(f"Нет колонки минимальной цены: минимальная = {IMPORT_MIN_PRICE_PERCENT:g}% от цены.")
    if plan.rows and not any(r['_weight_given'] for r in plan.rows):
        plan.warnings.append("В файле нет веса: у новых товаров вес будет 0, на ценнике он не печатается.")
    return plan


def apply_plan(plan: ImportPlan, update_existing=True):
    columns = set(plan.columns)
    # Для существующих товаров обновляем только то, что было в файле
    update_keys = {'article_number'} | (columns - {'arrival_date', 'Store'})
    if 'metal_type' in columns:
        update_keys.add('fineness')
    if 'Store' in columns:
        update_keys.add('store_id')
    rows = []
    for r in plan.rows:
        row = {k: v for k, v in r.items() if not k.startswith('_')}
        if r['_exists']:
            # и только заполненные ячейки: пустая ячейка не затирает значение в базе
            given = set(r['_given']) | {'article_number'}
            if 'Store' in given:
                given.add('store_id')
            if 'metal_type' in given:
                given.add('fineness')
            if 'price' in given:
                given.add('min_price')
            row = {k: v for k, v in row.items() if k in update_keys and k in given}
        else:
            if row['quantity'] is None:
                row['quantity'] = 0
            if row.get('arrival_date') is None:
                row.pop('arrival_date')
        rows.append(row)
    return import_products(rows, update_existing=update_existing)
