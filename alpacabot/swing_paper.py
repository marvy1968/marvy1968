"""Daily swing runner (PAPER only) for the Sector Top-Down rules: the same strategies.SectorTopDown code the backtest uses.

  scan  (weekdays 16:20 ET, after the close): settle stops that filled today, then plan for the NEXT session:
          exits    every held position whose daily close fell below EMA20
          entries  today's signals, ranked by relative strength, into the free slots (3 max), sized like engine.size_swing:
                   qty = min(equity x 0.35% / (est. risk per share + slippage), equity x 33.3% / price, free cash / price)
  open  (weekdays 09:20 ET, before the open): market orders for the plan sent seconds after the 09:30 open (the backtest fills at the open);
          after the fills, a broker-side GTC stop at the signal-bar stop price (EMA20 - 1 ATR) for each new position. The stop lives at
          Alpaca, so an overnight position is protected even if this VM is down. A fill at or below its stop (a gap down) is sold at once;
          the backtest skips such a trade.

Everything this runner sends carries the client order id prefix "sw-"; its state, plan and ledger live in state/alpaca_swing/. It never
touches orders or positions it did not open (the intraday bot's "ab-" ones, or anything else on the account).
"""

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data import NY, _fetch
from .strategies import SectorTopDown
from .universe import STOCKS

ET = ZoneInfo(NY)
UNIVERSE = list(STOCKS)          # the 5 largest S&P 500 stocks in each of the 11 sectors (universe.py)
RISK_PCT, MAX_POS, MAX_POS_FRAC, SLIP_BPS = 0.0035, 3, 0.333, 5.0
FUNNEL_RISK, FUNNEL_MAX = 0.005, 3     # owner's Stock Funnel (alpacabot.funnel): 0.5% risk, its own 3 slots, longs and shorts
WEEKLY_LIMIT = 0.05     # swing book 5% below its peak of the last 5 scans -> no new entries (exits and stops still run)
log = logging.getLogger("alpacabot.swing")


def daily_bars(symbol: str, now: datetime, fetch=_fetch, days: int = 260) -> pd.DataFrame:
    """Completed daily bars up to today's close (SIP; the free plan allows it once the data is 15+ minutes old)."""
    end = (now.astimezone(timezone.utc) - timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (now - timedelta(days=days * 1.5)).strftime("%Y-%m-%dT00:00:00Z")
    try:
        return fetch(symbol, "1Day", start, end, "sip")
    except Exception:
        return fetch(symbol, "1Day", start, end, "iex")


class SwingRunner:
    def __init__(self, broker, state_dir="state/alpaca_swing", universe=None, fetch=_fetch, sleep=time.sleep, base_equity: float | None = None):
        self.b, self.universe, self.fetch, self.sleep = broker, universe or UNIVERSE, fetch, sleep
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.state = self._load()
        if "base_equity" not in self.state:
            self.state["base_equity"] = float(base_equity if base_equity is not None else broker.equity())
            self._save()

    # ---------------------------------------------------------------- persistence
    def _load(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"positions": {}, "plan": None}

    def _save(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, default=str))
        os.replace(tmp, self.state_path)

    def journal(self, kind: str, when: datetime, **kw):
        with open(self.dir / "journal.jsonl", "a") as f:
            f.write(json.dumps({"t": when.isoformat(timespec="seconds"), "kind": kind, **kw}, default=str) + "\n")

    def record(self, sym: str, pos: dict, px: float, reason: str, when: datetime):
        side = pos.get("side", 1)
        pnl = side * (px - pos["entry"]) * pos["qty"]
        risk = side * (pos["entry"] - pos["stop"]) * pos["qty"]
        with open(self.dir / "trades.jsonl", "a") as f:
            f.write(json.dumps({"date": when.date().isoformat(), "symbol": sym, "side": "long" if side == 1 else "short", "qty": pos["qty"],
                                "entry": pos["entry"], "exit": px, "pnl": round(pnl, 2), "r": round(pnl / risk, 3) if risk > 0 else 0.0,
                                "reason": reason, "opened": pos["entry_date"], "strategy": pos.get("strategy", "topdown")}) + "\n")
        self.journal("exit", when, symbol=sym, px=px, reason=reason, pnl=round(pnl, 2))

    def realized(self) -> float:
        p = self.dir / "trades.jsonl"
        return sum(json.loads(x)["pnl"] for x in p.read_text().splitlines() if x.strip()) if p.exists() else 0.0

    # ---------------------------------------------------------------- after the close
    def zone_config(self) -> dict:
        """<state>/zones.json (written with defaults on first use; edited by the 2-hour optimizer). LIVE by the owner's decision."""
        from .engine import ZONE_PROFILES
        p = Path(self.dir) / "zones_override.json"
        default = {"live": True, "use_map": True, "profiles": ZONE_PROFILES}
        try:
            repo = Path(__file__).with_name("zones.json")              # source of truth, edited by the optimizer and deployed
            cfg = {**default, **(json.loads(repo.read_text()).get("swing", {}) if repo.exists() else {})}
            if p.exists():                                             # a local override in the state folder wins (manual switch-off)
                cfg.update(json.loads(p.read_text()))
            else:
                p.write_text(json.dumps({"_note": "local override (e.g. {\"live\": false} to switch zones off here); otherwise alpacabot/zones.json rules"}, indent=1))
            cfg["profiles"] = {k: {**ZONE_PROFILES[k], **cfg["profiles"].get(k, {})} for k in ZONE_PROFILES}
            return cfg
        except Exception:
            log.exception("swing zones.json unreadable: zones off until fixed")
            return {**default, "live": False}

    def settle_stops(self, now: datetime):
        """Positions whose broker stop filled are closed in our books at the broker's fill price."""
        for sym, pos in list(self.state["positions"].items()):
            oid = pos.get("stop_order_id")
            if not oid:
                continue
            o = self.b.order(oid)
            if o.get("status") == "filled":                       # plain stop, or the OCO's take-profit (zone) leg
                why = "zone" if pos.get("oco") else "stop"
                self.record(sym, pos, float(o.get("filled_avg_price") or (pos.get("zone_tp") if pos.get("oco") else pos["stop"])), why, now)
                del self.state["positions"][sym]
                continue
            leg = next((x for x in (o.get("legs") or []) if x.get("status") == "filled"), None)
            if pos.get("oco") and leg:                            # the OCO's stop leg filled
                self.record(sym, pos, float(leg.get("filled_avg_price") or pos["stop"]), "stop", now)
                del self.state["positions"][sym]
        self._save()

    def scan(self, now: datetime) -> dict:
        today = now.astimezone(ET).date()
        self.settle_stops(now)
        spy = daily_bars("SPY", now, self.fetch)
        if not len(spy) or spy.index[-1].date() != today:
            self.journal("scan_skipped", now, reason="no completed daily bar for today (holiday or data not ready)")
            return {}
        strat = SectorTopDown(spy["close"])
        frames, closes, bars_ = {}, {}, {}
        for sym in self.universe:
            d = daily_bars(sym, now, self.fetch)
            if len(d) and d.index[-1].date() == today:
                frames[sym], closes[sym] = strat.frame(d), float(d["close"].iloc[-1])
                bars_[sym] = d
        idx_bars = self.index_bars(now)                                  # owner 2026-10-26: SPY / QQQ for the 200-day trend rule (not funnel candidates)
        for s_, d_ in idx_bars.items():
            closes[s_] = float(d_["close"].iloc[-1])
        held = self.state["positions"]
        td_held = {s: p for s, p in held.items() if p.get("strategy", "topdown") == "topdown"}
        exits = [s for s in td_held if s in frames and bool(frames[s]["exit"].iloc[-1])]
        free = MAX_POS - (len(td_held) - len(exits))
        cands = sorted([s for s in frames if s not in held and bool(frames[s]["entry"].iloc[-1])], key=lambda s: -float(frames[s]["rs"].iloc[-1]))
        mv = sum(p["qty"] * closes.get(s, p["entry"]) for s, p in held.items())       # gross: a short ties up margin like a long ties up cash
        equity = self.state["base_equity"] + self.realized() + sum(p.get("side", 1) * (closes.get(s, p["entry"]) - p["entry"]) * p["qty"]
                                                                    for s, p in held.items())
        cash = equity - mv + sum(held[s]["qty"] * closes[s] for s in exits)
        hist = (self.state.get("equity_hist") or [])[-4:] + [round(equity, 2)]
        self.state["equity_hist"] = hist
        locked = equity < max(hist) * (1 - WEEKLY_LIMIT)
        if locked:
            self.journal("weekly_lock", now, equity=round(equity, 2), peak=max(hist))
            if not self.state.get("lock_alerted"):
                _alert(f"ALPACA PAPER (swing): weekly loss lock - book ${equity:,.0f} is {1 - equity / max(hist):.1%} below its 5-day peak. "
                       "No new entries until it recovers; exits and broker stops still run.")
                self.state["lock_alerted"] = True
            cands = []
        else:
            self.state["lock_alerted"] = False
        entries = []
        for s in cands[:max(free, 0)]:
            px, stop = closes[s], float(frames[s]["stop"].iloc[-1])
            risk_ps = (px - stop) + px * SLIP_BPS / 1e4
            if risk_ps <= 0:
                continue
            qty = int(min(equity * RISK_PCT / risk_ps, equity * MAX_POS_FRAC / px, max(cash, 0) / px))
            if qty < 1:
                continue
            cash -= qty * px
            entries.append({"symbol": s, "qty": qty, "stop": stop, "rs": round(float(frames[s]["rs"].iloc[-1]), 4), "signal_close": px,
                            **swing_zone(bars_[s], px, stop, self.zone_config())})
        exits, entries = self.funnel_plan(bars_, closes, held, exits, entries, equity, cash, locked, now)
        exits, entries = self.funnel_plan(bars_, closes, held, exits, entries, equity, cash, locked, now, name="oversold")
        exits, entries = self.index_trend_plan(spy, idx_bars, held, exits, entries, equity, cash, locked, now)
        self.state["plan"] = {"made": today.isoformat(), "exits": exits, "entries": entries}
        self._save()
        self.journal("plan", now, exits=exits, entries=entries, equity=round(equity, 2))
        return self.state["plan"]

    def index_bars(self, now: datetime) -> dict:
        """Completed daily bars of the 200-day-rule index symbols (zones.json swing.index_trend.symbols), when the rule is live."""
        cfg = self.zone_config().get("index_trend") or {}
        if not cfg.get("live"):
            return {}
        today, out = now.astimezone(ET).date(), {}
        for sym in cfg.get("symbols", ["SPY", "QQQ"]):
            try:
                d = daily_bars(sym, now, self.fetch)
            except Exception:
                log.exception("index bars %s", sym)
                continue
            if len(d) >= 200 and d.index[-1].date() == today:
                out[sym] = d
        return out

    def index_trend_plan(self, spy: pd.DataFrame, idx_bars: dict, held: dict, exits: list, entries: list, equity: float, cash: float,
                         locked: bool, now) -> tuple:
        """Owner 2026-10-26 (Gemini round 3 benchmark): the 200-day index rule on paper. (1) GATE: while SPY closes below its 200-day average the
        swing book plans no new LONG entries (shorts are untouched). (2) SLEEVE: hold each index symbol (SPY, QQQ) while its close is above its own
        200-day average, flat otherwise (signal on the close, orders at the next open, like the backtest); sized so a 1-ATR(14) move is
        `atr_risk_pct` of equity, capped at `max_frac` of equity and by cash; a broker GTC stop `stop_atr` ATR below the entry as a safety net
        (the rule itself exits on the close under the average). Backtest 2014-2026: SPY Sharpe 1.00, maxDD -20.2% vs buy-and-hold 0.90 / -33.8%;
        QQQ 1.02 / -22.5% vs 0.95 / -35.0%. Off with zones.json swing.index_trend.live = false."""
        cfg = self.zone_config().get("index_trend") or {}
        if not cfg.get("live"):
            return exits, entries
        if cfg.get("gate_longs", True) and len(spy) >= 200 and float(spy["close"].iloc[-1]) <= float(spy["close"].rolling(200).mean().iloc[-1]):
            blocked = [e["symbol"] for e in entries if e.get("side", 1) == 1]
            entries = [e for e in entries if e.get("side", 1) != 1]
            self.journal("index_gate", now, spy=round(float(spy["close"].iloc[-1]), 2), sma200=round(float(spy["close"].rolling(200).mean().iloc[-1]), 2),
                         blocked=blocked)
        sleeve = {s for s, p in held.items() if p.get("strategy") == "trend200"}
        cash -= sum(e["qty"] * e.get("signal_close", 0.0) for e in entries if e.get("side", 1) == 1)       # the stock entries planned above use the cash first
        planned = []
        for sym, d in idx_bars.items():
            c = d["close"]
            above = float(c.iloc[-1]) > float(c.rolling(200).mean().iloc[-1])
            if sym in sleeve:
                if not above and sym not in exits:
                    exits.append(sym)
                continue
            if sym in held or locked or not above or any(e["symbol"] == sym for e in entries):
                continue
            pc = c.shift(1)
            atr = float(pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1).rolling(14).mean().iloc[-1])
            px = float(c.iloc[-1])
            if not atr > 0:
                continue
            qty = int(min(equity * float(cfg.get("atr_risk_pct", 0.005)) / atr, equity * float(cfg.get("max_frac", 0.30)) / px, max(cash, 0) / px))
            if qty < 1:
                continue
            cash -= qty * px
            entries.append({"symbol": sym, "qty": qty, "stop": round(px - float(cfg.get("stop_atr", 4.0)) * atr, 2), "side": 1, "strategy": "trend200",
                            "signal_close": px, "atr": round(atr, 4)})
            planned.append(sym)
        self.journal("index_trend_plan", now, entries=planned, exits=[e for e in exits if e in sleeve])
        return exits, entries

    def funnel_plan(self, bars_: dict, closes: dict, held: dict, exits: list, entries: list, equity: float, cash: float, locked: bool, now,
                    name: str = "funnel") -> tuple:
        """The owner's Stock Funnel (alpacabot.funnel) beside Sector Top-Down: its own exits (2-of-3 sell vote or the stage changed), its own
        3 slots and 0.5% risk, longs and shorts. Never a stock either strategy already holds or plans to buy. Off with zones.json swing.funnel.live.
        name="oversold" runs the same machinery for the owner's majority-oversold confluence longs (zones.json swing.oversold, funnel.py oversold mode)."""
        cfg = self.zone_config().get(name) or {}
        if not cfg.get("live"):
            return exits, entries
        from .funnel import frame as funnel_frame
        try:
            ff = {s: funnel_frame(d, cfg.get("rules")) for s, d in bars_.items() if len(d) >= 200}
        except Exception:
            log.exception("funnel frames")
            return exits, entries
        fn_held = {s: p for s, p in held.items() if p.get("strategy") == name}
        f_exits = [s for s, p in fn_held.items() if s in ff and bool(ff[s]["long_exit" if p.get("side", 1) == 1 else "short_exit"].iloc[-1])]
        exits = exits + [s for s in f_exits if s not in exits]
        free = int(cfg.get("max", FUNNEL_MAX)) - (len(fn_held) - len(f_exits))
        if locked or free <= 0:
            return exits, entries
        taken = set(held) | {e["symbol"] for e in entries}
        cands = []
        for s, f in ff.items():
            if s in taken:
                continue
            side = 1 if bool(f["long_entry"].iloc[-1]) else -1 if (cfg.get("shorts") and bool(f["short_entry"].iloc[-1])) else 0
            if side:
                cands.append((s, side, f))
        risk_pct = float(cfg.get("risk_pct", FUNNEL_RISK))
        for s, side, f in sorted(cands, key=lambda x: x[0])[:free]:
            tag = "long" if side == 1 else "short"
            px, stop, tp = closes[s], float(f[f"{tag}_stop"].iloc[-1]), float(f[f"{tag}_tp"].iloc[-1])
            risk_ps = side * (px - stop) + px * SLIP_BPS / 1e4
            if risk_ps <= 0:
                continue
            qty = int(min(equity * risk_pct / risk_ps, equity * MAX_POS_FRAC / px, max(cash, 0) / px))   # whole shares, capped by size and cash
            if qty < 1:
                continue
            cash -= qty * px
            entries.append({"symbol": s, "qty": qty, "stop": round(stop, 2), "side": side, "strategy": name, "signal_close": px,
                            "zone_tp": round(tp, 2), "atr": round(float(f["atr"].iloc[-1]), 4)})
        self.journal(f"{name}_plan", now, exits=f_exits, entries=[e["symbol"] for e in entries if e.get("strategy") == name])
        return exits, entries

    # ---------------------------------------------------------------- at the open
    def open(self, now: datetime, wait_until: datetime | None = None, late: bool = False) -> dict:
        """The plan's orders at the open. Owner 2026-10-23: entries of the strategies in zones.json swing.late_entry.strategies (oversold)
        are deferred to late_entry.at (10:00 ET): open(..., late=True) sends them then, skipping any stock already at or through its stop or
        take-profit (the 10:00 backtest: oversold PF 1.27 -> 1.57 over 3 months, stop-outs 10 -> 6; funnel worse, so it keeps the open)."""
        plan = self.state.get("plan")
        if late:
            if not plan or not plan.get("done") or plan.get("late_done") or not plan.get("deferred"):
                return {}
        elif not plan or plan.get("done"):
            return {}
        if date.fromisoformat(plan["made"]) >= now.astimezone(ET).date():
            return {}                                    # a plan is acted on in a LATER session only
        if hasattr(self.b, "is_session") and not self.b.is_session(now.astimezone(ET).date()):
            return {}                                    # holiday: the plan waits for the next session
        tag = now.astimezone(ET).strftime("%Y%m%d")
        # Alpaca's paper account let all three opening-auction (OPG) orders expire unfilled on 2026-10-13, so orders are plain
        # market day orders sent a few seconds after the 09:30 open (the backtest fills at the open price).
        d = now.astimezone(ET)
        le = self.zone_config().get("late_entry") or {}
        late_strats = set(le.get("strategies") or [])
        if late:
            hh, mm = (int(x) for x in str(le.get("at", "10:00")).split(":"))
            opens = datetime(d.year, d.month, d.day, hh, mm, 5, tzinfo=ET)
        else:
            opens = datetime(d.year, d.month, d.day, 9, 30, 5, tzinfo=ET)
        wait = (opens.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
        if 0 < wait <= 3600:                             # the 09:20 timer; a manual run far from the open does not block
            self.sleep(wait)
        sent = {}
        for sym in ([] if late else plan["exits"]):
            pos = self.state["positions"].get(sym)
            if not pos:
                continue
            if pos.get("stop_order_id"):
                try:
                    self.b.cancel_order(pos["stop_order_id"])
                except Exception:
                    log.exception("cancel stop %s", sym)
                # 2026-10-27 fix: Alpaca keeps the shares "held_for_orders" until the cancel is processed; selling at once failed with
                # 403 "insufficient qty available" on 2026-10-26 and crashed the whole open. Wait for the cancel (or a fill) first.
                st = {}
                for _ in range(20):
                    try:
                        st = self.b.order(pos["stop_order_id"]) or {}
                    except Exception:
                        st = {}
                    if st.get("status") in ("canceled", "filled", "expired", "rejected"):
                        break
                    self.sleep(0.5)
                if st.get("status") == "filled":              # the stop / take-profit filled before the cancel: the broker already closed it
                    self.settle_stops(now)
                    continue
            try:
                sent[sym] = ("exit", self.b.submit_order(sym, pos["qty"], "sell" if pos.get("side", 1) == 1 else "buy", "market", "day",
                                                         f"sw-x-{sym}-{tag}"))
            except Exception as exc:                          # one failed order never stops the rest of the open
                log.exception("exit order %s", sym)
                self.journal("exit_error", now, symbol=sym, error=str(exc)[:200])
                try:                                          # never leave the position without a broker stop
                    so = self.b.submit_order(sym, pos["qty"], "sell" if pos.get("side", 1) == 1 else "buy", "stop", "gtc",
                                             f"sw-r-{sym}-{tag}", stop_price=round(pos["stop"], 2))
                    pos["stop_order_id"], pos["oco"] = (so.get("id") if isinstance(so, dict) else None), False
                except Exception:
                    log.exception("re-place stop %s", sym)
                _alert(f"SWING (paper): {sym} exit order failed ({str(exc)[:120]}); a stop at {pos['stop']} was re-placed - check it")
        zcfg = self.zone_config()
        for e in ((plan.get("deferred") or []) if late else plan["entries"]):
            side = e.get("side", 1)
            if not late and e.get("strategy") in late_strats:   # waits for late_entry.at (sent by open(..., late=True))
                plan.setdefault("deferred", []).append(e)
                self.journal("entry_deferred", now, symbol=e["symbol"], strategy=e.get("strategy"), until=le.get("at", "10:00"))
                continue
            if late and hasattr(self.b, "quote"):                 # already at / through its stop or its take-profit by now: no trade
                try:
                    bid, ask = (float(x) for x in self.b.quote(e["symbol"])[:2])
                    px_now = ask if side == 1 else bid
                    if side * (px_now - e["stop"]) <= 0 or (e.get("zone_tp") and side * (px_now - e["zone_tp"]) >= 0):
                        self.journal("late_skip", now, symbol=e["symbol"], px=px_now, stop=e["stop"], zone_tp=e.get("zone_tp"))
                        continue
                except Exception:
                    log.exception("late entry check %s (skipped)", e["symbol"])
                    continue
            if side == -1:                               # a funnel short: easy to borrow, and no other bot holds this stock (a sell would cut THEIR long)
                try:
                    if hasattr(self.b, "shortable") and not self.b.shortable(e["symbol"]):
                        self.journal("short_skip", now, symbol=e["symbol"], reason="not easy to borrow")
                        continue
                    if hasattr(self.b, "positions") and self.b.positions().get(e["symbol"], 0):
                        self.journal("short_skip", now, symbol=e["symbol"], reason="another bot holds this stock")
                        continue
                except Exception:
                    log.exception("short check %s (skipped)", e["symbol"])
                    continue
            if zcfg["live"] and e.get("atr") and hasattr(self.b, "quote"):        # entry zone: no chasing a gap past signal close + 0.5 ATR
                try:
                    bid, ask = (float(x) for x in self.b.quote(e["symbol"])[:2])
                    px_now = ask if side == 1 else bid
                    if side * (px_now - e["signal_close"]) > zcfg["profiles"]["trend"]["chase_atr"] * e["atr"]:
                        self.journal("chase_skip", now, symbol=e["symbol"], ask=px_now, signal_close=e["signal_close"], atr=e["atr"])
                        continue
                except Exception:
                    log.exception("chase check %s (entering anyway)", e["symbol"])
            try:
                sent[e["symbol"]] = ("entry", self.b.submit_order(e["symbol"], e["qty"], "buy" if side == 1 else "sell", "market", "day",
                                                                  f"sw-e-{e['symbol']}-{tag}"))
            except Exception as exc:                          # one rejected entry never stops the rest of the open
                log.exception("entry order %s", e["symbol"])
                self.journal("entry_error", now, symbol=e["symbol"], error=str(exc)[:200])
        if late:
            plan["late_done"] = True
        else:
            plan["done"] = True
        self._save()
        self.journal("orders_late" if late else "orders", now, sent={k: v[0] for k, v in sent.items()})
        deadline = wait_until or max(now, opens) + timedelta(minutes=10)
        pending = dict(sent)
        while pending:
            for sym, (kind, o) in list(pending.items()):
                st = self.b.order(o["id"]) if isinstance(o, dict) and o.get("id") else {"status": "rejected"}
                if st.get("status") in ("filled", "rejected", "canceled", "expired"):
                    self._settle_open(sym, kind, st, now)
                    del pending[sym]
            if pending and datetime.now(timezone.utc) < deadline.astimezone(timezone.utc):
                self.sleep(5)
            elif pending:                                # never leave a working order behind: a late fill would be untracked
                for sym, (kind, o) in pending.items():
                    try:
                        self.b.cancel_order(o["id"])
                        st = self.b.order(o["id"])
                    except Exception:
                        log.exception("cancel unfilled %s", sym)
                        st = {}
                    if float(st.get("filled_qty") or 0) > 0:
                        self._settle_open(sym, kind, {**st, "status": "filled"}, now)
                    else:
                        self.journal("open_unfilled", now, symbol=sym, order=kind, status=st.get("status"))
                break
        self._save()
        return sent

    def close_all(self, now: datetime, wait_s: float = 60.0) -> dict:
        """Owner's manual close: cancel each swing position's stop / OCO at Alpaca, sell it at market and book it as "owner_close"."""
        self.settle_stops(now)                            # a stop that already filled is booked at its own price first
        tag = now.astimezone(ET).strftime("%Y%m%d%H%M")
        sent = {}
        for sym, pos in list(self.state["positions"].items()):
            if pos.get("stop_order_id"):
                try:
                    self.b.cancel_order(pos["stop_order_id"])
                except Exception:
                    log.exception("cancel stop %s", sym)
            sent[sym] = self.b.submit_order(sym, pos["qty"], "sell" if pos.get("side", 1) == 1 else "buy", "market", "day", f"sw-c-{sym}-{tag}")
        out, waited = {}, 0.0
        pending = dict(sent)
        while pending:
            for sym, o in list(pending.items()):
                st = self.b.order(o["id"]) if isinstance(o, dict) and o.get("id") else {"status": "rejected"}
                if st.get("status") in ("filled", "rejected", "canceled", "expired"):
                    if st.get("status") == "filled":
                        px = float(st.get("filled_avg_price") or 0)
                        self.record(sym, self.state["positions"].pop(sym), px, "owner_close", now)
                        out[sym] = f"closed @ {px:.2f}"
                    else:
                        out[sym] = f"NOT closed ({st.get('status')}): stop was cancelled, check it"
                        self.journal("close_not_filled", now, symbol=sym, status=st.get("status"))
                    del pending[sym]
            if pending and waited < wait_s:
                self.sleep(2)
                waited += 2
            elif pending:
                for sym in pending:
                    out[sym] = "close order still working after %.0fs: check it" % wait_s
                break
        self._save()
        self.journal("owner_close", now, result=out)
        return out

    def _settle_open(self, sym: str, kind: str, o: dict, now: datetime):
        filled = o.get("status") == "filled"
        px = float(o.get("filled_avg_price") or 0)
        if kind == "exit":
            if filled:
                self.record(sym, self.state["positions"].pop(sym), px, "signal", now)
            else:
                self.journal("exit_not_filled", now, symbol=sym, status=o.get("status"))
            return
        e = next(x for x in self.state["plan"]["entries"] if x["symbol"] == sym)
        if not filled:
            self.journal("entry_not_filled", now, symbol=sym, status=o.get("status"))
            _alert(f"SWING (paper): {sym} buy not filled ({o.get('status')})")
            return
        qty = int(float(o.get("filled_qty") or e["qty"]))
        side = e.get("side", 1)
        close_side = "sell" if side == 1 else "buy"
        pos = {"qty": qty, "entry": px, "stop": e["stop"], "entry_date": now.astimezone(ET).date().isoformat()}
        if side == -1 or e.get("strategy"):
            pos.update(side=side, strategy=e.get("strategy", "topdown"))
        if side * (px - e["stop"]) <= 0:                 # opened at/through its stop: the backtest takes no such trade, so get out now
            self.b.submit_order(sym, qty, close_side, "market", "day", f"sw-g-{sym}-{now.astimezone(ET):%Y%m%d}")
            self.record(sym, pos, px, "gap_below_stop", now)
            return
        tp = e.get("zone_tp")
        if tp and self.zone_config()["live"]:
            if side * (px - tp) >= 0:                      # opened at/past its take-profit zone: take the profit now
                self.b.submit_order(sym, qty, close_side, "market", "day", f"sw-z-{sym}-{now.astimezone(ET):%Y%m%d}")
                self.record(sym, pos, px, "zone", now)
                return
            if hasattr(self.b, "submit_oco"):              # stop + take-profit zone at the broker, one cancels the other
                kw = {} if side == 1 else {"side": "buy"}
                so = self.b.submit_oco(sym, qty, round(tp, 2), round(e["stop"], 2), f"sw-o-{sym}-{now.astimezone(ET):%Y%m%d}", **kw)
                pos.update(stop_order_id=so.get("id") if isinstance(so, dict) else None, oco=True, zone_tp=round(tp, 2))
                self.state["positions"][sym] = pos
                self.journal("entry", now, symbol=sym, qty=qty, fill=px, stop=e["stop"], zone_tp=round(tp, 2), side=side, strategy=pos.get("strategy"))
                return
        so = self.b.submit_order(sym, qty, close_side, "stop", "gtc", f"sw-s-{sym}-{now.astimezone(ET):%Y%m%d}", stop_price=round(e["stop"], 2))
        pos["stop_order_id"] = so.get("id") if isinstance(so, dict) else None
        self.state["positions"][sym] = pos
        self.journal("entry", now, symbol=sym, qty=qty, fill=px, stop=e["stop"])


def swing_zone(d, close: float, stop: float, zcfg: dict) -> dict:
    """The swing trade's take-profit zone (owner's zones, trend profile): the nearest strong zone of the stock's daily zone map at least
    min_r x risk above the signal close; else the prior 20-day high if far enough; else default_r x risk. Plus ATR for the chase check."""
    from .indicators import atr
    from .zonemap import LEVEL_RULES, level_map, zone_target
    zp = zcfg["profiles"]["trend"]
    risk = close - stop
    if risk <= 0 or d is None or len(d) < 20:
        return {}
    a = float(atr(d, 14).iloc[-1])
    tgt = zone_target(level_map(d.iloc[-LEVEL_RULES["lookback"]:]), close, 1, risk, zp["min_r"]) if zcfg.get("use_map") else None
    if tgt is None:
        hi = float(d["high"].iloc[-zp["lookback"]:].max())
        tgt = hi if hi - close >= zp["min_r"] * risk else close + zp["default_r"] * risk
    return {"zone_tp": round(float(tgt), 2), "atr": round(a, 4)}


def _alert(text: str):
    try:
        from .paper import send_telegram
        send_telegram(text)
    except Exception:
        log.exception("telegram alert failed")


def summary(state_dir="state/alpaca_swing") -> list[str]:
    """Lines for the daily report card."""
    d = Path(state_dir)
    if not d.exists():
        return []
    st = json.loads((d / "state.json").read_text()) if (d / "state.json").exists() else {}
    trades = [json.loads(x) for x in (d / "trades.jsonl").read_text().splitlines() if x.strip()] if (d / "trades.jsonl").exists() else []
    w = sum(t["pnl"] > 0 for t in trades)
    lines = ["", "SWING (Sector Top-Down + Stock Funnel, daily, holds overnight)",
             f"  closed trades {len(trades)}, {w}W/{len(trades) - w}L, P&L ${sum(t['pnl'] for t in trades):+,.0f}"]
    for strat in ("topdown", "funnel", "oversold"):
        ts = [t for t in trades if t.get("strategy", "topdown") == strat]
        if ts and strat != "topdown":
            lines.append(f"  {strat}: {len(ts)} trades, {sum(t['pnl'] > 0 for t in ts)}W, P&L ${sum(t['pnl'] for t in ts):+,.0f}")
    pos = st.get("positions") or {}
    lines.append("  holding: " + (", ".join(f"{s} {'short ' if p.get('side', 1) == -1 else ''}{p['qty']} @ {p['entry']:.2f} (stop {p['stop']:.2f}"
                                           f"{', ' + p['strategy'] if p.get('strategy', 'topdown') != 'topdown' else ''})" for s, p in pos.items()) or "nothing"))
    plan = st.get("plan") or {}
    if plan and not plan.get("done"):
        lines.append(f"  next open: buy {[e['symbol'] for e in plan.get('entries', [])] or 'nothing'}, sell {plan.get('exits') or 'nothing'}")
    return lines
