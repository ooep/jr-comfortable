"""Odakyu per-car congestion + temperature from the official board API.

The train-location SPA's backend (the same d6oynijiy33tb.cloudfront.net host
and public x-api-key the delay baker already uses) answers a departure board
per station:

    GET /trains/schedules/depart?station_code=028&up_down=01&direction=1

Each row is one departure with the *scheduled* clock the timetable is built
on (`departure_time`, seconds from midnight), the predicted clock
(`estimate_departure_time`), `number_of_cars`, `final_destination_code/name`
and - only on cars that actually carry a sensor - `car_infos`:

    [{car_no, passenger_rate(%), car_temp(C), air_mode}]

Rates move within 35 s, so this is live sensing, not a forecast. Verified
2026-10-28 20:49 JST: 42 board requests -> 18 trains with car_infos, all of
them also present in /trains, 10-car formations and per-car arrays aligned.

The board is per station, not per train, so the source sweeps a fixed anchor
set that covers every stopping pattern of the three lines and keeps the
nearest upcoming departure per train_code. That departure becomes the
record: `from` = the anchor, and the train's own remaining halt list
(stopsv2) must equal the chosen suki pattern's suffix from that anchor
exactly, with the board's scheduled minute pinning one run of it. The match
is made here, not left to bake's generic stoptime window, because Odakyu
runs express and all-stops patterns past the same station within a minute of
each other: time + terminal alone pinned a 急行 to the all-stops key on
2026-10-28 (14/18 records), and that would attach another train's readings.

Identity comes from /trains/{code}?version=v2, which returns the run's
whole scheduled stop sequence plus the operator's own departure seconds at
every stop. That list is exact for 各駅停車 and for expresses alike. The
pin requires a suki key of the same line whose stop list ends with this
sequence (through services only expose their Odakyu segment, so a suffix is
the most the API can prove), then picks the one run of that key whose
departures tie out at every overlapping station. suki keeps whole minutes
while Odakyu schedules in 15 s units, so a full run drifts up to ~5 min by
the far terminal; the runner-up run must stay 4 min further away or the
train is dropped (宁漏不误报).

Do NOT read stopsv2's `express_stopping` as a per-train stop flag: it is a
property of the station (whether expresses call there). A 各駅停車's
local-only stops come back as 0, so the "stop list" silently collapses to
the express pattern and pins local trains onto express keys. `halt_station`
and `invalid_stopping` are likewise display/見込 flags; only the v2 stop
sequence is safe to compare.

Anchoring at the nearest stop is deliberate: the reading describes the cars
now, so it must only ride on a departure the train is about to make. Boards
are ~2 h deep; anything past LEAD_MAX is dropped instead of being presented
as a forecast.
"""

from concurrent.futures import ThreadPoolExecutor

from baker import common, suki
from baker.odakyu import BASE, HEADERS  # public key, one copy

DEPART_URL = BASE + "/trains/schedules/depart"
DETAIL_URL = BASE + "/trains/{code}?version=v2"
TRAINS_URL = BASE + "/trains"

# station_code -> name. Odakyu numbers stations contiguously per line:
# 001-047 小田原線, 051-070 江ノ島線, 071-077 多摩線. Every stopping pattern
# of the three lines calls at one of these anchors, so no sensor train can
# stay hidden between two of them for longer than the board's reach.
ANCHORS = {
    "001": "新宿",
    "005": "代々木上原",
    "007": "下北沢",
    "014": "成城学園前",
    "018": "登戸",
    "023": "新百合ヶ丘",
    "027": "町田",
    "028": "相模大野",
    "032": "海老名",
    "034": "本厚木",
    "036": "伊勢原",
    "039": "秦野",
    "041": "新松田",
    "047": "小田原",
    "055": "大和",
    "059": "湘南台",
    "063": "藤沢",
    "066": "片瀬江ノ島",
    "073": "黒川",
    "075": "小田急永山",
    "077": "唐木田",
}

LINES = {
    "小田原線": "Odakyu.Odawara",
    "小田急江ノ島線": "Odakyu.Enoshima",
    "多摩線": "Odakyu.Tama",
}

# The reading is "now". A departure a minute in the past can still be standing
# at the platform (the API's own predicted clock lags the scheduled one), but
# anything further ahead than this is a forecast and does not belong on a
# boarding car.
LEAD_MIN = -1.0
LEAD_MAX = 25.0

# suki stores whole minutes; Odakyu's schedule runs on 15 s units, so the
# two clocks drift apart by ~5 min over a full line in one direction.
DRIFT_MAX = 6.0
# A second run this close to the winner is ambiguity, not a match.
MARGIN = 4.0


def _grade(rate):
    # Same three-step scale Osaka Metro uses so the drawer colours agree.
    if rate <= 50:
        return 1
    if rate <= 100:
        return 2
    return 3


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clean(name):
    return "".join(str(name or "").split())


def _comfort(car_infos, cars):
    cong = []
    temps = []
    for car in car_infos or []:
        cong.append(_int(car.get("passenger_rate")))
        temp = car.get("car_temp")
        try:
            temp = float(temp)
        except (TypeError, ValueError):
            temp = None
        # 0 / absent is "no sensor on this car", not 0 degrees.
        temps.append(temp if temp and 1 <= temp <= 60 else None)
    if cars:
        cong = cong[:cars]
        temps = temps[:cars]
    valid = [c for c in cong if c is not None]
    if not valid and not any(t is not None for t in temps):
        return None
    block = {}
    if valid:
        block["congestion_cars"] = cong
        block["congestion"] = {"class": _grade(max(valid)), "rate": max(valid)}
    if any(t is not None for t in temps):
        block["temperature_cars"] = temps
    return block


def _line(anchor_code, dest_code):
    """Physical Odakyu line the anchored departure belongs to.

    Destination decides at the junctions (新百合ヶ丘, 相模大野); the anchor's
    own numbering range decides everywhere else - including trains that are
    already past the junction and now report a destination back up the line.
    """
    dest = _int(dest_code)
    if dest is not None:
        if 71 <= dest <= 77:
            return "多摩線"
        if 51 <= dest <= 70:
            return "小田急江ノ島線"
    anchor = int(anchor_code)
    if anchor >= 71:
        return "多摩線"
    if anchor >= 51:
        return "小田急江ノ島線"
    return "小田原線"


def _board(code, up_down):
    url = f"{DEPART_URL}?station_code={code}&up_down={up_down}&direction=1"
    data = common.get_json(url, HEADERS, retries=2)
    rows = data.get("schedules") if isinstance(data, dict) else data
    return rows if isinstance(rows, list) else []


def _running():
    """train_codes currently in transit, or None when the check is unavailable."""
    try:
        data = common.get_json(TRAINS_URL, HEADERS, retries=2)
    except Exception:  # noqa: BLE001 - the board alone is still usable
        return None
    trains = data.get("trains") if isinstance(data, dict) else data
    if not isinstance(trains, list):
        return None
    return {str(t.get("train_code")) for t in trains if t.get("train_code")}


def _detail(train_code):
    """The run's full scheduled stop sequence, or None."""
    data = common.get_json(DETAIL_URL.format(code=train_code), HEADERS, retries=2)
    doc = data[0] if isinstance(data, list) and data else data
    if not isinstance(doc, dict) or not isinstance(doc.get("arrive_times"), list):
        return None
    return doc


def _schedule(detail, anchor, board_dep):
    """(normalized stops, scheduled departure minutes) of this run.

    Empty when the detail does not cover our anchor, or when the anchor's
    scheduled clock disagrees with the board row (different run under a
    reused train_code).
    """
    want = suki.norm_station(anchor)
    stops, due, found = [], [], False
    for row in detail.get("arrive_times") or []:
        name = suki.norm_station(_clean(row.get("station_name")))
        secs = row.get("departure_time")
        if secs is None:
            secs = row.get("arrive_time")
        if not name or not isinstance(secs, (int, float)):
            return [], []
        if name == want:
            found = True
            if abs(secs - board_dep) > 60:
                return [], []
        stops.append(name)
        due.append(secs / 60)
    return (stops, due) if found else ([], [])


def _pin(index, stt, keys, stops, due, dest):
    """(suki key, origin departure, drift) for this exact run, or None.

    The run's whole stop sequence must end a key's stop list, and one run of
    that key must tie out at every overlapping station (within the 15 s-vs-
    whole-minute drift). The runner-up must be a clear margin away.
    """
    n = len(stops)
    hits = []
    for key in keys:
        key = str(key)
        stops_n = index._stops_norm.get(key)
        if not stops_n or len(stops_n) < n or stops_n[-n:] != stops:
            continue
        idx = len(stops_n) - n
        by_dep0 = []
        for j in range(n):
            rows = stt.stop_times(key, stops_n, stops_n[idx + j]) or []
            by_dep0.append({d0: dep for _arr, dep, d0 in rows})
        for dep0, _dep in by_dep0[0].items():
            drift = 0.0
            for j, expect in enumerate(due):
                got = by_dep0[j].get(dep0)
                if got is None:
                    drift = None
                    break
                drift = max(drift, abs(expect - got))
                if drift > DRIFT_MAX:
                    break
            if drift is not None and drift <= DRIFT_MAX:
                hits.append((drift, key, dep0))
    if not hits:
        return None
    hits.sort()
    if len(hits) > 1 and hits[1][0] <= hits[0][0] + MARGIN:
        return None
    drift, key, dep0 = hits[0]
    return key, dep0, drift


def fetch(src, index, stt, now):
    if now.hour < 3:
        # The board still runs the previous service day's 24h+ clock while
        # bake has already switched to today's timetable.
        return []
    now_min = now.hour * 60 + now.minute + now.second / 60
    jobs = [(code, ud) for code in ANCHORS for ud in ("01", "10")]
    failures = []

    def _one(job):
        code, ud = job
        try:
            return job, _board(code, ud)
        except Exception as exc:  # noqa: BLE001 - one dead board is not the feed
            failures.append(f"{code}/{ud} {type(exc).__name__}")
            return job, []

    with ThreadPoolExecutor(max_workers=6) as ex:
        boards = list(ex.map(_one, jobs))

    # Same train appears on every anchor ahead of it; the nearest upcoming
    # departure is the one the reading belongs to.
    best = {}
    for (code, ud), rows in boards:
        for row in rows:
            if not row.get("car_infos"):
                continue
            dep = row.get("departure_time")
            if not isinstance(dep, int):
                continue
            est = row.get("estimate_departure_time")
            if not isinstance(est, (int, float)):
                est = dep
            lead = est / 60
            if not (now_min + LEAD_MIN <= lead <= now_min + LEAD_MAX):
                continue
            train = str(row.get("train_code") or "")
            if not train:
                continue
            prev = best.get(train)
            if prev is None or est < prev[2]:
                best[train] = (code, ud, est, dep, row)

    if failures:
        print(f"comfort odakyu: {len(failures)}/{len(jobs)} boards failed ({failures[:3]})")

    running = _running()

    def _detail_for(item):
        train = item[0]
        try:
            return item, _detail(train)
        except Exception:  # noqa: BLE001 - an unverifiable train is dropped
            return item, None

    todo = [
        item for item in best.items()
        if running is None or item[0] in running
    ]
    with ThreadPoolExecutor(max_workers=6) as ex:
        details = list(ex.map(_detail_for, todo))

    out = []
    for (_train, (code, _ud, _est, dep, row)), detail in sorted(
        details, key=lambda p: p[0][1][2]
    ):
        if detail is None:
            continue
        dest = _clean(row.get("final_destination_name"))
        if not dest or _clean(detail.get("final_destination_name")) != dest:
            continue
        board_cars = row.get("number_of_cars")
        detail_cars = detail.get("number_of_cars")
        if isinstance(board_cars, int) and isinstance(detail_cars, int) \
                and board_cars != detail_cars:
            continue
        cars = board_cars if isinstance(board_cars, int) and board_cars > 0 \
            else len(row["car_infos"])
        block = _comfort(row.get("car_infos"), cars)
        if not block:
            continue
        stops, due = _schedule(detail, ANCHORS[code], dep)
        if not stops:
            continue
        line = _line(code, row.get("final_destination_code"))
        pin = _pin(
            index, stt, index._line_pool.get(line, []),
            stops, due, suki.norm_station(dest),
        )
        if not pin:
            continue
        key, dep0, drift = pin
        out.append({
            "op": "Odakyu",
            "line": line,
            "railway": LINES[line],
            "number": None,
            "type": _clean(row.get("train_type_name")) or None,
            "from": ANCHORS[code],
            "to": dest,
            "cars": cars,
            "comfort": block,
            "suki_key": key,
            "scheduled_dep": dep0,
            "gap": round(drift, 2),
        })
    return out
