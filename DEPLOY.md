# Деплой POS-бота

Основной репозиторий — GitHub. Деплой — GitHub Actions (`.github/workflows/deploy.yml`):

- **check** (GitHub-hosted runner) — синтаксис Python, валидация compose, сборка образа. Запускается на каждый push и PR в `POS-Bot`.
- **deploy** (self-hosted runner на сервере) — только после push в `POS-Bot` или ручного запуска. Код раскладывается в `/opt/pos-bot`, `.env` собирается из секретов, затем `docker compose up -d --build`.

Self-hosted runner сам подключается к GitHub исходящим HTTPS, входящие порты на сервер открывать не нужно.

**Сервер:** пошаговая инструкция для VPS is\*hosting в Казахстане (подготовка сервера, runner, бэкапы, перенос данных) — [`docs/DEPLOY_CLOUD.md`](docs/DEPLOY_CLOUD.md).

## 1. Секреты и переменные в GitHub

Repo → Settings → Environments → создать `production` и задать в нём:

**Secrets** (обязательные):

| Имя | Что это |
|---|---|
| `TOKEN` | Токен бота от @BotFather |
| `MYSQL_USER` | Пользователь БД (сейчас `bot`) |
| `MYSQL_PASSWORD` | Пароль пользователя БД |
| `MYSQL_DATABASE` | Имя БД (сейчас `bot`) |
| `MYSQL_ROOT_PASSWORD` | Пароль root MySQL |

**Variables**:

| Имя | По умолчанию | Что это |
|---|---|---|
| `ADMIN_PHONE` | — | Телефон первого администратора. На пустой БД без него никто не войдёт. |
| `TZ` | `Asia/Almaty` | Часовой пояс: даты операций, периоды отчётов, расписание |
| `REPORT_CHAT_ID` | — | Куда слать ежедневный отчёт. Если пусто — всем администраторам |
| `DAILY_REPORT_TIME` | `23:00` | Время ежедневного отчёта |
| `LOW_STOCK_TIME` | `09:00` | Время уведомления о низком остатке (пусто — выключено) |
| `LOW_STOCK_THRESHOLD` | `5` | Порог низкого остатка, шт. |

Схему БД создаёт и обновляет сам бот при старте (`schema.py`): на новой базе создаются все таблицы, на существующей добавляются недостающие колонки, данные не трогаются.

Рекомендуется в настройках environment `production` ограничить деплой веткой `POS-Bot` (Deployment branches).

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

## 3. Первый деплой

Actions → **Check & Deploy** → Run workflow (ветка `POS-Bot`), либо просто push в `POS-Bot`.

## Тесты

Сценарные тесты гоняют все процессы бота через настоящую БД и имитацию Telegram:

```bash
pip install -r requirements.txt -r requirements-dev.txt
TEST_DB_HOST=127.0.0.1 TEST_DB_USER=root TEST_DB_PASS=... TEST_DB_NAME=bot_test pytest -q tests
```

В CI они запускаются на MySQL 8.4 и latest перед каждым деплоем.

## Локальный запуск

```bash
cp .env.example .env   # заполнить значения
docker compose up -d --build
```
