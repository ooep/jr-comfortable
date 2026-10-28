# jr-comfortable

Per-train comfort data (congestion now; temperature / accessibility next) for
the trains already shown on [jr.suki.ing](https://jr.suki.ing), refreshed every
10 minutes by GitHub Actions.

`data/comfort.json` is the whole product: each record is one live train, already
pinned to the jr.suki.ing timetable key it will be drawn on, so the site needs
no join logic of its own.

## How a record gets its key

The mapping is not re-invented here. `ooep/delay` already answers "which suki
timetable key is this live train", and the site trusts that answer for delays.
This repo checks the delay repository out next to it and calls the same two
functions with the same anchors:

1. `suki.SukiIndex.match_direct(line, reported_station, direction, type, top=256)`
   — the candidate patterns for the line pool.
2. `delays.resolve_direct_by_time(...)` — the feed's own planned clock plus the
   feed's remaining stop sequence narrow the candidates to a single run.

Uniqueness stays strict: a record is written only when exactly one run survives
within ±3 minutes and its stop sequence agrees with the feed. Anything
ambiguous is dropped — showing another train's crowding level is worse than
showing nothing.

The difference from delay is the candidate set. Delay only looks at trains that
are running late; comfort looks at every train carrying a reading.

## Sources

| Source | Fields | Notes |
|---|---|---|
| JR-East (cloudrail, official app) | `congestionRate` (class + rate %), `greenCarCongestionRate`, cars | 首都圏 commuter lines; Shinkansen and limited expresses carry no readings |
| Osaka Metro | per-car congestion + temperature | next |
| JR-West | congestion / temperature / accessibility | official service exists, detail API not yet located |

## Output

```jsonc
{
  "version": 1,
  "generated_at": "2026-10-28T10:40:00+09:00",
  "date": "2026-10-28",          // JST date, gate against the site's calendar
  "count": 279,
  "candidates": 329,
  "stats": { "matched": 279, "unresolved": 47, "key_collision": 3 },
  "unresolved_by_line": { "京浜東北線": 8 },
  "trains": [{
    "op": "JR-East",
    "line": "中央線快速",
    "number": "533M",
    "from": "相模湖",              // station the feed reports the train approaching
    "to": "小淵沢",
    "cars": 10,
    "comfort": { "congestion": { "class": 1, "rate": 24 } },
    "scheduled": { "dep": "10:40", "origin": "相模湖", "stops": ["相模湖", "..."] },
    "suki": [{ "key": "9594", "size": 12, "overlap": 1.0 }]
  }],
  "by_suki_key": { "9594": [ /* same record(s) */ ] }
}
```

`scheduled.dep` is the **origin departure minute** of the resolved run — the
same basis as the site's `t.dep[0]`, which is how the browser tells two runs of
the same tmap key apart. `by_suki_key` is therefore key -> list.

`class` is the operator's crowding grade (1 roomy ... 3 crowded); `rate` is a
percentage and may exceed 100.

## Run locally

```bash
git clone https://github.com/ooep/delay ../delay
DELAY_DIR=../delay python3 -m comfort.bake --out data
DELAY_DIR=../delay python3 -m comfort.verify data/comfort.json
```

`--files 57 40` restricts the bake to a few cloudrail files for debugging.
`verify` re-checks every entry against `data/suki/trains_*.json` with the site's
own filter and aborts the workflow before the commit on any mismatch.

The bake also refuses to overwrite a snapshot with less than half the previous
count, so a silently broken source cannot replace good data with an empty board.
