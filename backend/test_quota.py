"""Checks for the usage limits on /api/plan.

Run from backend/:  python test_quota.py

The pipeline is replaced with a stub that returns a fixed plan: what is
under test is who gets through the door, not what they get once inside.
"""

from __future__ import annotations

import tempfile
import threading
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.config import Settings, get_settings
from app.models import DayPlan, ItineraryItem, TripPlan
from app.poi_repository import attractions_by_id
from app.quota import Limit, Quota, QuotaExceeded, local_day

START = date.today() + timedelta(days=10)
REQUEST = {
    "destination": "Rotorua",
    "start_date": START.isoformat(),
    "end_date": (START + timedelta(days=1)).isoformat(),
    "preferences": ["nature"],
    "budget_level": "mid_range",
}

_a = list(attractions_by_id("Rotorua").values())[0]
PLAN = TripPlan(
    destination="Rotorua", start_date=START, end_date=START + timedelta(days=1),
    days=[DayPlan(day=1, date=START, summary="A stubbed day for the test.",
                  items=[ItineraryItem(attraction=_a, time_slot="morning",
                                       note="Stubbed stop.")])],
)
STUB_STATE = {"plan": PLAN, "errors": [], "timings": {}}

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if condition else 'FAIL'} {label}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(label)


def client_with(**settings) -> tuple[TestClient, Quota]:
    """A client against a fresh quota database and the given settings."""
    db = Path(tempfile.mkdtemp()) / "quota.sqlite"
    quota = Quota(db)
    cfg = Settings(**{"demo_access_code": "", "rate_limit_generate": "off",
                      "daily_generation_cap": 0, "access_code_attempts": "off",
                      **settings})
    main.app.dependency_overrides = {get_settings: lambda: cfg,
                                     main.get_quota: lambda: quota}
    return TestClient(main.app), quota


def plan(client: TestClient, code: str | bytes = ""):
    return client.post("/api/plan", json=REQUEST, headers={"X-Access-Code": code})


with patch.object(main, "run_pipeline", return_value=STUB_STATE):

    print("1. wrong codes are limited, and a lockout outlasts a correct code")
    c, _ = client_with(demo_access_code="kea-2026", access_code_attempts="10/hour")
    codes = [plan(c, f"guess-{i}").status_code for i in range(10)]
    check("first ten wrong codes get 401", codes == [401] * 10, str(sorted(set(codes))))
    r = plan(c, "guess-10")
    check("eleventh wrong code gets 429", r.status_code == 429, str(r.status_code))
    check("429 carries Retry-After", "retry-after" in r.headers, r.headers.get("retry-after", "-"))
    r = plan(c, "kea-2026")
    check("the correct code is refused during lockout", r.status_code == 429, str(r.status_code))

    print("2. a wrong code spends nothing from the generation budget")
    c, q = client_with(demo_access_code="kea-2026", access_code_attempts="10/hour",
                       rate_limit_generate="3/hour", daily_generation_cap=30)
    for i in range(5):
        plan(c, f"guess-{i}")
    check("per-address budget untouched",
          q.peek("plan-ip", "testclient", Limit.parse("3/hour")) == 0)
    check("daily budget untouched",
          q.peek("plan-day", local_day(), Limit(30, 2 * 86400)) == 0)
    ok = [plan(c, "kea-2026").status_code for _ in range(3)]
    check("three generations still available afterwards", ok == [200] * 3, str(ok))

    print("3. per-address limit")
    c, _ = client_with(rate_limit_generate="3/hour")
    statuses = [plan(c).status_code for _ in range(4)]
    check("three succeed, the fourth is refused", statuses == [200, 200, 200, 429], str(statuses))
    r = plan(c)
    retry = int(r.headers.get("retry-after", "0"))
    check("Retry-After is within the hour", 0 < retry <= 3600, str(retry))
    check("message points at editing as the alternative", "editing" in r.json()["detail"])

    print("4. daily cap, reset at NZ midnight")
    c, _ = client_with(daily_generation_cap=2)
    statuses = [plan(c).status_code for _ in range(3)]
    check("two succeed, the third is refused", statuses == [200, 200, 429], str(statuses))
    retry = int(plan(c).headers.get("retry-after", "0"))
    check("Retry-After is the time to midnight, not a fixed window",
          0 < retry <= 86400, f"{retry}s ≈ {retry/3600:.1f}h")

    print("5. limits can be switched off for local development")
    c, _ = client_with(rate_limit_generate="off", daily_generation_cap=0)
    statuses = [plan(c).status_code for _ in range(8)]
    check("eight in a row with limits off", statuses == [200] * 8, str(sorted(set(statuses))))

    print("5b. an absent code is refused but not charged as a guess")
    c, q = client_with(demo_access_code="kea-2026", access_code_attempts="10/hour")
    statuses = [plan(c, "").status_code for _ in range(15)]
    check("fifteen empty submissions are all 401, never 429",
          statuses == [401] * 15, str(sorted(set(statuses))))
    check("none counted against the attempt limit",
          q.peek("code-fail", "testclient", Limit.parse("10/hour")) == 0)

    print("5c. the access routes")
    c, _ = client_with(demo_access_code="kea-2026")
    check("/api/access reports a code is required",
          c.get("/api/access").json() == {"required": True})
    c2, _ = client_with(demo_access_code="")
    check("/api/access reports none required when unset",
          c2.get("/api/access").json() == {"required": False})
    c, q = client_with(demo_access_code="kea-2026", access_code_attempts="3/hour",
                       rate_limit_generate="3/hour", daily_generation_cap=30)
    verify = lambda code: c.post("/api/access/verify", headers={"X-Access-Code": code})
    check("verify accepts the right code", verify("kea-2026").status_code == 200)
    check("verify spends no generation budget",
          q.peek("plan-ip", "testclient", Limit.parse("3/hour")) == 0
          and q.peek("plan-day", local_day(), Limit(30, 2 * 86400)) == 0)
    wrong = [verify(f"guess-{i}").status_code for i in range(4)]
    check("wrong codes on verify share the attempt limit",
          wrong == [401, 401, 401, 429], str(wrong))
    check("and the lockout then applies to /api/plan too",
          plan(c, "kea-2026").status_code == 429)

    print("6. a non-ASCII access code is a 401, not a 500")
    c, _ = client_with(demo_access_code="kea-2026")
    r = c.post("/api/plan", json=REQUEST, headers={"X-Access-Code": "café".encode("latin-1")})
    check("non-ASCII header rejected cleanly", r.status_code == 401, str(r.status_code))

main.app.dependency_overrides = {}

print("7. the window rolls over")
q = Quota(Path(tempfile.mkdtemp()) / "q.sqlite")
lim = Limit(count=2, window_s=60)
q.hit("b", "k", lim, "x", now=1000)
q.hit("b", "k", lim, "x", now=1010)
try:
    q.hit("b", "k", lim, "x", now=1020); rolled = False
except QuotaExceeded as e:
    rolled = True; check("third inside the window refused", True, f"retry in {e.retry_after_s}s")
check("refused inside the window", rolled)
check("counted afresh once the window has passed", q.hit("b", "k", lim, "x", now=1061) == 1)

print("8. concurrent requests cannot overshoot the cap")
q = Quota(Path(tempfile.mkdtemp()) / "q.sqlite")
lim = Limit(count=5, window_s=3600)
admitted, lock = [], threading.Lock()

def attempt():
    try:
        q.hit("plan-day", "today", lim, "x")
        with lock:
            admitted.append(1)
    except QuotaExceeded:
        pass

threads = [threading.Thread(target=attempt) for _ in range(40)]
for t in threads: t.start()
for t in threads: t.join()
check("exactly five of forty admitted", len(admitted) == 5, str(len(admitted)))

print("9. spec parsing")
check("'3/hour'", Limit.parse("3/hour") == Limit(3, 3600))
check("'10 / minutes'", Limit.parse("10 / minutes") == Limit(10, 60))
check("'off' disables", Limit.parse("off") is None)
try:
    Limit.parse("three per hour"); check("rejects nonsense", False)
except ValueError:
    check("rejects nonsense", True)

print()
print("all checks passed" if not failures else f"{len(failures)} FAILED: {failures}")
raise SystemExit(1 if failures else 0)
