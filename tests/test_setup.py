"""Настройки в БД, мастер /setup, страница /settings и деплой без секретов (#15)."""
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import config
import main
from core import settings_store
from conftest import ADMIN, MANAGER, login, db_query
from test_stores import _web, _csrf
from web.app import app

BOOTSTRAP = Path(__file__).resolve().parent.parent / "deploy" / "bootstrap-env.sh"
NEW_TOKEN = "987654321:AAFakeTokenForTests_abcdefghijk"


@pytest.fixture
def fresh(tg, monkeypatch):
    """Чистый сервер: ни токена в окружении, ни настроек, ни сотрудников, ни магазинов."""
    monkeypatch.delenv("TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_PHONE", raising=False)
    monkeypatch.delenv("WEB_URL", raising=False)
    monkeypatch.setattr(config, "ADMIN_PHONE", None)
    for table in ("products", "users", "stores", "settings", "settings_log"):
        db_query(f"DELETE FROM `{table}`")
    settings_store.reload()
    checked = []

    def fake_check(token):
        checked.append(token)
        return (True, "@test_pos_bot") if token == NEW_TOKEN else (False, "Telegram не принял токен")
    monkeypatch.setattr(settings_store, "check_telegram_token", fake_check)
    client = TestClient(app, base_url="https://pos.example.kz")
    client.checked = checked
    yield client
    # Вернуть тестовый стенд в обычное состояние для остальных тестов
    monkeypatch.undo()
    db_query("DELETE FROM settings")
    settings_store.reload()


def _setup_form(client, **fields):
    page = client.get("/setup")
    assert page.status_code == 200, page.text
    data = {"csrf": re.search(r'name="csrf" value="([^"]+)"', page.text).group(1),
            "code": config.SETUP_CODE, "TOKEN": NEW_TOKEN, "ORG_NAME": "ИП Тест", "ORG_BIN": "990101300123",
            "WEB_URL": "https://pos.example.kz", "TIMEZONE": "Asia/Almaty", "LABEL_WIDTH_MM": "40",
            "LABEL_HEIGHT_MM": "30", "WEBKASSA_URL": "https://devkkm.webkassa.kz",
            "stores": "Бутик Мега\nБутик Керуен", "admin_name": "Айгерим", "admin_phone": "8 701 555 44 33"}
    data.update(fields)
    return client.post("/setup", data={k: v for k, v in data.items() if v is not None})


def test_clean_server_waits_for_setup(fresh):
    assert not settings_store.is_configured() and config.TOKEN is None
    r = fresh.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    assert fresh.get("/health").json() == {"ok": True, "configured": False, "version": "dev", "mode": "server",
                                           "web_url": "", "telegram_seconds_ago": None}
    page = fresh.get("/setup")
    assert "Первоначальная настройка" in page.text and 'name="stores"' in page.text
    assert 'value="https://pos.example.kz"' in page.text      # адрес подсказан по домену запроса
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", config.SETUP_CODE)
    assert config.SETUP_CODE not in page.text                  # код не показывается на странице


def test_setup_wizard_configures_everything(fresh):
    r = _setup_form(fresh, code="WRONG-CODE-0000")
    assert r.status_code == 403 and "Неверный код" in r.text
    assert db_query("SELECT COUNT(*) AS n FROM stores")[0]['n'] == 0

    r = _setup_form(fresh, code=config.SETUP_CODE.lower())    # регистр не важен
    assert r.status_code == 200 and "Настройка сохранена" in r.text and "@test_pos_bot" in r.text, r.text
    assert fresh.checked == [NEW_TOKEN]
    assert [s['name'] for s in db_query("SELECT name FROM stores ORDER BY sort_order")] == ["Бутик Мега", "Бутик Керуен"]
    admin = db_query("SELECT * FROM users")[0]
    assert (admin['role'], admin['name'], admin['phone_number']) == ("admin", "Айгерим", "+77015554433")
    rows = {r['setting_key']: r for r in db_query("SELECT * FROM settings")}
    assert rows['TOKEN']['is_secret'] == 1 and NEW_TOKEN not in rows['TOKEN']['value']     # зашифрован
    assert rows['ORG_BIN']['value'] == "990101300123" and 'SETUP_CODE' not in rows
    assert config.TOKEN == NEW_TOKEN and config.LABEL_WIDTH_MM == 40 and config.FISCAL_ENABLED is False
    # В журнале — только факт изменения
    log = db_query("SELECT * FROM settings_log")
    assert {r['setting_key'] for r in log} >= {"TOKEN", "ORG_NAME"} and "value" not in log[0]
    # Мастер закрыт
    r = fresh.get("/setup", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    assert fresh.post("/setup", data={"code": "x"}, follow_redirects=False).status_code == 303
    assert fresh.get("/health").json()["configured"] is True
    assert fresh.get("/", follow_redirects=False).status_code == 401   # дальше — обычный вход через бота


def test_setup_validation_saves_nothing(fresh):
    r = _setup_form(fresh, TOKEN="", stores="", admin_phone="123", ORG_BIN="12", FISCAL_ENABLED="on",
                    LABEL_WIDTH_MM="abc")
    assert r.status_code == 400
    for text in ("Укажите токен", "хотя бы один магазин", "Телефон администратора", "ИИН / БИН",
                 "Для фискализации заполните", "нужно число"):
        assert text in r.text, text
    assert 'value="ИП Тест"' in r.text                          # введённое не теряется
    r = _setup_form(fresh, TOKEN="123456:BadTokenRejectedByTelegram")
    assert r.status_code == 400 and "Telegram не принял токен" in r.text
    assert db_query("SELECT COUNT(*) AS n FROM settings WHERE setting_key != 'SETUP_CODE'")[0]['n'] == 0
    assert not settings_store.is_configured()


def test_setup_with_fiscal_and_brute_force_limit(fresh, monkeypatch):
    r = _setup_form(fresh, FISCAL_ENABLED="on", WEBKASSA_LOGIN="cashier@test.kz", WEBKASSA_PASSWORD="p@ss\\word",
                    WEBKASSA_API_KEY="key-123")
    assert r.status_code == 200, r.text
    assert config.FISCAL_ENABLED is True and config.WEBKASSA_PASSWORD == "p@ss\\word"
    assert db_query("SELECT is_secret FROM settings WHERE setting_key='WEBKASSA_API_KEY'")[0]['is_secret'] == 1


def test_setup_code_attempts_are_limited(fresh, monkeypatch):
    monkeypatch.setattr("web.app._setup_attempts", {})
    for _ in range(10):
        assert _setup_form(fresh, code="AAAA-BBBB-CCCC").status_code == 403
    assert _setup_form(fresh).status_code == 429


def test_bot_waits_for_token_then_starts(fresh):
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:      # пока бот ждёт, администратор проходит мастер
            settings_store.save({"TOKEN": NEW_TOKEN})
            settings_store.finish_setup()
    assert main.wait_for_token(sleep=fake_sleep) == NEW_TOKEN
    assert sleeps == [main.TOKEN_WAIT_SECONDS] * 2


def test_existing_install_is_adopted(tg):
    # conftest: TOKEN и ADMIN_PHONE в окружении, администратор есть → мастер не нужен
    assert settings_store.is_configured()
    assert config.source("TOKEN") == "env"


def test_env_overrides_db(tg, monkeypatch):
    settings_store.save({"LABEL_TITLE": "Из базы"})
    assert config.LABEL_TITLE == "Из базы" and config.source("LABEL_TITLE") == "db"
    monkeypatch.setenv("LABEL_TITLE", "Из окружения")
    settings_store.reload()
    assert config.LABEL_TITLE == "Из окружения" and config.source("LABEL_TITLE") == "env"
    ok, changed = settings_store.save({"LABEL_TITLE": "Другое"})
    assert ok and changed == []                                 # настройку из окружения не перезаписать
    monkeypatch.delenv("LABEL_TITLE")
    settings_store.reload()
    assert config.LABEL_TITLE == "Из базы"


def test_secrets_need_the_master_key(tg, monkeypatch):
    settings_store.save({"WEBKASSA_API_KEY": "secret-1"})
    stored = db_query("SELECT value FROM settings WHERE setting_key='WEBKASSA_API_KEY'")[0]['value']
    assert "secret-1" not in stored and settings_store.decrypt(stored) == "secret-1"
    monkeypatch.setattr(config, "MASTER_KEY", "другой ключ")
    settings_store.reload()
    assert config.WEBKASSA_API_KEY is None                      # не расшифровать — считается не заданным
    monkeypatch.undo()
    settings_store.reload()
    assert config.WEBKASSA_API_KEY == "secret-1"


def test_settings_page(tg):
    manager = _web(tg, MANAGER)
    assert manager.get("/settings").status_code == 403
    settings_store.save({"WEBKASSA_PASSWORD": "hidden-pass"})
    client = _web(tg, ADMIN)
    page = client.get("/settings").text
    assert "hidden-pass" not in page and "123456:TEST" not in page
    assert "задан — оставьте пустым" in page
    assert re.search(r'value="https://pos.test" disabled', page)          # из окружения — только чтение
    token = _csrf(client, "/settings")
    form = {"csrf": token, "LABEL_WIDTH_MM": "58", "LABEL_HEIGHT_MM": "40", "TIMEZONE": "Asia/Almaty",
            "DAILY_REPORT_TIME": "22:30", "LOW_STOCK_THRESHOLD": "3", "WEBKASSA_URL": "https://kkm.webkassa.kz",
            "WEB_URL": "https://evil.example", "IMPORT_MIN_PRICE_PERCENT": "90"}
    page = client.post("/settings", data=form).text
    assert "Сохранено" in page and "Бот перезапустится" in page        # время отчёта — перезапуск
    assert config.LABEL_WIDTH_MM == 58 and config.DAILY_REPORT_TIME == "22:30" and config.WEB_URL == "https://pos.test"
    assert config.WEBKASSA_PASSWORD == "hidden-pass"                     # пустое поле секрета — без изменений
    assert "Ширина ценника" in page and "Админ" in page                  # журнал изменений
    # Применяется в боте без перезапуска
    login(tg, ADMIN)
    assert "Ценники 58×40 мм" in tg.texts(tg.send(ADMIN, "Ценники"))
    tg.send(ADMIN, "Отмена")
    # Ошибки не сохраняются; удалить секрет можно, токен — нельзя
    page = client.post("/settings", data={**form, "DAILY_REPORT_TIME": "25:00", "LABEL_WIDTH_MM": "60"}).text
    assert "ЧЧ:ММ" in page and config.LABEL_WIDTH_MM == 58
    client.post("/settings", data={**form, "clear_WEBKASSA_PASSWORD": "on"})
    assert config.WEBKASSA_PASSWORD is None
    page = client.post("/settings", data={**form, "FISCAL_ENABLED": "on"}).text
    assert "Для фискализации заполните" in page and config.FISCAL_ENABLED is False
    assert "Изменений нет" in client.post("/settings", data=form).text


def test_clearing_report_time_disables_report(tg, monkeypatch):
    """Стёрли время отчёта или остатков — уведомление выключено, а не возвращается время по умолчанию."""
    client = _web(tg, ADMIN)
    form = {"csrf": _csrf(client, "/settings"), "LABEL_WIDTH_MM": "30", "LABEL_HEIGHT_MM": "20",
            "TIMEZONE": "Asia/Almaty", "LOW_STOCK_THRESHOLD": "5", "WEBKASSA_URL": "https://devkkm.webkassa.kz",
            "IMPORT_MIN_PRICE_PERCENT": "100", "DAILY_REPORT_TIME": "", "LOW_STOCK_TIME": ""}
    assert config.DAILY_REPORT_TIME and config.LOW_STOCK_TIME                 # по умолчанию включены
    page = client.post("/settings", data=form).text
    assert "Сохранено" in page and "Бот перезапустится" in page
    assert config.DAILY_REPORT_TIME is None and config.LOW_STOCK_TIME is None
    assert {r['value'] for r in db_query("SELECT value FROM settings WHERE setting_key IN "
                                         "('DAILY_REPORT_TIME', 'LOW_STOCK_TIME')")} == {"off"}
    settings_store.reload()                                                  # после перезапуска — тоже выключено
    assert config.DAILY_REPORT_TIME is None and config.LOW_STOCK_TIME is None
    page = client.get("/settings").text
    assert re.search(r'name="DAILY_REPORT_TIME" value="" placeholder="не отправлять"', page)  # поле пустое
    assert main._parse_hhmm(config.DAILY_REPORT_TIME, "DAILY_REPORT_TIME") is None
    assert "Изменений нет" in client.post("/settings", data=form).text
    # включили снова
    client.post("/settings", data={**form, "DAILY_REPORT_TIME": "21:00"})
    assert config.DAILY_REPORT_TIME == "21:00" and config.LOW_STOCK_TIME is None
    # в окружении тоже можно выключить
    monkeypatch.setenv(config.BY_KEY["LOW_STOCK_TIME"].env_name, "off")
    db_query("DELETE FROM settings WHERE setting_key='LOW_STOCK_TIME'")
    settings_store.reload()
    assert config.LOW_STOCK_TIME is None


def test_watcher_restarts_bot_only_for_restart_settings(tg):
    restarts = []
    watch = main.make_settings_watcher(restart=lambda: restarts.append(1))
    settings_store.save({"LABEL_TITLE": "Новый"})
    watch()
    assert restarts == []
    settings_store.save({"TIMEZONE": "Asia/Aqtau"})
    watch()
    assert restarts == [1]


def test_caddy_issues_certificates_only_for_our_domain(tg, monkeypatch):
    client = TestClient(app)
    assert client.get("/caddy/ask?domain=pos.test").status_code == 200       # WEB_URL из окружения
    assert client.get("/caddy/ask?domain=other.example").status_code == 404
    assert client.get("/caddy/ask?domain=1.2.3.4").status_code == 404
    monkeypatch.delenv("WEB_URL")
    settings_store.reload()
    assert client.get("/caddy/ask?domain=other.example").status_code == 404  # настроено, адреса нет
    db_query("DELETE FROM settings WHERE setting_key='SETUP_DONE'")
    settings_store.reload()
    assert client.get("/caddy/ask?domain=new-shop.kz").status_code == 200    # до настройки — любой домен
    monkeypatch.undo()
    settings_store.adopt_existing_install()


# ---------------------------------------------------------------- deploy/bootstrap-env.sh

def _bootstrap(env_file, path=None, **overrides):
    env = {"PATH": path or os.environ["PATH"], **{f"OVERRIDE_{k}": v for k, v in overrides.items()}}
    return subprocess.run(["bash", str(BOOTSTRAP), str(env_file)], env=env, capture_output=True, text=True)


def _read_env(env_file):
    return dict(line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line)


def test_bootstrap_creates_env_once(tmp_path):
    env_file = tmp_path / "pos-bot" / ".env"
    env_file.parent.mkdir()
    r = _bootstrap(env_file)
    assert r.returncode == 0, r.stderr
    values = _read_env(env_file)
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert len(values["MYSQL_PASSWORD"]) >= 32 and values["DB_PASS"] == values["MYSQL_PASSWORD"]
    assert values["MYSQL_ROOT_PASSWORD"] != values["MYSQL_PASSWORD"]
    assert values["DB_USER"] == values["MYSQL_USER"] == "bot" and values["DB_HOST"] == "db"
    assert "TOKEN" not in values
    from cryptography.fernet import Fernet
    Fernet(values["POSBOT_MASTER_KEY"].encode())                # корректный ключ Fernet
    before = env_file.read_text()
    assert _bootstrap(env_file).returncode == 0
    assert env_file.read_text() == before                       # повторный запуск ничего не меняет


def test_bootstrap_keeps_existing_and_applies_overrides(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MYSQL_PASSWORD=old-pass\nCUSTOM=1\nMYSQL_USER=\n")
    env_file.chmod(0o644)
    r = _bootstrap(env_file, TOKEN="1:a\\b&c", WEB_DOMAIN="pos.example.kz", MYSQL_ROOT_PASSWORD="root-from-secret")
    assert r.returncode == 0, r.stderr
    values = _read_env(env_file)
    assert values["MYSQL_PASSWORD"] == values["DB_PASS"] == "old-pass"     # не перегенерирован
    assert values["CUSTOM"] == "1" and values["MYSQL_USER"] == "bot"
    assert values["TOKEN"] == "1:a\\b&c" and values["WEB_URL"] == "https://pos.example.kz"
    assert values["MYSQL_ROOT_PASSWORD"] == "root-from-secret"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert env_file.read_text().count("MYSQL_USER=") == 1


def test_bootstrap_refuses_new_password_for_existing_database(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_docker = bindir / "docker"
    fake_docker.write_text('#!/bin/sh\n[ "$1 $2 $3" = "volume inspect pos-bot_db_data" ] && exit 0\nexit 1\n')
    fake_docker.chmod(0o755)
    env_file = tmp_path / "pos-bot" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("TOKEN=keep\n")
    r = _bootstrap(env_file, path=f"{bindir}:{os.environ['PATH']}")
    assert r.returncode == 1 and "уже существует" in r.stderr
    assert env_file.read_text() == "TOKEN=keep\n"
