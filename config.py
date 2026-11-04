"""Конфигурация бота.

Все секреты и параметры окружения читаются из переменных окружения
(в Docker — из файла .env, который создаётся пайплайном из секретов Gitea).
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


# Токен телеграм-бота
TOKEN = _require("TOKEN")

# Параметры подключения к базе данных MySQL
DB_HOST = os.environ.get("DB_HOST", "db")
DB_USER = _require("DB_USER")
DB_PASS = _require("DB_PASS")
DB_NAME = _require("DB_NAME")

# Время ежедневного отчёта, формат HH:MM
DAILY_REPORT_TIME = os.environ.get("DAILY_REPORT_TIME", "23:00")

# ID чата для отчётов (личный chat_id или ID группы — отрицательное число).
# Если не задан, ежедневный отчёт не отправляется.
REPORT_CHAT_ID = _optional_int("REPORT_CHAT_ID")

# Порог остатка для уведомлений о низком запасе
LOW_STOCK_THRESHOLD = _optional_int("LOW_STOCK_THRESHOLD", 5)
