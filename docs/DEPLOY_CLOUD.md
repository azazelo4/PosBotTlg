# Деплой в облако: Oracle Cloud Always Free

Бесплатная навсегда VM в Oracle Cloud, бот и MySQL в Docker Compose, деплой — GitHub Actions
через self-hosted runner на этой же VM. Общая часть про секреты и runner — в [`DEPLOY.md`](../DEPLOY.md),
здесь — всё, что относится к облаку.

```
GitHub (push в POS-Bot)
   │  job check + test — на раннерах GitHub
   │  job deploy ──────────────┐
   ▼                           │ runner сам забирает задачу исходящим HTTPS
Oracle Cloud VM (ARM, Ubuntu)  │
   ├─ GitHub runner  ◄─────────┘
   └─ docker compose: bot ──► api.telegram.org (long polling)
                      db (MySQL, volume pos-bot_db_data)
                      adminer (только 127.0.0.1:8080)
```

Входящих портов, кроме SSH, не нужно: бот опрашивает Telegram сам, runner сам ходит в GitHub.

---

## 1. Аккаунт Oracle Cloud

1. Регистрация на <https://www.oracle.com/cloud/free/>. Нужна банковская карта для проверки — блокируется небольшая сумма и возвращается.
2. **Home region выбирается один раз и не меняется.** Always Free ресурсы создаются только в нём. Для бота задержка не важна, важнее свободные мощности: популярные регионы (Frankfurt, Amsterdam) чаще отвечают «Out of capacity».
3. Рекомендуется перевести аккаунт на **Pay As You Go** (Billing → Upgrade). В пределах Always Free это по-прежнему бесплатно, но:
   - простаивающие Always Free инстансы на бесплатном аккаунте Oracle может забрать — у бота нагрузка почти нулевая, это как раз наш случай;
   - ARM-мощности выдаются охотнее.
4. Сразу после апгрейда — **Billing → Budgets → Create budget** на 1 $ с уведомлением на почту, чтобы случайно созданный платный ресурс не остался незамеченным.

## 2. Сеть

**Networking → Virtual Cloud Networks → Start VCN Wizard → Create VCN with Internet Connectivity**, имя `pos-bot-vcn`, остальное по умолчанию.

Затем в созданной VCN: **Security Lists → Default Security List → Ingress Rules**:

- оставить только правило TCP **22**;
- если у тебя статический IP — заменить источник `0.0.0.0/0` на `<твой IP>/32`;
- ничего больше не открывать (MySQL и Adminer наружу не публикуются).

## 3. Виртуальная машина

**Compute → Instances → Create instance**:

| Параметр | Значение |
|---|---|
| Name | `pos-bot` |
| Image | Canonical Ubuntu 24.04 (aarch64) |
| Shape | `VM.Standard.A1.Flex` — **2 OCPU, 12 GB** (предел Always Free для ARM с июня 2026) |
| Networking | `pos-bot-vcn`, public subnet, **Assign a public IPv4 address** |
| SSH keys | загрузить свой публичный ключ (`~/.ssh/id_ed25519.pub`) |
| Boot volume | 50 GB (бесплатно до 200 GB суммарно) |
| Advanced → Management → cloud-init | вставить содержимое [`deploy/cloud-init.yaml`](../deploy/cloud-init.yaml) |

Если «Out of capacity for shape» — попробовать другой Availability Domain, повторить через несколько часов или после перехода на Pay As You Go.

Проверка после создания (2–5 минут):

```bash
ssh ubuntu@<PUBLIC_IP>
cloud-init status --wait          # должно быть: status: done
docker run --rm hello-world       # через sudo, если от ubuntu
timedatectl | grep "Time zone"    # Asia/Almaty
```

## 4. GitHub: секреты и переменные

Как в [`DEPLOY.md`, раздел 1](../DEPLOY.md#1-секреты-и-переменные-в-github): environment `production`,
секреты `TOKEN`, `MYSQL_*`, переменная **`ADMIN_PHONE`** (без неё на новой БД никто не войдёт).
В **Deployment branches** environment ограничить веткой `POS-Bot`.

Если токен бота ещё не перевыпущен после утечки в истории git — сделать это сейчас: @BotFather → `/revoke`.

## 5. Runner на VM

GitHub: **Settings → Actions → Runners → New self-hosted runner → Linux → ARM64**. Страница покажет
команды с актуальной версией и одноразовым токеном (действует час). Выполнить их от пользователя `gh-runner`:

```bash
sudo -iu gh-runner
cd ~/actions-runner
# curl ... и tar ... — скопировать со страницы GitHub
./config.sh --url https://github.com/azazelo4/PosBotTlg --token <ТОКЕН_СО_СТРАНИЦЫ> \
            --name oracle-pos-bot --labels pos-bot --unattended
exit
```

Сервис, чтобы runner переживал перезагрузку:

```bash
cd /home/gh-runner/actions-runner
sudo ./svc.sh install gh-runner
sudo ./svc.sh start
```

В GitHub runner должен появиться со статусом **Idle** и метками `self-hosted, Linux, ARM64, pos-bot`.

## 6. Первый деплой

1. Смержить PR в `POS-Bot` (или **Actions → Check & Deploy → Run workflow**).
2. Job `deploy` разложит код в `/opt/pos-bot`, соберёт `.env` из секретов и поднимет контейнеры.
3. Проверка на VM:

   ```bash
   cd /opt/pos-bot
   sudo docker compose ps                  # bot, db (healthy), adminer — Up
   sudo docker compose logs --tail=50 bot  # «Бот запущен, начинаю опрос Telegram.»
   ```

4. В Telegram с номера из `ADMIN_PHONE`: `/start` → «Поделиться номером» → меню администратора.
5. Добавить сотрудников: **Персонал → Добавить пользователя**.

## 7. Бэкапы

cloud-init уже настроил ежедневный бэкап в 03:30: [`deploy/backup.sh`](../deploy/backup.sh) делает
`mysqldump` в `/opt/pos-bot-backups` и хранит 14 дней. Проверить вручную:

```bash
sudo /opt/pos-bot/deploy/backup.sh
ls -lh /opt/pos-bot-backups
```

**Копия вне сервера** (рекомендуется: без неё бэкап пропадёт вместе с VM). Object Storage — 20 GB бесплатно:

1. **Storage → Buckets → Create bucket** `pos-bot-backups`, Standard, приватный.
2. В бакете: **Lifecycle Policy Rules → Create** — удалять объекты старше 30 дней.
3. **Pre-Authenticated Requests → Create**: target *Bucket*, access *Permit object writes*, срок — год. Скопировать URL (заканчивается на `/o/`).
4. На VM: `sudo nano /etc/pos-bot-backup.env` → `BACKUP_PAR_URL=<URL>`.
5. `sudo /opt/pos-bot/deploy/backup.sh` — в конце «копия загружена в Object Storage». Поставить в календарь продление PAR через год.

**Восстановление** из бэкапа:

```bash
cd /opt/pos-bot
sudo docker compose stop bot
gunzip -c /opt/pos-bot-backups/bot_YYYYMMDD_HHMM.sql.gz | \
  sudo docker compose exec -T -e MYSQL_PWD="$(sudo grep -m1 '^MYSQL_ROOT_PASSWORD=' .env | cut -d= -f2-)" db mysql -uroot bot
sudo docker compose start bot
```

## 8. Перенос данных со старого сервера

Когда старый сервер снова будет доступен:

```bash
# на старом сервере
cd /opt/pos-bot
sudo docker compose stop bot          # важно: два бота с одним токеном конфликтуют (ошибка 409)
sudo docker compose exec -T -e MYSQL_PWD=<root-пароль> db \
  mysqldump -uroot --single-transaction --routines --triggers --no-tablespaces bot | gzip > old_bot.sql.gz
scp old_bot.sql.gz ubuntu@<PUBLIC_IP>:/tmp/
```

На новой VM — шаги «Восстановление» с файлом `/tmp/old_bot.sql.gz`. При старте бот сам добавит
недостающие таблицы и колонки. Затем на старом сервере `sudo docker compose down` и отключить
автозапуск, чтобы он не поднялся с тем же токеном.

## 9. Обслуживание

| Задача | Команда (на VM, в `/opt/pos-bot`) |
|---|---|
| Логи бота | `sudo docker compose logs -f --tail=100 bot` |
| Перезапуск | `sudo docker compose restart bot` |
| Adminer | с ноутбука: `ssh -L 8080:127.0.0.1:8080 ubuntu@<PUBLIC_IP>`, затем <http://localhost:8080> (сервер `db`) |
| Место на диске | `df -h /` и `sudo docker system df` |
| Статус runner | `sudo /home/gh-runner/actions-runner/svc.sh status` |

Обновления безопасности ОС ставятся автоматически (`unattended-upgrades`). Раз в пару месяцев стоит
зайти и выполнить `sudo apt full-upgrade && sudo reboot`.

## 10. Если что-то не так

| Симптом | Причина и решение |
|---|---|
| Job `deploy` висит «Waiting for a runner» | Runner offline или без метки `pos-bot`. Проверить статус сервиса и метки в Settings → Actions → Runners. |
| Jobs `check`/`test` висят в очереди | Раннеры GitHub для приватного репо расходуют бесплатные минуты (2000 в месяц на Free). Проверить Settings → Billing. |
| Бот падает с «Не задана обязательная переменная» | Не заведён секрет в environment `production`. |
| В логах `409 Conflict: terminated by other getUpdates request` | Тот же токен опрашивает другой экземпляр бота (старый сервер, локальный запуск). Остановить лишний. |
| VM пропала из консоли | Free-аккаунт и простой — Oracle забрал инстанс. Перейти на Pay As You Go, поднять VM заново по шагам 3–6, восстановить БД из Object Storage. |
