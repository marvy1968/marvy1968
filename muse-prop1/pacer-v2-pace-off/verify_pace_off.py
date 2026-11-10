"""Verify the --no-pace change: unit tests, flag effect, backtest harness end-to-end.

Synthetic rows only — this proves the machinery, NOT profitability.
The real verdict needs the VM's graded file
(/opt/marv-feed/reports/pacer_v2/graded_nfl.jsonl for NFL rows).

Portable: resolves the pacer_v2 package from this file's own directory and
runs the backtest with the current interpreter (sys.executable). No hardcoded
machine paths — the earlier version pointed at /tmp/pacer-v2 and
/tmp/pacer-venv, which only exist on the author's machine (Grok flaw 6).
Synthetic check files go in a TemporaryDirectory, not a fixed /tmp path.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

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
with tempfile.TemporaryDirectory() as td:
    check_path = Path(td) / "pacer-check.jsonl"
    stats_path = Path(td) / "pacer-stats.json"
    check_path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    stats_path.write_text(json.dumps({"games": {f"g{g}": {"pace": 1.20} for g in range(10)}}))
    for label, extra in (("pace ON ", []), ("pace OFF", ["--no-pace"])):
        out = subprocess.run(
            [sys.executable, "-m", "pacer_v2", "backtest",
             "--in", str(check_path), "--team-stats", str(stats_path)] + extra,
            cwd=str(HERE), capture_output=True, text=True)
        print(f"--- backtest {label} ---")
        print(out.stdout.strip())
        assert out.returncode == 0, out.stderr
print("\nBACKTEST HARNESS: PASS — both arms run end-to-end on synthetic rows.")
