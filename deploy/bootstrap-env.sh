#!/usr/bin/env bash
# Создаёт или дополняет .env для docker compose. Безопасно запускать при каждом деплое.
#
#   deploy/bootstrap-env.sh /opt/pos-bot/.env
#
# * Пароли БД и ключ шифрования настроек генерируются ОДИН РАЗ, только если их нет в файле.
#   Существующие значения никогда не перегенерируются: MySQL берёт пароль из окружения только
#   при первой инициализации тома, новый пароль сломал бы подключение к уже созданной базе.
# * Токен бота, магазины, администратор и прочее в .env не нужны — они задаются в мастере /setup.
# * Совместимость: непустые переменные OVERRIDE_<ИМЯ> (их передаёт GitHub Actions из секретов и
#   переменных репозитория, если они заданы) записываются в файл как <ИМЯ>.
set -euo pipefail

ENV_FILE="${1:-.env}"
umask 077

if [ ! -f "$ENV_FILE" ]; then
  : > "$ENV_FILE"
  echo "Создан $ENV_FILE"
fi
chmod 600 "$ENV_FILE"

get() {
  # Последнее значение ключа (как читает docker compose); пусто, если ключа нет
  grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true
}

set_kv() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
  # Значение через ENVIRON, а не -v: awk не должен разбирать обратные слэши в паролях
  K="$key" V="$value" awk -F= '
    BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"] }
    $1 == k { if (!done) { print k "=" v; done = 1 }; next }
    { print }
    END { if (!done) print k "=" v }' "$ENV_FILE" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
}

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  else
    LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 48
  fi
}

fernet_key() {
  # 32 случайных байта в urlsafe base64 — формат ключа Fernet
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 32 | tr '+/' '-_'
  else
    head -c 32 /dev/urandom | base64 | tr '+/' '-_'
  fi
}

default() {
  # Записать значение, только если ключа нет или он пустой
  local key="$1" value="$2"
  if [ -z "$(get "$key")" ]; then
    set_kv "$key" "$value"
    echo "  $key: задан"
  fi
}

# --- совместимость: значения из секретов/переменных GitHub, если заданы
OVERRIDABLE="TOKEN MYSQL_USER MYSQL_PASSWORD MYSQL_DATABASE MYSQL_ROOT_PASSWORD ADMIN_PHONE ADMIN_NAME
REPORT_CHAT_ID TZ DAILY_REPORT_TIME LOW_STOCK_TIME LOW_STOCK_THRESHOLD WEB_DOMAIN WEB_SECRET
LABEL_WIDTH_MM LABEL_HEIGHT_MM LABEL_TITLE IMPORT_MIN_PRICE_PERCENT"
for key in $OVERRIDABLE; do
  var="OVERRIDE_$key"
  value="${!var:-}"
  if [ -n "$value" ] && [ "$(get "$key")" != "$value" ]; then
    set_kv "$key" "$value"
    echo "  $key: из настроек репозитория"
  fi
done
if [ -n "$(get WEB_DOMAIN)" ]; then
  set_kv WEB_URL "https://$(get WEB_DOMAIN)"
fi

# --- защита: пароля нет в .env, а база уже создана — новый пароль к ней не подойдёт
if [ -z "$(get MYSQL_PASSWORD)" ] && command -v docker >/dev/null 2>&1; then
  project="$(basename "$(cd "$(dirname "$ENV_FILE")" && pwd)" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')"
  if docker volume inspect "${project}_db_data" >/dev/null 2>&1; then
    echo "::error::В $ENV_FILE нет MYSQL_PASSWORD, но том базы ${project}_db_data уже существует." >&2
    echo "Новый пароль к существующей базе не подойдёт. Восстановите .env из резервной копии" >&2
    echo "или задайте секреты MYSQL_* в настройках репозитория. Ничего не изменено." >&2
    exit 1
  fi
fi

# --- значения по умолчанию (генерируются один раз)
default MYSQL_USER bot
default MYSQL_DATABASE bot
default MYSQL_PASSWORD "$(random_secret)"
default MYSQL_ROOT_PASSWORD "$(random_secret)"
default POSBOT_MASTER_KEY "$(fernet_key)"
default WEB_SECRET "$(random_secret)"
default TZ Asia/Almaty

# Подключение приложения к БД — всегда из параметров контейнера MySQL
if [ -z "$(get DB_HOST)" ] || [ "$(get DB_HOST)" = "db" ]; then
  set_kv DB_HOST db
  set_kv DB_USER "$(get MYSQL_USER)"
  set_kv DB_PASS "$(get MYSQL_PASSWORD)"
  set_kv DB_NAME "$(get MYSQL_DATABASE)"
fi

chmod 600 "$ENV_FILE"
echo "$ENV_FILE готов."
