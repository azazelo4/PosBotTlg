SET NAMES utf8;
SET time_zone = '+05:00';
SET foreign_key_checks = 0;
SET sql_mode = 'NO_AUTO_VALUE_ON_ZERO';

SET NAMES utf8mb4;

DROP TABLE IF EXISTS `products`;
CREATE TABLE `products` (
  `id` int NOT NULL AUTO_INCREMENT,
  `article_number` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `Store` enum('ЦУМ','Отау Сауда') CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `metal_type` enum('золото','серебро','бижутерия') CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `product_type` enum('кольцо','цепь','серьги','браслет','подвеска','брошь','набор','часы','блезык','булавка','детские серьги','колье','ложка','ионизатор','печатка','подвеска+кольцо','бусы') CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `model` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `weight` float NOT NULL,
  `price_per_gram` decimal(10,0) NOT NULL,
  `price` decimal(10,0) NOT NULL,
  `min_price` decimal(10,0) NOT NULL,
  `arrival_date` date NOT NULL,
  `quantity` int NOT NULL,
  `manufacturer` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `commentaries` text COLLATE utf8mb4_general_ci NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

DROP TABLE IF EXISTS `sales`;
CREATE TABLE `sales` (
  `id` int NOT NULL AUTO_INCREMENT,
  `product_id` int DEFAULT NULL,
  `user_id` int DEFAULT NULL,
  `sale_date` datetime NOT NULL,
  `sold_price` decimal(12,0) NOT NULL,
  `payment_type` tinytext CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `shift_num` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `product_id` (`product_id`),
  KEY `user_id` (`user_id`),
  KEY `shift_num` (`shift_num`),
  CONSTRAINT `sales_ibfk_3` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`),
  CONSTRAINT `sales_ibfk_4` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

DROP TABLE IF EXISTS `cash_withdrawals`;
CREATE TABLE `cash_withdrawals` (
  `id` int NOT NULL AUTO_INCREMENT,
  `amount` decimal(12,2) NOT NULL,
  `comment` text COLLATE utf8mb4_general_ci,
  `user_id` int NOT NULL,
  `store_id` int NOT NULL,
  `withdrawal_date` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`),
  CONSTRAINT `cash_withdrawals_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `phone_number` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `role` enum('admin','cashier','manager') CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `name` char(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `surename` char(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci DEFAULT NULL,
  `is_active` tinyint(1) DEFAULT '1',
  `store_id` int DEFAULT NULL, -- Эта колонка остается, но теперь это не FK к stores.id. Может хранить ID из старой системы или быть переосмыслена.
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Таблицы, которые старый код ожидал, но которых не было в init.sql
-- (так они могли быть созданы вручную на сервере)
CREATE TABLE IF NOT EXISTS `returns` (
  `id` int NOT NULL AUTO_INCREMENT,
  `original_sale_id` int NOT NULL,
  `product_article` varchar(255) NOT NULL,
  `return_price` decimal(12,2) NOT NULL,
  `payment_type` varchar(50) NOT NULL,
  `user_id` int NOT NULL,
  `store_id` int NOT NULL,
  `return_date` datetime NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `buybacks` (
  `id` int NOT NULL AUTO_INCREMENT,
  `product_article` varchar(255) NOT NULL,
  `buyback_price` decimal(12,2) NOT NULL,
  `payment_type` varchar(50) NOT NULL,
  `user_id` int NOT NULL,
  `store_id` int NOT NULL,
  `buyback_date` datetime NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `trade_ins` (
  `id` int NOT NULL AUTO_INCREMENT,
  `old_product_article` varchar(255) NOT NULL,
  `new_product_article` varchar(255) NOT NULL,
  `price_difference` decimal(12,2) NOT NULL,
  `payment_type_difference` varchar(50) NOT NULL,
  `user_id` int NOT NULL,
  `store_id` int NOT NULL,
  `trade_in_date` datetime NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

INSERT INTO users (phone_number, role, name, is_active, store_id) VALUES ('77010000009', 'cashier', 'Старый кассир', 1, 2);
INSERT INTO products (article_number, Store, metal_type, product_type, model, weight, price_per_gram, price, min_price, arrival_date, quantity, manufacturer, commentaries)
  VALUES ('OLD1', 'ЦУМ', 'золото', 'кольцо', 'Старое кольцо', 2, 1, 100000, 90000, '2025-01-01', 2, 'Завод', '');
INSERT INTO sales (product_id, user_id, sale_date, sold_price, payment_type) VALUES (1, 1, '2025-06-01 12:00:00', 95000, 'Наличные');
