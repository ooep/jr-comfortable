"""Bake data/comfort.json: per-train congestion / temperature / accessibility
readings pinned onto jr.suki.ing timetable keys.

Mapping is not re-invented here. ooep/delay already answers "which suki
timetable key is this live train" (match_direct over the line pool, then
resolve_direct_by_time against the site's own timetable), and its answer is
what the main site trusts for delays. We call the same two functions with the
same anchors, so a comfort record lands on exactly the trip that the delay
record would.

The difference is the candidate set: delay only looks at trains running late,
comfort looks at every train that carries a reading. Uniqueness rules stay
strict (single surviving key after time verification, one key per run) because
attaching another train's crowding level is worse than showing nothing.

    DELAY_DIR=../delay python3 -m comfort.bake --out data
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

_delay_dir = os.environ.get("DELAY_DIR")
if _delay_dir and _delay_dir not in sys.path:
    sys.path.insert(0, _delay_dir)

from baker import common, holidays, suki, suki_timetable  # noqa: E402
from baker.delays import resolve_direct_by_time  # noqa: E402

from comfort.sources import jreast, osakametro  # noqa: E402


def _calendar_day(now):
    today_iso = now.date().isoformat()
    wkd = now.weekday()
    # JR runs the holiday (Sat/Sun/Holiday) timetable on shukujitsu days, so a
    # Monday holiday must not match the weekday timetable.
    if holidays.is_holiday_today(today_iso):
        wkd = 6
    return today_iso, wkd


def _direction(number):
    # Same convention as ooep/delay: odd numbers bound away from the
    # terminal, even numbers back towards it. Only trusted for pure digits;
    # cloudrail uses alphanumeric numbers (533M) and gets no direction filter.
    if number.isdigit():
        return "down" if int(number) % 2 else "up"
    return None


def _resolve(index, stt, rec, now):
    direct = []
    seen = set()
    pools = rec.get("suki_pools") or rec.get("pools") or []
    direction = _direction(rec.get("number") or "")
    for line in pools:
        hits = index.match_direct(
            line, rec["from"], direction, rec.get("type"), top=256
        ) or []
        for c in hits:
            key = str(c["key"])
            if key not in seen:
                seen.add(key)
                direct.append(c)
    if not direct:
        return None
    # delay_min is 0: comfort has no delay reading, and the resolver prefers
    # the feed's own official clock whenever it is available.
    return resolve_direct_by_time(
        stt, index, direct, rec["from"], now, 0,
        rec["type"], official=rec.get("official"), dest=rec.get("to"),
    )


def bake(delay_dir, out_dir, files=None):
    data_dir = Path(delay_dir) / "data"
    index = suki.SukiIndex.load(data_dir)
    now = common.jst_now()
    today_iso, wkd = _calendar_day(now)
    stt = suki_timetable.SukiTimetable.load(data_dir, wkd)
    if not stt:
        print("suki timetable unavailable: refusing to bake (keys would be unverified)")
        return 2
    print(f"suki index: {len(index.trains)} keys; timetable runs: {len(stt.runs)} ({stt.source_file})")

    src = common.Source()
    raw = []
    for name, pull in (
        ("jreast", lambda: jreast.fetch(src, files)),
        ("osakametro", lambda: osakametro.fetch(src, index)),
    ):
        try:
            got = pull()
        except Exception as exc:  # noqa: BLE001 - fail closed per source
            print(f"{name} comfort fetch failed: {type(exc).__name__}: {str(exc)[:160]}")
            continue
        print(f"{name} comfort candidates: {len(got)}")
        raw.extend(got)

    claimed = {}
    trains = []
    stats = Counter()
    misses = Counter()
    for rec in raw:
        stats["candidates"] += 1
        resolved = _resolve(index, stt, rec, now)
        if not resolved:
            stats["unresolved"] += 1
            misses[rec["line"]] += 1
            continue
        key = str(resolved["cand"]["key"])
        # A suki key is a stop *pattern*, reused by many runs of the day
        # (中央線快速 runs every few minutes share one). Two live trains on
        # the same key are fine as long as their origin departure differs -
        # that minute is exactly what jr.suki.ing filters trips by. Only a
        # same-key same-departure pair is a genuine collision.
        slot = (key, resolved["scheduled"]["dep"])
        if slot in claimed:
            stats["key_collision"] += 1
            continue
        claimed[slot] = rec.get("number") or rec.get("vehicle") or ""
        entry = {
            "op": rec["op"],
            "line": rec["line"],
            "railway": rec["railway"],
            "number": rec.get("number"),
            "type": rec.get("type"),
            "from": rec["from"],
            "to": rec["to"],
            "cars": rec.get("cars"),
            "comfort": rec["comfort"],
            "scheduled": resolved["scheduled"],
            "suki": [{
                "key": key,
                "size": resolved["cand"].get("size"),
                "overlap": resolved["cand"].get("overlap"),
            }],
        }
        if rec.get("vehicle"):
            entry["vehicle"] = rec["vehicle"]
        trains.append(entry)
        stats["matched"] += 1

    by_key = {}
    for entry in trains:
        by_key.setdefault(str(entry["suki"][0]["key"]), []).append(entry)

    payload = {
        "version": 1,
        "generated_at": now.isoformat(),
        "date": today_iso,
        "count": len(trains),
        "candidates": len(raw),
        "stats": dict(stats),
        "unresolved_by_line": dict(misses.most_common()),
        "trains": trains,
        "by_suki_key": by_key,
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prev_path = out_dir / "comfort.json"
    if prev_path.exists():
        try:
            prev = json.loads(prev_path.read_text()).get("count") or 0
        except Exception:  # noqa: BLE001 - corrupt previous file is no reason to stop
            prev = 0
        # A source that silently answers half the trains looks identical to a
        # quiet evening. Refuse the write so the last good snapshot survives.
        if prev >= 40 and len(trains) < prev * 0.5:
            print(f"refusing write: {len(trains)} mapped vs {prev} in previous snapshot")
            return 3
    (out_dir / "comfort.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"comfort.json: {len(trains)} mapped / {len(raw)} candidates {dict(stats)}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay-dir", default=os.environ.get("DELAY_DIR", "../delay"))
    ap.add_argument("--out", default="data")
    ap.add_argument("--files", nargs="*", default=None,
                    help="restrict to these cloudrail file ids (dev only)")
    args = ap.parse_args()
    sys.exit(bake(args.delay_dir, args.out, args.files))


if __name__ == "__main__":
    main()
