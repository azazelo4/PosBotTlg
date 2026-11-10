#!/usr/bin/env bash
# Бэкап БД POS-бота: mysqldump из контейнера → gzip в /opt/pos-bot-backups и копия вне сервера/ПК.
# Фото товаров (том app_data) — зеркало в $BACKUP_DIR/photos: фото не меняются, копируются только новые,
# удалённые в админке остаются в копии (их может знать старый бэкап БД).
# Настройки: /etc/pos-bot-backup.env (KEEP_DAYS, BACKUP_OFFSITE, BACKUP_SSH_KEY; BACKUP_PAR_URL — старый вариант).
#
#   backup.sh                   бэкап сейчас (+ копия вне ПК, если настроена)
#   backup.sh --if-stale 20     бэкап, только если последний старше 20 часов (ПК в магазине:
#                               cron каждый час, поэтому выключенный ночью ПК не остаётся без копии)
#   backup.sh --offsite-only    только отправить последний бэкап вне ПК (проверка настройки)
#
# Куда копировать (BACKUP_OFFSITE):
#   ssh://user@host[:порт]/путь   по SSH (ключ BACKUP_SSH_KEY, по умолчанию /root/.ssh/posbot_backup)
#   rclone:remote:путь            через rclone (Google Drive, Яндекс Диск, S3…; remote — из `rclone config`)
#   https://…/                    PUT-загрузка: URL + имя файла (pre-authenticated request в Object Storage)
# Отметка успешной копии — $BACKUP_DIR/.offsite-ok (время), ошибки — $BACKUP_DIR/.offsite-error.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/pos-bot}
BACKUP_DIR=${BACKUP_DIR:-/opt/pos-bot-backups}
KEEP_DAYS=14
BACKUP_PAR_URL=""
BACKUP_OFFSITE=""
BACKUP_SSH_KEY=/root/.ssh/posbot_backup
CONFIG=${BACKUP_CONFIG:-/etc/pos-bot-backup.env}
# shellcheck disable=SC1090
if [ -f "$CONFIG" ]; then . "$CONFIG"; fi
OFFSITE="${BACKUP_OFFSITE:-$BACKUP_PAR_URL}"

IF_STALE=""
OFFSITE_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --if-stale) IF_STALE="${2:-}"; shift 2 ;;
    --offsite-only) OFFSITE_ONLY=1; shift ;;
    *) echo "неизвестный параметр $1" >&2; exit 2 ;;
  esac
done

log() { echo "$(date -Is) $*"; }

env_value() {
  # Значение из .env без source: пароли могут содержать $ и кавычки
  grep -m1 "^$1=" "$APP_DIR/.env" | cut -d= -f2-
}

latest_backup() {
  find "$BACKUP_DIR" -maxdepth 1 -name '*.sql.gz' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-
}

mtime() { stat -c %Y "$1" 2>/dev/null || echo 0; }

offsite_send() {
  # offsite_send ФАЙЛ — отправить файл по адресу BACKUP_OFFSITE (вывод ошибок — в stdout/stderr)
  local file="$1" name rest hostpart path port
  name="$(basename "$file")"
  case "$OFFSITE" in
    ssh://*)
      rest="${OFFSITE#ssh://}"; hostpart="${rest%%/*}"; path="/${rest#*/}"; port=22
      [ "$rest" != "$hostpart" ] || path="posbot-backups"
      if [[ "$hostpart" == *:* ]]; then port="${hostpart##*:}"; hostpart="${hostpart%:*}"; fi
      local opts=(-i "$BACKUP_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)
      ssh "${opts[@]}" -p "$port" "$hostpart" "mkdir -p '$path'" || return 1
      scp -q "${opts[@]}" -P "$port" "$file" "$hostpart:$path/$name.tmp" || return 1
      ssh "${opts[@]}" -p "$port" "$hostpart" \
        "mv '$path/$name.tmp' '$path/$name' && find '$path' -maxdepth 1 -name '*.sql.gz' -mtime +$KEEP_DAYS -delete" || return 1
      ;;
    rclone:*)
      command -v rclone >/dev/null || { echo "rclone не установлен: curl https://rclone.org/install.sh | sudo bash"; return 1; }
      rclone copyto "$file" "${OFFSITE#rclone:}/$name" || return 1
      ;;
    https://*|http://*)
      curl -fsS --max-time 600 -X PUT --data-binary @"$file" "${OFFSITE}${name}" || return 1
      ;;
    *)
      echo "непонятный адрес BACKUP_OFFSITE=$OFFSITE (ssh://…, rclone:…, https://…)"; return 1 ;;
  esac
}

offsite_copy() {
  # offsite_copy ФАЙЛ → 0, если скопировано или копия вне ПК не настроена
  local file="$1" out
  [ -n "$OFFSITE" ] || return 0
  out="$(mktemp)"
  if ! offsite_send "$file" > "$out" 2>&1; then
    { date -Is; tail -5 "$out"; } > "$BACKUP_DIR/.offsite-error"
    log "ОШИБКА: копия вне ПК не отправлена: $(tail -1 "$out")" >&2
    rm -f "$out"
    return 1
  fi
  rm -f "$out" "$BACKUP_DIR/.offsite-error"
  echo "$(date +%s) $(basename "$file")" > "$BACKUP_DIR/.offsite-ok"
  log "копия вне ПК: $(basename "$file") → ${OFFSITE%%\?*}"
}

photos_mirror() {
  # Новые фото из тома app_data → $BACKUP_DIR/photos. Нет контейнера или фото — ничего не делаем
  local cid src
  cid="$(cd "$APP_DIR" && docker compose ps -q web 2>/dev/null | head -1)" || return 0
  [ -n "$cid" ] || return 0
  src="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' "$cid" 2>/dev/null)" || return 0
  [ -n "$src" ] && [ -d "$src/photos" ] || return 0
  mkdir -p "$BACKUP_DIR/photos"
  cp -a -n "$src/photos/." "$BACKUP_DIR/photos/"
  log "фото: $(find "$BACKUP_DIR/photos" -name '*.jpg' ! -name '*_t.jpg' | wc -l) шт., $(du -sh "$BACKUP_DIR/photos" | cut -f1)"
}

photos_offsite() {
  # Копия фото вне ПК: только новые файлы (rsync/rclone), для https — архив photos.tar при изменениях
  local dir="$BACKUP_DIR/photos" sig rest hostpart path port
  [ -n "$OFFSITE" ] && [ -d "$dir" ] || return 0
  sig="$(find "$dir" -type f | wc -l) $(du -sk "$dir" | cut -f1)"
  [ "$(cat "$BACKUP_DIR/.photos-sent" 2>/dev/null)" != "$sig" ] || return 0
  case "$OFFSITE" in
    ssh://*)
      rest="${OFFSITE#ssh://}"; hostpart="${rest%%/*}"; path="/${rest#*/}"; port=22
      [ "$rest" != "$hostpart" ] || path="posbot-backups"
      if [[ "$hostpart" == *:* ]]; then port="${hostpart##*:}"; hostpart="${hostpart%:*}"; fi
      local opts=(-i "$BACKUP_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20)
      if command -v rsync >/dev/null && ssh "${opts[@]}" -p "$port" "$hostpart" "command -v rsync" >/dev/null 2>&1; then
        rsync -a -e "ssh ${opts[*]} -p $port" "$dir/" "$hostpart:$path/photos/" || return 1
      else
        tar -C "$dir" -cf - . | ssh "${opts[@]}" -p "$port" "$hostpart" "mkdir -p '$path/photos' && tar -C '$path/photos' -xkf - 2>/dev/null; true" || return 1
      fi
      ;;
    rclone:*)
      command -v rclone >/dev/null || return 1
      rclone copy "$dir" "${OFFSITE#rclone:}/photos" || return 1
      ;;
    https://*|http://*)
      tar -C "$BACKUP_DIR" -cf "$BACKUP_DIR/photos.tar.tmp" photos || return 1
      curl -fsS --max-time 1800 -X PUT --data-binary @"$BACKUP_DIR/photos.tar.tmp" "${OFFSITE}photos.tar" || { rm -f "$BACKUP_DIR/photos.tar.tmp"; return 1; }
      rm -f "$BACKUP_DIR/photos.tar.tmp"
      ;;
    *) return 1 ;;
  esac
  echo "$sig" > "$BACKUP_DIR/.photos-sent"
  log "фото скопированы вне ПК"
}

mkdir -p "$BACKUP_DIR"
umask 077

if [ "$OFFSITE_ONLY" = "1" ]; then
  last="$(latest_backup)"
  [ -n "$last" ] || { echo "бэкапов ещё нет: сначала posbot backup" >&2; exit 1; }
  offsite_copy "$last"
  exit $?
fi

if [ -n "$IF_STALE" ]; then
  last="$(latest_backup)"
  if [ -n "$last" ] && [ $(( $(date +%s) - $(mtime "$last") )) -lt $(( IF_STALE * 3600 )) ]; then
    # Свежий бэкап есть. Если в прошлый раз копия вне ПК не ушла (не было интернета) — повторяем
    ok_at="$(cut -d' ' -f1 "$BACKUP_DIR/.offsite-ok" 2>/dev/null || echo 0)"
    if [ -n "$OFFSITE" ] && [ "${ok_at:-0}" -lt "$(mtime "$last")" ]; then
      offsite_copy "$last"
    fi
    exit 0
  fi
fi

cd "$APP_DIR"
DB_NAME=$(env_value MYSQL_DATABASE)
ROOT_PASS=$(env_value MYSQL_ROOT_PASSWORD)
ts=$(date +%Y%m%d_%H%M)
file="$BACKUP_DIR/${DB_NAME}_${ts}.sql.gz"

trap 'rm -f "$file.tmp"' EXIT
docker compose exec -T -e MYSQL_PWD="$ROOT_PASS" db \
  mysqldump -uroot --single-transaction --routines --triggers --no-tablespaces "$DB_NAME" \
  | gzip > "$file.tmp"
# Пустой или битый дамп не должен вытеснить хорошие копии
gzip -t "$file.tmp"
[ "$(gzip -dc "$file.tmp" | head -c 100 | wc -c)" -gt 0 ]
mv "$file.tmp" "$file"
log "бэкап: $file ($(du -h "$file" | cut -f1))"

find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime +"$KEEP_DAYS" -delete

photos_mirror || log "ОШИБКА: фото не скопированы" >&2
status=0
offsite_copy "$file" || status=$?
photos_offsite || log "ОШИБКА: фото не скопированы вне ПК (повтор при следующем бэкапе)" >&2
exit "$status"
