"""Opening vs closing CFB lines from CFBD /lines (one call per season, cached in state/cache/cfbd_api).
Writes state/cache/cfb_lines_oc.csv: season,game_id,provider,spread_open,spread,total_open,total (HOME spread).
Provider preference: Bovada, DraftKings, ESPN Bet, consensus, then any line carrying an opener.
Used by the institutional hybrid simulation (marv/institutional.py) for the steam adjustment backtest.
Usage (needs CFBD_API_KEY in the environment): .venv/bin/python tools/cfb_open_lines.py [first_season] [last_season]
"""
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marv.data.cfbd import CFBDClient  # noqa: E402

PREF = ("bovada", "draftkings", "espn bet", "consensus", "williamhill (new jersey)", "teamrankings", "numberfire")
st = Path(__file__).resolve().parents[1] / "state"
lo, hi = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (2023, 2026)
cl = CFBDClient(os.environ.get("CFBD_API_KEY", ""), cache_dir=st / "cache" / "cfbd_api", ttl_hours=24 * 30)
out = st / "cache" / "cfb_lines_oc.csv"
old = {}
if out.exists():
    for r in csv.DictReader(out.open()):
        old[(r["season"], r["game_id"])] = r
for y in range(lo, hi + 1):
    if y == hi:
        cl.ttl = 6 * 3600  # current season: refresh openers / closers
    try:
        data = cl._get("/lines", year=y, seasonType="regular")
    except Exception as e:  # noqa: BLE001
        print("fail", y, type(e).__name__)
        continue
    n = 0
    for g in data or []:
        ls = [ln for ln in g.get("lines") or [] if ln.get("spreadOpen") is not None or ln.get("overUnderOpen") is not None]
        if not ls:
            continue
        ls.sort(key=lambda ln: PREF.index((ln.get("provider") or "").lower())
                if (ln.get("provider") or "").lower() in PREF else len(PREF))
        ln = ls[0]
        old[(str(y), str(g.get("id")))] = {"season": y, "game_id": g.get("id"), "provider": ln.get("provider"),
                                           "spread_open": ln.get("spreadOpen"), "spread": ln.get("spread"),
                                           "total_open": ln.get("overUnderOpen"), "total": ln.get("overUnder")}
        n += 1
    print(y, len(data or []), "games,", n, "with openers", flush=True)
with out.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["season", "game_id", "provider", "spread_open", "spread", "total_open", "total"])
    w.writeheader()
    for k in sorted(old):
        w.writerow(old[k])
print("wrote", out, len(old))
