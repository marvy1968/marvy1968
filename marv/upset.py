"""UPSET WATCH: a standalone Telegram alert when a heavy favourite's metrics don't back the price.

Trigger (reuses the H2H heavy-favourite gate in marv/insight.py + marv/defense.py, nothing new is modelled):
  heavy favourite  = ML -200 or shorter, spread >= defense.HEAVY (NFL 4.5 / CFB 6.5) or Marv margin >= HEAVY_MARGIN
  AND any metric off = the gate capped the read (bad / trending-down defense, O/D 0-2, stars against, QB out,
                       Marv model disagrees, line/Pinnacle against ...) OR Marv's own margin is under half the spread
One message per game per Eastern day (state/upset_alerts.json, file-locked: the edge loop and the bridge both call
this). Every alert is logged as a paper upset watch (PAPER_MODE) with the favourite's price so it can be graded;
it is still SENT, to Marvin's chat + the mirror if configured. Descriptive only: no % (marv/proven.py gates that).
Sources: marv-edges loop scans today's NFL/CFB slate every board refresh (scan); the bridge fires it when a
March_edge alert's /overlay hits the gate (from_overlay). March_edge itself only ever gets its one H2H line.
"""

import fcntl
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
SPORTS = ("nfl", "cfb")
LOG = "upset_alerts.json"
MARKET = ("line moved", "Bovada")  # market-only reasons: listed, never the sole trigger
MISMATCH = 0.5  # Marv margin under half the favourite's spread = "metrics don't warrant the price"


def enabled() -> bool:
    return os.environ.get("UPSET_ALERTS", "on").lower() not in ("0", "false", "no", "off")


def _short(sport: str, name: str) -> str:
    return name.split()[-1] if sport == "nfl" and len(name.split()) > 1 else name


def _ord(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def defense_short(p: dict, who: str) -> str | None:
    """'Cowboys D bad (29th yds allowed)' / 'Cowboys D trending down (33/g last 3 vs 24/g)'."""
    if not p or not (p.get("bad") or p.get("fading")):
        return None
    bits = []
    if p.get("bad"):
        k = min((k for k in ("pa", "ya") if p.get(k + "_rank")), key=lambda k: p[k + "_rank"] / p[k + "_of"], default=None)
        if k:
            bits.append(f"{_ord(p[k + '_of'] - p[k + '_rank'] + 1)} {'pts' if k == 'pa' else 'yds'} allowed")
    if p.get("fading"):
        bits.append(f"{p['pa3']:.0f}/g last 3 vs {p['pa']:.0f}/g")
    tag = "bad + trending down" if p.get("bad") and p.get("fading") else "bad" if p.get("bad") else "trending down"
    return f"{who} D {tag}" + (f" ({', '.join(bits)})" if bits else "")


def evaluate(state_dir: Path, sport: str, rec: dict, card: dict | None, t: str, price: float | None = None,
             line: float | None = None) -> dict | None:
    """Upset-watch read for team t ('home'/'away') as the favourite, or None. Never raises."""
    from . import defense as DF
    from . import insight as I
    try:
        margin = card["margin"] if card and card.get("margin") is not None else rec.get("model_margin") or 0.0
        sgn = 1 if t == "home" else -1
        ln = I.lines(state_dir, sport, rec.get("game_id"), rec["home"], rec["away"])
        fav = I.heavy_fav(state_dir, sport, rec, t, line, price, line is not None, margin, ln)
        if not fav:
            return None
        ins = I.side_insight(state_dir, sport, rec, card, t, None, price, False, spread=False)
        name, opp = (rec["home"], rec["away"]) if t == "home" else (rec["away"], rec["home"])
        reasons = []
        d = defense_short(DF.profile(state_dir, sport, name), _short(sport, name))
        if d:
            reasons.append(d)
        spread = line if line is not None else (sgn * ln["spread_last"] if ln and ln.get("spread_last") is not None else None)
        m = sgn * (rec.get("model_margin") if rec.get("_engine") and rec.get("model_margin") is not None else margin)
        if spread is not None and spread < 0 and m < -spread * MISMATCH:
            reasons.append(f"Marv only {_short(sport, name) if m > 0 else _short(sport, opp)} by {abs(m):.0f} vs {spread:+g}")
        for f in sorted((f for f in ins.factors if f.side < 0), key=lambda f: (not f.key, -f.w)):
            lbl = f.label.split(" — fade the favorite")[0]
            if " but " in lbl and lbl.startswith("heavy fav"):
                lbl = lbl.split(" but ", 1)[1]
            if " D " in lbl and d:  # defense already named in short form
                continue
            if lbl.startswith("Marv model") and any(r.startswith("Marv only") for r in reasons):
                continue
            reasons.append(lbl)
        if ins.cap is None and not any(r.startswith("Marv only") for r in reasons):
            return None
        if not [r for r in reasons if not r.startswith(MARKET)]:
            return None  # a line move / book gap alone is the market, not a weak metric: no upset alert
        reasons.sort(key=lambda r: r.startswith(MARKET))
        if price is not None:
            px = f"{price:+g}"
        elif spread is not None:
            px = f"{spread:+g}"
        else:
            px = fav
        if not (fav.startswith("ML") or fav.startswith("spread")) and fav not in px:
            px += f" ({fav})"  # heavy only on Marv's margin: say so
        return {"sport": sport, "game_id": rec.get("game_id"), "start": rec.get("start"), "home": rec["home"],
                "away": rec["away"], "fav": name, "dog": opp, "price": price, "spread": spread, "heavy": fav,
                "price_text": px, "reasons": reasons[:3], "verdict": ins.verdict}
    except Exception:  # noqa: BLE001 - an alert helper must never break the caller
        log.exception("upset evaluate failed")
        return None


def text(u: dict, paper: bool = True) -> str:
    import html
    when = ""
    if u.get("start"):
        try:
            when = " · " + datetime.fromisoformat(u["start"]).astimezone(ET).strftime("%-I:%M %p ET")
        except ValueError:
            pass
    head = f"🚨 UPSET WATCH — {_short(u['sport'], u['fav'])} {u['price_text']} · {' · '.join(u['reasons'])} — fade the favorite"
    tail = f"{u['away']} @ {u['home']}{when} · {u['sport'].upper()}"
    tail += " · 📝 paper upset watch, no proven %" if paper else " · no proven %"
    return html.escape(head) + "\n" + html.escape(tail)


def _key(u: dict, now: datetime | None = None) -> str:
    day = (now or datetime.now(timezone.utc)).astimezone(ET).date().isoformat()
    gid = u.get("game_id") or f"{u['away']}@{u['home']}"
    return f"{u['sport']}:{gid}:{day}"


def claim(state_dir: Path, u: dict, paper: bool, source: str, now: datetime | None = None) -> bool:
    """Record u in state/upset_alerts.json; False if this game already alerted today (dedup, file-locked)."""
    path = Path(state_dir) / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except json.JSONDecodeError:
            data = {}
        k = _key(u, now)
        if k in data:
            return False
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=120)).isoformat()
        data = {a: b for a, b in data.items() if b.get("logged", "") >= cutoff}
        data[k] = {**u, "mode": "paper upset watch" if paper else "upset watch", "paper": paper, "source": source,
                   "logged": (now or datetime.now(timezone.utc)).isoformat(), "sent": False, "result": None}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(path)
        return True


def _mark_sent(state_dir: Path, u: dict, ok: bool, err: str = "") -> None:
    path = Path(state_dir) / LOG
    with open(path.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        data = json.loads(path.read_text())
        k = _key(u)
        if k in data:
            data[k]["sent"] = ok
            if err:
                data[k]["error"] = err[:200]
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1))
            tmp.replace(path)


def notify(settings, u: dict, source: str, send=None) -> bool:
    """Dedup + paper log + standalone Telegram (Marvin's chat, mirror if configured). True when sent."""
    from .telegram import send_message
    state = Path(settings.state_dir)
    if not claim(state, u, settings.paper_mode, source):
        return False
    try:
        (send or send_message)(settings.telegram_bot_token, settings.telegram_chat_id, text(u, settings.paper_mode))
        _mark_sent(state, u, True)
        log.info("upset watch sent: %s", _key(u))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("upset watch send failed: %s", exc)
        _mark_sent(state, u, False, str(exc))
        return False


def _ml(state_dir: Path, sport: str, gid) -> dict:
    try:
        return json.loads((Path(state_dir) / "ml_prices.json").read_text()).get(f"{sport}:{gid}", {})
    except (OSError, ValueError):
        return {}


def candidates(state_dir: Path, sports=SPORTS, now: datetime | None = None) -> list[dict]:
    """Upset-watch reads for today's (Eastern) not-yet-started NFL/CFB games in Marv's slate."""
    from . import alertday
    from . import bridge
    now = now or datetime.now(timezone.utc)
    try:
        preds = json.loads((Path(state_dir) / "predictions.json").read_text())
    except (OSError, ValueError):
        return []
    out = []
    for rec in preds.values():
        if rec.get("sport") not in sports:
            continue
        try:
            start = datetime.fromisoformat(rec["start"])
        except (KeyError, ValueError):
            continue
        if start <= now or not alertday.is_today(start, now):
            continue
        sport = rec["sport"]
        rec = bridge._engine(rec)
        card = bridge._card_for(state_dir, sport, rec)
        ml = _ml(state_dir, sport, rec.get("game_id"))
        if ml.get("home") is not None and ml.get("away") is not None:
            t = "home" if ml["home"] < ml["away"] else "away"
            price = ml[t]
        else:
            margin = card["margin"] if card and card.get("margin") is not None else rec.get("model_margin") or 0.0
            from . import insight as I
            ln = I.lines(state_dir, sport, rec.get("game_id"), rec["home"], rec["away"])
            hs = ln.get("spread_last") if ln else None
            t = ("home" if hs < 0 else "away") if hs else ("home" if margin > 0 else "away")
            price = None
        u = evaluate(state_dir, sport, rec, card, t, price)
        if u:
            out.append(u)
    return out


def scan(settings, sports=SPORTS, send=None) -> list[dict]:
    """Edge-loop hook: alert every new upset watch on today's slate. Returns the ones sent."""
    if not enabled():
        return []
    sent = []
    for u in candidates(Path(settings.state_dir), [s for s in sports if s in SPORTS]):
        if notify(settings, u, "scan", send):
            sent.append(u)
    return sent


def from_overlay(settings, sport: str, market: str, side: str, price: float | None, line: float | None, team=None,
                 other=None, text_=None, send=None) -> bool:
    """Bridge hook (background thread): a March_edge ML/spread alert on a game whose heavy favourite has off metrics
    gets its own UPSET WATCH message (once per game/day). The March_edge alert itself is untouched."""
    from . import bridge
    if not enabled():
        return False
    sport = bridge.LEAGUE_TO_SPORT.get((sport or "").lower(), (sport or "").lower())
    kind = bridge._market_kind(market or "")
    if sport not in SPORTS or kind not in ("ml", "spread"):
        return False
    state = Path(settings.state_dir)
    rec = bridge.find_game(state, sport, team, other) if team else None
    if rec is None and text_:
        rec = bridge.find_game_in_text(state, sport, text_)
    if rec is None and side:
        rec = bridge.find_game(state, sport, side)
    if rec is None:
        return False
    try:
        if datetime.fromisoformat(rec["start"]) <= datetime.now(timezone.utc):
            return False  # pregame only
    except (KeyError, ValueError):
        return False
    rec = bridge._engine(rec)
    t = bridge._team_side(side, rec)
    if t is None:
        return False
    card = bridge._card_for(state, sport, rec)
    u = evaluate(state, sport, rec, card, t, price if kind == "ml" else None, line if kind == "spread" else None)
    if u is None:  # the alert was on the underdog: check the favourite on the other side
        u = evaluate(state, sport, rec, card, "away" if t == "home" else "home")
    return bool(u) and notify(settings, u, "march_edge_overlay", send)
