"""SQLite log of webhook bodies."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class EventStore:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT,
                site_id TEXT,
                zone_id TEXT,
                received_at TEXT NOT NULL,
                body TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def add(self, body: dict) -> bool:
        event_id = str(body.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("event_id is required")
        site = body.get("site") if isinstance(body.get("site"), dict) else {}
        zone = body.get("zone") if isinstance(body.get("zone"), dict) else {}
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO events
                        (event_id, event_type, site_id, zone_id, received_at, body)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        body.get("event_type"),
                        site.get("id"),
                        zone.get("id"),
                        _now(),
                        json.dumps(body, separators=(",", ":"), ensure_ascii=False),
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                return False
        return True

    def list_events(
        self,
        *,
        limit: int,
        event_type: str | None = None,
        zone_id: str | None = None,
    ) -> list[dict]:
        sql = "SELECT received_at, body FROM events WHERE 1=1"
        args: list[object] = []
        if event_type:
            sql += " AND event_type = ?"
            args.append(event_type)
        if zone_id:
            sql += " AND zone_id = ?"
            args.append(zone_id)
        sql += " ORDER BY received_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [
            {"received_at": row["received_at"], "body": json.loads(row["body"])} for row in rows
        ]

    def get(self, event_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT received_at, body FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return {"received_at": row["received_at"], "body": json.loads(row["body"])}

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        return int(row["n"])

    def clear(self) -> int:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM events")
            self._conn.commit()
            return int(cursor.rowcount)
