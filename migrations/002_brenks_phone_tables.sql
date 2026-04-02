-- Таблица brenks_essence_users уже существует в БД, не создаём.

CREATE TABLE IF NOT EXISTS `brenks_essence_phone_chats` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `title` VARCHAR(128) NOT NULL,
    `user_id` INT NOT NULL,
    `created_at` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
    `is_hidden` TINYINT(1) NOT NULL DEFAULT 0,
    INDEX `idx_brenks_phone_chats_id` (`id`),
    INDEX `idx_brenks_phone_chats_user_id` (`user_id`),
    CONSTRAINT `fk_brenks_phone_chats_user_id`
        FOREIGN KEY (`user_id`)
        REFERENCES `brenks_essence_users` (`id_user`)
        ON DELETE CASCADE
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `brenks_essence_phone_messages` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `chat_id` INT NOT NULL,
    `message_index` INT NOT NULL,
    `sender` VARCHAR(16) NOT NULL,
    `text` TEXT NOT NULL,
    `created_at` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
    INDEX `idx_brenks_phone_messages_id` (`id`),
    INDEX `idx_brenks_phone_messages_chat_id` (`chat_id`),
    CONSTRAINT `fk_brenks_phone_messages_chat_id`
        FOREIGN KEY (`chat_id`)
        REFERENCES `brenks_essence_phone_chats` (`id`)
        ON DELETE CASCADE
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
