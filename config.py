"""Конфигурация.

Два уровня:

* **Запуск** (только переменные окружения, файл .env создаёт deploy/bootstrap-env.sh):
  подключение к БД, ключ шифрования настроек, уровень логов.
* **Настройки приложения** (реестр SETTINGS ниже): токен Telegram, магазин, расписание,
  ценники, фискализация… Хранятся в таблице settings и задаются мастером /setup
  и страницей /settings веб-админки. Секреты в БД зашифрованы.

Приоритет: переменная окружения > значение в БД > значение по умолчанию.
Заданная в окружении настройка в веб-админке показывается только для чтения.

Код читает настройки как атрибуты модуля в момент использования (``config.TOKEN``),
а не через ``from config import TOKEN``: значения обновляются без перезапуска
(settings_store.reload()).
"""
import os
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo


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


# ================================================================ запуск (только окружение)

DB_HOST = _optional_str("DB_HOST", "db")
DB_PORT = _optional_int("DB_PORT", 3306)
DB_USER = _optional_str("DB_USER", "bot")
DB_PASS = _optional_str("DB_PASS", "")
DB_NAME = _optional_str("DB_NAME", "bot")

# Ключ шифрования секретов в таблице settings (Fernet). Создаётся один раз bootstrap-env.sh.
MASTER_KEY = _optional_str("POSBOT_MASTER_KEY")

LOG_LEVEL = _optional_str("LOG_LEVEL", "INFO")

# Версия сборки (проставляется при публикации образа)
VERSION = _optional_str("POSBOT_VERSION", "dev")

# Ключ подписи cookie веб-админки. Пусто — выводится из MASTER_KEY.
WEB_SECRET = _optional_str("WEB_SECRET")
WEB_SESSION_HOURS = _optional_int("WEB_SESSION_HOURS", 12)

# Телефон первого администратора (совместимость со старыми установками; новые — через /setup)
ADMIN_PHONE = _optional_str("ADMIN_PHONE")
ADMIN_NAME = _optional_str("ADMIN_NAME", "Администратор")

# Магазины для пустой базы (через запятую). Обычно не нужно: магазины задаются в /setup.
DEFAULT_STORES = tuple(s.strip() for s in (_optional_str("STORES", "")).split(",") if s.strip())


# ================================================================ настройки приложения

@dataclass(frozen=True)
class Setting:
    key: str
    title: str
    group: str
    kind: str = "str"          # str | int | float | time | tz | bool | url | bin
    default: object = None
    secret: bool = False
    restart: bool = False      # изменение требует перезапуска бота (делается автоматически)
    help: str = ""
    env: str = None            # имя переменной окружения, если отличается от key
    hidden: bool = False       # служебная, не показывается в форме
    placeholder: str = ""

    @property
    def env_name(self):
        return self.env or self.key


# Режим установки (deploy/bootstrap-env.sh): server — облачный сервер с доменом, local — ПК в магазине без белого IP
INSTALL_MODE = os.environ.get("POSBOT_MODE", "server").strip().lower() or "server"
LOCAL_MODE = INSTALL_MODE == "local"

GROUPS = {
    "telegram": "Telegram",
    "org": "Организация",
    "web": "Веб-админка и касса-приложение",
    "schedule": "Время и уведомления",
    "labels": "Ценники и импорт",
    "fiscal": "Фискализация (Webkassa)",
    "marking": "Маркировка",
}

TIMEZONES = ("Asia/Almaty", "Asia/Qostanay", "Asia/Qyzylorda", "Asia/Aqtobe", "Asia/Aqtau", "Asia/Atyrau", "Asia/Oral",
             "Asia/Tashkent", "Asia/Bishkek", "Europe/Moscow")

SETTINGS = (
    Setting("TOKEN", "Токен бота", "telegram", secret=True, restart=True,
            help="Выдаёт @BotFather: /newbot → токен вида 123456:ABC…", placeholder="123456789:AA…"),
    Setting("ORG_NAME", "Название ИП / организации", "org", placeholder="ИП Иванов"),
    Setting("ORG_BIN", "ИИН / БИН", "org", kind="bin", help="12 цифр. Понадобится для фискализации."),
    Setting("RECEIPT_FOOTER", "Текст внизу электронного чека", "org",
            placeholder="Гарантия на изделия — 12 месяцев. Обмен и возврат — 14 дней с чеком.",
            help="Гарантия, условия обмена и возврата, контакты. Показывается клиенту в Telegram и на странице чека."),
    Setting("WEB_URL", "Адрес веб-админки", "web", kind="url",
            help="https://ваш-домен — домен должен указывать на IP сервера (A-запись). "
                 "ПК в магазине без белого IP: http://адрес-ПК-в-сети:8000 (касса и админка — только в сети магазина, "
                 "касса-приложение в Telegram — только с туннелем, см. инструкцию). "
                 "Пусто — кнопки админки и кассы-приложения в боте скрыты.",
            placeholder="https://pos.example.kz"),
    Setting("TIMEZONE", "Часовой пояс", "schedule", kind="tz", restart=True,
            default=os.environ.get("TZ") or "Asia/Almaty", env="POSBOT_TIMEZONE",
            help="Даты операций, периоды отчётов и расписание уведомлений."),
    Setting("DAILY_REPORT_TIME", "Ежедневный отчёт, время", "schedule", kind="time",
            default="20:00" if LOCAL_MODE else "23:00", restart=True,
            help="ЧЧ:ММ. Пусто — не отправлять. Если бот в это время был выключен, отчёт придёт после включения."),
    Setting("REPORT_CHAT_ID", "Чат для ежедневного отчёта", "schedule", kind="int",
            help="ID чата/группы Telegram. Пусто — всем администраторам."),
    Setting("LOW_STOCK_TIME", "Уведомление о низком остатке, время", "schedule", kind="time",
            default="11:00" if LOCAL_MODE else "09:00", restart=True, help="ЧЧ:ММ. Пусто — не отправлять."),
    Setting("LOW_STOCK_THRESHOLD", "Порог низкого остатка, шт.", "schedule", kind="int", default=5),
    Setting("LABEL_WIDTH_MM", "Ширина ценника, мм", "labels", kind="float", default=30.0),
    Setting("LABEL_HEIGHT_MM", "Высота ценника, мм", "labels", kind="float", default=20.0),
    Setting("LABEL_TITLE", "Строка вверху ценника", "labels", help="Пусто — название магазина товара."),
    Setting("LABEL_BARCODE", "Штрихкод на ценнике", "labels", kind="bool", default=True,
            help="Штрихкод Code128 с артикулом для обычных USB-сканеров (1D) — в дополнение к QR-коду."),
    Setting("IMPORT_MIN_PRICE_PERCENT", "Минимальная цена при импорте, % от цены", "labels", kind="float",
            default=100.0, help="Если в файле каталога нет колонки минимальной цены."),
    Setting("FISCAL_ENABLED", "Фискализировать операции через Webkassa", "fiscal", kind="bool", default=False,
            help="Выключено — чеки, как и раньше, пробиваются отдельно (ReKassa и т. п.). "
                 "Включено — продажи, возвраты и инкассации пробиваются сами; кассы магазинов (ЗНК) — "
                 "в разделе «Фискализация»."),
    Setting("WEBKASSA_URL", "Адрес API Webkassa", "fiscal", kind="url", default="https://devkkm.webkassa.kz",
            help="Тестовый стенд: https://devkkm.webkassa.kz, рабочий: https://kkm.webkassa.kz"),
    Setting("WEBKASSA_LOGIN", "Логин кассира Webkassa", "fiscal"),
    Setting("WEBKASSA_PASSWORD", "Пароль кассира Webkassa", "fiscal", secret=True),
    Setting("WEBKASSA_API_KEY", "API-ключ интегратора (X-API-KEY)", "fiscal", secret=True),
    Setting("FISCAL_VAT_PERCENT", "НДС в чеке, %", "fiscal", kind="float", default=0.0,
            help="0 — без НДС (ИП на упрощённой декларации). Плательщикам НДС — ставка, например 12."),
    Setting("FISCAL_KASPI_MOBILE", "Kaspi — «мобильный платёж»", "fiscal", kind="bool", default=True,
            help="Включено — оплата Kaspi в чеке как мобильный платёж (Kaspi QR, перевод). "
                 "Выключено — как оплата картой (терминал Kaspi POS)."),
    Setting("MARKING_ENABLED", "Учёт кодов маркировки", "marking", kind="bool", default=False,
            help="Товары с отметкой «Маркируемый» продаются только по скану кода DataMatrix; коды "
                 "принимаются и сверяются в разделе «Маркировка». Передача кодов в ОФД — вместе с фискализацией."),
    # служебные
    Setting("SETUP_DONE", "Первоначальная настройка завершена", "system", kind="bool", default=False, hidden=True),
    Setting("SETUP_CODE", "Код мастера настройки", "system", secret=True, hidden=True),
)
BY_KEY = {s.key: s for s in SETTINGS}


class SettingError(ValueError):
    pass


_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_value(setting: Setting, raw):
    """Строка из формы/окружения/БД → значение нужного типа. Пустое → None. SettingError при ошибке."""
    if isinstance(raw, str):
        raw = raw.strip()
    if setting.kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw or "").lower() in ("1", "true", "yes", "on", "да")
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raw = str(raw)
    kind = setting.kind
    try:
        if kind == "int":
            return int(raw)
        if kind == "float":
            value = float(raw.replace(",", "."))
            if value <= 0:
                raise ValueError
            return value
    except ValueError:
        raise SettingError(f"«{setting.title}»: нужно число.")
    if kind == "time":
        m = _HHMM.match(raw)
        if not m:
            raise SettingError(f"«{setting.title}»: формат ЧЧ:ММ, например 23:00.")
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if kind == "tz":
        try:
            ZoneInfo(raw)
        except Exception:
            raise SettingError(f"«{setting.title}»: неизвестный часовой пояс {raw!r}.")
        return raw
    if kind == "url":
        raw = raw.rstrip("/")
        if not re.match(r"^https?://[^\s/]+", raw):
            raise SettingError(f"«{setting.title}»: адрес вида https://pos.example.kz")
        return raw
    if kind == "bin":
        digits = re.sub(r"\D", "", raw)
        if len(digits) != 12:
            raise SettingError(f"«{setting.title}»: 12 цифр.")
        return digits
    if setting.key == "TOKEN" and not re.match(r"^\d{3,}:[\w-]{3,}$", raw):
        raise SettingError("Токен бота выглядит неправильно: ожидается вид 123456789:AA… из @BotFather.")
    return raw


def env_value(setting: Setting):
    """Значение из окружения или None. WEB_URL можно задать и через WEB_DOMAIN (старые установки)."""
    raw = os.environ.get(setting.env_name)
    if (raw is None or not raw.strip()) and setting.key == "WEB_URL" and os.environ.get("WEB_DOMAIN", "").strip():
        raw = "https://" + os.environ["WEB_DOMAIN"].strip()
    if raw is None or not raw.strip():
        return None
    try:
        return parse_value(setting, raw)
    except SettingError as e:
        if setting.key not in _warned_env:
            _warned_env.add(setting.key)
            import logging
            logging.getLogger(__name__).error("Переменная окружения %s проигнорирована: %s", setting.env_name, e)
        return None


_warned_env = set()


def env_overridden(key) -> bool:
    return env_value(BY_KEY[key]) is not None


_db_values = {}


def apply(db_values=None):
    """Пересчитывает атрибуты модуля: окружение > БД > по умолчанию."""
    global _db_values
    if db_values is not None:
        _db_values = dict(db_values)
    g = globals()
    for s in SETTINGS:
        value = env_value(s)
        if value is None:
            value = _db_values.get(s.key)
        if value is None:
            value = s.default
        g[s.key] = value


def source(key):
    """Откуда взято значение: env | db | default."""
    if env_overridden(key):
        return "env"
    return "db" if _db_values.get(key) is not None else "default"


# Начальные значения (до чтения БД): окружение и значения по умолчанию.
TOKEN = ORG_NAME = ORG_BIN = RECEIPT_FOOTER = WEB_URL = TIMEZONE = DAILY_REPORT_TIME = REPORT_CHAT_ID = None
LOW_STOCK_TIME = LOW_STOCK_THRESHOLD = LABEL_WIDTH_MM = LABEL_HEIGHT_MM = LABEL_TITLE = LABEL_BARCODE = None
IMPORT_MIN_PRICE_PERCENT = FISCAL_ENABLED = WEBKASSA_URL = WEBKASSA_LOGIN = WEBKASSA_PASSWORD = None
WEBKASSA_API_KEY = SETUP_DONE = SETUP_CODE = None
apply({})


def mini_app_url():
    """Адрес кассы-приложения (Telegram Mini App) или None: Telegram открывает Mini App только по HTTPS."""
    if WEB_URL and str(WEB_URL).startswith("https://"):
        return f"{WEB_URL}/app"
    return None
