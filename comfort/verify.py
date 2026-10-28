"""Independent check of comfort.json against jr.suki.ing's own timetable.

The site's delay loader pins a trip by (tmap key, origin departure minute,
reported station in the stop list). This verifier applies exactly that filter
to trains_wd/sa/su.json, so a green run means the browser will find the same
trip the baker intended - it does not trust the baker's own bookkeeping.

    DELAY_DIR=../delay python3 -m comfort.verify data/comfort.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

_delay_dir = os.environ.get("DELAY_DIR")
if _delay_dir and _delay_dir not in sys.path:
    sys.path.insert(0, _delay_dir)

from baker import common, suki  # noqa: E402


def _trips(data_dir, weekday):
    from baker.suki_timetable import _calendar_file
    path = Path(data_dir) / "suki" / _calendar_file(weekday)
    doc = json.loads(path.read_text())
    out = {}
    for t in doc.get("T") or []:
        key = str(t.get("t"))
        parts = [int(x) for x in str(t.get("s") or "").split(",") if x != ""]
        out.setdefault(key, []).append(parts[0])
    return out


def verify(comfort_path, delay_dir):
    doc = json.loads(Path(comfort_path).read_text())
    data_dir = Path(delay_dir) / "data"
    index = suki.SukiIndex.load(data_dir)
    weekday = common.jst_now().weekday()
    trips = _trips(data_dir, weekday)

    ok = fail_key = fail_dep = fail_station = fail_stops = 0
    bad = []
    seen = set()
    for e in doc.get("trains") or []:
        sk = (e.get("suki") or [{}])[0]
        key = str(sk.get("key"))
        dep = e.get("scheduled", {}).get("dep", "")
        if not dep or ":" not in dep:
            fail_dep += 1
            bad.append(("dep-missing", e.get("number"), key))
            continue
        dep0 = int(dep.split(":")[0]) * 60 + int(dep.split(":")[1])
        if key not in trips:
            fail_key += 1
            bad.append(("key-absent", e.get("number"), key))
            continue
        if dep0 not in trips[key]:
            fail_dep += 1
            bad.append(("no-run-at-dep", e.get("number"), key, dep))
            continue
        stops = index._stops_norm.get(key) or []
        want = suki.norm_station(e.get("from") or "")
        if want not in stops and not index._has_pref_suffixed(stops, want):
            fail_station += 1
            bad.append(("from-not-on-route", e.get("number"), key, e.get("from")))
            continue
        declared = [suki.norm_station(s) for s in (e.get("scheduled", {}).get("stops") or [])]
        if declared and stops[:len(declared)] != declared:
            fail_stops += 1
            bad.append(("stops-mismatch", e.get("number"), key))
            continue
        combo = (key, dep0)
        if combo in seen:
            fail_dep += 1
            bad.append(("duplicate-slot", e.get("number"), key, dep))
            continue
        seen.add(combo)
        ok += 1

    total = len(doc.get("trains") or [])
    print(f"verified {ok}/{total}  (key-absent {fail_key}, no-run-at-dep {fail_dep}, "
          f"from-not-on-route {fail_station}, stops-mismatch {fail_stops})")
    for b in bad[:15]:
        print("  FAIL", b)
    return ok, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="data/comfort.json")
    ap.add_argument("--delay-dir", default=os.environ.get("DELAY_DIR", "../delay"))
    a = ap.parse_args()
    ok, total = verify(a.path, a.delay_dir)
    sys.exit(0 if total and ok == total else 1)


if __name__ == "__main__":
    main()
