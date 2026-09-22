"""Shared SQLite response cache.

One table, one helper. Every external lookup that is stable enough to
reuse goes through `cached_fetch`, so POI, routing, and weather calls all
share the same eviction and inspection story rather than each growing
their own ad-hoc cache.

Namespaces in use:
    poi      — pre-fetched Overpass results, keyed by destination
    route    — OSRM legs, keyed by rounded coordinate pair
    weather  — Open-Meteo daily forecasts, keyed by destination + date
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    namespace  TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    created_at REAL NOT NULL,
    ttl_s      REAL,
    PRIMARY KEY (namespace, key)
);
"""


class Cache:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, namespace: str, key: str) -> Optional[Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value, created_at, ttl_s FROM cache WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()
        if row is None:
            return None
        if row["ttl_s"] is not None and time.time() - row["created_at"] > row["ttl_s"]:
            self.delete(namespace, key)
            return None
        return json.loads(row["value"])

    def set(self, namespace: str, key: str, value: Any, ttl_s: Optional[float] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (namespace, key, value, created_at, ttl_s) "
                "VALUES (?, ?, ?, ?, ?)",
                (namespace, key, json.dumps(value), time.time(), ttl_s),
            )

    def delete(self, namespace: str, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM cache WHERE namespace=? AND key=?", (namespace, key))

    def keys(self, namespace: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key FROM cache WHERE namespace=? ORDER BY key", (namespace,)
            ).fetchall()
        return [r["key"] for r in rows]

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT namespace, COUNT(*) AS n FROM cache GROUP BY namespace"
            ).fetchall()
        return {r["namespace"]: r["n"] for r in rows}


def cached_fetch(
    cache: Cache,
    namespace: str,
    key: str,
    fetcher: Callable[[], Any],
    ttl_s: Optional[float] = None,
) -> Any:
    """Return the cached value, or call `fetcher` and store its result.

    `fetcher` is only invoked on a miss, which is what keeps runtime traffic
    to external services near zero for pre-fetched destinations.
    """
    hit = cache.get(namespace, key)
    if hit is not None:
        return hit
    value = fetcher()
    cache.set(namespace, key, value, ttl_s=ttl_s)
    return value


def route_key(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> str:
    """Coordinates rounded to ~11 m so trivially different requests share a hit."""
    return f"{from_lat:.4f},{from_lon:.4f}->{to_lat:.4f},{to_lon:.4f}"
