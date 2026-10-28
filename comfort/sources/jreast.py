"""JR-East per-train congestion from the official app feed (cloudrail).

The delay baker (ooep/delay) reads the same documents but keeps only delayed
trains and only the delay fields. Comfort wants *every* train that carries a
valid congestion reading, so this module re-walks the feed and keeps the
comfort fields plus the anchors (reported station, remaining stops, official
clock) that ooep/delay's resolver needs to pin a jr.suki.ing key.

Field shapes verified live 2026-10-28 against transaction 2.0.0:

    organizationBasicInfo[0].congestionRate       = {class, rate, valid, isTarget}
    organizationBasicInfo[0].greenCarCongestionRate = {class, rate, valid, hasGreenCars, isTarget}

`rate` is a percentage and may exceed 100 (111 observed). `class` is the
operator's own crowding grade (1 = roomy, 3 = crowded). Shinkansen (88) and
limited expresses (903 あずさ) answer valid=false every time - the equipment
is simply not fitted, so those files yield nothing and cost only a download.
"""

from baker import jreast_app as ja

# jr.suki.ing has no 埼京線 / 京浜東北線 line of its own - those services are
# timetabled under the physical lines they run on. delay's single-pool choice
# (川越線 / 根岸線) only covers the tail of each route, so trains reported at
# 大宮 or 赤羽 never find a candidate. Widening the pool is safe because the
# resolver still requires a unique run within 3 minutes, a matching terminal
# and a stop sequence consistent with the feed's own remaining stops.
POOL_OVERRIDES = {
    "JR-East.Saikyo": ["川越線", "赤羽線", "東北本線", "山手線"],
    "JR-East.KeihinTohoku": ["根岸線", "東北本線", "東海道本線"],
    # 武蔵野線 trains run onto 京葉線 to 海浜幕張/東京; suki tags those keys
    # 京葉線, so the 武蔵野線 pool alone never contains the reported tail.
    "JR-East.Musashino": ["武蔵野線", "京葉線"],
    # 横須賀線 trains continue onto 総武快速 (大船→津田沼), and suki tags that
    # through-run pattern 東海道本線+総武本線 rather than 横須賀線.
    "JR-East.Yokosuka": ["横須賀線", "総武本線", "東海道本線"],
}


def _anchors(train):
    """Reported station + remaining stops + feed-provided official clock.

    Mirrors the extraction in baker.jreast_app.fetch; the resolver treats the
    official departure as the matching anchor, so it has to agree exactly.
    """
    stops_raw = []
    ja._find_stations(train, stops_raw)
    remaining = []
    seen = set()
    for s in stops_raw:
        if s.get("pass"):
            continue
        name = (s.get("stationName") or {}).get("ja")
        if not name:
            continue
        # cloudrail nests both directions under kiten/shuten, so a recursive
        # scan can loop (上野->東京->上野). First run only.
        if name in seen:
            break
        seen.add(name)
        remaining.append({
            "name": name,
            "plan": ja._hm(s.get("planArrival")) or ja._hm(s.get("planDeparture")),
        })
    first = remaining[0]["name"] if remaining else None
    official = None
    if remaining:
        dep_min = ja._hhmm_min(remaining[0]["plan"])
        if dep_min is not None:
            official = {
                "departure": dep_min,
                "stations": [r["name"] for r in remaining[1:]],
                "terminal": None,
            }
    return first, remaining, official


def _comfort_block(org):
    cr = org.get("congestionRate") or {}
    gcr = org.get("greenCarCongestionRate") or {}
    block = {}
    if cr.get("valid"):
        block["congestion"] = {
            "class": cr.get("class"),
            "rate": cr.get("rate"),
        }
    if gcr.get("valid"):
        block["green_car"] = {
            "class": gcr.get("class"),
            "rate": gcr.get("rate"),
        }
    elif gcr.get("hasGreenCars"):
        block["green_car_present"] = True
    return block


def fetch(src, files=None):
    """Return comfort records for every train with a valid reading."""
    if not ja._register(src):
        return []
    if src.offline:
        ver = src.offline_json("jr-app-version.json")
    else:
        ver = ja.common.get_json(
            f"{ja.FD}/getresource?name=jrelines/version/version.json",
            headers=ja._headers(ja._UID),
        )
    tx_url = ver["transaction"]["current"]["url"]
    wanted = files or ja.PULL_FILES

    def _one(file_id):
        try:
            doc = ja._get(src, f"{tx_url}train_{file_id}.json")
        except Exception as exc:  # noqa: BLE001 - one file must not sink the run
            print(f"comfort jreast {file_id}: {type(exc).__name__}: {str(exc)[:140]}")
            return file_id, None
        return file_id, doc

    # 63 files at ~1.2s serial is 75s+ of pure network wait; six workers keep
    # the WAF quiet (the delay baker runs the same shape without 429s).
    from concurrent.futures import ThreadPoolExecutor
    if src.offline:
        pulled = [_one(f) for f in wanted]
    else:
        with ThreadPoolExecutor(max_workers=6) as ex:
            pulled = list(ex.map(_one, wanted))

    out = []
    seen_train = set()
    for file_id, doc in pulled:
        if not isinstance(doc, dict):
            continue
        trains = []
        ja._walk_trains(doc, trains)
        for t in trains:
            org = (t.get("organizationBasicInfo") or [{}])[0]
            block = _comfort_block(org)
            if not block:
                continue
            first, remaining, official = _anchors(t)
            if not first:
                continue
            meta = ja.LINES.get(
                str(org.get("lineCode") or ""),
                ja.LINES.get(str(file_id), ja.DEFAULT_LINE),
            )
            nickname = (org.get("nickname") or {}).get("ja")
            dest = ((org.get("destination") or {}).get("fullName") or {}).get("ja")
            if official is not None:
                official["terminal"] = dest
            number = str(t.get("trainNo") or "").strip()
            # One physical train is published in several route files (中央線快速
            # rides in train_57 and train_58; 上野東京ライン shares runs with
            # 東海道線). Its own clock and reported station are the identity.
            ident = (number, official["departure"] if official else first)
            if ident in seen_train:
                continue
            seen_train.add(ident)
            out.append({
                "op": "JR-East",
                "line": meta["board"],
                "railway": meta["railway"],
                "number": number,
                "type": nickname,
                "from": first,
                "to": dest,
                "cars": (t.get("cars") or {}).get("value"),
                "upcoming": [r["name"] for r in remaining],
                "official": official,
                # cloudrail's remaining-stop list stops at the JR boundary
                # (埼京線->新木場, 常磐線各停->小田急): let the resolver accept
                # a candidate that continues past it, prefix + dest still tie it.
                "max_extra": None,
                "suki_pools": POOL_OVERRIDES.get(
                    meta["railway"], meta.get("pools") or [meta["suki"]]
                ),
                "comfort": block,
                "_file": file_id,
                "_line_code": org.get("lineCode"),
            })
    return out
