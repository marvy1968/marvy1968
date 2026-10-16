"""On-demand daily card for March_edge /betcard (2026-10-16). Zero Odds API credits: today's stored daily card
(state/marv_predict/daily_card.json) re-rendered as the Daily Cross-Sport Ranked Action Plan (Master extremes and
Value Plays recomputed now from the current Bovada snapshot) + today's logged MARV PREDICT MAX bet card.
Window: games starting today (CT) or still live, through today+N days (`python -m marv.cardnow N`, N=0..2);
parlays are rebuilt from the in-window legs only. NBA excluded; unproven % stripped. Prints JSON {"text": ...}."""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import betcard, dailypicks as D

STATE = Path("/opt/marv-bot/state")
ET, CT = ZoneInfo("America/New_York"), ZoneInfo("America/Chicago")
LIVE_H = 4.5  # a game that started within this many hours counts as still live


def _strip_unproven(t: str) -> str:
    t = re.sub(r" · EV [+-]?\d+(\.\d+)?%", "", t)
    t = re.sub(r"Marv \d+% vs price \d+%:? ?\(?edge [+-]?\d+(\.\d+)?%\)?,? ?", "Marv model edge; ", t)
    return t


def _in_window(start, now: datetime, days: int) -> bool:
    try:
        st = start if isinstance(start, datetime) else datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return False
    if st.tzinfo is None:
        st = st.replace(tzinfo=timezone.utc)
    last = now.astimezone(CT).date() + timedelta(days=days)
    return st > now - timedelta(hours=LIVE_H) and st.astimezone(CT).date() <= last


def build(days: int = 0) -> str:
    from . import value_play as VP
    now = datetime.now(timezone.utc)
    inw = lambda x: _in_window(x.get("start") if isinstance(x, dict) else x, now, days)
    # window filters: Master extremes, Value Plays (no ledger/card.json side effects from this on-demand view)
    me, vp = D.master_extremes, VP.plays
    D.master_extremes = lambda now_=None: [x for x in me(now_) if inw(x)]
    VP.plays = lambda now_=None: [p for p in vp(now_) if inw(p)]
    VP.OUT = Path("/tmp/marv_cardnow_vp")
    label = "today" if days == 0 else f"today through +{days} day{'s' if days > 1 else ''}"
    card = D._jload(D._dir(STATE) / D.CARD, None)
    out, n = [], 0
    if card:
        card = dict(card, ml=[x for x in card["ml"] if inw(x)], ou=[x for x in card["ou"] if inw(x)],
                    upset_tags=[t for t in card.get("upset_tags", []) if inw(t) or "start" not in t])
        n = len(card["ml"]) + len(card["ou"]) + len(D.master_extremes()) + len(VP.plays())
        try:
            rec = D.record(STATE)
        except Exception:  # noqa: BLE001
            rec = ""
        out.append(D.telegram_text(card, rec))
        if card.get("date") != now.astimezone(CT).date().isoformat():
            out.append(f"⚠️ stored card is from {card.get('date')} (today's build not run yet)")
    out.append(f"🗓 Window: {label} (CT) + live games · parlays rebuilt from in-window legs only")
    out.append("ℹ️ stored morning build + Value Plays/Master re-priced now from the latest Bovada snapshot (0 odds credits)")
    try:
        book = json.loads((STATE / "betcard_log.json").read_text())
    except Exception:  # noqa: BLE001
        book = []
    fields = set(betcard.Bet.__dataclass_fields__)
    recent = {(now - timedelta(days=i)).astimezone(ET).date().isoformat() for i in range(0, 4)}
    bets = [betcard.Bet(**{k: v for k, v in b.items() if k in fields}) for b in book
            if b.get("card") in recent and b.get("sport") != "nba" and b.get("result") is None and inw(b)]
    bets = betcard.one_side(bets)
    if n == 0 and not bets:
        return (f"👽 MARV PREDICT · no qualifying games {label} (CT) on the stored card, Value Plays or MAX bet card."
                + ("\nTry /card_1day for tomorrow's games too." if days < 1 else
                   "\nTry /card_2day for a wider window." if days < 2 else ""))
    out.append("")
    out.append(betcard.text(bets, now.astimezone(ET)) if bets else
               "👽 <b>MARV PREDICT MAX — BET CARD</b>\nNo bet card picks in this window.")
    return _strip_unproven("\n".join(out))


if __name__ == "__main__":
    try:
        d = int(sys.argv[1]) if len(sys.argv) > 1 else 0
        print(json.dumps({"text": build(max(0, min(d, 2)))}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"text": "", "error": str(e)[:200]}))
        sys.exit(1)
