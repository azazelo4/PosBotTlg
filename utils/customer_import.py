"""Разбор файла клиентов (.xlsx / .csv): телефон, имя, день рождения, заметка, согласие на рассылку.

Заголовки распознаются по названию (как в импорте каталога), порядок колонок любой. Обязателен только телефон.
"""
import re
from datetime import date, datetime

from utils.catalog_import import read_rows, _norm
from utils.common import normalize_phone, parse_birthday, today, NO_YEAR

MAX_ROWS = 5000

HEADERS = {
    'phone': ['телефон', 'тел', 'номер', 'номер телефона', 'мобильный', 'моб телефон', 'сотовый', 'phone', 'mobile',
              'телефон клиента', 'whatsapp'],
    'name': ['имя', 'фио', 'клиент', 'покупатель', 'имя клиента', 'фамилия имя', 'name', 'full name'],
    'birthday': ['день рождения', 'дата рождения', 'др', 'birthday', 'birth date', 'date of birth'],
    'note': ['комментарий', 'заметка', 'примечание', 'note', 'comment'],
    'marketing': ['рассылка', 'согласие на рассылку', 'реклама', 'marketing'],
}
FIELD_TITLES = {'phone': "Телефон", 'name': "Имя", 'birthday': "День рождения", 'note': "Заметка",
                'marketing': "Рассылка"}
_HEADER_INDEX = {_norm(alias): key for key, aliases in HEADERS.items() for alias in aliases}
_YES = {'да', 'yes', '1', 'true', '+', 'согласен', 'согласна', 'x', 'х', 'v', '✓'}


def map_headers(headers):
    mapping, used = {}, set()
    for i, header in enumerate(headers):
        key = _HEADER_INDEX.get(_norm(header))
        if key and key not in used:
            mapping[i] = key
            used.add(key)
    return mapping


def _phone_text(value):
    """Номер из ячейки: Excel хранит 77011234567 как число (иногда 7.70112E+10)."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value or '').strip()


def _birthday(value):
    """(дата | None, ошибка | None). Пустая ячейка — не ошибка."""
    if value in (None, ''):
        return None, None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        if value > today() or value.year < 1900:
            return None, "дата рождения в будущем или раньше 1900 — без даты"
        return value, None
    text = re.sub(r"\s+", "", str(value))
    born = parse_birthday(text)
    return (born, None) if born else (None, f"не понял дату рождения «{str(value)[:20]}» — без даты")


def _text(value, limit):
    s = re.sub(r"\s+", " ", str(value or '')).strip()
    return s[:limit] or None


def parse_customers(data: bytes, filename: str):
    """→ (колонки {поле: заголовок}, строки). Строка: line, phone, name, birthday, note, marketing, error, warning.
    ValueError — если файл не читается или нет колонки с телефоном."""
    name = (filename or '').lower()
    if not name.endswith(('.xlsx', '.xlsm', '.csv', '.txt')):
        raise ValueError("Нужен файл .xlsx или .csv (Excel: «Сохранить как» → «Книга Excel» или «CSV»).")
    rows = read_rows(data, filename)
    header, mapping = None, {}
    for i, row in enumerate(rows[:15]):
        mapping = map_headers(row)
        if 'phone' in mapping.values():
            header, rows = row, rows[i + 1:]
            break
    if header is None:
        raise ValueError("Не нашёл колонку с телефоном. Назовите её «Телефон» (остальные — «Имя», «День рождения», "
                         "«Заметка», «Рассылка»).")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"В файле больше {MAX_ROWS} клиентов — разделите его на части.")
    columns = {field: str(header[i]).strip() for i, field in mapping.items()}
    result = []
    for n, row in enumerate(rows, start=1):
        cells = {field: (row[i] if i < len(row) else None) for i, field in mapping.items()}
        raw_phone = _phone_text(cells.get('phone'))
        item = {'line': n, 'raw_phone': raw_phone, 'phone': normalize_phone(raw_phone) if raw_phone else None,
                'name': _text(cells.get('name'), 100), 'note': _text(cells.get('note'), 255),
                'marketing': _text(cells.get('marketing'), 20) is not None
                and _norm(cells.get('marketing')) in _YES,
                'birthday': None, 'error': None, 'warning': None}
        # неверная дата рождения не мешает импорту клиента — только предупреждение
        item['birthday'], item['warning'] = _birthday(cells.get('birthday'))
        if not raw_phone:
            item['error'] = "нет телефона"
        elif not item['phone']:
            item['error'] = f"не понял номер «{raw_phone[:20]}»"
        result.append(item)
    return columns, result


def birthday_text(born):
    if not born:
        return ""
    return born.strftime('%d.%m') if born.year == NO_YEAR else born.strftime('%d.%m.%Y')
