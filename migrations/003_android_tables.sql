-- Таблицы для Android-чата (диалоги, сообщения пользователя и бота)

CREATE TABLE IF NOT EXISTS `brenks_essence_android_dialogs` (
    `id_android_dialogs` INT AUTO_INCREMENT PRIMARY KEY,
    `id_users` INT NOT NULL,
    `name_dialog` VARCHAR(255) NOT NULL,
    `date_created` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
    INDEX `idx_android_dialogs_id` (`id_android_dialogs`),
    INDEX `idx_android_dialogs_id_users` (`id_users`),
    CONSTRAINT `fk_android_dialogs_id_users`
        FOREIGN KEY (`id_users`)
        REFERENCES `brenks_essence_users` (`id_user`)
        ON DELETE CASCADE
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `brenks_essence_android_user_messages` (
    `id_user_android_message` INT AUTO_INCREMENT PRIMARY KEY,
    `id_android_dialogs` INT NOT NULL,
    `id_users` INT NOT NULL,
    `user_andoid_message` TEXT NOT NULL,
    `date_user_android_message` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
    `is_hidden` TINYINT(1) NOT NULL DEFAULT 0,
    INDEX `idx_user_messages_id` (`id_user_android_message`),
    INDEX `idx_user_messages_id_dialogs` (`id_android_dialogs`),
    INDEX `idx_user_messages_id_users` (`id_users`),
    CONSTRAINT `fk_user_messages_id_dialogs`
        FOREIGN KEY (`id_android_dialogs`)
        REFERENCES `brenks_essence_android_dialogs` (`id_android_dialogs`)
        ON DELETE CASCADE
        ON UPDATE CASCADE,
    CONSTRAINT `fk_user_messages_id_users`
        FOREIGN KEY (`id_users`)
        REFERENCES `brenks_essence_users` (`id_user`)
        ON DELETE CASCADE
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `brenks_essence_android_bot_messages` (
    `id_bot_android_message` INT AUTO_INCREMENT PRIMARY KEY,
    `id_android_dialogs` INT NOT NULL,
    `id_users` INT NOT NULL,
    `bot_android_message` TEXT NOT NULL,
    `date_bot_android_message` DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
    `tokens_android` INT NULL,
    INDEX `idx_bot_messages_id` (`id_bot_android_message`),
    INDEX `idx_bot_messages_id_dialogs` (`id_android_dialogs`),
    INDEX `idx_bot_messages_id_users` (`id_users`),
    CONSTRAINT `fk_bot_messages_id_dialogs`
        FOREIGN KEY (`id_android_dialogs`)
        REFERENCES `brenks_essence_android_dialogs` (`id_android_dialogs`)
        ON DELETE CASCADE
        ON UPDATE CASCADE,
    CONSTRAINT `fk_bot_messages_id_users`
        FOREIGN KEY (`id_users`)
        REFERENCES `brenks_essence_users` (`id_user`)
        ON DELETE CASCADE
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
