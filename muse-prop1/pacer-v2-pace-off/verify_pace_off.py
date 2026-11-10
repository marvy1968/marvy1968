"""Verify the --no-pace change: unit tests, flag effect, backtest harness end-to-end.

Synthetic rows only — this proves the machinery, NOT profitability.
The real verdict needs the VM's graded.jsonl.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/tmp/pacer-v2")

from pacer_v2.model import decide, default_model
from pacer_v2.parse import parse_row

# 1. Flag effect: same row, pace 1.25 in team-stats.
row = {
    "ts": "2026-02-01T20:00:00+00:00", "game_id": "g1", "player": "A", "stat": "pts",
    "line": 22.5, "side": "over", "cur": 8, "minutes_played": 14, "period": 2,
    "clock": "6:00", "fouls": 1, "margin": 2, "season_rate": 0.7,
    "p_model_raw": 0.80, "bid": 0.50, "ask": 0.50, "price": 0.50,
    "b2b": False, "result": True,
}
stats = {"games": {"g1": {"pace": 1.25}}}
on = decide(parse_row(row), default_model(), stats, pace_enabled=True)
off = decide(parse_row(row), default_model(), stats, pace_enabled=False)
base = decide(parse_row(row), default_model(), None, pace_enabled=True)
print("pace ON : pace_mult =", on["pace_mult"], "p_pacer =", on["p_pacer"], "gaps =", on["gaps"])
print("pace OFF: pace_mult =", off["pace_mult"], "p_pacer =", off["p_pacer"], "gaps =", off["gaps"])
print("no stats: pace_mult =", base["pace_mult"], "p_pacer =", base["p_pacer"])
assert on["pace_mult"] == 1.25 and on["p_pacer"] > base["p_pacer"] + 0.05
assert off["pace_mult"] == 1.0 and off["p_pacer"] == base["p_pacer"]
assert "pace_disabled" in off["gaps"]
print("FLAG EFFECT: PASS — pace off returns the exact no-pace probability.\n")

# 2. Backtest harness end-to-end on synthetic graded rows, both arms.
rows = []
for g in range(10):
    for i in range(12):
        r = dict(row)
        r["game_id"] = f"g{g}"
        r["ts"] = f"2026-02-{g+1:02d}T20:{i:02d}:00+00:00"
        r["player"] = f"P{i}"
        r["p_model_raw"] = 0.55 + 0.02 * (i % 5)
        r["price"] = 0.45
        r["result"] = (i % 3 != 0)
        rows.append(r)
Path("/tmp/pacer-check.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
Path("/tmp/pacer-stats.json").write_text(json.dumps({"games": {f"g{g}": {"pace": 1.20} for g in range(10)}}))
for label, extra in (("pace ON ", []), ("pace OFF", ["--no-pace"])):
    out = subprocess.run(
        ["/tmp/pacer-venv/bin/python", "-m", "pacer_v2", "backtest",
         "--in", "/tmp/pacer-check.jsonl", "--team-stats", "/tmp/pacer-stats.json"] + extra,
        cwd="/tmp/pacer-v2", capture_output=True, text=True)
    print(f"--- backtest {label} ---")
    print(out.stdout.strip())
    assert out.returncode == 0, out.stderr
print("\nBACKTEST HARNESS: PASS — both arms run end-to-end on synthetic rows.")
