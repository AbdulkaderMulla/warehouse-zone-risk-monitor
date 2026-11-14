# Warehouse risk monitor data dictionary

All timestamps are ISO 8601 UTC values ending in `Z`. Temperatures use °C, relative humidity uses `%RH`, occupancy uses percent of zone capacity, dwell uses hours, durations use seconds, and inventory quantities use whole units. `PK` marks a primary key and `FK` marks an enforced SQLite foreign key. Operational tables contain no ground-truth label fields; known synthetic cases are in the separate `ground_truth.csv`/`.json` files and the separate `ground_truth` table in `seed.sql`.

## Operational entities

### Warehouse

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `warehouse_id` | TEXT | Yes | PK; immutable `WH-001` format. |
| `name` | TEXT | Yes | Non-empty display name. |
| `region` | TEXT | Yes | Synthetic region label. |
| `lifecycle_state` | TEXT | Yes | `active`, `maintenance`, `retired`. |
| `created_at` | timestamp | Yes | UTC creation time. |

### Zone

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `zone_id` | TEXT | Yes | PK; immutable `ZONE-<warehouse-id>-001` format. |
| `warehouse_id` | TEXT FK | Yes | References `warehouse`; many zones per warehouse. |
| `name` | TEXT | Yes | Non-empty; unique within warehouse. |
| `zone_type` | TEXT | Yes | `ambient`, `cold`, `frozen`, `hot`. |
| `capacity_pallets` | INTEGER | Yes | Pallets; greater than zero. |
| `lifecycle_state` | TEXT | Yes | `active`, `maintenance`, `retired`. |
| `effective_from` | timestamp | Yes | UTC start of zone validity. |
| `effective_to` | timestamp | No | UTC; later than `effective_from` when present. |

### Sensor

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `sensor_id` | TEXT | Yes | PK; `SNS-<zone-id>-<type>-01`; immutable. |
| `sensor_type` | TEXT | Yes | `TEMP`, `HUMIDITY`, `OCCUPANCY`. |
| `unit` | TEXT | Yes | `C`, `%RH`, or `%`, consistent with sensor type. |
| `lifecycle_state` | TEXT | Yes | `active`, `maintenance`, `retired`. |
| `calibrated_at` | timestamp | Yes | UTC calibration reference date. |
| `drift_per_day` | REAL | Yes | C/day or percentage points/day, according to type. |
| `created_at` | timestamp | Yes | UTC creation time. |

### SensorZoneAssignment (effective-dated bridge)

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `assignment_id` | TEXT | Yes | PK; immutable assignment record ID. |
| `sensor_id` | TEXT FK | Yes | References `sensor`. |
| `zone_id` | TEXT FK | Yes | References `zone`. |
| `effective_from` | timestamp | Yes | UTC start; inclusive for current mapping. |
| `effective_to` | timestamp | No | UTC; exclusive end and later than start. |
| `rationale` | TEXT | No | Mapping justification; missing rationale on overlapping active mappings is an exception. |

### SensorReading

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `reading_id` | TEXT | Yes | PK; `RDG-<sensor-id>-<UTC timestamp>`; immutable. |
| `sensor_id` | TEXT FK | Yes | References `sensor`. |
| `captured_at` | timestamp | Yes | UTC; unique with `sensor_id`; expected cadence is 900 seconds. |
| `value` | REAL | Yes | In the declared unit of its sensor. |
| `quality_flag` | TEXT | Yes | `good`, `suspect`, `invalid`; only `good` drives threshold alarms. |
| `door_cycle_id` | TEXT FK | No | References `door_cycle`; links a temperature sample to the open-door cycle that overlaps it. |

### DoorCycle

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `door_cycle_id` | TEXT | Yes | PK; immutable `DCL-2026-000001` format. |
| `zone_id` | TEXT FK | Yes | References `zone`. |
| `opened_at` | timestamp | Yes | UTC open time. |
| `closed_at` | timestamp | Yes | UTC; not earlier than `opened_at`. |
| `duration_seconds` | INTEGER | Yes | Seconds; zero or greater and derived from the timestamps. |
| `closure_status` | TEXT | Yes | `closed`, `ajar`. |

### DoorEvent

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `door_event_id` | TEXT | Yes | PK; immutable `DOR-2026-000001` format. |
| `door_cycle_id` | TEXT FK | Yes | References `door_cycle`; two state events per generated cycle. |
| `zone_id` | TEXT FK | Yes | References `zone`; matches the parent cycle zone. |
| `occurred_at` | timestamp | Yes | UTC time of the state event. |
| `event_type` | TEXT | Yes | `open`, `closed`, `ajar`. |
| `duration_seconds` | INTEGER | Yes | Seconds; non-negative; duration is carried by the open event, terminal state events use zero. |

### StorageRequirement

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `requirement_id` | TEXT | Yes | PK; stable requirement profile ID. |
| `product_code` | TEXT | Yes | Unique product key referenced by lots. |
| `product_name` | TEXT | Yes | Non-empty synthetic product name. |
| `min_temp_c` | REAL | Yes | °C; less than `max_temp_c`. |
| `max_temp_c` | REAL | Yes | °C; greater than `min_temp_c`. |
| `min_humidity_pct` | REAL | Yes | `%RH`; less than `max_humidity_pct`. |
| `max_humidity_pct` | REAL | Yes | `%RH`; greater than `min_humidity_pct`. |
| `max_dwell_hours` | REAL | Yes | Hours; greater than zero. |
| `effective_from` | timestamp | Yes | UTC start of requirement. |
| `effective_to` | timestamp | No | UTC; later than start when present. |

### InventoryLot

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `lot_id` | TEXT | Yes | PK; immutable `LOT-2026-000001` format. |
| `product_code` | TEXT FK | Yes | References unique `storage_requirement.product_code`. |
| `quantity_units` | INTEGER | Yes | Whole units; greater than zero. |
| `received_at` | timestamp | Yes | UTC receipt time. |
| `expires_at` | timestamp | Yes | UTC; later than receipt. |
| `lifecycle_state` | TEXT | Yes | `received`, `stored`, `quarantined`, `released`, `shipped`, `consumed`, `expired`. |

### LotZoneAssignment (effective-dated bridge)

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `lot_zone_assignment_id` | TEXT | Yes | PK; immutable placement record ID. |
| `lot_id` | TEXT FK | Yes | References `inventory_lot`. |
| `zone_id` | TEXT FK | Yes | References `zone`. |
| `effective_from` | timestamp | Yes | UTC placement start; inclusive. |
| `effective_to` | timestamp | No | UTC exclusive end; later than start. |
| `dwell_hours` | REAL | Yes | Non-negative; derived to `effective_to` or dataset as-of time. |

### SensorOutage

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `outage_id` | TEXT | Yes | PK; immutable `OUT-2026-000001` format. |
| `sensor_id` | TEXT FK | Yes | References `sensor`. |
| `started_at` | timestamp | Yes | UTC time of last valid observation bounding the gap. |
| `ended_at` | timestamp | Yes | UTC time of next valid observation; later than start. |
| `expected_interval_seconds` | INTEGER | Yes | Seconds; greater than zero (900 seconds in generated data). |
| `reason` | TEXT | Yes | Synthetic telemetry-gap description. |

### Alarm

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `alarm_id` | TEXT | Yes | PK; immutable `ALM-2026-000001` format. |
| `zone_id` | TEXT FK | Yes | References `zone`. |
| `sensor_id` | TEXT FK | No | References source `sensor`; null for door, dwell, and placement rules. |
| `alarm_code` | TEXT | Yes | Examples: `TEMP_HIGH`, `SENSOR_GAP`, `DOOR_OPEN_LONG`, `STORAGE_MISMATCH`, `DWELL_LIMIT`. |
| `severity` | TEXT | Yes | `low`, `medium`, `high`, `critical`. |
| `triggered_at` | timestamp | Yes | UTC decision time. |
| `status` | TEXT | Yes | `open`, `acknowledged`, `inspecting`, `remediating`, `resolved`, `false_positive`. |
| `summary` | TEXT | Yes | Human-readable explanation of the rule outcome. |
| `source_key` | TEXT | Yes | Unique idempotency key derived from the source event. |
| `resolved_at` | timestamp | No | UTC; not earlier than trigger time. |

### AlarmAffectedLot (bridge)

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `alarm_id` | TEXT FK | Yes | References `alarm`; composite PK with `lot_id`. |
| `lot_id` | TEXT FK | Yes | References `inventory_lot`; composite PK with `alarm_id`. |
| `impact_quantity_units` | INTEGER | Yes | Non-negative whole units; equals the linked lot quantity. |

### Inspection

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `inspection_id` | TEXT | Yes | PK; immutable `INSP-2026-000001` format. |
| `alarm_id` | TEXT FK | Yes | References `alarm`; repeated inspection attempts are allowed. |
| `zone_id` | TEXT FK | Yes | References `zone`. |
| `assigned_role` | TEXT | Yes | `plant_operator`, `quality_engineer`, `maintenance_engineer`, `production_planner`, `ot_it_administrator`. |
| `status` | TEXT | Yes | `planned`, `in_progress`, `completed`, `cancelled`. |
| `created_at` | timestamp | Yes | UTC creation time. |
| `completed_at` | timestamp | No | UTC; not earlier than creation. |
| `findings` | TEXT | No | Inspection notes. |

### Remediation

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `remediation_id` | TEXT | Yes | PK; immutable `REM-2026-000001` format. |
| `inspection_id` | TEXT FK | Yes | References `inspection`. |
| `action_type` | TEXT | Yes | `repair_sensor`, `repair_hvac`, `adjust_setpoint`, `quarantine_lot`, `move_lot`, `verify_calibration`, `other`. |
| `status` | TEXT | Yes | `planned`, `in_progress`, `completed`, `cancelled`. |
| `inventory_impact_quantity_units` | INTEGER | Yes | Non-negative whole units; reconciles to remediation-lot links. |
| `created_at` | timestamp | Yes | UTC creation time. |
| `completed_at` | timestamp | No | UTC; not earlier than creation. |
| `notes` | TEXT | No | Action notes. |

### RemediationAffectedLot (bridge)

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `remediation_id` | TEXT FK | Yes | References `remediation`; composite PK with `lot_id`. |
| `lot_id` | TEXT FK | Yes | References `inventory_lot`; composite PK with `remediation_id`. |
| `impacted_quantity_units` | INTEGER | Yes | Positive whole units; reconciles to remediation total. |

### AuditEvent

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `audit_event_id` | TEXT | Yes | PK; immutable append-only event ID. |
| `entity_type` | TEXT | Yes | Operational entity name. |
| `entity_id` | TEXT | Yes | Identifier of the changed entity; generic reference is retained for audit flexibility. |
| `action` | TEXT | Yes | `created`, `state_changed`, `completed`, `impact_links_added`, or workflow action. |
| `old_state` | TEXT | No | Prior lifecycle state for state changes. |
| `new_state` | TEXT | No | New lifecycle state; transitions chain from the previous state. |
| `actor` | TEXT | Yes | Synthetic role or `risk_engine`. |
| `occurred_at` | timestamp | Yes | UTC event time. |
| `details_json` | TEXT | Yes | JSON object; defaults to `{}`. |

## Ground truth (separate evaluation data)

| Field | Type | Required | Allowed values, uniqueness, and validation |
|---|---|---:|---|
| `truth_id` | TEXT | Yes | PK; unique `GT-000001` label ID. |
| `scenario_id` | TEXT | Yes | Stable scenario identifier. |
| `entity_type` | TEXT | Yes | Operational table name. |
| `entity_id` | TEXT | Yes | Primary key of the operational row under evaluation. |
| `issue_type` | TEXT | Yes | Known anomaly label; only appears in ground truth. |
| `expected_decision` | TEXT | Yes | Documented expected system/operator outcome. |
| `expected_severity` | TEXT | No | `low`, `medium`, `high`, `critical`, or null for no-alert boundaries. |
| `rationale` | TEXT | Yes | Rule-based reason for the expected label. |

## Relationships, lifecycle, and decision boundaries

- `Warehouse 1:N Zone`; `Zone 1:N SensorZoneAssignment`, `DoorCycle`, and `Alarm`.
- `Sensor 1:N SensorZoneAssignment` and `SensorReading`; sensor-to-zone is M:N over time through `SensorZoneAssignment`.
- `Zone 1:N DoorCycle`; each cycle has two or more `DoorEvent` state records. A cycle can link zero or more readings; readings may have no door cycle.
- `StorageRequirement 1:N InventoryLot`; lots may be received without a placement, then occupy zones through effective-dated `LotZoneAssignment` (lot-zone M:N over time).
- `Alarm M:N InventoryLot` through `AlarmAffectedLot`; `Remediation M:N InventoryLot` through `RemediationAffectedLot`.
- `Alarm 1:N Inspection`; `Inspection 1:N Remediation`; each alarm in generated data has at least one inspection and remediation.
- Assignment periods are half-open `[effective_from, effective_to)`. An interval overlaps a lot placement when `assignment.start < event.end` and `assignment.end > event.start`. This avoids double-counting inventory that exits exactly at an event boundary.
- Temperature and humidity alert when a good-quality reading is strictly outside the active requirement. Occupancy alerts at `>=95%`; prolonged door events alert at `>=180 seconds`; sensor gaps alert only at `>45 minutes`.
- Lot lifecycle values include receipt, storage, quarantine, release, shipment, consumption, and expiry. Inspection, remediation, and alarm changes are retained as audit events rather than replacing historical records.
- The generator emits normal records first, then deterministic labelled cases. `ground_truth` is not a feature in the app API, operational CSV files, or `dataset.json`.
