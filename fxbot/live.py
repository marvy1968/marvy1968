"""Our own OANDA PRACTICE runner (forex only; nothing here touches the Alpaca stock bot). `python -m fxbot paper` (marv-fx-paper.service).

Strategies (the two with the best recorded evidence in the owner's old bots; neither has passed our own walk-forward bar, so this is a
practice-money forward test, monitored and tuned as it runs):
  lien_h1   Kathy Lien's Fader on H1 (fxbot.strategies.LienFader, as frozen in the owner's fx-experts bot)
  f0_h4     the owner's F0 mean reversion on H4 (fxbot.strategies.F0Reversion)

Owner's rules, the same as the stock bot:
  DIRECTION FIRST  the pair's daily bias (alpacabot.zonemap: up = close > EMA20 > SMA50 and ADX >= 20, down = mirror, else range):
                   up -> longs only, down -> shorts only, range -> both (both strategies are mean-reversion style)
  ENTRY ZONE       no chasing: skip when the price is more than 0.5 ATR past the signal close
  ENTRY            one entry risking 1% of this book (the owner's drawing, 2026-10-14); optional scale-in tiers in CONFIG["ladder"]
  SELL ZONE        one stop (the drawn 25/50% cut steps tested worse than one stop on 2016-22 forex history; owner chose one stop)
  TP ZONE          the nearer of the strategy's own target, the 20-bar mean (>= 0.75R, else 1.5R) and the nearest strong daily zone-map
                   level (>= 1R); sell 25% at the zone (stop to break-even at OANDA), 50% in all 0.25 ATR beyond (stop locked at the
                   zone); the rest rides the trend (OANDA take-profit 3 ATR beyond the zone)
  AT THE BROKER    every trade has its stop-loss AND take-profit (the 100% level) placed at OANDA on fill, so a runner crash or a
                   VM outage never leaves a trade unprotected
Safety: own book only (orders tagged marv-fx; a pair where another bot holds a trade is skipped), max 4 open trades groups, max 2 same-side
per currency, 2% daily loss breaker, kill switch file state/fx_paper/KILL_SWITCH (`python -m alpacabot paper-pause on --state state/fx_paper`), no entries around the 17:00 New York rollover, spread guard.
"""

import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data import _to_frame, mid, usd_per_unit
from .oanda import TAG, Oanda, OandaError
from .strategies import BoxConfirm, ConsensusMR, F0Reversion, HybridBox, InBox, LienFader, RangeBox200, RangeMR

log = logging.getLogger("fxbot.live")
NY = "America/New_York"
PAIRS = ["EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD", "USD_JPY", "USD_CAD", "USD_CHF"]
CONFIRM_VOTE = 1.0          # share of the 5 continuation indicators that must agree (1.0 = all; set from the owner's 3-month test)
STRATS = {"lien_h1": {"make": lambda: LienFader(break_pips=20), "gran": "H1", "count": 1500, "minutes": 60},   # break 20 pips (was 15): best of the 27-point
          # grid (2016-20 PF 1.46, 2021-22 PF 1.27); narrowly missed the 90% placebo bar (85%), so PROVISIONAL on paper money, 2026-10-15
          "f0_h4": {"make": F0Reversion, "gran": "H4", "count": 400, "minutes": 240},
          "box_h1": {"make": RangeBox200, "gran": "H1", "count": 600, "minutes": 60},
          # owner 2026-10-16: HYBRID zones (box + Fibonacci + swing levels agree), live on practice at half size without a backtest
          "hybrid_h1": {"make": HybridBox, "gran": "H1", "count": 600, "minutes": 60},
          # owner 2026-10-16 "fix the box": candles + volume + volatility confirm a break of the box -> trade the CONTINUING breakout
          # (2016-22: PF 1.54 on 120 trades before costs; the plain box fade lost, PF 0.68 on 1,308). Half size; vote from the 3-month test.
          "confirm_h1": {"make": lambda: BoxConfirm(mode="continuation", vol_mult=1.5, atr_ratio=1.1, vote=CONFIRM_VOTE), "gran": "H1",
                         "count": 600, "minutes": 60},
          # owner 2026-10-19 "place my experts inside the range box" (fxbot.strategies.InBox: ranging pairs only, longs in the lower half / shorts in
          # the upper half, the box's stop and take-profit lines). Check (last 12 months): range mean-reversion alone PF 0.48, -98.6% -> inside the
          # box PF 0.66 (118 trades, -4.5%); consensus inside the box PF 0.44 (harmful) and F0 3 trades: those two only LOG their signals.
          "boxmr_h1": {"make": lambda: InBox(RangeMR(bb_k=2.0, stop_atr=1.5, asian_only=False), tp_frac=0.5, label="range_mr"), "gran": "H1",
                       "count": 600, "minutes": 60},
          "boxcons_h1": {"make": lambda: InBox(ConsensusMR(votes=3), tp_frac=0.5, label="consensus"), "gran": "H1", "count": 600, "minutes": 60},
          "boxf0_h4": {"make": lambda: InBox(F0Reversion(n=100), tp_frac=0.5, label="f0"), "gran": "H4", "count": 400, "minutes": 240}}     # owner's range box (ranging pairs); levels = the
    # 100-bar high/low. The SMA200 +-2 sigma envelope was tested against it 2016-20 (cc6, 2026-10-15): PF 0.52 vs 0.60, so high/low stays
CONFIG = {"ladder": {"entry_risk": [0.01], "entry_depth_atr": [0.0],              # owner's drawing: one entry risking 1% of the book
                     "cut_fracs": [], "cut_depth_r": [],                              # one stop (owner 2026-10-14: the 25/50% cut steps lost
                                                                                      # more than one stop on all three strategies, 2016-22)
                     "tp_fracs": [0.25, 0.5], "tp_depth_atr": [0.0, 0.25],          # TP zone: 25% at the zone, 50% a little beyond
                     "lock": ["breakeven", "tp"], "ride_tp_atr": 3.0,               # stop to BE, then to the zone; the rest rides,
                     "trail_atr": 1.0, "trail_step_atr": 0.25},                     # followed by a stop 1 ATR behind the best price
          "zone": {"chase_atr": 0.5, "lookback": 20, "min_r": 0.75, "default_r": 1.5, "map_min_r": 1.0},
          "max_groups": 4, "max_per_ccy_side": 2, "breaker": 0.02, "max_notional_x": 10.0, "max_spread_frac": 0.25,
          "book_cap": 100_000.0, "strategies": list(STRATS),
          # owner 2026-10-14: Lien Fader and the owner's range box place practice trades (the owner wants to watch the box on paper money
          # although its 2016-20 backtest lost -75%); F0 (-13..-18%) only logs its signals as "paused (signal logged)"
          # box_h1 (the plain fade, 0 for 6 live, PF 0.68 in 2016-22) now only LOGS its signals; confirm_h1 replaces it (owner, 2026-10-16)
          "trade": ["lien_h1", "hybrid_h1", "confirm_h1", "boxmr_h1"],
          "risk_by_strategy": {"hybrid_h1": 0.005, "box_h1": 0.005, "confirm_h1": 0.005, "boxmr_h1": 0.005},
          # owner 2026-10-19: "if a trade turns negative, close it; don't wait for a reversal past 10%": every open trade is closed at market as soon
          # as the MID price is `loss_cut_r` x its risk (entry to stop distance) against its entry mid. 0.10 = 10% of the way to the stop. None = off.
          # The OANDA stop stays in place as the backstop; measured on the mid so the spread paid at entry does not trigger it.
          # 2026-10-19 scalp_fx backtest (4 live strategies, last 12 months, hourly bid/ask, pessimistic): cut 0.10R / out +0.30R -28.5% in total,
          # 0.25R / 0.75R -24.2%, 0.50R / 1.50R -9.5% (old ladder -14.8%); last 3 months -5.3% / -4.1% / -2.1%. The owner said "deploy the best":
          # 0.50 / 1.50. At 0.10R normal price noise stops a trade out almost every time (Lien Fader won 1 of 15, range box 318 trades at 13%).
          "loss_cut_r": 0.50,
          # owner 2026-10-19 ("leave at 10, tp and closeout at 30%"): the WHOLE position is taken off at +`take_profit_r` x its risk. The OANDA
          # take-profit is placed there (it survives a VM outage) and the runner also closes at market when the mid is that far in profit.
          # owner 2026-10-19: both bots 0.5R stop / 1.25R target. It replaces the scale-out steps and the rider. 0.10 against / 0.30 for = a 3 to 1 reward-to-risk. None = off (the old ladder).
          "take_profit_r": 1.25}                                # the hybrid trades at half the normal 1% (owner)


def decimals(instrument: str) -> int:
    return 3 if "JPY" in instrument else 5


def fmt(instrument: str, px: float) -> str:
    return f"{px:.{decimals(instrument)}f}"


def market_open(now: datetime) -> bool:
    """Forex trades Sunday 17:00 to Friday 17:00 New York time."""
    t = now.astimezone(ZoneInfo(NY))
    wd, hm = t.weekday(), t.hour * 60 + t.minute
    if wd == 5 or (wd == 4 and hm >= 17 * 60) or (wd == 6 and hm < 17 * 60):
        return False
    return True


def rollover(now: datetime) -> bool:
    """16:50-17:15 New York: daily rollover, spreads blow out; no new entries or adds."""
    t = now.astimezone(ZoneInfo(NY))
    return 16 * 60 + 50 <= t.hour * 60 + t.minute <= 17 * 60 + 15


def trading_day(now: datetime) -> str:
    t = pd.Timestamp(now).tz_convert(NY) - pd.Timedelta(hours=17)
    return t.date().isoformat()


class FxPaper:
    def __init__(self, api=None, state_dir="state/fx_paper", config=None, clock=None, sleep=time.sleep):
        self.api = api or Oanda()
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg = {**CONFIG, **(config or {})}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        st = self._load("groups.json", {})
        self.groups, self.last_bar, self.day = st.get("groups", {}), st.get("last_bar", {}), st.get("day", {})
        self.daily_cache, self.next_check = {}, {}

    # ---------------- persistence
    def _load(self, name, default):
        p = self.dir / name
        try:
            return json.loads(p.read_text()) if p.exists() else default
        except Exception:
            log.exception("%s unreadable", name)
            return default

    def save(self):
        tmp = self.dir / "groups.json.tmp"
        tmp.write_text(json.dumps({"groups": self.groups, "last_bar": self.last_bar, "day": self.day}, indent=1, default=str))
        tmp.replace(self.dir / "groups.json")

    def journal(self, kind: str, **kw):
        now = self.clock()
        with open(self.dir / f"journal-{now:%Y%m%d}.jsonl", "a") as f:
            f.write(json.dumps({"t": now.isoformat(timespec="seconds"), "kind": kind, **kw}, default=str) + "\n")
        log.info("%s %s", kind, kw)

    def ledger(self) -> list:
        p = self.dir / "trades.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def record(self, g: dict, trade_id: str, units: int, px: float, pl: float, reason: str, partial: bool):
        with open(self.dir / "trades.jsonl", "a") as f:
            f.write(json.dumps({"date": trading_day(self.clock()), "closed": self.clock().isoformat(timespec="seconds"), "group": g["id"],
                                "strategy": g["strategy"], "instrument": g["instrument"], "side": "long" if g["side"] == 1 else "short",
                                "trade_id": trade_id, "units": abs(int(units)), "entry": g["avg"], "exit": px, "pl": round(pl, 2),
                                "reason": reason, "partial": partial, "opened": g["opened"], "tier": g["tier"]}) + "\n")

    # ---------------- book
    def base(self) -> float:
        b = self._load("base.json", None)
        if b is None:
            nav = float(self.api.summary()["NAV"])
            b = {"equity": min(nav, self.cfg["book_cap"]), "set": self.clock().isoformat()}
            (self.dir / "base.json").write_text(json.dumps(b))
        return float(b["equity"])

    def equity(self, open_trades=None) -> float:
        ours = [t for t in (open_trades if open_trades is not None else self.api.open_trades()) if self.is_ours(t)]
        return self.base() + sum(t["pl"] for t in self.ledger()) + sum(float(t.get("unrealizedPL", 0) or 0) for t in ours)

    @staticmethod
    def is_ours(t: dict) -> bool:
        return (t.get("clientExtensions") or {}).get("tag") == TAG

    def entries_allowed(self, eq: float) -> str:
        now = self.clock()
        if (self.dir / "KILL").exists() or (self.dir / "KILL_SWITCH").exists():
            return "kill switch"
        if not market_open(now) or rollover(now):
            return "market closed / rollover"
        d = trading_day(now)
        if self.day.get("date") != d:
            self.day = {"date": d, "start": eq}
        if eq <= self.day["start"] * (1 - self.cfg["breaker"]):
            return f"daily breaker ({eq / self.day['start'] - 1:+.2%})"
        return ""

    # ---------------- market data
    def bars(self, instrument: str, gran: str, count: int) -> pd.DataFrame:
        return _to_frame(self.api.candles(instrument, gran, count))       # completed candles only

    def daily_bias(self, instrument: str):
        """(bias, levels) from completed daily bars, refreshed once per trading day."""
        from alpacabot.zonemap import BIAS_RULES, bias, level_map
        key = (instrument, trading_day(self.clock()))
        if key not in self.daily_cache:
            d = mid(self.bars(instrument, "D", 250))
            b = int(bias(d).iloc[-1]) if len(d) >= BIAS_RULES["sma"] + 15 else 0
            self.daily_cache[key] = (b, level_map(d))
        return self.daily_cache[key]

    # ---------------- the loop
    def step(self):
        now = self.clock()
        trades = self.api.open_trades()
        self.sync(trades)
        if not market_open(now):
            self.status(trades)
            return
        eq = self.equity(trades)
        why = self.entries_allowed(eq)
        for gid in list(self.groups):
            self.manage(gid, eq, why)
        for name in self.cfg["strategies"]:
            spec = STRATS[name]
            for inst in PAIRS:
                try:
                    self.scan(name, spec, inst, eq, why, trades)
                except OandaError as exc:
                    self.journal("error", where="scan", strategy=name, instrument=inst, error=str(exc)[:300])
        self.save()
        self.status(self.api.open_trades())

    def sync(self, trades: list):
        """Trades OANDA closed (stop-loss / take-profit / margin / netting) leave their group; their realized P&L goes to the ledger."""
        open_ids = {t["id"] for t in trades}
        for gid, g in list(self.groups.items()):
            for tid in [t for t in g["trades"] if t not in open_ids]:
                try:
                    t = self.api.trade(tid)
                    pl = float(t.get("realizedPL", 0) or 0) + float(t.get("financing", 0) or 0)
                    px = float(t.get("averageClosePrice", 0) or 0)
                    units = g["units"].get(tid, 0)
                except OandaError as exc:
                    self.journal("error", where="sync", trade=tid, error=str(exc)[:300])
                    continue
                near = lambda a, b: abs(a - b) <= 0.15 * g["atr"]                            # noqa: E731
                why = ("broker_tp" if px and (g["side"] * (px - g["tp_px"]) >= -1e-9 or near(px, g["tp_px"])) else
                       "broker_sl" if px and near(px, g["stop_px"]) else "closed_outside_runner" if px else "broker")   # (e.g. closed by hand)
                self.record(g, tid, units, px, pl, why, partial=False)
                g["trades"].remove(tid)
                g["units"].pop(tid, None)
                self.journal("closed_at_broker", group=gid, trade=tid, reason=why, pl=round(pl, 2), px=px)
            if not g["trades"]:
                self.groups.pop(gid)
                self.journal("group_closed", group=gid)
        self.save()

    def scan(self, name: str, spec: dict, inst: str, eq: float, why: str, trades: list):
        key, now = f"{name}:{inst}", self.clock()
        if now < self.next_check.get(key, now):          # the next bar can't have completed yet: no download
            return
        bars = self.bars(inst, spec["gran"], spec["count"])
        if len(bars) < 120:
            return
        last = bars.index[-1].isoformat()
        if self.last_bar.get(key) == last:
            self.next_check[key] = now + timedelta(minutes=2)
            return
        self.last_bar[key] = last
        self.next_check[key] = bars.index[-1].to_pydatetime() + timedelta(minutes=2 * spec["minutes"], seconds=5)
        m = mid(bars)
        strat = spec["make"]()
        f = strat.frame(m)
        # exits on the bar close for our open group of this strategy/pair: the strategy's own exit and its time stop
        for gid, g in list(self.groups.items()):
            if g["strategy"] == name and g["instrument"] == inst:
                held = int((m.index > pd.Timestamp(g["opened"])).sum())
                ex = bool(f["exit_long"].iloc[-1] if g["side"] == 1 else f["exit_short"].iloc[-1])
                if ex or held >= strat.max_bars:
                    self.close_group(gid, "signal" if ex else "time")
        row = f.iloc[-1]
        side = 1 if row["long"] else -1 if row["short"] else 0
        if not side:
            return
        sd = float(row["stop"])
        if name not in self.cfg.get("trade", list(STRATS)):
            self.journal("signal", strategy=name, instrument=inst, side="long" if side == 1 else "short", bar=last,
                         close=float(m["close"].iloc[-1]), stop_dist=sd, result="paused (signal logged)")
            return
        res = self.try_open(name, inst, side, sd, float(row.get("target", float("nan"))), m, eq, why, trades, last,
                            zone_px=float(row.get("zone_px", float("nan"))), zone2_px=float(row.get("zone2_px", float("nan"))))
        self.journal("signal", strategy=name, instrument=inst, side="long" if side == 1 else "short", bar=last, result=res)

    def try_open(self, name, inst, side, sd, own_tgt, m, eq, why, trades, bar, zone_px: float = float("nan"),
                 zone2_px: float = float("nan")) -> str:
        if why:
            return why
        if not (sd > 0 and math.isfinite(sd)):
            return "no stop"
        if any(g["instrument"] == inst for g in self.groups.values()):
            return "already in this pair"
        if any(t["instrument"] == inst and not self.is_ours(t) for t in trades):
            return "another bot holds this pair"
        if len(self.groups) >= self.cfg["max_groups"]:
            return "max open trades"
        base_c, quote_c = inst.split("_")
        load = {}
        for g in self.groups.values():
            b, q = g["instrument"].split("_")
            load[(b, g["side"])] = load.get((b, g["side"]), 0) + 1
            load[(q, -g["side"])] = load.get((q, -g["side"]), 0) + 1
        if load.get((base_c, side), 0) >= self.cfg["max_per_ccy_side"] or load.get((quote_c, -side), 0) >= self.cfg["max_per_ccy_side"]:
            return "currency limit"
        bias, levels = self.daily_bias(inst)
        if (bias == 1 and side == -1) or (bias == -1 and side == 1):
            return f"against direction (bias {bias:+d})"
        from alpacabot.indicators import atr as atr_fn
        a = float(atr_fn(m, 14).iloc[-1])
        close = float(m["close"].iloc[-1])
        bid, ask = self.api.price(inst)
        px = ask if side == 1 else bid
        if ask - bid > self.cfg["max_spread_frac"] * sd:
            return f"spread {ask - bid:.5f} too wide"
        z = self.cfg["zone"]
        if side * (px - close) > z["chase_atr"] * a:
            return "chase: price past the entry zone"
        depth = side * (close - px) / a if a > 0 else 0.0
        lad = self.cfg["ladder"]
        tier = max(k for k, d in enumerate(lad["entry_depth_atr"]) if depth >= d or k == 0)
        stop_px = px - side * sd
        # take-profit zone: nearest of own target, 20-bar mean (>= min_r, else default_r) and a strong daily map level (>= map_min_r)
        mean = float(m["close"].iloc[-z["lookback"]:].mean())
        zt = mean if side * (mean - px) >= z["min_r"] * sd else px + side * z["default_r"] * sd
        cands = [zt] + ([px + side * own_tgt] if math.isfinite(own_tgt) and own_tgt > 0 else [])
        from alpacabot.zonemap import zone_target
        mt = zone_target(levels, px, side, sd, z["map_min_r"])
        if mt is not None:
            cands.append(mt)
        zone_tp = min(cands) if side == 1 else max(cands)
        if math.isfinite(zone_px) and side * (zone_px - px) > 0:         # a strategy with its own drawn take-profit line (range box)
            zone_tp = zone_px
        tp_px = zone_tp + side * lad.get("ride_tp_atr", lad["tp_depth_atr"][-1]) * a   # OANDA take-profit for what rides the trend
        if math.isfinite(zone2_px) and side * (zone2_px + side * a - tp_px) > 0:          # never let OANDA's own TP close before the hybrid's TP2
            tp_px = zone2_px + side * a
        if self.cfg.get("take_profit_r"):                                                  # owner: the whole position out at +take_profit_r x the risk
            tp_px = px + side * float(self.cfg["take_profit_r"]) * sd
        risk_pct = float(self.cfg.get("risk_by_strategy", {}).get(name, lad["entry_risk"][tier]))
        units = self.size(inst, eq, risk_pct, 0.0, px, stop_px)
        if units < 1:
            return "size 0"
        gid = f"{name}-{inst}-{bar[:16].replace(':', '')}"
        got = self.api.market(inst, side * units, fmt(inst, stop_px), fmt(inst, tp_px), f"mx-{gid}-0"[:60])
        if "rejected" in got:
            return f"rejected: {got['rejected']}"
        fill = got["price"]
        self.groups[gid] = {"id": gid, "strategy": name, "instrument": inst, "side": side, "opened": bar, "signal_close": close,
                            "atr": a, "stop_px": stop_px, "stop0": stop_px, "fill": fill, "mid0": (bid + ask) / 2, "tp_steps": 0, "zone_tp": zone_tp, "tp_px": tp_px, "tier": tier, "avg": fill,
                            "trades": [got["trade_id"]], "units": {got["trade_id"]: abs(got["units"])}, "peak_units": abs(got["units"]),
                            "sold": 0, "be": False, "bias": bias}
        if math.isfinite(zone2_px) and side * (zone2_px - zone_tp) > 0 and a > 0:     # hybrid: the second take-profit step at its own line
            self.groups[gid]["tp_depth"] = [0.0, side * (zone2_px - zone_tp) / a]
        self.journal("open", group=gid, side=side, units=got["units"], fill=fill, stop=fmt(inst, stop_px), zone_tp=fmt(inst, zone_tp),
                     tp=fmt(inst, tp_px), tier=tier, risk_pct=risk_pct, bias=bias)
        self.save()
        return "opened"

    def size(self, inst: str, eq: float, risk_pct: float, risk_now: float, px: float, stop_px: float) -> int:
        """Units so the group's total risk to the stop reaches risk_pct of this book; notional capped at max_notional_x x equity."""
        per_unit = abs(usd_per_unit(inst, abs(px - stop_px), px))
        if per_unit <= 0:
            return 0
        units = int((eq * risk_pct - risk_now) / per_unit)
        notional_per_unit = 1.0 if inst.startswith("USD") else px
        return max(0, min(units, int(eq * self.cfg["max_notional_x"] / notional_per_unit)))

    def manage(self, gid: str, eq: float, why: str):
        g = self.groups[gid]
        inst, side = g["instrument"], g["side"]
        try:
            bid, ask = self.api.price(inst)
        except OandaError as exc:
            self.journal("error", where="price", instrument=inst, error=str(exc)[:300])
            return
        lad = self.cfg["ladder"]
        cut = self.cfg.get("loss_cut_r")
        if cut:                                  # owner: a trade that turns against us is closed at once (see CONFIG["loss_cut_r"])
            risk0 = abs(g.get("fill", g["avg"]) - g.get("stop0", g["stop_px"]))
            mid_now, mid0 = (bid + ask) / 2, g.get("mid0", g.get("fill", g["avg"]) - side * (ask - bid) / 2)
            if risk0 > 0 and side * (mid0 - mid_now) >= cut * risk0:
                self.journal("loss_cut", group=gid, against_r=round(side * (mid0 - mid_now) / risk0, 3), mid=round(mid_now, 6), entry_mid=round(mid0, 6))
                self.close_group(gid, "loss_cut")
                return
        tpr = self.cfg.get("take_profit_r")
        if tpr:                                  # owner: take the profit and close the whole position out at +take_profit_r x the risk
            risk0 = abs(g.get("fill", g["avg"]) - g.get("stop0", g["stop_px"]))
            mid_now, mid0 = (bid + ask) / 2, g.get("mid0", g.get("fill", g["avg"]) - side * (ask - bid) / 2)
            if risk0 > 0 and side * (mid_now - mid0) >= tpr * risk0:
                self.journal("take_profit_r", group=gid, profit_r=round(side * (mid_now - mid0) / risk0, 3), mid=round(mid_now, 6), entry_mid=round(mid0, 6))
                self.close_group(gid, "take_profit_r")
                return
        # scale in: one more tier when price is that much deeper into the entry zone and still short of the stop
        k = g["tier"] + 1
        px_in = ask if side == 1 else bid
        depth = side * (g["signal_close"] - px_in) / g["atr"] if g["atr"] > 0 else 0.0
        if not why and not g["sold"] and k < len(lad["entry_risk"]) and depth >= lad["entry_depth_atr"][k] and side * (px_in - g["stop_px"]) > 0:
            units_now = sum(g["units"].values())
            risk_now = abs(usd_per_unit(inst, abs(g["avg"] - g["stop_px"]), g["avg"])) * units_now
            add = self.size(inst, eq, lad["entry_risk"][k], risk_now, px_in, g["stop_px"])
            g["tier"] = k
            if add >= 1:
                got = self.api.market(inst, side * add, fmt(inst, g["stop_px"]), fmt(inst, g["tp_px"]), f"mx-{gid}-{k}"[:60])
                if "rejected" in got:
                    self.journal("ladder_add_rejected", group=gid, tier=k, reason=got["rejected"])
                else:
                    u = abs(got["units"])
                    g["avg"] = (g["avg"] * units_now + got["price"] * u) / (units_now + u)
                    g["trades"].append(got["trade_id"])
                    g["units"][got["trade_id"]] = u
                    g["peak_units"] = units_now + u
                    self.journal("ladder_add", group=gid, tier=k, units=u, fill=got["price"], avg=round(g["avg"], 6),
                                 risk_pct=lad["entry_risk"][k])
            self.save()
        # against the trade, inside the sell zone: cut in steps before the stop (shared, cumulative fractions of the peak position)
        px_out = bid if side == 1 else ask
        risk = abs(g.get("fill", g["avg"]) - g.get("stop0", g["stop_px"]))
        against = side * (g.get("fill", g["avg"]) - px_out) / risk if risk > 0 else 0.0
        cuts = [k for k, d in enumerate(lad.get("cut_depth_r", [])) if against >= d]
        if cuts and self.scale_to(gid, lad["cut_fracs"][max(cuts)], px_out, f"cut{int(lad['cut_fracs'][max(cuts)] * 100)}"):
            return
        # what rides the trend: trail the OANDA stop trail_atr behind the best price (moved in trail_step_atr steps, never loosened)
        g["best"] = max(g.get("best", px_out), px_out) if side == 1 else min(g.get("best", px_out), px_out)
        lvl = None
        if lad.get("trail_atr") and g.get("tp_steps", 0) >= len(lad["tp_fracs"]):
            lvl = g["best"] - side * lad["trail_atr"] * g["atr"]
        if lad.get("trail_from_entry_r") and side * (g["best"] - g.get("fill", g["avg"])) > 0:    # optional: trail from the first profit
            t2 = g["best"] - side * lad["trail_from_entry_r"] * abs(g.get("fill", g["avg"]) - g.get("stop0", g["stop_px"]))
            lvl = t2 if lvl is None else (max(lvl, t2) if side == 1 else min(lvl, t2))
        if lvl is not None:
            if side * (lvl - g["stop_px"]) >= lad.get("trail_step_atr", 0.25) * g["atr"]:
                for tid in g["trades"]:
                    try:
                        self.api.set_stop(tid, fmt(inst, lvl))
                    except OandaError as exc:
                        self.journal("error", where="trail", trade=tid, error=str(exc)[:300])
                g["stop_px"] = lvl
                self.journal("stop_trailed", group=gid, stop=fmt(inst, lvl))
        # take-profit zone: scale out, lock the stop at OANDA after each step, let the rest ride
        beyond = side * (px_out - g["zone_tp"]) / g["atr"] if g["atr"] > 0 else 99.0
        steps = [k for k, d in enumerate(g.get("tp_depth") or lad["tp_depth_atr"]) if beyond >= d]
        if not steps:
            self.save()
            return
        k = max(steps)
        if self.scale_to(gid, lad["tp_fracs"][k], px_out, f"zone{int(lad['tp_fracs'][k] * 100)}"):
            return
        locks = lad.get("lock") or []
        for step in range(g.get("tp_steps", 0), k + 1):
            how = locks[step] if step < len(locks) else None
            level = g["avg"] if how == "breakeven" else g["zone_tp"] if how == "tp" else None
            if level is not None and side * (level - g["stop_px"]) > 0:
                for tid in g["trades"]:
                    try:
                        self.api.set_stop(tid, fmt(inst, level))
                    except OandaError as exc:
                        self.journal("error", where="set_stop", trade=tid, error=str(exc)[:300])
                g["stop_px"], g["be"] = level, True
                self.journal("stop_locked", group=gid, how=how, stop=fmt(inst, level))
        g["tp_steps"] = k + 1
        self.save()

    def scale_to(self, gid: str, frac: float, px: float, reason: str) -> bool:
        """Sell down to (1 - frac) of the peak position. True when the group is closed."""
        g = self.groups[gid]
        sell = int(round(g["peak_units"] * frac)) - g["sold"]
        if sell < 1:
            return False
        if frac >= 1.0 or sell >= sum(g["units"].values()):
            self.close_group(gid, reason)
            return True
        self.close_units(gid, sell, px, reason)
        return False

    def close_units(self, gid: str, units: int, px: float, reason: str):
        """Close `units` of the group, oldest trade first (FIFO-friendly)."""
        g = self.groups[gid]
        left = units
        for tid in list(g["trades"]):
            if left <= 0:
                break
            have = g["units"][tid]
            n = min(have, left)
            try:
                got = self.api.close(tid, None if n == have else n)
            except OandaError as exc:
                self.journal("error", where="close", trade=tid, error=str(exc)[:300])
                break
            self.record(g, tid, n, got["price"] or px, got["pl"], reason, partial=True)
            g["sold"] += n
            left -= n
            if n == have:
                g["trades"].remove(tid)
                g["units"].pop(tid)
            else:
                g["units"][tid] = have - n
        self.journal("scale_out", group=gid, units=units - left, reason=reason)

    def close_group(self, gid: str, reason: str):
        g = self.groups[gid]
        for tid in list(g["trades"]):
            try:
                got = self.api.close(tid)
            except OandaError as exc:
                self.journal("error", where="close", trade=tid, error=str(exc)[:300])
                continue
            self.record(g, tid, g["units"][tid], got["price"], got["pl"], reason, partial=False)
            g["trades"].remove(tid)
            g["units"].pop(tid)
        if not g["trades"]:
            self.groups.pop(gid)
        self.journal("group_closed", group=gid, reason=reason)
        self.save()

    def status(self, trades: list):
        led = self.ledger()
        today = [t for t in led if t["date"] == trading_day(self.clock())]
        ours = [t for t in trades if self.is_ours(t)]
        lines = [f"MARV FX PRACTICE RUNNER {self.clock():%Y-%m-%d %H:%M} UTC  (OANDA practice; forex only)",
                 f"book equity ${self.equity(trades):,.2f} (start ${self.base():,.2f})  realized ${sum(t['pl'] for t in led):+,.2f}"
                 f"  today ${sum(t['pl'] for t in today):+,.2f} ({len(today)} fills)",
                 f"market {'OPEN' if market_open(self.clock()) else 'CLOSED'}  kill switch {'ON' if (self.dir / 'KILL').exists() or (self.dir / 'KILL_SWITCH').exists() else 'off'}",
                 "last completed bar scanned: " + ", ".join(
                     f"{n} {sum(1 for k in self.last_bar if k.startswith(n + ':'))}/{len(PAIRS)} pairs @ "
                     f"{max((v for k, v in self.last_bar.items() if k.startswith(n + ':')), default='-')[:16]}" for n in self.cfg["strategies"]),
                 f"open groups {len(self.groups)}, OANDA trades {len(ours)}:"]
        for g in self.groups.values():
            upl = sum(float(t.get("unrealizedPL", 0) or 0) for t in ours if t["id"] in g["trades"])
            lines.append(f"  {g['id']}: {'long' if g['side'] == 1 else 'short'} {sum(g['units'].values())}u avg {fmt(g['instrument'], g['avg'])} "
                         f"stop {fmt(g['instrument'], g['stop_px'])} zone {fmt(g['instrument'], g['zone_tp'])} tp {fmt(g['instrument'], g['tp_px'])} "
                         f"tier {g['tier']} sold {g['sold']}/{g['peak_units']} uPL ${upl:+,.2f}")
        (self.dir / "status.txt").write_text("\n".join(lines) + "\n")


def run_forever(poll: float = 30.0):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bot = FxPaper()
    log.info("fx practice runner up (OANDA practice host only)")
    while True:
        try:
            bot.step()
        except Exception as exc:
            log.exception("step failed")
            try:
                bot.journal("error", where="step", error=f"{type(exc).__name__}: {str(exc)[:200]}")   # (the reason, not just that it failed)
            except Exception:
                pass
        time.sleep(poll)
