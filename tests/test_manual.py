"""Инструкции для сотрудников: страницы /help и ссылка в /help бота."""
import pytest
from fastapi.testclient import TestClient

from conftest import CASHIER, MANAGER, login
from web import manual
from web.app import app


@pytest.fixture
def client(tg):
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def test_help_pages_open_without_login(tg, client):
    index = client.get("/help")
    assert index.status_code == 200
    for slug in manual.MANUALS:
        assert f'href="/help/{slug}"' in index.text
        page = client.get(f"/help/{slug}")
        assert page.status_code == 200, slug
        assert "Содержание" in page.text and "<table>" in page.text
    assert client.get("/help/hacker").status_code == 404


def test_manual_images_and_links_exist(tg, client):
    import re
    for slug in manual.MANUALS:
        html = manual.render(slug)[1]
        for src in re.findall(r'src="([^"]+)"', html):
            assert client.get(src).status_code == 200, src
        for href in re.findall(r'href="(/help/[^"#]*)', html):
            assert client.get(href).status_code == 200, href
    assert 'id="pc"' in manual.render("kassir")[1]  # на якорь ссылаются касса и инструкция менеджера


def test_bot_help_links_role_manual(tg):
    login(tg, CASHIER)
    assert "https://pos.test/help/kassir" in tg.texts(tg.send(CASHIER, "/help"))
    login(tg, MANAGER)
    assert "https://pos.test/help/manager" in tg.texts(tg.send(MANAGER, "/help"))
