"""Telegram queries and edge alerts (`python -m marv edges --watch`, the marv-edges service).

Ask the Marv bot in Telegram:
  /board                 current edge board (recommended bets first, then leans and paper props)
  /game Lions            Marv's projection, fair prices and best current prices for that game
  /check nfl Lions total under 47.5 -110      fair probability for any price (e.g. a March_edge alert)
  /check nfl Lions ml Lions +150
  /props                 today's player-prop picks
  /prop Josh Allen pass  Marv's projection and over/under chance for a player's posted props
  /record                alerted bets: closing-line value (the best early sign of a real edge)
  /gaps                  college/NFL games where Bovado's spread or total is off Pinnacle's
  /help
Only the configured TELEGRAM_CHAT_ID gets answers. Every refresh, new RECOMMENDED entries are pushed
as alerts (set ALERT_LEANS=true to also alert leans with edge >= ALERT_EDGE, default 6%).
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import board, bridge
from . import edges as E
from .telegram import API, send_message

log = logging.getLogger(__name__)


def _ledger(state: Path) -> Path:
    return state / "alerted_bets.json"


def track(state: Path, entries: list[dict]) -> list[dict]:
    """Save newly alerted entries; keep updating the market price until kickoff (closing-line value)."""
    path = _ledger(state)
    book = json.loads(path.read_text()) if path.exists() else {}
    now = datetime.now(timezone.utc)
    alert_leans = os.environ.get("ALERT_LEANS", "false").lower() in ("1", "true", "yes")
    min_edge = float(os.environ.get("ALERT_EDGE", "0.06"))
    new = []
    for e in entries:
        started = e.get("start") and len(e["start"]) > 10 and datetime.fromisoformat(e["start"]) <= now
        if e["key"] in book:
            if not started and e.get("p_market") is not None:
                book[e["key"]]["close_p_market"] = e["p_market"]
                book[e["key"]]["close_price"] = e["price"]
            continue
        if e["status"] == "recommended" or (alert_leans and e["status"] == "lean" and e["edge"] >= min_edge):
            book[e["key"]] = {**e, "alerted": now.isoformat(), "close_p_market": e.get("p_market")}
            new.append(e)
    path.write_text(json.dumps(book, indent=1))
    return new


def record_text(state: Path) -> str:
    path = _ledger(state)
    book = json.loads(path.read_text()) if path.exists() else {}
    rows = [b for b in book.values() if b.get("close_p_market") is not None]
    if not rows:
        return "No alerted bets with closing prices yet."
    clv = [b["close_p_market"] * E.payout(b["price"]) - (1 - b["close_p_market"]) for b in rows]
    beat = sum(c > 0 for c in clv)
    return (f"Alerted bets: {len(book)} · closing-line value on {len(rows)}: average {sum(clv) / len(clv):+.1%}, "
            f"beat the close {beat}/{len(rows)}\n(Positive CLV over 100+ bets is the strongest early evidence of an edge.)")


def answer(settings, text: str) -> str:
    state = Path(settings.state_dir)
    parts = text.strip().split()
    cmd = parts[0].split("@")[0].lower() if parts else ""
    try:
        if cmd in ("/board", "/edges"):
            data = json.loads((state / "board.json").read_text()) if (state / "board.json").exists() else {"entries": []}
            return board.text(data["entries"]) + f"\n(built {data.get('built', 'never')[:16]} UTC)"
        if cmd == "/game" and len(parts) > 1:
            team = " ".join(parts[1:])
            preds = json.loads((state / "predictions.json").read_text()) if (state / "predictions.json").exists() else {}
            rec = max(preds.values(), key=lambda r: max(bridge.similarity(team, r["home"]), bridge.similarity(team, r["away"])),
                      default=None)
            if not rec:
                return "No projection for that team yet."
            fav = rec["home"] if rec["home_win"] >= .5 else rec["away"]
            p = max(rec["home_win"], 1 - rec["home_win"])
            lines = [f"{rec['away']} @ {rec['home']} ({rec['sport'].upper()}, {rec['start'][:16]} UTC)",
                     f"Marv: {fav} {p:.0%} (fair {E.to_american(p):+.0f}) · projected {rec['away_exp']:.1f}-{rec['home_exp']:.1f}, "
                     f"total {rec['model_total']:.1f}"]
            data = json.loads((state / "board.json").read_text()) if (state / "board.json").exists() else {"entries": []}
            for e in data["entries"]:
                if e["game"] == f"{rec['away']} @ {rec['home']}":
                    lines.append(f"{e['status']}: {e['pick']} {int(e['price']):+d} {e['book']} · edge {e['edge']:+.1%}")
            return "\n".join(lines)
        if cmd == "/check" and len(parts) >= 6:
            sport, team, market, side = parts[1].lower(), parts[2], parts[3].lower(), parts[4]
            line = float(parts[5]) if market == "total" else None
            price = float(parts[6] if market == "total" else parts[5])
            v = bridge.check(state, sport, team, market, side, price, line)
            return v.line() if v.found else v.reason
        if cmd == "/prop" and len(parts) > 1:
            from .props.run import lookup
            words = parts[1:]
            market = words[-1] if words[-1].lower() in ("pass", "rush", "rec", "reception", "receptions", "points",
                                                         "rebounds", "assists", "threes") else None
            rows = lookup(state, " ".join(words[:-1] if market else words), market)
            if not rows:
                return "No priced props for that player in the latest run."
            return "\n".join(f"{r['player']} {r.get('label', r['market'])} {r['line']:g}: proj {r['proj']:.1f} · "
                             f"over {r['p_over']:.0%} / under {1 - r['p_over']:.0%} · {r.get('book_title', '')} "
                             f"{r.get('over_price')}/{r.get('under_price')}" for r in rows[:8])
        if cmd == "/props":
            entries = board._props_entries(state)
            return board.text(entries) if entries else "No open prop picks today."
        if cmd == "/record":
            return record_text(state)
        if cmd == "/gaps":
            from . import sharpgap
            entries = sharpgap.scan(settings, [k for k in settings.sports if k in sharpgap.SPORT_KEYS])
            sharpgap.log_gaps(state, entries)
            return sharpgap.text(entries) + "\n\n" + sharpgap.report(sharpgap.record(state))
        return __doc__.split("Ask the Marv bot in Telegram:")[1].split("Only the")[0].strip()
    except Exception as exc:  # never let a bad query kill the service
        log.exception("query failed")
        return f"Couldn't answer that: {exc}"


def poll_commands(settings, offset_path: Path) -> None:
    """Answer any new Telegram commands from the owner's chat."""
    offset = int(offset_path.read_text()) if offset_path.exists() else 0
    resp = requests.get(API.format(token=settings.telegram_bot_token, method="getUpdates"),
                        params={"offset": offset, "timeout": 25}, timeout=35)
    resp.raise_for_status()
    for upd in resp.json().get("result", []):
        offset = upd["update_id"] + 1
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(settings.telegram_chat_id) or not msg.get("text", "").startswith("/"):
            continue
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, answer(settings, msg["text"]))
    offset_path.write_text(str(offset))


def refresh_props(settings, sports: list[str]) -> None:
    """Re-price player props against current lines (no Telegram card; the board and /prop read the result)."""
    import importlib
    runners = {"nfl": ("marv.props.run", {}), "cfb": ("marv.props.cfb_run", {}),
               "ncaab": ("marv.props.basketball_run", {"sport": "ncaab"}),
               "ncaaw": ("marv.props.basketball_run", {"sport": "ncaaw"}),
               "wnba": ("marv.props.basketball_run", {"sport": "wnba"})}
    for sport in sports:
        if sport in runners and settings.odds_api_key:
            mod, kw = runners[sport]
            try:
                log.info(importlib.import_module(mod).run_live(settings, 36, True, **kw)[:200])
            except Exception:
                log.exception("props refresh %s failed", sport)


def watch(settings, sports: list[str], refresh_minutes: int = 30) -> None:
    """Rebuild the board every `refresh_minutes`, alert new recommended bets, answer queries in between.
    Player props are re-priced every PROPS_REFRESH_HOURS (default 3)."""
    state = Path(settings.state_dir)
    last, last_props = 0.0, 0.0
    props_every = float(os.environ.get("PROPS_REFRESH_HOURS", "3")) * 3600
    while True:
        if time.time() - last_props >= props_every:
            refresh_props(settings, sports)
            last_props = time.time()
        if time.time() - last >= refresh_minutes * 60:
            try:
                entries = board.build(settings, sports)
                new = track(state, entries)
                if new:
                    send_message(settings.telegram_bot_token, settings.telegram_chat_id, "🚨 NEW EDGES\n" + board.text(new))
            except Exception:
                log.exception("board refresh failed")
            try:  # closing-line value: keep the latest pre-kickoff line for every open bet card pick
                from . import betcard
                betcard.update_close(state, settings)
            except Exception:
                log.exception("bet card closing lines failed")
            try:  # Bovado vs Pinnacle gaps (college/NFL): alert new ones
                from . import sharpgap
                gap_sports = [k for k in sports if k in sharpgap.SPORT_KEYS]
                if gap_sports and settings.odds_api_key:
                    fresh = sharpgap.log_gaps(state, sharpgap.scan(settings, gap_sports))
                    alert = [e for e in fresh if e["market"] == "total" or os.environ.get("GAP_ALERT_SPREADS") == "true"]
                    if alert:
                        send_message(settings.telegram_bot_token, settings.telegram_chat_id, sharpgap.text(alert))
            except Exception:
                log.exception("sharp gap refresh failed")
            last = time.time()
        try:
            poll_commands(settings, state / ".telegram_offset")
        except Exception as exc:
            log.warning("telegram poll: %s", exc)
            time.sleep(10)
