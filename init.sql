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
