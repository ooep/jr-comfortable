"""Tokyo Metro per-train congestion (Metro CrowdNavi static charts).

Tokyo Metro publishes, per station / direction / day type, one "列車別混雑
グラフ" page. Each row is one departure from that station and carries two
independent readings:

    bar       = 直近5日間の平均値 for that departure (levels 1-6)
    polyline  = 本日の混雑実績, one point per row, only for trains that have
                already left the station today (levels 1-6)

The chart is keyed by the timetable, not by a live train number, so the
anchor is (station, scheduled departure minute, destination). That is the
one case where ooep/delay's resolve_direct_by_time does not fit: its
`official` path demands the feed's remaining stop list, which CrowdNavi does
not publish (an empty list would reject every candidate with more than one
stop to run). The pool still comes from SukiIndex.match_direct and the clock
from SukiTimetable, and the uniqueness rules stay stricter than delay's:

    * destination must be the candidate pattern's terminal (parenthetical
      qualifiers such as 中野(東京都) tolerated),
    * exactly one run of that pattern departs the anchor within +/-1 min,
      and exactly one pattern survives; two patterns at the same minute means
      the row cannot be attributed -> drop the row.

Rows are collected only around the bake time (the drawer only ever shows
trains that are running now), which keeps comfort.json small enough for the
site to fetch every five minutes. Values use the 5-day average when today's
actual reading is not available yet, and are tagged `est` so the drawer can
label them as an average instead of a live measurement.
"""

import html
import re
from concurrent.futures import ThreadPoolExecutor

from baker import common, holidays, suki

CONFIG_URL = "https://tmcawebapp1.tokyometro.jp/v1/index2.html"

# How far around now to keep rows, in minutes of anchor-station departure.
# Running trains are the only ones the drawer can open, and the longest
# Tokyo Metro through-run is well under the 210-minute tail.
WINDOW_BACK = 210
WINDOW_FWD = 90

# Lines whose end-of-line station is not where most trains enter the line:
# Chiyoda's A direction is fed by JR through services via 綾瀬 (北綾瀬 only
# sees the short branch shuttles), and Marunouchi's branch trains leave from
# 方南町 rather than the main-line termini.
ANCHOR_OVERRIDES = {
    ("C", "A"): "ayase",
    ("M", "B"): "honancho",
}

# 5-day average / actual levels are 1-6; the drawer's chip has three shades.
LINE_CODE = {"銀座線": "G", "丸ノ内線": "M", "日比谷線": "H", "東西線": "T",
             "千代田線": "C", "有楽町線": "Y", "半蔵門線": "Z", "南北線": "N",
             "副都心線": "F"}


def _pick_line(lines, route_line):
    """A page can carry another operator's train (副都心線 page at 和光市
    lists 有楽町線 runs). Prefer the page's own line, else the first metro
    line the pattern belongs to."""
    if route_line in lines:
        return route_line
    for line in lines:
        if line.startswith(("東京メトロ", "都営")):
            return line
    return lines[0] if lines else route_line


def _railway(line):
    short = line[len("東京メトロ"):] if line.startswith("東京メトロ") else line
    code = LINE_CODE.get(short)
    return f"TokyoMetro.{code}" if code else ""


def _grade(level):
    if level <= 2:
        return 1
    if level <= 4:
        return 2
    return 3


_ROUTE_RE = re.compile(
    r"'([A-Za-z]+)': \{ name: \{ja: '([^']+)', en: '[^']*'\}, color: '[^']*', "
    r"icon: '[^']*', baseUrl: '([^']+)', directions: \{'A': \{ja: '([^']+)', "
    r"en: '[^']*'\}, 'B': \{ja: '([^']+)', en: '[^']*'\}\}, "
    r"disabledDetailTypes: \[[^\]]*\], stationNames: \{(.*?)\}, "
    r"pathNames: \{(.*?)\} \}",
    re.S,
)
_STATION_RE = re.compile(r"'([A-Za-z]+\d+)':\s*\{ja: '([^']+)'")
_PATH_RE = re.compile(r"'([A-Za-z]+\d+)':\s*'([^']+)'")
_ROW_RE = re.compile(
    r"(\d\d):(\d\d)発\s+(.+?)\s+(\S+?)始発\s*→\s*(.+?)行", re.S
)
_DATE_RE = re.compile(r"折れ線は(\d+)月(\d+)日の混雑実績")
_POINT_RE = re.compile(r"<polyline points='([^']+)'")
_BAR_RE = re.compile(r"class='chart-bar' style='width: ([0-9.]+)%")


def _text(src, url, fixture):
    if src.offline:
        try:
            return src.offline.get_text(fixture)
        except FileNotFoundError:
            return None
    try:
        return common.get_text(url, headers={"Referer": "https://www.tokyometro.jp/"})
    except Exception as exc:  # noqa: BLE001 - one page must not sink the run
        print(f"tokyometro {url.rsplit('/', 1)[-1]}: {type(exc).__name__}: {str(exc)[:120]}")
        return None


def _routes(src):
    """Route table published in the CrowdNavi app shell.

    The app builds every iframe URL from this object, so reading it keeps us
    on the same paths the official page opens (station slugs change with
    rebuilds; the shell is the source of truth).
    """
    text = _text(src, CONFIG_URL, "tokyometro-index2.html")
    if not text:
        return []
    out = []
    for code, name, base, dir_a, dir_b, names, paths in _ROUTE_RE.findall(text):
        stations = dict(_STATION_RE.findall(names))
        slugs = dict(_PATH_RE.findall(paths))
        main = [c for c in stations if not c.startswith("Mb")]
        if len(main) < 2 or not slugs.get(main[0]) or not slugs.get(main[-1]):
            continue
        out.append({
            "code": code,
            "name": name,
            "base": base,
            "dirs": {"A": dir_a, "B": dir_b},
            "stations": stations,
            "slugs": slugs,
            "ends": (main[0], main[-1]),
        })
    return out


def _origin(route, direction):
    """The end station where that direction's trains enter the line.

    Direction labels name the far end (渋谷方面), so the page with the most
    rows is the *other* end. 北綾瀬-style branch ends are closer to the
    label than the trunk, hence the substring test; overrides cover the two
    lines where the published end is not where trains actually start.
    """
    first, last = route["ends"]
    label = route["dirs"][direction].split("・")[0].split("方面")[0]
    first_ja = route["stations"][first]

    def names_end(station_ja):
        return station_ja == label or station_ja.startswith(label) or station_ja.endswith(label)

    origin = last if names_end(first_ja) else first
    override = ANCHOR_OVERRIDES.get((route["code"], direction))
    if override:
        for code, ja in route["stations"].items():
            if route["slugs"].get(code) == override:
                return code
    return origin


def _deps(stt, key, anchor_idx):
    """[(departure minute at anchor, run origin minute)] for every run."""
    out = []
    for rec in stt._series.get(str(key)) or []:
        runs = rec["runs"]
        dwells = rec.get("dwells") or []
        if anchor_idx > len(runs):
            continue
        acc = rec["dep0"]
        for i in range(anchor_idx):
            acc += runs[i]
            if i < len(dwells):
                acc += dwells[i]
        dep = acc + (dwells[anchor_idx] if anchor_idx < len(dwells) else 0)
        out.append((dep, rec["dep0"]))
    return out


def _anchor_idx(stops, want):
    try:
        return stops.index(want)
    except ValueError:
        hits = [
            i for i, s in enumerate(stops)
            if s.startswith(want) and (s[len(want):].startswith("(") or s[len(want):] in _PREF)
        ]
        return hits[0] if len(hits) == 1 else None


_PREF = {"都", "道", "府", "県", "(東京都)", "(千葉県)", "(埼玉県)", "(神奈川県)",
         "(茨城県)", "(栃木県)", "(群馬県)", "(山梨県)", "(静岡県)"}


def _base(name):
    """Station name without its disambiguating bracket.

    CrowdNavi spells 押上〈スカイツリー前〉 / 明治神宮前〈原宿〉 while the
    suki index uses the short form for some lines, so both sides drop the
    bracketed qualifier before comparing (norm_station only handles parens).
    """
    return suki.norm_station(re.sub(r"〈.*?〉", "", name or "").strip())


def _term_ok(term, want):
    if term == want:
        return True
    if not term.startswith(want):
        return False
    rest = term[len(want):]
    return rest.startswith("(") or rest in _PREF


def _station_lines(index):
    """station -> every suki line with a pattern calling there.

    A CrowdNavi page is scoped by station, not by operator line: 和光市's
    Fukutoshin page also lists Yurakucho trains to 新木場, and 目黒's Namboku
    page mixes in Toei Mita services. Restricting the pool to the page's own
    line dropped those rows, so the pool is every line through the station;
    the destination terminal still has to match the published 行先.
    """
    out = {}
    for rec in index.trains.values():
        for line in rec.get("lines") or []:
            for station in rec["s_jas"]:
                out.setdefault(_base(station), set()).add(line)
    return out


def _lookup(index, stt, pool, want, wantd, minute, tol, wanto=None):
    hits = []
    for key in pool:
        stops = [_base(s) for s in index._stops_norm[key]]
        if not _term_ok(stops[-1], wantd):
            continue
        if wanto is not None and stops[0] != wanto:
            continue
        idx = _anchor_idx(stops, want)
        if idx is None:
            continue
        near = [(abs(dep - minute), dep0) for dep, dep0 in _deps(stt, key, idx)
                if abs(dep - minute) <= tol]
        if len(near) > 1:
            if wanto is None:
                return None  # a pattern runs twice in the window: row is unprovable
            continue
        if len(near) == 1:
            hits.append((near[0][0], key, near[0][1]))
    return hits


def _match(index, stt, lines, station, minute, dest, origin):
    """(suki key, run origin minute, key lines, clock gap) for one departure.

    Metro-native segments match the timetable to the minute (pass 1). Runs
    entering from another operator (東武/東急/JR through services) accumulate
    the baker's estimated run times and can sit up to 3 minutes off the
    published clock; that wider window is opened only for patterns whose own
    first stop is the row's 始発 station, which is what keeps the row
    attributable (pass 2).
    """
    want = _base(station)
    wantd = _base(dest)
    wanto = _base(origin)
    pool = {}
    for line in lines:
        for cand in index.match_direct(line, want, None, None, top=256) or []:
            pool[str(cand["key"])] = cand

    def pick(hits):
        if not hits:
            return None
        exact = [h for h in hits
                 if _base(index._stops_norm[h[1]][0]) == wanto]
        if exact:
            hits = exact
        best = min(h[0] for h in hits)
        winners = [h for h in hits if h[0] == best]
        if len(winners) != 1:
            return None
        gap, key, dep0 = winners[0]
        return key, dep0, index.trains[key].get("lines") or [], gap

    got = pick(_lookup(index, stt, pool, want, wantd, minute, 1))
    if got:
        return got
    return pick(_lookup(index, stt, pool, want, wantd, minute, 3, wanto))


def _page(src, route, code, direction, day_type):
    slug = route["slugs"].get(code)
    if not slug:
        return None
    url = f"{route['base']}train/{slug}_{direction}_{day_type}.html"
    return _text(src, url, f"tokyometro-{slug}-{direction}-{day_type}.html"), url


def _actual_levels(text, n_rows, today):
    """Today's measured level per row index, keyed off the chart polyline.

    The polyline starts at the first row and is contiguous while readings
    exist; y = 22 + 44*row and x = level * 100/6. A missing or stale date
    disables it entirely - the bars are then still usable as averages.
    """
    m = _DATE_RE.search(text)
    if not m or (int(m.group(1)), int(m.group(2))) != (today.month, today.day):
        return {}
    m = _POINT_RE.search(text)
    if not m:
        return {}
    out = {}
    for point in m.group(1).split():
        try:
            x, y = (float(v) for v in point.split(","))
        except ValueError:
            return {}
        idx = (y - 22) / 44
        if idx < 0 or idx >= n_rows or abs(idx - round(idx)) > 1e-6:
            return {}
        level = round(x / (100 / 6))
        if not 1 <= level <= 6:
            return {}
        out[int(round(idx))] = level
    return out


def _rows(text):
    out = []
    for chunk in text.split("<div class='chart-row' data-dept-time='")[1:]:
        if len(chunk) < 5 or not chunk[:4].isdigit():
            continue
        block = re.search(r"<div class='train-label'>(.*?)</div>", chunk, re.S)
        if not block:
            continue
        label = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", block.group(1))).split())
        row = _ROW_RE.search(label)
        bar = _BAR_RE.search(chunk)
        if not row or not bar:
            continue
        hour, minute = int(row.group(1)), int(row.group(2))
        out.append({
            "minute": hour * 60 + minute,
            "type": row.group(3).strip(),
            "origin": row.group(4).strip(),
            "dest": row.group(5).strip(),
            "bar": round(float(bar.group(1)) / (100 / 6)),
        })
    return out


def fetch(src, index, stt, now):
    """Comfort records for every Tokyo Metro train near the bake time."""
    routes = _routes(src)
    if not routes:
        print("tokyometro: route table unavailable, skipping")
        return []
    day_type = (
        "holiday"
        if now.weekday() >= 5 or holidays.is_holiday_today(now.date().isoformat())
        else "weekday"
    )
    lo, hi = now.hour * 60 + now.minute - WINDOW_BACK, now.hour * 60 + now.minute + WINDOW_FWD
    station_lines = _station_lines(index)
    jobs = []
    for route in routes:
        line = route["name"]
        if not index._line_pool.get(line):
            line = "東京メトロ" + line
        if not index._line_pool.get(line):
            print(f"tokyometro: no suki line for {route['name']}, skipping")
            continue
        for direction in ("A", "B"):
            code = _origin(route, direction)
            jobs.append((route, line, code, direction))

    def one(job):
        route, line, code, direction = job
        got = _page(src, route, code, direction, day_type)
        return job, got

    out = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        pages = list(pool.map(one, jobs))
    for (route, line, code, direction), got in pages:
        if not got or not got[0]:
            continue
        text, url = got
        rows = _rows(text)
        actual = _actual_levels(text, len(rows), now)
        name = route["stations"].get(code, code)
        picked = []
        in_window = 0
        for i, row in enumerate(rows):
            if not (lo <= row["minute"] <= hi) or row["minute"] >= 1440:
                continue
            in_window += 1
            hit = _match(index, stt, station_lines.get(_base(name)) or [line],
                         name, row["minute"], row["dest"], row["origin"])
            if hit:
                picked.append((i, row) + hit)

        # One run cannot be two page rows. CrowdNavi lists neighbouring
        # locals and rapids whose clocks the baker places on the same run
        # (10:10 各駅停車 resolved onto the 10:08 快速); keep the row whose
        # clock is closest to that run and drop the slot when two rows tie.
        slots = {}
        for pos, item in enumerate(picked):
            slots.setdefault((item[2], item[3]), []).append(pos)
        drop = set()
        for slot in slots.values():
            if len(slot) < 2:
                continue
            best = min(picked[p][5] for p in slot)
            winners = [p for p in slot if picked[p][5] == best]
            keep = winners[0] if len(winners) == 1 else None
            drop.update(p for p in slot if p != keep)

        kept = 0
        for pos, (i, row, key, dep0, key_lines, _gap) in enumerate(picked):
            if pos in drop:
                continue
            level = actual.get(i)
            est = level is None
            if est:
                level = row["bar"]
            if not 1 <= level <= 6:
                continue
            comfort = {"congestion": {"class": _grade(level), "level": level}}
            if est:
                comfort["congestion"]["est"] = True
            matched = _pick_line(key_lines, line)
            out.append({
                "op": "東京都交通局" if matched.startswith("都営") else "東京メトロ",
                "line": matched,
                "railway": _railway(matched),
                "number": None,
                "type": row["type"],
                "from": _base(name),
                "to": row["dest"],
                "cars": None,
                "comfort": comfort,
                "basis": "5day_avg" if est else "today",
                "suki_key": key,
                "scheduled_dep": dep0,
                "source": url,
            })
            kept += 1
        print(f"tokyometro {route['name']} {direction} {code} {name}: "
              f"{in_window} rows in window, {kept} mapped, {len(drop)} contested")

    # The Yurakucho and Fukutoshin pages share the 和光市 corridor, so one
    # train can be listed on both. Same key and same origin departure is the
    # same run: keep it once when the readings agree, drop it when they do
    # not (the pages disagree about the level, so neither is trustworthy).
    by_slot = {}
    for rec in out:
        by_slot.setdefault((rec["suki_key"], rec["scheduled_dep"]), []).append(rec)
    final = []
    for recs in by_slot.values():
        levels = {(r["comfort"]["congestion"]["level"], r["basis"]) for r in recs}
        if len(levels) == 1:
            final.append(recs[0])
    return final
