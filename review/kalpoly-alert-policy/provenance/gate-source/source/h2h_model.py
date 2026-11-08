"""CFB H2H matchup model line for NCAAF alerts and /game cards + 'h2h_cfb' paper tracking (Marvin approved 2026-10-20 2:42 PM CT).

Data: ~/aj-bet-bot/data/h2h_model_cfb.json, written daily by ~/cfb-h2h/src/daily.sh (cron). The line is display-only:
it never creates, sends or vetoes an alert. H2H_MODEL=off disables the line; H2H_MODEL_FILE overrides the path.
Headline % = 'adj' (H2H categories + home + weather + SRS opponent strength, logistic, walk-forward validated).
80%+ adj = high confidence (backtest 2025-26 hold-out: ~91% straight-up on ~22% of games).
CLI:  python -m ajbet.h2h_model paper   -> records h2h_cfb paper picks (SU at ESPN moneyline, ATS at ESPN spread -110)
                                          for games kicking off in the next 24 h; dedupe one per game per market."""
from __future__ import annotations
import json, os, re, sys, time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

HC = 0.80
_CACHE: Dict[str, Any] = {"t": 0.0, "d": None, "m": 0.0}


def _path() -> str:
    return os.environ.get("H2H_MODEL_FILE", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                         "data", "h2h_model_cfb.json"))


def _on() -> bool:
    return os.environ.get("H2H_MODEL", "on").lower() not in ("0", "off", "false", "no")


def load() -> Dict[str, Any]:
    p = _path()
    try:
        m = os.path.getmtime(p)
        if _CACHE["d"] is None or m != _CACHE["m"] or time.time() - _CACHE["t"] > 300:
            with open(p) as fh:
                _CACHE.update(d=json.load(fh), m=m, t=time.time())
    except Exception:  # noqa: BLE001
        _CACHE["d"] = _CACHE["d"] or {}
    return _CACHE["d"] or {}


def _n(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def find(event_id: Any = None, home: str = "", away: str = "", data: Optional[Dict[str, Any]] = None):
    d = data if data is not None else load()
    gs = d.get("games") or []
    if event_id is not None:
        for r in gs:
            if str(r.get("game_id")) == str(event_id):
                return r
    if home and away:   # exact full-name match only (avoids Miami vs Miami (OH))
        for r in gs:
            if _n(r.get("home_full")) == _n(home) and _n(r.get("away_full")) == _n(away):
                return r
    return None


def pick(rec: Dict[str, Any]):
    """(team, side, p) by adj (fallback model)."""
    p = rec.get("p_adj_home")
    if p is None:
        p = rec.get("p_model_home", 0.5)
    return (rec["home"], "home", p) if p >= 0.5 else (rec["away"], "away", 1 - p)


def h2h_line(rec: Dict[str, Any], marv_pick: str = "") -> str:
    if not rec:
        return ""
    h, a, ph, pa = rec["home"], rec["away"], float(rec["pts_home"]), float(rec["pts_away"])
    lead, lp, tp = (h, ph, pa) if ph >= pa else (a, pa, ph)
    share = lp / (lp + tp) if lp + tp else 0.5
    team, side, p = pick(rec)
    pm = rec.get("p_model_home")
    s = f"📐 H2H: {lead} {lp:g}-{tp:g} ({share * 100:.0f}%) · {team} {p * 100:.0f}% adj"
    if pm is not None:
        pmt = pm if side == "home" else 1 - pm
        s += f" (model {pmt * 100:.0f}%)"
    if p >= HC:
        s += " · 🔥 high confidence"
    if rec.get("low_sample"):
        s += " · ⚠️ low sample"
    mp = (marv_pick or "").strip()
    if mp:
        agree = _n(team) and (_n(team) in _n(mp) or _n(mp) in _n(team))
        s += f" · Marv: {mp} " + ("✅" if agree else "⚠️ disagrees")
    return s


def line(sport: str, event_id: Any = None, home: str = "", away: str = "", marv_pick: str = "",
         data: Optional[Dict[str, Any]] = None) -> str:
    """'' unless NCAAF, switched on, and the game is in today's H2H file. Never raises."""
    try:
        if (sport or "").lower() != "ncaaf" or not _on():
            return ""
        r = find(event_id, home, away, data)
        return h2h_line(r, marv_pick) if r else ""
    except Exception:  # noqa: BLE001
        return ""


def record_paper(db, data: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None,
                 horizon_h: float = 24.0, stake: float = 100.0) -> int:
    """Insert h2h_cfb paper bets (SU + ATS) for games starting within horizon. Returns rows inserted."""
    from .oddsmath import american_to_decimal
    d = data if data is not None else load()
    now = now or datetime.now(timezone.utc)
    n = 0
    for r in d.get("games") or []:
        try:
            ko = datetime.fromisoformat(str(r["date"]))
        except Exception:  # noqa: BLE001
            continue
        if not (now < ko <= now + timedelta(hours=horizon_h)):
            continue
        team, side, p = pick(r)
        full = r.get("home_full" if side == "home" else "away_full") or team
        det = {"matchup": f"{r.get('away_full') or r['away']} @ {r.get('home_full') or r['home']}", "p_adj": p,
               "p_model": r.get("p_model_home") if side == "home" else 1 - r.get("p_model_home", 0.5),
               "h2h_pts": [r["pts_home"], r["pts_away"]], "high_conf": p >= HC, "low_sample": r.get("low_sample"),
               "tiers": [r.get("tier_home"), r.get("tier_away")], "line_src": r.get("odds_provider")}
        bets = []
        ml = r.get("ml_home") if side == "home" else r.get("ml_away")
        bets.append(("h2h", None, float(ml) if ml not in (None, "") else -110.0))
        sh = r.get("spread_home")
        if sh is not None:
            bets.append(("spreads", float(sh) if side == "home" else -float(sh), -110.0))
        for mk, ln, px in bets:
            key = f"{r['game_id']}:h2h_cfb:{mk}"
            if db.has_alert(key):
                continue
            aid = db.insert_alert(dict(dedupe_key=key, event_id=str(r["game_id"]), league="ncaaf", strategy="h2h_cfb",
                                       market=mk, selection=full, line=ln, price=px, book=r.get("odds_provider") or "espn",
                                       fair_p=p if mk == "h2h" else None, market_p=None, edge=None, value=0,
                                       trigger="paper", message="H2H model pick (paper)",
                                       details_json=json.dumps({**det, "ml_missing": mk == "h2h" and ml in (None, "")})))
            if aid is None:
                continue
            db.insert_paper_bet(dict(alert_id=aid, event_id=str(r["game_id"]), league="ncaaf", strategy="h2h_cfb",
                                     market=mk, selection=full, side=side, line=ln, price=px,
                                     book=r.get("odds_provider") or "espn", stake=stake,
                                     to_win=round(stake * (american_to_decimal(px) - 1), 2), tag="paper:h2h_cfb",
                                     sport="ncaaf", commence=r["date"], fair_p=p if mk == "h2h" else None,
                                     model_p=p if mk == "h2h" else None))
            n += 1
    return n


def alert_append(db, alert_id: Any, text: str) -> str:
    """Append the H2H line to an existing NCAAF alert card (once). Never raises; never blocks an alert."""
    try:
        if db is None or alert_id is None or not _on() or "📐 H2H:" in (text or ""):
            return text
        aid = alert_id[0] if isinstance(alert_id, (list, tuple)) else alert_id
        row = db.conn.execute("SELECT league, event_id FROM alerts WHERE id=?", (aid,)).fetchone()
        if not row or str(row[0] or "").lower() != "ncaaf":
            return text
        hl = line("ncaaf", event_id=row[1])
        if not hl:
            return text
        if "<b>" in text or "</" in text:
            hl = hl.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"{text}\n{hl}"
    except Exception:  # noqa: BLE001
        return text


def _main(argv) -> int:
    if len(argv) > 1 and argv[1] in ("paper", "dry"):
        from .config import load_config, resolve_path   # same DB the service uses
        from .db import DB
        cfg = load_config()
        dbp = str(resolve_path(cfg, cfg.get("db_path", "ajbet.db")))
        if argv[1] == "dry":   # read-only: no rows, no Telegram
            import sqlite3
            db = DB.__new__(DB); db.conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
            print(dry_report(db, cfg, horizon_h=float(argv[2]) if len(argv) > 2 else 36.0))
            return 0
        db = DB(dbp)
        if v2_on():
            out = record_paper_v2(db, cfg)
            print(f"{datetime.now().isoformat(timespec='seconds')} h2h_cfb v2: {out}")
            return 0
        n = record_paper(db)
        print(f"{datetime.now().isoformat(timespec='seconds')} h2h_cfb paper rows inserted (v1): {n}")
        return 0
    print(__doc__)
    return 0



# ====================================================================== v2 (Marvin approved 2026-10-24)
# 1) ONE bet per game: ML or spread, whichever has the bigger edge (model p minus the price's break-even p,
#    i.e. vig included).  Spread cover p is DERIVED: implied margin m = sigma * Phi^-1(p_win) (model win prob),
#    p_cover = Phi((m + side_spread) / sigma), sigma = ncaaf sigma from config (16.0).  ESPN gives no spread
#    price, so the spread is priced at -110 (break-even 52.4%).  No ML price -> spread only.
# 2) Edge gate: bet + alert only when edge >= min_edge (default 0.03 = 3 pts; config h2h_model.min_edge or env
#    H2H_MIN_EDGE).  No edge -> track-only row (tag watchlist:h2h_no_edge; graded, outside the main ledger).
# 3) Telegram: h2h alerts go to the PRIMARY chat only (TELEGRAM_CHAT_ID = Marvin), never extra recipients (AJ).
#    One alert per game (dedupe key <game>:h2h_cfb:pick), sent once.  Other alert types are untouched.
# Games that already have an h2h_cfb bet (e.g. the 24 v1 rows) are skipped so nothing is double-counted.
# OFF SWITCHES: H2H_V2=off env or data/H2H_V2_OFF file -> v1 behaviour (ML + spread, no gate, no alerts).
#               H2H_ALERTS=off env or data/H2H_ALERTS_OFF file -> v2 bets still recorded, no Telegram.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _flag(env, fname):
    return os.environ.get(env, "on").lower() not in ("0", "off", "false", "no") and \
        not os.path.exists(os.path.join(_ROOT, "data", fname))


def v2_on() -> bool:
    return _flag("H2H_V2", "H2H_V2_OFF")


def alerts_on() -> bool:
    return _flag("H2H_ALERTS", "H2H_ALERTS_OFF")


def _settings(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    c = ((cfg or {}).get("h2h_model") or {})
    sig = c.get("sigma") or (((cfg or {}).get("sports") or {}).get("ncaaf") or {}).get("sigma") or 16.0
    me = os.environ.get("H2H_MIN_EDGE") or c.get("min_edge", 0.03)
    return {"min_edge": float(me), "sigma": float(sig), "spread_price": float(c.get("spread_price", -110.0))}


def evaluate(r: Dict[str, Any], st: Dict[str, float]) -> Dict[str, Any]:
    """Both candidate bets for one game + the chosen one.  Pure (no DB)."""
    from statistics import NormalDist
    from .oddsmath import american_to_prob
    N = NormalDist()
    team, side, p = pick(r)
    full = r.get("home_full" if side == "home" else "away_full") or team
    cands = []
    ml = r.get("ml_home") if side == "home" else r.get("ml_away")
    if ml not in (None, ""):
        try:
            be = american_to_prob(float(ml))
            cands.append({"market": "h2h", "line": None, "price": float(ml), "p": p, "implied": be, "edge": p - be})
        except ValueError:
            pass
    sh = r.get("spread_home")
    if sh is not None:
        ln = float(sh) if side == "home" else -float(sh)
        pw = min(max(p, 1e-4), 1 - 1e-4)
        m = st["sigma"] * N.inv_cdf(pw)
        pc = N.cdf((m + ln) / st["sigma"])
        be = american_to_prob(st["spread_price"])
        cands.append({"market": "spreads", "line": ln, "price": st["spread_price"], "p": pc, "implied": be,
                      "edge": pc - be, "margin": round(m, 1)})
    best = max(cands, key=lambda c: c["edge"]) if cands else None
    return {"game_id": str(r["game_id"]), "team": team, "full": full, "side": side, "p_win": p, "cands": cands,
            "best": best, "bet": bool(best and best["edge"] >= st["min_edge"]),
            "matchup": f"{r.get('away_full') or r['away']} @ {r.get('home_full') or r['home']}", "ko": r.get("date")}


def _ct(iso):
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(str(iso)).astimezone(ZoneInfo("America/Chicago")).strftime("%a %-I:%M %p CT")
    except Exception:  # noqa: BLE001
        return str(iso)


def alert_text(e: Dict[str, Any]) -> str:
    b = e["best"]
    what = f"{e['full']} ML {b['price']:+.0f}" if b["market"] == "h2h" else f"{e['full']} {b['line']:+g} ({b['price']:+.0f})"
    return (f"🧪 H2H model (test) · NCAAF · {what}\n"
            f"{e['matchup']} · {_ct(e['ko'])}\n"
            f"Model {b['p'] * 100:.0f}% · implied {b['implied'] * 100:.0f}% · edge {b['edge'] * 100:+.1f} pts · paper $100")


def _send_marvin_only(cfg, text: str) -> bool:
    """Primary chat (TELEGRAM_CHAT_ID = Marvin) only; never extra recipients. Token/chat never printed."""
    from .alerts import Notifier
    n = Notifier(cfg, dry_run=False)
    if not (n._token and n._chat):
        return False
    return n._send_one(str(n._chat), text, False)


def record_paper_v2(db, cfg=None, data=None, now=None, horizon_h: float = 24.0, stake: float = 100.0,
                    dry: bool = False, send: bool = True, printer=print) -> Dict[str, int]:
    from .oddsmath import american_to_decimal
    d = data if data is not None else load()
    now = now or datetime.now(timezone.utc)
    st = _settings(cfg)
    out = {"bet": 0, "track": 0, "sent": 0, "skipped_existing": 0}
    rows, pend = [], []
    for r in d.get("games") or []:
        try:
            ko = datetime.fromisoformat(str(r["date"]))
        except Exception:  # noqa: BLE001
            continue
        if not (now < ko <= now + timedelta(hours=horizon_h)):
            continue
        e = evaluate(r, st)
        if not e["best"]:
            continue
        had = db.conn.execute("SELECT COUNT(*) FROM paper_bets WHERE strategy='h2h_cfb' AND event_id=?",
                              (e["game_id"],)).fetchone()[0]
        e["existing"] = had
        rows.append((r, e))
    if dry:
        return {"rows": rows, "settings": st}
    for r, e in rows:
        if e["existing"]:
            out["skipped_existing"] += 1
            continue
        b = e["best"]
        key = f"{e['game_id']}:h2h_cfb:pick"
        if db.has_alert(key):
            continue
        det = {"matchup": e["matchup"], "p_win": e["p_win"], "p_bet": b["p"], "implied": b["implied"],
               "edge": b["edge"], "min_edge": st["min_edge"], "cands": e["cands"], "v": 2,
               "p_model": r.get("p_model_home") if e["side"] == "home" else 1 - r.get("p_model_home", 0.5),
               "h2h_pts": [r["pts_home"], r["pts_away"]], "low_sample": r.get("low_sample"),
               "line_src": r.get("odds_provider"), "track_only": not e["bet"], "telegram": "marvin_only"}
        aid = db.insert_alert(dict(dedupe_key=key, event_id=e["game_id"], league="ncaaf", strategy="h2h_cfb",
                                   market=b["market"], selection=e["full"], line=b["line"], price=b["price"],
                                   book=r.get("odds_provider") or "espn", fair_p=b["p"], market_p=b["implied"],
                                   edge=b["edge"], value=1 if e["bet"] else 0,
                                   trigger="h2h_v2" if e["bet"] else "h2h_v2_no_edge",
                                   message=alert_text(e), details_json=json.dumps(det)))
        if aid is None:
            continue
        db.insert_paper_bet(dict(alert_id=aid, event_id=e["game_id"], league="ncaaf", strategy="h2h_cfb",
                                 market=b["market"], selection=e["full"], side=e["side"], line=b["line"], price=b["price"],
                                 book=r.get("odds_provider") or "espn", stake=stake,
                                 to_win=round(stake * (american_to_decimal(b["price"]) - 1), 2),
                                 tag="paper:h2h_cfb" if e["bet"] else "watchlist:h2h_no_edge",
                                 sport="ncaaf", commence=r["date"], fair_p=b["p"], model_p=e["p_win"],
                                 ev=round(b["p"] * (american_to_decimal(b["price"]) - 1) - (1 - b["p"]), 4)))
        out["bet" if e["bet"] else "track"] += 1
        if e["bet"]:
            pend.append((aid, alert_text(e)))
    # one Telegram message per run (no spam): every new edge pick as its own 3-line block, Marvin only
    if pend and send and alerts_on():
        try:
            if _send_marvin_only(cfg or {}, "\n\n".join(t for _, t in pend)):
                for aid, _ in pend:
                    db.mark_sent(aid)
                out["sent"] = len(pend)
        except Exception as ex:  # noqa: BLE001
            printer(f"h2h alert send failed: {type(ex).__name__}")
    return out


def dry_report(db, cfg=None, now=None, horizon_h: float = 36.0) -> str:
    res = record_paper_v2(db, cfg, now=now, horizon_h=horizon_h, dry=True)
    st = res["settings"]
    L = [f"H2H v2 dry run · min edge {st['min_edge'] * 100:.0f} pts · sigma {st['sigma']:g} · window {horizon_h:g} h"]
    for r, e in sorted(res["rows"], key=lambda t: t[1]["ko"] or ""):
        b = e["best"]
        what = f"ML {b['price']:+.0f}" if b["market"] == "h2h" else f"{b['line']:+g} ({b['price']:+.0f})"
        other = [c for c in e["cands"] if c is not b]
        oth = "; other " + ", ".join(f"{'ML' if c['market'] == 'h2h' else 'ATS'} {c['p'] * 100:.0f}/{c['implied'] * 100:.0f} {c['edge'] * 100:+.1f}" for c in other) if other else ""
        verdict = "ALREADY BET (v1 row, skipped)" if e["existing"] else ("ALERT+BET" if e["bet"] else "track-only (no edge)")
        L.append(f"{_ct(e['ko'])} | {e['matchup']} | {e['full']} {what} | model {b['p'] * 100:.0f}% implied {b['implied'] * 100:.0f}% "
                 f"edge {b['edge'] * 100:+.1f} | {verdict}{oth}")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
