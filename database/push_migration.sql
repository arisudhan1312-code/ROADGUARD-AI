-- ROADGUARD AI: Push subscriptions table
-- Run once: SOURCE D:/New folder/ROADGAURD_/database/push_migration.sql;
USE roadguard_ai;
CREATE TABLE IF NOT EXISTS push_subscriptions (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id    BIGINT UNSIGNED NOT NULL,
  endpoint   VARCHAR(2000) NOT NULL,
  p256dh     VARCHAR(255) NOT NULL,
  auth       VARCHAR(255) NOT NULL,
  is_active  TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_push_endpoint (endpoint(500)),
  KEY idx_push_user_active (user_id, is_active),
  CONSTRAINT fk_push_user FOREIGN KEY (user_id) REFERENCES users (id) ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB;
