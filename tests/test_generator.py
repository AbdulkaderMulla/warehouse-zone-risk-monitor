import csv
import itertools
import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from backend.seed_data import TABLE_COLUMNS, build_dataset
from scripts.generate_data import checksum_dataset, emit


class GeneratorTests(unittest.TestCase):
    def test_starter_is_reproducible_and_covers_rules_and_exceptions(self):
        first = build_dataset("starter", 20261022)
        second = build_dataset("starter", 20261022)
        self.assertEqual(checksum_dataset(first), checksum_dataset(second))
        self.assertEqual(first.expected_counts["warehouse"], 3)
        self.assertEqual(first.expected_counts["zone"], 12)
        self.assertEqual(first.expected_counts["sensor"], 36)
        self.assertEqual(first.expected_counts["sensor_reading"], 36 * 70)
        self.assertEqual(first.expected_counts["inventory_lot"], 120)
        self.assertNotIn("issue_type", {column for columns in TABLE_COLUMNS.values() for column in columns})
        issue_counts = Counter(row["issue_type"] for row in first.truth_rows)
        for issue in ("incompatible_zone", "unjustified_sensor_mapping", "reading_gap_treated_as_normal", "alarm_missing_lot_link"):
            self.assertGreaterEqual(issue_counts[issue], 2)
        self.assertGreaterEqual(issue_counts["temperature_threshold_boundary"], 2)
        self.assertGreaterEqual(issue_counts["humidity_threshold_boundary"], 2)

    def test_full_volume_parameters_and_streaming_reading_shape(self):
        dataset = build_dataset("full", 20261022)
        self.assertEqual(dataset.expected_counts["warehouse"], 10)
        self.assertEqual(dataset.expected_counts["zone"], 100)
        self.assertEqual(dataset.expected_counts["sensor"], 500)
        self.assertEqual(dataset.expected_counts["sensor_reading"], 1_000_000)
        self.assertEqual(dataset.expected_counts["inventory_lot"], 10_000)
        self.assertEqual(dataset.expected_counts["door_event"], 5_000)
        sample = list(itertools.islice(dataset.rows("sensor_reading"), 1000))
        self.assertEqual(len(sample), 1000)
        self.assertTrue(all(row["reading_id"].startswith("RDG-") for row in sample))
        self.assertEqual(len({row["reading_id"] for row in sample}), 1000)

    def test_all_exports_validate_without_label_leakage(self):
        dataset = build_dataset("starter", 20261022)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "export"
            manifest = emit(dataset, output, {"csv", "json", "sql"})
            self.assertEqual(manifest["validation"]["status"], "passed", manifest["validation"]["errors"])
            self.assertTrue((output / "ground_truth.csv").is_file())
            self.assertTrue((output / "ground_truth.json").is_file())
            data = json.loads((output / "dataset.json").read_text())
            self.assertEqual(len(data["warehouse"]), 3)
            self.assertNotIn("ground_truth", data)
            self.assertNotIn("issue_type", data["alarm"][0])
            self.assertNotIn("issue_type", data["sensor_reading"][0])
            self.assertNotIn("issue_type", data["door_event"][0])
            seeded = sqlite3.connect(":memory:")
            seeded.executescript((output / "seed.sql").read_text())
            self.assertEqual(seeded.execute("SELECT COUNT(*) FROM sensor_reading").fetchone()[0], 36 * 70)
            self.assertEqual(seeded.execute("SELECT COUNT(*) FROM ground_truth").fetchone()[0], len(dataset.truth_rows))
            self.assertEqual(seeded.execute("PRAGMA foreign_key_check").fetchall(), [])
            door_types = {row[0] for row in seeded.execute("SELECT DISTINCT event_type FROM door_event")}
            self.assertEqual(door_types, {"open", "closed", "ajar"})
            self.assertEqual(seeded.execute("SELECT COUNT(*) FROM inspection").fetchone()[0], seeded.execute("SELECT COUNT(*) FROM alarm").fetchone()[0])
            self.assertEqual(seeded.execute("SELECT COUNT(*) FROM remediation").fetchone()[0], seeded.execute("SELECT COUNT(*) FROM alarm").fetchone()[0])
            operational = [table for table in TABLE_COLUMNS]
            for table in operational:
                columns = {row[1] for row in seeded.execute(f"PRAGMA table_info({table})")}
                self.assertNotIn("issue_type", columns, table)
            seeded.close()


if __name__ == "__main__":
    unittest.main()
