"""Read-only dry run of the hybrid engine wiring: one game per sport through /game (H2H overlay + hybrid line),
plus /upset and /parlay (no Odds API refetch: odds key blanked). Sports with no upcoming game in the slate get a
direct hybrid read on a recent matchup from cached box scores (as of the end of last season). Sends nothing.
Usage: .venv/bin/python tools/hybrid_dryrun.py"""
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marv import hybrid as H  # noqa: E402
from marv import parlay, querybot, upset  # noqa: E402
from marv.config import Settings  # noqa: E402

s = Settings.from_env()
s = dataclasses.replace(s, odds_api_key=None) if dataclasses.is_dataclass(s) else s
state = Path(s.state_dir)
now = datetime.now(timezone.utc)
preds = json.loads((state / "predictions.json").read_text())
OFF = {  # sport -> (home, away, as_of) for sports without an upcoming slate game
    "nba": ("Oklahoma City Thunder", "Boston Celtics", datetime(2026, 4, 13, tzinfo=timezone.utc)),
    "ncaab": ("Duke Blue Devils", "Houston Cougars", datetime(2026, 3, 15, tzinfo=timezone.utc)),
    "ncaaw": ("South Carolina Gamecocks", "UConn Huskies", datetime(2026, 3, 15, tzinfo=timezone.utc)),
    "euroleague": ("REAL MADRID", "PANATHINAIKOS AKTOR ATHENS", now),
}
print(f"PAPER_MODE={getattr(s, 'paper_mode', None)}  sports={s.sports}\n")
for sp in H.SPORTS:
    up = sorted((r for r in preds.values() if r["sport"] == sp and datetime.fromisoformat(r["start"]) > now),
                key=lambda r: r["start"])
    print(f"===== {sp.upper()}")
    if up:
        r = up[0]
        print(querybot.answer(s, f"/game {r['home']}"))
        hr = H.game(state, sp, r["home"], r["away"])
        print("hybrid:", H.text(hr, r["home"], r["away"], sp) if hr else "no hybrid read (fewer than 3 games / no box)")
    elif sp in OFF:
        h, a, asof = OFF[sp]
        hr = H.game(state, sp, h, a, as_of=asof)
        print(f"(no {sp} game in the slate) {a} @ {h} as of {asof:%Y-%m-%d}:")
        print("hybrid:", H.text(hr, h, a, sp) if hr else "no hybrid read (fewer than 3 games / no box cached)")
        if hr:
            print("  mods", hr["mod_home"], hr["mod_away"], hr["why_home"], hr["why_away"], "won", hr["won"])
    print()
print("===== /upset")
print(upset.best_text(state, [k for k in s.sports if k in upset.SPORTS], getattr(s, "paper_mode", True)))
print("\n===== /parlay (no refetch)")
print(parlay.answer(s))
