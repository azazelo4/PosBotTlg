"""Общие помощники: время в часовом поясе магазина, разбор ввода, форматирование."""
import re
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from functools import lru_cache
from zoneinfo import ZoneInfo

import config


@lru_cache(maxsize=8)
def _zone(name):
    return ZoneInfo(name)


def tz() -> ZoneInfo:
    """Часовой пояс магазина (настройка TIMEZONE)."""
    return _zone(config.TIMEZONE or "Asia/Almaty")


def now() -> datetime:
    """Текущее время магазина без tzinfo (так хранится в DATETIME)."""
    return datetime.now(tz()).replace(tzinfo=None, microsecond=0)


def today() -> date:
    return now().date()


def day_bounds(start: date, end: date):
    """Полуинтервал [start 00:00, end+1 00:00) для фильтрации DATETIME."""
    return datetime.combine(start, datetime.min.time()), datetime.combine(end + timedelta(days=1), datetime.min.time())


def parse_money(text):
    """'15 000', '15000,50', '15.000,50' -> Decimal. None при ошибке. Допускает минус."""
    if text is None:
        return None
    s = str(text).strip().replace(' ', '').replace(' ', '')
    negative = s.startswith('-')
    s = re.sub(r"[^\d,.]", "", s)
    if not s:
        return None
    if '.' in s and ',' in s:
        if s.rfind('.') < s.rfind(','):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif ',' in s:
        s = s.replace(',', '.')
    if s.count('.') > 1:
        head, *tail = s.split('.')
        s = head + '.' + ''.join(tail)
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_float(text):
    try:
        return float(str(text).strip().replace(',', '.'))
    except (ValueError, TypeError):
        return None


def parse_int(text):
    try:
        return int(str(text).strip())
    except (ValueError, TypeError):
        return None


def parse_date(text):
    """Принимает ДД.ММ.ГГГГ, ДД.ММ.ГГ и ГГГГ-ММ-ДД."""
    s = (text or '').strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def normalize_phone(phone):
    """Любой казахстанский формат -> '+7XXXXXXXXXX'. None, если номер некорректен."""
    digits = re.sub(r"\D", "", str(phone or ''))
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    elif len(digits) == 10:
        digits = '7' + digits
    if len(digits) != 11 or not digits.startswith('7'):
        return None
    return '+' + digits


def money(value) -> str:
    """12345.0 -> '12 345 ₸'."""
    if value is None:
        value = 0
    d = Decimal(str(value)).quantize(Decimal('1'))
    return f"{int(d):,}".replace(',', ' ') + " ₸"


def h(text) -> str:
    """Экранирование для parse_mode=HTML."""
    return escape(str(text if text is not None else ''))
