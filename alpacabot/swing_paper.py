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
        pnl = (px - pos["entry"]) * pos["qty"]
        risk = (pos["entry"] - pos["stop"]) * pos["qty"]
        with open(self.dir / "trades.jsonl", "a") as f:
            f.write(json.dumps({"date": when.date().isoformat(), "symbol": sym, "side": "long", "qty": pos["qty"], "entry": pos["entry"],
                                "exit": px, "pnl": round(pnl, 2), "r": round(pnl / risk, 3) if risk > 0 else 0.0, "reason": reason,
                                "opened": pos["entry_date"]}) + "\n")
        self.journal("exit", when, symbol=sym, px=px, reason=reason, pnl=round(pnl, 2))

    def realized(self) -> float:
        p = self.dir / "trades.jsonl"
        return sum(json.loads(x)["pnl"] for x in p.read_text().splitlines() if x.strip()) if p.exists() else 0.0

    # ---------------------------------------------------------------- after the close
    def zone_config(self) -> dict:
        """<state>/zones.json (written with defaults on first use; edited by the 2-hour optimizer). LIVE by the owner's decision."""
        from .engine import ZONE_PROFILES
        p = Path(self.dir) / "zones.json"
        default = {"live": True, "use_map": True, "profiles": ZONE_PROFILES}
        try:
            if not p.exists():
                p.write_text(json.dumps(default, indent=1))
            cfg = {**default, **json.loads(p.read_text())}
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
        held = self.state["positions"]
        exits = [s for s in held if s in frames and bool(frames[s]["exit"].iloc[-1])]
        free = MAX_POS - (len(held) - len(exits))
        cands = sorted([s for s in frames if s not in held and bool(frames[s]["entry"].iloc[-1])], key=lambda s: -float(frames[s]["rs"].iloc[-1]))
        mv = sum(p["qty"] * closes.get(s, p["entry"]) for s, p in held.items())
        equity = self.state["base_equity"] + self.realized() + sum((closes.get(s, p["entry"]) - p["entry"]) * p["qty"] for s, p in held.items())
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
        self.state["plan"] = {"made": today.isoformat(), "exits": exits, "entries": entries}
        self._save()
        self.journal("plan", now, exits=exits, entries=entries, equity=round(equity, 2))
        return self.state["plan"]

    # ---------------------------------------------------------------- at the open
    def open(self, now: datetime, wait_until: datetime | None = None) -> dict:
        plan = self.state.get("plan")
        if not plan or plan.get("done"):
            return {}
        if date.fromisoformat(plan["made"]) >= now.astimezone(ET).date():
            return {}                                    # a plan is acted on in a LATER session only
        if hasattr(self.b, "is_session") and not self.b.is_session(now.astimezone(ET).date()):
            return {}                                    # holiday: the plan waits for the next session
        tag = now.astimezone(ET).strftime("%Y%m%d")
        # Alpaca's paper account let all three opening-auction (OPG) orders expire unfilled on 2026-10-13, so orders are plain
        # market day orders sent a few seconds after the 09:30 open (the backtest fills at the open price).
        d = now.astimezone(ET)
        opens = datetime(d.year, d.month, d.day, 9, 30, 5, tzinfo=ET)
        wait = (opens.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
        if 0 < wait <= 1800:                             # the 09:20 timer; a manual run far from the open does not block
            self.sleep(wait)
        sent = {}
        for sym in plan["exits"]:
            pos = self.state["positions"].get(sym)
            if not pos:
                continue
            if pos.get("stop_order_id"):
                try:
                    self.b.cancel_order(pos["stop_order_id"])
                except Exception:
                    log.exception("cancel stop %s", sym)
            sent[sym] = ("exit", self.b.submit_order(sym, pos["qty"], "sell", "market", "day", f"sw-x-{sym}-{tag}"))
        zcfg = self.zone_config()
        for e in plan["entries"]:
            if zcfg["live"] and e.get("atr") and hasattr(self.b, "quote"):        # entry zone: no chasing a gap past signal close + 0.5 ATR
                try:
                    ask = float(self.b.quote(e["symbol"])[1])
                    if ask > e["signal_close"] + zcfg["profiles"]["trend"]["chase_atr"] * e["atr"]:
                        self.journal("chase_skip", now, symbol=e["symbol"], ask=ask, signal_close=e["signal_close"], atr=e["atr"])
                        continue
                except Exception:
                    log.exception("chase check %s (entering anyway)", e["symbol"])
            sent[e["symbol"]] = ("entry", self.b.submit_order(e["symbol"], e["qty"], "buy", "market", "day", f"sw-e-{e['symbol']}-{tag}"))
        plan["done"] = True
        self._save()
        self.journal("orders", now, sent={k: v[0] for k, v in sent.items()})
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
        pos = {"qty": qty, "entry": px, "stop": e["stop"], "entry_date": now.astimezone(ET).date().isoformat()}
        if px <= e["stop"]:                              # opened at/below its stop: the backtest takes no such trade, so get out now
            self.b.submit_order(sym, qty, "sell", "market", "day", f"sw-g-{sym}-{now.astimezone(ET):%Y%m%d}")
            self.record(sym, pos, px, "gap_below_stop", now)
            return
        tp = e.get("zone_tp")
        if tp and self.zone_config()["live"]:
            if px >= tp:                                   # opened at/above its take-profit zone: take the profit now
                self.b.submit_order(sym, qty, "sell", "market", "day", f"sw-z-{sym}-{now.astimezone(ET):%Y%m%d}")
                self.record(sym, pos, px, "zone", now)
                return
            if hasattr(self.b, "submit_oco"):              # stop + take-profit zone at the broker, one cancels the other
                so = self.b.submit_oco(sym, qty, round(tp, 2), round(e["stop"], 2), f"sw-o-{sym}-{now.astimezone(ET):%Y%m%d}")
                pos.update(stop_order_id=so.get("id") if isinstance(so, dict) else None, oco=True, zone_tp=round(tp, 2))
                self.state["positions"][sym] = pos
                self.journal("entry", now, symbol=sym, qty=qty, fill=px, stop=e["stop"], zone_tp=round(tp, 2))
                return
        so = self.b.submit_order(sym, qty, "sell", "stop", "gtc", f"sw-s-{sym}-{now.astimezone(ET):%Y%m%d}", stop_price=round(e["stop"], 2))
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
    lines = ["", "SWING (Sector Top-Down, daily, holds overnight)",
             f"  closed trades {len(trades)}, {w}W/{len(trades) - w}L, P&L ${sum(t['pnl'] for t in trades):+,.0f}"]
    pos = st.get("positions") or {}
    lines.append("  holding: " + (", ".join(f"{s} {p['qty']} @ {p['entry']:.2f} (stop {p['stop']:.2f})" for s, p in pos.items()) or "nothing"))
    plan = st.get("plan") or {}
    if plan and not plan.get("done"):
        lines.append(f"  next open: buy {[e['symbol'] for e in plan.get('entries', [])] or 'nothing'}, sell {plan.get('exits') or 'nothing'}")
    return lines
