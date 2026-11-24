"""Аудит Н-2: токен бота и текст сообщений не попадают в логи — ни в текст записи, ни в трассировку."""
import logging

import pytest
import requests
from telebot import TeleBot, apihelper

from core import config, logsafe
from core.logsafe import describe, scrub

TOKEN = "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
PHONE = "7701" + "1112233"           # собирается из частей: строка исходника в трассировке не должна содержать номер
TEXT = "Анна +" + PHONE + " кольцо 150000"


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(config, "TOKEN", TOKEN)
    logsafe.install()
    return TOKEN


def _network_error(token):
    """Настоящая ошибка сети от библиотеки Telegram: в тексте — адрес запроса с токеном и текстом сообщения."""
    old = apihelper.API_URL, apihelper.CONNECT_TIMEOUT
    apihelper.API_URL, apihelper.CONNECT_TIMEOUT = "https://127.0.0.1:9/bot{0}/{1}", 1
    try:
        TeleBot(token).send_message(1, TEXT)
    except Exception as e:
        return e
    finally:
        apihelper.API_URL, apihelper.CONNECT_TIMEOUT = old
    raise AssertionError("ожидалась ошибка сети")


def test_scrub_and_describe(token):
    assert scrub(f"url: /bot{token}/sendMessage?chat_id=1&text=Анна") == "url: /bot<токен>/sendMessage?…"
    assert "<токен>" in scrub("other 987654321:ZZHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1 token")
    assert scrub("обычный текст 12:30") == "обычный текст 12:30"
    err = apihelper.ApiTelegramException("sendMessage", None, {"error_code": 403, "description": "Forbidden: bot was blocked"})
    assert describe(err) == "Telegram 403: Forbidden: bot was blocked"
    assert describe(requests.exceptions.ConnectionError(f"/bot{token}/getMe")) == "ConnectionError (нет связи с Telegram)"
    assert token not in describe(ValueError(f"x /bot{token}/x"))


def test_real_network_error_is_masked(token, caplog):
    error = _network_error(token)
    assert token in str(error) and PHONE in str(error)          # без маскировки токен утёк бы
    log = logging.getLogger("posbot.test")
    with caplog.at_level(logging.INFO):
        log.warning("Не удалось отправить: %s", error)
        try:
            raise error
        except Exception:
            log.exception("Ошибка при отправке")
    assert caplog.records
    assert token not in caplog.text and PHONE not in caplog.text
    assert "<токен>" in caplog.text and "Traceback" in caplog.text


def test_failed_customer_receipt_logs_no_token(tg, token, caplog, monkeypatch):
    """Путь из аудита: чек клиенту не доставлен — в логе нет ни токена, ни содержимого чека."""
    from utils import scheduled_jobs
    from core.state import bot
    error = requests.exceptions.ConnectionError(f"url: /bot{token}/sendMessage?chat_id=5&text=Чек+{PHONE}")
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: (_ for _ in ()).throw(error))
    with caplog.at_level(logging.WARNING):
        assert scheduled_jobs._send_safe(5, "x") is False
    assert "нет связи с Telegram" in caplog.text and token not in caplog.text and PHONE not in caplog.text


def test_uvicorn_access_log_still_formats(token):
    """Ревью: аргументы записи сохраняются — журнал запросов uvicorn разбирает их кортежем."""
    import io
    from uvicorn.logging import AccessFormatter
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s'))
    handler.handleError = lambda record: out.write("Logging error\n")
    access = logging.getLogger("uvicorn.access.test")
    access.addHandler(handler)
    access.propagate = False
    try:
        access.warning('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", f"/x?t={token}", "1.1", 200)
    finally:
        access.removeHandler(handler)
    text = out.getvalue()
    assert "Logging error" not in text and "GET /x?t=<токен> HTTP/1.1" in text and token not in text
    assert logging.getLogger().makeRecord("x", 30, "f", 1, "число %d, доля %.1f%%", (5, 12.5), None).getMessage() \
        == "число 5, доля 12.5%"
