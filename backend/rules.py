"""Risk rules, traceability queries, and append-only workflow transitions."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
FRESHNESS_SECONDS = 45 * 60
LONG_DOOR_SECONDS = 180
ROLE_VALUES = {"plant_operator", "quality_engineer", "maintenance_engineer", "production_planner", "ot_it_administrator"}


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def as_dict(row: sqlite3.Row | None):
    return dict(row) if row else None


def latest_data_time(db: sqlite3.Connection) -> str:
    row = db.execute("SELECT MAX(captured_at) FROM sensor_reading WHERE quality_flag='good'").fetchone()
    return row[0] or ""


def active_lots_in_zone(db: sqlite3.Connection, zone_id: str, at: str) -> list[sqlite3.Row]:
    return db.execute(
        """SELECT l.lot_id,l.product_code,l.quantity_units,l.lifecycle_state,
                  r.min_temp_c,r.max_temp_c,r.min_humidity_pct,r.max_humidity_pct,r.max_dwell_hours,
                  a.effective_from,a.effective_to,a.dwell_hours
           FROM lot_zone_assignment a JOIN inventory_lot l ON l.lot_id=a.lot_id
           JOIN storage_requirement r ON r.product_code=l.product_code
           WHERE a.zone_id=? AND a.effective_from<=? AND (a.effective_to IS NULL OR a.effective_to>?)
             AND l.lifecycle_state IN ('stored','quarantined','released')""",
        (zone_id, at, at),
    ).fetchall()


def lots_overlapping_interval(db: sqlite3.Connection, zone_id: str, start: str, end: str) -> list[sqlite3.Row]:
    return db.execute(
        """SELECT DISTINCT l.lot_id,l.quantity_units
           FROM lot_zone_assignment a JOIN inventory_lot l ON l.lot_id=a.lot_id
           WHERE a.zone_id=? AND l.lifecycle_state IN ('stored','quarantined','released')
             AND a.effective_from<? AND (a.effective_to IS NULL OR a.effective_to>?)""",
        (zone_id, end, start),
    ).fetchall()


def _audit(db: sqlite3.Connection, entity_type: str, entity_id: str, action: str,
           old_state: str | None, new_state: str | None, actor: str, at: str, details: dict | None = None) -> None:
    next_id = db.execute("SELECT COUNT(*)+1 FROM audit_event").fetchone()[0]
    db.execute(
        "INSERT INTO audit_event VALUES (?,?,?,?,?,?,?,?,?)",
        (f"AUD-APP-{next_id:07d}", entity_type, entity_id, action, old_state, new_state, actor, at, json.dumps(details or {}, sort_keys=True)),
    )


def _insert_alarm(db: sqlite3.Connection, *, source_key: str, zone_id: str, sensor_id: str | None,
                  code: str, severity: str, at: str, summary: str, lots: list[sqlite3.Row]) -> bool:
    if db.execute("SELECT 1 FROM alarm WHERE source_key=?", (source_key,)).fetchone():
        return False
    n = db.execute("SELECT COUNT(*)+1 FROM alarm").fetchone()[0]
    alarm_id = f"ALM-APP-{n:06d}"
    db.execute(
        "INSERT INTO alarm(alarm_id,zone_id,sensor_id,alarm_code,severity,triggered_at,status,summary,source_key,resolved_at) VALUES(?,?,?,?,?,?,?,?,?,NULL)",
        (alarm_id, zone_id, sensor_id, code, severity, at, "open", summary, source_key),
    )
    for lot in lots:
        db.execute("INSERT OR IGNORE INTO alarm_affected_lot(alarm_id,lot_id,impact_quantity_units) VALUES(?,?,?)", (alarm_id, lot["lot_id"], int(lot["quantity_units"])))
    _audit(db, "alarm", alarm_id, "created", None, "open", "risk_engine", at, {"source_key": source_key, "code": code, "affected_lot_count": len(lots)})
    return True


def evaluate(db: sqlite3.Connection) -> dict[str, int]:
    """Evaluate quality-approved observations and append idempotent alarm records."""
    anchor = latest_data_time(db)
    if not anchor:
        return {"created": 0, "temperature_humidity": 0, "door": 0, "sensor_gap": 0, "compatibility": 0}
    created = {"temperature_humidity": 0, "door": 0, "sensor_gap": 0, "compatibility": 0}
    mappings = db.execute(
        """SELECT sz.sensor_id,MIN(sz.zone_id) AS zone_id,COUNT(DISTINCT sz.zone_id) AS zone_count
           FROM sensor_zone_assignment sz JOIN sensor s ON s.sensor_id=sz.sensor_id
           WHERE sz.effective_from<=? AND (sz.effective_to IS NULL OR sz.effective_to>?) AND s.lifecycle_state='active'
           GROUP BY sz.sensor_id""", (anchor, anchor),
    ).fetchall()
    unique_mapping = {row["sensor_id"]: row["zone_id"] for row in mappings if row["zone_count"] == 1}
    sensors = db.execute("SELECT sensor_id,sensor_type FROM sensor WHERE lifecycle_state='active'").fetchall()
    zone_profiles = {"frozen": (-25.0, -12.0), "cold": (0.0, 8.0), "ambient": (15.0, 28.0), "hot": (28.0, 40.0)}
    for sensor in sensors:
        sid = sensor["sensor_id"]
        zid = unique_mapping.get(sid)
        if not zid:
            continue
        if sensor["sensor_type"] in {"TEMP", "HUMIDITY"}:
            zone_type = db.execute("SELECT zone_type FROM zone WHERE zone_id=?", (zid,)).fetchone()[0]
            zone_low, zone_high = zone_profiles[zone_type]
            readings = db.execute("""SELECT reading_id,captured_at,value FROM sensor_reading
                WHERE sensor_id=? AND quality_flag='good' ORDER BY captured_at""", (sid,)).fetchall()
            in_episode = False
            last_code = None
            for reading in readings:
                all_lots = active_lots_in_zone(db, zid, reading["captured_at"])
                # Storage mismatch has its own alert rule. Do not multiply that
                # placement exception into temperature/humidity alarms.
                lots = [lot for lot in all_lots if lot["max_temp_c"] >= zone_low and lot["min_temp_c"] <= zone_high]
                value = float(reading["value"])
                if sensor["sensor_type"] == "TEMP":
                    breached = [lot for lot in lots if value < lot["min_temp_c"] or value > lot["max_temp_c"]]
                    high = any(value > lot["max_temp_c"] for lot in breached)
                    code = "TEMP_HIGH" if high else "TEMP_LOW"
                    severity = "critical" if breached and abs(value - (max(l["max_temp_c"] for l in breached) if high else min(l["min_temp_c"] for l in breached))) >= 7 else "high"
                    summary = f"Temperature reading {value:.1f} C is outside the active lot requirement."
                else:
                    breached = [lot for lot in lots if value < lot["min_humidity_pct"] or value > lot["max_humidity_pct"]]
                    high = any(value > lot["max_humidity_pct"] for lot in breached)
                    code = "HUMIDITY_HIGH" if high else "HUMIDITY_LOW"
                    severity = "high"
                    summary = f"Humidity reading {value:.1f}% RH is outside the active lot requirement."
                if not breached:
                    in_episode = False
                    last_code = None
                    continue
                if in_episode and code == last_code:
                    continue
                key = f"engine:{code}:{sid}:{reading['reading_id']}"
                if _insert_alarm(db, source_key=key, zone_id=zid, sensor_id=sid, code=code, severity=severity,
                                 at=reading["captured_at"], summary=summary, lots=breached):
                    created["temperature_humidity"] += 1
                in_episode, last_code = True, code
        elif sensor["sensor_type"] == "OCCUPANCY":
            readings = db.execute("""SELECT reading_id,captured_at,value FROM sensor_reading
                WHERE sensor_id=? AND quality_flag='good' ORDER BY captured_at""", (sid,)).fetchall()
            prior_high = False
            for reading in readings:
                high = float(reading["value"]) >= 95
                if high and not prior_high:
                    key = f"engine:OCCUPANCY_HIGH:{sid}:{reading['reading_id']}"
                    if _insert_alarm(db, source_key=key, zone_id=zid, sensor_id=sid, code="OCCUPANCY_HIGH", severity="medium",
                                     at=reading["captured_at"], summary=f"Occupancy reached {float(reading['value']):.0f}% of zone capacity.",
                                     lots=active_lots_in_zone(db, zid, reading["captured_at"])):
                        created["temperature_humidity"] += 1
                prior_high = high

    for outage in db.execute("SELECT outage_id,sensor_id,started_at,ended_at FROM sensor_outage ORDER BY started_at").fetchall():
        gap = (dt(outage["ended_at"]) - dt(outage["started_at"])).total_seconds()
        zid = unique_mapping.get(outage["sensor_id"])
        if gap <= FRESHNESS_SECONDS or not zid:
            continue
        at = iso(dt(outage["started_at"]) + timedelta(seconds=FRESHNESS_SECONDS))
        lots = lots_overlapping_interval(db, zid, outage["started_at"], outage["ended_at"])
        key = f"engine:SENSOR_GAP:{outage['sensor_id']}:{outage['outage_id']}"
        if _insert_alarm(db, source_key=key, zone_id=zid, sensor_id=outage["sensor_id"], code="SENSOR_GAP", severity="high",
                         at=at, summary=f"Observed sensor gap lasted {gap/60:.0f} minutes; freshness limit is 45 minutes.", lots=lots):
            created["sensor_gap"] += 1

    window_start = iso(dt(anchor) - timedelta(hours=24))
    for event in db.execute("""SELECT door_event_id,zone_id,occurred_at,duration_seconds FROM door_event
          WHERE event_type='open' AND occurred_at>=? AND occurred_at<=? AND duration_seconds>=?""",
          (window_start, anchor, LONG_DOOR_SECONDS)).fetchall():
        lots = active_lots_in_zone(db, event["zone_id"], event["occurred_at"])
        key = f"engine:DOOR_OPEN_LONG:{event['door_event_id']}"
        if _insert_alarm(db, source_key=key, zone_id=event["zone_id"], sensor_id=None, code="DOOR_OPEN_LONG", severity="high",
                         at=event["occurred_at"], summary=f"Door-open duration was {event['duration_seconds']} seconds.", lots=lots):
            created["door"] += 1

    profiles = {"frozen": (-25.0, -12.0), "cold": (0.0, 8.0), "ambient": (15.0, 28.0), "hot": (28.0, 40.0)}
    assignments = db.execute("""SELECT a.lot_id,a.zone_id,a.lot_zone_assignment_id,a.dwell_hours,l.quantity_units,l.product_code,
          z.zone_type,r.min_temp_c,r.max_temp_c,r.max_dwell_hours FROM lot_zone_assignment a
          JOIN inventory_lot l ON l.lot_id=a.lot_id JOIN zone z ON z.zone_id=a.zone_id
          JOIN storage_requirement r ON r.product_code=l.product_code
          WHERE a.effective_from<=? AND (a.effective_to IS NULL OR a.effective_to>?)
            AND l.lifecycle_state IN ('stored','quarantined','released')""", (anchor, anchor)).fetchall()
    for row in assignments:
        low, high = profiles[row["zone_type"]]
        mismatched = row["max_temp_c"] < low or row["min_temp_c"] > high
        lot_stub = [{"lot_id": row["lot_id"], "quantity_units": row["quantity_units"]}]
        if mismatched:
            key = f"engine:STORAGE_MISMATCH:{row['lot_id']}:{row['lot_zone_assignment_id']}"
            if _insert_alarm(db, source_key=key, zone_id=row["zone_id"], sensor_id=None, code="STORAGE_MISMATCH", severity="high",
                             at=anchor, summary=f"{row['product_code']} requirement does not overlap the {row['zone_type']} zone profile.", lots=lot_stub):
                created["compatibility"] += 1
        if float(row["dwell_hours"]) > float(row["max_dwell_hours"]):
            key = f"engine:DWELL_LIMIT:{row['lot_id']}:{row['lot_zone_assignment_id']}"
            if _insert_alarm(db, source_key=key, zone_id=row["zone_id"], sensor_id=None, code="DWELL_LIMIT", severity="high",
                             at=anchor, summary=f"Lot has remained in zone for {row['dwell_hours']:.1f} hours; limit is {row['max_dwell_hours']:.0f} hours.", lots=lot_stub):
                created["compatibility"] += 1
    created["created"] = sum(created.values())
    return created


def dashboard(db: sqlite3.Connection) -> dict:
    anchor = latest_data_time(db)
    total_sensors = db.execute("SELECT COUNT(*) FROM sensor WHERE lifecycle_state='active'").fetchone()[0]
    fresh = 0
    if anchor:
        fresh = db.execute(
            """SELECT COUNT(*) FROM sensor s JOIN (
                 SELECT sensor_id,MAX(captured_at) AS last_at FROM sensor_reading WHERE quality_flag='good' GROUP BY sensor_id
               ) r ON r.sensor_id=s.sensor_id WHERE s.lifecycle_state='active' AND julianday(r.last_at)>=julianday(?)-(45.0/1440.0)""", (anchor,),
        ).fetchone()[0]
    active_alarm_count = db.execute("SELECT COUNT(*) FROM alarm WHERE status IN ('open','acknowledged','inspecting','remediating')").fetchone()[0]
    impacted = db.execute("""SELECT COUNT(DISTINCT al.lot_id),COALESCE(SUM(DISTINCT al.impact_quantity_units),0)
                             FROM alarm_affected_lot al JOIN alarm a ON a.alarm_id=al.alarm_id
                             WHERE a.status IN ('open','acknowledged','inspecting','remediating')""").fetchone()
    # Sum quantity by distinct lot instead of double counting a lot across alarms.
    units = db.execute("""SELECT COALESCE(SUM(quantity_units),0) FROM inventory_lot WHERE lot_id IN
                          (SELECT DISTINCT al.lot_id FROM alarm_affected_lot al JOIN alarm a ON a.alarm_id=al.alarm_id
                           WHERE a.status IN ('open','acknowledged','inspecting','remediating'))""").fetchone()[0]
    total_zones = db.execute("SELECT COUNT(*) FROM zone WHERE lifecycle_state='active'").fetchone()[0]
    missing_links = db.execute("SELECT COUNT(*) FROM alarm a WHERE NOT EXISTS (SELECT 1 FROM alarm_affected_lot l WHERE l.alarm_id=a.alarm_id) AND a.status NOT IN ('resolved','false_positive')").fetchone()[0]
    sensor_mapping_exceptions = db.execute("""SELECT COUNT(*) FROM (
        SELECT sensor_id FROM sensor_zone_assignment WHERE effective_from<=? AND (effective_to IS NULL OR effective_to>?)
        GROUP BY sensor_id HAVING COUNT(DISTINCT zone_id)>1)""", (anchor, anchor)).fetchone()[0] if anchor else 0
    incompatible_lots = db.execute("""SELECT COUNT(*) FROM lot_zone_assignment a
        JOIN inventory_lot l ON l.lot_id=a.lot_id JOIN zone z ON z.zone_id=a.zone_id
        JOIN storage_requirement r ON r.product_code=l.product_code
        WHERE ((z.zone_type='cold' AND (r.max_temp_c<0 OR r.min_temp_c>8))
           OR (z.zone_type='frozen' AND (r.max_temp_c<-25 OR r.min_temp_c>-12))
           OR (z.zone_type='ambient' AND (r.max_temp_c<15 OR r.min_temp_c>28))
           OR (z.zone_type='hot' AND (r.max_temp_c<28 OR r.min_temp_c>40)))""").fetchone()[0]
    stale_count = max(0, total_sensors - fresh)
    open_inspections = db.execute("SELECT COUNT(*) FROM inspection WHERE status IN ('planned','in_progress')").fetchone()[0]
    completed_inspections = db.execute("SELECT COUNT(*) FROM inspection WHERE status='completed'").fetchone()[0]
    avg_ack_seconds = db.execute("""SELECT AVG((julianday(e.occurred_at)-julianday(a.triggered_at))*86400)
        FROM audit_event e JOIN alarm a ON e.entity_type='alarm' AND e.entity_id=a.alarm_id
        WHERE e.action='state_changed' AND e.new_state='acknowledged'""").fetchone()[0]
    alarms = db.execute("SELECT COUNT(*) FROM alarm").fetchone()[0]
    return {
        "as_of": anchor,
        "warehouse_count": db.execute("SELECT COUNT(*) FROM warehouse WHERE lifecycle_state='active'").fetchone()[0],
        "zone_count": total_zones,
        "sensor_count": total_sensors,
        "fresh_sensor_count": fresh,
        "stale_sensor_count": stale_count,
        "sensor_coverage_pct": round((fresh / total_sensors * 100) if total_sensors else 0, 1),
        "active_alarm_count": active_alarm_count,
        "critical_alarm_count": db.execute("SELECT COUNT(*) FROM alarm WHERE severity='critical' AND status IN ('open','acknowledged','inspecting','remediating')").fetchone()[0],
        "impacted_lot_count": int(impacted[0] or 0),
        "impacted_units": int(units or 0),
        "open_inspection_count": open_inspections,
        "completed_inspection_count": completed_inspections,
        "alarm_count": alarms,
        "missing_impact_link_count": missing_links,
        "ambiguous_sensor_mapping_count": sensor_mapping_exceptions,
        "incompatible_lot_count": incompatible_lots,
        "average_ack_minutes": round(avg_ack_seconds / 60, 1) if avg_ack_seconds is not None else None,
    }


def alarms_list(db: sqlite3.Connection, status: str | None = None) -> list[dict]:
    where, args = ("WHERE a.status=?", [status]) if status else ("", [])
    rows = db.execute(
        f"""SELECT a.alarm_id,a.zone_id,z.name AS zone_name,w.warehouse_id,w.name AS warehouse_name,
                   a.sensor_id,a.alarm_code,a.severity,a.triggered_at,a.status,a.summary,
                   COUNT(DISTINCT l.lot_id) AS impacted_lot_count,
                   COALESCE(SUM(DISTINCT l.impact_quantity_units),0) AS linked_units,
                   GROUP_CONCAT(DISTINCT l.lot_id) AS lot_ids,
                   (SELECT i.inspection_id FROM inspection i WHERE i.alarm_id=a.alarm_id ORDER BY i.created_at DESC LIMIT 1) AS inspection_id,
                   (SELECT i.status FROM inspection i WHERE i.alarm_id=a.alarm_id ORDER BY i.created_at DESC LIMIT 1) AS inspection_status,
                   (SELECT r.status FROM remediation r JOIN inspection i ON i.inspection_id=r.inspection_id WHERE i.alarm_id=a.alarm_id ORDER BY r.created_at DESC LIMIT 1) AS remediation_status,
                   (SELECT r.remediation_id FROM remediation r JOIN inspection i ON i.inspection_id=r.inspection_id WHERE i.alarm_id=a.alarm_id ORDER BY r.created_at DESC LIMIT 1) AS remediation_id
            FROM alarm a JOIN zone z ON z.zone_id=a.zone_id JOIN warehouse w ON w.warehouse_id=z.warehouse_id
            LEFT JOIN alarm_affected_lot l ON l.alarm_id=a.alarm_id {where}
            GROUP BY a.alarm_id ORDER BY CASE a.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, a.triggered_at DESC""", args,
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["linked_units"] = int(db.execute("""SELECT COALESCE(SUM(quantity_units),0) FROM inventory_lot WHERE lot_id IN
                                        (SELECT lot_id FROM alarm_affected_lot WHERE alarm_id=?)""", (item["alarm_id"],)).fetchone()[0])
        item["lot_ids"] = [v for v in (item["lot_ids"] or "").split(",") if v]
        item["has_impact_link"] = item["impacted_lot_count"] > 0
        result.append(item)
    return result


def risk_zones(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """SELECT z.zone_id,z.name AS zone_name,z.zone_type,w.warehouse_id,w.name AS warehouse_name,
                  COUNT(DISTINCT a.alarm_id) AS active_alarm_count,
                  COUNT(DISTINCT CASE WHEN a.severity IN ('high','critical') THEN a.alarm_id END) AS high_risk_count,
                  COUNT(DISTINCT al.lot_id) AS impacted_lots,
                  (SELECT MAX(sr.captured_at) FROM sensor_zone_assignment sza JOIN sensor_reading sr ON sr.sensor_id=sza.sensor_id WHERE sza.zone_id=z.zone_id AND sr.quality_flag='good') AS last_reading_at,
                  (SELECT sr.value FROM sensor_zone_assignment sza JOIN sensor s ON s.sensor_id=sza.sensor_id
                     JOIN sensor_reading sr ON sr.sensor_id=s.sensor_id WHERE sza.zone_id=z.zone_id AND s.sensor_type='TEMP' AND sr.quality_flag='good'
                     ORDER BY sr.captured_at DESC LIMIT 1) AS latest_temp_c,
                  (SELECT sr.value FROM sensor_zone_assignment sza JOIN sensor s ON s.sensor_id=sza.sensor_id
                     JOIN sensor_reading sr ON sr.sensor_id=s.sensor_id WHERE sza.zone_id=z.zone_id AND s.sensor_type='HUMIDITY' AND sr.quality_flag='good'
                     ORDER BY sr.captured_at DESC LIMIT 1) AS latest_humidity_pct,
                  (SELECT sr.value FROM sensor_zone_assignment sza JOIN sensor s ON s.sensor_id=sza.sensor_id
                     JOIN sensor_reading sr ON sr.sensor_id=s.sensor_id WHERE sza.zone_id=z.zone_id AND s.sensor_type='OCCUPANCY' AND sr.quality_flag='good'
                     ORDER BY sr.captured_at DESC LIMIT 1) AS latest_occupancy_pct,
                  (SELECT d.occurred_at FROM door_event d WHERE d.zone_id=z.zone_id AND d.event_type='open' ORDER BY d.occurred_at DESC LIMIT 1) AS last_door_at,
                  (SELECT d.duration_seconds FROM door_event d WHERE d.zone_id=z.zone_id AND d.event_type='open' ORDER BY d.occurred_at DESC LIMIT 1) AS last_door_duration_seconds
           FROM zone z JOIN warehouse w ON w.warehouse_id=z.warehouse_id
           LEFT JOIN alarm a ON a.zone_id=z.zone_id AND a.status IN ('open','acknowledged','inspecting','remediating')
           LEFT JOIN alarm_affected_lot al ON al.alarm_id=a.alarm_id
           WHERE z.lifecycle_state='active' GROUP BY z.zone_id
           ORDER BY high_risk_count DESC,active_alarm_count DESC,z.zone_id"""
    ).fetchall()
    return [dict(row) for row in rows]


def risk_lots(db: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in db.execute(
        """SELECT DISTINCT l.lot_id,l.product_code,r.product_name,l.quantity_units,l.lifecycle_state,
                  z.zone_id,z.name AS zone_name,z.zone_type,w.name AS warehouse_name,
                  a.severity,a.alarm_code,a.triggered_at,al.impact_quantity_units
           FROM alarm_affected_lot al JOIN alarm a ON a.alarm_id=al.alarm_id
           JOIN inventory_lot l ON l.lot_id=al.lot_id JOIN storage_requirement r ON r.product_code=l.product_code
           JOIN zone z ON z.zone_id=a.zone_id JOIN warehouse w ON w.warehouse_id=z.warehouse_id
           WHERE a.status IN ('open','acknowledged','inspecting','remediating')
           ORDER BY CASE a.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,a.triggered_at DESC,l.lot_id""").fetchall()]


def exceptions(db: sqlite3.Connection) -> list[dict]:
    results = []
    for row in db.execute("""SELECT a.alarm_id AS record_id,'alarm_missing_lot_link' AS issue_type,
                                   'high' AS severity,'Alarm has no affected inventory lot link' AS description,
                                   a.triggered_at AS occurred_at,a.zone_id
                            FROM alarm a WHERE a.status NOT IN ('resolved','false_positive')
                              AND NOT EXISTS (SELECT 1 FROM alarm_affected_lot x WHERE x.alarm_id=a.alarm_id)"""):
        results.append(dict(row))
    anchor = latest_data_time(db)
    if anchor:
        for row in db.execute("""SELECT sensor_id,GROUP_CONCAT(DISTINCT zone_id) AS zone_ids
                FROM sensor_zone_assignment WHERE effective_from<=? AND (effective_to IS NULL OR effective_to>?)
                GROUP BY sensor_id HAVING COUNT(DISTINCT zone_id)>1""", (anchor, anchor)):
            results.append({"record_id": row["sensor_id"], "issue_type": "unjustified_sensor_mapping", "severity": "medium", "description": f"Sensor maps to multiple active zones ({row['zone_ids']}); rationale and effective dates require review.", "occurred_at": anchor, "zone_id": None})
    for row in db.execute("""SELECT a.lot_id,a.zone_id,a.effective_from,z.zone_type,r.product_code
            FROM lot_zone_assignment a JOIN inventory_lot l ON l.lot_id=a.lot_id JOIN zone z ON z.zone_id=a.zone_id
            JOIN storage_requirement r ON r.product_code=l.product_code WHERE
            (z.zone_type='cold' AND (r.max_temp_c<0 OR r.min_temp_c>8)) OR
            (z.zone_type='frozen' AND (r.max_temp_c<-25 OR r.min_temp_c>-12)) OR
            (z.zone_type='ambient' AND (r.max_temp_c<15 OR r.min_temp_c>28)) OR
            (z.zone_type='hot' AND (r.max_temp_c<28 OR r.min_temp_c>40))"""):
        results.append({"record_id": row["lot_id"], "issue_type": "incompatible_zone", "severity": "high", "description": f"{row['product_code']} is assigned to a {row['zone_type']} zone.", "occurred_at": row["effective_from"], "zone_id": row["zone_id"]})
    return sorted(results, key=lambda r: (r["severity"], r["occurred_at"]), reverse=True)


def state_change(db: sqlite3.Connection, alarm_id: str, target: str, actor: str = "plant_operator") -> dict:
    allowed = {"open": {"acknowledged", "inspecting", "false_positive", "resolved"}, "acknowledged": {"inspecting", "remediating", "resolved"}, "inspecting": {"remediating", "resolved"}, "remediating": {"resolved"}, "resolved": set(), "false_positive": set()}
    row = db.execute("SELECT status,triggered_at FROM alarm WHERE alarm_id=?", (alarm_id,)).fetchone()
    if not row:
        raise KeyError("Alarm not found")
    if target not in allowed.get(row["status"], set()):
        raise ValueError(f"Transition {row['status']} -> {target} is not allowed")
    at = iso(datetime.now(UTC))
    with db:
        db.execute("UPDATE alarm SET status=?,resolved_at=? WHERE alarm_id=?", (target, at if target in {"resolved", "false_positive"} else None, alarm_id))
        _audit(db, "alarm", alarm_id, "state_changed", row["status"], target, actor, at)
    return {"alarm_id": alarm_id, "old_status": row["status"], "status": target, "occurred_at": at}


def create_inspection(db: sqlite3.Connection, alarm_id: str, role: str, actor: str) -> dict:
    if role not in ROLE_VALUES:
        raise ValueError("Unknown inspection role")
    alarm = db.execute("SELECT zone_id,status FROM alarm WHERE alarm_id=?", (alarm_id,)).fetchone()
    if not alarm:
        raise KeyError("Alarm not found")
    if alarm["status"] not in {"open", "acknowledged"}:
        raise ValueError("An inspection can only start from an open or acknowledged alarm")
    n = db.execute("SELECT COUNT(*)+1 FROM inspection").fetchone()[0]
    inspection_id = f"INSP-APP-{n:06d}"
    at = iso(datetime.now(UTC))
    with db:
        db.execute("INSERT INTO inspection VALUES(?,?,?,?,?,?,NULL,NULL)", (inspection_id, alarm_id, alarm["zone_id"], role, "in_progress", at))
        db.execute("UPDATE alarm SET status='inspecting' WHERE alarm_id=?", (alarm_id,))
        _audit(db, "inspection", inspection_id, "created", None, "in_progress", actor, at, {"alarm_id": alarm_id, "assigned_role": role})
        _audit(db, "alarm", alarm_id, "state_changed", alarm["status"], "inspecting", actor, at, {"inspection_id": inspection_id})
    return {"inspection_id": inspection_id, "alarm_id": alarm_id, "status": "in_progress", "assigned_role": role}


def start_inspection(db: sqlite3.Connection, inspection_id: str, actor: str) -> dict:
    row = db.execute("SELECT i.status,i.alarm_id,a.status AS alarm_status FROM inspection i JOIN alarm a ON a.alarm_id=i.alarm_id WHERE i.inspection_id=?", (inspection_id,)).fetchone()
    if not row:
        raise KeyError("Inspection not found")
    if row["status"] != "planned":
        raise ValueError("Only a planned inspection can be started")
    if row["alarm_status"] not in {"open", "acknowledged"}:
        raise ValueError("Alarm is not in a startable state")
    at = iso(datetime.now(UTC))
    with db:
        db.execute("UPDATE inspection SET status='in_progress' WHERE inspection_id=?", (inspection_id,))
        _audit(db, "inspection", inspection_id, "state_changed", "planned", "in_progress", actor, at)
        db.execute("UPDATE alarm SET status='inspecting' WHERE alarm_id=?", (row["alarm_id"],))
        _audit(db, "alarm", row["alarm_id"], "state_changed", row["alarm_status"], "inspecting", actor, at, {"inspection_id": inspection_id})
    return {"inspection_id": inspection_id, "alarm_id": row["alarm_id"], "status": "in_progress"}


def complete_inspection(db: sqlite3.Connection, inspection_id: str, findings: str, actor: str) -> dict:
    row = db.execute("SELECT status,created_at FROM inspection WHERE inspection_id=?", (inspection_id,)).fetchone()
    if not row:
        raise KeyError("Inspection not found")
    if row["status"] not in {"planned", "in_progress"}:
        raise ValueError("Inspection is already final")
    at = iso(datetime.now(UTC))
    with db:
        db.execute("UPDATE inspection SET status='completed',completed_at=?,findings=? WHERE inspection_id=?", (at, findings[:2000], inspection_id))
        _audit(db, "inspection", inspection_id, "completed", row["status"], "completed", actor, at)
    return {"inspection_id": inspection_id, "status": "completed", "completed_at": at}


def create_remediation(db: sqlite3.Connection, inspection_id: str, action_type: str, notes: str, actor: str) -> dict:
    allowed = {"repair_sensor", "repair_hvac", "adjust_setpoint", "quarantine_lot", "move_lot", "verify_calibration", "other"}
    if action_type not in allowed:
        raise ValueError("Unknown remediation type")
    inspection = db.execute("SELECT i.status,i.alarm_id,a.status AS alarm_status FROM inspection i JOIN alarm a ON a.alarm_id=i.alarm_id WHERE i.inspection_id=?", (inspection_id,)).fetchone()
    if not inspection:
        raise KeyError("Inspection not found")
    if inspection["status"] != "completed":
        raise ValueError("Complete the inspection before planning remediation")
    links = db.execute("SELECT lot_id,impact_quantity_units FROM alarm_affected_lot WHERE alarm_id=?", (inspection["alarm_id"],)).fetchall()
    n = db.execute("SELECT COUNT(*)+1 FROM remediation").fetchone()[0]
    remediation_id = f"REM-APP-{n:06d}"
    at = iso(datetime.now(UTC))
    total = sum(int(link["impact_quantity_units"]) for link in links)
    with db:
        db.execute("INSERT INTO remediation VALUES(?,?,?,'planned',?,?,NULL,?)", (remediation_id, inspection_id, action_type, total, at, notes[:2000]))
        for link in links:
            if link["impact_quantity_units"] > 0:
                db.execute("INSERT INTO remediation_affected_lot VALUES(?,?,?)", (remediation_id, link["lot_id"], link["impact_quantity_units"]))
        _audit(db, "remediation", remediation_id, "created", None, "planned", actor, at, {"inspection_id": inspection_id, "impacted_lot_count": len(links), "inventory_impact_quantity_units": total})
        if inspection["alarm_status"] == "inspecting":
            db.execute("UPDATE alarm SET status='remediating' WHERE alarm_id=?", (inspection["alarm_id"],))
            _audit(db, "alarm", inspection["alarm_id"], "state_changed", "inspecting", "remediating", actor, at, {"remediation_id": remediation_id})
    return {"remediation_id": remediation_id, "inspection_id": inspection_id, "status": "planned", "impacted_lot_count": len(links), "inventory_impact_quantity_units": total}


def complete_remediation(db: sqlite3.Connection, remediation_id: str, actor: str) -> dict:
    row = db.execute("""SELECT r.status,r.inspection_id,i.alarm_id,a.status AS alarm_status
                       FROM remediation r JOIN inspection i ON i.inspection_id=r.inspection_id
                       JOIN alarm a ON a.alarm_id=i.alarm_id WHERE r.remediation_id=?""", (remediation_id,)).fetchone()
    if not row:
        raise KeyError("Remediation not found")
    if row["status"] not in {"planned", "in_progress"}:
        raise ValueError("Remediation is already final")
    if row["alarm_status"] != "remediating":
        raise ValueError("Alarm must be in remediating state")
    at = iso(datetime.now(UTC))
    with db:
        db.execute("UPDATE remediation SET status='completed',completed_at=? WHERE remediation_id=?", (at, remediation_id))
        _audit(db, "remediation", remediation_id, "state_changed", row["status"], "completed", actor, at)
        db.execute("UPDATE alarm SET status='resolved',resolved_at=? WHERE alarm_id=?", (at, row["alarm_id"]))
        _audit(db, "alarm", row["alarm_id"], "state_changed", "remediating", "resolved", actor, at, {"remediation_id": remediation_id})
    return {"remediation_id": remediation_id, "alarm_id": row["alarm_id"], "status": "completed", "alarm_status": "resolved", "completed_at": at}


def reconcile_impact(db: sqlite3.Connection, alarm_id: str, actor: str) -> dict:
    alarm = db.execute("SELECT zone_id,triggered_at FROM alarm WHERE alarm_id=?", (alarm_id,)).fetchone()
    if not alarm:
        raise KeyError("Alarm not found")
    existing = db.execute("SELECT COUNT(*) FROM alarm_affected_lot WHERE alarm_id=?", (alarm_id,)).fetchone()[0]
    if existing:
        return {"alarm_id": alarm_id, "linked_lot_count": existing, "added": False}
    lots = active_lots_in_zone(db, alarm["zone_id"], alarm["triggered_at"])
    if not lots:
        raise ValueError("No active lots overlap this zone at the alarm time")
    at = iso(datetime.now(UTC))
    with db:
        for lot in lots:
            db.execute("INSERT OR IGNORE INTO alarm_affected_lot VALUES(?,?,?)", (alarm_id, lot["lot_id"], lot["quantity_units"]))
        _audit(db, "alarm", alarm_id, "impact_links_added", None, None, actor, at, {"method": "zone_and_time_overlap", "linked_lot_ids": [lot["lot_id"] for lot in lots]})
    return {"alarm_id": alarm_id, "linked_lot_count": len(lots), "linked_lot_ids": [lot["lot_id"] for lot in lots], "added": True}
