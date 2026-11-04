#!/usr/bin/env bash
# Бэкап БД POS-бота: mysqldump из контейнера → gzip в /opt/pos-bot-backups,
# опционально копия в Object Storage по pre-authenticated request URL.
# Настройки: /etc/pos-bot-backup.env (KEEP_DAYS, BACKUP_PAR_URL).
# Ручной запуск: sudo /opt/pos-bot/deploy/backup.sh
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/pos-bot}
BACKUP_DIR=${BACKUP_DIR:-/opt/pos-bot-backups}
KEEP_DAYS=14
BACKUP_PAR_URL=""
CONFIG=${BACKUP_CONFIG:-/etc/pos-bot-backup.env}
# shellcheck disable=SC1090
if [ -f "$CONFIG" ]; then . "$CONFIG"; fi

env_value() {
  # Значение из .env без source: пароли могут содержать $ и кавычки
  grep -m1 "^$1=" "$APP_DIR/.env" | cut -d= -f2-
}

cd "$APP_DIR"
DB_NAME=$(env_value MYSQL_DATABASE)
ROOT_PASS=$(env_value MYSQL_ROOT_PASSWORD)
ts=$(date +%Y%m%d_%H%M)
file="$BACKUP_DIR/${DB_NAME}_${ts}.sql.gz"

mkdir -p "$BACKUP_DIR"
umask 077
trap 'rm -f "$file.tmp"' EXIT
docker compose exec -T -e MYSQL_PWD="$ROOT_PASS" db \
  mysqldump -uroot --single-transaction --routines --triggers --no-tablespaces "$DB_NAME" \
  | gzip > "$file.tmp"
# Пустой или битый дамп не должен вытеснить хорошие копии
gzip -t "$file.tmp"
[ "$(gzip -dc "$file.tmp" | head -c 100 | wc -c)" -gt 0 ]
mv "$file.tmp" "$file"
echo "$(date -Is) бэкап: $file ($(du -h "$file" | cut -f1))"

if [ -n "$BACKUP_PAR_URL" ]; then
  curl -fsS -X PUT --data-binary @"$file" "${BACKUP_PAR_URL}$(basename "$file")"
  echo "$(date -Is) копия загружена в Object Storage"
fi

find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime +"$KEEP_DAYS" -delete
