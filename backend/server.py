"""Small, dependency-free HTTP API and static web server."""
from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from backend.db import DEFAULT_DB, connect, initialize
from backend.rules import alarms_list, complete_inspection, complete_remediation, create_inspection, create_remediation, dashboard, evaluate, exceptions, reconcile_impact, risk_lots, risk_zones, start_inspection

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class Handler(BaseHTTPRequestHandler):
    server_version = "ZoneRiskMonitor/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def _send(self, status: int, payload: object, content_type: str = "application/json; charset=utf-8") -> None:
        body = payload if isinstance(payload, bytes) else (json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = min(int(self.headers.get("Content-Length", "0") or 0), 16_384)
        if not length:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            db = connect()
            try:
                if path == "/api/health":
                    fk = db.execute("PRAGMA foreign_key_check").fetchall()
                    counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("warehouse", "zone", "sensor", "sensor_reading", "door_event", "inventory_lot", "alarm", "inspection", "remediation")}
                    return self._send(200, {"status": "ok" if not fk else "degraded", "foreign_key_violations": len(fk), "counts": counts})
                if path == "/api/dashboard":
                    return self._send(200, dashboard(db))
                if path == "/api/alarms":
                    status = parse_qs(parsed.query).get("status", [None])[0]
                    return self._send(200, alarms_list(db, status))
                if path == "/api/zones":
                    return self._send(200, risk_zones(db))
                if path == "/api/lots/at-risk":
                    return self._send(200, risk_lots(db))
                if path == "/api/exceptions":
                    return self._send(200, exceptions(db))
                if path == "/api/audit":
                    limit = max(1, min(500, int(parse_qs(parsed.query).get("limit", ["100"])[0])))
                    rows = db.execute("SELECT * FROM audit_event ORDER BY occurred_at DESC,audit_event_id DESC LIMIT ?", (limit,)).fetchall()
                    return self._send(200, [dict(row) for row in rows])
                return self._send(404, {"error": "API route not found"})
            except Exception as exc:
                return self._send(500, {"error": str(exc)})
            finally:
                db.close()
        self._serve_frontend(path)

    def _serve_frontend(self, path: str) -> None:
        filename = "index.html" if path in ("", "/") else path.lstrip("/")
        candidate = (FRONTEND / filename).resolve()
        if not candidate.is_relative_to(FRONTEND.resolve()) or not candidate.is_file():
            return self._send(404, b"Not found\n", "text/plain; charset=utf-8")
        self._send(200, candidate.read_bytes(), mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")

    def do_POST(self):
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        db = connect()
        try:
            body = self._body()
            if parts == ["api", "evaluate"]:
                return self._send(200, evaluate(db))
            if len(parts) == 4 and parts[:2] == ["api", "alarms"] and parts[3] == "acknowledge":
                from backend.rules import state_change
                return self._send(200, state_change(db, parts[2], "acknowledged", body.get("actor", "plant_operator")))
            if len(parts) == 4 and parts[:2] == ["api", "alarms"] and parts[3] == "inspect":
                return self._send(201, create_inspection(db, parts[2], body.get("assigned_role", "quality_engineer"), body.get("actor", "plant_operator")))
            if len(parts) == 4 and parts[:2] == ["api", "alarms"] and parts[3] == "reconcile-impact":
                return self._send(200, reconcile_impact(db, parts[2], body.get("actor", "quality_engineer")))
            if len(parts) == 4 and parts[:2] == ["api", "inspections"] and parts[3] == "remediations":
                return self._send(201, create_remediation(db, parts[2], body.get("action_type", "verify_calibration"), body.get("notes", ""), body.get("actor", "quality_engineer")))
            if len(parts) == 4 and parts[:2] == ["api", "inspections"] and parts[3] == "complete":
                return self._send(200, complete_inspection(db, parts[2], body.get("findings", "Inspection completed."), body.get("actor", "quality_engineer")))
            if len(parts) == 4 and parts[:2] == ["api", "inspections"] and parts[3] == "start":
                return self._send(200, start_inspection(db, parts[2], body.get("actor", "quality_engineer")))
            if len(parts) == 4 and parts[:2] == ["api", "remediations"] and parts[3] == "complete":
                return self._send(200, complete_remediation(db, parts[2], body.get("actor", "quality_engineer")))
            return self._send(404, {"error": "API route not found"})
        except KeyError as exc:
            return self._send(404, {"error": str(exc).strip("'")})
        except (ValueError, json.JSONDecodeError) as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:
            return self._send(500, {"error": str(exc)})
        finally:
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the warehouse zone risk monitor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--empty", action="store_true", help="Initialize schema without seed data")
    parser.add_argument("--scale", choices=("starter", "full"), default="starter", help="Dataset scale to load when the database is empty")
    parser.add_argument("--seed", type=int, default=20261022, help="Deterministic starter-data seed")
    args = parser.parse_args()
    # Keep server handlers on the same configured path without mutating global env.
    import os
    os.environ["WAREHOUSE_DB_PATH"] = str(args.database.resolve())
    db = initialize(args.database, seed_demo=not args.empty, scale=args.scale, seed=args.seed)
    db.close()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Warehouse Zone Risk Monitor running at http://{args.host}:{args.port}")
    print(f"SQLite database: {args.database.resolve()}")
    print(f"Dataset scale: {args.scale if not args.empty else 'empty'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
