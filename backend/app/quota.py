"""Usage limits for the one route that spends money.

`/api/plan` makes two model calls per request, and once the app is on a
public address the access code is the only other thing standing between a
stranger and the API bill. Three limits sit behind it:

    plan-ip     generations per client address per window   (config: rate_limit_generate)
    plan-day    generations across everyone per NZ calendar day   (daily_generation_cap)
    code-fail   wrong access codes per client address per window  (access_code_attempts)

The last exists because a gate that can be tried indefinitely is only a
delay. Without it, a four-character code falls to a loop.

Counts live in SQLite, in the same file as the response cache, rather than
in process memory. A restart must not hand out a fresh day's budget, and
the counts must agree across however many workers are serving requests.
Each increment is a single upsert with RETURNING, so two concurrent
requests cannot both read "29" and both proceed to 30.

Windows are fixed rather than sliding. A sliding window is fairer at the
edges, but the point here is a ceiling on cost, not a smooth rate, and a
fixed window is one row per key.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# The day the daily cap resets on is the operator's day, not UTC's: a cap
# that rolls over at noon Auckland time is harder to reason about.
LOCAL_TZ = ZoneInfo("Pacific/Auckland")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quota (
    bucket        TEXT NOT NULL,
    key           TEXT NOT NULL,
    window_start  REAL NOT NULL,
    count         INTEGER NOT NULL,
    PRIMARY KEY (bucket, key)
);
"""

# Both SET expressions read the pre-update row, which is SQLite's rule for
# UPDATE: the window test uses the old start even though the next line may
# be replacing it.
_HIT = """
INSERT INTO quota (bucket, key, window_start, count) VALUES (?, ?, ?, 1)
ON CONFLICT (bucket, key) DO UPDATE SET
    count = CASE
        WHEN excluded.window_start - quota.window_start >= ? THEN 1
        ELSE quota.count + 1
    END,
    window_start = CASE
        WHEN excluded.window_start - quota.window_start >= ? THEN excluded.window_start
        ELSE quota.window_start
    END
RETURNING count, window_start
"""

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


@dataclass(frozen=True)
class Limit:
    count: int
    window_s: float

    @classmethod
    def parse(cls, spec: str) -> "Limit | None":
        """Read "3/hour", "10 / minute" and so on; blank or "off" disables."""
        spec = spec.strip().lower()
        if spec in ("", "off", "none", "0"):
            return None
        match = re.fullmatch(r"(\d+)\s*/\s*(second|minute|hour|day)s?", spec)
        if not match:
            raise ValueError(f"unreadable rate limit {spec!r}; expected e.g. '3/hour'")
        return cls(count=int(match.group(1)), window_s=_UNITS[match.group(2)])


class QuotaExceeded(Exception):
    """A limit was reached. Carries what the client should be told."""

    def __init__(self, message: str, retry_after_s: float):
        super().__init__(message)
        self.retry_after_s = max(1, int(retry_after_s))


class Quota:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # A generous lock timeout: under a burst, a request that waits a
        # moment for the write lock is better than one that fails.
        return sqlite3.connect(self.db_path, timeout=10)

    def hit(self, bucket: str, key: str, limit: Limit, message: str,
            now: float | None = None) -> int:
        """Count one use; raise QuotaExceeded if this one is over the limit.

        The use is counted even when it is refused. That is deliberate for
        the access-code bucket — each wrong guess must cost something — and
        harmless for the others, since a refused request never reaches the
        model.
        """
        now = time.time() if now is None else now
        with self._connect() as conn:
            count, window_start = conn.execute(
                _HIT, (bucket, key, now, limit.window_s, limit.window_s)
            ).fetchone()
        if count > limit.count:
            raise QuotaExceeded(message, window_start + limit.window_s - now)
        return count

    def peek(self, bucket: str, key: str, limit: Limit,
             now: float | None = None) -> int:
        """The count in the current window, without adding to it."""
        now = time.time() if now is None else now
        with self._connect() as conn:
            row = conn.execute(
                "SELECT count, window_start FROM quota WHERE bucket=? AND key=?",
                (bucket, key),
            ).fetchone()
        if row is None or now - row[1] >= limit.window_s:
            return 0
        return row[0]

    def retry_after(self, bucket: str, key: str, limit: Limit,
                    now: float | None = None) -> float:
        now = time.time() if now is None else now
        with self._connect() as conn:
            row = conn.execute(
                "SELECT window_start FROM quota WHERE bucket=? AND key=?",
                (bucket, key),
            ).fetchone()
        return 0 if row is None else row[0] + limit.window_s - now


def local_day(now: float | None = None) -> str:
    """Today's date in Auckland, as the key for the daily cap."""
    moment = datetime.fromtimestamp(time.time() if now is None else now, LOCAL_TZ)
    return moment.date().isoformat()


def seconds_to_local_midnight(now: float | None = None) -> float:
    moment = datetime.fromtimestamp(time.time() if now is None else now, LOCAL_TZ)
    midnight = (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - moment).total_seconds()
