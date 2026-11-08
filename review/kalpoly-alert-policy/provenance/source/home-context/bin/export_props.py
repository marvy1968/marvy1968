#!/usr/bin/env python3
"""Runs as moonrider1968 (cron, every minute). Read-only export of March_edge player props -> /opt/kalshi-demo/data/props/props_export.json
pregame: commence in future. live: game started, alert row logged in last 10 min (prop_feed enforces <3 min)."""
import re
import json, os, sqlite3, tempfile
from datetime import datetime, timezone, timedelta
DB = "/home/moonrider1968/aj-bet-bot/ajbet.db"; OUT = "/opt/kalshi-demo/data/props/props_export.json"
nowdt = datetime.now(timezone.utc); now = nowdt.strftime("%Y-%m-%dT%H:%M:%SZ")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15)
K = "league market player side line price ev p commence event_id".split()
rows = con.execute("""select league,market,player,side,line,price,max(ev),coalesce(model_p,fair_p),commence,event_id
    from paper_bets where (market like 'player%' or market like 'batter%' or market like 'pitcher%') and commence > ? and ev is not null
    group by league,market,player,side,line""", (now,)).fetchall()
picks = [dict(zip(K, r)) for r in rows]
live = {}
for r in con.execute("""select league,market,player,side,line,price,ev,coalesce(model_p,fair_p),commence,event_id,ts
    from paper_bets where (market like 'player%' or market like 'batter%' or market like 'pitcher%') and commence <= ? and ev is not null and id > (select max(id)-400 from paper_bets)""", (now,)):
    d = dict(zip(K + ["ts"], r))
    try:
        t = datetime.fromisoformat(d["ts"])
    except Exception:
        continue
    if nowdt - t > timedelta(minutes=10):
        continue
    d["ts_utc"] = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"); d["live"] = True
    k = (d["league"], d["market"], d["player"], d["side"], d["line"])
    if k not in live or d["ts"] > live[k]["ts"]:
        live[k] = d
# 2026-10-16: March_edge SENT Telegram alerts (sent=1), last 48h -> "alerts" (Kalshi demo + Polymarket paper feed)
alerts = []
since = (nowdt - timedelta(hours=48)).astimezone().isoformat()
for r in con.execute("""select a.id,a.ts,a.event_id,a.league,a.strategy,a.market,a.selection,a.line,a.price,a.book,a.fair_p,a.edge,
        a.trigger,a.message,a.details_json,(select player from paper_bets p where p.alert_id=a.id limit 1),
        (select commence from paper_bets p where p.alert_id=a.id limit 1)
        from alerts a where (a.sent=1 or (a.strategy='team_trend' and a.league='nfl')) and a.ts >= ? order by a.id""", (since,)):
    try:
        det = json.loads(r[14] or "{}")
    except Exception:
        det = {}
    keep = {k: det.get(k) for k in ("matchup", "in_game", "live", "p", "win_p", "trend", "label", "move", "live_total",
                                    "pregame_total", "my_book", "my_price", "situation") if k in det}
    _wc = re.search(r"Win chance (\d+)%", r[13] or "")   # 2026-10-20: the card's (paper-calibrated when shown) win chance
    if _wc: keep["card_p"] = int(_wc.group(1)) / 100
    if r[4] == "team_trend" and r[3] == "nfl": keep["paper_feed"] = True   # NFL team trends: +19.7% all-time, not Telegram-sent
    alerts.append({"id": r[0], "ts": r[1], "event_id": r[2], "league": r[3], "strategy": r[4], "market": r[5], "selection": r[6],
                   "line": r[7], "price": r[8], "book": r[9], "fair_p": r[10], "edge": r[11], "trigger": r[12],
                   "headline": (r[13] or "").split("\n")[0][:160], "details": keep, "player": r[15], "commence": r[16]})
# 2026-10-23 (Marvin, 2:37 PM CT): in-game player props March_edge generated but did NOT send (below the 70% bar),
# last 2h, for the Kalshi DEMO trader only (separate key; Polymarket reads "alerts" only; Telegram unchanged).
unsent = []
since2 = (nowdt - timedelta(hours=2)).astimezone().isoformat()
for r in con.execute("""select a.id,a.ts,a.event_id,a.league,a.strategy,a.market,a.selection,a.line,a.price,a.book,a.fair_p,a.edge,
        a.details_json,(select player from paper_bets p where p.alert_id=a.id limit 1),
        (select commence from paper_bets p where p.alert_id=a.id limit 1)
        from alerts a where a.sent=0 and (a.market like 'player%' or a.market like 'batter%' or a.market like 'pitcher%')
        and a.ts >= ? order by a.id""", (since2,)):
    try:
        det = json.loads(r[12] or "{}")
    except Exception:
        det = {}
    if not det.get("in_game"):
        continue
    keep = {k: det.get(k) for k in ("matchup", "in_game", "my_stale", "live_reason", "alert_blocked", "price_age_s",
                                    "consensus_loc", "median_line", "pacer_p", "p_current", "prob_src", "pacer_err") if k in det}   # 2026-10-31 kalfix: + pacer projection
    keep["p_cal"] = (det.get("paper_conf") or {}).get("p_cal")
    unsent.append({"id": r[0], "ts": r[1], "event_id": r[2], "league": r[3], "strategy": r[4], "market": r[5], "selection": r[6],
                   "line": r[7], "price": r[8], "book": r[9], "fair_p": r[10], "edge": r[11], "details": keep,
                   "player": r[13], "commence": r[14], "headline": "unsent live prop"})
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT))
with os.fdopen(fd, "w") as f: json.dump({"exported": now, "picks": picks, "live": list(live.values()), "alerts": alerts, "unsent_live_props": unsent}, f)
os.chmod(tmp, 0o644); os.replace(tmp, OUT)
