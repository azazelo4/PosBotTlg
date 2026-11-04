# Деплой на VPS (is*hosting, Казахстан)

Бот и MySQL работают в Docker Compose на Ubuntu-VPS, деплой — GitHub Actions через self-hosted
runner на этом же сервере. Общая часть про секреты GitHub — в [`DEPLOY.md`](../DEPLOY.md).

Выбранный сервер: **is*hosting VPS Kazakhstan, тариф Start** — 2 vCPU, 2 GB RAM, 30 GB SSD.
Сервер в Казахстане выбран и из-за требования закона РК о персональных данных хранить такие базы
на территории РК (в БД — имена и телефоны сотрудников).

```
GitHub (push в POS-Bot)
   │  job check + test — на раннерах GitHub
   │  job deploy ──────────────┐
   ▼                           │ runner сам забирает задачу исходящим HTTPS
VPS (Ubuntu 24.04)             │
   ├─ GitHub runner  ◄─────────┘
   └─ docker compose: bot ──► api.telegram.org (long polling)
                      db (MySQL, volume pos-bot_db_data)
                      adminer (только 127.0.0.1:8080)
```

Входящих портов, кроме SSH, не нужно: бот сам опрашивает Telegram, runner сам ходит в GitHub.

---

## 1. Заказ сервера

В панели is*hosting: **VPS → Kazakhstan → Start**.

| Параметр | Значение |
|---|---|
| ОС | Ubuntu 24.04 |
| Панель управления | не нужна |
| SSH-ключ | добавить свой публичный ключ, если панель позволяет (иначе — шаг 2) |
| Оплата | помесячно; при оплате за год обычно дешевле |

После активации в панели будут IP-адрес и пароль root.

## 2. Первый вход и SSH-ключ

С ноутбука (если ключа ещё нет — `ssh-keygen -t ed25519`):

```bash
ssh-copy-id root@<IP>     # ввести пароль root из панели
ssh root@<IP>             # теперь без пароля
```

Ключ нужен до следующего шага: скрипт подготовки отключит вход по паролю, только если увидит ключ.

## 3. Подготовка сервера

С ноутбука, из каталога репозитория:

```bash
scp deploy/setup-server.sh root@<IP>:~
ssh root@<IP> 'bash setup-server.sh'
```

[`deploy/setup-server.sh`](../deploy/setup-server.sh) (повторный запуск безопасен):

- часовой пояс `Asia/Almaty`, обновление системы и автообновления безопасности;
- swap 2 GB — запас для сборки образа и пиков MySQL;
- Docker и ротация логов контейнеров;
- пользователь `gh-runner` для GitHub runner, каталог `/opt/pos-bot`;
- ежедневный бэкап БД в 03:30;
- firewall `ufw`: входящие только SSH; `fail2ban`;
- вход по SSH только по ключу.

Проверка:

```bash
ssh root@<IP>
docker run --rm hello-world
free -h          # Swap: 2.0Gi
ufw status       # 22/tcp (OpenSSH) ALLOW
```

> Если провайдер поддерживает cloud-init при создании сервера, вместо шагов 2–3 можно вставить
> [`deploy/cloud-init.yaml`](../deploy/cloud-init.yaml) — он делает то же, кроме swap, ufw и fail2ban.

## 4. GitHub: секреты, переменные, runner

**Секреты и переменные** — как в [`DEPLOY.md`, раздел 1](../DEPLOY.md#1-секреты-и-переменные-в-github):
environment `production`, секреты `TOKEN`, `MYSQL_*`, переменная **`ADMIN_PHONE`** (без неё на новой
БД никто не войдёт). В **Deployment branches** ограничить environment веткой `POS-Bot`.

Если токен бота ещё не перевыпущен после утечки в истории git — сделать это сейчас: @BotFather → `/revoke`.

**Runner.** GitHub: **Settings → Actions → Runners → New self-hosted runner → Linux → x64**. Страница
покажет команды с актуальной версией и одноразовым токеном (действует час). Выполнить на сервере:

```bash
sudo -iu gh-runner
cd ~/actions-runner
# curl ... и tar ... — скопировать со страницы GitHub
./config.sh --url https://github.com/azazelo4/PosBotTlg --token <ТОКЕН_СО_СТРАНИЦЫ> \
            --name ishosting-pos-bot --labels pos-bot --unattended
exit

cd /home/gh-runner/actions-runner
./svc.sh install gh-runner      # от root: runner как сервис, переживает перезагрузку
./svc.sh start
```

В GitHub runner должен появиться со статусом **Idle** и метками `self-hosted, Linux, X64, pos-bot`.

## 5. Первый деплой

1. **Actions → Check & Deploy → Run workflow** (ветка `POS-Bot`) или любой push в `POS-Bot`.
   Если деплой уже стоял в очереди «Waiting for a runner», он запустится сам.
2. Job `deploy` разложит код в `/opt/pos-bot`, соберёт `.env` из секретов и поднимет контейнеры.
3. Проверка на сервере:

   ```bash
   cd /opt/pos-bot
   docker compose ps                  # bot, db (healthy), adminer — Up
   docker compose logs --tail=50 bot  # «Бот запущен, начинаю опрос Telegram.»
   ```

4. В Telegram с номера из `ADMIN_PHONE`: `/start` → «Поделиться номером» → меню администратора.
5. Добавить сотрудников: **Персонал → Добавить пользователя**.

## 6. Бэкапы

Ежедневно в 03:30 [`deploy/backup.sh`](../deploy/backup.sh) делает `mysqldump` в `/opt/pos-bot-backups`
и хранит 14 дней. Проверить вручную:

```bash
/opt/pos-bot/deploy/backup.sh
ls -lh /opt/pos-bot-backups
```

**Копия вне сервера.** Бэкап на том же диске пропадёт вместе с сервером, поэтому стоит регулярно
забирать копии к себе:

```bash
# с ноутбука или другого сервера, например раз в неделю по cron
rsync -av root@<IP>:/opt/pos-bot-backups/ ~/pos-bot-backups/
```

Либо автоматически в S3-совместимое хранилище: в `/etc/pos-bot-backup.env` задать `BACKUP_PAR_URL` —
URL, по которому разрешена PUT-загрузка (имя файла дописывается в конец).

**Восстановление:**

```bash
cd /opt/pos-bot
docker compose stop bot
gunzip -c /opt/pos-bot-backups/bot_YYYYMMDD_HHMM.sql.gz | \
  docker compose exec -T -e MYSQL_PWD="$(grep -m1 '^MYSQL_ROOT_PASSWORD=' .env | cut -d= -f2-)" db mysql -uroot bot
docker compose start bot
```

## 7. Перенос данных со старого сервера

Когда старый сервер снова будет доступен:

```bash
# на старом сервере
cd /opt/pos-bot
docker compose stop bot          # важно: два бота с одним токеном конфликтуют (ошибка 409)
docker compose exec -T -e MYSQL_PWD=<root-пароль> db \
  mysqldump -uroot --single-transaction --routines --triggers --no-tablespaces bot | gzip > old_bot.sql.gz
scp old_bot.sql.gz root@<IP>:/tmp/
```

На новом сервере — шаги «Восстановление» с файлом `/tmp/old_bot.sql.gz`. При старте бот сам добавит
недостающие таблицы и колонки. Затем на старом сервере `docker compose down` и отключить автозапуск,
чтобы он не поднялся с тем же токеном.

## 8. Обслуживание

| Задача | Команда (на сервере, в `/opt/pos-bot`) |
|---|---|
| Логи бота | `docker compose logs -f --tail=100 bot` |
| Перезапуск | `docker compose restart bot` |
| Adminer | с ноутбука: `ssh -L 8080:127.0.0.1:8080 root@<IP>`, затем <http://localhost:8080> (сервер `db`) |
| Память и диск | `free -h`, `df -h /`, `docker system df` |
| Статус runner | `/home/gh-runner/actions-runner/svc.sh status` |
| Очистка старых образов | `docker image prune -f` (деплой делает это сам) |

Обновления безопасности ставятся автоматически. Раз в пару месяцев: `apt full-upgrade && reboot` —
контейнеры (`restart: always`) и runner (сервис) поднимутся сами.

## 9. Если что-то не так

| Симптом | Причина и решение |
|---|---|
| Job `deploy` висит «Waiting for a runner» | Runner offline или без метки `pos-bot`: `svc.sh status`, метки в Settings → Actions → Runners. |
| Jobs `check`/`test` висят в очереди | Раннеры GitHub для приватного репо расходуют бесплатные минуты (2000 в месяц на Free). Проверить Settings → Billing. |
| Бот падает с «Не задана обязательная переменная» | Не заведён секрет в environment `production`. |
| В логах `409 Conflict: terminated by other getUpdates request` | Тот же токен опрашивает другой экземпляр бота (старый сервер, локальный запуск). Остановить лишний. |
| Сборка образа падает или сервер «задыхается» | Мало памяти: `free -h`, проверить, что swap включён (`swapon --show`). |
| Нет доступа по SSH после настройки | Войти через консоль (VNC) в панели is*hosting, проверить `/etc/ssh/sshd_config.d/10-pos-bot.conf` и ключ в `~/.ssh/authorized_keys`. |
