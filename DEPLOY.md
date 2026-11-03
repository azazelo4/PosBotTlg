# Деплой POS-бота

Основной репозиторий — GitHub. Деплой — GitHub Actions (`.github/workflows/deploy.yml`):

- **check** (GitHub-hosted runner) — синтаксис Python, валидация compose, сборка образа. Запускается на каждый push и PR в `POS-Bot`.
- **deploy** (self-hosted runner на сервере) — только после push в `POS-Bot` или ручного запуска. Код раскладывается в `/opt/pos-bot`, `.env` собирается из секретов, затем `docker compose up -d --build`.

Self-hosted runner сам подключается к GitHub исходящим HTTPS, входящие порты на сервер открывать не нужно.

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

**Variables** (необязательные): `REPORT_CHAT_ID`, `DAILY_REPORT_TIME` (по умолчанию `23:00`), `LOW_STOCK_THRESHOLD` (по умолчанию `5`).

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

## Локальный запуск

```bash
cp .env.example .env   # заполнить значения
docker compose up -d --build
```
