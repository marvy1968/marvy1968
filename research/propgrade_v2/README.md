# Prop grade v2 backtest (Marvin 2026-10-16) - research only

Goal: a true per-prop 👍 / neutral / 👎 (marv/propgrade.py `prop_grade`) and a test of whether the Kalshi demo prop path should
use it instead of Marv's game-level read. Real data only, 0 Odds API credits. Scripts write to /tmp/pg (OUT in analyze_lib.py).

Order: `build.py` (joins research/propgrade_v1 features + Marv walk-forward game projections) -> `analyze.py` / `variants.py`
(fit on 2025 wk1-6, variant chosen on two inner folds inside wk1-6 only) -> `replay.py` (March_edge's own
ajbet.valuefinder.prop_findings + live config replayed on the cached 2025 Bovada/DK/FD snapshots) -> `final_bt.py` (tables).
research/propgrade_v1/features.py built the Marv features (projection models trained on earlier seasons only).

Populations (all unseen by the fit):
- T1 every real NFL line, both sides, 2025-10-16..2025-11-10 (wk7-10), 3,216 graded
- T2 March_edge rule replayed on 2025 wk7-10 (EV >= 5%, 3 books), 24 graded
- T3 March_edge's actual graded pregame NFL prop picks 2026-10-02..10-13 (ajbet.db paper_bets, deduped), 54 graded
  (Marv game read reconstructed: pick_engine walk-forward for wk4, predictions snapshot of 2026-10-08 for wk5)

Results (ROI at the actual prices, 1 unit each): see out/final_table.csv, out/final_2star.csv. Headline:
T1 👍 142-146 -5.3% (n=288) vs every side -6.5%, always-under +1.9% -> the grade has no edge on its own.
T2+T3 March_edge picks: (a) game read 26-17 +4.8% (43) | (b) 👍 31-12 +23.8% (43) | (c) all 50-28 +11.9% (78).
T3 alone: (a) 17-11 -1.9% (28) | (b) 22-12 +4.8% (34) | (c) 35-19 +7.1% (54).
Decision: not switched (sample < 100 and no edge on the large unseen set). Shadow tracking: marv/propgrade_shadow.py.
