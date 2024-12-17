-- Adminer 4.8.1 MySQL 8.0.40-0ubuntu0.20.04.1 dump

SET NAMES utf8;
SET time_zone = '+05:00';
SET foreign_key_checks = 0;
SET sql_mode = 'NO_AUTO_VALUE_ON_ZERO';

SET NAMES utf8mb4;

DROP TABLE IF EXISTS `SHIFT`;
CREATE TABLE `SHIFT` (
  `date` date NOT NULL,
  `SHIFT_OPEN` datetime DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
  `SHIFT_CLOSE` datetime DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
  `SHIFT_NUM` int NOT NULL,
  `USER_ID` int NOT NULL,
  `STORE_ID` int NOT NULL,
  `SALES_TOTAL` int NOT NULL,
  `VISA_TOTAL` int NOT NULL,
  `CASH_TOTAL` int NOT NULL,
  `INCASATION` int NOT NULL,
  `CASHBOX` int NOT NULL,
  KEY `USER_ID` (`USER_ID`),
  KEY `SHIFT_NUM` (`SHIFT_NUM`),
  CONSTRAINT `SHIFT_ibfk_1` FOREIGN KEY (`USER_ID`) REFERENCES `users` (`id`),
  CONSTRAINT `SHIFT_ibfk_2` FOREIGN KEY (`SHIFT_NUM`) REFERENCES `sales` (`shift_num`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


DROP TABLE IF EXISTS `products`;
CREATE TABLE `products` (
  `id` int NOT NULL AUTO_INCREMENT,
  `article_number` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `product_type` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `manufacturer` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `model` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `metal_type` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `price_per_gram` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `min_price` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `размер` varchar(512) DEFAULT NULL,
  `weight` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `price` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `quantity` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `arrival_date` date DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


DROP TABLE IF EXISTS `products_bac`;
CREATE TABLE `products_bac` (
  `id` int NOT NULL AUTO_INCREMENT,
  `store_id` int DEFAULT NULL,
  `article_number` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  `metal_type` enum('золото','серебро','бижутерия') COLLATE utf8mb4_general_ci NOT NULL,
  `product_type` enum('кольцо','цепь','серьги','браслет','подвеска','брошь','набор','часы','блезык','булавка','детские серьги','колье','ложка','ионизатор','печатка','подвеска+кольцо','бусы') CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `model` text CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
  `weight` decimal(10,0) NOT NULL,
  `price_per_gram` decimal(10,0) NOT NULL,
  `price` decimal(10,0) NOT NULL,
  `min_price` decimal(10,0) NOT NULL,
  `arrival_date` date NOT NULL,
  `quantity` int NOT NULL,
  `manufacturer` text COLLATE utf8mb4_general_ci NOT NULL,
  `commentaries` tinyint NOT NULL,
  PRIMARY KEY (`id`),
  KEY `store_id` (`store_id`),
  CONSTRAINT `products_bac_ibfk_1` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;


DROP TABLE IF EXISTS `sales`;
CREATE TABLE `sales` (
  `id` int NOT NULL AUTO_INCREMENT,
  `product_id` int DEFAULT NULL,
  `store_id` int DEFAULT NULL,
  `user_id` int DEFAULT NULL,
  `sale_date` datetime NOT NULL,
  `sold_price` decimal(12,0) NOT NULL,
  `payment_type` tinytext COLLATE utf8mb4_general_ci NOT NULL,
  `shift_num` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `product_id` (`product_id`),
  KEY `store_id` (`store_id`),
  KEY `user_id` (`user_id`),
  KEY `shift_num` (`shift_num`),
  CONSTRAINT `sales_ibfk_2` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`),
  CONSTRAINT `sales_ibfk_3` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`),
  CONSTRAINT `sales_ibfk_4` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;


DROP TABLE IF EXISTS `stores`;
CREATE TABLE `stores` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;


DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `store_id` int DEFAULT NULL,
  `phone_number` varchar(255) COLLATE utf8mb4_general_ci NOT NULL,
  `role` enum('admin','cashier') COLLATE utf8mb4_general_ci NOT NULL,
  `name` char(255) COLLATE utf8mb4_general_ci NOT NULL,
  `is_active` tinyint(1) DEFAULT '1',
  PRIMARY KEY (`id`),
  KEY `store_id` (`store_id`),
  CONSTRAINT `users_ibfk_1` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;


-- 2024-12-17 21:30:48
