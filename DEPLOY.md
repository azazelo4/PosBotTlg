# Деплой POS-бота

Основной репозиторий — GitHub. Деплой — GitHub Actions (`.github/workflows/deploy.yml`):

- **check** (GitHub-hosted runner) — синтаксис Python, валидация compose, сборка образа. Запускается на каждый push и PR в `POS-Bot`.
- **deploy** (self-hosted runner на сервере) — только после push в `POS-Bot` или ручного запуска. Код раскладывается в `/opt/pos-bot`, `.env` дополняется `deploy/bootstrap-env.sh`, затем `docker compose up -d --build`.

Self-hosted runner сам подключается к GitHub исходящим HTTPS, входящие порты на сервер открывать не нужно.

**Сервер:** пошаговая инструкция для VPS is\*hosting в Казахстане (подготовка сервера, runner, бэкапы, перенос данных) — [`docs/DEPLOY_CLOUD.md`](docs/DEPLOY_CLOUD.md).

## 1. Настройки GitHub — не нужны

Секреты и переменные репозитория **не обязательны**: деплой создаёт `.env` сам (`deploy/bootstrap-env.sh`):
пароли БД и ключ шифрования генерируются один раз и дальше не меняются. Токен бота, магазины,
администратор и остальное задаются в мастере `/setup` после первого деплоя.

Рекомендуется создать environment `production` (Settings → Environments) и ограничить деплой веткой `POS-Bot`.

> **Старые установки.** Если секреты `TOKEN`, `MYSQL_*`, `WEB_SECRET` или переменные (`ADMIN_PHONE`, `WEB_DOMAIN`,
> `TZ`, `LABEL_*`…) уже заведены — они по-прежнему переносятся в `.env` и работают как раньше
> (в веб-админке такие настройки только для чтения). Чтобы управлять ими из админки: задайте значение
> на странице «Настройки», удалите секрет/переменную в GitHub и строку из `/opt/pos-bot/.env`.
> Пароли `MYSQL_*` из секретов не удаляйте, пока в `.env` нет тех же значений.

Схему БД создаёт и обновляет само приложение при старте (`schema.py`): на новой базе создаются все таблицы, на существующей добавляются недостающие колонки, данные не трогаются.

## 2. Runner на сервере

На VM с Docker (Ubuntu/Debian):

```bash
# отдельный пользователь для runner с доступом к docker
sudo useradd -m -s /bin/bash gh-runner
sudo usermod -aG docker gh-runner
sudo mkdir -p /opt/pos-bot && sudo chown gh-runner:gh-runner /opt/pos-bot
```

Repo → Settings → Actions → Runners → **New self-hosted runner** → Linux x64. Выполнить показанные команды от имени `gh-runner` (`sudo -iu gh-runner`), при `./config.sh` указать дополнительную метку:

```bash
./config.sh --url https://github.com/azazelo4/PosBotTlg --token <TOKEN_ИЗ_UI> --labels pos-bot --unattended
```

Установить как сервис (из каталога runner, под sudo-пользователем):

```bash
sudo ./svc.sh install gh-runner
sudo ./svc.sh start
```

Runner должен появиться в списке со статусом **Idle**.

> Если на сервере уже есть `/opt/pos-bot` от старого деплоя — его оставить: compose-проект и volume `pos-bot_db_data` с данными сохранятся. Нужно только `sudo chown -R gh-runner:gh-runner /opt/pos-bot`.

## 3. Первый деплой и мастер настройки

Actions → **Check & Deploy** → Run workflow (ветка `POS-Bot`), либо просто push в `POS-Bot`.

Деплой на чистом сервере зелёный: бот и веб запускаются и ждут настройки (в Summary запуска — подсказка).

1. Домен: A-запись `pos.ваш-домен.kz` → IP сервера. Сертификат HTTPS выпустится сам при первом заходе.
   Без домена — SSH-туннель: `ssh -L 8000:127.0.0.1:8000 user@сервер` и адрес <http://127.0.0.1:8000>.
2. Код настройки на сервере: `cd /opt/pos-bot && docker compose exec web python -m setup_code`.
3. Открыть `https://pos.ваш-домен.kz/setup`, ввести код, токен от @BotFather, магазины, свой телефон.
4. Через 10–20 секунд бот запущен: в Telegram `/start` → «Поделиться номером».

## Тесты

Сценарные тесты гоняют все процессы бота через настоящую БД и имитацию Telegram:

```bash
pip install -r requirements.txt -r requirements-dev.txt
TEST_DB_HOST=127.0.0.1 TEST_DB_USER=root TEST_DB_PASS=... TEST_DB_NAME=bot_test pytest -q tests
```

В CI они запускаются на MySQL 8.4 и latest перед каждым деплоем.

## Локальный запуск

```bash
deploy/bootstrap-env.sh .env
docker compose up -d --build
docker compose exec web python -m setup_code   # → http://127.0.0.1:8000/setup
```
