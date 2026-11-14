# Provenance

Review copies of the feed overlays that this change actually edits, plus the synthetic fixture. They are not a second install path. Nothing in `marv/` imports this directory. A later copy onto the existing feed directory is described in `DEPLOY_PLAN.md` and is not performed here.

The fixture hash matches the export (`aabc131215f2ec9089c27f4011d4ab8495463bab6c33560bda6f67fd45dbdf4b`). It is labeled synthetic and is not a ledger.

Base export SHA-256 (bytes before these edits):

| File | Base SHA-256 |
|---|---|
| `marv_lane_pause.py` | `8929f00007afcf00d4d9cce32b4c8f3cff801af7d48472ece027378586774cf4` |
| `marv_ncaab_card.py` | `16d9f2f6ac7b1209d6104518997ddc03267ce6b7df392de777873a76a234472f` |
| `run_marv.py` | `13ff83e031f42cacb21abd3b54e4b8a9d232bc94cec7b4ae89b8205aac9ff9fa` |
| `run_marv_svc.py` | `e378b5cc06bbf6f364442320f19766d46964013a033b163604e3205bbfde1307` |

Shared bot files `marv/main.py`, `marv/betcard.py`, `marv/dailypicks.py`, `marv/parlay.py`, and `deploy/marv-health.sh` matched deployed `e7d1f50c` byte for byte before the edits in this branch. They are patched in place, not copied into this folder.

Not vendored, because this change does not alter them and several of their imports are absent from the export (`marv_v4`, `parlay_pause`, `v32_markets`, `cfbd_budget`, `marv_stats_guard`, and others): `marv_rules.py`, `marv_edge_labels.py`, `marv_wincap.py`, `marv_card_fixes.py`, `marv_betcard_fix.py`, `marv_type_gate.py`, `marv_total_conflict.py`, `pick_logging.py`, `marv_fixes.py`, `run_dailypicks.py`. Edge-label display and the internal HIGH flag are left as they are. No policy module was stubbed.
