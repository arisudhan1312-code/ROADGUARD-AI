USE roadguard_ai;

-- Run after roadguard.sql. This adds AI image intake records without modifying
-- citizen reports, hazards, or any of the existing repair tables.
CREATE TABLE IF NOT EXISTS ai_detections (
  detection_id CHAR(36) NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  report_id VARCHAR(19) NULL,
  original_image_path VARCHAR(255) NOT NULL,
  annotated_image_path VARCHAR(255) NULL,
  hazard_category VARCHAR(80) NULL,
  detection_count SMALLINT UNSIGNED NULL,
  pothole_count SMALLINT UNSIGNED NULL,
  confidence_score DECIMAL(6,5) NULL,
  bounding_boxes JSON NULL,
  detection_status ENUM(
    'Model Unavailable',
    'Pending Admin Review',
    'Pending Inference',
    'Low Confidence',
    'Awaiting Admin Review',
    'No Detections',
    'Confirmed',
    'Rejected',
    'Failed'
  ) NOT NULL DEFAULT 'Model Unavailable',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  submitted_for_review_at TIMESTAMP NULL,
  reviewed_by BIGINT UNSIGNED NULL,
  reviewed_at TIMESTAMP NULL,
  review_notes VARCHAR(1000) NULL,
  PRIMARY KEY (detection_id),
  KEY idx_ai_detections_user_date (user_id, created_at),
  KEY idx_ai_detections_report_date (report_id, created_at),
  KEY idx_ai_detections_review (detection_status, submitted_for_review_at),
  CONSTRAINT fk_ai_detections_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_ai_detections_reviewer
    FOREIGN KEY (reviewed_by) REFERENCES users (id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_ai_detections_report
    FOREIGN KEY (report_id) REFERENCES road_reports (report_id)
    ON UPDATE CASCADE
    ON DELETE SET NULL,
  CONSTRAINT chk_ai_detection_counts
    CHECK (
      (detection_count IS NULL AND pothole_count IS NULL)
      OR
      (detection_count IS NOT NULL AND pothole_count IS NOT NULL
        AND pothole_count <= detection_count)
    ),
  CONSTRAINT chk_ai_detection_confidence
    CHECK (confidence_score IS NULL OR confidence_score BETWEEN 0 AND 1)
) ENGINE=InnoDB;
