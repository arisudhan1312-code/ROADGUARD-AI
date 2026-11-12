CREATE DATABASE IF NOT EXISTS roadguard_ai
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE roadguard_ai;

-- Additive bootstrap schema for the existing roadguard_ai database.
-- CREATE TABLE IF NOT EXISTS preserves any tables and rows already present.
CREATE TABLE IF NOT EXISTS users (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  full_name VARCHAR(100) NOT NULL,
  email VARCHAR(254) NOT NULL,
  mobile VARCHAR(20) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('citizen', 'driver', 'worker', 'admin') NOT NULL DEFAULT 'citizen',
  status ENUM('active', 'disabled') NOT NULL DEFAULT 'active',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS road_reports (
  report_id VARCHAR(19) NOT NULL,
  user_id BIGINT UNSIGNED NOT NULL,
  hazard_category ENUM(
    'Pothole',
    'Damaged Road',
    'Broken Streetlight',
    'Waterlogging',
    'Road Obstruction',
    'Other'
  ) NOT NULL,
  problem_description VARCHAR(2000) NOT NULL,
  location_name VARCHAR(255) NOT NULL,
  latitude DECIMAL(10, 7) NULL,
  longitude DECIMAL(11, 7) NULL,
  image_path VARCHAR(500) NULL,
  report_datetime TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  priority ENUM('Normal', 'Low', 'Medium', 'High') NOT NULL DEFAULT 'Normal',
  calculated_priority ENUM('Critical', 'High', 'Medium', 'Low') NULL,
  hazard_priority ENUM('Critical', 'High', 'Medium', 'Low') NULL,
  priority_score TINYINT UNSIGNED NULL,
  priority_report_count INT UNSIGNED NULL,
  traffic_importance ENUM('Unknown', 'Low', 'Medium', 'High')
    NOT NULL DEFAULT 'Unknown',
  priority_source ENUM('Rule-Based', 'Admin Override')
    NOT NULL DEFAULT 'Rule-Based',
  status ENUM(
    'Pending Review',
    'Approved',
    'Rejected',
    'In Progress',
    'Completed'
  ) NOT NULL DEFAULT 'Pending Review',
  PRIMARY KEY (report_id),
  KEY idx_road_reports_user_date (user_id, report_datetime),
  KEY idx_road_reports_status (status),
  KEY idx_road_reports_hazard_priority (hazard_priority, priority_score),
  CONSTRAINT fk_road_reports_user
    FOREIGN KEY (user_id) REFERENCES users (id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT chk_road_reports_coordinates
    CHECK (
      (latitude IS NULL AND longitude IS NULL)
      OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)
    )
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS hazards (
  hazard_id VARCHAR(19) NOT NULL,
  report_id VARCHAR(19) NOT NULL,
  category ENUM(
    'Pothole',
    'Damaged Road',
    'Broken Streetlight',
    'Waterlogging',
    'Road Obstruction',
    'Other'
  ) NOT NULL,
  location_name VARCHAR(255) NOT NULL,
  latitude DECIMAL(10, 7) NOT NULL,
  longitude DECIMAL(11, 7) NOT NULL,
  severity ENUM('High', 'Medium', 'Low') NOT NULL,
  status ENUM('Active', 'Assigned', 'Resolved') NOT NULL DEFAULT 'Active',
  verification_status ENUM('Pending', 'Verified', 'Rejected')
    NOT NULL DEFAULT 'Pending',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (hazard_id),
  UNIQUE KEY uq_hazards_report (report_id),
  KEY idx_hazards_driver_lookup (verification_status, status, latitude, longitude),
  CONSTRAINT fk_hazards_report
    FOREIGN KEY (report_id) REFERENCES road_reports (report_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT chk_hazards_coordinates
    CHECK (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)
) ENGINE=InnoDB;

-- Worker profiles are required by the worker APIs and task foreign keys.
CREATE TABLE IF NOT EXISTS workers (
  worker_id BIGINT UNSIGNED NOT NULL,
  skills JSON NOT NULL,
  availability ENUM('Available', 'On Task', 'Off Duty')
    NOT NULL DEFAULT 'Available',
  location_name VARCHAR(255) NULL,
  latitude DECIMAL(10, 7) NULL,
  longitude DECIMAL(11, 7) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (worker_id),
  CONSTRAINT fk_workers_user
    FOREIGN KEY (worker_id) REFERENCES users (id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT chk_workers_coordinates
    CHECK (
      (latitude IS NULL) = (longitude IS NULL)
      AND (
        latitude IS NULL
        OR (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)
      )
    )
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS repair_tasks (
  task_id VARCHAR(19) NOT NULL,
  hazard_id VARCHAR(19) NOT NULL,
  worker_id BIGINT UNSIGNED NOT NULL,
  assigned_by BIGINT UNSIGNED NOT NULL,
  priority ENUM('High', 'Medium', 'Low') NOT NULL,
  status ENUM(
    'Assigned',
    'Accepted',
    'Declined',
    'In Progress',
    'Awaiting Admin Verification',
    'Verified',
    'Rejected'
  ) NOT NULL DEFAULT 'Assigned',
  description VARCHAR(1000) NULL,
  assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (task_id),
  KEY idx_repair_tasks_hazard_status (hazard_id, status),
  KEY idx_repair_tasks_worker_status (worker_id, status),
  CONSTRAINT fk_repair_tasks_hazard
    FOREIGN KEY (hazard_id) REFERENCES hazards (hazard_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_repair_tasks_worker
    FOREIGN KEY (worker_id) REFERENCES workers (worker_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_repair_tasks_admin
    FOREIGN KEY (assigned_by) REFERENCES users (id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS repair_evidence (
  evidence_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  task_id VARCHAR(19) NOT NULL,
  worker_id BIGINT UNSIGNED NOT NULL,
  before_image_path VARCHAR(500) NOT NULL,
  after_image_path VARCHAR(500) NOT NULL,
  latitude DECIMAL(10, 7) NOT NULL,
  longitude DECIMAL(11, 7) NOT NULL,
  repair_description VARCHAR(2000) NOT NULL,
  completion_datetime TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  status ENUM(
    'Submitted',
    'Approved',
    'Rejected',
    'Additional Evidence Requested'
  ) NOT NULL DEFAULT 'Submitted',
  review_notes VARCHAR(1000) NULL,
  reviewed_by BIGINT UNSIGNED NULL,
  reviewed_at TIMESTAMP NULL,
  PRIMARY KEY (evidence_id),
  KEY idx_repair_evidence_status (status, completion_datetime),
  KEY idx_repair_evidence_task (task_id, evidence_id),
  CONSTRAINT fk_repair_evidence_task
    FOREIGN KEY (task_id) REFERENCES repair_tasks (task_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_repair_evidence_worker
    FOREIGN KEY (worker_id) REFERENCES workers (worker_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_repair_evidence_reviewer
    FOREIGN KEY (reviewed_by) REFERENCES users (id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT chk_repair_evidence_coordinates
    CHECK (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS volunteer_activities (
  activity_id VARCHAR(19) NOT NULL,
  name VARCHAR(80) NOT NULL,
  description VARCHAR(1000) NOT NULL,
  location_name VARCHAR(255) NOT NULL,
  activity_date DATE NOT NULL,
  required_volunteers SMALLINT UNSIGNED NOT NULL,
  volunteer_points SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  status ENUM('Open', 'Closed') NOT NULL DEFAULT 'Open',
  created_by BIGINT UNSIGNED NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (activity_id),
  KEY idx_volunteer_activities_date (activity_date, status),
  CONSTRAINT fk_volunteer_activities_admin
    FOREIGN KEY (created_by) REFERENCES users (id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS volunteer_participants (
  participant_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  activity_id VARCHAR(19) NOT NULL,
  worker_id BIGINT UNSIGNED NOT NULL,
  status ENUM('Joined', 'Submitted', 'Approved', 'Rejected')
    NOT NULL DEFAULT 'Joined',
  completion_details VARCHAR(2000) NULL,
  joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP NULL,
  reviewed_at TIMESTAMP NULL,
  reviewed_by BIGINT UNSIGNED NULL,
  review_notes VARCHAR(1000) NULL,
  PRIMARY KEY (participant_id),
  UNIQUE KEY uq_volunteer_participant (activity_id, worker_id),
  KEY idx_volunteer_participants_status (status, activity_id),
  CONSTRAINT fk_volunteer_participants_activity
    FOREIGN KEY (activity_id) REFERENCES volunteer_activities (activity_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_volunteer_participants_worker
    FOREIGN KEY (worker_id) REFERENCES workers (worker_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_volunteer_participants_reviewer
    FOREIGN KEY (reviewed_by) REFERENCES users (id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS rewards (
  reward_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  worker_id BIGINT UNSIGNED NOT NULL,
  reward_type ENUM('Repair', 'Volunteer') NOT NULL,
  task_id VARCHAR(19) NULL,
  participant_id BIGINT UNSIGNED NULL,
  points INT UNSIGNED NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (reward_id),
  UNIQUE KEY uq_rewards_task (task_id),
  UNIQUE KEY uq_rewards_participant (participant_id),
  KEY idx_rewards_worker_type (worker_id, reward_type),
  CONSTRAINT fk_rewards_worker
    FOREIGN KEY (worker_id) REFERENCES workers (worker_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_rewards_task
    FOREIGN KEY (task_id) REFERENCES repair_tasks (task_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT,
  CONSTRAINT fk_rewards_participant
    FOREIGN KEY (participant_id) REFERENCES volunteer_participants (participant_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT
  -- chk_rewards_source removed: MySQL 8.0 does not allow a column used in a FK
  -- referential action to also appear in a CHECK constraint (Error 3823).
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS notifications (
  notification_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  recipient_user_id BIGINT UNSIGNED NOT NULL,
  notification_type VARCHAR(50) NOT NULL,
  title VARCHAR(120) NOT NULL,
  message VARCHAR(1000) NOT NULL,
  related_entity_type VARCHAR(40) NULL,
  related_entity_id VARCHAR(64) NULL,
  event_key VARCHAR(160) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  read_at TIMESTAMP NULL,
  PRIMARY KEY (notification_id),
  UNIQUE KEY uq_notifications_event_key (event_key),
  KEY idx_notifications_recipient_read (recipient_user_id, read_at, created_at),
  CONSTRAINT fk_notifications_recipient
    FOREIGN KEY (recipient_user_id) REFERENCES users (id)
    ON UPDATE CASCADE
    ON DELETE CASCADE
) ENGINE=InnoDB;

-- Existing AI review APIs require this additional table.
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

-- Create empty profiles only for already registered worker accounts.
INSERT IGNORE INTO workers (worker_id, skills, availability)
SELECT id, JSON_ARRAY(), 'Available'
FROM users
WHERE role = 'worker';

-- -----------------------------------------------------------------------
-- Bootstrap admin account
-- Email   : ariharasudhan1312@gmail.com
-- Password: arisudhan2008  (Werkzeug pbkdf2:sha256 hex format, 260000 iterations)
-- -----------------------------------------------------------------------
INSERT IGNORE INTO users (full_name, email, mobile, password_hash, role, status)
VALUES (
  'Admin',
  'ariharasudhan1312@gmail.com',
  '0000000000',
  'pbkdf2:sha256:260000$kYY1O6zu7fJ2YtFP$9f4128f9b772781cdca7c75c27ea47582ead82f691ec7a01587b42ed812789ad',
  'admin',
  'active'
);
