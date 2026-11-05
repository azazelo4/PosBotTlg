"""Форма настроек: общая для мастера /setup и страницы /settings."""
import config
import settings_store

SETUP_GROUPS = ("telegram", "org", "web", "schedule", "labels", "fiscal")
# В мастере показываем самое нужное, остальное — на странице «Настройки»
SETUP_KEYS = ("TOKEN", "ORG_NAME", "ORG_BIN", "WEB_URL", "TIMEZONE", "LABEL_WIDTH_MM", "LABEL_HEIGHT_MM",
              "FISCAL_ENABLED", "WEBKASSA_URL", "WEBKASSA_LOGIN", "WEBKASSA_PASSWORD", "WEBKASSA_API_KEY")


def _display(setting, value):
    if value is None:
        return ""
    if setting.kind == "float":
        return f"{value:g}"
    return str(value)


def build_groups(keys=None, overrides=None):
    """[(group_key, title, [field…])] для шаблона. Секреты не выводятся, только признак «задан»."""
    overrides = overrides or {}
    groups = []
    for group in SETUP_GROUPS:
        fields = []
        for s in config.SETTINGS:
            if s.group != group or s.hidden or (keys is not None and s.key not in keys):
                continue
            value = getattr(config, s.key)
            locked = config.env_overridden(s.key)
            field = {
                "s": s,
                "locked": locked,
                "is_set": value not in (None, "", False),
                "value": "" if s.secret else overrides.get(s.key, _display(s, value)),
                "checked": bool(value) if s.kind == "bool" else False,
            }
            fields.append(field)
        if fields:
            groups.append((group, config.GROUPS[group], fields))
    return groups


def collect(form, keys):
    """Разбор формы → (changes, errors). Пустое поле секрета — оставить как есть."""
    changes, errors = {}, []
    for key in keys:
        s = config.BY_KEY[key]
        if s.hidden or config.env_overridden(key):
            continue
        if s.kind == "bool":
            changes[key] = bool(form.get(key))
            continue
        raw = (form.get(key) or "").strip()
        if s.secret:
            if form.get("clear_" + key):
                changes[key] = None
            elif raw:
                try:
                    changes[key] = config.parse_value(s, raw)
                except config.SettingError as e:
                    errors.append(str(e))
            continue
        try:
            changes[key] = config.parse_value(s, raw)
        except config.SettingError as e:
            errors.append(str(e))
    if changes.get("LABEL_WIDTH_MM") and changes["LABEL_WIDTH_MM"] > 120 or \
            changes.get("LABEL_HEIGHT_MM") and changes["LABEL_HEIGHT_MM"] > 120:
        errors.append("Размер ценника — не больше 120 мм.")
    return changes, errors


def check_token_change(changes):
    """Новый токен проверяется запросом к Telegram. Возвращает (ошибка | None, '@username' | None)."""
    token = changes.get("TOKEN")
    if not token or token == config.TOKEN:
        return None, None
    ok, result = settings_store.check_telegram_token(token)
    return (None, result) if ok else (result, None)


def fiscal_errors(changes):
    """Включённая фискализация требует учётных данных Webkassa."""
    def current(key):
        return changes[key] if key in changes else getattr(config, key)
    if not current("FISCAL_ENABLED"):
        return []
    missing = [config.BY_KEY[k].title for k in ("WEBKASSA_LOGIN", "WEBKASSA_PASSWORD", "WEBKASSA_API_KEY")
               if not current(k)]
    return [f"Для фискализации заполните: {', '.join(missing)}."] if missing else []
