"""Хранение настроек в БД (таблица settings) и шифрование секретов.

Секреты (токен бота, пароли и ключи сервисов) шифруются Fernet ключом POSBOT_MASTER_KEY
из .env. В журнал settings_log пишется только факт изменения, без значений.
"""
import base64
import hashlib
import logging
import secrets
import threading
import time

from cryptography.fernet import Fernet, InvalidToken

import config
from database import with_connection, with_transaction
from utils.common import now

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_loaded_at = 0.0
_warned_no_key = False


# ---------------------------------------------------------------- шифрование

def _fernet():
    global _warned_no_key
    key = config.MASTER_KEY
    if key:
        try:
            return Fernet(key.encode())
        except ValueError:
            # Не формат Fernet (например, задан вручную) — выводим ключ из строки
            pass
    else:
        if not _warned_no_key:
            logger.warning("POSBOT_MASTER_KEY не задан: секреты шифруются ключом, выведенным из пароля БД. "
                           "Запустите deploy/bootstrap-env.sh, чтобы создать отдельный ключ.")
            _warned_no_key = True
        key = "db:" + (config.DB_PASS or "")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("posbot:" + key).encode()).digest()))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def _to_db(setting, value):
    if value is None:
        return None
    if setting.kind == "bool":
        return "1" if value else "0"
    if setting.kind == "float":
        return f"{value:g}"
    return str(value)


# ---------------------------------------------------------------- чтение

@with_connection
def _read_rows(cursor):
    cursor.execute("SELECT setting_key, value, is_secret, updated_at FROM settings")
    return cursor.fetchall()


def read_db_values():
    """{key: значение} из БД. None, если БД недоступна или таблицы ещё нет."""
    rows = _read_rows()
    if rows is None:
        return None
    values = {}
    for row in rows:
        setting = config.BY_KEY.get(row['setting_key'])
        if setting is None or row['value'] is None:
            continue
        raw = row['value']
        if row['is_secret']:
            raw = decrypt(raw)
            if raw is None:
                logger.error("Настройка %s: не удалось расшифровать (сменился POSBOT_MASTER_KEY?). "
                             "Задайте её заново в веб-админке.", setting.key)
                continue
        try:
            values[setting.key] = config.parse_value(setting, raw)
        except config.SettingError as e:
            logger.error("Настройка %s в БД некорректна: %s", setting.key, e)
    return values


def reload() -> bool:
    """Перечитывает настройки из БД в модуль config. False, если БД недоступна."""
    global _loaded_at
    values = read_db_values()
    if values is None:
        return False
    with _lock:
        config.apply(values)
        _loaded_at = time.monotonic()
    return True


def reload_if_stale(max_age=5.0):
    if time.monotonic() - _loaded_at > max_age:
        reload()


def snapshot(keys):
    return tuple(getattr(config, k) for k in keys)


RESTART_KEYS = tuple(s.key for s in config.SETTINGS if s.restart)


# ---------------------------------------------------------------- запись

@with_transaction
def _write(cursor, changes, user_id):
    stamp = now()
    changed = []
    for key, value in changes.items():
        setting = config.BY_KEY[key]
        stored = _to_db(setting, value)
        cursor.execute("SELECT value FROM settings WHERE setting_key = %s FOR UPDATE", (key,))
        row = cursor.fetchone()
        old = row['value'] if row else None
        if setting.secret and old is not None:
            old = decrypt(old)
        if old == stored:
            continue
        if stored is None:
            cursor.execute("DELETE FROM settings WHERE setting_key = %s", (key,))
            action = "clear"
        else:
            db_value = encrypt(stored) if setting.secret else stored
            cursor.execute(
                "INSERT INTO settings (setting_key, value, is_secret, updated_at, updated_by) VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE value = VALUES(value), is_secret = VALUES(is_secret), "
                "updated_at = VALUES(updated_at), updated_by = VALUES(updated_by)",
                (key, db_value, int(setting.secret), stamp, user_id))
            action = "set"
        if not setting.hidden:
            cursor.execute("INSERT INTO settings_log (setting_key, action, user_id, changed_at) VALUES (%s, %s, %s, %s)",
                           (key, action, user_id, stamp))
        changed.append(key)
    return True, changed


def save(changes: dict, user_id=None):
    """Сохраняет {key: значение} (None — удалить). Настройки из окружения пропускаются.
    Возвращает (ok, список изменённых ключей | текст ошибки)."""
    allowed = {k: v for k, v in changes.items() if k in config.BY_KEY and not config.env_overridden(k)}
    ok, result = _write(allowed, user_id)
    if ok:
        reload()
        if result:
            logger.info("Изменены настройки: %s", ", ".join(result))
    return ok, result


@with_connection
def recent_changes(cursor, limit=20):
    cursor.execute("SELECT l.setting_key, l.action, l.changed_at, u.name FROM settings_log l "
                   "LEFT JOIN users u ON u.id = l.user_id ORDER BY l.id DESC LIMIT %s", (limit,))
    return cursor.fetchall()


# ---------------------------------------------------------------- первоначальная настройка

def is_configured() -> bool:
    return bool(config.SETUP_DONE)


_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@with_transaction
def _create_setup_code(cursor):
    cursor.execute("SELECT value FROM settings WHERE setting_key = 'SETUP_CODE' FOR UPDATE")
    if cursor.fetchone():
        return True, False
    code = "-".join("".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)) for _ in range(3))
    cursor.execute("INSERT INTO settings (setting_key, value, is_secret, updated_at) VALUES ('SETUP_CODE', %s, 1, %s)",
                   (encrypt(code), now()))
    return True, True


def ensure_setup_code():
    """Код для мастера /setup (создаётся один раз, пока настройка не завершена)."""
    reload()
    if is_configured():
        return None
    if not config.SETUP_CODE:
        _create_setup_code()
        reload()
    return config.SETUP_CODE


def check_setup_code(code) -> bool:
    expected = config.SETUP_CODE
    if not expected or not code:
        return False
    normalized = code.strip().upper().replace(" ", "")
    return secrets.compare_digest(normalized.encode(), expected.encode())


@with_connection
def _has_active_admin(cursor):
    cursor.execute("SELECT 1 FROM users WHERE role = 'admin' AND is_active = 1 LIMIT 1")
    return cursor.fetchone() is not None


def adopt_existing_install():
    """Установка до мастера настройки: токен уже есть (в .env) и есть администратор — считаем настроенной."""
    if is_configured() or not config.TOKEN:
        return False
    if _has_active_admin():
        ok, _ = save({"SETUP_DONE": True, "SETUP_CODE": None})
        if ok:
            logger.info("Найдена рабочая установка (токен и администратор уже есть): мастер /setup не нужен.")
        return ok
    return False


def finish_setup(user_id=None):
    return save({"SETUP_DONE": True, "SETUP_CODE": None}, user_id)


def log_setup_hint():
    code = ensure_setup_code()
    if code:
        where = f"{config.WEB_URL}/setup" if config.WEB_URL else "https://<ваш-домен>/setup"
        logger.warning("Первоначальная настройка не выполнена. Откройте %s (или через SSH-туннель "
                       "http://127.0.0.1:8000/setup). Код настройки: %s", where, code)
    return code


# ---------------------------------------------------------------- проверка токена

def check_telegram_token(token):
    """(True, '@username') или (False, причина). Делает запрос getMe к Telegram."""
    from telebot import apihelper
    try:
        me = apihelper.get_me(token)
    except apihelper.ApiTelegramException as e:
        if e.error_code in (401, 404):
            return False, "Telegram не принял токен: проверьте, что он скопирован из @BotFather целиком."
        return False, f"Telegram ответил ошибкой: {e.description}"
    except Exception as e:  # сеть
        logger.warning("Проверка токена: %s", e)
        return False, "Не удалось связаться с Telegram. Проверьте доступ сервера в интернет."
    return True, "@" + (me.get('username') or '?')

