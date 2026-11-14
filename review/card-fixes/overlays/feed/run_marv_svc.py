#!/usr/bin/env python3
"""Wrapper for marv-edges / marv-bridge: apply /opt/marv-feed upset overlay (NBA quiet), then `python -m marv <args>`."""
import sys
sys.path.insert(0, "/opt/marv-bot")
sys.path.insert(0, "/opt/marv-feed")
try:
    import marv_edge_labels  # 2026-11-06 edge-size labels (off: EDGE_LABELS=off | EDGE_LABELS_OFF)
    marv_edge_labels.apply()
except Exception as e:  # noqa: BLE001
    print("edge_labels overlay failed:", e, file=__import__("sys").stderr)
try:
    import marv_upset_fix
    marv_upset_fix.apply()
    marv_upset_fix.apply_ncaab_track()  # 2026-11-04 NCAAB fav <= -1000 track-only. Off: MARV_NCAAB_UPSET_TRACK=off | /opt/marv-feed/MARV_NCAAB_UPSET_TRACK_OFF
except Exception:  # noqa: BLE001 - never block the service
    import logging; logging.getLogger("marv_upset_fix").exception("not applied")
try:
    import marv_lane_pause
    marv_lane_pause.apply_betcard()
except Exception:  # noqa: BLE001
    import logging
    logging.getLogger("marv_lane_pause").exception("betcard pause not applied")
from marv.main import main
raise SystemExit(main())
