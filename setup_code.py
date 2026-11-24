"""Печатает код мастера первоначальной настройки (/setup).

    docker compose exec web python -m setup_code
    docker compose exec web python -m setup_code --web-url https://pos.example.kz   (адрес веб-админки)
    docker compose exec -T web python -m setup_code --token < файл_или_ввод       (новый токен бота, posbot token)
"""
import sys

from core import config
from core import settings_store
from core.database import init_pool, get_connection
from core.schema import ensure_schema


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


TOKEN_IN_ENV = 3   # код выхода: токен проверен, но задан в .env — менять его там (так делает posbot token)


def set_token(raw):
    """Новый токен бота без входа в админку — например, после /revoke, когда бот уже не работает и ссылку входа
    получить негде. Токен читается со стандартного ввода (не из аргументов: не останется в истории и в ps),
    проверяется у Telegram и сохраняется зашифрованным, как в «Настройках». Бот перезапустится сам."""
    token = (raw or "").strip()
    try:
        token = config.parse_value(config.BY_KEY["TOKEN"], token)
    except config.SettingError as e:
        print(e)
        return 1
    if not token:
        print("Токен пустой. Скопируйте его из @BotFather: ваш бот → API Token.")
        return 1
    ok, result = settings_store.check_telegram_token(token)
    if not ok:
        print(result)
        return 1
    if config.env_overridden("TOKEN"):
        print(f"Токен подходит ({result}), но задан строкой TOKEN в .env — замените его там и перезапустите: "
              "docker compose up -d (posbot token делает это сам).")
        return TOKEN_IN_ENV
    ok, err = settings_store.save({"TOKEN": token})
    if not ok:
        print(f"Не сохранено: {err}")
        return 1
    print(f"Токен сохранён: {result}. Бот перезапустится с ним в течение минуты.")
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
    if argv[:1] == ["--token"]:
        return set_token(sys.stdin.readline())
    code = settings_store.ensure_setup_code()
    if not code:
        print("Первоначальная настройка уже выполнена. Настройки меняются в веб-админке: раздел «Настройки».")
        return 0
    where = f"{config.WEB_URL}/setup" if config.WEB_URL else "https://<ваш-домен>/setup или http://127.0.0.1:8000/setup"
    print(f"Код настройки: {code}\nОткройте {where}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
