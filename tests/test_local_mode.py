"""Установка на ПК в магазине без белого IP (#28): досылка отчётов, связь с Telegram, Mini App только по HTTPS."""
from datetime import timedelta

from fastapi.testclient import TestClient
from starlette.requests import Request
from telebot import types

from core import config
import setup_code
from conftest import ADMIN, CASHIER, login, db_query
from core.database import record_sale, get_job_run, mark_job_run
from handlers.web_handler import send_link
from core.state import bot
from utils.common import now, today
from utils import scheduled_jobs as jobs
from web.app import app, _suggest_web_url

MIDNIGHT = (0, 0)   # отчёт «в полночь»: отчёт за сегодня уже «должен был уйти» в любой момент дня


def _sell():
    pid = db_query("SELECT id FROM products WHERE article_number='A100'")[0]['id']
    assert record_sale(pid, "A100", db_query("SELECT id FROM users WHERE role='admin'")[0]['id'], 150000, "Kaspi")[0]


def _reports(tg):
    return [c for c in tg.calls if c.text and c.text.startswith("📊")]


def test_first_start_only_sets_baseline(tg):
    login(tg, ADMIN)
    _sell()
    tg.calls.clear()
    assert jobs.catch_up_daily_reports((23, 59), now().replace(hour=10)) == []
    assert get_job_run("daily_report")['last_for_date'] == today() - timedelta(days=1)
    assert not _reports(tg)


def test_catch_up_sends_missed_days_with_operations(tg):
    login(tg, ADMIN)
    mark_job_run("daily_report", today() - timedelta(days=3))      # ПК выключали три дня
    _sell()
    tg.calls.clear()
    assert jobs.catch_up_daily_reports(MIDNIGHT) == [today()]     # пустые дни пропущены, сегодняшний — отправлен
    reports = _reports(tg)
    assert len(reports) == 1 and "не был отправлен вовремя" in reports[0].text and tg.documents
    assert get_job_run("daily_report")['last_for_date'] == today()
    # повторно и по расписанию — не дублируется
    assert jobs.catch_up_daily_reports(MIDNIGHT) == []
    jobs.scheduled_daily_report()
    assert len(_reports(tg)) == 1


def test_catch_up_waits_for_connection(tg, monkeypatch):
    login(tg, ADMIN)
    mark_job_run("daily_report", today() - timedelta(days=1))
    _sell()
    monkeypatch.setattr(tg, "online", False)
    assert jobs.catch_up_daily_reports(MIDNIGHT) == []
    assert get_job_run("daily_report")['last_for_date'] == today() - timedelta(days=1)   # не отмечен — повторим
    monkeypatch.setattr(tg, "online", True)
    assert jobs.catch_up_daily_reports(MIDNIGHT) == [today()]


def test_catch_up_is_limited_to_a_week(tg):
    mark_job_run("daily_report", today() - timedelta(days=60))
    jobs.catch_up_daily_reports(MIDNIGHT)
    assert get_job_run("daily_report")['last_for_date'] == today()


def test_scheduled_report_before_report_time_is_not_due(tg):
    login(tg, ADMIN)
    mark_job_run("daily_report", today() - timedelta(days=1))
    _sell()
    assert jobs.catch_up_daily_reports((23, 59), now().replace(hour=10, minute=0)) == []   # сегодняшний ещё рано


def test_telegram_heartbeat_in_health(tg, monkeypatch):
    c = TestClient(app)
    assert c.get("/health").json()["telegram_seconds_ago"] is None
    monkeypatch.setattr(tg, "online", False)
    assert jobs.telegram_heartbeat() is False
    monkeypatch.setattr(tg, "online", True)
    assert jobs.telegram_heartbeat() is True
    health = c.get("/health").json()
    assert 0 <= health["telegram_seconds_ago"] < 5 and health["mode"] == "server"
    assert health["web_url"] == "https://pos.test"


def test_mini_app_only_over_https(tg, monkeypatch):
    login(tg, CASHIER)
    assert isinstance(tg.menu_buttons[CASHIER], types.MenuButtonWebApp)
    monkeypatch.setattr(config, "WEB_URL", "http://192.168.1.5:8000")
    assert config.mini_app_url() is None
    calls = tg.send(CASHIER, "/app")
    assert "только через интернет-адрес с HTTPS" in tg.texts(calls)
    assert calls[-1].markup.keyboard[0][0].url == "http://192.168.1.5:8000/kassa"
    assert isinstance(tg.menu_buttons[CASHIER], types.MenuButtonDefault)
    monkeypatch.setattr(config, "WEB_URL", "https://abc.ts.net")
    calls = tg.send(CASHIER, "/app")
    assert calls[-1].markup.keyboard[0][0].web_app.url == "https://abc.ts.net/app"


def test_link_falls_back_to_text(tg, monkeypatch):
    sent = []

    def picky(chat_id, text, reply_markup=None, **kw):
        if reply_markup is not None:
            raise RuntimeError("Bad Request: BUTTON_URL_INVALID")
        sent.append(text)

    monkeypatch.setattr(bot, "send_message", picky)
    send_link(ADMIN, "Ссылка для входа", "Открыть", "http://localhost:8000/login?t=x")
    assert sent == ["Ссылка для входа\n\nhttp://localhost:8000/login?t=x"]


def test_web_url_from_command_line(tg, monkeypatch):
    monkeypatch.delenv("WEB_URL", raising=False)
    assert setup_code.main(["--web-url", "ftp://bad"]) == 1
    assert setup_code.main(["--web-url", "http://192.168.1.5:8000/"]) == 0
    assert config.WEB_URL == "http://192.168.1.5:8000"
    monkeypatch.setenv("WEB_URL", "https://pos.test")
    assert setup_code.main(["--web-url", "https://other.kz"]) == 1        # задан в .env — там и менять


def _request(host):
    return Request({"type": "http", "method": "GET", "path": "/setup", "headers": [(b"host", host.encode())]})


def test_setup_suggests_lan_address(monkeypatch):
    assert _suggest_web_url(_request("pos.example.kz")) == "https://pos.example.kz"
    assert _suggest_web_url(_request("192.168.1.5:8000")) == ""
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setenv("POSBOT_LAN_URL", "http://192.168.1.5:8000")
    assert _suggest_web_url(_request("192.168.1.5:8000")) == "http://192.168.1.5:8000"
    assert _suggest_web_url(_request("localhost:8000")) == "http://192.168.1.5:8000"
