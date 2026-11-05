"""Аутентификация веба.

* Админка: одноразовая ссылка из бота (кнопка «Веб-админка») → подписанная cookie-сессия.
* Mini App: каждый запрос несёт Telegram initData, подпись проверяется токеном бота.
"""
import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta
from urllib.parse import parse_qsl

import config
from database import create_login_token, consume_login_token
from utils.common import now

LOGIN_TTL = timedelta(minutes=10)
INIT_DATA_MAX_AGE = 24 * 3600
ADMIN_ROLES = ('admin', 'manager')
CASH_ROLES = ('admin', 'manager', 'cashier')


def session_secret():
    """Ключ подписи cookie. Не зависит от токена бота: веб работает и до настройки."""
    if config.WEB_SECRET:
        return config.WEB_SECRET
    base = config.MASTER_KEY or ("db:" + (config.DB_PASS or ""))
    return hashlib.sha256(("posbot-web:" + base).encode()).hexdigest()


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_login_link(user_id):
    """Создаёт одноразовую ссылку входа (10 минут). None, если веб не настроен."""
    if not config.WEB_URL:
        return None
    token = secrets.token_urlsafe(24)
    ok, _ = create_login_token(user_id, _hash(token), now() + LOGIN_TTL)
    return f"{config.WEB_URL}/login?t={token}" if ok else None


def redeem_login_token(token):
    """(True, user_id) или (False, причина)."""
    if not token or len(token) > 100:
        return False, "Ссылка для входа недействительна."
    return consume_login_token(_hash(token))


def validate_init_data(init_data, bot_token=None, max_age=INIT_DATA_MAX_AGE):
    """Проверяет подпись Telegram WebApp initData. Возвращает dict пользователя Telegram или None."""
    token = bot_token or config.TOKEN
    if not init_data or not token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = pairs.pop('hash', None)
    if not received:
        return None
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        auth_date = int(pairs.get('auth_date', 0))
        user = json.loads(pairs['user'])
    except (KeyError, ValueError):
        return None
    if max_age and time.time() - auth_date > max_age:
        return None
    return user


def new_csrf():
    return secrets.token_urlsafe(16)
