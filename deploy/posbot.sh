#!/usr/bin/env bash
# posbot — управление установленным POS-ботом (ставится установщиком в /usr/local/bin/posbot).
#
#   posbot status [--check]    контейнеры, версия, связь с Telegram, бэкапы и копия вне ПК
#   posbot logs [bot|web|db]   логи (Ctrl+C — выход)
#   posbot update [версия]     бэкап → новая версия образа → перезапуск (миграции БД автоматические)
#   posbot backup              бэкап БД сейчас
#   posbot restore ФАЙЛ        восстановить БД из бэкапа (перед этим делается бэкап текущей)
#   posbot setup-code          код мастера первоначальной настройки
#   posbot web-url [АДРЕС]     адрес веб-админки (как в «Настройках»)
#   posbot offsite [АДРЕС|test|off]   копия бэкапов вне ПК: ssh://user@host/путь, rclone:remote:путь, https://…/
#   posbot tunnel [cloudflare ТОКЕН|tailscale|off]   публичный HTTPS без белого IP (Mini App, вход снаружи)
#   posbot version             версия
set -euo pipefail

DIR="${POSBOT_DIR:-/opt/posbot}"
BACKUP_DIR="${BACKUP_DIR:-/opt/pos-bot-backups}"
BACKUP_CONFIG="${BACKUP_CONFIG:-/etc/pos-bot-backup.env}"
OFFSITE_WARN_DAYS=2

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

cfg_get() { grep -E "^$1=" "$BACKUP_CONFIG" 2>/dev/null | tail -n1 | cut -d= -f2- || true; }

cfg_set() {
  local key="$1" value="$2"
  [ -f "$BACKUP_CONFIG" ] || install -m 600 /dev/null "$BACKUP_CONFIG"
  if grep -q "^$key=" "$BACKUP_CONFIG"; then
    K="$key" V="$value" awk -F= 'BEGIN{k=ENVIRON["K"];v=ENVIRON["V"]} $1==k{print k "=" v; next} {print}' \
      "$BACKUP_CONFIG" > "$BACKUP_CONFIG.tmp"
    chmod 600 "$BACKUP_CONFIG.tmp"; mv "$BACKUP_CONFIG.tmp" "$BACKUP_CONFIG"
  else
    echo "$key=$value" >> "$BACKUP_CONFIG"
  fi
}

# Значение поля из однострочного JSON /health (строка, число, true/false/null)
json_field() { sed -n "s/.*\"$1\": *\"\{0,1\}\([^,\"}]*\).*/\1/p"; }

ago() {
  local s="$1"
  if [ "$s" -lt 120 ]; then echo "только что"
  elif [ "$s" -lt 7200 ]; then echo "$((s / 60)) мин назад"
  elif [ "$s" -lt 172800 ]; then echo "$((s / 3600)) ч назад"
  else echo "$((s / 86400)) дн назад"; fi
}

offsite_target() { local t; t="$(cfg_get BACKUP_OFFSITE)"; echo "${t:-$(cfg_get BACKUP_PAR_URL)}"; }

show_backups() {
  # Печатает состояние бэкапов; код 1 — есть о чём предупредить
  local warn=0 last now target ok_at
  now="$(date +%s)"
  last="$(find "$BACKUP_DIR" -maxdepth 1 -name '*.sql.gz' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
  if [ -n "$last" ]; then
    echo "Последний бэкап: $(basename "$last"), $(ago $((now - $(stat -c %Y "$last"))))"
  else
    echo "Последний бэкап: ещё не было (posbot backup)"; warn=1
  fi
  target="$(offsite_target)"
  if [ -z "$target" ]; then
    echo "Копия вне ПК: НЕ НАСТРОЕНА — при поломке диска данные пропадут (posbot offsite ssh://…)"; warn=1
  else
    ok_at="$(cut -d' ' -f1 "$BACKUP_DIR/.offsite-ok" 2>/dev/null || true)"
    if [ -n "$ok_at" ]; then
      echo "Копия вне ПК: ${target%%\?*}, последняя $(ago $((now - ok_at)))"
      if [ $((now - ok_at)) -gt $((OFFSITE_WARN_DAYS * 86400)) ]; then
        echo "  ВНИМАНИЕ: последней копии вне ПК больше $OFFSITE_WARN_DAYS дней"; warn=1
      fi
    else
      echo "Копия вне ПК: ${target%%\?*}, ещё ни одной успешной (posbot offsite test)"; warn=1
    fi
    if [ -f "$BACKUP_DIR/.offsite-error" ]; then
      echo "  Последняя ошибка: $(tail -1 "$BACKUP_DIR/.offsite-error")"
    fi
  fi
  return $warn
}

cmd="${1:-status}"
shift || true
case "$cmd" in
  status)
    compose ps
    echo
    mode="$(env_get POSBOT_MODE)"
    echo "Режим: ${mode:-server}$([ -z "$(env_get TUNNEL_TOKEN)" ] || echo ", туннель Cloudflare")"
    problems=0
    if h="$(health)"; then
      echo "Версия: $(echo "$h" | json_field version)"
      [ "$(echo "$h" | json_field configured)" = "true" ] || echo "Настройка не завершена: posbot setup-code"
      echo "Адрес веб-админки: $(echo "$h" | json_field web_url)"
      seen="$(echo "$h" | json_field telegram_seconds_ago)"
      if [ -z "$seen" ] || [ "$seen" = "null" ]; then
        echo "Telegram: бот ещё не выходил на связь"
      elif [ "$seen" -le 900 ]; then
        echo "Telegram: связь есть ($(ago "$seen"))"
      else
        echo "Telegram: НЕТ СВЯЗИ $(ago "$seen") — проверьте интернет и posbot logs bot"; problems=1
      fi
    else
      echo "Веб-админка не отвечает: posbot logs web"; problems=1
    fi
    show_backups || problems=1
    # --check: код 1 при проблемах (для мониторинга); без него — всегда 0
    [ "${1:-}" != "--check" ] || exit $problems
    ;;
  web-url)
    if [ $# -eq 0 ]; then
      health | json_field web_url
    else
      compose exec -T web python -m setup_code --web-url "$1"
    fi
    ;;
  offsite)
    arg="${1:-}"
    case "$arg" in
      "")
        show_backups || true ;;
      test)
        [ -n "$(offsite_target)" ] || die "копия вне ПК не настроена: posbot offsite ssh://user@host/путь"
        find "$BACKUP_DIR" -maxdepth 1 -name '*.sql.gz' 2>/dev/null | grep -q . || do_backup
        APP_DIR="$DIR" BACKUP_DIR="$BACKUP_DIR" "$DIR/deploy/backup.sh" --offsite-only && echo "Готово: копия отправлена." ;;
      off)
        cfg_set BACKUP_OFFSITE ""; cfg_set BACKUP_PAR_URL ""
        rm -f "$BACKUP_DIR/.offsite-ok" "$BACKUP_DIR/.offsite-error"
        echo "Копия вне ПК выключена." ;;
      ssh://*|rclone:*|https://*)
        cfg_set BACKUP_OFFSITE "$arg"
        rm -f "$BACKUP_DIR/.offsite-ok" "$BACKUP_DIR/.offsite-error"
        echo "Копия вне ПК: $arg"
        if [[ "$arg" == ssh://* ]]; then
          key="$(cfg_get BACKUP_SSH_KEY)"; key="${key:-/root/.ssh/posbot_backup}"
          if [ ! -f "$key" ]; then
            mkdir -p "$(dirname "$key")" && chmod 700 "$(dirname "$key")"
            ssh-keygen -q -t ed25519 -N "" -C "posbot-backup@$(hostname)" -f "$key"
          fi
          echo
          echo "Добавьте этот ключ на сервер для копий (~/.ssh/authorized_keys пользователя из адреса):"
          echo
          cat "$key.pub"
          echo
        elif [[ "$arg" == rclone:* ]] && ! command -v rclone >/dev/null; then
          echo "Установите rclone и настройте хранилище: curl https://rclone.org/install.sh | sudo bash && sudo rclone config"
        fi
        echo "Проверка: posbot offsite test" ;;
      *)
        die "адрес копии: ssh://user@host/путь, rclone:remote:путь или https://…/ (posbot help)" ;;
    esac
    ;;
  tunnel)
    arg="${1:-}"
    case "$arg" in
      "")
        if [ -n "$(env_get TUNNEL_TOKEN)" ]; then
          echo "Туннель: Cloudflare"; compose ps tunnel
        elif command -v tailscale >/dev/null && tailscale funnel status 2>/dev/null | grep -q https://; then
          echo "Туннель: Tailscale Funnel"; tailscale funnel status
        else
          echo "Туннель не включён: Mini App и вход снаружи не работают, касса и бот — работают."
        fi
        echo "Адрес веб-админки: $(health | json_field web_url)" ;;
      cloudflare)
        token="${2:-}"
        [ -n "$token" ] || die "нужен токен: Cloudflare Zero Trust → Networks → Tunnels → Create a tunnel → Docker → строка после --token"
        set_env TUNNEL_TOKEN "$token"
        bash "$DIR/deploy/bootstrap-env.sh" .env >/dev/null
        compose up -d --remove-orphans
        echo "Туннель Cloudflare запущен. В туннеле добавьте Public Hostname:"
        echo "  домен pos.ваш-домен.kz → сервис HTTP, адрес web:8000"
        echo "Затем: posbot web-url https://pos.ваш-домен.kz" ;;
      tailscale)
        command -v tailscale >/dev/null || die "Tailscale не установлен: curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"
        tailscale funnel --bg 8000 || die "Funnel не включился: включите HTTPS и Funnel в консоли Tailscale (ссылка выше)"
        url="$(tailscale funnel status 2>/dev/null | grep -o 'https://[^ ]*' | head -1)"; url="${url%/}"
        [ -n "$url" ] || die "адрес Funnel не найден: tailscale funnel status"
        compose exec -T web python -m setup_code --web-url "$url"
        echo "Mini App и админка доступны по $url" ;;
      off)
        if [ -n "$(env_get TUNNEL_TOKEN)" ]; then
          set_env TUNNEL_TOKEN ""
          bash "$DIR/deploy/bootstrap-env.sh" .env >/dev/null
          # Контейнер выключенного профиля compose сам не останавливает
          compose rm -sf tunnel >/dev/null 2>&1 || true
          compose up -d --remove-orphans
        fi
        if command -v tailscale >/dev/null; then tailscale funnel reset >/dev/null 2>&1 || true; fi
        lan="$(env_get POSBOT_LAN_URL)"
        [ -z "$lan" ] || compose exec -T web python -m setup_code --web-url "$lan"
        echo "Туннель выключен." ;;
      *) die "posbot tunnel cloudflare ТОКЕН | tailscale | off" ;;
    esac
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
    sed -n '2,14p' "$0"
    ;;
  *)
    die "неизвестная команда $cmd (posbot help)"
    ;;
esac
