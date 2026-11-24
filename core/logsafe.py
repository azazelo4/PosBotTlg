"""Логи без токена бота и текста сообщений (аудит, Н-2).

Токен стоит в адресе каждого запроса к Telegram (api.telegram.org/bot<ТОКЕН>/метод?параметры), и ошибка сети
(requests.ConnectionError и т. п.) содержит этот адрес целиком — вместе с текстом сообщения, например чеком
клиента. install() подменяет фабрику записей лога: в каждой записи — и в тексте, и в трассировке — токен
и параметры запросов к Telegram вырезаются, где бы ни произошла ошибка. describe() — короткое описание ошибки
Telegram для логов (тип и код, без адреса).
"""
import logging
import re
import traceback

from core import config

MASK = "<токен>"
_TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")
# метод Bot API с параметрами: /bot<токен>/sendMessage?chat_id=…&text=… → /bot<токен>/sendMessage?…
_QUERY_RE = re.compile(r"(/bot[^/\s'\"]+/[A-Za-z]+)\?[^\s'\")]*")
_installed = False


def scrub(text):
    """Текст без токена бота и параметров запросов к Telegram."""
    if not text:
        return text
    token = config.TOKEN
    if token and len(token) > 8:
        text = text.replace(token, MASK)
    text = _TOKEN_RE.sub(MASK, text)
    return _QUERY_RE.sub(r"\1?…", text)


def describe(error):
    """Ошибка запроса к Telegram для лога: без адреса запроса, токена и текста сообщения."""
    code = getattr(error, 'error_code', None)
    if code is not None:                                  # ApiTelegramException: ответ Telegram
        return f"Telegram {code}: {scrub(str(getattr(error, 'description', '') or ''))[:200]}"
    name = type(error).__name__
    if type(error).__module__.startswith(('requests', 'urllib3')):
        return f"{name} (нет связи с Telegram)"
    return f"{name}: {scrub(str(error))[:200]}"


def install():
    """Маскировать токен во всех записях лога процесса. Повторный вызов ничего не меняет."""
    global _installed
    if _installed:
        return
    _installed = True
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:                                 # кривой формат — оставляем как есть, logging сам сообщит
            return record
        record.msg, record.args = scrub(message), None
        if record.exc_info and record.exc_info[0] is not None:
            text = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
            clean = scrub(text)
            if clean != text:
                record.exc_text = clean
        if record.stack_info:
            record.stack_info = scrub(record.stack_info)
        return record

    logging.setLogRecordFactory(factory)
