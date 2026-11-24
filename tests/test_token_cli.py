"""Аудит Н-3: новый токен без входа в админку — `python -m setup_code --token` и `posbot token`."""
import os
import subprocess
from pathlib import Path

import pytest

import setup_code
from core import config, settings_store
from conftest import db_query

NEW = "7777777777:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
POSBOT = Path(__file__).resolve().parent.parent / "deploy" / "posbot.sh"


@pytest.fixture
def no_env_token(tg, monkeypatch):
    monkeypatch.delenv("TOKEN", raising=False)
    monkeypatch.setattr(settings_store, "check_telegram_token", lambda t: (True, "@new_bot") if t == NEW else (False, "нет"))
    yield
    monkeypatch.undo()
    settings_store.reload()


def test_set_token_saves_encrypted(no_env_token, capsys):
    assert setup_code.set_token("плохой") == 1 and "123456789:AA" in capsys.readouterr().out
    assert setup_code.set_token("1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw1") == 1     # Telegram не принял
    assert setup_code.set_token(NEW + "\n") == 0
    assert "@new_bot" in capsys.readouterr().out and config.TOKEN == NEW
    row = db_query("SELECT value, is_secret FROM settings WHERE setting_key='TOKEN'")[0]
    assert row['is_secret'] == 1 and NEW not in row['value']                                 # хранится зашифрованным


def test_set_token_when_env_overrides(tg, monkeypatch, capsys):
    monkeypatch.setenv("TOKEN", "123456:TEST")
    monkeypatch.setattr(settings_store, "check_telegram_token", lambda t: (True, "@new_bot"))
    assert setup_code.set_token(NEW) == setup_code.TOKEN_IN_ENV
    assert ".env" in capsys.readouterr().out


def _posbot(tmp_path, env_line, rc):
    """posbot token с поддельным docker: пишет аргументы и stdin в журнал, exec возвращает rc."""
    inst = tmp_path / "inst"
    inst.mkdir(parents=True)
    (inst / "docker-compose.yml").write_text("services: {}\n")
    (inst / ".env").write_text(f"MYSQL_PASSWORD=x\n{env_line}")
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    log = tmp_path / "docker.log"
    docker = bindir / "docker"
    docker.write_text(f'#!/bin/sh\necho "ARGS $*" >> {log}\ncase "$*" in *exec*) cat >> {log}; exit {rc};; esac\nexit 0\n')
    docker.chmod(0o755)
    env = {"PATH": f"{bindir}:{os.environ['PATH']}", "POSBOT_DIR": str(inst), "HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(POSBOT), "token"], input=NEW + "\n", env=env, capture_output=True, text=True)
    return r, log.read_text(), (inst / ".env").read_text()


def test_posbot_token_saves_via_web(tmp_path):
    r, log, env = _posbot(tmp_path, "", 0)
    assert r.returncode == 0, r.stderr
    assert "exec -T web python -m setup_code --token" in log and NEW in log          # токен — через stdin
    assert NEW not in "".join(l for l in log.splitlines() if l.startswith("ARGS"))    # не в аргументах
    assert "TOKEN=" not in env


def test_posbot_token_replaces_env_line(tmp_path):
    r, log, env = _posbot(tmp_path, "TOKEN=111:old\n", 3)
    assert r.returncode == 0, r.stderr
    assert f"TOKEN={NEW}" in env and "111:old" not in env and "ARGS compose up -d" in log
    r, _, env = _posbot(tmp_path / "b", "", 1)                                       # Telegram не принял
    assert r.returncode == 1 and "TOKEN=" not in env
