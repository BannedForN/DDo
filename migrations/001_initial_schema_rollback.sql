-- Откат миграции 001_initial_schema.sql
-- Удаление таблиц в обратном порядке (сначала зависимые, потом родительские)

-- Удаление таблицы messages
DROP TABLE IF EXISTS `messages`;

-- Удаление таблицы chats
DROP TABLE IF EXISTS `chats`;

-- Удаление таблицы users
DROP TABLE IF EXISTS `users`;

