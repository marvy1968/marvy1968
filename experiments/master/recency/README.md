RECENCY study (Master Formula // Dynamic Recency Update PDF, received 2026-10-15).
New idea: team ratings = 60% last 3 games + 40% macro baseline (was macro only). Applied to the Master MC power rates and TCI/PPE.
rec_bt.py <sport>: Master sides walk-forward (ML/spread, UF v2 off, TF on) for RW in {0,.15,.3,.45,.6,.75}; RW=0 == current Master.
  Common random numbers per game, so RW is the only difference. Output bets_<sport>.csv.
rec_report.py: picks RW on games before 2026-08-06 only, then checks unseen W10 (08-06..10-14) and W3 (09-24..10-14) -> report.json.
rec_ll.py: MLB/NHL have no cached historical lines, so this is a scores-only log-loss check -> ll_mlb_nhl.json.
Result: no sport beat current Master on both unseen windows -> tracking only everywhere (marv/tci_ppe_uf.py RECENCY layer,
  log state/master/recency_track.json). Promote a sport later with MARV_RECENCY_LIVE=nfl,... ; disable with MARV_RECENCY=0.
Rollback: restore marv/tci_ppe_uf.py.bak-20261015-recency and restart marv-bridge.
