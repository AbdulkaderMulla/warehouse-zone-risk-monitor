import json
import os
import tempfile
import unittest
import io
from pathlib import Path

from backend.db import initialize
from backend.rules import complete_inspection, complete_remediation, create_remediation, dashboard, evaluate, reconcile_impact, risk_zones, start_inspection
from backend.server import Handler


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "test.sqlite"
        self.previous_db = os.environ.get("WAREHOUSE_DB_PATH")
        os.environ["WAREHOUSE_DB_PATH"] = str(self.db_path)
        self.addCleanup(self._restore_db_path)
        self.db = initialize(self.db_path)
        self.addCleanup(self.db.close)

    def _restore_db_path(self):
        if self.previous_db is None:
            os.environ.pop("WAREHOUSE_DB_PATH", None)
        else:
            os.environ["WAREHOUSE_DB_PATH"] = self.previous_db

    def test_generated_alerts_are_idempotent_and_dashboard_measures_coverage(self):
        result = evaluate(self.db)
        self.assertEqual(result["created"], 0)
        self.assertEqual(evaluate(self.db)["created"], 0)
        metrics = dashboard(self.db)
        self.assertEqual(metrics["warehouse_count"], 3)
        self.assertEqual(metrics["zone_count"], 12)
        self.assertEqual(metrics["sensor_count"], 36)
        self.assertEqual(metrics["stale_sensor_count"], 0)
        self.assertEqual(metrics["missing_impact_link_count"], 2)
        self.assertGreater(metrics["impacted_lot_count"], 0)
        zone = next(item for item in risk_zones(self.db) if item["zone_id"] == "ZONE-WH-001-001")
        self.assertIsNotNone(zone["latest_temp_c"])
        self.assertIsNotNone(zone["latest_humidity_pct"])
        self.assertIsNotNone(zone["latest_occupancy_pct"])
        self.assertIsNotNone(zone["last_door_duration_seconds"])

    def test_missing_impact_reconciliation_uses_zone_and_half_open_time_overlap(self):
        alarm_id = self.db.execute("""SELECT a.alarm_id FROM alarm a WHERE a.status NOT IN ('resolved','false_positive')
            AND NOT EXISTS (SELECT 1 FROM alarm_affected_lot x WHERE x.alarm_id=a.alarm_id) ORDER BY a.alarm_id LIMIT 1""").fetchone()[0]
        result = reconcile_impact(self.db, alarm_id, "quality_engineer")
        self.assertTrue(result["added"])
        self.assertGreater(result["linked_lot_count"], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM alarm_affected_lot WHERE alarm_id=?", (alarm_id,)).fetchone()[0], result["linked_lot_count"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_event WHERE entity_id=? AND action='impact_links_added'", (alarm_id,)).fetchone()[0], 1)

    def test_inspection_remediation_lifecycle_and_inventory_reconciliation(self):
        inspection_id = self.db.execute("""SELECT i.inspection_id FROM inspection i JOIN alarm a ON a.alarm_id=i.alarm_id
            WHERE i.status='planned' AND a.status IN ('open','acknowledged')
              AND EXISTS (SELECT 1 FROM alarm_affected_lot l WHERE l.alarm_id=a.alarm_id)
            ORDER BY i.inspection_id LIMIT 1""").fetchone()[0]
        started = start_inspection(self.db, inspection_id, "quality_engineer")
        self.assertEqual(started["status"], "in_progress")
        complete_inspection(self.db, inspection_id, "Door seal and cooling recovery checked.", "quality_engineer")
        planned = create_remediation(self.db, inspection_id, "repair_hvac", "Repair and verify the cooling controller.", "maintenance_engineer")
        self.assertGreater(planned["inventory_impact_quantity_units"], 0)
        alarm_id = self.db.execute("SELECT alarm_id FROM inspection WHERE inspection_id=?", (inspection_id,)).fetchone()[0]
        self.assertEqual(self.db.execute("SELECT status FROM alarm WHERE alarm_id=?", (alarm_id,)).fetchone()[0], "remediating")
        completed = complete_remediation(self.db, planned["remediation_id"], "maintenance_engineer")
        self.assertEqual(completed["alarm_status"], "resolved")
        alarm = self.db.execute("SELECT status,resolved_at FROM alarm WHERE alarm_id=?", (alarm_id,)).fetchone()
        self.assertEqual(alarm["status"], "resolved")
        self.assertIsNotNone(alarm["resolved_at"])
        recorded = self.db.execute("SELECT inventory_impact_quantity_units FROM remediation WHERE remediation_id=?", (planned["remediation_id"],)).fetchone()[0]
        bridge_total = self.db.execute("SELECT COALESCE(SUM(impacted_quantity_units),0) FROM remediation_affected_lot WHERE remediation_id=?", (planned["remediation_id"],)).fetchone()[0]
        self.assertEqual(recorded, bridge_total)

    def test_http_api_exposes_decisions_but_not_ground_truth(self):
        class FakeHandler(Handler):
            def __init__(self, path, body=b""):
                self.path = path
                self.headers = {"Content-Length": str(len(body))}
                self.rfile = io.BytesIO(body)
                self.wfile = io.BytesIO()
                self.status = None
            def send_response(self, status, message=None):
                self.status = status
            def send_header(self, key, value):
                pass
            def end_headers(self):
                pass
        get = FakeHandler("/api/dashboard")
        get.do_GET()
        payload = json.loads(get.wfile.getvalue())
        self.assertEqual(payload["sensor_count"], 36)
        self.assertNotIn("ground_truth", json.dumps(payload))
        zones_get = FakeHandler("/api/zones")
        zones_get.do_GET()
        zones = json.loads(zones_get.wfile.getvalue())
        self.assertIn("latest_temp_c", zones[0])
        self.assertNotIn("issue_type", json.dumps(zones))
        alarm_id = self.db.execute("SELECT alarm_id FROM alarm WHERE status='open' ORDER BY alarm_id LIMIT 1").fetchone()[0]
        post = FakeHandler(f"/api/alarms/{alarm_id}/acknowledge", b'{"actor":"plant_operator"}')
        post.do_POST()
        changed = json.loads(post.wfile.getvalue())
        self.assertEqual(post.status, 200)
        self.assertEqual(changed["status"], "acknowledged")
        health_get = FakeHandler("/api/health")
        health_get.do_GET()
        health = json.loads(health_get.wfile.getvalue())
        self.assertEqual(health["foreign_key_violations"], 0)


if __name__ == "__main__":
    unittest.main()
