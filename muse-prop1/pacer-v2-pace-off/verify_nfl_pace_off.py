"""Verify the --no-pace change covers NFL rows: unit flag effect, backtest harness.

Synthetic rows only — this proves the machinery, NOT profitability.
The real verdict needs the VM's graded.jsonl (NFL rows) plus team_stats.json.

Mirrors verify_pace_off.py (NBA). The flag itself is sport-agnostic: project()
forces pace_mult = 1.0 for any stat, and production_shift applies pace to
football stats (py/ry/recy/rec/td) exactly as it does to basketball stats.
This script proves that with NFL rows instead of assuming it.
"""

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pacer_v2.model import decide, default_model, project
from pacer_v2.parse import parse_row

# 1. Flag effect: same NFL row, pace 1.25 in team-stats.
row = {
    "ts": "2026-10-11T20:00:00+00:00", "game_id": "nfl1", "player": "QB A", "stat": "py",
    "line": 275.5, "side": "over", "cur": 120, "minutes_played": 30, "period": 3,
    "clock": "7:30", "margin": 3, "season_rate": 4.5,
    "p_model_raw": 0.80, "bid": 0.50, "ask": 0.50, "price": 0.50,
    "b2b": False, "result": True, "team": "KC", "opponent": "BUF", "league": "nfl",
}
stats = {"games": {"nfl1": {"pace": 1.25}}}
assert project(parse_row(row), stats)["sport"] == "football"
on = decide(parse_row(row), default_model(), stats, pace_enabled=True)
off = decide(parse_row(row), default_model(), stats, pace_enabled=False)
base = decide(parse_row(row), default_model(), None, pace_enabled=True)
print("sport     :", project(parse_row(row), stats)["sport"])
print("pace ON : pace_mult =", on["pace_mult"], "p_pacer =", on["p_pacer"])
print("pace OFF: pace_mult =", off["pace_mult"], "p_pacer =", off["p_pacer"], "gaps =", off["gaps"])
print("no stats: pace_mult =", base["pace_mult"], "p_pacer =", base["p_pacer"])
assert on["pace_mult"] == 1.25 and on["p_pacer"] > base["p_pacer"] + 0.05
assert off["pace_mult"] == 1.0 and off["p_pacer"] == base["p_pacer"]
assert "pace_disabled" in off["gaps"]
print("FLAG EFFECT (NFL): PASS — pace off returns the exact no-pace probability.\n")

# 2. Backtest harness end-to-end on synthetic graded NFL rows, both arms.
rows = []
for g in range(10):
    for i in range(12):
        r = dict(row)
        r["game_id"] = f"nfl{g}"
        r["ts"] = f"2026-10-{g + 1:02d}T20:{i:02d}:00+00:00"
        r["player"] = f"QB{i}"
        r["p_model_raw"] = 0.55 + 0.02 * (i % 5)
        r["price"] = 0.45
        r["result"] = (i % 3 != 0)
        rows.append(r)
Path("/tmp/pacer-nfl-check.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
Path("/tmp/pacer-nfl-stats.json").write_text(json.dumps({"games": {f"nfl{g}": {"pace": 1.20} for g in range(10)}}))
for label, extra in (("pace ON ", []), ("pace OFF", ["--no-pace"])):
    out = subprocess.run(
        [sys.executable, "-m", "pacer_v2", "backtest",
         "--in", "/tmp/pacer-nfl-check.jsonl", "--team-stats", "/tmp/pacer-nfl-stats.json"] + extra,
        cwd=str(HERE), capture_output=True, text=True)
    print(f"--- backtest {label} (NFL) ---")
    print(out.stdout.strip())
    assert out.returncode == 0, out.stderr
print("\nBACKTEST HARNESS (NFL): PASS — both arms run end-to-end on synthetic NFL rows.")
