USE roadguard_ai;

-- This migration is additive and safe to rerun. It creates only the two
-- worker/task tables missing from the checked-in base schema.
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

-- Existing worker accounts receive an empty skills/location profile only when
-- they do not already have one. Admins can add skills and location later.
INSERT IGNORE INTO workers (worker_id, skills, availability)
SELECT id, JSON_ARRAY(), 'Available'
FROM users
WHERE role = 'worker';
