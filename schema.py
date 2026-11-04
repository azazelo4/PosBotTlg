"""Схема БД и идемпотентные миграции.

Запускается при старте бота (ensure_schema). Безопасно и для пустой БД,
и для уже работающей: таблицы создаются через CREATE TABLE IF NOT EXISTS,
недостающие колонки и индексы добавляются, существующие данные не трогаются.
"""
import logging

logger = logging.getLogger(__name__)

STORES = ('ЦУМ', 'Отау Сауда')
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
          `Store` {_enum(STORES)} NOT NULL,
          `metal_type` {_enum(METAL_TYPES)} NOT NULL,
          `product_type` {_enum(PRODUCT_TYPES)} NOT NULL,
          `model` text NOT NULL,
          `size` varchar(32) DEFAULT NULL,
          `weight` float NOT NULL,
          `price_per_gram` decimal(10,0) NOT NULL DEFAULT 0,
          `price` decimal(10,0) NOT NULL,
          `min_price` decimal(10,0) NOT NULL,
          `arrival_date` date NOT NULL,
          `quantity` int NOT NULL DEFAULT 0,
          `manufacturer` text DEFAULT NULL,
          `commentaries` text DEFAULT NULL,
          `track_stock` tinyint(1) NOT NULL DEFAULT 1,
          PRIMARY KEY (`id`),
          KEY `idx_products_article` (`article_number`)
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
          PRIMARY KEY (`id`),
          KEY `product_id` (`product_id`),
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
          PRIMARY KEY (`id`),
          KEY `idx_trade_ins_date` (`trade_in_date`),
          CONSTRAINT `trade_ins_user_fk` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
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
}

# Колонки, которых могло не быть в старой БД: (таблица, колонка, определение)
COLUMNS = [
    ("users", "chat_id", "bigint DEFAULT NULL"),
    ("products", "size", "varchar(32) DEFAULT NULL"),
    ("products", "track_stock", "tinyint(1) NOT NULL DEFAULT 1"),
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
    ("users", "idx_users_chat", "(`chat_id`)"),
    ("sales", "idx_sales_date", "(`sale_date`)"),
    ("products", "idx_products_article", "(`article_number`)"),
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


def ensure_schema(connection):
    """Создаёт недостающие таблицы/колонки. Принимает открытое соединение."""
    cursor = connection.cursor()
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
        for table, index, cols in INDEXES:
            if not _index_exists(cursor, table, index):
                logger.info("Миграция: добавляю индекс %s.%s", table, index)
                cursor.execute(f"ALTER TABLE `{table}` ADD INDEX `{index}` {cols}")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        connection.commit()
        logger.info("Схема БД проверена и актуальна.")
    finally:
        cursor.close()
