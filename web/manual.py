"""Инструкции для сотрудников: Markdown из web/manual/ → страницы /help/<роль>.

Страницы открыты без входа: в них нет данных магазина, а ссылку удобно дать новому сотруднику заранее.
"""
from functools import lru_cache
from pathlib import Path

import markdown

DIR = Path(__file__).parent / "manual"
MANUALS = {  # адрес → (файл, заголовок, для кого)
    "kassir": ("kassir.md", "Кассир", "продажа, возврат, скупка, Trade-In, касса на компьютере"),
    "manager": ("manager.md", "Менеджер", "товары, остатки, ценники, импорт каталога, отчёты"),
    "admin": ("admin.md", "Администратор", "сотрудники, магазины, настройки, сервер и бэкапы"),
}
ROLE_SLUGS = {"cashier": "kassir", "manager": "manager", "admin": "admin"}


@lru_cache(maxsize=None)
def render(slug):
    """(заголовок страницы, HTML) или None."""
    if slug not in MANUALS:
        return None
    text = (DIR / MANUALS[slug][0]).read_text(encoding="utf-8")
    md = markdown.Markdown(extensions=["tables", "attr_list", "toc"],
                           extension_configs={"toc": {"title": "Содержание", "toc_depth": "2-3"}})
    html = md.convert(text)
    title = text.splitlines()[0].lstrip("# ").strip()
    return title, html
