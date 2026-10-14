UFP/UVF research 2026-10-13. bt_upset.py (NFL ML 2020-26), bt_cfb.py (CFB ATS 2018-26; no cached CFB ML), bt_wnba.py (WNBA ML 2018-26). summary_*.json = tables.
Result: no signal held in Aug1-Oct12 + Sep21-Oct12 2026 + prior seasons -> nothing adopted. Tags tracking-only (marv/ufp_uvf.py, MARV_UPSET=1).
Ledger: state/upset_tags/tags.json (on_card = A/B flag). MLB: no cached odds.
Rollback: MARV_UPSET=0 in .env (or restore marv/dailypicks.py.bak-20261013-upset + .env.bak-20261013-upset, rm marv/ufp_uvf.py); restart marv-bridge then marv-edges.

2026-10-13 round 2 (bt_dog.py -> dog_probs.csv, an_dog.py -> summary_dog.json): model-edge dogs.
ADOPTED: WNBA dog >= +120 with edge [6%,10%) (MC -> K=0.10 TCI*PPE -> +TF vs vig-free) -> Master active at half stake + feed (src=master_uvf). Flag MARV_UVF_DOG=1.
Rejected: all NFL buckets, WNBA 10%+, confirmations (NFL eff, WNBA rest needs B-window sample), logistic (OOS logloss worse than market).
Rollback: MARV_UVF_DOG=0 in .env + the 4 systemd master.conf drop-ins (daemon-reload), or restore marv/tci_ppe_uf.py.bak-20261013-uvfdog,
marv/ufp_uvf.py.bak-20261013-uvfdog, /opt/marv-feed/pick_feed.py.bak-uvfdog-20261013, *.service.d/master.conf.bak-uvfdog-20261013.

2026-10-13 round 3 (bt_dcp.py -> summary_dcp.json): Dynamic Confidence Penalty (A) and UVF travel/trench gates (B), NFL 2020-26. Nothing adopted, no code changed.
