"""Deterministic synthetic data for the Warehouse Zone Risk Monitor."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Iterator

UTC = timezone.utc
AS_OF = datetime(2026, 10, 22, 23, 45, tzinfo=UTC)
AS_OF_TEXT = "2026-10-22T23:45:00Z"
BASELINE = datetime(2025, 1, 1, tzinfo=UTC)
PROFILE = {
    "cold": (0.0, 8.0), "frozen": (-25.0, -12.0),
    "ambient": (15.0, 28.0), "hot": (28.0, 40.0),
}
PRODUCTS = {
    "cold": [
        ("PROD-DAIRY", "Chilled dairy", 2.0, 6.0, 40.0, 70.0, 120.0),
        ("PROD-PRODUCE", "Fresh produce", 3.0, 8.0, 55.0, 90.0, 96.0),
    ],
    "frozen": [
        ("PROD-FROZEN", "Frozen meals", -22.0, -15.0, 20.0, 60.0, 360.0),
        ("PROD-FROZEN-BAKERY", "Frozen bakery", -20.0, -12.0, 25.0, 65.0, 300.0),
    ],
    "ambient": [
        ("PROD-AMBIENT", "Ambient packaged goods", 16.0, 25.0, 30.0, 65.0, 240.0),
        ("PROD-DRY", "Ambient dry goods", 18.0, 27.0, 25.0, 60.0, 360.0),
    ],
    "hot": [
        ("PROD-CURED", "Hot cured foods", 28.0, 35.0, 35.0, 70.0, 48.0),
        ("PROD-HOT-PROCESS", "Heated process stock", 30.0, 40.0, 25.0, 65.0, 24.0),
    ],
}
TABLE_COLUMNS: dict[str, list[str]] = {
    "warehouse": ["warehouse_id", "name", "region", "lifecycle_state", "created_at"],
    "zone": ["zone_id", "warehouse_id", "name", "zone_type", "capacity_pallets", "lifecycle_state", "effective_from", "effective_to"],
    "storage_requirement": ["requirement_id", "product_code", "product_name", "min_temp_c", "max_temp_c", "min_humidity_pct", "max_humidity_pct", "max_dwell_hours", "effective_from", "effective_to"],
    "sensor": ["sensor_id", "sensor_type", "unit", "lifecycle_state", "calibrated_at", "drift_per_day", "created_at"],
    "sensor_zone_assignment": ["assignment_id", "sensor_id", "zone_id", "effective_from", "effective_to", "rationale"],
    "door_cycle": ["door_cycle_id", "zone_id", "opened_at", "closed_at", "duration_seconds", "closure_status"],
    "inventory_lot": ["lot_id", "product_code", "quantity_units", "received_at", "expires_at", "lifecycle_state"],
    "lot_zone_assignment": ["lot_zone_assignment_id", "lot_id", "zone_id", "effective_from", "effective_to", "dwell_hours"],
    "sensor_outage": ["outage_id", "sensor_id", "started_at", "ended_at", "expected_interval_seconds", "reason"],
    "sensor_reading": ["reading_id", "sensor_id", "captured_at", "value", "quality_flag", "door_cycle_id"],
    "door_event": ["door_event_id", "door_cycle_id", "zone_id", "occurred_at", "event_type", "duration_seconds"],
    "alarm": ["alarm_id", "zone_id", "sensor_id", "alarm_code", "severity", "triggered_at", "status", "summary", "source_key", "resolved_at"],
    "alarm_affected_lot": ["alarm_id", "lot_id", "impact_quantity_units"],
    "inspection": ["inspection_id", "alarm_id", "zone_id", "assigned_role", "status", "created_at", "completed_at", "findings"],
    "remediation": ["remediation_id", "inspection_id", "action_type", "status", "inventory_impact_quantity_units", "created_at", "completed_at", "notes"],
    "remediation_affected_lot": ["remediation_id", "lot_id", "impacted_quantity_units"],
    "audit_event": ["audit_event_id", "entity_type", "entity_id", "action", "old_state", "new_state", "actor", "occurred_at", "details_json"],
}
TRUTH_COLUMNS = ["truth_id", "scenario_id", "entity_type", "entity_id", "issue_type", "expected_decision", "expected_severity", "rationale"]


@dataclass
class Dataset:
    scale: str
    seed: int
    expected_counts: dict[str, int]
    _rows: dict[str, list[dict[str, object]] | Callable[[], Iterable[dict[str, object]]]]
    truth_rows: list[dict[str, object]]

    @property
    def table_names(self) -> list[str]:
        return list(TABLE_COLUMNS)

    @property
    def truth_columns(self) -> list[str]:
        return TRUTH_COLUMNS

    def rows(self, table: str) -> Iterator[dict[str, object]]:
        value = self._rows[table]
        return iter(value() if callable(value) else value)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def token(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def _row(**values: object) -> dict[str, object]:
    return values


def build_dataset(scale: str = "starter", seed: int = 20261022) -> Dataset:
    if scale not in {"starter", "full"}:
        raise ValueError("scale must be 'starter' or 'full'")
    rng = random.Random(seed)
    full = scale == "full"
    warehouse_count, zones_per_wh = (10, 10) if full else (3, 4)
    reading_count, sensor_count_per_zone = (2000, 5) if full else (70, 3)
    lot_count = 10_000 if full else 120
    door_cycle_count = 2500 if full else 20
    zone_types = ("cold", "frozen", "ambient", "hot")

    warehouses = [_row(warehouse_id=f"WH-{i:03d}", name=f"Distribution Center {i:02d}",
                       region=("South", "North", "East", "West", "Central")[(i-1) % 5],
                       lifecycle_state="active", created_at=iso(BASELINE))
                  for i in range(1, warehouse_count + 1)]
    zones: list[dict[str, object]] = []
    zone_products: dict[str, tuple] = {}
    for wi in range(1, warehouse_count + 1):
        wh = f"WH-{wi:03d}"
        for zi in range(1, zones_per_wh + 1):
            zone_id = f"ZONE-{wh}-{zi:03d}"
            ztype = zone_types[(zi - 1) % 4]
            zone = _row(zone_id=zone_id, warehouse_id=wh, name=f"Zone {zi:03d}", zone_type=ztype,
                        capacity_pallets=100 + 10 * zi, lifecycle_state="active",
                        effective_from=iso(BASELINE), effective_to=None)
            zones.append(zone)
            zone_products[zone_id] = PRODUCTS[ztype][(wi + zi) % 2]

    requirements = [_row(requirement_id=f"REQ-{prod[0]}", product_code=prod[0], product_name=prod[1],
                         min_temp_c=prod[2], max_temp_c=prod[3], min_humidity_pct=prod[4],
                         max_humidity_pct=prod[5], max_dwell_hours=prod[6],
                         effective_from=iso(BASELINE), effective_to=None)
                    for ptype in zone_types for prod in PRODUCTS[ptype]]
    zones_by_id = {str(z["zone_id"]): z for z in zones}

    sensors: list[dict[str, object]] = []
    sensor_assignment: list[dict[str, object]] = []
    sensor_info: dict[str, tuple[str, str, str]] = {}
    for zone in zones:
        zid = str(zone["zone_id"])
        types = [("TEMP", "C"), ("HUMIDITY", "%RH"), ("OCCUPANCY", "%")]
        if full:
            types += [("TEMP", "C"), ("HUMIDITY", "%RH")]
        for typ, unit in types:
            # Distinguish redundant probes without leaking incident labels.
            suffix = 2 if typ == "TEMP" and any(s["sensor_id"] == f"SNS-{zid}-TEMP-01" for s in sensors) else (
                2 if typ == "HUMIDITY" and any(s["sensor_id"] == f"SNS-{zid}-HUMIDITY-01" for s in sensors) else 1)
            sid = f"SNS-{zid}-{typ}-{suffix:02d}"
            drift = round(rng.uniform(-0.012, 0.012), 4) if typ == "TEMP" else round(rng.uniform(-0.02, 0.02), 4)
            sensors.append(_row(sensor_id=sid, sensor_type=typ, unit=unit, lifecycle_state="active",
                                calibrated_at="2026-10-01T00:00:00Z", drift_per_day=drift,
                                created_at=iso(BASELINE)))
            sensor_assignment.append(_row(assignment_id=f"SZA-{len(sensor_assignment)+1:07d}", sensor_id=sid,
                                          zone_id=zid, effective_from="2025-01-01T00:00:00Z",
                                          effective_to=None, rationale="commissioned in zone"))
            sensor_info[sid] = (typ, zid, unit)

    # Give selected sensors a second, unjustified active zone mapping. The source
    # rows remain unlabeled; the separate ground truth file carries the issue label.
    mapping_count = 8 if full else 2
    duplicated_sensors: list[str] = []
    duplicate_assignment_ids: list[tuple[str, str]] = []
    for i, sensor in enumerate(sensors):
        if len(duplicated_sensors) >= mapping_count:
            break
        sid, _, home_zone = str(sensor["sensor_id"]), str(sensor["sensor_type"]), sensor_info[str(sensor["sensor_id"])][1]
        if sensor_info[sid][0] != "TEMP":
            continue
        other = next(str(z["zone_id"]) for z in zones if z["zone_id"] != home_zone and z["zone_type"] != zones_by_id[home_zone]["zone_type"])
        duplicated_sensors.append(sid)
        duplicate_assignment_id = f"SZA-{len(sensor_assignment)+1:07d}"
        duplicate_assignment_ids.append((sid, duplicate_assignment_id))
        sensor_assignment.append(_row(assignment_id=duplicate_assignment_id, sensor_id=sid,
                                      zone_id=other, effective_from="2025-01-01T00:00:00Z",
                                      effective_to=None, rationale=None))

    # Lot statuses are a deterministic lifecycle mix, with active stock in every
    # storage class and historical assignments closed for shipped/consumed stock.
    status_weights = [("received", 0.08), ("stored", 0.55), ("quarantined", 0.10),
                      ("released", 0.10), ("shipped", 0.07), ("consumed", 0.05), ("expired", 0.05)]
    lot_statuses = [state for state, weight in status_weights for _ in range(int(lot_count * weight))]
    lot_statuses.extend(["stored"] * (lot_count - len(lot_statuses)))
    rng.shuffle(lot_statuses)
    lot_specs: list[dict[str, object]] = []
    for i in range(1, lot_count + 1):
        home = zones[(i - 1) % len(zones)]
        zid = str(home["zone_id"])
        product = zone_products[zid]
        status = lot_statuses[i - 1]
        max_active_dwell = float(product[6]) * 0.78
        if status in {"stored", "quarantined", "released"}:
            age_hours = rng.uniform(2.0, max(2.01, max_active_dwell))
        elif status in {"shipped", "consumed", "expired"}:
            age_hours = rng.uniform(180.0, 900.0)
        else:
            age_hours = rng.uniform(2.0, 900.0)
        received = AS_OF - timedelta(hours=age_hours, minutes=rng.choice([0, 15, 30, 45]))
        if status == "expired":
            expires = received + timedelta(hours=rng.randint(1, 300))
            if expires >= AS_OF:
                expires = AS_OF - timedelta(hours=1)
        else:
            expires = received + timedelta(days=rng.randint(15, 240))
        lot_specs.append(_row(lot_id=f"LOT-2026-{i:06d}", product_code=product[0],
                              quantity_units=rng.randint(24, 420), received_at=iso(received),
                              expires_at=iso(expires), lifecycle_state=status, home_zone=zid,
                              received_dt=received))

    active_specs = [lot for lot in lot_specs if lot["lifecycle_state"] in {"stored", "quarantined", "released"}]
    incompatible_count = 15 if full else 3
    incompatible_specs = active_specs[::max(1, len(active_specs)//incompatible_count)][:incompatible_count]
    incompatible_ids = {str(lot["lot_id"]) for lot in incompatible_specs}
    assignments: list[dict[str, object]] = []
    lot_zone_by_id: dict[str, dict[str, object]] = {}
    for lot in lot_specs:
        state = str(lot["lifecycle_state"])
        if state == "received":
            continue
        zid = str(lot["home_zone"])
        if str(lot["lot_id"]) in incompatible_ids:
            src_type = str(zones_by_id[zid]["zone_type"])
            zid = next(str(z["zone_id"]) for z in zones if str(z["zone_type"]) != src_type)
        start = lot["received_dt"]
        end: datetime | None = None
        if state in {"shipped", "consumed", "expired"}:
            end = AS_OF - timedelta(hours=rng.randint(1, 96))
            if end <= start:
                start = end - timedelta(hours=24)
        dwell = (end or AS_OF) - start
        assign = _row(lot_zone_assignment_id=f"LZA-{len(assignments)+1:07d}", lot_id=lot["lot_id"],
                      zone_id=zid, effective_from=iso(start), effective_to=iso(end) if end else None,
                      dwell_hours=round(dwell.total_seconds()/3600, 6))
        assignments.append(assign)
        lot_zone_by_id[str(lot["lot_id"])] = assign

    # Event schedules. Outage intervals are observed gaps, so exactly 45 minutes
    # is a documented no-alert boundary while >45 minutes produces a SENSOR_GAP.
    temp_count, humidity_count, outage_count, long_door_count = ((16, 12, 8, 8) if full else (5, 4, 3, 3))
    outage_count += 2  # repeated exact-threshold boundary cases
    temp_sensors = [sid for sid, info in sensor_info.items() if info[0] == "TEMP" and sid not in duplicated_sensors]
    humidity_sensors = [sid for sid, info in sensor_info.items() if info[0] == "HUMIDITY"]
    occupancy_sensors = [sid for sid, info in sensor_info.items() if info[0] == "OCCUPANCY"]
    base_start = AS_OF - timedelta(minutes=15*(reading_count-1))

    def choose_sensor(candidates: list[str], idx: int) -> str:
        return candidates[(idx * 7 + 1) % len(candidates)]

    temp_plans: list[dict[str, object]] = []
    humidity_plans: list[dict[str, object]] = []
    outage_plans: list[dict[str, object]] = []
    incident_truth: list[dict[str, object]] = []
    truth_n = 0

    def add_truth(scenario: str, entity_type: str, entity_id: str, issue: str, decision: str,
                  severity: str | None, rationale: str) -> None:
        nonlocal truth_n
        truth_n += 1
        incident_truth.append(_row(truth_id=f"GT-{truth_n:06d}", scenario_id=scenario,
                                   entity_type=entity_type, entity_id=entity_id, issue_type=issue,
                                   expected_decision=decision, expected_severity=severity, rationale=rationale))

    for i in range(temp_count):
        sid = choose_sensor(temp_sensors, i)
        typ, zid, _ = sensor_info[sid]
        idx = reading_count - max(24, temp_count + 7) + i
        when = base_start + timedelta(minutes=15*idx)
        req = next(p for p in PRODUCTS[str(zones_by_id[zid]["zone_type"])] if p[0] == zone_products[zid][0])
        direction = "high" if i % 2 == 0 else "low"
        value = req[3] + 4.0 + (i % 3) if direction == "high" else req[2] - 4.0 - (i % 2)
        plan = _row(sensor_id=sid, zone_id=zid, index=idx, captured_at=when, value=round(value, 3),
                    direction=direction, requirement=req, scenario=f"TEMP-{i+1:03d}")
        temp_plans.append(plan)
        add_truth(plan["scenario"], "sensor_reading", f"RDG-{sid}-{token(when)}", "temperature_excursion",
                  "raise_temperature_alarm_and_identify_overlapping_lots", "critical" if abs(value-req[3]) > 6 else "high",
                  "Reading is outside the active storage requirement; at least two consecutive samples are injected.")

    for i in range(humidity_count):
        sid = choose_sensor(humidity_sensors, i)
        _, zid, _ = sensor_info[sid]
        idx = reading_count - max(24, humidity_count + 7) + i
        when = base_start + timedelta(minutes=15*idx)
        req = next(p for p in PRODUCTS[str(zones_by_id[zid]["zone_type"])] if p[0] == zone_products[zid][0])
        direction = "high" if i % 2 == 0 else "low"
        value = req[5] + 12.0 if direction == "high" else req[4] - 12.0
        plan = _row(sensor_id=sid, zone_id=zid, index=idx, captured_at=when, value=round(value, 3),
                    direction=direction, requirement=req, scenario=f"HUM-{i+1:03d}")
        humidity_plans.append(plan)
        add_truth(plan["scenario"], "sensor_reading", f"RDG-{sid}-{token(when)}", "humidity_excursion",
                  "raise_humidity_alarm_and_identify_overlapping_lots", "high",
                  "Reading is outside the active humidity requirement; two adjacent samples are injected.")

    # Temperature and humidity points exactly on an allowed maximum are boundary
    # controls. Strict greater-than comparisons must not alert at equality.
    boundary_count = 4 if full else 2
    used_temp_sensors = {str(p["sensor_id"]) for p in temp_plans}
    boundary_temp_sensors = [sid for sid in temp_sensors if sid not in used_temp_sensors]
    used_humidity_sensors = {str(p["sensor_id"]) for p in humidity_plans}
    boundary_humidity_sensors = [sid for sid in humidity_sensors if sid not in used_humidity_sensors]
    for i in range(boundary_count):
        sid = boundary_temp_sensors[i]
        _, zid, _ = sensor_info[sid]
        req = next(p for p in PRODUCTS[str(zones_by_id[zid]["zone_type"])] if p[0] == zone_products[zid][0])
        idx = reading_count - 8 + i * 2
        when = base_start + timedelta(minutes=15*idx)
        temp_plans.append(_row(sensor_id=sid, zone_id=zid, index=idx, captured_at=when, value=req[3], direction="boundary",
                               requirement=req, scenario=f"TEMP-BOUNDARY-{i+1:03d}"))
        add_truth(f"TEMP-BOUNDARY-{i+1:03d}", "sensor_reading", f"RDG-{sid}-{token(when)}", "temperature_threshold_boundary",
                  "do_not_alert_at_exact_threshold", None, "Temperature equals the product maximum; the rule is strictly greater-than.")

        hsid = boundary_humidity_sensors[i]
        _, hzid, _ = sensor_info[hsid]
        hreq = next(p for p in PRODUCTS[str(zones_by_id[hzid]["zone_type"])] if p[0] == zone_products[hzid][0])
        hwhen = base_start + timedelta(minutes=15*(idx+1))
        humidity_plans.append(_row(sensor_id=hsid, zone_id=hzid, index=idx+1, captured_at=hwhen,
                                   value=hreq[5], direction="boundary", requirement=hreq,
                                   scenario=f"HUM-BOUNDARY-{i+1:03d}"))
        add_truth(f"HUM-BOUNDARY-{i+1:03d}", "sensor_reading", f"RDG-{hsid}-{token(hwhen)}", "humidity_threshold_boundary",
                  "do_not_alert_at_exact_threshold", None, "Humidity equals the product maximum; the rule is strictly greater-than.")

    severe_outages = outage_count - 2
    outage_sensor_choices = [sid for sid in occupancy_sensors if sid not in duplicated_sensors]
    for i in range(severe_outages + 2):
        sid = choose_sensor(outage_sensor_choices, i)
        _, zid, _ = sensor_info[sid]
        idx = reading_count - 20 + i
        is_boundary = i >= severe_outages
        skipped = 2 if is_boundary else (3 + i % 3)
        start = base_start + timedelta(minutes=15*idx)
        end = start + timedelta(minutes=15*(skipped+1))
        plan = _row(sensor_id=sid, zone_id=zid, index=idx, start=start, end=end,
                    duration_seconds=int((end-start).total_seconds()), scenario=f"GAP-{i+1:03d}",
                    boundary=is_boundary)
        outage_plans.append(plan)
        if is_boundary:
            add_truth(plan["scenario"], "sensor_outage", f"OUT-2026-{i+1:06d}", "reading_gap_treated_as_normal",
                      "do_not_alert_at_exactly_45_minutes", None,
                      "Observed gap is exactly 45 minutes, matching the strict >45-minute alert rule.")
        else:
            add_truth(plan["scenario"], "sensor_outage", f"OUT-2026-{i+1:06d}", "sensor_outage",
                      "raise_sensor_gap_alarm", "high", "Observed gap exceeds the 45-minute freshness threshold.")

    # Inject invalid quality readings as explicit source quality, not as labels.
    # One selected invalid point is additionally documented in separate truth.
    invalid_samples: set[tuple[str, int]] = set()
    invalid_target = sensors[0]
    invalid_sid = str(invalid_target["sensor_id"])
    invalid_samples.add((invalid_sid, min(reading_count-2, 6)))
    add_truth("QUALITY-INVALID-001", "sensor_reading", f"RDG-{invalid_sid}-{token(base_start + timedelta(minutes=15*min(reading_count-2,6)))}",
              "invalid_reading", "exclude_from_risk_evaluation", None,
              "Reading quality is marked invalid; evaluation must ignore it.")

    # Build active lot lookup using half-open assignment intervals [from,to).
    lot_by_id = {str(lot["lot_id"]): lot for lot in lot_specs}
    assignments_by_zone: dict[str, list[dict[str, object]]] = {}
    for a in assignments:
        assignments_by_zone.setdefault(str(a["zone_id"]), []).append(a)

    def overlaps(zone_id: str, start: datetime, end: datetime) -> list[dict[str, object]]:
        found = []
        for a in assignments_by_zone.get(zone_id, []):
            astart = datetime.fromisoformat(str(a["effective_from"]).replace("Z", "+00:00"))
            aend = datetime.fromisoformat(str(a["effective_to"]).replace("Z", "+00:00")) if a["effective_to"] else None
            if astart < end and (aend is None or aend > start):
                lot = lot_by_id[str(a["lot_id"])]
                if lot["lifecycle_state"] in {"stored", "quarantined", "released"}:
                    found.append(lot)
        return found

    def active_at(zone_id: str, at: datetime) -> list[dict[str, object]]:
        return overlaps(zone_id, at, at + timedelta(seconds=1))

    # Door cycles cover all zones. Eight (starter: three) are prolonged; the
    # first cycles overlap an excursion in the same zone and are linked to its
    # temperature readings with door_cycle_id.
    door_cycles: list[dict[str, object]] = []
    door_events: list[dict[str, object]] = []
    long_door_count = min(long_door_count, door_cycle_count)
    for i in range(door_cycle_count):
        if i < min(long_door_count, len(temp_plans)):
            associated = temp_plans[i]
            zid = str(associated["zone_id"])
            opened = associated["captured_at"] - timedelta(minutes=5)
        else:
            zid = str(zones[(i * 13 + 2) % len(zones)]["zone_id"])
            idx = 1 + (i * 37) % max(2, reading_count-2)
            opened = base_start + timedelta(minutes=15*idx)
        prolonged = i < long_door_count
        duration = (480 + (i % 4)*180) if prolonged else (45 + (i*23) % 135)
        closed = opened + timedelta(seconds=duration)
        closure = "ajar" if i % 10 == 9 else "closed"
        cycle_id = f"DCL-2026-{i+1:06d}"
        door_cycles.append(_row(door_cycle_id=cycle_id, zone_id=zid, opened_at=iso(opened), closed_at=iso(closed),
                                duration_seconds=duration, closure_status=closure))
        open_id = f"DOR-2026-{2*i+1:06d}"
        close_id = f"DOR-2026-{2*i+2:06d}"
        door_events.append(_row(door_event_id=open_id, door_cycle_id=cycle_id, zone_id=zid,
                                occurred_at=iso(opened), event_type="open", duration_seconds=duration))
        door_events.append(_row(door_event_id=close_id, door_cycle_id=cycle_id, zone_id=zid,
                                occurred_at=iso(closed), event_type=closure, duration_seconds=0))
        if prolonged:
            add_truth(f"DOOR-{i+1:03d}", "door_event", open_id, "prolonged_door_open",
                      "raise_door_alarm_and_check_temperature_recovery", "high",
                      f"Door remained open for {duration} seconds; prolonged threshold is 180 seconds.")
        if i < min(long_door_count, len(temp_plans)):
            add_truth(f"DOOR-TEMP-{i+1:03d}", "door_cycle", cycle_id, "door_temperature_correlation",
                      "link_door_opening_to_nearby_temperature_excursion", "high",
                      "Door-open cycle and temperature excursion overlap in the same zone.")

    # Per-sensor reading plans. Outage samples are omitted, then the same number
    # of historical samples are added to a healthy probe to preserve exact volume.
    gap_indices: dict[str, set[int]] = {}
    for outage in outage_plans:
        # start is the last-good observation; remove the following scheduled rows
        # until the next observed row gives the documented interval.
        start_index = int((outage["start"] - base_start).total_seconds() // 900)
        gap = int(outage["duration_seconds"] // 900) - 1
        gap_indices.setdefault(str(outage["sensor_id"]), set()).update(range(start_index+1, start_index+1+gap))
    missing_total = sum(map(len, gap_indices.values()))
    balance_sensor = next(sid for sid in temp_sensors if sid not in {str(p["sensor_id"]) for p in temp_plans})
    balance_extra = missing_total
    temp_injections: dict[tuple[str, int], float] = {}
    humidity_injections: dict[tuple[str, int], float] = {}
    for plan in temp_plans:
        sid, idx = str(plan["sensor_id"]), int(plan["index"])
        temp_injections[(sid, idx)] = float(plan["value"])
        if plan["direction"] != "boundary":
            temp_injections[(sid, idx+1)] = float(plan["value"])
            temp_injections[(sid, idx+2)] = float(plan["value"])
    for plan in humidity_plans:
        sid, idx = str(plan["sensor_id"]), int(plan["index"])
        humidity_injections[(sid, idx)] = float(plan["value"])
        if plan["direction"] != "boundary":
            humidity_injections[(sid, idx+1)] = float(plan["value"])

    cycle_for_temp: dict[tuple[str, int], str] = {}
    for i in range(min(long_door_count, len(temp_plans))):
        plan = temp_plans[i]
        cycle_for_temp[(str(plan["sensor_id"]), int(plan["index"]))] = str(door_cycles[i]["door_cycle_id"])

    def reading_rows() -> Iterable[dict[str, object]]:
        sensor_lookup = {str(s["sensor_id"]): s for s in sensors}
        for sidx, sensor in enumerate(sensors):
            sid = str(sensor["sensor_id"])
            typ, zid, _ = sensor_info[sid]
            base_temp = {"cold": 4.3, "frozen": -18.5, "ambient": 21.5, "hot": 33.0}[str(zones_by_id[zid]["zone_type"])]
            req = zone_products[zid]
            base_hum = {"cold": 62.5, "frozen": 42.5, "ambient": 45.0, "hot": 50.0}[str(zones_by_id[zid]["zone_type"])]
            rows_by_time: list[tuple[int, datetime]] = [
                (idx, base_start + timedelta(minutes=15*idx))
                for idx in range(reading_count) if idx not in gap_indices.get(sid, set())
            ]
            if sid == balance_sensor:
                rows_by_time += [(-balance_extra+i, base_start - timedelta(minutes=15*(balance_extra-i))) for i in range(balance_extra)]
            rows_by_time.sort(key=lambda pair: pair[1])
            local = random.Random(seed * 1_000_003 + sidx * 97)
            for idx, captured in rows_by_time:
                days = (captured - datetime(2026, 10, 1, tzinfo=UTC)).total_seconds()/86400
                if typ == "TEMP":
                    value = base_temp + float(sensor["drift_per_day"])*days + local.gauss(0, 0.42)
                    value = temp_injections.get((sid, idx), value)
                elif typ == "HUMIDITY":
                    value = base_hum + local.gauss(0, 1.6)
                    value = humidity_injections.get((sid, idx), value)
                else:
                    value = min(94.0, max(8.0, 46 + 14*local.random() + 4*local.gauss(0, 1)))
                    if idx == reading_count-2 and sid == occupancy_sensors[0]:
                        value = 95.0
                quality = "invalid" if (sid, idx) in invalid_samples else ("invalid" if local.random() < 0.002 else "suspect" if local.random() < 0.03 else "good")
                if (sid, idx) in temp_injections or (sid, idx) in humidity_injections:
                    quality = "good"
                rid = f"RDG-{sid}-{token(captured)}"
                yield _row(reading_id=rid, sensor_id=sid, captured_at=iso(captured), value=round(float(value), 3),
                           quality_flag=quality, door_cycle_id=cycle_for_temp.get((sid, idx)))

    # Create derived alarm specifications from source observations and explicit
    # domain rules. Ground truth is never read by this calculation.
    alarm_specs: list[dict[str, object]] = []
    for plan in temp_plans:
        if plan["direction"] == "boundary":
            continue
        when, sid, zid, idx = plan["captured_at"], str(plan["sensor_id"]), str(plan["zone_id"]), int(plan["index"])
        sample_id = f"RDG-{sid}-{token(when)}"
        code = "TEMP_HIGH" if plan["direction"] == "high" else "TEMP_LOW"
        affected = active_at(zid, when)
        alarm_specs.append(_row(zone_id=zid, sensor_id=sid, alarm_code=code,
                                severity="critical" if abs(float(plan["value"])-float(plan["requirement"][3 if code == "TEMP_HIGH" else 2])) >= 7 else "high",
                                triggered_at=iso(when), summary="Temperature reading is outside the active product storage requirement.",
                                source_key=f"engine:{code}:{sid}:{sample_id}", affected=affected, category="temperature"))
    for plan in humidity_plans:
        if plan["direction"] == "boundary":
            continue
        when, sid, zid = plan["captured_at"], str(plan["sensor_id"]), str(plan["zone_id"])
        sample_id = f"RDG-{sid}-{token(when)}"
        code = "HUMIDITY_HIGH" if plan["direction"] == "high" else "HUMIDITY_LOW"
        alarm_specs.append(_row(zone_id=zid, sensor_id=sid, alarm_code=code, severity="high",
                                triggered_at=iso(when), summary="Humidity reading is outside the active product storage requirement.",
                                source_key=f"engine:{code}:{sid}:{sample_id}", affected=active_at(zid, when), category="humidity"))
    for plan in outage_plans:
        if plan["boundary"]:
            continue
        oid = f"OUT-2026-{outage_plans.index(plan)+1:06d}"
        start, end, zid, sid = plan["start"], plan["end"], str(plan["zone_id"]), str(plan["sensor_id"])
        affected = overlaps(zid, start, end)
        alarm_specs.append(_row(zone_id=zid, sensor_id=sid, alarm_code="SENSOR_GAP", severity="high",
                                triggered_at=iso(start + timedelta(seconds=2700)),
                                summary="Observed sensor gap exceeded the 45-minute freshness threshold.",
                                source_key=f"engine:SENSOR_GAP:{sid}:{oid}", affected=affected, category="gap"))
    for i in range(long_door_count):
        cycle = door_cycles[i]
        opened = datetime.fromisoformat(str(cycle["opened_at"]).replace("Z", "+00:00"))
        zid = str(cycle["zone_id"])
        open_event = door_events[2*i]
        alarm_specs.append(_row(zone_id=zid, sensor_id=None, alarm_code="DOOR_OPEN_LONG", severity="high",
                                triggered_at=str(cycle["opened_at"]),
                                summary=f"Door remained open for {cycle['duration_seconds']} seconds.",
                                source_key=f"engine:DOOR_OPEN_LONG:{open_event['door_event_id']}",
                                affected=active_at(zid, opened), category="door"))

    # Boundary occupancy exactly at 95% is a positive threshold case.
    occ_sid = occupancy_sensors[0]
    occ_zid = sensor_info[occ_sid][1]
    occ_at = AS_OF - timedelta(minutes=15)
    occ_reading = f"RDG-{occ_sid}-{token(occ_at)}"
    alarm_specs.append(_row(zone_id=occ_zid, sensor_id=occ_sid, alarm_code="OCCUPANCY_HIGH", severity="medium",
                            triggered_at=iso(occ_at), summary="Occupancy reached the 95% alert threshold.",
                            source_key=f"engine:OCCUPANCY_HIGH:{occ_sid}:{occ_reading}",
                            affected=active_at(occ_zid, occ_at), category="occupancy"))
    add_truth("OCCUPANCY-BOUNDARY-001", "sensor_reading", occ_reading, "occupancy_threshold_boundary",
              "raise_occupancy_alarm_at_exactly_95_percent", "medium", "Occupancy is exactly 95%, and the threshold is inclusive.")

    # Compatibility alerts are derived from product requirements against the
    # zone class; no anomaly flags appear in operational tables.
    for lot in incompatible_specs:
        a = lot_zone_by_id[str(lot["lot_id"])]
        ztype = str(zones_by_id[str(a["zone_id"])]["zone_type"])
        product = next(p for pp in PRODUCTS.values() for p in pp if p[0] == lot["product_code"])
        low, high = PROFILE[ztype]
        if float(product[3]) < low or float(product[2]) > high:
            code = "STORAGE_MISMATCH"
        else:
            # The allowed zone ranges are disjoint; keep a deterministic fallback.
            code = "STORAGE_MISMATCH"
        alarm_specs.append(_row(zone_id=a["zone_id"], sensor_id=None, alarm_code=code, severity="high",
                                triggered_at=AS_OF_TEXT,
                                summary=f"{lot['product_code']} requirement does not overlap the {ztype} zone profile.",
                                source_key=f"engine:STORAGE_MISMATCH:{lot['lot_id']}:{a['lot_zone_assignment_id']}",
                                affected=[lot], category="compatibility"))
        add_truth(f"INCOMPATIBLE-{lot['lot_id']}", "lot_zone_assignment", str(a["lot_zone_assignment_id"]), "incompatible_zone",
                  "raise_storage_mismatch_and_quarantine_or_move_lot", "high",
                  f"Lot requirement is incompatible with its assigned {ztype} zone.")

    # Two deliberate impact-link omissions are documented ground-truth cases.
    # The alert itself remains present and receives an inspection/remediation.
    missing_link_count = 2
    linkless_specs = [s for s in alarm_specs if s["category"] in {"temperature", "humidity"}][:missing_link_count]
    for spec in linkless_specs:
        spec["affected"] = []
        add_truth(f"MISSING-LINK-{spec['source_key']}", "alarm", str(spec["source_key"]),
                  "alarm_missing_lot_link", "reconcile_from_event_time_and_zone", "high",
                  "Alarm has no affected-lot bridge despite active inventory overlapping its zone and event time.")

    # Deterministic alarm records and append-only lifecycle history. Every alarm
    # receives an inspection and remediation; affected-lot bridge totals reconcile.
    alarms: list[dict[str, object]] = []
    alarm_lots: list[dict[str, object]] = []
    inspections: list[dict[str, object]] = []
    remediations: list[dict[str, object]] = []
    remediation_lots: list[dict[str, object]] = []
    audit_events: list[dict[str, object]] = []
    role_cycle = ["quality_engineer", "maintenance_engineer", "plant_operator", "production_planner", "ot_it_administrator"]
    audit_n = 0

    def add_audit(entity_type: str, entity_id: str, action: str, old: str | None, new: str | None,
                  at: datetime, details: dict[str, object] | None = None) -> None:
        nonlocal audit_n
        audit_n += 1
        import json
        audit_events.append(_row(audit_event_id=f"AUD-{audit_n:07d}", entity_type=entity_type, entity_id=entity_id,
                                 action=action, old_state=old, new_state=new, actor="synthetic_risk_engine",
                                 occurred_at=iso(at), details_json=json.dumps(details or {}, sort_keys=True)))

    lot_history = {
        "received": [], "stored": ["stored"], "quarantined": ["stored", "quarantined"],
        "released": ["stored", "quarantined", "released"], "shipped": ["stored", "released", "shipped"],
        "consumed": ["stored", "released", "consumed"], "expired": ["stored", "expired"],
    }
    for lot in lot_specs:
        lid = str(lot["lot_id"])
        received = lot["received_dt"]
        add_audit("inventory_lot", lid, "created", None, "received", received,
                  {"product_code": lot["product_code"], "quantity_units": lot["quantity_units"]})
        old_state = "received"
        for step, state in enumerate(lot_history[str(lot["lifecycle_state"])], 1):
            add_audit("inventory_lot", lid, "state_changed", old_state, state, received + timedelta(seconds=step),
                      {"lifecycle": "inventory"})
            old_state = state

    for i, spec in enumerate(alarm_specs, 1):
        aid = f"ALM-2026-{i:06d}"
        at = datetime.fromisoformat(str(spec["triggered_at"]).replace("Z", "+00:00"))
        status_idx = i % 5
        final_status = ("resolved", "acknowledged", "open", "inspecting", "remediating")[status_idx]
        resolved_at = iso(at + timedelta(minutes=35)) if final_status == "resolved" else None
        alarms.append(_row(alarm_id=aid, zone_id=spec["zone_id"], sensor_id=spec["sensor_id"],
                           alarm_code=spec["alarm_code"], severity=spec["severity"], triggered_at=spec["triggered_at"],
                           status=final_status, summary=spec["summary"], source_key=spec["source_key"], resolved_at=resolved_at))
        affected = list(spec["affected"])
        for lot in affected:
            alarm_lots.append(_row(alarm_id=aid, lot_id=lot["lot_id"], impact_quantity_units=lot["quantity_units"]))
        inspection_id, remediation_id = f"INSP-2026-{i:06d}", f"REM-2026-{i:06d}"
        inspection_state = "completed" if final_status in {"resolved", "remediating"} else ("in_progress" if final_status == "inspecting" else "planned")
        remediation_state = "completed" if final_status == "resolved" else ("in_progress" if final_status == "remediating" else "planned")
        created_at = at + timedelta(seconds=1)
        inspection_completed = at + timedelta(minutes=4) if inspection_state == "completed" else None
        inspections.append(_row(inspection_id=inspection_id, alarm_id=aid, zone_id=spec["zone_id"],
                                assigned_role=role_cycle[(i-1) % len(role_cycle)], status=inspection_state,
                                created_at=iso(created_at), completed_at=iso(inspection_completed) if inspection_completed else None,
                                findings="Zone, sensor telemetry, and overlapping inventory reviewed." if inspection_completed else None))
        impact_total = sum(int(l["quantity_units"]) for l in affected)
        remediation_created = at + timedelta(seconds=2)
        remediation_completed = at + timedelta(minutes=18) if remediation_state == "completed" else None
        action_type = {"SENSOR_GAP": "repair_sensor", "DOOR_OPEN_LONG": "repair_hvac",
                       "STORAGE_MISMATCH": "move_lot", "OCCUPANCY_HIGH": "other"}.get(str(spec["alarm_code"]), "adjust_setpoint")
        remediations.append(_row(remediation_id=remediation_id, inspection_id=inspection_id, action_type=action_type,
                                 status=remediation_state, inventory_impact_quantity_units=impact_total,
                                 created_at=iso(remediation_created), completed_at=iso(remediation_completed) if remediation_completed else None,
                                 notes="Corrective action planned; inventory impact follows alarm links."))
        for lot in affected:
            remediation_lots.append(_row(remediation_id=remediation_id, lot_id=lot["lot_id"],
                                         impacted_quantity_units=lot["quantity_units"]))
        add_audit("alarm", aid, "created", None, "open", at)
        alarm_path = ["acknowledged"] if final_status in {"acknowledged", "inspecting", "remediating", "resolved"} else []
        if final_status in {"inspecting", "remediating", "resolved"}:
            alarm_path.append("inspecting")
        if final_status in {"remediating", "resolved"}:
            alarm_path.append("remediating")
        if final_status == "resolved":
            alarm_path.append("resolved")
        old_state = "open"
        for step, state in enumerate(alarm_path, 1):
            add_audit("alarm", aid, "state_changed", old_state, state, at + timedelta(seconds=step), {"source_key": spec["source_key"]})
            old_state = state
        add_audit("inspection", inspection_id, "created", None, "planned", created_at, {"alarm_id": aid})
        if inspection_state in {"in_progress", "completed"}:
            add_audit("inspection", inspection_id, "state_changed", "planned", "in_progress", created_at + timedelta(seconds=1))
        if inspection_state == "completed":
            add_audit("inspection", inspection_id, "state_changed", "in_progress", "completed", inspection_completed)
        add_audit("remediation", remediation_id, "created", None, "planned", remediation_created, {"inspection_id": inspection_id})
        if remediation_state == "in_progress":
            add_audit("remediation", remediation_id, "state_changed", "planned", "in_progress", remediation_created + timedelta(seconds=1))
        elif remediation_state == "completed":
            add_audit("remediation", remediation_id, "state_changed", "planned", "completed", remediation_completed)

    truths = incident_truth + [
        _row(truth_id=f"GT-{truth_n+i+1:06d}", scenario_id=f"MAPPING-{i+1:03d}", entity_type="sensor_zone_assignment",
             entity_id=assignment_id, issue_type="unjustified_sensor_mapping", expected_decision="review_and_correct_active_zone_mapping",
             expected_severity="medium", rationale="Sensor has overlapping active zone mappings with no assignment rationale.")
        for i, (sid, assignment_id) in enumerate(duplicate_assignment_ids)
    ]
    source_to_alarm_id = {str(spec["source_key"]): f"ALM-2026-{i:06d}" for i, spec in enumerate(alarm_specs, 1)}
    for truth in truths:
        if truth["issue_type"] == "alarm_missing_lot_link":
            truth["entity_id"] = source_to_alarm_id.get(str(truth["entity_id"]), truth["entity_id"])

    # Ensure exact observed-gap boundary truth is repeated in full and starter.
    rows: dict[str, list[dict[str, object]] | Callable[[], Iterable[dict[str, object]]]] = {
        "warehouse": warehouses, "zone": zones, "storage_requirement": requirements, "sensor": sensors,
        "sensor_zone_assignment": sensor_assignment, "door_cycle": door_cycles,
        "inventory_lot": [{k: v for k, v in lot.items() if k in TABLE_COLUMNS["inventory_lot"]} for lot in lot_specs],
        "lot_zone_assignment": assignments,
        "sensor_outage": [
            _row(outage_id=f"OUT-2026-{i+1:06d}", sensor_id=p["sensor_id"], started_at=iso(p["start"]), ended_at=iso(p["end"]),
                 expected_interval_seconds=900, reason="telemetry sample gap") for i, p in enumerate(outage_plans)
        ],
        "sensor_reading": reading_rows,
        "door_event": door_events, "alarm": alarms, "alarm_affected_lot": alarm_lots,
        "inspection": inspections, "remediation": remediations,
        "remediation_affected_lot": remediation_lots, "audit_event": audit_events,
    }
    counts = {table: (len(value) if not callable(value) else 0) for table, value in rows.items()}
    counts["sensor_reading"] = sensor_count_per_zone * len(zones) * reading_count
    # Sensor outages remove samples; the healthy balancing probe receives the same
    # number of historical rows, so the requested exact scale remains intact.
    counts["sensor_reading"] += balance_extra - missing_total
    return Dataset(scale=scale, seed=seed, expected_counts=counts, _rows=rows, truth_rows=truths)
