"""UPSET ALERT: a standalone Telegram alert when a heavy favourite's metrics don't support the heavy price.

Heavy favourite = ML -200 or shorter, spread >= defense.HEAVY (NFL 4.5 / CFB 6.5) or Marv margin >= HEAVY_MARGIN
(marv/insight.heavy_fav). For that favourite EVERY metric Marv has is weighed into one UPSET SCORE:
  ratings margin / Marv model (scaled by size), O+D category ratings, star H2H (QB/RB/WR[/K]), QB out / backup QB / starters out,
  defense quality + last-3 trend (both teams), hybrid category matrix + trend-catcher modifier (marv/hybrid.py:
  a heavy fav that loses the winner-take-all categories or has a last-3 turnover spike / ypp-margin drop), line move, Bovada vs Pinnacle gap, O/U trend fade (Under = fewer
  possessions = live dog, Over = favourite's side), and Marv's margin vs the spread
  support = weighted FOR minus AGAINST the favourite (market-favourite tautology excluded: it IS the price)
  need    = what the price asks for: implied-probability edge over a coin flip x10 (ML) or spread / unit, cap 4
  score   = need - support  (higher = metrics further short of the price = likelier upset)
Fires when score >= FIRE_SCORE (UPSET_SCORE_MIN, default 1.0) AND at least one non-market
metric is against the favourite (a line move / book gap alone is the market, never the trigger).
One message per game per Eastern day (state/upset_alerts.json, file-locked: the edge loop and the bridge both call
this). Every alert is logged as a paper upset alert (PAPER_MODE) with the favourite's price so it can be graded;
it is still SENT, to Marvin's chat + the mirror if configured. Descriptive only: no % (marv/proven.py gates that).
Sources: marv-edges loop scans today's NFL/CFB slate every board refresh (scan); the bridge fires it when a
March_edge alert's /overlay hits the gate (from_overlay); Telegram /upset returns the single most likely upset
on the upcoming slate (best(), read-only, no alert/log).
"""

import fcntl
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
SPORTS = ("nfl", "cfb")
LOG = "upset_alerts.json"
MARKET = ("line moved", "Bovada")  # market-only reasons: listed, never the sole trigger
MISMATCH = 0.5  # Marv margin under half the favourite's spread = "metrics don't warrant the price"
FIRE_SCORE = float(os.environ.get("UPSET_SCORE_MIN", "1.0"))  # need - support at/above this fires an alert
SPREAD_UNIT = {"nfl": 3.5, "cfb": 5.0}  # points of spread / margin per factor unit
NEED_CAP = 4.0  # a -10000 price can't ask for more than the metrics can ever give


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


def _need(sport: str, price: float | None, spread: float | None, fav: str) -> float:
    """Factor units of metric support the heavy price asks for (implied edge over 50% x 10, or spread / unit)."""
    if price is not None and price < 0:
        p = -price / (-price + 100)
        return round(min(NEED_CAP, (p - 0.5) * 10), 1)
    if spread is not None and spread < 0:
        return round(min(NEED_CAP, -spread / SPREAD_UNIT.get(sport, 4.0)), 1)
    m = re.search(r"by (\d+)", fav or "")
    return round(min(NEED_CAP, float(m.group(1)) / SPREAD_UNIT.get(sport, 4.0)), 1) if m else 1.0


def _ou_factor(state_dir: Path, sport: str, rec: dict, card: dict | None):
    """O/U trend fade as an upset factor: Under (low-scoring, fewer possessions) helps the dog; Over the favourite."""
    from . import insight as I
    tags = I.ou_tags(state_dir, sport, rec.get("game_id") or (card or {}).get("game_id"), rec.get("notes", []))
    fade = ((card or {}).get("fade") or (tags[0][1] if tags else "")).upper()
    if fade == "UNDER":
        return I.Factor("O/U trends → Under (low-scoring helps the dog)", -1)
    if fade == "OVER":
        return I.Factor("O/U trends → Over (shootout suits the fav)", 1)
    return None


def evaluate(state_dir: Path, sport: str, rec: dict, card: dict | None, t: str, price: float | None = None,
             line: float | None = None, force: bool = False) -> dict | None:
    """Upset read for team t ('home'/'away') as the heavy favourite, or None. force=True returns the scored read
    even when it would not fire (/upset ranking). Never raises."""
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
        factors = [f for f in ins.factors if not f.label.startswith("Bovada favours") and
                   not f.label.startswith("Bovada has as dog")]  # the market favourite IS the price, not a metric
        unit = SPREAD_UNIT.get(sport, 4.0)
        src = "ratings: " if card else "Marv model: "
        # the projected margin is scaled by size (by 30 backs a big price far more than by 8), capped like the need
        factors = [I.Factor(f.label, f.side, round(min(NEED_CAP, max(1.0, abs(margin) / unit)), 1), f.key)
                   if f.label.startswith(src) else f for f in factors]
        ou = _ou_factor(state_dir, sport, rec, card)
        if ou:
            factors.append(ou)
        spread = line if line is not None else (sgn * ln["spread_last"] if ln and ln.get("spread_last") is not None else None)
        m = sgn * (rec.get("model_margin") if rec.get("_engine") and rec.get("model_margin") is not None else margin)
        mism = None
        if spread is not None and spread < 0 and m < -spread * MISMATCH:
            mism = f"Marv only {_short(sport, name) if m > 0 else _short(sport, opp)} by {abs(m):.0f} vs {spread:+g}"
            factors.append(I.Factor(mism, -1, 1, key=True))
        support = round(sum(f.side * f.w for f in factors), 1)
        need = _need(sport, price, spread, fav)
        score = round(need - support, 1)
        reasons = []
        d = defense_short(DF.profile(state_dir, sport, name), _short(sport, name))
        if d:
            reasons.append(d)
        for f in sorted((f for f in factors if f.side < 0), key=lambda f: (not f.key, -f.w)):
            lbl = f.label.split(" — fade the favorite")[0]
            if " but " in lbl and lbl.startswith("heavy fav"):
                lbl = lbl.split(" but ", 1)[1]
            if " D " in lbl and d:  # defense already named in short form
                continue
            if lbl.startswith("Marv model") and mism:
                continue
            reasons.append(lbl)
        against = round(sum(f.w for f in factors if f.side < 0), 1)
        backing = round(sum(f.w for f in factors if f.side > 0), 1)
        fires = score >= FIRE_SCORE and bool([r for r in reasons if not r.startswith(MARKET)])
        if not fires and not force:
            return None  # metrics back the price, or only the market (line move / book gap) is against
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
                "price_text": px, "reasons": reasons[:4], "verdict": ins.verdict, "score": score, "need": need,
                "support": support, "for_w": backing, "against_w": against, "fires": fires,
                "for": [f.label for f in factors if f.side > 0][:4]}
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
    sc = f" · upset score {u['score']:+.1f}" if u.get("score") is not None else ""
    head = (f"🚨 UPSET ALERT — {_short(u['sport'], u['fav'])} {u['price_text']}{sc} · {' · '.join(u['reasons'])}"
            " — fade the favorite")
    tail = f"{u['away']} @ {u['home']}{when} · {u['sport'].upper()}"
    if u.get("need") is not None:
        tail += (f" · price needs {u['need']:.1f}, metrics give {u['support']:+.1f} "
                 f"({u.get('for_w', 0):g} for / {u.get('against_w', 0):g} against)")
    tail += " · 📝 paper upset alert, no proven %" if paper else " · no proven %"
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
        data[k] = {**u, "mode": "paper upset alert" if paper else "upset alert", "paper": paper, "source": source,
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
        log.info("upset alert sent: %s", _key(u))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("upset alert send failed: %s", exc)
        _mark_sent(state, u, False, str(exc))
        return False


def _ml(state_dir: Path, sport: str, gid) -> dict:
    try:
        return json.loads((Path(state_dir) / "ml_prices.json").read_text()).get(f"{sport}:{gid}", {})
    except (OSError, ValueError):
        return {}


def candidates(state_dir: Path, sports=SPORTS, now: datetime | None = None, days: int | None = None,
               force: bool = False) -> list[dict]:
    """Upset reads for not-yet-started NFL/CFB games in Marv's slate: today's (Eastern) by default, or the next
    `days` days; force=True keeps every heavy favourite's scored read (for ranking), not just the ones that fire."""
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
        if start <= now or (days is None and not alertday.is_today(start, now)) or \
                (days is not None and start > now + timedelta(days=days)):
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
        u = evaluate(state_dir, sport, rec, card, t, price, force=force)
        if u:
            out.append(u)
    return out


def best(state_dir: Path, sports=SPORTS, now: datetime | None = None, days: int = 7) -> list[dict]:
    """Upcoming heavy favourites ranked by upset score (likeliest upset first). Read-only: no alert, no log."""
    rows = candidates(Path(state_dir), sports, now, days=days, force=True)
    return sorted(rows, key=lambda u: (-u["score"], -u["against_w"], u.get("start") or ""))


def best_text(state_dir: Path, sports=SPORTS, paper: bool = True, now: datetime | None = None) -> str:
    """Telegram /upset: the single most likely upset (highest upset score) + the next two on the board."""
    rows = best(state_dir, sports, now)
    if not rows:
        return "No heavy favourites (ML -200 / big spread) on the upcoming NFL/CFB slate right now."
    top = rows[0]
    if top["fires"]:
        note = ""
    elif top["score"] >= FIRE_SCORE:
        note = " (no alert: only the market is against the favourite)"
    else:
        note = " (no alert: metrics still back every heavy price)"
    out = ["🎯 MOST LIKELY UPSET" + note,
           text(top, paper).replace("🚨 UPSET ALERT — ", f"{_short(top['sport'], top['dog'])} over ", 1)]
    if top.get("for"):
        import html
        out.append(html.escape("for the fav: " + ", ".join(top["for"])))
    nxt = [f"{_short(u['sport'], u['fav'])} {u['price_text']} vs {_short(u['sport'], u['dog'])} ({u['score']:+.1f})"
           for u in rows[1:3]]
    if nxt:
        import html
        out.append(html.escape("next: " + " · ".join(nxt)))
    return "\n".join(out)


def scan(settings, sports=SPORTS, send=None) -> list[dict]:
    """Edge-loop hook: alert every new upset alert on today's slate. Returns the ones sent."""
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
    gets its own UPSET ALERT message (once per game/day). The March_edge alert itself is untouched."""
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
    if rec is None and side and not (team and other):
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
