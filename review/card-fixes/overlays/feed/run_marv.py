#!/usr/bin/env python3
"""Wrapper for marv-bot / marv-bot-late: apply /opt/marv-feed bug-fix overlay, then `python -m marv`."""
import sys
sys.path.insert(0, "/opt/marv-bot")
sys.path.insert(0, "/opt/marv-feed")
import marv_fixes
marv_fixes.apply()
import cfbd_budget  # 2026-10-24 CFBD call budget/429 fallback (off: CFBD_BUDGET=off | CFBD_BUDGET_OFF)
cfbd_budget.apply()
try:
    import marv_betcard_fix  # 2026-10-26 gap lines show side + "Bovada" (off: BETCARD_GAP_FIX=off | state/BETCARD_GAP_FIX_OFF)
    marv_betcard_fix.apply()
except Exception:  # noqa: BLE001 - cosmetic, never block the bot
    pass
try:
    import marv_edge_labels  # 2026-11-06 HIGH CONFIDENCE -> estimated-edge size labels (off: EDGE_LABELS=off | EDGE_LABELS_OFF)
    marv_edge_labels.apply()
except Exception:  # noqa: BLE001 - presentation only, never block the bot
    import logging; logging.getLogger("marv_edge_labels").exception("not applied")
try:
    import marv_wincap  # 2026-10-26 win chance cap 60% before edge check (off: MARV_WINCAP=off | /opt/marv-feed/MARV_WINCAP_OFF)
    marv_wincap.apply()
except Exception:  # noqa: BLE001
    import logging; logging.getLogger("marv_wincap").exception("not applied")
try:
    import marv_card_fixes  # 2026-10-26 audit: recap once/day, Bovada spelling, bet card gates (off: RECAP_ONCE / BOVADA_SPELL / BETCARD_GATE)
    marv_card_fixes.apply()
except Exception:  # noqa: BLE001
    import logging; logging.getLogger("marv_card_fixes").exception("not applied")
try:
    import marv_nba_injury_timeout  # 2026-11-02 bound NBA injury PDF fetch (off: MARV_NBA_INJ_TIMEOUT=off | /opt/marv-feed/MARV_NBA_INJ_TIMEOUT_OFF)
    marv_nba_injury_timeout.apply()
except Exception:  # noqa: BLE001
    import logging; logging.getLogger("marv_nba_injury_timeout").exception("not applied")
try:
    import marv_stats_guard  # 2026-11-03 CFB dup team-game crash + per-sport stats time budget (off: MARV_STATS_GUARD=off | /opt/marv-feed/MARV_STATS_GUARD_OFF; budget only: MARV_STATS_BUDGET=off | /opt/marv-feed/MARV_STATS_BUDGET_OFF)
    marv_stats_guard.apply()
except Exception:  # noqa: BLE001
    import logging; logging.getLogger("marv_stats_guard").exception("not applied")
try:
    import marv_ncaab_card  # 2026-11-05 NCAAB card: model gap / one per game / 15% night cap track-only + paper-only to Nov 17 (off: MARV_NCAAB_CARD_FIX=off | /opt/marv-feed/MARV_NCAAB_CARD_FIX_OFF)
    marv_ncaab_card.apply()
except Exception:  # noqa: BLE001
    import logging; logging.getLogger("marv_ncaab_card").exception("not applied")
try:
    import marv_lane_pause
    marv_lane_pause.apply_betcard()
except Exception:  # noqa: BLE001
    import logging
    logging.getLogger("marv_lane_pause").exception("betcard pause not applied")
from marv.main import main
raise SystemExit(main())
