"""JR-West per-car congestion / temperature / carriage facilities.

The train-guide position feed (ooep/delay's baker/jrwest_guide.py) carries no
comfort fields, but the same service publishes a second document:

    GET https://www.train-guide.westjr.co.jp/api/v3/trainmonitorinfo.json
    {"update": "...", "trains": {"1421": [{"cars": [{carNo, status,
        congestion, temp, facilities, types}, ...]}, ...]}}

Keys are exactly the `no` values of <slug>.json, so a position record and a
monitor record join on the train number alone. Values are per coupled set
(a 8+4 formation shows two groups); `congestion` is a percentage and `temp`
degrees Celsius, both -1 when that car has no sensor; `status != 1` blanks the
whole car. Facility codes are the icon order in the official CSS sprite:
1 wheelchair space, 2 stroller space, 3 Wi-Fi, 4 restroom, 5 luggage space.

Station anchor is <slug>.json's `pos` prefix (a station code in that slug's
_st.json), resolved to a name here; the baker then pins the suki run with the
same resolver delay uses.
"""

import datetime
import re
from concurrent.futures import ThreadPoolExecutor

from baker import common

BASE = "https://www.train-guide.westjr.co.jp/api/v3"
AREAS = ["kinki", "hokuriku", "okayama", "hiroshima", "sanin"]

# train-guide markets services (JR京都線, 大和路線...) while jr.suki.ing keys
# are tagged with the physical lines. Through-running is the norm in Kansai:
# a 新快速 野洲→姫路 is filed under JR京都線 but its suki key carries both
# 東海道本線 and 山陽本線, and a はるか 関西空港→京都 crosses four of them.
# So each service maps to every physical line its route can touch; the
# resolver still requires a unique run in the time window with a matching
# terminal and stop sequence, so a wider pool only adds candidates.
LINE_POOLS = {
    "JR京都線": ["東海道本線", "山陽本線"],
    "JR神戸線・山陽線": ["山陽本線", "東海道本線"],
    "山陽線": ["山陽本線", "東海道本線"],
    "JR宝塚線・福知山線": ["福知山線", "東海道本線"],
    "JR宝塚線": ["福知山線", "東海道本線"],
    "大和路線": ["関西本線", "大阪環状線"],
    "学研都市線": ["片町線", "大阪環状線", "おおさか東線"],
    "北陸線・琵琶湖線": ["北陸本線", "東海道本線", "湖西線"],
    "山陰線": ["山陰本線"],
    "嵯峨野線": ["山陰本線"],
    "きのくに線": ["紀勢本線", "阪和線"],
    "万葉まほろば線": ["桜井線", "関西本線"],
    "JRゆめ咲線": ["桜島線", "大阪環状線"],
    "阪和線・羽衣線": ["阪和線", "大阪環状線", "東海道本線"],
    "阪和線": ["阪和線", "大阪環状線", "東海道本線"],
    "大阪環状線": ["大阪環状線", "阪和線", "関西本線", "桜島線", "おおさか東線"],
    "関西空港線": ["関西空港線", "阪和線", "大阪環状線", "東海道本線"],
    "関西線": ["関西本線", "大阪環状線"],
    "奈良線": ["jr奈良線", "関西本線"],
    "JR東西線": ["jr東西線", "片町線", "関西本線"],
    "おおさか東線": ["おおさか東線", "関西本線", "片町線", "大阪環状線"],
    "湖西線": ["湖西線", "東海道本線", "北陸本線"],
    "福知山線": ["福知山線", "東海道本線"],
    "東海道本線": ["東海道本線", "山陽本線", "湖西線", "北陸本線"],
    "山陽本線": ["山陽本線", "東海道本線"],
    "北陸本線": ["北陸本線", "東海道本線", "湖西線"],
    "赤穂線": ["赤穂線", "山陽本線"],
    "舞鶴線": ["舞鶴線", "山陰本線"],
    "桜井線": ["桜井線", "関西本線"],
    "和歌山線": ["和歌山線", "阪和線", "関西本線"],
    "桜島線": ["桜島線", "大阪環状線"],
    "片町線": ["片町線", "大阪環状線", "おおさか東線", "関西本線"],
    "関西本線": ["関西本線", "大阪環状線"],
    "草津線": ["草津線", "東海道本線"],
    "播但線": ["播但線", "山陽本線"],
    "姫新線": ["姫新線", "山陽本線"],
    "因美線": ["因美線", "山陰本線"],
    "伯備線": ["伯備線", "山陰本線", "山陽本線"],
    "津山線": ["津山線", "山陽本線"],
    "瀬戸大橋線": ["本四備讃線", "宇野線", "山陽本線"],
    "宇野みなと線": ["宇野線", "山陽本線"],
    "福塩線": ["福塩線", "山陽本線"],
    "可部線": ["可部線", "山陽本線"],
    "呉線": ["呉線", "山陽本線"],
    "芸備線": ["芸備線", "山陽本線"],
    "山口線": ["jr山口線", "山陽本線"],
}

FACILITIES = {1: "wheelchair", 2: "stroller", 3: "wifi", 4: "restroom", 5: "luggage"}

# A loop service reports dest "環状" (back to the same terminus), which never
# matches a suki run's last stop; drop it so the filter does not kill the pool.
_DEST_IGNORE = {"環状", "直通", ""}


def _grade(rate):
    return 1 if rate <= 50 else (2 if rate <= 100 else 3)


def _get(src, path):
    if src.offline:
        name = path.replace("/", "-")
        try:
            return src.offline_json(name)
        except Exception:
            return None
    try:
        return common.get_json(
            f"{BASE}/{path}",
            headers={"Referer": "https://www.train-guide.westjr.co.jp/"},
        )
    except Exception as exc:  # noqa: BLE001 - one line must not sink the run
        print(f"jrwest {path}: {type(exc).__name__}: {str(exc)[:120]}")
        return None


def _dest(raw):
    text = raw.get("text") if isinstance(raw, dict) else raw
    text = (text or "").strip()
    # Composite destinations ("関西空港/和歌山方面", "福知山／東舞鶴") name two
    # possible terminals and the actual one varies per run; "方面" is a
    # direction, not a stop. Treating either as a terminal rejects every
    # candidate. Unknown is safe - time window + stop sequence + uniqueness
    # still have to agree.
    if not text or text in _DEST_IGNORE or "方面" in text or "/" in text or "／" in text:
        return None
    return text


def _car_no(car):
    try:
        return int(car.get("carNo"))
    except (TypeError, ValueError):
        return 0


def _block(groups):
    """Coupled car sets -> flat per-car arrays + a train-level summary."""
    # The feed lists cars in coupler order (7,6,5,4 for a rear set) and the
    # coupled sets in either order (4634C: 7..4 then 3..1, 4653C: 1..3 then
    # 4..7). The drawer draws one bar per car left to right, so sort all cars
    # of the formation by car number, not per set.
    cars = [car for group in groups or [] for car in group.get("cars") or []]
    cong, temp, facilities = [], [], set()
    for car in sorted(cars, key=_car_no):
        if car.get("status") != 1:
            cong.append(None)
            temp.append(None)
            continue
        c = car.get("congestion")
        cong.append(c if isinstance(c, int) and c >= 0 else None)
        t = car.get("temp")
        temp.append(t if isinstance(t, int) and 0 <= t <= 50 else None)
        for code in car.get("facilities") or []:
            name = FACILITIES.get(code)
            if name:
                facilities.add(name)
    known = [c for c in cong if c is not None]
    temps = [t for t in temp if t is not None]
    block = {}
    if known:
        block["congestion_cars"] = cong
        block["congestion"] = {"class": _grade(max(known)), "rate": max(known)}
    if temps:
        block["temperature_cars"] = temp
    if facilities:
        block["facilities"] = sorted(facilities)
    return block


def fetch(src, index=None):
    src = src or common.Source()
    slugs = {}
    for area in AREAS:
        master = _get(src, f"area_{area}_master.json")
        for slug, meta in ((master or {}).get("lines") or {}).items():
            name = meta.get("name_ja") or meta.get("name")
            if name:
                slugs[slug] = name
    if not slugs:
        print("jrwest comfort: no line master")
        return []

    monitor = (_get(src, "trainmonitorinfo.json") or {}).get("trains") or {}
    if not monitor:
        print("jrwest comfort: trainmonitorinfo empty")
        return []

    with ThreadPoolExecutor(max_workers=8) as ex:
        lines = dict(ex.map(lambda s: (s, _get(src, f"{s}.json")), list(slugs)))

    # Only trains that actually carry a reading are worth resolving, and only
    # the slug they were seen under can decode their `pos` station code.
    picked = {}
    for slug, doc in lines.items():
        for t in (doc or {}).get("trains") or []:
            number = str(t.get("no") or "").strip()
            if not number or number in picked or number not in monitor:
                continue
            pos = str(t.get("pos") or "")
            if "_" not in pos:
                continue
            picked[number] = (slug, t, pos)

    need_st = {slug for slug, _, _ in picked.values()}
    with ThreadPoolExecutor(max_workers=8) as ex:
        sts = dict(ex.map(lambda s: (s, _get(src, f"{s}_st.json")), list(need_st)))
    names = {}
    for slug, doc in sts.items():
        names[slug] = {
            str(row["info"]["code"]): row["info"].get("name")
            for row in (doc or {}).get("stations") or []
            if (row.get("info") or {}).get("code")
        }

    out = []
    for number, (slug, t, pos) in picked.items():
        block = _block(monitor.get(number))
        if not block:
            continue
        a_code, _, b_code = pos.partition("_")
        station_names = names.get(slug, {})
        a_name = station_names.get(a_code)
        if not a_name:
            continue
        # pos is "<last called>_<adjacent>", but the adjacent code is the
        # *next* station only on down trains (direction=1). Up trains
        # (direction=0) report the station behind, so the train just left B
        # and is heading to A - verified against the timetable: dir1 anchors
        # matched A 32/36, dir0 anchors matched B 21/21 with A at 0. A
        # same-data A/B (2026-10-28 20:19) re-confirmed the split: 154/202
        # resolved with it vs 130/202 without, so do not "simplify" it away.
        if b_code and not b_code.startswith("#") and t.get("direction") == 0:
            b_name = station_names.get(b_code)
            if not b_name:
                continue
            station, next_station = b_name, a_name
        else:
            station = a_name
            next_station = station_names.get(b_code) if b_code and not b_code.startswith("#") else None
        # stopTime is the absolute moment the train stopped at the station it
        # last called at (the anchor above), server JST stored in UTC; it
        # identifies the run without any wall-clock drift.
        stop_min = None
        stamp = t.get("stopTime")
        if stamp:
            try:
                dt = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                jst = dt.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
                stop_min = jst.hour * 60 + jst.minute
            except ValueError:
                pass
        raw_name = slugs.get(slug, slug)
        pools = LINE_POOLS.get(raw_name, [raw_name])
        line = pools[0]
        out.append({
            "op": "JR-West",
            "line": line,
            "railway": f"JR-West.{slug}",
            "number": number,
            "type": t.get("displayType") or None,
            "direction": t.get("direction"),
            "from": station,
            "next": next_station,
            "stop": stop_min,
            "to": _dest(t.get("dest")),
            "cars": t.get("numberOfCars"),
            "official": None,
            # The position record carries the train's own delay, which the
            # resolver subtracts from `now` to recover when the run was
            # scheduled to pass the reported station. Without it every train
            # that left the station more than 3 minutes ago misses the window.
            "delay_min": int(t.get("delayMinutes") or 0),
            "pools": pools,
            "comfort": block,
        })
    return out
