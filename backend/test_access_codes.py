"""Checks for trial access codes.

Run from backend/:  python test_access_codes.py
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.access_codes import AccessCodes, main as cli
from app.config import Settings, get_settings
from app.quota import Quota, Limit

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if condition else 'FAIL'} {label}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(label)


def fresh() -> AccessCodes:
    return AccessCodes(Path(tempfile.mkdtemp()) / "codes.sqlite")


print("1. issuing")
store = fresh()
code, rec = store.issue("Prof Smith", days=14)
check("the code is 12 random characters", len(code) == 12, code)
check("two codes differ", store.issue("Someone else")[0] != code)
check("a short id is returned, and it is not the code",
      len(rec.id) == 6 and rec.id not in code, rec.id)
check("nothing stores the code in the clear",
      all(code not in str(r.__dict__) for r in store.all())
      and code not in Path(store.db_path).read_bytes().decode("latin-1"))

print("2. checking")
check("the right code is accepted", store.check(code) is not None)
check("an unknown code is not", store.check("not-a-real-code") is None)
check("an empty code is not", store.check("") is None)
check("use is counted", [r for r in store.all() if r.id == rec.id][0].uses == 1)
check("last used is recorded",
      [r for r in store.all() if r.id == rec.id][0].last_used_at is not None)

print("3. expiry and revocation")
store = fresh()
short, _ = store.issue("Expires tomorrow", days=1)
check("valid before expiry", store.check(short) is not None)
check("refused after expiry",
      store.check(short, now=time.time() + 2 * 86400) is None)
check("status reads 'expired'",
      store.all()[0].status(time.time() + 2 * 86400) == "expired")

store = fresh()
doomed, drec = store.issue("To be revoked")
check("works before revocation", store.check(doomed) is not None)
store.revoke(drec.id)
check("refused after revocation", store.check(doomed) is None)
check("status reads 'revoked'", store.all()[0].status() == "revoked")
check("revoking an unknown id says so", store.revoke("zzzzzz") is None)
check("a revoked code still appears in the listing, with its label",
      store.all()[0].label == "To be revoked")

print("4. has_active — what the deployment would fall back to")
store = fresh()
check("none issued", store.has_active() is False)
c, r = store.issue("only one", days=1)
check("one live", store.has_active() is True)
store.revoke(r.id)
check("revoked does not count", store.has_active() is False)

print("5. through the API")
db = Path(tempfile.mkdtemp()) / "api.sqlite"
codes = AccessCodes(db)
quota = Quota(db)
good, good_rec = codes.issue("Reviewer", days=7)
gone, gone_rec = codes.issue("Ex-reviewer")
codes.revoke(gone_rec.id)
# Issued already expired, rather than moving the clock: patching
# app.access_codes.time.time patches the attribute on the time module
# itself, so every module sees it — including the rate limiter, whose
# window then rolled over and reset the count this test is reading.
stale, _ = codes.issue("Lapsed", days=-1)

cfg = Settings(demo_access_code="master-code", rate_limit_generate="off",
               daily_generation_cap=0, access_code_attempts="10/hour")
main.app.dependency_overrides = {get_settings: lambda: cfg,
                                 main.get_quota: lambda: quota,
                                 main.get_codes: lambda: codes}
client = TestClient(main.app)
verify = lambda code: client.post("/api/access/verify", headers={"X-Access-Code": code})

check("an issued code is accepted", verify(good).status_code == 200)
check("the master code still works", verify("master-code").status_code == 200)
check("a revoked code is refused", verify(gone).status_code == 401)
check("an unknown code is refused", verify("nonsense-code").status_code == 401)
check("an expired code is refused", verify(stale).status_code == 401)
check("use counted through the API",
      [r for r in codes.all() if r.id == good_rec.id][0].uses == 1)
check("wrong codes still charge the attempt limit",
      quota.peek("code-fail", "testclient", Limit.parse("10/hour")) == 3,
      str(quota.peek("code-fail", "testclient", Limit.parse("10/hour"))))
main.app.dependency_overrides = {}

print("6. the command line")
db2 = Path(tempfile.mkdtemp()) / "cli.sqlite"
with patch("app.access_codes._store", lambda: AccessCodes(db2)):
    check("add succeeds", cli(["add", "Jane Doe", "--days", "14"]) == 0)
    check("list succeeds", cli(["list"]) == 0)
    issued = AccessCodes(db2).all()
    check("one code recorded, with its label",
          len(issued) == 1 and issued[0].label == "Jane Doe", str(len(issued)))
    check("revoke succeeds", cli(["revoke", issued[0].id]) == 0)
    check("revoking an unknown id exits non-zero", cli(["revoke", "nope00"]) == 1)
    check("it is now revoked", AccessCodes(db2).all()[0].status() == "revoked")

print()
print("all checks passed" if not failures else f"{len(failures)} FAILED: {failures}")
raise SystemExit(1 if failures else 0)
