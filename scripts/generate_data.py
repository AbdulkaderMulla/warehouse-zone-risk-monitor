#!/usr/bin/env python3
"""Deterministic warehouse-monitoring dataset generator (stdlib only)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.seed_data import Dataset, build_dataset, TABLE_COLUMNS


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def write_csv(dataset: Dataset, out: Path) -> dict[str, dict[str, object]]:
    out.mkdir(parents=True, exist_ok=True)
    stats: dict[str, dict[str, object]] = {}
    for table in dataset.table_names:
        path = out / f"{table}.csv"
        digest = hashlib.sha256()
        count = 0
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=TABLE_COLUMNS[table], lineterminator="\n")
            writer.writeheader()
            for row in dataset.rows(table):
                writer.writerow(row)
                digest.update((canonical(row) + "\n").encode())
                count += 1
        stats[table] = {"rows": count, "sha256": digest.hexdigest(), "file": path.name}
    return stats


def write_json(dataset: Dataset, out: Path) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "dataset.json"
    digest = hashlib.sha256()
    with path.open("w", encoding="utf-8") as stream:
        stream.write("{\n")
        for index, table in enumerate(dataset.table_names):
            if index:
                stream.write(",\n")
            stream.write(json.dumps(table) + ": [\n")
            first = True
            for row in dataset.rows(table):
                if not first:
                    stream.write(",\n")
                line = canonical(row)
                stream.write(line)
                digest.update((table + ":" + line + "\n").encode())
                first = False
            stream.write("\n]")
        stream.write("\n}\n")
    return {"file": path.name, "sha256": digest.hexdigest()}


def sql_value(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def write_sql(dataset: Dataset, out: Path) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "seed.sql"
    digest = hashlib.sha256()
    schema = (ROOT / "database" / "schema.sql").read_text(encoding="utf-8")
    with path.open("w", encoding="utf-8") as stream:
        stream.write(schema + "\nBEGIN TRANSACTION;\n")
        for table in dataset.table_names:
            columns = TABLE_COLUMNS[table]
            for row in dataset.rows(table):
                line = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(sql_value(row[c]) for c in columns)});"
                stream.write(line + "\n")
                digest.update((line + "\n").encode())
        for row in dataset.truth_rows:
            columns = dataset.truth_columns
            line = f"INSERT INTO ground_truth ({', '.join(columns)}) VALUES ({', '.join(sql_value(row[c]) for c in columns)});"
            stream.write(line + "\n")
            digest.update((line + "\n").encode())
        stream.write("COMMIT;\n")
    return {"file": path.name, "sha256": digest.hexdigest()}


def checksum_dataset(dataset: Dataset) -> dict[str, dict[str, object]]:
    result = {}
    for table in dataset.table_names:
        digest, count = hashlib.sha256(), 0
        for row in dataset.rows(table):
            digest.update((canonical(row) + "\n").encode())
            count += 1
        result[table] = {"rows": count, "sha256": digest.hexdigest()}
    truth_digest = hashlib.sha256()
    for row in dataset.truth_rows:
        truth_digest.update((canonical(row) + "\n").encode())
    result["ground_truth"] = {"rows": len(dataset.truth_rows), "sha256": truth_digest.hexdigest()}
    return result


def run_validation(csv_dir: Path, truth_rows: list[dict[str, object]], scale: str, expected_counts: dict[str, int]) -> dict[str, object]:
    temp_fd, temp_name = tempfile.mkstemp(prefix="zonewatch-validation-", suffix=".sqlite")
    os.close(temp_fd)
    connection = sqlite3.connect(temp_name)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript((ROOT / "database" / "schema.sql").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    errors: list[str] = []
    for table in TABLE_COLUMNS:
        path = csv_dir / f"{table}.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            columns = TABLE_COLUMNS[table]
            placeholders = ",".join("?" for _ in columns)
            sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
            rows = []
            for raw in reader:
                vals = []
                for col in columns:
                    value = raw[col]
                    if value == "":
                        value = None
                    if col in {"quantity_units", "capacity_pallets", "duration_seconds", "expected_interval_seconds", "impact_quantity_units", "impacted_quantity_units", "inventory_impact_quantity_units"} and value is not None:
                        value = int(value)
                    elif col in {"value", "drift_per_day", "min_temp_c", "max_temp_c", "min_humidity_pct", "max_humidity_pct", "max_dwell_hours"} and value is not None:
                        value = float(value)
                    vals.append(value)
                rows.append(tuple(vals))
                if len(rows) == 5000:
                    connection.executemany(sql, rows)
                    rows = []
            if rows:
                connection.executemany(sql, rows)
            counts[table] = reader.line_num - 1
    fk_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        errors.append(f"Foreign-key violations: {len(fk_errors)}")
    bad_alarm_quantities = connection.execute("""SELECT COUNT(*) FROM alarm_affected_lot a
        JOIN inventory_lot l ON l.lot_id=a.lot_id WHERE a.impact_quantity_units<>l.quantity_units""").fetchone()[0]
    if bad_alarm_quantities:
        errors.append(f"Alarm impact quantities fail lot reconciliation on {bad_alarm_quantities} links")
    bad_remediation_totals = connection.execute("""SELECT COUNT(*) FROM remediation r LEFT JOIN
        (SELECT remediation_id,SUM(impacted_quantity_units) AS units FROM remediation_affected_lot GROUP BY remediation_id) x
        ON x.remediation_id=r.remediation_id WHERE r.inventory_impact_quantity_units<>COALESCE(x.units,0)""").fetchone()[0]
    if bad_remediation_totals:
        errors.append(f"Remediation totals fail inventory reconciliation on {bad_remediation_totals} records")
    bad_dwell = connection.execute("""SELECT COUNT(*) FROM lot_zone_assignment WHERE ABS(dwell_hours-
        ((julianday(COALESCE(effective_to,'2026-10-22T23:45:00Z'))-julianday(effective_from))*24))>0.03""").fetchone()[0]
    if bad_dwell:
        errors.append(f"Derived lot dwell time is inconsistent on {bad_dwell} assignments")
    bad_cycle_duration = connection.execute("""SELECT COUNT(*) FROM door_cycle WHERE ABS(duration_seconds-
        ((julianday(closed_at)-julianday(opened_at))*86400))>1""").fetchone()[0]
    if bad_cycle_duration:
        errors.append(f"Door cycle duration is inconsistent on {bad_cycle_duration} cycles")
    bad_door_pairs = connection.execute("""SELECT COUNT(*) FROM door_cycle c WHERE
        (SELECT COUNT(*) FROM door_event e WHERE e.door_cycle_id=c.door_cycle_id)<>2 OR
        NOT EXISTS (SELECT 1 FROM door_event e WHERE e.door_cycle_id=c.door_cycle_id AND e.event_type='open' AND e.occurred_at=c.opened_at AND e.duration_seconds=c.duration_seconds) OR
        NOT EXISTS (SELECT 1 FROM door_event e WHERE e.door_cycle_id=c.door_cycle_id AND e.event_type=c.closure_status AND e.occurred_at=c.closed_at AND e.duration_seconds=0)""").fetchone()[0]
    if bad_door_pairs:
        errors.append(f"Door cycle event pairs fail reconciliation on {bad_door_pairs} cycles")
    bad_door_reading_links = connection.execute("""SELECT COUNT(*) FROM sensor_reading r
        JOIN sensor s ON s.sensor_id=r.sensor_id JOIN door_cycle c ON c.door_cycle_id=r.door_cycle_id
        JOIN sensor_zone_assignment a ON a.sensor_id=s.sensor_id AND a.zone_id=c.zone_id
        WHERE s.sensor_type<>'TEMP' OR r.captured_at<c.opened_at OR r.captured_at>c.closed_at""").fetchone()[0]
    if bad_door_reading_links:
        errors.append(f"{bad_door_reading_links} readings link to a non-overlapping or non-temperature door cycle")
    states: dict[tuple[str, str], str] = {}
    allowed_transitions = {
        "alarm": {"open": {"acknowledged", "inspecting", "false_positive", "resolved"},
                  "acknowledged": {"inspecting", "remediating", "resolved"},
                  "inspecting": {"remediating", "resolved"}, "remediating": {"resolved"}},
        "inspection": {"planned": {"in_progress", "cancelled"}, "in_progress": {"completed", "cancelled"}},
        "remediation": {"planned": {"in_progress", "completed", "cancelled"}, "in_progress": {"completed", "cancelled"}},
        "inventory_lot": {"received": {"stored"}, "stored": {"quarantined", "released", "shipped", "consumed", "expired"},
                          "quarantined": {"released", "stored"}, "released": {"shipped", "consumed", "quarantined"}},
    }
    for row in connection.execute("SELECT entity_type,entity_id,action,old_state,new_state FROM audit_event ORDER BY entity_type,entity_id,occurred_at,audit_event_id"):
        if row["action"] == "created" and row["new_state"]:
            states[(row["entity_type"], row["entity_id"])] = row["new_state"]
        elif row["action"] == "state_changed":
            key = (row["entity_type"], row["entity_id"])
            if states.get(key) != row["old_state"]:
                errors.append(f"Invalid lifecycle transition for {key[0]} {key[1]}: expected {states.get(key)}, got {row['old_state']}")
            if row["new_state"] not in allowed_transitions.get(row["entity_type"], {}).get(row["old_state"], set()):
                errors.append(f"Disallowed lifecycle transition for {key[0]} {key[1]}: {row['old_state']} -> {row['new_state']}")
            states[key] = row["new_state"]
    state_tables = {
        "alarm": ("alarm", "alarm_id", "status"),
        "inspection": ("inspection", "inspection_id", "status"),
        "remediation": ("remediation", "remediation_id", "status"),
        "inventory_lot": ("inventory_lot", "lot_id", "lifecycle_state"),
    }
    for entity_type, (table, id_column, state_column) in state_tables.items():
        for row in connection.execute(f"SELECT {id_column},{state_column} FROM {table}"):
            if states.get((entity_type, row[id_column])) != row[state_column]:
                errors.append(f"Final audit state does not match {table}.{state_column} for {row[id_column]}")
    primary_keys = {
        "alarm_affected_lot": ["alarm_id", "lot_id"],
        "remediation_affected_lot": ["remediation_id", "lot_id"],
    }
    for table, columns in TABLE_COLUMNS.items():
        pk_fields = primary_keys.get(table, [columns[0]])
        pk = ",".join(pk_fields)
        duplicate_count = connection.execute(f"SELECT COUNT(*) FROM (SELECT {pk} FROM {table} GROUP BY {pk} HAVING COUNT(*) > 1)").fetchone()[0]
        if duplicate_count:
            errors.append(f"{table} primary key ({pk}) has {duplicate_count} duplicate keys")
    for table, target in expected_counts.items():
        if counts.get(table) != target:
            errors.append(f"{scale}-scale count mismatch for {table}: {counts.get(table)} != {target}")
    leaking_tables = [table for table, columns in TABLE_COLUMNS.items() if "issue_type" in columns]
    if leaking_tables:
        errors.append(f"Ground-truth label leakage in operational tables: {', '.join(leaking_tables)}")
    profiles = connection.execute("SELECT COUNT(*) FROM storage_requirement").fetchone()[0]
    if profiles < 8:
        errors.append(f"Expected at least eight storage requirement profiles, found {profiles}")
    hot_ambient = connection.execute("""SELECT COUNT(DISTINCT z.zone_type) FROM lot_zone_assignment a
        JOIN inventory_lot l ON l.lot_id=a.lot_id JOIN zone z ON z.zone_id=a.zone_id
        WHERE z.zone_type IN ('ambient','hot') AND l.lifecycle_state IN ('stored','quarantined','released')
          AND a.effective_from<='2026-10-22T23:45:00Z' AND (a.effective_to IS NULL OR a.effective_to>'2026-10-22T23:45:00Z')""").fetchone()[0]
    if hot_ambient != 2:
        errors.append("Active inventory is missing from ambient or hot zones")
    door_types = {row[0] for row in connection.execute("SELECT DISTINCT event_type FROM door_event")}
    if not {"open", "closed", "ajar"}.issubset(door_types):
        errors.append(f"Door event coverage is incomplete: {sorted(door_types)}")
    if connection.execute("SELECT COUNT(*) FROM sensor_reading WHERE quality_flag='invalid'").fetchone()[0] < 1:
        errors.append("No invalid-quality readings were generated")
    required_lot_states = {"received", "stored", "quarantined", "released", "shipped", "consumed", "expired"}
    lot_states = {row[0] for row in connection.execute("SELECT DISTINCT lifecycle_state FROM inventory_lot")}
    if not required_lot_states.issubset(lot_states):
        errors.append(f"Inventory lifecycle coverage is incomplete: {sorted(lot_states)}")
    alarms_without_workflow = connection.execute("""SELECT COUNT(*) FROM alarm a WHERE
        NOT EXISTS (SELECT 1 FROM inspection i WHERE i.alarm_id=a.alarm_id) OR
        NOT EXISTS (SELECT 1 FROM inspection i JOIN remediation r ON r.inspection_id=i.inspection_id WHERE i.alarm_id=a.alarm_id)""").fetchone()[0]
    if alarms_without_workflow:
        errors.append(f"{alarms_without_workflow} alarms do not have both an inspection and remediation")
    correlated_door_rises = connection.execute("""SELECT COUNT(*) FROM sensor_reading r
        JOIN sensor s ON s.sensor_id=r.sensor_id JOIN door_cycle d ON d.door_cycle_id=r.door_cycle_id
        WHERE s.sensor_type='TEMP' AND d.duration_seconds>=180""").fetchone()[0]
    if not correlated_door_rises:
        errors.append("Door-open events are not correlated with any temperature readings")
    truth_ids = {str(row["entity_id"]) for row in truth_rows}
    anomaly_counts = {
        "known_truth_rows": len(truth_rows),
        "incompatible_lot_cases": sum(row["issue_type"] == "incompatible_zone" for row in truth_rows),
        "unjustified_sensor_mapping_cases": sum(row["issue_type"] == "unjustified_sensor_mapping" for row in truth_rows),
        "reading_gap_cases": sum(row["issue_type"] == "reading_gap_treated_as_normal" for row in truth_rows),
        "unlinked_alarm_cases": sum(row["issue_type"] == "alarm_missing_lot_link" for row in truth_rows),
    }
    if not truth_ids or any(anomaly_counts[key] < 2 for key in ("incompatible_lot_cases", "unjustified_sensor_mapping_cases", "reading_gap_cases", "unlinked_alarm_cases")):
        errors.append("One or more required labelled exception scenarios do not have repeated coverage")
    truth_counts = {issue: sum(row["issue_type"] == issue for row in truth_rows) for issue in {str(row["issue_type"]) for row in truth_rows}}
    for issue in ("temperature_threshold_boundary", "humidity_threshold_boundary"):
        if truth_counts.get(issue, 0) < 2:
            errors.append(f"Repeated threshold boundary coverage is missing for {issue}")
    for truth in truth_rows:
        if truth["entity_type"] not in TABLE_COLUMNS or str(truth["entity_id"]) == "":
            errors.append(f"Ground-truth row {truth['truth_id']} has no traceable operational entity")
        else:
            table = str(truth["entity_type"])
            pk = TABLE_COLUMNS[table][0]
            if not connection.execute(f"SELECT 1 FROM {table} WHERE {pk}=? LIMIT 1", (truth["entity_id"],)).fetchone():
                errors.append(f"Ground-truth row {truth['truth_id']} does not resolve to {table}.{pk}")
        if truth["issue_type"] in {"temperature_threshold_boundary", "humidity_threshold_boundary"}:
            reading = connection.execute("SELECT sensor_id,captured_at,value FROM sensor_reading WHERE reading_id=?", (truth["entity_id"],)).fetchone()
            if reading:
                column = "max_temp_c" if truth["issue_type"] == "temperature_threshold_boundary" else "max_humidity_pct"
                threshold = connection.execute(f"""SELECT MAX(r.{column}) FROM sensor_zone_assignment sza
                    JOIN zone z ON z.zone_id=sza.zone_id
                    JOIN lot_zone_assignment lza ON lza.zone_id=sza.zone_id
                    JOIN inventory_lot l ON l.lot_id=lza.lot_id
                    JOIN storage_requirement r ON r.product_code=l.product_code
                    WHERE sza.sensor_id=? AND sza.effective_from<=? AND (sza.effective_to IS NULL OR sza.effective_to>?)
                      AND lza.effective_from<=? AND (lza.effective_to IS NULL OR lza.effective_to>?)
                      AND ((z.zone_type='cold' AND r.max_temp_c>=0 AND r.min_temp_c<=8)
                        OR (z.zone_type='frozen' AND r.max_temp_c>=-25 AND r.min_temp_c<=-12)
                        OR (z.zone_type='ambient' AND r.max_temp_c>=15 AND r.min_temp_c<=28)
                        OR (z.zone_type='hot' AND r.max_temp_c>=28 AND r.min_temp_c<=40))
                      AND l.lifecycle_state IN ('stored','quarantined','released')""",
                    (reading["sensor_id"], reading["captured_at"], reading["captured_at"], reading["captured_at"], reading["captured_at"])).fetchone()[0]
                if threshold is None or abs(float(reading["value"])-float(threshold)) > 1e-6:
                    errors.append(f"Boundary case {truth['truth_id']} does not equal an active lot's upper threshold")
                if connection.execute("SELECT 1 FROM alarm WHERE sensor_id=? AND triggered_at=? LIMIT 1", (reading["sensor_id"], reading["captured_at"])).fetchone():
                    errors.append(f"Exact-threshold boundary {truth['truth_id']} incorrectly produced an alarm")
        elif truth["issue_type"] == "reading_gap_treated_as_normal":
            gap = connection.execute("SELECT sensor_id,started_at,ended_at FROM sensor_outage WHERE outage_id=?", (truth["entity_id"],)).fetchone()
            if gap:
                seconds = (datetime.fromisoformat(gap["ended_at"].replace("Z", "+00:00")) - datetime.fromisoformat(gap["started_at"].replace("Z", "+00:00"))).total_seconds()
                if seconds != 2700:
                    errors.append(f"Boundary outage {truth['truth_id']} is not exactly 45 minutes")
                key = f"engine:SENSOR_GAP:{gap['sensor_id']}:{truth['entity_id']}"
                if connection.execute("SELECT 1 FROM alarm WHERE source_key=?", (key,)).fetchone():
                    errors.append(f"Exact 45-minute outage {truth['truth_id']} incorrectly produced an alarm")
    result = {
        "status": "passed" if not errors else "failed",
        "scale": scale,
        "counts": counts,
        "foreign_key_violations": len(fk_errors),
        "arithmetic_reconciliation": "passed" if not bad_alarm_quantities and not bad_remediation_totals and not bad_dwell and not bad_cycle_duration and not bad_door_pairs else "failed",
        "lifecycle_transitions": "passed" if not any("lifecycle transition" in error.lower() or "final audit state" in error.lower() for error in errors) else "failed",
        "unique_primary_keys": "failed" if any("duplicate keys" in error for error in errors) else "passed",
        "required_exception_coverage": anomaly_counts,
        "metrics": {
            "alarm_workflow_coverage_pct": round(100 * (counts.get("alarm", 0) - alarms_without_workflow) / counts.get("alarm", 1), 1),
            "alarms_with_missing_impact_links": connection.execute("""SELECT COUNT(*) FROM alarm a WHERE
                NOT EXISTS (SELECT 1 FROM alarm_affected_lot x WHERE x.alarm_id=a.alarm_id)""").fetchone()[0],
            "active_ambient_hot_zone_classes_with_lots": hot_ambient,
            "door_event_type_counts": {row[0]: row[1] for row in connection.execute("SELECT event_type,COUNT(*) FROM door_event GROUP BY event_type")},
            "quality_flag_counts": {row[0]: row[1] for row in connection.execute("SELECT quality_flag,COUNT(*) FROM sensor_reading GROUP BY quality_flag")},
            "door_temperature_links": correlated_door_rises,
            "boundary_case_counts": {issue: truth_counts.get(issue, 0) for issue in (
                "temperature_threshold_boundary", "humidity_threshold_boundary", "reading_gap_treated_as_normal")},
        },
        "errors": errors,
    }
    connection.close()
    Path(temp_name).unlink(missing_ok=True)
    return result


def emit(dataset: Dataset, output: Path, formats: set[str]) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    file_stats: dict[str, object] = {}
    if "csv" in formats:
        file_stats["csv"] = write_csv(dataset, output)
    if "json" in formats:
        file_stats["json"] = write_json(dataset, output)
        truth_json = json.dumps(dataset.truth_rows, indent=2) + "\n"
        (output / "ground_truth.json").write_text(truth_json, encoding="utf-8")
        file_stats["ground_truth_json"] = {"file": "ground_truth.json", "rows": len(dataset.truth_rows), "sha256": hashlib.sha256(truth_json.encode()).hexdigest()}
    if "sql" in formats:
        file_stats["sql"] = write_sql(dataset, output)
    if "csv" in formats:
        validation = run_validation(output, dataset.truth_rows, dataset.scale, dataset.expected_counts)
    else:
        validation = {"status": "not_run", "note": "CSV output is required for relational validation."}
    manifest = {
        "project": "Warehouse Zone Risk Monitor",
        "source": "Synthetic records generated locally; no external datasets used.",
        "seed": dataset.seed,
        "scale": dataset.scale,
        "generated_at": "2026-11-14T00:00:00Z",
        "format": sorted(formats),
        "counts": dataset.expected_counts,
        "files": file_stats,
        "validation": validation,
        "ground_truth_file": "ground_truth.csv" if "csv" in formats else ("ground_truth.json" if "json" in formats else "ground_truth table in seed.sql"),
        "ground_truth_is_operational_input": False,
    }
    if "csv" in formats:
        with (output / "ground_truth.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=dataset.truth_columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(dataset.truth_rows)
        digest = hashlib.sha256()
        for row in dataset.truth_rows:
            digest.update((canonical(row) + "\n").encode())
        file_stats["ground_truth_csv"] = {"file": "ground_truth.csv", "rows": len(dataset.truth_rows), "sha256": digest.hexdigest()}
    (output / "validation_summary.json").write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    (output / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", choices=("starter", "full"), default="starter")
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--format", choices=("csv", "json", "sql", "all"), default="all")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "demo")
    parser.add_argument("--check-reproducibility", action="store_true", help="Generate twice in memory and compare row counts and SHA-256 digests.")
    args = parser.parse_args()
    fmt = {"csv", "json", "sql"} if args.format == "all" else {args.format}
    dataset = build_dataset(args.scale, args.seed)
    manifest = emit(dataset, args.output, fmt)
    if args.check_reproducibility:
        first = checksum_dataset(build_dataset(args.scale, args.seed))
        second = checksum_dataset(build_dataset(args.scale, args.seed))
        reproducible = first == second
        manifest["reproducibility"] = {"passed": reproducible, "first_run": first, "second_run": second}
        (args.output / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if not reproducible:
            return 1
    print(json.dumps({"output": str(args.output), "scale": args.scale, "seed": args.seed, "validation": manifest["validation"], "reproducibility": manifest.get("reproducibility", {}).get("passed")}, indent=2))
    return 0 if manifest["validation"]["status"] in ("passed", "not_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
