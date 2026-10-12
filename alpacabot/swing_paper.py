"""Daily swing runner (PAPER only) for the Sector Top-Down rules: the same strategies.SectorTopDown code the backtest uses.

  scan  (weekdays 16:20 ET, after the close): settle stops that filled today, then plan for the NEXT session:
          exits    every held position whose daily close fell below EMA20
          entries  today's signals, ranked by relative strength, into the free slots (3 max), sized like engine.size_swing:
                   qty = min(equity x 0.35% / (est. risk per share + slippage), equity x 33.3% / price, free cash / price)
  open  (weekdays 09:20 ET, before the open): opening-auction (OPG) orders for the plan, so fills ARE the open the backtest uses;
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

ET = ZoneInfo(NY)
UNIVERSE = ["AAPL", "NVDA", "MSFT", "AMD", "TSLA", "AMZN", "GOOGL", "META", "XLK", "QQQ"]
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
    def settle_stops(self, now: datetime):
        """Positions whose broker stop filled are closed in our books at the broker's fill price."""
        for sym, pos in list(self.state["positions"].items()):
            oid = pos.get("stop_order_id")
            if not oid:
                continue
            o = self.b.order(oid)
            if o.get("status") == "filled":
                self.record(sym, pos, float(o.get("filled_avg_price") or pos["stop"]), "stop", now)
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
        frames, closes = {}, {}
        for sym in self.universe:
            d = daily_bars(sym, now, self.fetch)
            if len(d) and d.index[-1].date() == today:
                frames[sym], closes[sym] = strat.frame(d), float(d["close"].iloc[-1])
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
            entries.append({"symbol": s, "qty": qty, "stop": stop, "rs": round(float(frames[s]["rs"].iloc[-1]), 4), "signal_close": px})
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
            sent[sym] = ("exit", self.b.submit_order(sym, pos["qty"], "sell", "market", "opg", f"sw-x-{sym}-{tag}"))
        for e in plan["entries"]:
            sent[e["symbol"]] = ("entry", self.b.submit_order(e["symbol"], e["qty"], "buy", "market", "opg", f"sw-e-{e['symbol']}-{tag}"))
        plan["done"] = True
        self._save()
        self.journal("orders", now, sent={k: v[0] for k, v in sent.items()})
        deadline = wait_until or now + timedelta(minutes=15)
        pending = dict(sent)
        while pending:
            for sym, (kind, o) in list(pending.items()):
                st = self.b.order(o["id"]) if isinstance(o, dict) and o.get("id") else {"status": "rejected"}
                if st.get("status") in ("filled", "rejected", "canceled", "expired"):
                    self._settle_open(sym, kind, st, now)
                    del pending[sym]
            if pending and datetime.now(timezone.utc) < deadline.astimezone(timezone.utc):
                self.sleep(5)
            elif pending:
                for sym, (kind, _) in pending.items():
                    self.journal("open_unfilled", now, symbol=sym, kind=kind)
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
            return
        qty = int(float(o.get("filled_qty") or e["qty"]))
        pos = {"qty": qty, "entry": px, "stop": e["stop"], "entry_date": now.astimezone(ET).date().isoformat()}
        if px <= e["stop"]:                              # opened at/below its stop: the backtest takes no such trade, so get out now
            self.b.submit_order(sym, qty, "sell", "market", "day", f"sw-g-{sym}-{now.astimezone(ET):%Y%m%d}")
            self.record(sym, pos, px, "gap_below_stop", now)
            return
        so = self.b.submit_order(sym, qty, "sell", "stop", "gtc", f"sw-s-{sym}-{now.astimezone(ET):%Y%m%d}", stop_price=round(e["stop"], 2))
        pos["stop_order_id"] = so.get("id") if isinstance(so, dict) else None
        self.state["positions"][sym] = pos
        self.journal("entry", now, symbol=sym, qty=qty, fill=px, stop=e["stop"])


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
