"""SQLite storage setup and deterministic starter seeding."""
from __future__ import annotations

import csv
import os
import sqlite3
from pathlib import Path

from backend.seed_data import TABLE_COLUMNS, TRUTH_COLUMNS, build_dataset

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "warehouse_monitor.db"


def db_path() -> Path:
    return Path(os.environ.get("WAREHOUSE_DB_PATH", str(DEFAULT_DB))).expanduser().resolve()


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_file = path or db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_file, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 20000")
    return connection


def _insert_csv(connection: sqlite3.Connection, csv_path: Path, table: str, columns: list[str], batch_size: int = 5000) -> None:
    """Load a generated CSV incrementally to keep full-scale imports memory-safe."""
    marks = ",".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks})"
    with csv_path.open("r", newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != columns:
            raise ValueError(f"Unexpected columns in {csv_path}: {reader.fieldnames}")
        batch = []
        for row in reader:
            batch.append(tuple(row[column] if row[column] != "" else None for column in columns))
            if len(batch) >= batch_size:
                connection.executemany(sql, batch)
                batch.clear()
        if batch:
            connection.executemany(sql, batch)


def seed_database(connection: sqlite3.Connection, scale: str = "starter", seed: int = 20261022) -> None:
    if scale == "full":
        # The repository ships the validated full-scale CSV exports. Stream these
        # into SQLite rather than building one million Python row dictionaries.
        full_dir = ROOT / "data" / "generated" / "full"
        missing = [table for table in TABLE_COLUMNS if not (full_dir / f"{table}.csv").is_file()]
        if missing or not (full_dir / "ground_truth.csv").is_file():
            raise FileNotFoundError(
                "Full-scale CSV exports are missing. Run scripts/generate_data.py "
                "--scale full --format csv --output data/generated/full first."
            )
        with connection:
            for table, columns in TABLE_COLUMNS.items():
                _insert_csv(connection, full_dir / f"{table}.csv", table, columns)
            _insert_csv(connection, full_dir / "ground_truth.csv", "ground_truth", TRUTH_COLUMNS)
        return

    dataset = build_dataset(scale, seed)
    with connection:
        for table in dataset.table_names:
            columns = TABLE_COLUMNS[table]
            marks = ",".join("?" for _ in columns)
            sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks})"
            connection.executemany(sql, (tuple(row[col] for col in columns) for row in dataset.rows(table)))
        marks = ",".join("?" for _ in dataset.truth_columns)
        connection.executemany(
            f"INSERT INTO ground_truth ({','.join(dataset.truth_columns)}) VALUES ({marks})",
            (tuple(row[col] for col in dataset.truth_columns) for row in dataset.truth_rows),
        )


def initialize(path: Path | None = None, seed_demo: bool = True, scale: str = "starter", seed: int = 20261022) -> sqlite3.Connection:
    connection = connect(path)
    schema = (ROOT / "database" / "schema.sql").read_text(encoding="utf-8")
    connection.executescript(schema)
    count = connection.execute("SELECT COUNT(*) FROM warehouse").fetchone()[0]
    if seed_demo and count == 0:
        seed_database(connection, scale=scale, seed=seed)
    return connection
