"""Script O/U live tracker (tracking, unproven). Paper/fake-money only; NEVER places real bets.
Rule (PDF defaults; backtest-tuned thresholds did not validate on held-out data -> not used):
  extreme tier only: |Marv total - market total| >= 3 pts NFL/CFB, >= 4 WNBA, >= 0.6 goals soccer; UF=1; EV>0.
Each run: (1) find new picks -> ledger (+ March_edge Telegram only if SCRIPT_OU_TELEGRAM is on; default off), (2) refresh closing line for pending picks,
(3) grade finished games via ESPN scoreboard, (4) write summary for /reportcard. pick_feed.py reads the ledger.
Disable: systemctl disable --now script-ou.timer   (or touch /opt/marv-bot/state/script_ou/KILL)"""
import json, math, os, sys, time, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
ST = Path("/opt/marv-bot/state"); D = ST / "script_ou"; LED = D / "ledger.json"; SUM = D / "summary.json"
TH = {"nfl": 3.0, "cfb": 3.0, "wnba": 4.0, "nba": 5.0, "soccer": 0.6}  # nba: pregame only, WNBA-style (>=5; tuning did not validate)
SD = {"nfl": 13.0, "cfb": 16.0, "wnba": 15.0, "nba": 19.0}
ESPN = {"nfl": "football/nfl", "cfb": "football/college-football", "wnba": "basketball/wnba", "nba": "basketball/nba"}
SOC_LG = ["eng.1", "eng.2", "esp.1", "fra.1", "ger.1", "ita.1", "mex.1", "ned.1", "por.1", "usa.1"]
now = datetime.now(timezone.utc)

def jl(p, d):
    try: return json.load(open(p))
    except Exception: return d
def ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def pay(a): return a / 100 if a > 0 else 100 / -a
def d2a(d): return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))
def pois_over(lam, line=2.5):
    k = int(math.floor(line)); return 1 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k + 1))
def lam_for(po):
    lo, hi = 0.2, 8.0
    for _ in range(60):
        m = (lo + hi) / 2; lo, hi = (m, hi) if pois_over(m) < po else (lo, m)
    return (lo + hi) / 2
def ts(s):
    try: return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception: return None
def n(s): return re.sub(r"[^a-z0-9]", "", str(s or "").lower())

def bov(sport, home, away):
    for e in (jl(ST / "bovada_prices.json", {}).get(sport) or {}).get("events", []):
        if n(e.get("home")) == n(home) and n(e.get("away")) == n(away) and e.get("total"): return e["total"]
    return None

def candidates():
    out = []
    for key, v in jl(ST / "predictions.json", {}).items():
        sp = v.get("sport")
        if sp not in ("nfl", "cfb", "wnba", "nba") or v.get("model_total") is None: continue
        st = ts(v.get("start"))
        if not st or st <= now: continue
        t = bov(sp, v.get("home"), v.get("away"))
        if t: line, po, pu = float(t[0]), float(t[1] or -110), float(t[2] or -110); book = "Bovada"
        else:
            ln = jl(ST / "lines.json", {}).get(key) or {}
            if ln.get("total") is None: continue
            line, po, pu, book = float(ln.get("total_last") or ln["total"]), -110.0, -110.0, "consensus (Bovada n/a, -110 assumed)"
        proj = float(v["model_total"]); d = proj - line; side = 1 if d > 0 else -1
        mu = 0.5 * proj + 0.5 * line; p_over = 1 - ncdf((line - mu) / SD[sp])
        p = p_over if side > 0 else 1 - p_over; price = po if side > 0 else pu
        out.append(dict(key=f"scriptou:{v.get('game_id') or key}:{'over' if side > 0 else 'under'}", sport=sp, pred_key=key,
                        game=v.get("game_id") or key, home=v.get("home"), away=v.get("away"), start=v.get("start"),
                        line=line, proj=round(proj, 2), d=round(d, 2), side=side, price=price, book=book, p=round(p, 4),
                        ev=round(p * pay(price) - (1 - p), 4), uf=int(v.get("uf", 1))))
    card = jl(ST / "marv_predict/soccer/card.json", {})
    for m in card.get("matches", []):
        bq = m.get("best_qualified") or {}; sel = str(bq.get("selection", "")).lower()
        if bq.get("market") != "total" or "2.5" not in sel or not bq.get("p_market") or not bq.get("price"): continue
        st = ts(str(m.get("kickoff", "")).replace(" ", "T") + ":00+01:00")  # card kickoff is UK local (approx)
        if not st or st <= now: continue
        pm = float(bq["p_market"]); po_m = pm if sel.startswith("over") else 1 - pm
        mkt = lam_for(min(max(po_m, 0.05), 0.95)); proj = float(m.get("total_mean")); d = proj - mkt; side = 1 if d > 0 else -1
        if (side > 0) != sel.startswith("over"): continue  # only price we have is the card's selection
        p_over = pois_over(0.5 * proj + 0.5 * mkt); p = p_over if side > 0 else 1 - p_over; price = d2a(float(bq["price"]))
        hard = [x for x in (m.get("vetoes") or []) if not str(x).lower().startswith("soft")]
        gid = f"{m.get('competition')}:{m.get('home')}-{m.get('away')}:{str(m.get('kickoff'))[:10]}"
        out.append(dict(key=f"scriptou:{gid}:{'over' if side > 0 else 'under'}", sport="soccer", league=m.get("competition"),
                        game=gid, home=m.get("home"), away=m.get("away"), start=st.isoformat(), line=2.5, mkt_goals=round(mkt, 2),
                        proj=round(proj, 2), d=round(d, 2), side=side, price=price, book=m.get("market") or "market avg",
                        p=round(p, 4), ev=round(p * pay(price) - (1 - p), 4), uf=0 if hard else 1))
    return out

def tg(text):
    sys.path.insert(0, os.path.expanduser("~/aj-bet-bot"))
    import yaml; from ajbet.alerts import Notifier
    root = os.path.expanduser("~/aj-bet-bot"); cfg = yaml.safe_load(open(f"{root}/config.yaml")); cfg["_root"] = root
    return Notifier(cfg, dry_run=os.environ.get("SCRIPT_OU_DRY", "0") == "1").send(text, html=False)

def alert(r):
    unit = "goals" if r["sport"] == "soccer" else "pts"
    mk = r.get("mkt_goals", r["line"]) if r["sport"] == "soccer" else r["line"]
    tier = "High Shootout" if r["side"] > 0 else "Defensive Grind"
    pr = f"{int(r['price']):+d}"
    pk = f"{'Over' if r['side'] > 0 else 'Under'} {r['line']}"  # 2026-10-13 March_edge-style header
    pr = pr.replace("-", "\u2212")
    if r.get("book") == "Bovada":
        head = f"\U0001F4CA SCRIPT O/U \u2014 {pk} ({pr}) @ Bovada"
    else:
        ref = "consensus" if 'consensus' in str(r.get('book', '')) else str(r.get('book') or 'market')
        head = f"\U0001F4CA SCRIPT O/U \u2014 {pk} \u00b7 Bovada: no line (using {ref} {pr})"
    emo = {"nfl": "\U0001F3C8", "cfb": "\U0001F3C8", "nba": "\U0001F3C0", "wnba": "\U0001F3C0", "soccer": "\u26BD"}.get(r["sport"], "\U0001F3DF")
    return (f"{head}\n{emo} {r['sport'].upper()} \u00b7 {r['away']} @ {r['home']}\n"
            f"Script O/U (tracking, unproven)\n"
            f"Tier: EXTREME - {tier}\nMarv total {r['proj']} vs market {mk} {unit} ({r['d']:+.1f})\n"
            f"Paper/fake-money tracking only. Not a proven edge.")

def finals(sport, day, league=None):
    path = ESPN.get(sport) or f"soccer/{league}"
    try:
        j = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard",
                         params={"dates": day.strftime("%Y%m%d"), "limit": 400, **({"groups": 80} if sport == "cfb" else {})}, timeout=20).json()
    except Exception: return []
    out = []
    for e in j.get("events", []):
        c = e["competitions"][0]
        if not c.get("status", {}).get("type", {}).get("completed"): continue
        t = {x["homeAway"]: x for x in c["competitors"]}
        out.append((t["home"]["team"].get("displayName"), t["away"]["team"].get("displayName"), float(t["home"]["score"]), float(t["away"]["score"])))
    return out

def same(a, b):
    a, b = n(a), n(b); return bool(a and b) and (a == b or a in b or b in a)

def grade(r):
    st = ts(r["start"])
    if not st or now < st + timedelta(hours=3): return
    days = [(st - timedelta(hours=6)).date(), st.date()]
    lgs = SOC_LG if r["sport"] == "soccer" else [None]
    for lg in lgs:
        for dd in dict.fromkeys(days):
            for h, a, hs, as_ in finals(r["sport"], datetime.combine(dd, datetime.min.time()), lg):
                if same(h, r["home"]) and same(a, r["away"]):
                    tot = hs + as_; res = (tot - r["line"]) * r["side"]
                    r.update(final=f"{int(as_)}-{int(hs)}", total=tot, status="W" if res > 0 else ("L" if res < 0 else "P"),
                             units=round(pay(r["price"]) if res > 0 else (-1.0 if res < 0 else 0.0), 3), graded=now.isoformat())
                    return
    if now > st + timedelta(days=4): r.update(status="ungraded")

def refresh_close(r):
    if r["sport"] == "soccer": return
    st = ts(r["start"])
    if st and now < st:
        t = bov(r["sport"], r["home"], r["away"])
        ln = (jl(ST / "lines.json", {}).get(r.get("pred_key", "")) or {})
        c = float(t[0]) if t else (ln.get("total_last") or ln.get("total"))
        if c is not None: r["close"] = float(c); r["clv"] = round((float(c) - r["line"]) * r["side"], 2)

def summary(L):
    out = {"updated": now.isoformat(), "rule": "extreme only; |d|>=3 NFL/CFB, >=4 WNBA, >=5 NBA (pregame), >=0.6 soccer; UF=1; EV>0", "by": {}}
    for sp in ["ALL", "nfl", "cfb", "wnba", "nba", "soccer"]:
        G = [r for r in L if r.get("status") in ("W", "L", "P") and (sp == "ALL" or r["sport"] == sp)]
        if sp != "ALL" and not G and not any(r["sport"] == sp for r in L): continue
        u = sum(r["units"] for r in G); cl = [r["clv"] for r in L if r.get("clv") is not None and (sp == "ALL" or r["sport"] == sp)]
        out["by"][sp] = dict(W=sum(r["status"] == "W" for r in G), L=sum(r["status"] == "L" for r in G), P=sum(r["status"] == "P" for r in G),
                             units=round(u, 2), roi=round(u / len(G), 3) if G else None, clv=round(sum(cl) / len(cl), 2) if cl else None,
                             pending=sum(1 for r in L if r.get("status") == "pending" and (sp == "ALL" or r["sport"] == sp)))
    a = out["by"].get("ALL", {})
    out["line"] = (f"Script O/U (tracking, unproven): {a.get('W',0)}-{a.get('L',0)}-{a.get('P',0)}, {a.get('units',0):+.2f}u"
                   + (f", ROI {a['roi']*100:+.1f}%" if a.get("roi") is not None else "") + (f", CLV {a['clv']:+.2f}" if a.get("clv") is not None else "")
                   + f", {a.get('pending',0)} pending")
    return out

REALERT_MOVE = 1.5  # re-alert only if the line moved >= 1.5 pts in the bet's favour since the last alert

def save(L):
    """Atomic ledger write. Raises on failure so we never alert without persisting the dedupe record."""
    tmp = LED.with_suffix(".tmp"); json.dump(L, open(tmp, "w"), indent=1); os.replace(tmp, LED)

def _telegram_on():
    """SCRIPT_OU_TELEGRAM must be truthy to send separate Script O/U / Master extremes Telegram alerts.
    Default OFF: tracking, ledger, and grading stay intact; O/U still appears on the daily morning card."""
    return os.environ.get("SCRIPT_OU_TELEGRAM", "").strip().lower() in ("1", "true", "yes", "on")

def _send(c, text):
    if not _telegram_on():
        return False  # silenced; ledger/grading still run
    try:
        ok = tg(text)
        return "dry-run" if os.environ.get("SCRIPT_OU_DRY", "0") == "1" else bool(ok)
    except Exception as ex:
        c["alert_err"] = str(ex)[:200]; return False

def main():
    import fcntl
    D.mkdir(parents=True, exist_ok=True)
    if (D / "KILL").exists(): print("KILL file present"); return
    lk = open(D / ".lock", "a"); fcntl.flock(lk, fcntl.LOCK_EX)  # no overlapping runs
    if not os.access(D, os.W_OK): raise SystemExit(f"state dir {D} not writable; refusing to alert (dedupe would be lost)")
    L = jl(LED, []); have = {r["key"]: r for r in L}
    save(L)  # fail fast BEFORE any Telegram send if the ledger can't be written
    for c in candidates():
        if c["uf"] != 1 or c["ev"] <= 0 or abs(c["d"]) < TH[c["sport"]]: continue
        nba = c.get("sport") == "nba" or "nba" in str(c.get("key", "")).lower()
        r = have.get(c["key"])
        if r is not None:  # already alerted this game/side: only a >=1.5 favourable line move re-alerts
            if r.get("status") != "pending" or nba: continue
            base = float(r.get("alert_line") or r["line"])
            move = (base - c["line"]) * r["side"]  # over: line drops = better; under: line rises = better
            if move < REALERT_MOVE: continue
            r["alert_line"] = c["line"]; r.setdefault("realerts", []).append(dict(ts=now.isoformat(), line=c["line"], price=c["price"], move=round(move, 2), sent=None))
            save(L)
            msg = alert(c).replace("Script O/U (tracking, unproven)", f"LINE MOVE {base} \u2192 {c['line']} ({move:+.1f} in our favour) \u00b7 original pick {r['line']} still graded\nScript O/U (tracking, unproven)", 1)
            r["realerts"][-1]["sent"] = _send(r, msg); save(L)
            print("REALERT", c["key"], base, "->", c["line"]); continue
        opp = c["key"].rsplit(":", 1)[0] + (":under" if c["side"] > 0 else ":over")
        if opp in have: continue
        c.update(status="pending", tier="extreme", created=now.isoformat(), close=None, clv=None, units=None, alert_line=c["line"], alerted=None)
        L.append(c); have[c["key"]] = c
        save(L)  # persist the dedupe record first, then send
        c["alerted"] = False if nba else _send(c, alert(c))
        save(L); print("NEW", c["key"], c["d"], c["price"], "alerted", c["alerted"])
    for r in L:
        if r.get("status") == "pending": refresh_close(r); grade(r)
    save(L)
    s = summary(L); json.dump(s, open(SUM, "w"), indent=1); print(s["line"])

if __name__ == "__main__":
    main()
