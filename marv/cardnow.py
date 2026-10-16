"""On-demand daily card for March_edge /betcard (2026-10-16). Zero Odds API credits: today's stored daily card
(state/marv_predict/daily_card.json) re-rendered as the Daily Cross-Sport Ranked Action Plan (Master extremes and
Value Plays recomputed now from the current Bovada snapshot) + today's logged MARV PREDICT MAX bet card.
NBA excluded; unproven % (Value Play EV, model win %) stripped. Prints JSON {"text": ...} (Telegram HTML)."""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import betcard, dailypicks as D

STATE = Path("/opt/marv-bot/state")
ET, CT = ZoneInfo("America/New_York"), ZoneInfo("America/Chicago")


def _strip_unproven(t: str) -> str:
    t = re.sub(r" · EV [+-]?\d+(\.\d+)?%", "", t)
    t = re.sub(r"Marv \d+% vs price \d+%:? ?\(?edge [+-]?\d+(\.\d+)?%\)?,? ?", "Marv model edge; ", t)
    return t


def build() -> str:
    now = datetime.now(timezone.utc)
    card = D._jload(D._dir(STATE) / D.CARD, None)
    out = []
    if card:
        try:
            rec = D.record(STATE)
        except Exception:  # noqa: BLE001
            rec = ""
        out.append(D.telegram_text(card, rec))
        if card.get("date") != now.astimezone(CT).date().isoformat():
            out.append(f"⚠️ stored card is from {card.get('date')} (today's build not run yet)")
    else:
        out.append("Daily card: no stored build yet today.")
    out.append("ℹ️ stored morning build + Value Plays/Master re-priced now from the latest Bovada snapshot (0 odds credits)")
    day = now.astimezone(ET).date().isoformat()
    try:
        book = json.loads((STATE / "betcard_log.json").read_text())
    except Exception:  # noqa: BLE001
        book = []
    fields = set(betcard.Bet.__dataclass_fields__)
    bets = [betcard.Bet(**{k: v for k, v in b.items() if k in fields}) for b in book
            if b.get("card") == day and b.get("sport") != "nba"]
    bets = betcard.one_side(bets)
    out.append("")
    out.append(betcard.text(bets, now.astimezone(ET)) if bets else
               "👽 <b>MARV PREDICT MAX — BET CARD</b>\nNo bet card logged yet today (it builds at the 10:00 ET run).")
    return _strip_unproven("\n".join(out))


if __name__ == "__main__":
    try:
        print(json.dumps({"text": build()}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"text": "", "error": str(e)[:200]}))
        sys.exit(1)
