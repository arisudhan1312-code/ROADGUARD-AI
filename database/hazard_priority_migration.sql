USE roadguard_ai;

-- Add priority fields to the existing report rows; no parallel table is created.
-- Existing reports remain unscored (NULL priority fields) until the API
-- recalculates them with the transparent Python rule set.
DROP PROCEDURE IF EXISTS roadguard_add_hazard_priority_fields;

DELIMITER //
CREATE PROCEDURE roadguard_add_hazard_priority_fields()
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'road_reports'
      AND COLUMN_NAME = 'calculated_priority'
  ) THEN
    ALTER TABLE road_reports
      ADD COLUMN calculated_priority ENUM('Critical', 'High', 'Medium', 'Low') NULL
      AFTER priority;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'road_reports'
      AND COLUMN_NAME = 'hazard_priority'
  ) THEN
    ALTER TABLE road_reports
      ADD COLUMN hazard_priority ENUM('Critical', 'High', 'Medium', 'Low') NULL
      AFTER calculated_priority;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'road_reports'
      AND COLUMN_NAME = 'priority_score'
  ) THEN
    ALTER TABLE road_reports
      ADD COLUMN priority_score TINYINT UNSIGNED NULL
      AFTER hazard_priority;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'road_reports'
      AND COLUMN_NAME = 'priority_report_count'
  ) THEN
    ALTER TABLE road_reports
      ADD COLUMN priority_report_count INT UNSIGNED NULL
      AFTER priority_score;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'road_reports'
      AND COLUMN_NAME = 'traffic_importance'
  ) THEN
    ALTER TABLE road_reports
      ADD COLUMN traffic_importance ENUM('Unknown', 'Low', 'Medium', 'High')
      NOT NULL DEFAULT 'Unknown'
      AFTER priority_report_count;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'road_reports'
      AND COLUMN_NAME = 'priority_source'
  ) THEN
    ALTER TABLE road_reports
      ADD COLUMN priority_source ENUM('Rule-Based', 'Admin Override')
      NOT NULL DEFAULT 'Rule-Based'
      AFTER traffic_importance;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'road_reports'
      AND INDEX_NAME = 'idx_road_reports_hazard_priority'
  ) THEN
    ALTER TABLE road_reports
      ADD KEY idx_road_reports_hazard_priority (hazard_priority, priority_score);
  END IF;
END//
DELIMITER ;

CALL roadguard_add_hazard_priority_fields();
DROP PROCEDURE roadguard_add_hazard_priority_fields;
