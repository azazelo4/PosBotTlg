#!/usr/bin/env bash
# Подготовка чистого Ubuntu-VPS (22.04/24.04) для POS-бота. Запускать от root:
#   scp deploy/setup-server.sh root@IP:~ && ssh root@IP 'bash setup-server.sh'
#
# Что делает (повторный запуск безопасен):
#   часовой пояс, обновления и автообновления безопасности, swap, Docker,
#   пользователь gh-runner для GitHub runner, каталоги, ежедневный бэкап БД,
#   firewall (SSH, HTTP/HTTPS), fail2ban, вход по SSH только по ключу (если ключ уже добавлен).
#
# Переменные: TZ_NAME, SWAP_SIZE, APP_DIR (каталог бота, по умолчанию /opt/pos-bot),
# POSBOT_RUNNER=0 — без пользователя для GitHub runner (так вызывает deploy/install.sh).
set -euo pipefail

TZ_NAME="${TZ_NAME:-Asia/Almaty}"
SWAP_SIZE="${SWAP_SIZE:-2G}"
RUNNER_USER=gh-runner
APP_DIR="${APP_DIR:-/opt/pos-bot}"
POSBOT_RUNNER="${POSBOT_RUNNER:-1}"

log() { echo -e "\n==> $*"; }

[ "$(id -u)" -eq 0 ] || { echo "Запустите от root (sudo bash $0)"; exit 1; }
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || echo "ВНИМАНИЕ: скрипт проверялся на Ubuntu, у вас ${PRETTY_NAME:-неизвестная ОС}"

log "Часовой пояс: $TZ_NAME"
timedatectl set-timezone "$TZ_NAME"

log "Обновление системы"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get -y -q -o Dpkg::Options::=--force-confold upgrade
apt-get install -y -q ca-certificates curl jq unattended-upgrades ufw fail2ban

log "Автоматические обновления безопасности"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

if ! swapon --show | grep -q .; then
  log "Swap $SWAP_SIZE (запас для сборки образа и пиков MySQL)"
  fallocate -l "$SWAP_SIZE" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
  sysctl -q -p /etc/sysctl.d/99-swappiness.conf
else
  log "Swap уже есть, пропускаю"
fi

if ! command -v docker >/dev/null; then
  log "Установка Docker"
  curl -fsSL https://get.docker.com | sh
else
  log "Docker уже установлен: $(docker --version)"
fi
systemctl enable --now docker
# Ротация логов контейнеров по умолчанию, чтобы они не съели диск
if [ ! -f /etc/docker/daemon.json ]; then
  echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"5"}}' > /etc/docker/daemon.json
  systemctl restart docker
fi

mkdir -p "$APP_DIR" /opt/pos-bot-backups
chmod 700 /opt/pos-bot-backups
if [ "$POSBOT_RUNNER" = "1" ]; then
  log "Пользователь $RUNNER_USER для GitHub runner"
  id "$RUNNER_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$RUNNER_USER"
  usermod -aG docker "$RUNNER_USER"
  mkdir -p "/home/$RUNNER_USER/actions-runner"
  chown "$RUNNER_USER:$RUNNER_USER" "$APP_DIR" "/home/$RUNNER_USER/actions-runner"
fi

log "Ежедневный бэкап БД в 03:30"
cat > /etc/cron.d/pos-bot-backup <<EOF
# Скрипт появляется в $APP_DIR после первого деплоя/установки
30 3 * * * root [ -x $APP_DIR/deploy/backup.sh ] && APP_DIR=$APP_DIR $APP_DIR/deploy/backup.sh >> /var/log/pos-bot-backup.log 2>&1
EOF
if [ ! -f /etc/pos-bot-backup.env ]; then
  install -m 600 /dev/null /etc/pos-bot-backup.env
  cat > /etc/pos-bot-backup.env <<'EOF'
# Сколько дней хранить локальные копии
KEEP_DAYS=14
# Необязательно: URL для копии вне сервера (PUT-загрузка, например pre-authenticated
# request в S3-совместимом хранилище). Пусто — только локально.
BACKUP_PAR_URL=
EOF
fi

log "Firewall: входящие SSH, HTTP и HTTPS"
ufw allow OpenSSH >/dev/null
# 80/443 — для веб-админки и Mini App (Caddy с сертификатом Let's Encrypt)
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status | sed -n '1,6p'
# Docker публикует порты в обход ufw, поэтому в docker-compose.yml наружу
# ничего не публикуется (Adminer — только 127.0.0.1:8080).

log "fail2ban для SSH"
systemctl enable --now fail2ban

# Отключаем пароли, только если тот, кто запускает скрипт, уже входит по ключу —
# иначе можно потерять доступ к серверу
KEY_FILE=/root/.ssh/authorized_keys
[ -n "${SUDO_USER:-}" ] && KEY_FILE="$(getent passwd "$SUDO_USER" | cut -d: -f6)/.ssh/authorized_keys"
if [ -s "$KEY_FILE" ]; then
  log "SSH: вход только по ключу"
  cat > /etc/ssh/sshd_config.d/10-pos-bot.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
  sshd -t
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
else
  echo "ВНИМАНИЕ: SSH-ключ не найден, вход по паролю оставлен включённым."
  echo "Добавьте ключ (ssh-copy-id root@IP) и запустите скрипт ещё раз."
fi

log "Готово"
[ "$POSBOT_RUNNER" = "1" ] || exit 0
cat <<EOF
Дальше:
  1. Зарегистрировать GitHub runner от пользователя $RUNNER_USER (docs/DEPLOY_CLOUD.md, шаг 4).
  2. Запустить деплой в GitHub Actions.
Проверка: docker run --rm hello-world && free -h && ufw status
EOF
