"""Печатает код мастера первоначальной настройки (/setup).

    docker compose exec web python -m setup_code
"""
import sys

import config
import settings_store
from database import init_pool, get_connection
from schema import ensure_schema


def main():
    init_pool(retries=5)
    connection = get_connection()
    try:
        ensure_schema(connection, config.DEFAULT_STORES)
    finally:
        connection.close()
    code = settings_store.ensure_setup_code()
    if not code:
        print("Первоначальная настройка уже выполнена. Настройки меняются в веб-админке: раздел «Настройки».")
        return 0
    where = f"{config.WEB_URL}/setup" if config.WEB_URL else "https://<ваш-домен>/setup или http://127.0.0.1:8000/setup"
    print(f"Код настройки: {code}\nОткройте {where}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
