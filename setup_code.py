"""Печатает код мастера первоначальной настройки (/setup).

    docker compose exec web python -m setup_code
    docker compose exec web python -m setup_code --web-url https://pos.example.kz   (адрес веб-админки)
"""
import sys

import config
import settings_store
from database import init_pool, get_connection
from schema import ensure_schema


def set_web_url(url):
    """Адрес веб-админки из командной строки (posbot web-url, posbot tunnel): тот же, что в «Настройках»."""
    try:
        value = config.parse_value(config.BY_KEY["WEB_URL"], url)
    except config.SettingError as e:
        print(e)
        return 1
    if config.env_overridden("WEB_URL"):
        print("Адрес задан переменной окружения WEB_URL/WEB_DOMAIN в .env — поменяйте его там.")
        return 1
    ok, err = settings_store.save({"WEB_URL": value})
    if not ok:
        print(f"Не сохранено: {err}")
        return 1
    print(f"Адрес веб-админки: {value or '(не задан)'}")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    init_pool(retries=5)
    connection = get_connection()
    try:
        ensure_schema(connection, config.DEFAULT_STORES)
    finally:
        connection.close()
    if argv[:1] == ["--web-url"]:
        return set_web_url(argv[1] if len(argv) > 1 else "")
    code = settings_store.ensure_setup_code()
    if not code:
        print("Первоначальная настройка уже выполнена. Настройки меняются в веб-админке: раздел «Настройки».")
        return 0
    where = f"{config.WEB_URL}/setup" if config.WEB_URL else "https://<ваш-домен>/setup или http://127.0.0.1:8000/setup"
    print(f"Код настройки: {code}\nОткройте {where}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
