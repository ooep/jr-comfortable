"""Osaka Metro per-car congestion + temperature (official web SPA API).

baker/osakametro.py already pulls the same feed for the delay board, but it
only publishes a standalone position payload. The rows carry, per train:

    fromStationCode / toStationCode / terminalStationCode  ("M21", "C09", ...)
    directionId ("A" = 上り, "B" = 下り), progress ("0" at the station,
    "1.0" arrived at toStation), vehicleNumber
    trainCarCongestionList  = "44,26,24,..."  per-car percentage
    trainCarTemperatureList = "24.5,24,..."   per-car Celsius, 0 = no sensor

The API has no train number and no timetabled clock, so the anchor is
position + time: at progress 0 the train is due out of fromStation now, at
progress 1.0 it is due into toStation now. resolve_direct_by_time then has to
find exactly one run of one stop-pattern within +/-3 minutes with the same
terminal - anything else is dropped (宁漏不误报).

Station codes are not published as a master file (every station-ish endpoint
answers 403). They are derived instead: jr.suki.ing's own stop lists are the
authoritative station order, and Osaka Metro numbering is contiguous along
each line, so codes map to positions once one (station, code) anchor is known
(anchor pairs are public numbering: 箕面萱野=M06 ... なかもず=M30, 夢洲=C09,
天下茶屋=K20, ...). Both directions are tried and the one that reproduces
adjacent from/to pairs on today's live rows wins; a line that cannot be
aligned confidently is skipped entirely.
"""

from baker import osakametro as om

# route_code -> (suki line name, railway id, anchor station, anchor code).
# Anchor codes are the public station numbers printed on every platform.
LINES = {
    "1": ("御堂筋線", "OsakaMetro.Midosuji", "箕面萱野", 6),
    "2": ("谷町線", "OsakaMetro.Tanimachi", "大日", 11),
    "3": ("四つ橋線", "OsakaMetro.Yotsubashi", "西梅田", 11),
    "4": ("中央線", "OsakaMetro.Chuo", "夢洲", 9),
    "5": ("千日前線", "OsakaMetro.Sennichimae", "野田阪神", 11),
    "6": ("堺筋線", "OsakaMetro.Sakaisuji", "天下茶屋", 20),
    "7": ("長堀鶴見緑地線", "OsakaMetro.NagahoriTsurumiRyokuchi", "大正", 11),
    "8": ("今里筋線", "OsakaMetro.Imazatosuji", "井高野", 11),
    "9": ("南港ポートタウン線", "OsakaMetro.NewTram", "コスモスクエア", 9),
}

# Crowding grade for the drawer's three colours, from the per-car max rate.
# Osaka Metro's own app paints cars by percentage; the site's chip only has
# three steps, so scale to them (>100% readings do occur at peak).
def _grade(rate):
    if rate <= 50:
        return 1
    if rate <= 100:
        return 2
    return 3


def _num(code):
    if not code:
        return None
    s = str(code).strip()
    if not s or not s[1:].isdigit():
        return None
    return int(s[1:])


def _order(index, line):
    """Full station order of a line, merged from every stop pattern."""
    pats = [
        list(v["s_jas"]) for v in index.trains.values()
        if line in (v.get("lines") or [])
    ]
    if not pats:
        return []
    pats.sort(key=len, reverse=True)
    base = list(pats[0])
    changed = True
    while changed:
        changed = False
        for p in pats[1:]:
            for i, s in enumerate(p):
                if s in base:
                    continue
                prev = p[i - 1] if i else None
                nxt = p[i + 1] if i + 1 < len(p) else None
                if prev in base:
                    base.insert(base.index(prev) + 1, s)
                    changed = True
                elif nxt in base:
                    base.insert(base.index(nxt), s)
                    changed = True
    return base


def _maps(order, anchor, acode):
    """Both numbering directions as {code_str: order_index}."""
    ai = next(
        (i for i, s in enumerate(order) if s == anchor or s.startswith(anchor)),
        None,
    )
    if ai is None:
        return []
    out = []
    for sign in (1, -1):
        cm = {}
        for i in range(len(order)):
            n = acode + sign * (i - ai)
            if 0 <= n <= 99:
                cm[str(n)] = i
        out.append(cm)
    return out


def _pick_map(order, anchor, acode, trains):
    """Choose the numbering direction that keeps live from/to codes adjacent."""
    best = None
    for cm in _maps(order, anchor, acode):
        ok = tot = 0
        for t in trains:
            a = cm.get(str(_num(t.get("from_station"))))
            b = cm.get(str(_num(t.get("to_station"))))
            if a is None or b is None:
                continue
            tot += 1
            if abs(a - b) == 1:
                ok += 1
        if tot and (best is None or ok / tot > best[0]):
            best = (ok / tot, cm, tot)
    # Fewer than three adjacent observations is not evidence; a mis-anchored
    # line would attach another train's crowding level.
    if best and best[2] >= 3 and best[0] >= 0.75:
        return best[1]
    return None


def _car_ints(raw):
    out = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if part == "":
            continue
        try:
            out.append(int(float(part)))
        except ValueError:
            out.append(None)
    return out


def _car_temps(raw):
    out = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if part == "":
            continue
        try:
            v = float(part)
        except ValueError:
            out.append(None)
            continue
        # 0 is the feed's "no sensor on this car" sentinel, not 0 degrees.
        out.append(None if v <= 0 else v)
    return out


def _comfort(cong_raw, temp_raw):
    cong = _car_ints(cong_raw)
    temp = _car_temps(temp_raw)
    valid = [c for c in cong if c is not None]
    if not valid and not any(t is not None for t in temp):
        return None
    block = {}
    if valid:
        block["congestion_cars"] = cong
        block["congestion"] = {"class": _grade(max(valid)), "rate": max(valid)}
    if any(t is not None for t in temp):
        block["temperature_cars"] = temp
    return block


def fetch(src, index, route_names=None):
    """Return comfort records for every Osaka Metro train with a reading."""
    raw = om.fetch(src)
    if not raw or not raw.get("ok"):
        return []
    route_names = route_names or raw.get("routes") or {}
    out = []
    for code, line, railway, anchor, acode in [
        (code, *meta) for code, meta in LINES.items()
    ]:
        trains = raw["body"].get(code) or []
        if not trains:
            continue
        order = _order(index, line)
        if not order:
            print(f"comfort osakametro {code}: no suki stop list for {line}")
            continue
        cm = _pick_map(order, anchor, acode, trains)
        if not cm:
            print(f"comfort osakametro {code}: station codes did not align")
            continue
        for t in trains:
            cong_raw = t.get("congestion") or ""
            temp_raw = t.get("temperature") or ""
            block = _comfort(cong_raw, temp_raw)
            if not block:
                continue
            prog = str(t.get("progress") or "")
            f_idx = cm.get(str(_num(t.get("from_station"))))
            to_idx = cm.get(str(_num(t.get("to_station"))))
            term_idx = cm.get(str(_num(t.get("terminal"))))
            if f_idx is None or term_idx is None:
                continue
            # progress 0 = standing at fromStation, 1.0 = arrived at toStation;
            # anything between is un-timed and would need interpolation, so it
            # is dropped rather than guessed.
            if prog in ("0", "0.0"):
                anchor_idx = f_idx
            elif prog in ("1", "1.0") and to_idx is not None:
                anchor_idx = to_idx
            else:
                continue
            out.append({
                "op": "OsakaMetro",
                "line": route_names.get(code) or line,
                "railway": railway,
                "number": None,
                "vehicle": t.get("vehicle"),
                "type": None,
                "from": order[anchor_idx],
                "to": order[term_idx],
                "pools": [line],
                "official": None,
                "progress": prog,
                "comfort": block,
            })
    return out
