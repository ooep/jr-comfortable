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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_delay_dir = os.environ.get("DELAY_DIR")
if _delay_dir and _delay_dir not in sys.path:
    sys.path.insert(0, _delay_dir)

from baker import common, holidays, suki, suki_timetable  # noqa: E402
from baker.delays import resolve_direct_by_time  # noqa: E402

from comfort.sources import jreast, jrwest, osakametro, tokyometro  # noqa: E402


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


def _candidates(index, rec):
    direct = []
    seen = set()
    direction = _direction(rec.get("number") or "")
    for line in rec.get("suki_pools") or rec.get("pools") or []:
        for c in index.match_direct(
            line, rec["from"], direction, rec.get("type"), top=256
        ) or []:
            key = str(c["key"])
            if key not in seen:
                seen.add(key)
                direct.append(c)
    return direct


def _station_index(stops_n, want):
    if want in stops_n:
        return stops_n.index(want)
    return next(
        (i for i, s in enumerate(stops_n)
         if s.startswith(want) and s[len(want):].endswith(("都", "道", "府", "県"))),
        None,
    )


def _resolved(index, cand, dep0):
    stops = index.trains[str(cand["key"])]["s_jas"]
    c2 = dict(cand)
    c2["size"] = len(stops)
    return {
        "cand": c2,
        "scheduled": {
            "dep": f"{dep0 // 60:02d}:{dep0 % 60:02d}",
            "origin": stops[0],
            "stops": stops,
        },
    }


def _resolve_stoptime(index, stt, rec, tol=3):
    """JR-West: pin the run by the train's own stopTime at A.

    trainmonitorinfo's position record says when the train stopped at the
    station it last called at (`stopTime`, to the second). Matching that
    against the timetable's arrival/departure at A is an absolute clock, so
    it does not drift with the current wall time like the strict resolver's
    arrival window. Verified live: every mid-segment hit's next scheduled
    stop equals the feed's `next` station (62/63), i.e. same physical train.
    """
    stop = rec.get("stop")
    if stop is None:
        return None
    est = stop - (rec.get("delay_min") or 0)
    want = suki.norm_station(rec["from"])
    want_next = suki.norm_station(rec["next"]) if rec.get("next") else None
    want_dest = suki.norm_station(rec["to"]) if rec.get("to") else None
    hits = []
    for c in _candidates(index, rec):
        key = str(c["key"])
        stops_n = index._stops_norm[key]
        if want_dest and stops_n and stops_n[-1] != want_dest:
            continue
        idx = _station_index(stops_n, want)
        if idx is None:
            continue
        # The feed's "last station -> next station" pair must be a forward
        # move on this pattern (an express may skip the next station).
        if want_next is not None and want_next in stops_n and stops_n.index(want_next) <= idx:
            continue
        for arr, dep, dep0 in stt.stop_times(key, stops_n, stops_n[idx]) or []:
            if abs(arr - est) <= tol or abs(dep - est) <= tol:
                hits.append((key, c, dep0))
    if len({(h[0], h[2]) for h in hits}) != 1:
        return None
    key, cand, dep0 = hits[0]
    return _resolved(index, cand, dep0)


def _resolve_interval(index, stt, rec, now):
    """JR-West mid-segment fallback when the record has no stopTime.

    The feed says the train left A and has not reached B yet, so 表定到A <=
    now-delay <= 表定到下一停站 is the physically correct window. Uniqueness
    stays strict (one key, one run in the interval), and the terminal must
    still equal the reported destination.
    """
    next_station = rec.get("next")
    if not next_station:
        return None
    est = now.hour * 60 + now.minute - (rec.get("delay_min") or 0)
    want = suki.norm_station(rec["from"])
    want_next = suki.norm_station(next_station)
    want_dest = suki.norm_station(rec["to"]) if rec.get("to") else None
    hits = []
    for c in _candidates(index, rec):
        key = str(c["key"])
        stops_n = index._stops_norm[key]
        if want_dest and stops_n and stops_n[-1] != want_dest:
            continue
        idx = _station_index(stops_n, want)
        if idx is None or idx + 1 >= len(stops_n):
            continue
        # An express may skip the adjacent station; the next scheduled stop is
        # still a valid (looser) upper bound for "has not arrived there yet".
        rest = stops_n[idx + 1:]
        bound = want_next if want_next in rest else rest[0]
        stop_a = {dep0: (arr, dep) for arr, dep, dep0 in
                  stt.stop_times(key, stops_n, stops_n[idx]) or []}
        arr_b_by_run = {dep0: arr for arr, dep, dep0 in
                        stt.stop_times(key, stops_n, bound) or []}
        for dep0, (arr_a, dep_a) in stop_a.items():
            arr_b = arr_b_by_run.get(dep0)
            if arr_b is not None and dep_a <= est <= arr_b:
                hits.append((key, c, dep0))
    if len(hits) != 1:
        return None
    key, cand, dep0 = hits[0]
    return _resolved(index, cand, dep0)


def _resolve(index, stt, rec, now):
    direct = _candidates(index, rec)
    if not direct:
        return None
    # JR-East passes the official clock, so delay_min is unused there; JR-West
    # has no official stop times but its position feed carries delayMinutes,
    # which shifts `now` back onto the scheduled departure of the reported
    # station exactly like delay's own mapping does.
    ok = resolve_direct_by_time(
        stt, index, direct, rec["from"], now, rec.get("delay_min") or 0,
        rec["type"], official=rec.get("official"), dest=rec.get("to"),
        max_extra=rec.get("max_extra", 1),
    )
    if ok or rec.get("official") is None:
        return ok
    # The operator's own clock is exact to the minute (drift vs suki observed
    # at 0-1 min), but rush-hour headways put several stop patterns inside the
    # ±3 window, which the ambiguity rule then rejects. Retrying at ±1 asks
    # the sharper question - which single (pattern, run) departs this station
    # at the announced minute - and stays silent when two of them do.
    return resolve_direct_by_time(
        stt, index, direct, rec["from"], now, rec.get("delay_min") or 0,
        rec["type"], official=rec.get("official"), dest=rec.get("to"),
        max_extra=rec.get("max_extra", 1), tol=1,
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
    pulls = (
        ("jreast", lambda: jreast.fetch(src, files)),
        ("osakametro", lambda: osakametro.fetch(src, index)),
        ("jrwest", lambda: jrwest.fetch(src, index)),
        ("tokyometro", lambda: tokyometro.fetch(src, index, stt, now)),
    )

    def _pull(item):
        name, fn = item
        try:
            return name, fn(), None
        except Exception as exc:  # noqa: BLE001 - fail closed per source
            return name, [], f"{type(exc).__name__}: {str(exc)[:160]}"

    # Four different operators, so running them side by side adds no
    # concurrency to any single host - it just stops the slow one (Tokyo
    # Metro's chart pages) from waiting behind the other three.
    with ThreadPoolExecutor(max_workers=len(pulls)) as ex:
        results = list(ex.map(_pull, pulls))
    raw = []
    for name, got, err in results:
        if err:
            print(f"{name} comfort fetch failed: {err}")
            continue
        print(f"{name} comfort candidates: {len(got)}")
        raw.extend(got)

    claimed = {}
    trains = []
    stats = Counter()
    misses = Counter()
    for rec in raw:
        stats["candidates"] += 1
        # Sources whose feed is anchored to a published timetable (Tokyo
        # Metro) already picked the run themselves; their matcher lives in
        # the source because delay's resolver needs an official stop list
        # that CrowdNavi does not publish.
        if rec.get("suki_key"):
            key = str(rec["suki_key"])
            stops = index.trains[key]["s_jas"]
            dep0 = int(rec["scheduled_dep"])
            resolved = {
                "cand": {"key": key, "size": len(stops), "overlap": 1.0},
                "scheduled": {
                    "dep": f"{dep0 // 60:02d}:{dep0 % 60:02d}",
                    "origin": stops[0],
                    "stops": stops,
                },
            }
        else:
            resolved = _resolve_stoptime(index, stt, rec)
            # `est` is stopTime minus a whole-minute delay, so the true run is
            # normally within a minute of it. When the +/-3 min window admits
            # two patterns, the tighter window is what separates them; when it
            # admits none, nothing was lost.
            if not resolved:
                resolved = _resolve_stoptime(index, stt, rec, tol=1)
            if not resolved and rec.get("next"):
                resolved = _resolve_interval(index, stt, rec, now)
            if not resolved and not rec.get("next"):
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
