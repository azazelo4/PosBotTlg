"""Конфигурация бота.

Все секреты и параметры окружения читаются из переменных окружения
(в Docker — из файла .env, который создаётся пайплайном из секретов).
Шаблон переменных — в .env.example. Никаких секретов в этом файле.
"""
import os


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Не задана обязательная переменная окружения {name}")
    return value


def _optional_int(name: str, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _optional_str(name: str, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


# Токен телеграм-бота
TOKEN = _require("TOKEN")

# Параметры подключения к базе данных MySQL
DB_HOST = _optional_str("DB_HOST", "db")
DB_PORT = _optional_int("DB_PORT", 3306)
DB_USER = _require("DB_USER")
DB_PASS = _require("DB_PASS")
DB_NAME = _require("DB_NAME")

# Часовой пояс магазинов: даты операций, периоды отчётов и расписание
TIMEZONE = _optional_str("TZ", "Asia/Almaty")

# Телефон первого администратора. Если в БД нет такого активного пользователя,
# он будет создан при старте — иначе на пустой базе никто не сможет войти.
ADMIN_PHONE = _optional_str("ADMIN_PHONE")
ADMIN_NAME = _optional_str("ADMIN_NAME", "Администратор")

# Ежедневный отчёт, формат HH:MM. Отправляется в REPORT_CHAT_ID,
# а если он не задан — всем активным администраторам.
DAILY_REPORT_TIME = _optional_str("DAILY_REPORT_TIME", "23:00")
REPORT_CHAT_ID = _optional_int("REPORT_CHAT_ID")

# Уведомление о низком остатке (администраторам и менеджерам), формат HH:MM.
# Пустое значение отключает уведомление.
LOW_STOCK_TIME = _optional_str("LOW_STOCK_TIME", "09:00")
LOW_STOCK_THRESHOLD = _optional_int("LOW_STOCK_THRESHOLD", 5)

LOG_LEVEL = _optional_str("LOG_LEVEL", "INFO")

# Магазины для пустой базы (через запятую). На существующей базе магазины берутся из
# таблицы stores и меняются в веб-админке; переменная больше ни на что не влияет.
DEFAULT_STORES = tuple(s.strip() for s in (_optional_str("STORES", "ЦУМ, Отау Сауда")).split(",") if s.strip())

# ---------------------------------------------------------------- веб-админка и Mini App
# Публичный HTTPS-адрес (например, https://pos.example.kz). Пусто — веб выключен,
# бот не показывает кнопки админки и кассы-приложения.
WEB_URL = (_optional_str("WEB_URL") or "").rstrip("/") or None
# Ключ подписи cookie сессий веб-админки. Если не задан — выводится из TOKEN.
WEB_SECRET = _optional_str("WEB_SECRET")
WEB_SESSION_HOURS = _optional_int("WEB_SESSION_HOURS", 12)

# ---------------------------------------------------------------- ценники
LABEL_WIDTH_MM = float(_optional_str("LABEL_WIDTH_MM", "30"))
LABEL_HEIGHT_MM = float(_optional_str("LABEL_HEIGHT_MM", "20"))
# Строка вверху ценника (название магазина). Пусто — печатается магазин товара.
LABEL_TITLE = _optional_str("LABEL_TITLE")

# ---------------------------------------------------------------- импорт каталога
# Если в файле нет минимальной цены: какой процент от цены считать минимальной
IMPORT_MIN_PRICE_PERCENT = float(_optional_str("IMPORT_MIN_PRICE_PERCENT", "100"))
