# ZoneWatch — Warehouse zone risk monitor

ZoneWatch is a runnable, end-to-end synthetic monitoring workflow for warehouse temperature, humidity, occupancy, and door events. It turns source events into traceable risk alerts, identifies overlapping inventory lots, and records inspections and remediation as explicit, append-only events.

The app is split into a browser frontend, a Python API and rules layer, and a SQLite database. It uses only the Python standard library. No external dataset, personal data, or external service is used.

## Run the application

Requirements: Python 3.10 or later. The application uses only the Python standard library; no `pip install` step is required.

From the repository root, start the quick starter dataset:

```bash
python3 -m backend.server
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). On first start, the app creates `data/warehouse_monitor.db`, applies `database/schema.sql`, and loads the deterministic starter dataset. Press `Ctrl+C` to stop the server.

### Run the full-scale dataset

The full dataset is intentionally **not committed to Git**. The 1-million-row `sensor_reading.csv` alone is larger than GitHub's 100 MB per-file limit, and generated data is excluded by `.gitignore`. Generate it locally after cloning the repository:

```bash
python3 scripts/generate_data.py \\
  --scale full \\
  --seed 20261022 \\
  --format csv \\
  --output data/generated/full
```

Validate the generated exports:

```bash
python3 scripts/validate_data.py data/generated/full
```

Then start the dashboard against a separate full-scale SQLite database:

```bash
python3 -m backend.server \\
  --scale full \\
  --database data/warehouse_monitor_full.db
```

The first full-scale generation and SQLite import take longer and require additional disk space. The importer reads CSV files in batches to avoid loading all one million readings into memory. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The API health endpoint at [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health) reports loaded table counts and foreign-key status.

`--scale` applies only when the target database is empty. If you need to rebuild a database, stop the server and use a new database path or remove the existing database file intentionally. The default command uses the smaller starter dataset for quick local development.

Optional arguments:

- `--host` (default `127.0.0.1`)
- `--port` (default `8000`)
- `--database` (SQLite file path)
- `--scale starter|full` (default `starter`)
- `--seed` (default `20261022`)
- `--empty` (initialize schema without loading seed data)

## Operator workflow

1. Review network coverage, risk zones, open alarms, and inventory impact on **Monitor**.
2. Select **Evaluate now** to run idempotent rules against the persisted source events.
3. Acknowledge a linked alarm, start or complete its inspection, then plan and complete its remediation. State changes are written to `audit_event` with actor and timestamp.
4. If an alarm has no inventory bridge, use **Link lots**. The system selects active lots whose zone assignment overlaps the alarm zone and event time, then appends the links and an audit record.
5. Review incompatible lot placement, missing alarm links, stale sensors, and ambiguous sensor mappings in **Exceptions**. Export the alarm snapshot, inventory impact, or event history as CSV.

## Rules and decisions

Thresholds come from the effective `StorageRequirement` row for each active lot:

- Temperature outside a lot's minimum or maximum raises a critical `TEMP_LOW` or `TEMP_HIGH` alarm.
- Humidity outside a lot's range raises a high `HUMIDITY_LOW` or `HUMIDITY_HIGH` alarm.
- An observed reading gap longer than 45 minutes raises a high `SENSOR_GAP` alarm. The 15-minute cadence is recorded explicitly; exactly 45 minutes is a no-alert boundary. Missing samples remain missing and outages are explicit `SensorOutage` records.
- A door-open event lasting at least 180 seconds raises a high `DOOR_OPEN_LONG` alarm.
- Occupancy at or above 95% raises a medium `OCCUPANCY_HIGH` alarm.
- A lot whose qualified temperature range does not overlap its zone class is flagged as high `STORAGE_MISMATCH`. A lot whose derived dwell time exceeds its product limit raises `DWELL_LIMIT`.
- Inventory impact is determined by lot-zone assignment and event-time overlap. `AlarmAffectedLot` records the linked lot and quantity. An injected alarm without links remains visible as a traceability exception until reconciled.
- Suspect and invalid readings do not drive threshold alarms. Every alarm has an inspection and remediation record, including deliberate missing-impact cases.

Rules use measurements, effective-dated assignments, product requirements, and event timing. Prolonged door cycles link to nearby temperature readings in the same zone. The separate `ground_truth.csv` describes expected anomaly decisions and is never read by the rules engine or returned by the operational API. Operational CSV/JSON tables contain no `issue_type` label columns.

## Data model

The primary entities are Warehouse, Zone, Sensor, SensorReading, DoorEvent, InventoryLot, StorageRequirement, Alarm, Inspection, and Remediation. Effective-dated bridge tables support sensor-zone, lot-zone, alarm-lot, and remediation-lot relationships. `AuditEvent` records workflow state changes without overwriting the event history. `SensorOutage` records gaps explicitly.

The full field-level types, optionality, enums, units, constraints, cardinalities, and time rules are in [docs/data-dictionary.md](docs/data-dictionary.md). The SQLite schema and foreign keys are in [database/schema.sql](database/schema.sql). `DoorCycle` is the parent record for paired open and closed/ajar state events.

ID patterns include `WH-001`, `ZONE-WH-001-001`, `SNS-ZONE-WH-001-001-TEMP-01`, `RDG-<sensor-id>-<UTC timestamp>`, and `LOT-2026-000001`. IDs are deterministic and immutable for a given seed and scale.

## Generate data and export files

The generator uses one seeded logic for starter and full scale. The default seed is `20261022`.

Generate all starter exports and run validation plus two-run reproducibility checks:

```bash
python3 scripts/generate_data.py \
  --scale starter \
  --seed 20261022 \
  --format all \
  --output data/demo \
  --check-reproducibility
```

This writes CSV files for each operational table, `dataset.json`, `seed.sql`, a separate `ground_truth.csv` and `ground_truth.json`, a source manifest, and a validation summary. The full dataset has 10 warehouses, 100 zones, 500 sensors, 1,000,000 readings, 10,000 lots, 2,500 door cycles, and 5,000 door events. It includes eight product profiles, inventory in all zone classes, repeated excursions and outages, exact-threshold cases, invalid readings, and correlated door/temperature events:

```bash
python3 scripts/generate_data.py \
  --scale full \
  --seed 20261022 \
  --format csv \
  --output data/generated/full \
  --check-reproducibility
```

Full-scale CSV is the practical bulk format. JSON and SQL exports are available through `--format json`, `--format sql`, or `--format all`; those formats contain a full copy of every requested row and can take substantially more disk space. Generated full-scale output is ignored by Git so source code remains suitable for a public repository.

Validate an existing generated directory:

```bash
python3 scripts/validate_data.py data/demo
```

The generator and validator check row counts, primary-key uniqueness (including composite bridge keys), foreign-key integrity, schema enums and date constraints, ambient/hot inventory coverage, door-event variety, lot dwell arithmetic, impact quantity reconciliation, alarm-to-inspection/remediation coverage, lifecycle transition chains, repeated anomaly and boundary coverage, label separation, and deterministic checksums.

## Tests

Run the complete test suite:

```bash
python3 -m unittest discover -s tests -v
```

Tests cover generator counts and reproducibility, source-to-alarm evaluation, idempotency, anomaly-label separation, API behavior, foreign keys, exception reconciliation, and the inspection-to-remediation lifecycle.

## Layout

```text
backend/       HTTP API, rules, database setup, shared seeded data model
frontend/      Browser dashboard and operator workflow
database/      SQLite DDL and relational constraints
scripts/       Data export, validation, and reproducibility commands
tests/         Unit and API integration tests
docs/          Data dictionary and relationship documentation
data/demo/     Seeded starter CSV/JSON/SQL exports and validation manifest
```


## GitHub submission notes

- Commit the source code, schema, tests, documentation, and the small starter dataset in `data/demo/`.
- Do not commit SQLite database files, virtual environments, secrets, or generated full-scale exports. They are excluded by `.gitignore`.
- A fresh clone can run the starter app immediately. To run full scale, generate `data/generated/full/` locally using the command above before starting the server with `--scale full`.
- Before publishing, run the test suite and inspect `git status` to ensure no local database or large generated file is staged.
- Add a repository license only if you have chosen the terms under which others may use and redistribute your code. No license is implied by this project.


## Synthetic provenance

All records are generated locally by `backend/seed_data.py` with a fixed seed and fixed source-data as-of timestamp. External reference datasets were not used, so there are no external licenses, retrieval rights, or attribution requirements.
