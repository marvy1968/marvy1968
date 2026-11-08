"""Alert enrichment (Marvin 2026-10-17): ONE extra tag line on the same alert + stake tier on the existing stake line.
Information only: never blocks, never sends anything on its own. Every piece fails silent. MARV_ENRICH=off disables.
Tags (only when relevant): 🎯 proven sub-signal label + tracker record · ⚡ sharp move (Pinnacle moved first, from pulls
already made) · 🌬️/🌧️ kickoff weather (Open-Meteo, outdoor NFL/CFB) · 🩹 QB / star out-doubtful-questionable (ESPN).
Standing rule: a win % only when a type has 100+ graded picks (backtest+live) AND is profitable; else record only."""
import json, logging, os, re, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

log = logging.getLogger(__name__)
LEAGUES = {"nfl", "ncaaf", "wnba"}
ESPN = {"nfl": "football/nfl", "ncaaf": "football/college-football", "wnba": "basketball/wnba"}
MARV = Path("/opt/marv-bot/state")
CACHE = Path(__file__).resolve().parent.parent / "state" / "enrich_cache.json"
_mem = {}
# Backtest records (unseen/held-out periods, -110 unless noted) for each recommendation type
BACKTEST = {
    "NFL Grind/Shootout": (57, 33, 0, "NFL 2024-26 unseen, real prices"),
    "CFB Line-Level O/U": (113, 89, 1, "CFB 2023-26 unseen"),
    "CFB Top-10 Marv O/U": (99, 77, 0, "CFB 2023-26 unseen, games with an AP top-10 team"),
    "WNBA Marv Total": (101, 84, 0, "WNBA 2024-26 unseen"),
}


def _on() -> bool:
    return os.environ.get("MARV_ENRICH", "on").lower() not in ("0", "off", "false", "no")


def _get(url, params=None, ttl=600, timeout=3):
    k = url + json.dumps(params or {}, sort_keys=True)
    hit = _mem.get(k)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        r = requests.get(url, params=params, timeout=timeout)
        js = r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        js = None
    _mem[k] = (time.time(), js)
    return js


def _summary(league, event_id):
    js = _get(f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[league]}/summary", {"event": event_id}, ttl=900)
    if not js:
        return None
    try:
        comp = js["header"]["competitions"][0]
        teams = {c["homeAway"]: {"name": c["team"].get("displayName", ""), "abbr": c["team"].get("abbreviation", ""),
                                 "loc": c["team"].get("location", ""), "rank": c.get("rank") or (c.get("curatedRank") or {}).get("current", 99) or 99}
                 for c in comp["competitors"]}
        gi = js.get("gameInfo", {}).get("venue", {}) or {}
        return {"teams": teams, "date": comp.get("date"), "indoor": bool(gi.get("indoor")),
                "city": (gi.get("address") or {}).get("city"), "state": (gi.get("address") or {}).get("state"),
                "state_name": (comp.get("status") or {}).get("type", {}).get("state")}
    except Exception:  # noqa: BLE001
        return None


def _sim(a, b):
    a, b = (a or "").lower(), (b or "").lower()
    return bool(a and b and (a in b or b in a))


# ------------------------------------------------------------------ 🩹 injuries (ESPN, NFL + WNBA; no free CFB feed)
_STAR = {}


def _wnba_stars():
    """WNBA players averaging 12+ pts in 2026 (state/wnba_stars.json, built from Marv's box-score cache)."""
    if "w" not in _STAR:
        try:
            _STAR["w"] = {n.lower() for n in json.load(open(CACHE.parent / "wnba_stars.json"))}
        except Exception:  # noqa: BLE001
            _STAR["w"] = set()
    return _STAR["w"]


def injury_tag(league, sm):
    if league not in ("nfl", "wnba") or not sm:
        return ""
    from .injuries import InjuryFeed
    feed = _mem.setdefault("_feed", InjuryFeed(ttl=900))
    try:
        tbl = feed.teams(league)
    except Exception:  # noqa: BLE001
        return ""
    bits = []
    stars = _wnba_stars() if league == "wnba" else set()
    for side in ("away", "home"):
        t = sm["teams"].get(side) or {}
        recs = tbl.get(t.get("name")) or tbl.get(t.get("abbr")) or []
        for x in recs:
            st = (x.get("status") or "").upper()
            if st not in ("OUT", "DOUBTFUL", "QUESTIONABLE", "INJURED RESERVE", "SUSPENSION"):
                continue
            st = "OUT" if st in ("INJURED RESERVE", "SUSPENSION") else st
            key = (league == "nfl" and x.get("pos") == "QB") or (league == "wnba" and x.get("name", "").lower() in stars)
            if not key or (league == "nfl" and st == "OUT" and _old(x.get("date"), 21)):
                continue  # long-term IR QB is already priced; show fresh news only
            nm = x.get("name", "").split()
            short = f"{nm[0][0]}. {' '.join(nm[1:])}" if len(nm) > 1 else x.get("name", "")
            bits.append(f"{t.get('abbr', '')} {'QB ' if league == 'nfl' else ''}{short} {st}")
    return ("🩹 " + ", ".join(bits[:2])) if bits else ""


def _old(iso, days):
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(str(iso).replace("Z", "+00:00")) > timedelta(days=days)
    except Exception:  # noqa: BLE001
        return False


def news_tag(lg, row, sm):
    """Marv news-adjusted projection (marv/news_adjust.py) for this game -> '🩹 adjusted for QB X out'."""
    try:
        sp = {"ncaaf": "cfb"}.get(lg, lg)
        P = _jl(MARV / "predictions.json", {}) or {}
        rec = P.get(f"{sp}:{row['event_id']}")
        if rec is None and sm:
            hm, aw = sm["teams"].get("home", {}).get("name"), sm["teams"].get("away", {}).get("name")
            rec = next((v for k, v in P.items() if k.startswith(sp + ":") and _sim(v.get("home"), hm) and _sim(v.get("away"), aw)), None)
        na = (rec or {}).get("news_adj") or {}
        if na.get("why"):
            return "🩹 adjusted for " + ", ".join(na["why"][:2])
    except Exception:  # noqa: BLE001
        pass
    return ""


# ------------------------------------------------------------------ 🌬️ weather (Open-Meteo, free)
def weather_tag(league, sm, live):
    if league not in ("nfl", "ncaaf") or not sm or sm["indoor"] or live or not sm.get("city"):
        return ""
    try:
        ko = datetime.fromisoformat(str(sm["date"]).replace("Z", "+00:00"))
        if not (timedelta(hours=-1) < ko - datetime.now(timezone.utc) < timedelta(days=6)):
            return ""
        geo = _get("https://geocoding-api.open-meteo.com/v1/search", {"name": sm["city"], "count": 5, "country": "US"}, ttl=86400 * 30)
        res = (geo or {}).get("results") or []
        pick = next((r for r in res if sm.get("state") and sm["state"].lower() in (r.get("admin1", "").lower(), r.get("admin1_code", "").lower())), res[0] if res else None)
        if not pick:
            return ""
        fc = _get("https://api.open-meteo.com/v1/forecast", {"latitude": pick["latitude"], "longitude": pick["longitude"],
                  "hourly": "wind_speed_10m,precipitation", "wind_speed_unit": "mph", "timezone": "UTC", "forecast_days": 7}, ttl=3600)
        h = fc["hourly"]; want = ko.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")
        if want not in h["time"]:
            return ""
        i = h["time"].index(want); w = max(h["wind_speed_10m"][i:i + 3]); pr = max(h["precipitation"][i:i + 3])
        bits = []
        if w >= 15:
            bits.append(f"🌬️ {w:.0f} mph wind")
        if pr >= 2.5:
            bits.append("🌧️ heavy rain")
        return " · ".join(bits)
    except Exception:  # noqa: BLE001
        return ""


# ------------------------------------------------------------------ ⚡ sharp move (Pinnacle first; reuses recorded pulls)
def _cents(a):
    return a - 100 if a >= 100 else a + 100


def sharp_tag(db, row, sm):
    try:
        mk, sel, ev = row["market"], row["selection"] or "", str(row["event_id"])
        home = (sm or {}).get("teams", {}).get("home", {}).get("name", "")
        if mk in ("totals",):
            key, sgn = "total", (1 if sel.lower().startswith("over") else -1)
        elif mk in ("spreads",):
            key, sgn = "spread", (-1 if _sim(sel, home) else 1)   # home spread falling = home more favoured
        elif mk == "h2h":
            key, sgn = ("ml_home" if _sim(sel, home) else "ml_away"), -1  # price shortening = backed
        else:
            return ""
        cut = (datetime.now(timezone.utc) - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
        def series(feed):
            return [float(r[0]) for r in db.conn.execute("SELECT value FROM line_history WHERE event_id=? AND market=? AND feed=? "
                                                        "AND ts_utc>=? AND phase!='open' ORDER BY id", (ev, key, feed, cut))]
        p, b = series("pinnacle"), series("bovada")
        if len(p) < 2:
            return ""
        if key.startswith("ml"):
            mv = (_cents(p[-1]) - _cents(p[0])) * sgn; bm = ((_cents(b[-1]) - _cents(b[0])) * sgn) if len(b) >= 2 else 0
            ok = mv >= 15 and bm < mv / 2
        else:
            mv = (p[-1] - p[0]) * sgn; bm = ((b[-1] - b[0]) * sgn) if len(b) >= 2 else 0
            ok = mv >= 1 and bm < mv / 2
        return "⚡ sharp move" if ok else ""
    except Exception:  # noqa: BLE001
        return ""


# ------------------------------------------------------------------ 🎯 proven sub-signal labels
def _jl(p, d=None):
    try:
        return json.load(open(p))
    except Exception:  # noqa: BLE001
        return d


def signal_label(row, sm):
    lg, mk, sel = row["league"], row["market"], (row["selection"] or "").lower()
    if mk not in ("totals",) or row["line"] is None:
        return None
    side = 1 if sel.startswith("over") else -1
    line = float(row["line"])
    hm, aw = ((sm or {}).get("teams", {}).get("home", {}).get("name", ""), (sm or {}).get("teams", {}).get("away", {}).get("name", ""))
    if lg == "nfl":
        for r in _jl(MARV / "script_ou" / "ledger.json", []) or []:
            if r.get("sport") == "nfl" and r.get("side") == side and r.get("status") == "pending" and \
                    _sim(r.get("home"), hm) and _sim(r.get("away"), aw):
                return ("NFL Grind/Shootout", "Grind Under" if side < 0 else "Shootout Over")
        return None
    if lg == "ncaaf":
        rows = (_jl(MARV / "script_ou" / "linelevel_ledger.json", {}) or {}).get("rows", {})
        r = rows.get(f"cfb:{row['event_id']}")
        if r and r.get("new_side") == side and r.get("status") == "pending":
            return ("CFB Line-Level O/U", "Line-Level " + ("Over" if side > 0 else "Under"))
        if sm and min(sm["teams"].get("home", {}).get("rank", 99), sm["teams"].get("away", {}).get("rank", 99)) <= 10:
            p = (_jl(MARV / "predictions.json", {}) or {}).get(f"cfb:{row['event_id']}")
            if p and p.get("model_total") is not None and (p["model_total"] - line) * side >= 3:
                return ("CFB Top-10 Marv O/U", "Top-10 Marv " + ("Over" if side > 0 else "Under"))
        return None
    if lg == "wnba":
        p = (_jl(MARV / "predictions.json", {}) or {}).get(f"wnba:{row['event_id']}")
        if p and p.get("model_total") is not None and (p["model_total"] - line) * side >= 4:
            return ("WNBA Marv Total", "Marv Total " + ("Over" if side > 0 else "Under"))
    return None


# ------------------------------------------------------------------ 📒 tracker (backtest + live graded)
def live_record(db, typ):
    W = L = P = 0; u = 0.0
    try:
        q = db.conn.execute("SELECT a.details_json, p.status, p.pnl, p.stake FROM alerts a JOIN paper_bets p ON p.alert_id=a.id "
                            "WHERE a.details_json LIKE ?", (f'%"signal_type": "{typ}"%',))
        seen = set()
        for det, st, pnl, stake in q:
            d = json.loads(det or "{}")
            k = (d.get("signal_game"), d.get("signal_side"))
            if k in seen:
                continue
            seen.add(k)
            if st == "won": W += 1; u += (pnl or 0) / (stake or 1)
            elif st == "lost": L += 1; u -= 1
            elif st == "push": P += 1
    except Exception:  # noqa: BLE001
        pass
    return W, L, P, u


def record(db, typ):
    bw, bl, bp, note = BACKTEST.get(typ, (0, 0, 0, ""))
    lw, ll_, lp, lu = live_record(db, typ)
    W, L, P = bw + lw, bl + ll_, bp + lp
    n = W + L + P
    units = bw * (100 / 110) - bl + lu
    proven = n >= 100 and units > 0
    return dict(type=typ, bt=(bw, bl, bp), live=(lw, ll_, lp), W=W, L=L, P=P, n=n, units=units, proven=proven, note=note)


def record_text(r):
    s = f"{r['W']}-{r['L']}" + (f"-{r['P']}" if r["P"] else "")
    src = "bt+live" if sum(r["live"]) else "backtest"
    return f"{s} ({r['W'] / max(r['W'] + r['L'], 1):.0%}) {src}" if r["proven"] else f"{s} tracking ({src})"


# ------------------------------------------------------------------ entry point
def enrich(db, alert_id, text: str) -> str:
    """Return text with at most ONE extra tag line, and a stake tier appended to the existing 'Stake' line."""
    try:
        if not _on() or db is None or alert_id is None or "🎯" in text or "⚡ sharp" in text:
            return text
        ids = alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]
        cur = db.conn.execute("SELECT id, event_id, league, market, selection, line, price, details_json FROM alerts WHERE id=?", (int(ids[0]),))
        r = cur.fetchone()
        if r is None:
            return text
        row = dict(zip(("id", "event_id", "league", "market", "selection", "line", "price", "details"), r))
        lg = (row["league"] or "").lower()
        if lg not in LEAGUES:
            return text
        det = json.loads(row["details"] or "{}")
        live = bool(det.get("in_game") or det.get("score") or det.get("live_total"))
        sm = _summary(lg, row["event_id"])
        tags, stake = [], ""
        sig = None if live else signal_label(row, sm)
        if sig:
            rec = record(db, sig[0])
            tags.append(f"🎯 {sig[1]} · {record_text(rec)}")
            stake = "1u (proven)" if rec["proven"] else "0.5u (experimental)"
            for a in ids:  # tag for the tracker (graded automatically via paper_bets)
                q = db.conn.execute("SELECT details_json FROM alerts WHERE id=?", (int(a),)).fetchone()
                d0 = json.loads((q[0] if q else None) or "{}") or {}
                d0.update(signal_type=sig[0], signal_label=sig[1], signal_game=str(row["event_id"]),
                          signal_side=(row["selection"] or "").lower())
                db.conn.execute("UPDATE alerts SET details_json=? WHERE id=?", (json.dumps(d0), int(a)))
            db.conn.commit()
        for t in (sharp_tag(db, row, sm), weather_tag(lg, sm, live), news_tag(lg, row, sm) or injury_tag(lg, sm)):
            if t:
                tags.append(t)
        if stake:
            lines = text.split("\n")
            for i, ln in enumerate(lines):
                if re.sub(r"<[^>]+>", "", ln).lstrip().startswith("Stake"):
                    lines[i] = ln + f" · {stake}"
                    break
            text = "\n".join(lines)
        if tags:
            text += "\n" + " · ".join(tags)
        return text
    except Exception as e:  # noqa: BLE001
        log.debug("enrich skipped: %s", e)
        return text


# ------------------------------------------------------------------ /reportcard block
def reportcard_block(db) -> str:
    out = ["📒 Recommendation tracker (backtest + live graded; % only at 100+ graded & profitable)"]
    for typ in BACKTEST:
        r = record(db, typ)
        lw, ll_, lp = r["live"]
        roi = r["units"] / r["n"] if r["n"] else 0
        out.append(f"• {typ}: {record_text(r)}" + (f", {roi:+.1%} ROI" if r["proven"] else "") +
                   f" [bt {r['bt'][0]}-{r['bt'][1]} · live {lw}-{ll_}]")
    try:  # CLV: alert price vs close (paper ledger, Pinnacle no-vig close)
        n, b = db.conn.execute("SELECT COUNT(*), SUM(clv>0) FROM paper_bets WHERE clv IS NOT NULL").fetchone()
        if n:
            out.append(f"📈 CLV alerts: beat close {b / n:.0%} of {n}")
    except Exception:  # noqa: BLE001
        pass
    try:
        import sqlite3
        c = sqlite3.connect(f"file:{MARV / 'marv_clv.db'}?mode=ro", uri=True)
        n, b = c.execute("SELECT COUNT(*), SUM(beat) FROM clv WHERE src='marv' AND beat IS NOT NULL").fetchone()
        if n:
            out.append(f"📈 CLV Marv picks: beat close {b / n:.0%} of {n}")
    except Exception:  # noqa: BLE001
        pass
    try:
        ln = (_jl(MARV / "news_adj" / "summary.json", {}) or {}).get("line")
        if ln:
            out.append(ln)
    except Exception:  # noqa: BLE001
        pass
    try:  # 2026-10-17 (Marvin): CFB pregame props shadow (Marv); record only until 100+ graded and profitable
        cs = _jl(MARV / "props_cfb_shadow_summary.json", {}) or {}
        t = cs.get("thumbs") or {}
        if t.get("n") or cs:
            pct = f" ({t['w'] / t['n']:.0%}, {t['roi']:+.1%} ROI)" if cs.get("proven") and t.get("n") else " tracking"
            out.append(f"🏈 CFB props 👍 (Marv, Bovada): {t.get('w', 0)}-{t.get('l', 0)}{pct}")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(out)
