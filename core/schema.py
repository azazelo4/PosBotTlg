"""Схема БД и идемпотентные миграции.

Запускается при старте бота (ensure_schema). Безопасно и для пустой БД,
и для уже работающей: таблицы создаются через CREATE TABLE IF NOT EXISTS,
недостающие колонки и индексы добавляются, существующие данные не трогаются.
"""
import logging

logger = logging.getLogger(__name__)

# Магазины хранятся в таблице stores и задаются мастером /setup (или переменной STORES для пустой базы).
METAL_TYPES = ('золото', 'серебро', 'бижутерия')
PRODUCT_TYPES = (
    'кольцо', 'цепь', 'серьги', 'браслет', 'подвеска', 'брошь', 'набор', 'часы',
    'блезык', 'булавка', 'детские серьги', 'колье', 'ложка', 'ионизатор',
    'печатка', 'подвеска+кольцо', 'бусы',
)


def _enum(values):
    return "enum(" + ",".join("'" + v.replace("'", "''") + "'" for v in values) + ")"


CHARSET = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"

TABLES = {
    "settings": f"""
        CREATE TABLE IF NOT EXISTS `settings` (
          `setting_key` varchar(64) NOT NULL,
          `value` text DEFAULT NULL,
          `is_secret` tinyint(1) NOT NULL DEFAULT 0,
          `updated_at` datetime NOT NULL,
          `updated_by` int DEFAULT NULL,
          PRIMARY KEY (`setting_key`)
        ) {CHARSET}""",
    "settings_log": f"""
        CREATE TABLE IF NOT EXISTS `settings_log` (
          `id` int NOT NULL AUTO_INCREMENT,
          `setting_key` varchar(64) NOT NULL,
          `action` varchar(16) NOT NULL,
          `user_id` int DEFAULT NULL,
          `changed_at` datetime NOT NULL,
          PRIMARY KEY (`id`)
        ) {CHARSET}""",
    "stores": f"""
        CREATE TABLE IF NOT EXISTS `stores` (
          `id` int NOT NULL AUTO_INCREMENT,
          `name` varchar(100) NOT NULL,
          `address` varchar(255) DEFAULT NULL,
          `is_active` tinyint(1) NOT NULL DEFAULT 1,
          `sort_order` int NOT NULL DEFAULT 0,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_stores_name` (`name`)
        ) {CHARSET}""",
    "users": f"""
        CREATE TABLE IF NOT EXISTS `users` (
          `id` int NOT NULL AUTO_INCREMENT,
          `phone_number` varchar(32) NOT NULL,
          `role` enum('admin','cashier','manager') NOT NULL,
          `name` varchar(255) NOT NULL,
          `surename` varchar(255) DEFAULT NULL,
          `is_active` tinyint(1) NOT NULL DEFAULT 1,
          `store_id` int DEFAULT NULL,
          `chat_id` bigint DEFAULT NULL,
          PRIMARY KEY (`id`),
          KEY `idx_users_phone` (`phone_number`),
          KEY `idx_users_chat` (`chat_id`)
        ) {CHARSET}""",
    "products": f"""
        CREATE TABLE IF NOT EXISTS `products` (
          `id` int NOT NULL AUTO_INCREMENT,
          `article_number` varchar(255) NOT NULL,
          `store_id` int DEFAULT NULL,
          `metal_type` {_enum(METAL_TYPES)} NOT NULL,
          `product_type` {_enum(PRODUCT_TYPES)} NOT NULL,
          `model` text NOT NULL,
          `size` varchar(32) DEFAULT NULL,
          `fineness` varchar(16) DEFAULT NULL,
          `inserts` varchar(255) DEFAULT NULL,
          `weight` float NOT NULL,
          `price_per_gram` decimal(10,0) NOT NULL DEFAULT 0,
          `price` decimal(10,0) NOT NULL,
          `min_price` decimal(10,0) NOT NULL,
          `arrival_date` date NOT NULL,
          `quantity` int NOT NULL DEFAULT 0,
          `manufacturer` text DEFAULT NULL,
          `commentaries` text DEFAULT NULL,
          `track_stock` tinyint(1) NOT NULL DEFAULT 1,
          `cost_price` decimal(12,0) DEFAULT NULL,
          `is_marked` tinyint(1) NOT NULL DEFAULT 0,
          PRIMARY KEY (`id`),
          KEY `idx_products_article` (`article_number`),
          KEY `idx_products_store` (`store_id`)
        ) {CHARSET}""",
    "receipts": f"""
        CREATE TABLE IF NOT EXISTS `receipts` (
          `id` int NOT NULL AUTO_INCREMENT,
          `store_id` int DEFAULT NULL,
          `user_id` int DEFAULT NULL,
          `created_at` datetime NOT NULL,
          `total` decimal(12,0) NOT NULL,
          `items_count` int NOT NULL,
          `payment_type` varchar(32) NOT NULL,
          `customer_id` int DEFAULT NULL,
          `public_token` char(32) DEFAULT NULL,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_receipts_token` (`public_token`),
          KEY `idx_receipts_date` (`created_at`),
          KEY `idx_receipts_store` (`store_id`),
          CONSTRAINT `receipts_user_fk` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
        ) {CHARSET}""",
    "product_codes": f"""
        CREATE TABLE IF NOT EXISTS `product_codes` (
          `id` int NOT NULL AUTO_INCREMENT,
          `code` varchar(255) NOT NULL,
          `code_key` varchar(255) NOT NULL,
          `product_id` int NOT NULL,
          `store_id` int DEFAULT NULL,
          `status` enum('in_stock','sold','returned','written_off') NOT NULL DEFAULT 'in_stock',
          `sale_id` int DEFAULT NULL,
          `created_at` datetime NOT NULL,
          `created_by` int DEFAULT NULL,
          `updated_at` datetime NOT NULL,
          `note` varchar(255) DEFAULT NULL,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_product_codes_key` (`code_key`),
          KEY `idx_product_codes_product` (`product_id`, `status`),
          CONSTRAINT `product_codes_product_fk` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`)
        ) {CHARSET}""",
    "sales": f"""
        CREATE TABLE IF NOT EXISTS `sales` (
          `id` int NOT NULL AUTO_INCREMENT,
          `product_id` int DEFAULT NULL,
          `user_id` int DEFAULT NULL,
          `sale_date` datetime NOT NULL,
          `sold_price` decimal(12,0) NOT NULL,
          `payment_type` varchar(32) NOT NULL,
          `shift_num` int DEFAULT NULL,
          `store_id` int DEFAULT NULL,
          `receipt_id` int DEFAULT NULL,
          `list_price` decimal(12,0) DEFAULT NULL,
          `mark_code_id` int DEFAULT NULL,
          PRIMARY KEY (`id`),
          KEY `idx_sales_receipt` (`receipt_id`),
          KEY `product_id` (`product_id`),
          KEY `idx_sales_store` (`store_id`),
          KEY `user_id` (`user_id`),
          KEY `idx_sales_date` (`sale_date`),
          CONSTRAINT `sales_ibfk_3` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`),
          CONSTRAINT `sales_ibfk_4` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`)
        ) {CHARSET}""",
    "returns": f"""
        CREATE TABLE IF NOT EXISTS `returns` (
          `id` int NOT NULL AUTO_INCREMENT,
          `original_sale_id` int NOT NULL,
          `product_article` varchar(255) NOT NULL,
          `return_price` decimal(12,0) NOT NULL,
          `payment_type` varchar(32) NOT NULL,
          `user_id` int NOT NULL,
          `store_id` int DEFAULT NULL,
          `return_date` datetime NOT NULL,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_returns_sale` (`original_sale_id`),
          KEY `idx_returns_date` (`return_date`),
          CONSTRAINT `returns_sale_fk` FOREIGN KEY (`original_sale_id`) REFERENCES `sales` (`id`),
          CONSTRAINT `returns_user_fk` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
        ) {CHARSET}""",
    "buybacks": f"""
        CREATE TABLE IF NOT EXISTS `buybacks` (
          `id` int NOT NULL AUTO_INCREMENT,
          `product_article` varchar(255) DEFAULT NULL,
          `description` text DEFAULT NULL,
          `metal_type` varchar(32) DEFAULT NULL,
          `weight` float DEFAULT NULL,
          `buyback_price` decimal(12,0) NOT NULL,
          `payment_type` varchar(32) NOT NULL,
          `user_id` int NOT NULL,
          `store_id` int DEFAULT NULL,
          `buyback_date` datetime NOT NULL,
          PRIMARY KEY (`id`),
          KEY `idx_buybacks_date` (`buyback_date`),
          CONSTRAINT `buybacks_user_fk` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
        ) {CHARSET}""",
    "trade_ins": f"""
        CREATE TABLE IF NOT EXISTS `trade_ins` (
          `id` int NOT NULL AUTO_INCREMENT,
          `old_product_article` varchar(255) DEFAULT NULL,
          `old_item_description` text DEFAULT NULL,
          `old_metal_type` varchar(32) DEFAULT NULL,
          `old_weight` float DEFAULT NULL,
          `old_item_value` decimal(12,0) DEFAULT NULL,
          `new_product_article` varchar(255) NOT NULL,
          `new_sale_price` decimal(12,0) DEFAULT NULL,
          `price_difference` decimal(12,0) NOT NULL,
          `payment_type_difference` varchar(32) DEFAULT NULL,
          `user_id` int NOT NULL,
          `store_id` int DEFAULT NULL,
          `trade_in_date` datetime NOT NULL,
          `new_mark_code_id` int DEFAULT NULL,
          PRIMARY KEY (`id`),
          KEY `idx_trade_ins_date` (`trade_in_date`),
          CONSTRAINT `trade_ins_user_fk` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
        ) {CHARSET}""",
    "web_login_tokens": f"""
        CREATE TABLE IF NOT EXISTS `web_login_tokens` (
          `token_hash` char(64) NOT NULL,
          `user_id` int NOT NULL,
          `expires_at` datetime NOT NULL,
          `used_at` datetime DEFAULT NULL,
          PRIMARY KEY (`token_hash`),
          KEY `idx_wlt_user` (`user_id`)
        ) {CHARSET}""",
    # Вход на кассу ПК: браузер показывает QR → сотрудник подтверждает в боте → браузер забирает вход
    "kassa_logins": f"""
        CREATE TABLE IF NOT EXISTS `kassa_logins` (
          `token_hash` char(64) NOT NULL,
          `status` enum('pending','approved','denied','used') NOT NULL DEFAULT 'pending',
          `user_id` int DEFAULT NULL,
          `client_info` varchar(255) DEFAULT NULL,
          `created_at` datetime NOT NULL,
          `expires_at` datetime NOT NULL,
          `decided_at` datetime DEFAULT NULL,
          PRIMARY KEY (`token_hash`),
          KEY `idx_kl_expires` (`expires_at`)
        ) {CHARSET}""",
    # Отметки фоновых задач: когда бот последний раз связался с Telegram, за какой день отправлен отчёт.
    # Нужны, чтобы ПК в магазине, выключенный ночью, догонял пропущенное при включении.
    "job_runs": f"""
        CREATE TABLE IF NOT EXISTS `job_runs` (
          `name` varchar(64) NOT NULL,
          `last_run_at` datetime NOT NULL,
          `last_for_date` date DEFAULT NULL,
          PRIMARY KEY (`name`)
        ) {CHARSET}""",
    # Оплаты чека: один способ — одна строка; смешанная оплата (часть наличными, часть Kaspi) — несколько
    "receipt_payments": f"""
        CREATE TABLE IF NOT EXISTS `receipt_payments` (
          `id` int NOT NULL AUTO_INCREMENT,
          `receipt_id` int NOT NULL,
          `payment_type` varchar(32) NOT NULL,
          `amount` decimal(12,0) NOT NULL,
          PRIMARY KEY (`id`),
          KEY `idx_rp_receipt` (`receipt_id`)
        ) {CHARSET}""",
    # Фото товаров: файлы в DATA_DIR/photos/<product_id>/, в БД — имя файла и порядок (0 — главное)
    "product_photos": f"""
        CREATE TABLE IF NOT EXISTS `product_photos` (
          `id` int NOT NULL AUTO_INCREMENT,
          `product_id` int NOT NULL,
          `file` varchar(64) NOT NULL,
          `sort_order` int NOT NULL DEFAULT 0,
          `created_at` datetime NOT NULL,
          `created_by` int DEFAULT NULL,
          PRIMARY KEY (`id`),
          KEY `idx_product_photos_product` (`product_id`, `sort_order`)
        ) {CHARSET}""",
    # Покупатели: телефон с согласием на хранение; chat_id — если подписались на чеки в Telegram (/start r_…)
    "customers": f"""
        CREATE TABLE IF NOT EXISTS `customers` (
          `id` int NOT NULL AUTO_INCREMENT,
          `phone` varchar(16) NOT NULL,
          `name` varchar(100) DEFAULT NULL,
          `birthday` date DEFAULT NULL,
          `note` varchar(255) DEFAULT NULL,
          `chat_id` bigint DEFAULT NULL,
          `consent_at` datetime NOT NULL,
          `consent_source` varchar(16) NOT NULL,
          `consent_by` int DEFAULT NULL,
          `marketing` tinyint(1) NOT NULL DEFAULT 0,
          `created_at` datetime NOT NULL,
          `updated_at` datetime NOT NULL,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_customers_phone` (`phone`),
          UNIQUE KEY `uq_customers_chat` (`chat_id`)
        ) {CHARSET}""",
    # Фискализация (Webkassa): очередь чеков. Операция сохраняется сразу, чек уходит в ОФД из очереди с повторами
    "fiscal_receipts": f"""
        CREATE TABLE IF NOT EXISTS `fiscal_receipts` (
          `id` int NOT NULL AUTO_INCREMENT,
          `kind` enum('sale','return','withdrawal','deposit','z_report') NOT NULL,
          `ref_id` int DEFAULT NULL,
          `store_id` int DEFAULT NULL,
          `cashbox` varchar(32) NOT NULL,
          `total` decimal(12,2) DEFAULT NULL,
          `user_id` int DEFAULT NULL,
          `status` enum('pending','done','error','manual') NOT NULL DEFAULT 'pending',
          `attempts` int NOT NULL DEFAULT 0,
          `next_try_at` datetime NOT NULL,
          `external_number` varchar(64) NOT NULL,
          `check_number` varchar(64) DEFAULT NULL,
          `ticket_url` varchar(512) DEFAULT NULL,
          `fiscal_at` datetime DEFAULT NULL,
          `offline` tinyint(1) NOT NULL DEFAULT 0,
          `response` mediumtext DEFAULT NULL,
          `error` text DEFAULT NULL,
          `created_at` datetime NOT NULL,
          `updated_at` datetime NOT NULL,
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_fiscal_external` (`external_number`),
          KEY `idx_fiscal_queue` (`status`, `next_try_at`),
          KEY `idx_fiscal_ref` (`kind`, `ref_id`)
        ) {CHARSET}""",
    "cash_withdrawals": f"""
        CREATE TABLE IF NOT EXISTS `cash_withdrawals` (
          `id` int NOT NULL AUTO_INCREMENT,
          `amount` decimal(12,2) NOT NULL,
          `comment` text DEFAULT NULL,
          `user_id` int NOT NULL,
          `store_id` int DEFAULT NULL,
          `withdrawal_date` datetime NOT NULL,
          PRIMARY KEY (`id`),
          KEY `user_id` (`user_id`),
          KEY `idx_withdrawals_date` (`withdrawal_date`),
          CONSTRAINT `cash_withdrawals_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
        ) {CHARSET}""",
    # Внесение наличных в кассу (размен на начало дня, возврат из банка) — обратная операция к инкассации
    "cash_deposits": f"""
        CREATE TABLE IF NOT EXISTS `cash_deposits` (
          `id` int NOT NULL AUTO_INCREMENT,
          `amount` decimal(12,2) NOT NULL,
          `comment` text DEFAULT NULL,
          `user_id` int NOT NULL,
          `store_id` int DEFAULT NULL,
          `deposit_date` datetime NOT NULL,
          PRIMARY KEY (`id`),
          KEY `user_id` (`user_id`),
          KEY `idx_deposits_date` (`deposit_date`),
          CONSTRAINT `cash_deposits_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
        ) {CHARSET}""",
}

# Колонки, которых могло не быть в старой БД: (таблица, колонка, определение)
COLUMNS = [
    ("products", "store_id", "int DEFAULT NULL"),
    ("sales", "store_id", "int DEFAULT NULL"),
    ("users", "chat_id", "bigint DEFAULT NULL"),
    ("products", "size", "varchar(32) DEFAULT NULL"),
    ("products", "track_stock", "tinyint(1) NOT NULL DEFAULT 1"),
    ("products", "fineness", "varchar(16) DEFAULT NULL"),
    ("products", "inserts", "varchar(255) DEFAULT NULL"),
    ("returns", "store_id", "int DEFAULT NULL"),
    ("buybacks", "description", "text DEFAULT NULL"),
    ("buybacks", "metal_type", "varchar(32) DEFAULT NULL"),
    ("buybacks", "weight", "float DEFAULT NULL"),
    ("buybacks", "store_id", "int DEFAULT NULL"),
    ("trade_ins", "old_item_description", "text DEFAULT NULL"),
    ("trade_ins", "old_metal_type", "varchar(32) DEFAULT NULL"),
    ("trade_ins", "old_weight", "float DEFAULT NULL"),
    ("trade_ins", "old_item_value", "decimal(12,0) DEFAULT NULL"),
    ("trade_ins", "new_sale_price", "decimal(12,0) DEFAULT NULL"),
    ("trade_ins", "store_id", "int DEFAULT NULL"),
    # чеки из нескольких позиций, цена по ценнику на момент продажи, себестоимость — для аналитики
    ("sales", "receipt_id", "int DEFAULT NULL"),
    ("sales", "list_price", "decimal(12,0) DEFAULT NULL"),
    ("products", "cost_price", "decimal(12,0) DEFAULT NULL"),
    # маркировка: поштучные коды DataMatrix
    ("products", "is_marked", "tinyint(1) NOT NULL DEFAULT 0"),
    ("sales", "mark_code_id", "int DEFAULT NULL"),
    ("trade_ins", "new_mark_code_id", "int DEFAULT NULL"),
    # фискализация: заводской номер кассы Webkassa (ЗНК) у магазина
    ("stores", "cashbox", "varchar(32) DEFAULT NULL"),
    # электронный чек: когда отправлен клиенту в Telegram
    ("receipts", "sent_at", "datetime DEFAULT NULL"),
]

# Колонки, которые раньше были NOT NULL без значения по умолчанию,
# из-за чего добавление товара/операций падало
MODIFY = [
    ("products", "manufacturer", "text DEFAULT NULL"),
    ("products", "commentaries", "text DEFAULT NULL"),
    ("buybacks", "product_article", "varchar(255) DEFAULT NULL"),
    ("trade_ins", "old_product_article", "varchar(255) DEFAULT NULL"),
    ("trade_ins", "payment_type_difference", "varchar(32) DEFAULT NULL"),
    ("cash_withdrawals", "store_id", "int DEFAULT NULL"),
    ("sales", "payment_type", "varchar(32) NOT NULL"),
    ("returns", "store_id", "int DEFAULT NULL"),
    ("buybacks", "store_id", "int DEFAULT NULL"),
    ("trade_ins", "store_id", "int DEFAULT NULL"),
]

INDEXES = [
    ("products", "idx_products_store", "(`store_id`)"),
    ("sales", "idx_sales_store", "(`store_id`)"),
    ("users", "idx_users_chat", "(`chat_id`)"),
    ("sales", "idx_sales_date", "(`sale_date`)"),
    ("products", "idx_products_article", "(`article_number`)"),
    ("sales", "idx_sales_receipt", "(`receipt_id`)"),
    ("receipts", "idx_receipts_customer", "(`customer_id`)"),
]


def _column_info(cursor, table, column):
    cursor.execute(
        "SELECT IS_NULLABLE, DATA_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    # MySQL 8+ может отдавать значения information_schema как bytes
    return tuple(v.decode() if isinstance(v, (bytes, bytearray)) else v for v in row)


def _index_exists(cursor, table, index):
    cursor.execute(
        "SELECT 1 FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s LIMIT 1",
        (table, index),
    )
    return cursor.fetchone() is not None


def _enum_values(column_type):
    if column_type.lower().startswith("enum("):
        return [v.replace("''", "'") for v in column_type[len("enum('"):-len("')")].split("','")]
    return []


def _migrate_stores(cursor, default_stores):
    """Магазины: ENUM products.Store → справочник stores (один раз, без потери данных)."""
    cursor.execute(
        "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'products' AND COLUMN_NAME = 'Store'")
    legacy = cursor.fetchone()
    if legacy:
        column_type = legacy[0].decode() if isinstance(legacy[0], (bytes, bytearray)) else legacy[0]
        cursor.execute("SELECT DISTINCT `Store` FROM products WHERE `Store` IS NOT NULL AND `Store` != ''")
        used = [r[0] for r in cursor.fetchall()]
        names = list(dict.fromkeys(_enum_values(column_type) + used))
        logger.info("Миграция: переношу магазины в справочник: %s", ", ".join(names))
        for order, name in enumerate(names):
            cursor.execute("INSERT IGNORE INTO stores (name, sort_order) VALUES (%s, %s)", (name, order))
        cursor.execute("UPDATE products p JOIN stores s ON s.name = p.`Store` SET p.store_id = s.id WHERE p.store_id IS NULL")
        # Колонку не удаляем, а переименовываем: старые значения остаются для сверки
        cursor.execute("ALTER TABLE products CHANGE COLUMN `Store` `legacy_store` varchar(100) DEFAULT NULL")
        # Сотрудники со store_id, которого нет в справочнике, остаются без магазина
        cursor.execute("UPDATE users u LEFT JOIN stores s ON s.id = u.store_id SET u.store_id = NULL "
                       "WHERE u.store_id IS NOT NULL AND s.id IS NULL")
    cursor.execute("SELECT COUNT(*) FROM stores")
    if cursor.fetchone()[0] == 0 and default_stores:
        for order, name in enumerate(default_stores):
            cursor.execute("INSERT IGNORE INTO stores (name, sort_order) VALUES (%s, %s)", (name, order))
        logger.info("Созданы магазины: %s", ", ".join(default_stores))
    # Магазин продажи — магазин товара на момент продажи; возврат — магазин продажи
    cursor.execute("UPDATE sales s JOIN products p ON p.id = s.product_id SET s.store_id = p.store_id "
                   "WHERE s.store_id IS NULL AND p.store_id IS NOT NULL")
    if legacy:
        cursor.execute("UPDATE returns r JOIN sales s ON s.id = r.original_sale_id SET r.store_id = s.store_id "
                       "WHERE s.store_id IS NOT NULL")
        cursor.execute("UPDATE trade_ins t JOIN products p ON p.article_number = t.new_product_article "
                       "SET t.store_id = p.store_id WHERE p.store_id IS NOT NULL")
        for table in ("buybacks", "cash_withdrawals"):
            cursor.execute(f"UPDATE {table} x LEFT JOIN stores s ON s.id = x.store_id SET x.store_id = NULL "
                           "WHERE x.store_id IS NOT NULL AND s.id IS NULL")


FISCAL_KINDS = "enum('sale','return','withdrawal','deposit','z_report')"


def _migrate_fiscal_kinds(cursor):
    """В очереди фискализации появился вид «внесение» (deposit) — расширяем enum на старых базах."""
    cursor.execute("SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                   "AND TABLE_NAME = 'fiscal_receipts' AND COLUMN_NAME = 'kind'")
    row = cursor.fetchone()
    column_type = (row[0].decode() if isinstance(row[0], bytes) else row[0]) if row else None
    if column_type and "'deposit'" not in column_type:
        logger.info("Миграция: fiscal_receipts.kind — добавляю 'deposit'")
        cursor.execute(f"ALTER TABLE `fiscal_receipts` MODIFY COLUMN `kind` {FISCAL_KINDS} NOT NULL")


def ensure_schema(connection, default_stores=None):
    """Создаёт недостающие таблицы/колонки. Принимает открытое соединение.

    Бот и веб стартуют одновременно: миграции выполняются под именованной блокировкой MySQL.
    """
    cursor = connection.cursor()
    cursor.execute("SELECT GET_LOCK('posbot_schema', 120)")
    cursor.fetchall()
    try:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        for name, ddl in TABLES.items():
            cursor.execute(ddl)
        for table, column, definition in COLUMNS:
            if _column_info(cursor, table, column) is None:
                logger.info("Миграция: добавляю колонку %s.%s", table, column)
                cursor.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
        for table, column, definition in MODIFY:
            info = _column_info(cursor, table, column)
            if info is None:
                continue
            nullable = info[0] == 'YES'
            wants_null = 'NOT NULL' not in definition
            data_type = (info[1] or '').lower()
            if (wants_null and not nullable) or (column == 'payment_type' and data_type != 'varchar'):
                logger.info("Миграция: изменяю колонку %s.%s", table, column)
                cursor.execute(f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` {definition}")
        _migrate_fiscal_kinds(cursor)
        # Аудит Н-1: вход из группы привязывал сотрудника/клиента к чату группы (id < 0) — отвязываем
        cursor.execute("UPDATE users SET chat_id = NULL WHERE chat_id < 0")
        cursor.execute("UPDATE customers SET chat_id = NULL WHERE chat_id < 0")
        _migrate_stores(cursor, default_stores)
        for table, index, cols in INDEXES:
            if not _index_exists(cursor, table, index):
                logger.info("Миграция: добавляю индекс %s.%s", table, index)
                cursor.execute(f"ALTER TABLE `{table}` ADD INDEX `{index}` {cols}")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        connection.commit()
        logger.info("Схема БД проверена и актуальна.")
    finally:
        try:
            cursor.execute("SELECT RELEASE_LOCK('posbot_schema')")
            cursor.fetchall()
        finally:
            cursor.close()
