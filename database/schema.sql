PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS warehouse (
  warehouse_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  region TEXT NOT NULL,
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active','maintenance','retired')),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zone (
  zone_id TEXT PRIMARY KEY,
  warehouse_id TEXT NOT NULL REFERENCES warehouse(warehouse_id),
  name TEXT NOT NULL,
  zone_type TEXT NOT NULL CHECK (zone_type IN ('ambient','cold','frozen','hot')),
  capacity_pallets INTEGER NOT NULL CHECK (capacity_pallets > 0),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active','maintenance','retired')),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  UNIQUE (warehouse_id, name),
  CHECK (effective_to IS NULL OR effective_to > effective_from)
);

CREATE TABLE IF NOT EXISTS sensor (
  sensor_id TEXT PRIMARY KEY,
  sensor_type TEXT NOT NULL CHECK (sensor_type IN ('TEMP','HUMIDITY','OCCUPANCY')),
  unit TEXT NOT NULL CHECK (unit IN ('C','%RH','%')),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active','maintenance','retired')),
  calibrated_at TEXT NOT NULL,
  drift_per_day REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sensor_zone_assignment (
  assignment_id TEXT PRIMARY KEY,
  sensor_id TEXT NOT NULL REFERENCES sensor(sensor_id),
  zone_id TEXT NOT NULL REFERENCES zone(zone_id),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  rationale TEXT,
  CHECK (effective_to IS NULL OR effective_to > effective_from)
);

CREATE TABLE IF NOT EXISTS sensor_reading (
  reading_id TEXT PRIMARY KEY,
  sensor_id TEXT NOT NULL REFERENCES sensor(sensor_id),
  captured_at TEXT NOT NULL,
  value REAL NOT NULL,
  quality_flag TEXT NOT NULL CHECK (quality_flag IN ('good','suspect','invalid')),
  door_cycle_id TEXT REFERENCES door_cycle(door_cycle_id),
  UNIQUE (sensor_id, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_reading_sensor_time ON sensor_reading(sensor_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS door_cycle (
  door_cycle_id TEXT PRIMARY KEY,
  zone_id TEXT NOT NULL REFERENCES zone(zone_id),
  opened_at TEXT NOT NULL,
  closed_at TEXT NOT NULL,
  duration_seconds INTEGER NOT NULL CHECK (duration_seconds >= 0),
  closure_status TEXT NOT NULL CHECK (closure_status IN ('closed','ajar')),
  CHECK (closed_at >= opened_at)
);

CREATE TABLE IF NOT EXISTS door_event (
  door_event_id TEXT PRIMARY KEY,
  door_cycle_id TEXT NOT NULL REFERENCES door_cycle(door_cycle_id),
  zone_id TEXT NOT NULL REFERENCES zone(zone_id),
  occurred_at TEXT NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type IN ('open','closed','ajar')),
  duration_seconds INTEGER NOT NULL CHECK (duration_seconds >= 0)
);
CREATE INDEX IF NOT EXISTS idx_door_zone_time ON door_event(zone_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS storage_requirement (
  requirement_id TEXT PRIMARY KEY,
  product_code TEXT NOT NULL UNIQUE,
  product_name TEXT NOT NULL,
  min_temp_c REAL NOT NULL,
  max_temp_c REAL NOT NULL,
  min_humidity_pct REAL NOT NULL,
  max_humidity_pct REAL NOT NULL,
  max_dwell_hours REAL NOT NULL CHECK (max_dwell_hours > 0),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  CHECK (min_temp_c < max_temp_c),
  CHECK (min_humidity_pct < max_humidity_pct),
  CHECK (effective_to IS NULL OR effective_to > effective_from)
);

CREATE TABLE IF NOT EXISTS inventory_lot (
  lot_id TEXT PRIMARY KEY,
  product_code TEXT NOT NULL REFERENCES storage_requirement(product_code),
  quantity_units INTEGER NOT NULL CHECK (quantity_units > 0),
  received_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('received','stored','quarantined','released','shipped','consumed','expired')),
  CHECK (expires_at > received_at)
);
CREATE INDEX IF NOT EXISTS idx_lot_product_state ON inventory_lot(product_code, lifecycle_state);

CREATE TABLE IF NOT EXISTS lot_zone_assignment (
  lot_zone_assignment_id TEXT PRIMARY KEY,
  lot_id TEXT NOT NULL REFERENCES inventory_lot(lot_id),
  zone_id TEXT NOT NULL REFERENCES zone(zone_id),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  dwell_hours REAL NOT NULL CHECK (dwell_hours >= 0),
  CHECK (effective_to IS NULL OR effective_to > effective_from)
);
CREATE INDEX IF NOT EXISTS idx_lot_zone_period ON lot_zone_assignment(zone_id, effective_from, effective_to);

CREATE TABLE IF NOT EXISTS alarm (
  alarm_id TEXT PRIMARY KEY,
  zone_id TEXT NOT NULL REFERENCES zone(zone_id),
  sensor_id TEXT REFERENCES sensor(sensor_id),
  alarm_code TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('low','medium','high','critical')),
  triggered_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open','acknowledged','inspecting','remediating','resolved','false_positive')),
  summary TEXT NOT NULL,
  source_key TEXT NOT NULL UNIQUE,
  resolved_at TEXT,
  CHECK (resolved_at IS NULL OR resolved_at >= triggered_at)
);
CREATE INDEX IF NOT EXISTS idx_alarm_status_time ON alarm(status, triggered_at DESC);

CREATE TABLE IF NOT EXISTS alarm_affected_lot (
  alarm_id TEXT NOT NULL REFERENCES alarm(alarm_id),
  lot_id TEXT NOT NULL REFERENCES inventory_lot(lot_id),
  impact_quantity_units INTEGER NOT NULL CHECK (impact_quantity_units >= 0),
  PRIMARY KEY (alarm_id, lot_id)
);

CREATE TABLE IF NOT EXISTS inspection (
  inspection_id TEXT PRIMARY KEY,
  alarm_id TEXT NOT NULL REFERENCES alarm(alarm_id),
  zone_id TEXT NOT NULL REFERENCES zone(zone_id),
  assigned_role TEXT NOT NULL CHECK (assigned_role IN ('plant_operator','quality_engineer','maintenance_engineer','production_planner','ot_it_administrator')),
  status TEXT NOT NULL CHECK (status IN ('planned','in_progress','completed','cancelled')),
  created_at TEXT NOT NULL,
  completed_at TEXT,
  findings TEXT,
  CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE TABLE IF NOT EXISTS remediation (
  remediation_id TEXT PRIMARY KEY,
  inspection_id TEXT NOT NULL REFERENCES inspection(inspection_id),
  action_type TEXT NOT NULL CHECK (action_type IN ('repair_sensor','repair_hvac','adjust_setpoint','quarantine_lot','move_lot','verify_calibration','other')),
  status TEXT NOT NULL CHECK (status IN ('planned','in_progress','completed','cancelled')),
  inventory_impact_quantity_units INTEGER NOT NULL DEFAULT 0 CHECK (inventory_impact_quantity_units >= 0),
  created_at TEXT NOT NULL,
  completed_at TEXT,
  notes TEXT,
  CHECK (completed_at IS NULL OR completed_at >= created_at)
);

CREATE TABLE IF NOT EXISTS remediation_affected_lot (
  remediation_id TEXT NOT NULL REFERENCES remediation(remediation_id),
  lot_id TEXT NOT NULL REFERENCES inventory_lot(lot_id),
  impacted_quantity_units INTEGER NOT NULL CHECK (impacted_quantity_units > 0),
  PRIMARY KEY (remediation_id, lot_id)
);

CREATE TABLE IF NOT EXISTS sensor_outage (
  outage_id TEXT PRIMARY KEY,
  sensor_id TEXT NOT NULL REFERENCES sensor(sensor_id),
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL,
  expected_interval_seconds INTEGER NOT NULL CHECK (expected_interval_seconds > 0),
  reason TEXT NOT NULL,
  CHECK (ended_at > started_at)
);

CREATE TABLE IF NOT EXISTS audit_event (
  audit_event_id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  action TEXT NOT NULL,
  old_state TEXT,
  new_state TEXT,
  actor TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_event(entity_type, entity_id, occurred_at);

CREATE TABLE IF NOT EXISTS ground_truth (
  truth_id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  issue_type TEXT NOT NULL,
  expected_decision TEXT NOT NULL,
  expected_severity TEXT,
  rationale TEXT NOT NULL
);
