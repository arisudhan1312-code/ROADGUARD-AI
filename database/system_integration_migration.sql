USE roadguard_ai;

-- Run after roadguard.sql and worker_tasks_migration.sql.
-- These tables extend the existing schema; no existing table is recreated.
-- IF NOT EXISTS does not alter a pre-existing table; verify its columns before deploying.
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
    ON DELETE RESTRICT,
  CONSTRAINT chk_rewards_source
    CHECK (
      (reward_type = 'Repair' AND task_id IS NOT NULL AND participant_id IS NULL)
      OR
      (reward_type = 'Volunteer' AND participant_id IS NOT NULL AND task_id IS NULL)
    )
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
