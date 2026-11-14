#!/usr/bin/env python3
"""Validate generated CSV data against the relational schema and manifest."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.generate_data import run_validation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", nargs="?", type=Path, default=ROOT / "data" / "demo")
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    manifest_path = data_dir / "source_manifest.json"
    truth_path = data_dir / "ground_truth.csv"
    if not manifest_path.is_file() or not truth_path.is_file():
        parser.error("data directory must contain source_manifest.json and ground_truth.csv")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with truth_path.open(newline="", encoding="utf-8") as stream:
        truth_rows = list(csv.DictReader(stream))
    result = run_validation(data_dir, truth_rows, manifest["scale"], manifest["counts"])
    summary_path = data_dir / "validation_summary.json"
    summary_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"data_dir": str(data_dir), **result}, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
