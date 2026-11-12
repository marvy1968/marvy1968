"""alt70 -> Kalshi DEMO paper trades (Marvin 2026-10-19). Fake money, demo API only.
Reads /opt/marv-feed/alt70/picks.jsonl (🛡️ 70% alt-line picks). Trades a pick only when Kalshi lists the same
player+stat (KXNFLPASSYDS / KXNFLRECYDS / KXNFLRUSHYDS) or team total (KXNFLTEAMTOTAL / KXNCAAFTEAMTOTAL) at a
line within 10% of the pick, Kalshi's ask implies <= 70% (ask <= 70c), and the model P re-priced at Kalshi's line
beats the ask by >= min_edge_cents. One position per game + side (side_guard + held tickers); props one per
player/stat/game. Logs to data/demo_log.jsonl (me_type alt70) so the existing grader settles it.
Off: ALT70_KALSHI=off or KILL file. Dry: DEMO_DRY=1 (no orders, nothing logged)."""
import json, math, os, re, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import yaml
import autogate as ag
import prop_feed as PF
import side_guard as SG
from kalshi_demo import KalshiDemo, _normalize_market
_kfee = lambda a: (0.07 * a * (100 - a) / 100) if isinstance(a, (int, float)) and 0 < a < 100 else 0.0  # 2026-10-30 (Marvin): Kalshi taker fee in cents/contract, subtracted from edge before gate

PICKS = os.environ.get("ALT70_PICKS", "/opt/marv-feed/alt70/picks.jsonl")
LOG = os.path.join(HERE, "data", "demo_log.jsonl")
MAX_ASK = 70
MAX_EDGE = 25    # model - ask > 25c = stale/one-sided demo book (e.g. SF over 20.5 at 12c, no bid), not an edge
TT_SERIES = {"nfl": "KXNFLTEAMTOTAL", "ncaaf": "KXNCAAFTEAMTOTAL"}
STAT_MK = {"pass_yds": "player_pass_yds", "rush_yds": "player_rush_yds", "rec_yds": "player_reception_yds"}
ET = ZoneInfo("America/New_York")


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _last(s):
    return re.sub(r"[^a-z]", " ", str(s).lower()).split()[-1:] or [""]


def map_team(x, c):
    """Kalshi team-total market for the pick: same team (nickname), same game day, 'over T.5' within 10%."""
    day = datetime.fromisoformat(x["start"].replace("Z", "+00:00")).astimezone(ET).strftime("%y%b%d").upper()
    nick = _last(x["who"])[0]; best = None
    for m in c.markets(series_ticker=TT_SERIES.get(x.get("sport", "nfl"), "KXNFLTEAMTOTAL"), status="open", limit=1000).get("markets", []):
        sub = m.get("yes_sub_title") or ""
        if day not in str(m.get("event_ticker")) or nick not in sub.lower():
            continue
        mt = re.search(r"over ([\d.]+)", sub.lower())
        if not mt:
            continue
        kl = float(mt.group(1))
        if abs(kl - x["line"]) / max(1.0, x["line"]) > 0.10:
            continue
        under = x["side"] == "Under"
        po = 1 - _ncdf((kl - float(x["proj"])) / 10.0)          # alt70 team model: Normal(proj, SD 10)
        pk = 1 - po if under else po
        ask = PF.side_ask(_normalize_market(m), "no" if under else "yes")
        if not ask or ask >= 100:
            continue
        edge = round(round(100 * pk) - ask - _kfee(ask), 2)
        if best is None or edge > best["edge_c"]:
            best = {"ticker": m["ticker"], "side": "no" if under else "yes", "yes_ask": ask, "kalshi_line": kl,
                    "p_kalshi": round(pk, 4), "edge_c": edge, "event_ticker": m.get("event_ticker")}
    return (best, None) if best else (None, "no Kalshi team total within 10%")


def main():
    if os.environ.get("ALT70_KALSHI", "on").lower() in ("0", "off", "false", "no"):
        print("alt70_kalshi off"); return
    if not os.path.exists(PICKS):
        print("alt70_kalshi: no picks file"); return
    cfg = yaml.safe_load(open(os.path.join(HERE, "kalshi_demo.yaml")))
    gcfg = ag.load_cfg(os.path.join(HERE, "autogate.yaml"))
    assert gcfg.get("AUTO_ENABLED") is False, "AUTO_ENABLED must stay false (production lock)"
    DRY = os.environ.get("DEMO_DRY") == "1"
    kill = "manual KILL file" if os.path.exists(os.path.join(HERE, "KILL")) else None
    c = KalshiDemo()
    rows = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
    today = datetime.now(ag.CT).date().isoformat()
    skip = {r.get("ticker") for r in rows if r.get("date") == today and r.get("mode") == "demo" and r.get("order") and not r.get("error")}
    if c.has_key:
        for mp in (c.positions().get("market_positions") or []):
            if float(mp.get("position_fp") or 0) != 0: skip.add(mp.get("ticker"))
    sides = SG.held_sides(rows, skip)
    done = {r.get("key") for r in rows if r.get("mode") == "demo" and r.get("order") and not r.get("error")}
    props_done = {(PF._n(r.get("prop_player") or ""), r.get("prop_market"), str(r.get("ticker", "")).split("-")[1:2] and str(r.get("ticker")).split("-")[1])
                  for r in rows if r.get("mode") == "demo" and r.get("order") and not r.get("error") and r.get("ticker") in skip and r.get("prop_player")}
    now = datetime.now(timezone.utc); placed = 0; out = []
    for ln in open(PICKS):
        try:
            x = json.loads(ln)
        except ValueError:
            continue
        key = "alt70:" + x["key"]
        if key in done or datetime.fromisoformat(x["start"].replace("Z", "+00:00")) <= now:
            continue
        res = {"pick": f"{x['who']} {x['stat']} {x['side']} {x['line']:g} ({int(x['price']):+d} Bovada, P {x['p']:.2f})"}
        if x["kind"] == "team":
            m, why = map_team(x, c)
        else:
            mk = STAT_MK.get(x["stat"])
            pk = {"league": x.get("sport", "nfl"), "market": mk, "player": x["who"], "side": x["side"].lower(),
                  "line": x["line"], "p": x["p"]}
            m, why = PF.map_prop(pk, c) if (x.get("sport", "nfl"), mk) in PF.SERIES else (None, "no Kalshi series")
        if not m:
            out.append(dict(res, result="skip", reason=why)); continue
        game = str(m["ticker"]).split("-")[1] if "-" in m["ticker"] else m["ticker"]
        sk = (game, f"{x['who'].lower()} {x['stat']} {x['side'].lower()}")
        if m["ticker"] in skip or sk in sides or (x["kind"] == "player" and (PF._n(x["who"]), STAT_MK.get(x["stat"]), game) in props_done):
            out.append(dict(res, result="skip", reason="already hold this game+side / player+stat", ticker=m["ticker"])); continue
        ask = m["yes_ask"]; pkal = float(m["p_kalshi"]); edge = round(round(100 * pkal) - ask - _kfee(ask), 2)
        stake = PF.kelly_usd(pkal, ask, gcfg["bankroll"], gcfg["staking"]["after_kelly_frac"], gcfg["staking"]["after_cap_pct"], cfg["max_stake_usd"], 1.0)
        reason = None
        if kill: reason = f"kill: {kill}"
        elif ask > MAX_ASK: reason = f"Kalshi ask {ask}c implies > 70%"
        elif edge > MAX_EDGE: reason = f"Kalshi demo quote looks stale ({ask}c vs model {round(100 * pkal)}c)"
        elif edge < cfg["min_edge_cents"]: reason = f"edge {edge}c < {cfg['min_edge_cents']}c (model {pkal:.0%} at Kalshi line {m['kalshi_line']:g})"
        elif stake <= 0: reason = "kelly stake 0"
        elif placed >= cfg["max_orders_per_run"]: reason = "max orders this run"
        elif not (cfg.get("DEMO_AUTO") and c.has_key): reason = "DEMO_AUTO off / no key"
        elif x["kind"] == "player" and not cfg.get("DEMO_PROPS"): reason = "DEMO_PROPS off"
        import type_gate as TG   # 2026-10-23 TYPE-GATE: alt70 has no settled bets -> track-only until it earns a spot
        if reason is None and not TG.allowed("alt70"):
            reason = "track-only: alt70"
            if not DRY:
                TG.track(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "track_only.jsonl"),
                         {"date": today, "type": "alt70", "key": key, "pick": "ALT70 " + res["pick"], "ticker": m["ticker"], "side": m["side"],
                          "ask": ask, "fair_c": round(100 * pkal), "edge_c": edge, "stake": stake, "contracts": max(1, int(stake * 100 // ask))})
        if reason or DRY:
            out.append(dict(res, result="would_order" if (DRY and not reason) else "skip", reason=reason or "DEMO_DRY",
                            ticker=m["ticker"], ask=ask, model_c=round(100 * pkal), kalshi_line=m["kalshi_line"])); continue
        rec = {"ts": datetime.now(ag.CT).isoformat(), "date": today, "key": key, "pick": "ALT70 " + res["pick"], "ticker": m["ticker"],
               "side": m["side"], "ask": ask, "fair_c": round(100 * pkal), "edge_c": edge, "stake": stake, "kill": kill,
               "me_type": "alt70", "src": "alt70", "prop_player": x["who"] if x["kind"] == "player" else None,
               "prop_market": STAT_MK.get(x["stat"]), "kalshi_line": m["kalshi_line"], "mode": "demo"}
        try:
            rec["order"] = c.place_order(m["ticker"], m["side"], max(1, int(stake * 100 // ask)), ask, meta=rec)
            placed += 1; skip.add(m["ticker"]); sides.add(sk)
        except Exception as ex:
            rec["error"] = str(ex)[:200]
        with open(LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        out.append(dict(res, result="order" if "order" in rec else "error", ticker=m["ticker"], ask=ask))
    for r in out:
        print(r)
    print(f"alt70_kalshi: {placed} demo order(s), {len(out)} pick(s) checked{' (DRY)' if DRY else ''}")


if __name__ == "__main__":
    main()
