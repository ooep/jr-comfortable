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
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_delay_dir = os.environ.get("DELAY_DIR")
if _delay_dir and _delay_dir not in sys.path:
    sys.path.insert(0, _delay_dir)

from baker import common, holidays, suki, suki_timetable  # noqa: E402
from baker.delays import _stops_consistent, resolve_direct_by_time  # noqa: E402

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


def _resolved(index, cand, dep0, tier=None, gap=None):
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
        "tier": tier,
        "gap": gap,
        "dep0": dep0,
    }


def _next_must_stop(rec):
    """True when the reported next station has to be a stop of the pattern.

    A 新快速 legitimately passes the station the sign names as next, so the
    resolvers tolerate its absence - but that same tolerance let 環状線 1485
    (西九条→弁天町, 普通) claim the 西九条→桜島 ゆめ咲線 pattern, where 弁天町
    is not even on the line. An all-stops train has no such excuse.
    """
    if rec.get("op") != "JR-West":
        return False
    train_type = rec.get("type") or ""
    return any(k in train_type for k in ("普通", "各駅停車", "各停"))


def _next_forward(stops_n, idx, want_next, must_stop):
    """Feed's next station vs the pattern: forward stop, or absent-but-allowed."""
    if want_next is None:
        return True
    if want_next in stops_n:
        return stops_n.index(want_next) > idx
    return not must_stop


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
    must_stop = _next_must_stop(rec)
    hits = []
    for c in _candidates(index, rec):
        key = str(c["key"])
        stops_n = index._stops_norm[key]
        idx = _station_index(stops_n, want)
        if idx is None:
            continue
        if want_dest and stops_n and stops_n[-1] != want_dest:
            continue
        # The feed's "last station -> next station" pair must be a forward
        # move on this pattern (an express may skip the next station).
        if not _next_forward(stops_n, idx, want_next, must_stop):
            continue
        for arr, dep, dep0 in stt.stop_times(key, stops_n, stops_n[idx]) or []:
            g = min(abs(arr - est), abs(dep - est))
            if g <= tol:
                hits.append((key, c, dep0, g))
    if len({(h[0], h[2]) for h in hits}) != 1:
        return None
    key, cand, dep0, g = min(hits, key=lambda h: h[3])
    # tier 0: the train's own stopTime is an absolute clock, so the disagreement
    # with the timetable is a minute-level measurement, not an inference.
    return _resolved(index, cand, dep0, tier=0, gap=g)


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
    must_stop = _next_must_stop(rec)
    hits = []
    for c in _candidates(index, rec):
        key = str(c["key"])
        stops_n = index._stops_norm[key]
        idx = _station_index(stops_n, want)
        if idx is None or idx + 1 >= len(stops_n):
            continue
        if want_dest and stops_n and stops_n[-1] != want_dest:
            continue
        if not _next_forward(stops_n, idx, want_next, must_stop):
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
    # tier 1: the interval [表定発車A, 表定到着B] is a physical bound but not a
    # clock reading, so it is weaker evidence than a stopTime match.
    return _resolved(index, cand, dep0, tier=1)


def _by_dest(index):
    """last normalised stop -> keys.

    Each line advertises a pool of stop patterns, and for JR-West those pools
    stop at the line the service is named after: a 特急こうか 白浜行き is
    published under 阪和線, whose patterns all end at 和歌山 or 天王寺, so the
    printed terminus is not the last stop of any candidate and the destination
    test kills all of them. Indexing every pattern by its own last stop lets
    the terminus - the strongest thing the feed says - find the run itself.
    """
    out = defaultdict(list)
    for key, stops in index._stops_norm.items():
        if not stops:
            continue
        last = stops[-1]
        out[last].append(key)
        # 関西空港(鉄道) / 大宮(埼玉県): the feed prints the bare name, so the
        # qualifier must not decide whether the scan looks at all.
        bare = last.split("(")[0]
        if bare != last:
            out[bare].append(key)
    return out


def _resolve_destscan(index, by_dest, stt, rec, now, tol=3):
    """Pin by the reported terminus across every pattern, ignoring line pools.

    Weaker than the pool resolvers in one way (it never saw the operator's
    line name) and stronger in the rest: the pattern must end exactly where
    the sign says the train goes, must contain the reported station ahead of
    nothing else, keep the reported next station forward, and be the only run
    in the window. Two different trains with the same terminus passing the
    same station within three minutes is refused - which is exactly why this
    runs last instead of instead of the pool tests.
    """
    if not rec.get("to"):
        return None
    official = rec.get("official") or {}
    est, tier = None, 2
    if rec.get("stop") is not None:
        est, tier = rec["stop"] - (rec.get("delay_min") or 0), 0
    elif official.get("departure") is not None:
        est, tier = official["departure"], 0
    if est is None:
        est = now.hour * 60 + now.minute - (rec.get("delay_min") or 0)
    want = suki.norm_station(rec["from"])
    want_dest = suki.norm_station(rec["to"])
    want_next = suki.norm_station(rec["next"]) if rec.get("next") else None
    must_stop = _next_must_stop(rec)
    hits = []
    keys = by_dest.get(want_dest) or by_dest.get(want_dest.split("(")[0]) or []
    for key in keys:
        stops_n = index._stops_norm[key]
        idx = _station_index(stops_n, want)
        # The scan's only discriminator is "what still lies ahead", so a record
        # reported at the pattern's own last stop proves nothing: with no
        # future stops, every pattern sharing that terminus agrees with it.
        if idx is None or idx + 1 >= len(stops_n):
            continue
        if not _next_forward(stops_n, idx, want_next, must_stop):
            continue
        if rec.get("official") and not _stops_consistent(
                stops_n, stops_n[idx], rec["official"], rec.get("max_extra", 1)):
            continue
        for arr, dep, dep0 in stt.stop_times(key, stops_n, stops_n[idx]) or []:
            g = min(abs(arr - est), abs(dep - est))
            if g <= tol:
                hits.append((key, dep0, g))
    if len({(h[0], h[1]) for h in hits}) != 1:
        return None
    key, dep0, g = min(hits, key=lambda h: h[2])
    return _resolved(index, {"key": key}, dep0, tier=tier, gap=g)


def _resolve(index, stt, rec, now):
    direct = _candidates(index, rec)
    if not direct:
        return None
    dest = rec.get("to")
    if rec.get("official") is None and rec.get("next"):
        # Same next-station rule the JR-West interval resolvers use: delay's
        # strict resolver only cross-checks the stop list when the operator
        # publishes one, so an all-stops train could otherwise land on a
        # pattern that never calls its reported next station.
        must_stop = _next_must_stop(rec)
        want_next = suki.norm_station(rec["next"])
        want = suki.norm_station(rec["from"])
        keep = []
        for c in direct:
            stops_n = index._stops_norm[str(c["key"])]
            idx = _station_index(stops_n, want)
            if idx is not None and not _next_forward(stops_n, idx, want_next, must_stop):
                continue
            keep.append(c)
        direct = keep
    # JR-East passes the operator's own clock, so delay_min is unused there;
    # JR-West has no official stop times but its position feed carries
    # delayMinutes, which shifts `now` back onto the scheduled departure of
    # the reported station exactly like delay's own mapping does.
    ok = resolve_direct_by_time(
        stt, index, direct, rec["from"], now, rec.get("delay_min") or 0,
        rec["type"], official=rec.get("official"), dest=dest,
        max_extra=rec.get("max_extra", 1),
    )
    if ok or rec.get("official") is None:
        return _tag_strict(index, stt, rec, ok, now)
    # The operator's own clock is exact to the minute (drift vs suki observed
    # at 0-1 min), but rush-hour headways put several stop patterns inside the
    # ±3 window, which the ambiguity rule then rejects. Retrying at ±1 asks
    # the sharper question - which single (pattern, run) departs this station
    # at the announced minute - and stays silent when two of them do.
    return _tag_strict(index, stt, rec, resolve_direct_by_time(
        stt, index, direct, rec["from"], now, rec.get("delay_min") or 0,
        rec["type"], official=rec.get("official"), dest=dest,
        max_extra=rec.get("max_extra", 1), tol=1,
    ), now)


def _tag_strict(index, stt, rec, ok, now):
    """Attach the evidence tier and the clock disagreement to a strict match.

    tier 0 means the operator itself announced the minute the train leaves the
    reported station (cloudrail's planDeparture); tier 2 means only the wall
    clock minus the reported delay was available, which every other train on
    the line shares. Slot arbitration uses both.
    """
    if ok is None:
        return None
    official = rec.get("official") or {}
    est = official.get("departure")
    tier = 0 if est is not None else 2
    if est is None:
        est = now.hour * 60 + now.minute - (rec.get("delay_min") or 0)
    key = str(ok["cand"]["key"])
    stops_n = index._stops_norm[key]
    want = suki.norm_station(rec["from"])
    anchor = want if want in stops_n else next(
        (s for s in stops_n
         if s.startswith(want) and s[len(want):].endswith(("都", "道", "府", "県"))), None)
    dep0 = int(ok["scheduled"]["dep"][:2]) * 60 + int(ok["scheduled"]["dep"][3:])
    gap = None
    if anchor is not None:
        gap = next((abs(arr - est) for arr, d0 in
                    stt.passage_times(key, stops_n, anchor) or [] if d0 == dep0), None)
    return {**ok, "tier": tier, "gap": gap, "dep0": dep0}


def _stages(index, by_dest, stt, rec, now):
    """One full pass of every resolver, strongest anchor first.

    The train's own stopTime is an absolute clock, so it goes first; the
    [発車A, 到着B] interval is a physical bound and comes second; the wall-clock
    strict resolver is the weakest and last of the pool tests. The destination
    scan runs after all three - it is the only one that can reach a pattern the
    line's own pool never lists, and it pays for that with a stricter
    uniqueness rule.
    """
    got = _resolve_stoptime(index, stt, rec)
    if got:
        return got
    # `est` is stopTime minus a whole-minute delay, so the true run is normally
    # within a minute of it; when the ±3 window admits two patterns the tighter
    # one separates them, and when it admits none nothing was lost.
    got = _resolve_stoptime(index, stt, rec, tol=1)
    if got:
        return got
    if rec.get("next"):
        got = _resolve_interval(index, stt, rec, now)
        if got:
            return got
    got = _resolve(index, stt, rec, now)
    if got:
        return got
    got = _resolve_destscan(index, by_dest, stt, rec, now)
    if got:
        got["scanned"] = True
    return got


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
    by_dest = _by_dest(index)

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

    pending = []
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
                # Pinned against a published timetable row, not a time window:
                # already the sharpest evidence a slot can have.
                "tier": -1,
            }
        else:
            resolved = _stages(index, by_dest, stt, rec, now)
            if resolved and resolved.get("scanned"):
                stats["dest_scan"] += 1
        if not resolved:
            stats["unresolved"] += 1
            misses[rec["line"]] += 1
            continue
        # A suki key is a stop *pattern*, reused by many runs of the day
        # (中央線快速 runs every few minutes share one). Two live trains on
        # the same key are fine as long as their origin departure differs -
        # that minute is exactly what jr.suki.ing filters trips by. Only a
        # same-key same-departure pair is a genuine collision.
        slot = (str(resolved["cand"]["key"]), resolved["scheduled"]["dep"])
        pending.append((slot, rec, resolved))

    # Two live trains that both pin to one (pattern, departure minute) cannot
    # both be that run, and which one wins must not depend on the order the
    # sources happened to return. Keep a single claimant only when its evidence
    # is strictly sharper - the train's own clock agreeing to the minute beats
    # a neighbour that needed the ±3 window - and otherwise report neither.
    def _strength(resolved):
        gap = resolved.get("gap")
        return (resolved.get("tier", 0), 99 if gap is None else gap)

    groups = defaultdict(list)
    for slot, rec, resolved in pending:
        groups[slot].append((rec, resolved))
    trains = []
    for group in groups.values():
        contested = len(group) > 1
        if contested:
            best = min(group, key=lambda g: _strength(g[1]))
            if sum(1 for g in group if _strength(g[1]) == _strength(best[1])) != 1:
                stats["key_collision"] += len(group)
                continue
            stats["collision_won"] += len(group) - 1
            rec, resolved = best
        else:
            rec, resolved = group[0]
        key, dep = (str(resolved["cand"]["key"]), resolved["scheduled"]["dep"])
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
        # Audit trail: a contested slot is the one mapping that can still be
        # wrong (two live trains claimed the same run), so mark it and let a
        # bad record be traced back to the rule that kept it.
        ev = []
        if contested:
            ev.append("contested")
        if resolved.get("scanned"):
            ev.append("dest_scan")
        if ev:
            entry["ev"] = ev
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
