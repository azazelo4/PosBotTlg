#!/usr/bin/env bash
# posbot — управление установленным POS-ботом (ставится установщиком в /usr/local/bin/posbot).
#
#   posbot status              состояние контейнеров и версия
#   posbot logs [bot|web|db]   логи (Ctrl+C — выход)
#   posbot update [версия]     бэкап → новая версия образа → перезапуск (миграции БД автоматические)
#   posbot backup              бэкап БД сейчас
#   posbot restore ФАЙЛ        восстановить БД из бэкапа (перед этим делается бэкап текущей)
#   posbot setup-code          код мастера первоначальной настройки
#   posbot version             версия
set -euo pipefail

DIR="${POSBOT_DIR:-/opt/posbot}"
BACKUP_DIR="${BACKUP_DIR:-/opt/pos-bot-backups}"

die() { echo "ОШИБКА: $*" >&2; exit 1; }
[ -f "$DIR/docker-compose.yml" ] || die "установка не найдена в $DIR (задайте POSBOT_DIR)"
cd "$DIR"

env_get() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }
compose() { docker compose "$@"; }

health() { curl -fsS --max-time 3 http://127.0.0.1:8000/health 2>/dev/null; }

wait_healthy() {
  local h=""
  for _ in $(seq 1 60); do
    if h="$(health)"; then echo "$h"; return 0; fi
    sleep 3
  done
  return 1
}

do_backup() {
  APP_DIR="$DIR" BACKUP_DIR="$BACKUP_DIR" "$DIR/deploy/backup.sh"
}

set_env() {
  local key="$1" value="$2"
  if grep -q "^$key=" .env; then
    K="$key" V="$value" awk -F= 'BEGIN{k=ENVIRON["K"];v=ENVIRON["V"]} $1==k{print k "=" v; next} {print}' .env > .env.tmp
    chmod 600 .env.tmp; mv .env.tmp .env
  else
    echo "$key=$value" >> .env
  fi
}

cmd="${1:-status}"
shift || true
case "$cmd" in
  status)
    compose ps
    echo
    health || echo "веб-админка не отвечает"
    echo
    ;;
  logs)
    compose logs --tail=200 -f "$@"
    ;;
  version)
    echo "образ: $(env_get POSBOT_IMAGE):$(env_get POSBOT_VERSION)"
    health | sed -n 's/.*"version":"\([^"]*\)".*/запущена версия: \1/p'
    ;;
  setup-code)
    compose exec -T web python -m setup_code
    ;;
  backup)
    do_backup
    ;;
  update)
    image="$(env_get POSBOT_IMAGE)"; image="${image:-ghcr.io/azazelo4/posbottlg}"
    old="$(env_get POSBOT_VERSION)"; old="${old:-latest}"
    new="${1:-$old}"
    echo "==> Бэкап перед обновлением"
    do_backup
    echo "==> Загрузка $image:$new"
    if [ "${POSBOT_NO_PULL:-0}" != "1" ]; then
      docker pull -q "$image:$new" >/dev/null || die "не удалось скачать $image:$new"
    fi
    # Новые compose-файл, Caddyfile и скрипты из нового образа
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"; docker rm -f posbot-extract >/dev/null 2>&1 || true' EXIT
    docker rm -f posbot-extract >/dev/null 2>&1 || true
    docker create --name posbot-extract "$image:$new" >/dev/null
    docker cp posbot-extract:/app/deploy "$tmp/deploy"
    rm -rf "$DIR/deploy.prev" && mv "$DIR/deploy" "$DIR/deploy.prev"
    mv "$tmp/deploy" "$DIR/deploy"
    chmod +x "$DIR"/deploy/*.sh
    cp "$DIR/deploy/compose.prod.yml" docker-compose.yml
    install -m 755 "$DIR/deploy/posbot.sh" /usr/local/bin/posbot 2>/dev/null || true
    bash "$DIR/deploy/bootstrap-env.sh" .env >/dev/null
    set_env POSBOT_VERSION "$new"
    echo "==> Перезапуск"
    compose up -d --remove-orphans
    if h="$(wait_healthy)"; then
      echo "Готово: $h"
      docker image prune -f >/dev/null
    else
      compose logs --tail=50 web bot
      echo
      echo "Новая версия не отвечает. Вернуть прежнюю: posbot update $old"
      echo "Если обновление успело изменить базу — восстановить последний бэкап: posbot restore <файл в $BACKUP_DIR>"
      exit 1
    fi
    ;;
  restore)
    file="${1:-}"
    [ -f "$file" ] || die "укажите файл бэкапа: posbot restore $BACKUP_DIR/<файл>.sql.gz"
    gzip -t "$file" || die "файл повреждён: $file"
    if [ "${POSBOT_YES:-0}" != "1" ]; then
      read -r -p "Текущая база будет заменена данными из $(basename "$file"). Продолжить? [да/НЕТ] " answer
      [ "$answer" = "да" ] || [ "$answer" = "yes" ] || die "отменено"
    fi
    echo "==> Бэкап текущей базы на всякий случай"
    do_backup
    db="$(env_get MYSQL_DATABASE)"
    root_pass="$(env_get MYSQL_ROOT_PASSWORD)"
    echo "==> Останавливаю бота и веб"
    compose stop bot web
    echo "==> Восстанавливаю $db из $(basename "$file")"
    compose exec -T -e MYSQL_PWD="$root_pass" db mysql -uroot -e "DROP DATABASE IF EXISTS \`$db\`; CREATE DATABASE \`$db\`;"
    gzip -dc "$file" | compose exec -T -e MYSQL_PWD="$root_pass" db mysql -uroot "$db"
    compose start bot web
    wait_healthy >/dev/null && echo "Готово." || die "веб не поднялся: posbot logs web"
    ;;
  -h|--help|help)
    sed -n '2,11p' "$0"
    ;;
  *)
    die "неизвестная команда $cmd (posbot help)"
    ;;
esac
