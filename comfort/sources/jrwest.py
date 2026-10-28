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

import re
from concurrent.futures import ThreadPoolExecutor

from baker import common

BASE = "https://www.train-guide.westjr.co.jp/api/v3"
AREAS = ["kinki", "hokuriku", "okayama", "hiroshima", "sanin"]

# Same suki-side names delay uses; the site's timetable keys are the official
# 本線 names, while train-guide markets the services (JR京都線, 大和路線...).
LINE_NAME_REWRITES = {
    "JR京都線": "東海道本線",
    "JR神戸線・山陽線": "山陽本線",
    "山陽線": "山陽本線",
    "JR宝塚線・福知山線": "福知山線",
    "JR宝塚線": "福知山線",
    "大和路線": "関西本線",
    "学研都市線": "片町線",
    "北陸線・琵琶湖線": "北陸本線",
    "山陰線": "山陰本線",
    "きのくに線": "紀勢本線",
    "万葉まほろば線": "桜井線",
    "JRゆめ咲線": "桜島線",
    "阪和線・羽衣線": "阪和線",
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
    return None if text in _DEST_IGNORE else text


def _car_no(car):
    try:
        return int(car.get("carNo"))
    except (TypeError, ValueError):
        return 0


def _block(groups):
    """Coupled car sets -> flat per-car arrays + a train-level summary."""
    cong, temp, facilities = [], [], set()
    for group in groups or []:
        # The feed lists cars in coupler order (4,3,2,1 for a 4-car set); the
        # drawer draws one bar per car left to right, so sort by car number.
        for car in sorted(group.get("cars") or [], key=_car_no):
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
        at = pos.split("_")[0]
        station = names.get(slug, {}).get(at)
        if not station:
            continue
        raw_name = slugs.get(slug, slug)
        line = LINE_NAME_REWRITES.get(raw_name, raw_name)
        out.append({
            "op": "JR-West",
            "line": line,
            "railway": f"JR-West.{slug}",
            "number": number,
            "type": t.get("displayType") or None,
            "from": station,
            "to": _dest(t.get("dest")),
            "cars": t.get("numberOfCars"),
            "official": None,
            "pools": [line],
            "comfort": block,
        })
    return out
