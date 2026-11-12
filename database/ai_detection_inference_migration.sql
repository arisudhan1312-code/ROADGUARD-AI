USE roadguard_ai;

-- Upgrade installations that already ran ai_detection_migration.sql.
-- Every schema change is guarded so this migration can be safely re-run.
DROP PROCEDURE IF EXISTS roadguard_upgrade_ai_detection;

DELIMITER //
CREATE PROCEDURE roadguard_upgrade_ai_detection()
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ai_detections'
      AND COLUMN_NAME = 'report_id'
  ) THEN
    ALTER TABLE ai_detections
      ADD COLUMN report_id VARCHAR(19) NULL AFTER user_id;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ai_detections'
      AND INDEX_NAME = 'idx_ai_detections_report_date'
  ) THEN
    ALTER TABLE ai_detections
      ADD KEY idx_ai_detections_report_date (report_id, created_at);
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'ai_detections'
      AND CONSTRAINT_NAME = 'fk_ai_detections_report'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
  ) THEN
    ALTER TABLE ai_detections
      ADD CONSTRAINT fk_ai_detections_report
      FOREIGN KEY (report_id) REFERENCES road_reports (report_id)
      ON UPDATE CASCADE
      ON DELETE SET NULL;
  END IF;

  ALTER TABLE ai_detections
    MODIFY detection_status ENUM(
      'Model Unavailable',
      'Pending Admin Review',
      'Pending Inference',
      'Low Confidence',
      'Awaiting Admin Review',
      'No Detections',
      'Confirmed',
      'Rejected',
      'Failed'
    ) NOT NULL DEFAULT 'Model Unavailable';
END//
DELIMITER ;

CALL roadguard_upgrade_ai_detection();
DROP PROCEDURE roadguard_upgrade_ai_detection;
