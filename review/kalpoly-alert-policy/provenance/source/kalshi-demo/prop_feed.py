"""PROP feed for Kalshi DEMO (fake money only). Pulls March_edge graded pregame player props (stars >= 2, game not started),
requires Marv H2H overlay verdict 'good' (👍; n/a / mixed / 👎 rejected), maps to Kalshi demo player-prop markets
(player + stat + exact or favorable line; worse line skipped). Overs only (client buys YES only)."""
import json, os, re, sqlite3, subprocess, unicodedata
from datetime import datetime, timezone

AJDB = os.environ.get("AJBET_DB", "/home/moonrider1968/aj-bet-bot/ajbet.db")
MARV_PY = "/opt/marv-bot/.venv/bin/python"
SERIES = {  # 2026-10-20 (Marvin): ALL props, every sport with a Kalshi player series (demo only)
    ("nfl", "player_reception_yds"): "KXNFLRECYDS", ("nfl", "player_receptions"): "KXNFLREC",
    ("nfl", "player_pass_yds"): "KXNFLPASSYDS", ("nfl", "player_pass_tds"): "KXNFLPASSTDS",
    ("nfl", "player_rush_yds"): "KXNFLRSHYDS", ("nfl", "player_anytime_td"): "KXNFLANYTD",
    ("nfl", "player_pass_attempts"): "KXNFLPASSATT", ("nfl", "player_pass_completions"): "KXNFLPASSCOMP",
    ("nfl", "player_pass_interceptions"): "KXNFLPASSINT", ("nfl", "player_rush_attempts"): "KXNFLRSHATT",
    ("nfl", "player_rush_reception_yds"): "KXNFLRRYDS",
    ("mlb", "batter_hits"): "KXMLBHIT", ("mlb", "pitcher_strikeouts"): "KXMLBKS", ("mlb", "batter_total_bases"): "KXMLBTB",
    ("mlb", "batter_home_runs"): "KXMLBHR", ("mlb", "batter_rbis"): "KXMLBRBI", ("mlb", "batter_hits_runs_rbis"): "KXMLBHRR",
    ("mlb", "batter_stolen_bases"): "KXMLBSB", ("mlb", "pitcher_outs"): "KXMLBOUTS",
    ("nhl", "player_points"): "KXNHLPTS", ("nhl", "player_goals"): "KXNHLGOAL", ("nhl", "player_assists"): "KXNHLAST",
    ("nhl", "player_total_saves"): "KXNHLSAVE"}  # 2026-10-28 fix: real series is KXNHLSAVE (KXNHLSAVES has no markets)
for _lg, _pre in (("nba", "KXNBA"), ("wnba", "KXWNBA")):
    for _mk, _suf in (("player_points", "PTS"), ("player_rebounds", "REB"), ("player_assists", "AST"), ("player_threes", "3PT"),
                      ("player_blocks", "BLK"), ("player_steals", "STL"), ("player_points_rebounds_assists", "PRA"),
                      ("player_points_rebounds", "PR"), ("player_points_assists", "PA"), ("player_rebounds_assists", "RA")):
        SERIES[(_lg, _mk)] = _pre + _suf

def stars(ev):  # same thresholds as ajbet/friendly.py
    return 1 if ev < 0.07 else (2 if ev <= 0.10 else 3)

def _n(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s).strip()

MIN_EV = float(os.environ.get("PROP_MIN_EV", "0.0001"))  # 2026-10-20: any +EV (no star tier); venue edge still >= min_edge_cents   # 2026-10-15: March_edge prop_ev_min (all EV>=5%, incl. paper-held)
MAX_LINE_DEV = 0.10   # Kalshi line may differ from March_edge line by <= 10%
EXPORT = os.environ.get("PROPS_EXPORT", "/opt/kalshi-demo/data/props/props_export.json")
PREDS = "/opt/marv-bot/state/predictions.json"

BOOST = {"player_pass_yds": 1.25}   # 2026-10-20: passing-yards props 22-12 all-time -> x1.25 stake + first in line

def _boost(r):
    b = BOOST.get(r.get("market"))
    if b:
        r["stake_mult"] = float(r.get("stake_mult") or 1.0) * b; r["prio"] = 10
    return r

def picks(stats=None):
    stats = stats if stats is not None else {}
    try:
        d = json.load(open(EXPORT))
    except Exception as ex:
        stats["blocker"] = f"no export {ex}"; return []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = [r for r in d.get("picks", []) if (r.get("commence") or "") > now]
    stats["export"] = d.get("exported"); stats["raw"] = len(out)
    out = [r for r in out if r.get("ev") is not None and r["ev"] >= MIN_EV and r.get("p")]
    stats["ev5plus"] = len(out)
    return [_boost(r) for r in out]

def picks_live(stats=None, max_age_s=180):
    """Fresh (<3 min) March_edge live prop alerts, >=2 stars, for in-progress games."""
    stats = stats if stats is not None else {}
    try:
        d = json.load(open(EXPORT))
    except Exception as ex:
        stats["blocker"] = f"no export {ex}"; return []
    now = datetime.now(timezone.utc); out = []
    for r in d.get("live", []):
        try:
            age = (now - datetime.fromisoformat(r["ts_utc"].replace("Z", "+00:00"))).total_seconds()
        except Exception:
            continue
        if age <= max_age_s and r.get("ev") is not None and r["ev"] >= MIN_EV and r.get("p"):
            r["age_s"] = int(age); out.append(_boost(r))
    stats.update(export=d.get("exported"), live_raw=len(d.get("live", [])), live_fresh_ev5=len(out))
    return out

MASTER = os.environ.get("MARV_MASTER", "0") == "1"   # 2026-10-13 Master game-script read on the demo prop path
PROP_READS = "/opt/marv-bot/state/master/prop_reads.jsonl"

def marv_game_read(pick, m):
    g = _marv_game_read_legacy(pick, m)
    if not MASTER or pick.get("ts_utc"):  # pregame demo props only (live props untouched)
        return g
    try:
        import sys; sys.path.insert(0, "/opt/marv-feed"); import game_script as GS
        r = GS.read(pick["league"], pick["commence"])
        g["script"] = r
        if r and r.get("grade"):
            side = 1 if pick.get("side") == "over" else -1
            pick.setdefault("p_raw", pick["p"])
            pick["p"] = round(min(0.99, max(0.01, pick["p_raw"] + side * r["prop_shift_over"])), 4)
            if side > 0 and r["grade"] in ("Def Grind", "Lean Grind") and g["verdict"] == "backs":
                g["verdict"], g["why"] = "opposes", g["why"] + f"; script {r['grade']} -> lean under (over skipped)"
        try:
            with open(PROP_READS, "a") as f:
                f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "league": pick["league"], "event_id": pick.get("event_id"),
                                    "player": pick["player"], "market": pick["market"], "side": pick["side"], "line": pick["line"],
                                    "commence": pick["commence"], "p_raw": pick.get("p_raw"), "p": pick["p"], "verdict": g["verdict"],
                                    "script": r}) + "\n")
        except Exception:
            pass
    except Exception as ex:
        g["script_err"] = str(ex)[:100]
    return g

def _marv_game_read_legacy(pick, m):
    """Marv engine game read (predictions.json home_win) for the player's team. Backs = team win prob > 0.5."""
    try:
        et = m["event_ticker"].split("-")[1][7:]          # e.g. BUFLAR
        seg = m["ticker"].split("-")[2]                     # e.g. LARPNACUA12
        side = None
        for k in (2, 3, 4):
            if seg.startswith(et[k:]): side = "home"; break
            if seg.startswith(et[:k]): side = "away"; break
        if not side:
            return {"verdict": "missing", "why": "player team unknown"}
        c0 = datetime.fromisoformat(pick["commence"].replace("Z", "+00:00"))
        for v in json.load(open(PREDS)).values():
            if not isinstance(v, dict) or v.get("sport") != pick["league"] or v.get("home_win") is None:
                continue
            try:
                st = datetime.fromisoformat(str(v.get("start")).replace("Z", "+00:00"))
            except Exception:
                continue
            if abs((st - c0).total_seconds()) > 3 * 3600:
                continue
            hw = float(v["master_home_win"] if MASTER and v.get("master_home_win") is not None else v["home_win"]); tw = hw if side == "home" else 1 - hw
            team = v.get(side)
            return {"verdict": "backs" if tw > 0.5 else "opposes", "why": f"{team} win p={tw:.2f} ({v.get('away')} @ {v.get('home')})"}
        return {"verdict": "missing", "why": "no Marv game read"}
    except Exception as ex:
        return {"verdict": "missing", "why": str(ex)[:100]}

# 2026-10-15: March_edge prop SDs (config.yaml value_finder.prop_sd); count stats use sqrt(line)
PROP_SD = {"player_pass_yds": 55, "player_rush_yds": 24, "player_reception_yds": 22, "player_points": 5.5}
MATCH_LOG = os.environ.get("PROP_MATCH_LOG", "/opt/kalshi-demo/data/prop_match_log.jsonl")

def _ncdf(x):
    import math; return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def _ninv(p):
    lo, hi = -8.0, 8.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _ncdf(mid) < p: lo = mid
        else: hi = mid
    return (lo + hi) / 2

def p_over_at(pick, kline):
    """Same-side (over) probability re-priced from March_edge's line to Kalshi's line (normal approx)."""
    L, p = float(pick["line"]), min(0.995, max(0.005, float(pick["p"])))
    if abs(kline - L) < 1e-9: return p
    sd = PROP_SD.get(pick["market"]) or max(0.9, L ** 0.5)
    mu = L + sd * _ninv(p)                  # P(X > L) = p  ->  mean
    return 1 - _ncdf((kline - mu) / sd)

def log_match(rec):
    try:
        with open(MATCH_LOG, "a") as f: f.write(json.dumps(dict(rec, ts=datetime.now(timezone.utc).isoformat())) + "\n")
    except Exception:
        pass

def kelly_usd(p, ask_c, B, frac=0.25, cap_pct=1.0, max_usd=50.0, mult=1.0):
    """Quarter-Kelly for a YES contract at ask_c cents, capped at cap_pct% of B and max_usd, x stake multiplier."""
    q = ask_c / 100.0
    if not 0 < q < 1: return 0.0
    f = max(0.0, (p - q) / (1 - q)) * frac
    return round(min(f * B, B * cap_pct / 100, max_usd) * mult, 2)

def side_ask(m, side):
    """Cents to BUY `side`: YES ask, or NO ask (no_ask_dollars, else 100 - YES bid)."""
    if side != "no":
        return m.get("yes_ask")
    na = m.get("no_ask_dollars")
    if na not in (None, ""):
        try:
            return int(round(float(na) * 100))
        except (TypeError, ValueError):
            pass
    yb = m.get("yes_bid")
    return 100 - yb if yb else None

def map_prop(pick, client):
    """Best Kalshi YES market for the pick: same player + stat, line within 10%, edge re-checked at Kalshi's line."""
    series = SERIES.get((pick["league"], pick["market"]))
    if not series:
        return None, "unsupported stat (no Kalshi series)"
    if pick["side"] not in ("over", "under"):
        return None, "unsupported side"
    under = pick["side"] == "under"
    pn = _n(pick["player"]); L = float(pick["line"]); best = None; seen = 0
    for m in client.markets(series_ticker=series, status="open", limit=1000).get("markets", []):
        sub = m.get("yes_sub_title") or ""
        name, _, thr = sub.partition(":")
        if _n(name) != pn:
            continue
        if pick.get("away_abbr") and pick.get("home_abbr"):   # 2026-10-16: exact game (both team codes = event suffix)
            from mapper import event_exact
            if not event_exact(m.get("event_ticker"), pick["away_abbr"], pick["home_abbr"]):
                continue
        mt = re.search(r"([\d.]+)\+", thr)
        if not mt:
            continue
        seen += 1
        T = float(mt.group(1)); kl = T - 0.5          # "T+" == over T-0.5
        if L > 0 and abs(kl - L) / L > MAX_LINE_DEV:
            continue
        # 2026-10-31 kalshi exact-or-better (Marvin): Over needs Kalshi line <= alert line, Under needs >= (Over 13.5 on 15+ was a bug)
        if (not under and kl > L + 1e-9) or (under and kl < L - 1e-9):
            continue
        ask = side_ask(m, "no" if under else "yes")
        if not ask or ask >= 100:
            continue
        if under:   # Under kl == NO on 'T+' (T = kl + 0.5); over prob at the pick line = 1 - p(under)
            pk = 1 - p_over_at(dict(pick, p=1 - float(pick["p"])), kl)
        else:
            pk = p_over_at(pick, kl)
        edge = round(100 * pk) - ask
        if best is None or edge > best["edge_c"]:
            best = {"ticker": m["ticker"], "yes_ask": ask, "side": "no" if under else "yes", "T": T, "kalshi_line": kl,
                    "p_kalshi": round(pk, 4), "edge_c": edge, "yes_sub_title": sub, "event_ticker": m.get("event_ticker")}
    if best: return best, None
    return None, ("player not on Kalshi" if not seen else "no exact-or-better Kalshi line within 10% with an ask")
