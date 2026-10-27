"""Live/paper runner for the five systems on OANDA v20 (practice by default).

One-shot and idempotent: a systemd timer runs it every 5 minutes.  Each run
  * at the first run after 17:15 NY of a new trading day: end-of-day management of open trades
    (trail/time exits) using the just-closed daily candle, then plans today's entry orders;
  * intraday: arms conditional orders from today's 5-minute highs/lows, places broker-side stop
    entries with stopLossOnFill/takeProfitOnFill (GTD = 17:00 NY), keeps Turtle Soup's
    'one tick under today's low' stop current before the fill, cancels OCO siblings after a fill,
    queues Turtle Soup re-entries, and flattens day trades (Crabel) at 16:50 NY.
Every stop and target lives at OANDA, so a crash or VM reboot never leaves a trade unprotected.
"""
import argparse, json, os, sys, time, traceback, dataclasses
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
import pandas as pd

from .strategies import BY_SID
from .strategies.base import Order, Position
from .instruments import PAIRS, ALL_PAIRS, pip, precision, rnd, spread as typ_spread, MAX_SPREAD_MULT
from .sizing import units_for_risk
from .oanda import Oanda, OandaError

NY = ZoneInfo("America/New_York")
TAG = "fxexp"

# ---------------------------------------------------------------- time helpers
def trading_day(ts):
    """FX trading date for an aware datetime: after 17:00 NY belongs to the next date."""
    return (ts.astimezone(NY) + timedelta(hours=7)).date()

def day_bounds(td):
    start = datetime(td.year, td.month, td.day, 17, 0, tzinfo=NY) - timedelta(days=1)
    end = datetime(td.year, td.month, td.day, 17, 0, tzinfo=NY)
    return start, end

def market_open(now):
    n = now.astimezone(NY); wd = n.weekday()
    if wd == 5: return False
    if wd == 4 and n.hour >= 17: return False
    if wd == 6 and n.hour < 17: return False
    return True

def in_rollover(now):
    n = now.astimezone(NY); m = n.hour * 60 + n.minute
    return 16 * 60 + 55 <= m < 17 * 60 + 15

def fmt_px(pair, x): return f"{rnd(pair, x):.{precision(pair)}f}"

# ---------------------------------------------------------------- order math (pure)
def entry_levels(o, pair, day_open, day_low, day_high, spr):
    """Return (entry_level, stop_price, stop_distance, tp_price) for a stop-entry order."""
    t = pip(pair)
    level = o.price if o.price is not None else day_open + o.from_open
    if o.sl is not None: sl = o.sl
    elif o.sl_from_open is not None: sl = day_open + o.sl_from_open
    elif o.sl_dist is not None: sl = level - o.side * o.sl_dist
    elif o.sl_daylow: sl = (day_low - spr / 2 - t) if o.side == 1 else (day_high + spr / 2 + t)
    else: raise ValueError("order without a stop")
    tp = o.tp if o.tp is not None else (level + o.side * o.tp_dist if o.tp_dist else None)
    return level, sl, (o.sl_dist if o.sl_dist is not None else None), tp

def is_armed(o, day_low, day_high):
    if o.arm is None: return True
    return day_low <= o.arm if o.side == 1 else day_high >= o.arm

def order_body(pair, o, units, level, sl, sl_dist, tp, gtd, cid, sid, spr):
    body = {"type": {"stop": "STOP", "limit": "LIMIT"}.get(o.kind, "MARKET"), "instrument": pair,
            "units": str(units * o.side), "positionFill": "OPEN_ONLY",
            "clientExtensions": {"id": cid, "tag": TAG, "comment": sid},
            "tradeClientExtensions": {"id": "t-" + cid, "tag": TAG, "comment": sid}}
    if o.kind == "stop":
        body.update(price=fmt_px(pair, level), timeInForce="GTD", gtdTime=gtd.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                    priceBound=fmt_px(pair, level + o.side * 3 * spr))
    elif o.kind == "limit":
        body.update(price=fmt_px(pair, level), timeInForce="GTD", gtdTime=gtd.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z"))
    else:
        body.update(timeInForce="FOK")
    if sl_dist is not None:
        body["stopLossOnFill"] = {"distance": f"{sl_dist:.{precision(pair)}f}", "timeInForce": "GTC"}
    else:
        body["stopLossOnFill"] = {"price": fmt_px(pair, sl), "timeInForce": "GTC"}
    if tp is not None:
        body["takeProfitOnFill"] = {"price": fmt_px(pair, tp), "timeInForce": "GTC"}
    return body

def o2d(o): return dataclasses.asdict(o)
def d2o(d): return Order(**{k: v for k, v in d.items() if k in {f.name for f in dataclasses.fields(Order)}})

# ---------------------------------------------------------------- runner
class Runner:
    def __init__(self, cfg, clients, state_path, now=None, log=print):
        self.cfg, self.clients, self.state_path = cfg, clients, state_path
        self.now = now or datetime.now(timezone.utc); self.log = log
        self.state = json.load(open(state_path)) if os.path.exists(state_path) else {}
        self._daily, self._intra, self._prices, self._bars = {}, {}, {}, {}

    def save(self):
        tmp = self.state_path + ".tmp"; json.dump(self.state, open(tmp, "w"), indent=1, default=str); os.replace(tmp, self.state_path)

    # data (shared across systems in one run; reads use the first client)
    def daily(self, cli, pair, td):
        if pair not in self._daily:
            df = cli.candles(pair, "D", count=400)
            df = df[df["complete"]].copy()
            df["date"] = pd.to_datetime([trading_day(t.to_pydatetime()) for t in df["time"]])
            self._daily[pair] = df.set_index("date")[["open", "high", "low", "close"]]
        d = self._daily[pair]
        return d[d.index < pd.Timestamp(td)]

    def intraday(self, cli, pair, td):
        if pair not in self._intra:
            start, _ = day_bounds(td)
            df = cli.candles(pair, "M5", frm=start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            if df.empty: self._intra[pair] = None
            else: self._intra[pair] = (float(df["open"].iloc[0]), float(df["low"].min()), float(df["high"].max()))
        return self._intra[pair]

    def bars(self, cli, pair, td):
        """Completed M5 bars of the last ~5 calendar days (intraday systems), with trading date 'td'."""
        if pair not in self._bars:
            # most recent ~1700 M5 candles (~6 trading days). NB: 'from' without 'count' returns only 500.
            df = cli.candles(pair, "M5", count=1700)
            if not df.empty:
                df = df[df["complete"]].copy()
                if "volume" not in df: df["volume"] = 1
                df["td"] = [trading_day(t.to_pydatetime()) for t in df["time"]]
                df = df.reset_index(drop=True)
            self._bars[pair] = df
        return self._bars[pair]

    def pairs_for(self, sid, strat):
        return self.cfg.get("system_pairs", {}).get(sid) or strat.pairs or self.cfg["pairs"]

    def prices(self, cli):
        if not self._prices:
            names = list(dict.fromkeys(list(PAIRS) + [p for sid in self.cfg["systems"] for p in self.pairs_for(sid, BY_SID[sid]())]))
            self._prices = cli.pricing([p for p in names if p in ALL_PAIRS])
        return self._prices

    def run(self):
        if not market_open(self.now):
            self.log("market closed; nothing to do"); return
        td = trading_day(self.now)
        for sid in self.cfg["systems"]:
            cli = self.clients[sid]; strat = BY_SID[sid]()
            try:
                self.run_system(sid, strat, cli, td)
            except Exception as e:
                self.log(f"[{sid}] ERROR {e}\n{traceback.format_exc()}")
        self.save()

    def run_system(self, sid, strat, cli, td):
        st = self.state.setdefault(sid, {})
        all_trades = cli.open_trades()
        trades = [t for t in all_trades if t.get("clientExtensions", {}).get("comment") == sid]
        # US accounts: no hedging + FIFO. Never open a pair another system/bot already holds in this account.
        busy = {t["instrument"] for t in all_trades if t.get("clientExtensions", {}).get("comment") != sid}
        pend = [o for o in cli.pending_orders() if o.get("clientExtensions", {}).get("comment") == sid]
        summ = cli.summary(); nav = float(summ["NAV"]); prices = self.prices(cli)
        # account-level safety: with 11 systems sharing one account, stop adding exposure when
        # margin in use exceeds max_margin_used (default 50%) of NAV. Existing trades keep their stops.
        self._margin_full = float(summ.get("marginUsed", 0) or 0) > self.cfg.get("max_margin_used", 0.5) * nav
        mids = {k: (b + a) / 2 for k, (b, a, _) in prices.items()}
        n = self.now.astimezone(NY)
        for pair in self.pairs_for(sid, strat):
            ps = st.setdefault(pair, {"day": None, "orders": [], "trades": {}, "sstate": {}})
            ptrades = [t for t in trades if t["instrument"] == pair]
            ppend = [o for o in pend if o["instrument"] == pair]
            self.sync_closed(sid, strat, cli, pair, ps, ptrades, td)
            for t in ptrades: self.register(ps, t, td)
            # --- once per trading day, after the rollover settles
            if ps["day"] != str(td) and not in_rollover(self.now):
                hist = self.daily(cli, pair, td)
                for t in ptrades: self.eod_manage(sid, strat, cli, pair, ps, t, hist)
                for o in ppend: cli.cancel_order(o["id"]); self.log(f"[{sid}] {pair} cancel stale order {o['id']}")
                ppend = []
                carry = [r for r in ps["orders"] if r["exp"] >= str(td) and r["status"] in ("planned", "armed", "placed")]
                for r in carry: r["status"] = "planned"
                ps["orders"] = carry
                if not ptrades and not self.cfg.get("no_new_entries"):
                    for i, o in enumerate(strat.plan(hist, pair, ps["sstate"])):
                        ps["orders"].append({"o": o2d(o), "status": "planned", "cid": f"{sid[:6]}-{pair}-{td:%Y%m%d}-{i}", "exp": str(td + timedelta(days=o.expires_days))})
                ps["day"] = str(td)
                if ps["orders"]: self.log(f"[{sid}] {pair} planned {len(ps['orders'])} order(s) for {td}")
            # --- day trades: flat before the close
            if (n.hour, n.minute) >= (16, 50) and n.hour < 17:
                for t in ptrades:
                    if ps["trades"].get(t["id"], {}).get("close_eod"):
                        cli.close_trade(t["id"]); self.log(f"[{sid}] {pair} close day trade {t['id']} (eod)")
                for o in ppend: cli.cancel_order(o["id"])
                continue
            if in_rollover(self.now): continue
            if pair in busy and not ptrades:
                for o in ppend: cli.cancel_order(o["id"]); self.log(f"[{sid}] {pair} cancel {o['id']}: pair held by another system/bot")
                for r in ps["orders"]:
                    if r["status"] == "placed": r["status"] = "armed"
                continue
            if strat.intraday and not ptrades and not self.cfg.get("no_new_entries"):
                self.intraday_signal(sid, strat, cli, pair, ps, td)
            self.intraday_orders(sid, cli, pair, ps, ptrades, ppend, td, nav, mids, prices)

    def sync_closed(self, sid, strat, cli, pair, ps, ptrades, td):
        """Detect trades that closed since last run (Turtle Soup re-entry rule)."""
        open_ids = {t["id"] for t in ptrades}
        for tid, meta in list(ps["trades"].items()):
            if tid in open_ids: continue
            ps["trades"].pop(tid)
            try: tr = cli.trade(tid)
            except OandaError: continue
            stopped = tr.get("stopLossOrder", {}).get("state") == "FILLED"
            day_k = len(pd.bdate_range(meta["entry_day"], str(td)))  # trading days incl. entry day
            if stopped:
                pos = Position(meta["side"], float(tr["price"]), 0, None, 0, 0, None, meta=meta.get("meta", {}))
                ro = strat.after_stop(pos, day_k, pair, ps["sstate"])
                if ro is not None:
                    ps["orders"] = [{"o": o2d(ro), "status": "planned", "cid": f"{sid[:6]}-{pair}-{td:%Y%m%d}-r",
                                     "exp": str(td + timedelta(days=ro.expires_days))}]
                    self.log(f"[{sid}] {pair} queued re-entry after stop on day {day_k}")

    def register(self, ps, t, td):
        """Remember side/entry day/strategy meta for a trade opened by one of our orders."""
        if t["id"] in ps["trades"]: return
        cid = t.get("clientExtensions", {}).get("id", "")[2:]
        rec = next((r for r in ps["orders"] if r["cid"] == cid), None)
        o = d2o(rec["o"]) if rec else None
        if rec: rec["status"] = "filled"
        ps["trades"][t["id"]] = {"side": 1 if float(t["currentUnits"]) > 0 else -1,
                                 "entry_day": str(trading_day(pd.Timestamp(t["openTime"]).to_pydatetime())),
                                 "close_eod": bool(o and o.close_eod), "meta": dict(o.meta) if o else {}}

    def eod_manage(self, sid, strat, cli, pair, ps, t, hist):
        meta = ps["trades"][t["id"]]
        side = meta["side"]; sl = float(t["stopLossOrder"]["price"]) if t.get("stopLossOrder") else None
        ed = pd.Timestamp(meta["entry_day"])
        entry_di = int((hist.index < ed).sum())
        pos = Position(side, float(t["price"]), sl, None, 0.0, entry_di, t["openTime"], meta=meta["meta"])
        m = strat.manage(pos, hist, pair, ps["sstate"])
        if m.close:
            cli.close_trade(t["id"]); self.log(f"[{sid}] {pair} close {t['id']} ({m.reason})")
        elif m.new_sl is not None and sl is not None and (m.new_sl - sl) * side > pip(pair) * 0.1:
            cli.set_stop(t["id"], fmt_px(pair, m.new_sl)); self.log(f"[{sid}] {pair} trail stop {t['id']} {fmt_px(pair, sl)} -> {fmt_px(pair, m.new_sl)}")

    def intraday_signal(self, sid, strat, cli, pair, ps, td):
        """Intraday systems: ask the strategy for a new entry when it has nothing live on this pair."""
        if any(r["status"] in ("planned", "armed", "placed", "sent") for r in ps["orders"]): return
        sig = ps.setdefault("sig", {})
        if sig.get("day") != str(td): sig.clear(); sig.update(day=str(td), n=0)
        if sig["n"] >= strat.max_per_day: return
        bars = self.bars(cli, pair, td)
        if bars is None or len(bars) == 0: return
        hist = self.daily(cli, pair, td)
        orders = strat.signal(hist, bars, pair, ps["sstate"], self.now)
        for o in orders:
            sig["n"] += 1
            ps["orders"].append({"o": o2d(o), "status": "armed", "cid": f"{sid[:6]}-{pair}-{td:%Y%m%d}-s{sig['n']}", "exp": str(td),
                                 "gtd": (self.now + timedelta(minutes=o.gtd_minutes)).isoformat() if o.gtd_minutes else None})
            self.log(f"[{sid}] {pair} signal {'BUY' if o.side == 1 else 'SELL'} {o.kind} {o.meta}")

    def intraday_orders(self, sid, cli, pair, ps, ptrades, ppend, td, nav, mids, prices):
        if not ps["orders"]: return
        spr_now = None
        if pair in prices: b, a, tradeable = prices[pair]; spr_now = a - b
        spr = typ_spread(pair)
        # a fill happened -> cancel OCO siblings, remember the trade's meta
        if ptrades:
            for o in ppend: cli.cancel_order(o["id"]); self.log(f"[{sid}] {pair} OCO cancel {o['id']}")
            for r in ps["orders"]:
                if r["status"] in ("planned", "armed", "placed"): r["status"] = "cancelled"
            return
        ix = self.intraday(cli, pair, td)
        if ix is None: return
        day_open, lo, hi = ix
        pend_by_cid = {o.get("clientExtensions", {}).get("id"): o for o in ppend}
        for r in ps["orders"]:
            if r["status"] in ("filled", "cancelled", "expired", "missed"): continue
            if r["exp"] < str(td): r["status"] = "expired"; continue
            o = d2o(r["o"])
            if r["status"] == "planned" and is_armed(o, lo, hi): r["status"] = "armed"
            if r["status"] == "armed" and r.get("gtd") and datetime.fromisoformat(r["gtd"]) <= self.now:
                r["status"] = "expired"; continue
            if r["status"] == "placed" and r["cid"] not in pend_by_cid:
                r["status"] = "missed"; continue     # cancelled by OANDA (GTD / priceBound)
            if r["status"] == "placed" and o.sl_daylow:   # keep 'one tick beyond today's extreme' current
                level, sl, _, tp = entry_levels(o, pair, day_open, lo, hi, spr)
                if abs(sl - r.get("sl", sl)) >= pip(pair) * 0.5:
                    units = abs(int(float(pend_by_cid[r["cid"]]["units"])))
                    body = order_body(pair, o, units, level, sl, None, tp, day_bounds(td)[1] - timedelta(minutes=1), r["cid"] + "x", sid, spr)
                    cli.replace_order(pend_by_cid[r["cid"]]["id"], body); r["cid"] += "x"; r["sl"] = sl
                    self.log(f"[{sid}] {pair} move pre-fill stop to {fmt_px(pair, sl)}")
                continue
            if r["status"] != "armed": continue
            if getattr(self, "_margin_full", False):
                self.log(f"[{sid}] {pair} margin used above limit; defer"); continue
            if spr_now is None or not prices[pair][2] or spr_now > MAX_SPREAD_MULT * spr:
                self.log(f"[{sid}] {pair} spread {spr_now} too wide; defer"); continue
            if o.kind == "market":
                level = (prices[pair][1] if o.side == 1 else prices[pair][0])
                sl = o.sl if o.sl is not None else level - o.side * o.sl_dist
                tp = o.tp if o.tp is not None else (level + o.side * o.tp_dist if o.tp_dist else None)
                sl_dist = o.sl_dist if o.sl is None else None
            else:
                level, sl, sl_dist, tp = entry_levels(o, pair, day_open, lo, hi, spr)
            if o.kind == "limit" and (level - (prices[pair][1] if o.side == 1 else prices[pair][0])) * o.side > 0:
                self.log(f"[{sid}] {pair} limit {fmt_px(pair, level)} already through the market; skip"); r["status"] = "missed"; continue
            if (level - sl) * o.side <= 0: r["status"] = "missed"; continue
            units = units_for_risk(pair, level, sl, nav, self.cfg["risk_pct"], mids)
            if units < 1: r["status"] = "missed"; continue
            exp_day = date.fromisoformat(r["exp"])
            gtd = day_bounds(exp_day)[1] - timedelta(minutes=1)
            if r.get("gtd"): gtd = min(gtd, datetime.fromisoformat(r["gtd"]))
            body = order_body(pair, o, units, level, sl, sl_dist, tp, gtd, r["cid"], sid, spr)
            res = cli.create_order(body); r["status"] = "placed"; r["sl"] = sl
            if o.kind == "market": r["status"] = "sent"
            self.log(f"[{sid}] {pair} {'BUY' if o.side == 1 else 'SELL'} {o.kind} {units}u @ {fmt_px(pair, level)} SL {fmt_px(pair, sl)} TP {fmt_px(pair, tp) if tp else '-'}")

# ---------------------------------------------------------------- entry point
def load_env(path):
    env = {}
    if path and os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1); env[k.replace("export ", "").strip()] = v.strip().strip('"').strip("'")
    return env

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/config.json"); ap.add_argument("--env", default=".env")
    ap.add_argument("--state", default="state/live_state.json"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--now", help="testing only (requires --dry-run): pretend it is this ISO time")
    a = ap.parse_args()
    if a.now and not a.dry_run: sys.exit("--now requires --dry-run")
    cfg = json.load(open(a.config)); env = {**load_env(a.env), **{k: v for k, v in os.environ.items() if k.startswith(("OANDA_", "FX_"))}}
    oenv = env.get("OANDA_ENV", "practice")
    if oenv != "practice": sys.exit("refusing: OANDA_ENV must be 'practice'")
    clients = {}
    for sid in cfg["systems"]:
        acct = env.get(f"OANDA_ACCOUNT_{sid.upper().replace('-', '_')}") or env["OANDA_ACCOUNT_ID"]
        clients[sid] = Oanda(env["OANDA_TOKEN"], acct, oenv, dry_run=a.dry_run or cfg.get("dry_run", False))
    os.makedirs(os.path.dirname(a.state) or ".", exist_ok=True)
    stamp = datetime.now(NY).strftime("%Y-%m-%d %H:%M:%S %Z")
    now = datetime.fromisoformat(a.now).astimezone(timezone.utc) if a.now else None
    r = Runner(cfg, clients, a.state, now=now, log=lambda m: print(f"{stamp} {m}", flush=True)); r.run()
    if a.dry_run:
        for sid, c in clients.items():
            for w in c.writes: print(f"{stamp} [dry-run] {sid} would {w['method']} {w['path'].split('/v3/accounts/')[-1].split('/', 1)[-1]} {json.dumps(w['body'])[:300] if w['body'] else ''}", flush=True)

if __name__ == "__main__":
    main()
