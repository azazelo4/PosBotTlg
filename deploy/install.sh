#!/usr/bin/env bash
# Установка POS-бота на чистый Ubuntu-VPS (22.04/24.04) одной командой. Запускать от root:
#
#   sudo bash install.sh --key <ключ_доступа> [--domain pos.example.kz]
#
# Ключ доступа к образу выдаёт поставщик (токен GitHub только на чтение пакетов).
# Без --domain установщик спросит домен; пустой ответ — бесплатный <IP>.sslip.io.
#
# Что делает (повторный запуск безопасен, данные и пароли сохраняются):
#   1. Docker, swap, firewall, fail2ban, автообновления, ежедневный бэкап (deploy/setup-server.sh)
#   2. Вход в реестр образов с ключом, загрузка образа
#   3. Каталог /opt/posbot: docker-compose.yml, Caddyfile, скрипты, .env (пароли — один раз)
#   4. Запуск и ссылка на мастер настройки /setup с кодом
#   5. Команда posbot: status, logs, update, backup, restore, setup-code
#
# Параметры:
#   --key KEY            ключ доступа (или переменная POSBOT_KEY)
#   --domain DOMAIN      домен веб-админки, A-запись → IP сервера
#   --version TAG        версия образа (по умолчанию latest)
#   --dir DIR            каталог установки (по умолчанию /opt/posbot)
#   --yes                ничего не спрашивать
#   --skip-system        не трогать систему (Docker уже стоит; для проверок и своих серверов)
#   --image NAME         другой образ, без тега (по умолчанию ghcr.io/azazelo4/posbottlg)
#   --no-pull            не скачивать образ (он уже есть локально)
set -euo pipefail

IMAGE="${POSBOT_IMAGE:-ghcr.io/azazelo4/posbottlg}"
VERSION="${POSBOT_VERSION:-latest}"
DIR="${POSBOT_DIR:-/opt/posbot}"
KEY="${POSBOT_KEY:-}"
REGISTRY_USER="${POSBOT_REGISTRY_USER:-azazelo4}"
DOMAIN=""
ASSUME_YES=0
SKIP_SYSTEM=0
PULL=1

die() { echo -e "\nОШИБКА: $*" >&2; exit 1; }
log() { echo -e "\n==> $*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --key) KEY="${2:-}"; shift 2 ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --dir) DIR="${2:-}"; shift 2 ;;
    --image) IMAGE="${2:-}"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --skip-system) SKIP_SYSTEM=1; shift ;;
    --no-pull) PULL=0; shift ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) die "неизвестный параметр $1 (см. --help)" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || die "запустите от root: sudo bash $0 ..."
[ -n "$VERSION" ] && [ -n "$IMAGE" ] && [ -n "$DIR" ] || die "пустой --image, --version или --dir"

ask() {
  # ask "Вопрос" default → ответ (в неинтерактивном режиме — default)
  # Читаем с терминала, а не stdin: при `curl ... | bash` stdin — это сам скрипт
  local answer=""
  if [ "$ASSUME_YES" = "0" ]; then
    { printf '%s ' "$1" > /dev/tty && read -r answer < /dev/tty; } 2>/dev/null || true
  fi
  echo "${answer:-$2}"
}

public_ip() {
  local ip=""
  for url in https://api.ipify.org https://ifconfig.me https://icanhazip.com; do
    ip="$(curl -4 -fsS --max-time 5 "$url" 2>/dev/null | tr -d '[:space:]')" || true
    [[ "$ip" =~ ^[0-9]+(\.[0-9]+){3}$ ]] && { echo "$ip"; return; }
  done
  hostname -I 2>/dev/null | awk '{print $1}'
}

FULL_IMAGE="$IMAGE:$VERSION"

# ---------------------------------------------------------------- 1. система и Docker
if [ "$SKIP_SYSTEM" = "0" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    log "Установка Docker"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q
    apt-get install -y -q ca-certificates curl
    curl -fsSL https://get.docker.com | sh
  fi
fi
command -v docker >/dev/null 2>&1 || die "Docker не установлен"
docker compose version >/dev/null 2>&1 || die "нет docker compose (плагин compose v2)"

# ---------------------------------------------------------------- 2. образ
if [ "$PULL" = "1" ]; then
  registry="${IMAGE%%/*}"
  if [ -n "$KEY" ]; then
    log "Вход в $registry"
    echo "$KEY" | docker login "$registry" -u "$REGISTRY_USER" --password-stdin >/dev/null \
      || die "ключ доступа не подошёл. Проверьте ключ или запросите новый у поставщика."
  elif ! grep -q "\"$registry\"" /root/.docker/config.json 2>/dev/null; then
    KEY="$(ask "Ключ доступа к образу:" "")"
    [ -n "$KEY" ] || die "нужен ключ доступа: --key <ключ>"
    echo "$KEY" | docker login "$registry" -u "$REGISTRY_USER" --password-stdin >/dev/null \
      || die "ключ доступа не подошёл"
  fi
  log "Загрузка образа $FULL_IMAGE"
  docker pull -q "$FULL_IMAGE" >/dev/null || die "не удалось скачать $FULL_IMAGE (нет доступа или такой версии)"
fi
docker image inspect "$FULL_IMAGE" >/dev/null 2>&1 || die "образа $FULL_IMAGE нет"

# ---------------------------------------------------------------- 3. файлы установки
log "Файлы в $DIR"
mkdir -p "$DIR"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"; docker rm -f posbot-extract >/dev/null 2>&1 || true' EXIT
docker rm -f posbot-extract >/dev/null 2>&1 || true
docker create --name posbot-extract "$FULL_IMAGE" >/dev/null
docker cp posbot-extract:/app/deploy "$tmp/deploy"
docker rm posbot-extract >/dev/null
rm -rf "$DIR/deploy"
mv "$tmp/deploy" "$DIR/deploy"
chmod +x "$DIR"/deploy/*.sh
cp "$DIR/deploy/compose.prod.yml" "$DIR/docker-compose.yml"
install -m 755 "$DIR/deploy/posbot.sh" /usr/local/bin/posbot

if [ "$SKIP_SYSTEM" = "0" ]; then
  log "Подготовка сервера"
  APP_DIR="$DIR" POSBOT_RUNNER=0 bash "$DIR/deploy/setup-server.sh"
fi

bash "$DIR/deploy/bootstrap-env.sh" "$DIR/.env"
set_env() {
  local key="$1" value="$2" file="$DIR/.env"
  if grep -q "^$key=" "$file"; then
    K="$key" V="$value" awk -F= 'BEGIN{k=ENVIRON["K"];v=ENVIRON["V"]} $1==k{print k "=" v; next} {print}' "$file" > "$file.tmp"
    chmod 600 "$file.tmp"; mv "$file.tmp" "$file"
  else
    echo "$key=$value" >> "$file"
  fi
}
set_env POSBOT_IMAGE "$IMAGE"
set_env POSBOT_VERSION "$VERSION"

# ---------------------------------------------------------------- 4. запуск
log "Запуск"
cd "$DIR"
docker compose up -d --remove-orphans

echo -n "Жду веб-админку"
health=""
for _ in $(seq 1 60); do
  if health="$(curl -fsS --max-time 3 http://127.0.0.1:8000/health 2>/dev/null)"; then break; fi
  health=""; echo -n "."; sleep 3
done
echo
if [ -z "$health" ]; then
  docker compose ps
  docker compose logs --tail=50 web
  die "веб-админка не ответила за 3 минуты (логи выше). Повторите установку или: posbot logs web"
fi

# ---------------------------------------------------------------- 5. итог
if echo "$health" | grep -q '"configured":true'; then
  log "Готово: бот уже настроен и работает ($(echo "$health" | sed -n 's/.*"version":"\([^"]*\)".*/\1/p'))."
  echo "Обновление: posbot update · статус: posbot status"
  exit 0
fi

if [ -z "$DOMAIN" ]; then
  ip="$(public_ip)"
  suggestion="${ip//./-}.sslip.io"
  echo
  echo "Домен для веб-админки: A-запись вашего домена → IP $ip."
  echo "Своего домена нет — можно бесплатный $suggestion (он сам указывает на этот IP)."
  DOMAIN="$(ask "Домен [$suggestion]:" "$suggestion")"
fi
DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%%/*}"

resolved="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}')" || true
ip="${ip:-$(public_ip)}"
if [ -z "$resolved" ]; then
  echo "ВНИМАНИЕ: домен $DOMAIN пока не резолвится. Добавьте A-запись → $ip и подождите несколько минут."
elif [ -n "$ip" ] && [ "$resolved" != "$ip" ]; then
  echo "ВНИМАНИЕ: $DOMAIN указывает на $resolved, а IP сервера $ip. Сертификат не выпустится, пока это не исправить."
fi

code="$(docker compose exec -T web python -m setup_code | sed -n 's/^Код настройки: //p')"
cat <<EOF

================================================================
  POS-бот установлен. Осталась настройка в браузере:

    https://$DOMAIN/setup
    Код настройки: ${code:-<posbot setup-code>}

  Понадобятся: токен бота от @BotFather, названия магазинов,
  ваш номер телефона в Telegram.

  Без домена: ssh -L 8000:127.0.0.1:8000 root@${ip:-<IP>}
  и http://127.0.0.1:8000/setup

  Команды: posbot status | logs | update | backup | restore
================================================================
EOF
