"""Paper-trading runner: the backtest's Brain decides, Alpaca's PAPER account fills, the virtual exit manager closes.

PAPER ONLY. The trading endpoint is hard-wired to paper-api.alpaca.markets; there is no setting for a live account.
Nothing here is validated yet: this runner exists to collect real forward results while the backtests run. Every decision and fill is
appended to state/alpaca_paper/journal.jsonl so the forward record can be compared with the backtest later (`python -m alpacabot paper-report`).
"""

import json
import logging
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .brain import Brain
from .data import NY, _fetch, _headers
from .engine import resample_bars
from .exits import StateStore
from .execution import CircuitBreaker, EntryWindow, Executor, VirtualExitManager, retry_call, setup_logging, spread_ok
from .experts import ALL_EXPERTS, features_both
from . import gates

ET = ZoneInfo(NY)
TRADE_URL = "https://paper-api.alpaca.markets/v2"
DATA_URL = "https://data.alpaca.markets/v2"
SYMBOLS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "AMD"]
log = logging.getLogger("alpacabot")


class RestBroker:
    """Paper broker over plain REST (no extra dependency). Market orders only; exits are virtual."""

    def __init__(self, session: requests.Session | None = None):
        self.s = session or requests.Session()

    def _req(self, method, url, **kw):
        r = self.s.request(method, url, headers=_headers(), timeout=30, **kw)
        if r.status_code in (429,) or r.status_code >= 500:
            raise requests.ConnectionError(f"HTTP {r.status_code}")
        if r.status_code >= 400 and r.status_code != 422:      # 401/403 = wrong or live keys: fail loudly
            r.raise_for_status()
        return r

    def equity(self) -> float:
        return float(self._req("GET", f"{TRADE_URL}/account").json()["equity"])

    def clock_open(self) -> bool:
        return bool(self.clock()["is_open"])

    def clock(self) -> dict:
        return self._req("GET", f"{TRADE_URL}/clock").json()

    JUNK_SPREAD = 0.001     # an IEX quote wider than 0.1% of price is IEX's thin book, not the market

    def last_trade(self, symbol: str) -> float:
        return float(self._req("GET", f"{DATA_URL}/stocks/{symbol}/trades/latest", params={"feed": "iex"}).json()["trade"]["p"])

    def quote(self, symbol: str):
        """(bid, ask). The free plan only has IEX, one exchange whose top of book is often far wider than the real market (TSLA $2-3,
        AMD $30 on Oct 12 when the real spread was ~1 cent). Such a quote is replaced by the last IEX trade print on both sides, so neither
        the entry guard nor the virtual stops act on a phantom spread."""
        q = self._req("GET", f"{DATA_URL}/stocks/{symbol}/quotes/latest", params={"feed": "iex"}).json()["quote"]
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if bid <= 0 or ask <= 0 or ask < bid or (ask - bid) / ((ask + bid) / 2) > self.JUNK_SPREAD:
            t = self.last_trade(symbol)
            return t, t
        return bid, ask

    def submit_order(self, symbol: str, qty: int, side: str, type_: str, tif: str, client_order_id: str, stop_price: float | None = None) -> dict:
        """Generic order (the swing runner: OPG market orders at the open, GTC stops). A repeated client id returns the existing order."""
        body = {"symbol": symbol, "qty": str(qty), "side": side, "type": type_, "time_in_force": tif, "client_order_id": client_order_id}
        if stop_price is not None:
            body["stop_price"] = f"{stop_price:.2f}"
        r = self._req("POST", f"{TRADE_URL}/orders", json=body)
        if r.status_code == 422 and "client_order_id" in r.text:
            return self._req("GET", f"{TRADE_URL}/orders:by_client_order_id", params={"client_order_id": client_order_id}).json()
        r.raise_for_status()
        return r.json()

    def cancel_order(self, order_id: str):
        return self._req("DELETE", f"{TRADE_URL}/orders/{order_id}")

    def is_session(self, day) -> bool:
        cal = self._req("GET", f"{TRADE_URL}/calendar", params={"start": day.isoformat(), "end": day.isoformat()}).json()
        return any(c.get("date") == day.isoformat() for c in cal)

    def order(self, order_id: str) -> dict:
        return self._req("GET", f"{TRADE_URL}/orders/{order_id}").json()

    def positions(self) -> dict[str, int]:
        """symbol -> signed quantity (negative = short)."""
        return {p["symbol"]: abs(int(float(p["qty"]))) * (-1 if p.get("side") == "short" else 1) for p in self._req("GET", f"{TRADE_URL}/positions").json()}

    def shortable(self, symbol: str) -> bool:
        a = self._req("GET", f"{TRADE_URL}/assets/{symbol}").json()
        return bool(a.get("shortable")) and bool(a.get("easy_to_borrow"))

    def daily(self, symbol: str, days: int = 90):
        now = datetime.now(ET)
        return _fetch(symbol, "1Day", (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z"), now.strftime("%Y-%m-%dT%H:%M:%SZ"), "iex")

    def submit_market(self, symbol: str, qty: int, client_order_id: str | None = None, side: str = "buy"):
        body = {"symbol": symbol, "qty": str(qty), "side": side, "type": "market", "time_in_force": "day"}
        if client_order_id:
            body["client_order_id"] = client_order_id
        r = self._req("POST", f"{TRADE_URL}/orders", json=body)
        if r.status_code == 422 and "client_order_id" in r.text:
            return None                                    # duplicate id: this order was already sent
        r.raise_for_status()
        return r.json()

    def close_tagged(self, symbol: str, qty: int, side: int, client_order_id: str):
        """Close `qty` of OUR position with a market order that carries our own client order id (sell a long / buy back a short)."""
        return self.submit_market(symbol, qty, client_order_id, side="sell" if side == 1 else "buy")

    def close(self, symbol: str, qty: int | None = None):
        """Close `qty` shares: sells a long or buys back a short (only this bot's own shares on a shared account); the whole position when qty is None."""
        return self._req("DELETE", f"{TRADE_URL}/positions/{symbol}", params={"qty": str(qty)} if qty else None)


class OwnBook:
    """The account is shared with the owner's other bots, so this bot must only ever touch, and only ever measure, its own shares.

    equity()     = this bot's own book: starting capital + its realized P&L + its open P&L (drives sizing, the breaker and the report card)
    close(sym)   = sells only this bot's quantity in sym
    flatten_all  = closes only this bot's positions; never cancels or closes anything the other bots own
    """

    def __init__(self, broker, runner):
        self.b, self.r = broker, runner

    def __getattr__(self, name):                 # quote, positions, submit_market, clock_open pass straight through
        return getattr(self.b, name)

    def equity(self) -> float:
        return self.r.own_equity()

    def close(self, symbol: str):
        pos = self.r.exits.positions.get(symbol)
        if not pos:
            return None
        if hasattr(self.b, "close_tagged"):          # exit orders carry the ab- prefix too, so the history can always trace them
            coid = f"ab-x-{symbol}-{int(time.time() * 1000)}"
            return self.b.close_tagged(symbol, pos["qty"], pos.get("side", 1), coid)
        return self.b.close(symbol, pos["qty"])

    def flatten_all(self):
        for sym in list(self.r.exits.positions):
            bid, ask = retry_call(self.b.quote, sym, sleep=self.r.sleep)
            self.close(sym)
            self.r.record_exit(sym, bid if self.r.exits.positions[sym].get("side", 1) == 1 else ask, "flatten")


def today_frame(sym: str, now_et: datetime, brain: Brain, fetch=_fetch):
    """Today's completed 5-minute bars with causal long AND short (mirrored) features, computed on the last 5 sessions so they are warm."""
    start = (now_et - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
    raw = fetch(sym, "1Min", start, (now_et + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"), "iex")
    raw = raw.between_time("09:30", "15:59") if len(raw) else raw
    if raw.empty:
        return None
    df = resample_bars(raw, 5)
    df = df[df.index + timedelta(minutes=5) <= now_et]       # a bar is usable only once it has closed
    if len(df) < 20:
        return None
    df = df.join(features_both(df))
    day = df[df.index.date == now_et.date()]
    return day if len(day) >= 3 else None


def bar_start(t: datetime) -> pd.Timestamp:
    """Start of the 5-minute bar that contains t."""
    return pd.Timestamp(t).floor("5min")


ENGAGED = Path(__file__).with_name("brain_config.json")     # the validated configuration, committed to the repo when one passes
BRAIN_KEYS = {"experts", "short_experts", "priority", "route", "mtf", "consensus", "sector_filter", "short_gate", "max_trades", "cooldown"}


def default_brain(state_dir) -> Brain:
    """Paper default: every expert both ways; longs blocked in a BEARISH sector; shorts only while the market or the sector is BEARISH or
    SPY is red on the day. Replaced by alpacabot/brain_config.json once validation has proven a configuration (its "brain" block), and by
    <state>/brain.json for a local override."""
    cfg = {"experts": list(ALL_EXPERTS), "short_experts": list(ALL_EXPERTS), "sector_filter": True, "short_gate": "red"}
    for p in (ENGAGED, Path(state_dir) / "brain.json"):
        if p.exists():
            raw = json.loads(p.read_text())
            cfg.update({k: v for k, v in raw.get("brain", raw).items() if k in BRAIN_KEYS})
    log.info("brain: %s", cfg)
    return Brain(**cfg)


def engaged_symbols() -> list[str]:
    """The paper universe: SYMBOLS plus any stock added to brain_config.json ("symbols": [...], "sectors": {"XOM": "XLE"}) after it passed
    `python -m alpacabot symbol-check`. Added stocks join the sector heat map through their sector ETF."""
    syms = list(SYMBOLS)
    if ENGAGED.exists():
        raw = json.loads(ENGAGED.read_text())
        for sym, etf in (raw.get("sectors") or {}).items():
            gates.add_sector(sym, etf)
        syms += [x for x in raw.get("symbols", []) if x not in syms]
    return syms


class PaperRunner:
    def __init__(self, broker, brain: Brain | None = None, state_dir="state/alpaca_paper", risk_pct=0.01, symbols=None, fetch=_fetch, sleep=time.sleep,
                 breaker_limit: float = 0.02, max_positions: int = 6):
        self.broker, self.brain, self.symbols, self.fetch, self.sleep = broker, brain or default_brain(state_dir), symbols or engaged_symbols(), fetch, sleep
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store = StateStore(self.dir / "positions.json")
        self.raw = broker
        self.broker = OwnBook(broker, self)
        self.exits = VirtualExitManager(self.store, self.broker, sleep=sleep)
        # no 45-minute stale exit: each signal carries the backtest's own time stop (max_bars), checked at every bar close
        self.exec = Executor(self.broker, EntryWindow(), CircuitBreaker(limit=breaker_limit), risk_pct=risk_pct, max_positions=max_positions,
                             stale_minutes=10 ** 9, sleep=sleep, max_spread_pct=0.001)      # relative guard: 0.1% of price
        self.last_px: dict[str, float] = {}      # last exit-side price: bid for longs, ask for shorts
        self.heat, self.heat_day, self.borrow = {}, None, {}
        self.day, self.trades_today, self.last_exit = None, {}, {}
        self.now = None   # per-symbol daily trade cap and post-exit cooldown, as in the backtest
        base = self.dir / "base_equity.json"
        if not base.exists():                    # the bot's starting capital = the account equity the first time it runs
            base.write_text(json.dumps({"equity": float(retry_call(broker.equity, sleep=sleep))}))
        self.base_equity = json.loads(base.read_text())["equity"]
        self.exec.opened = self.exits.opened_at()
        self.last_bar: dict[str, object] = {}
        self.entries_today = 0
        self.report_marker = self.dir / "last_report.txt"
        self.reported = date.fromisoformat(self.report_marker.read_text().strip()) if self.report_marker.exists() else None
        self.traded_days: set = set()
        self._session_cache: dict = {}

    def journal(self, kind: str, **kw):
        with open(self.dir / "journal.jsonl", "a") as f:
            f.write(json.dumps({"t": self.clock().isoformat(timespec="seconds"), "kind": kind, **kw}, default=str) + "\n")

    def realized(self) -> float:
        return sum(t["pnl"] for t in _jl(self.dir / "trades.jsonl"))

    def clock(self) -> datetime:
        """The trading clock (the time the loop is working on), not the machine's wall clock."""
        return self.now or datetime.now(ET)

    @staticmethod
    def entry_px(pos) -> float:
        return pos.get("side", 1) * pos["state"].entry          # shorts are tracked on -price

    def own_equity(self) -> float:
        open_pnl = sum(p.get("side", 1) * (self.last_px.get(s, self.entry_px(p)) - self.entry_px(p)) * p["qty"] for s, p in self.exits.positions.items())
        return self.base_equity + self.realized() + open_pnl

    def record_exit(self, sym: str, px: float, reason: str, pos=None):
        """px: the real exit price (bid for a long, ask for a short)."""
        pos = pos or self.exits.positions.get(sym)
        if not pos:
            return
        qty, side, entry = pos["qty"], pos.get("side", 1), self.entry_px(pos)
        risk = pos["state"].atr * pos["state"].trail * qty
        pnl = side * (px - entry) * qty
        self.journal("exit", symbol=sym, reason=reason, px=px, side=side)
        with open(self.dir / "trades.jsonl", "a") as f:
            f.write(json.dumps({"date": self.clock().date().isoformat(), "symbol": sym, "side": "short" if side == -1 else "long", "qty": qty,
                                "entry": entry, "exit": px, "pnl": round(pnl, 2), "r": round(pnl / risk, 3) if risk else 0.0,
                                "reason": reason, "opened": pos["opened"]}) + "\n")

    def reconcile(self):
        """After a restart: forget virtual positions the broker no longer holds."""
        held = retry_call(self.broker.positions, sleep=self.sleep)
        for sym in [s for s in self.exits.positions if s not in held]:
            self.exits.positions.pop(sym)
            self.exec.opened.pop(sym, None)
            self.journal("reconcile_drop", symbol=sym)
        self.store.save(self.exits.positions)

    def poll_exits(self, now_et: datetime):
        """Virtual stop / trail / take-profit, run every ~15 s on live quotes."""
        self.now = now_et
        for sym in list(self.exits.positions):
            bid, ask = retry_call(self.broker.quote, sym, sleep=self.sleep)
            pos = self.exits.positions.get(sym)
            px = bid if pos.get("side", 1) == 1 else ask
            self.last_px[sym] = px
            reason = self.exits.on_quote(sym, bid, ask)       # closes only our qty (OwnBook.close)
            if reason:
                self.exec.opened.pop(sym, None)
                self.record_exit(sym, px, reason, pos)
                self.last_exit[sym] = bar_start(now_et)

    def refresh_heat(self, now_et: datetime):
        """Sector & market heat map, once a day from completed daily bars."""
        if self.heat_day == now_et.date():
            return
        daily = {}
        for etf in gates.HEAT_ETFS:
            try:
                d = self.raw.daily(etf) if hasattr(self.raw, "daily") else None
                daily[etf] = d if d is not None and len(d) else None      # state_on() only reads closes before today
            except Exception:
                log.exception("heat map: no daily bars for %s (treated as NEUTRAL)", etf)
        self.heat, self.heat_day = gates.heat_map(daily), now_et.date()
        self.journal("heat_map", **{k: gates.state_on(v, now_et.date()) for k, v in self.heat.items()})

    def can_short(self, sym: str) -> str:
        """'' when shorting sym is allowed; otherwise the reason."""
        held = retry_call(self.broker.positions, sleep=self.sleep).get(sym, 0)
        if held:                                    # another bot holds sym: a sell would cut THEIR long, not open our short
            return "another bot holds this symbol"
        if sym not in self.borrow:
            try:
                self.borrow[sym] = bool(self.raw.shortable(sym)) if hasattr(self.raw, "shortable") else False
            except Exception:
                self.borrow[sym] = False
        return "" if self.borrow[sym] else "not easy to borrow"

    def close_now(self, sym: str, reason: str, exit_bar):
        pos = self.exits.positions.get(sym)
        bid, ask = retry_call(self.broker.quote, sym, sleep=self.sleep)
        px = bid if pos.get("side", 1) == 1 else ask
        retry_call(self.broker.close, sym, sleep=self.sleep)          # our qty only
        self.record_exit(sym, px, reason, pos)
        self.exits.positions.pop(sym, None)
        self.exec.opened.pop(sym, None)
        self.store.save(self.exits.positions)
        self.last_exit[sym] = exit_bar

    def bar_exits(self, sym: str, day, now_et: datetime):
        """At a bar close: the signal's time stop (max_bars) and the expert's own exit rule (exit_col), in the backtest's order."""
        pos = self.exits.positions[sym]
        held = int((day.index >= pd.Timestamp(pos["entry_bar"])).sum())
        if held < 1:
            return
        if pos.get("max_bars") and held >= pos["max_bars"]:
            self.close_now(sym, "time", day.index[-1])
            return
        col = pos.get("exit_col") or ""
        col = col if pos.get("side", 1) == 1 or not col else "m_" + col
        if col and col in day and bool(day[col].iloc[-1]):
            self.close_now(sym, "signal", day.index[-1] + pd.Timedelta(minutes=5))      # "exit" decided at this close, filled at the next open

    def on_bar_close(self, now_et: datetime):
        """Once per new 5-minute bar: exits that are decided at a bar close, then entries (long, or short when the gate allows)."""
        self.now = now_et
        self.refresh_heat(now_et)
        if self.day != now_et.date():
            self.day, self.trades_today, self.last_exit = now_et.date(), {}, {}
        spy_day = None
        for sym in sorted(self.symbols, key=lambda s: s != gates.MARKET):      # SPY first: its 'red' state feeds the other symbols
            day = today_frame(sym, now_et, self.brain, self.fetch)
            if sym == gates.MARKET:
                spy_day = day
            if day is None or day.index[-1] == self.last_bar.get(sym):
                continue
            self.last_bar[sym] = day.index[-1]
            gates.apply(day, sym, self.heat, spy_day)
            if sym in self.exits.positions:
                self.bar_exits(sym, day, now_et)
                if sym in self.exits.positions:
                    continue
            sig = next((s for s in self.brain.signals(day) if s.i == len(day) - 1), None)
            if not sig:
                continue
            if self.trades_today.get(sym, 0) >= self.brain.max_trades:
                continue
            if sym in self.last_exit and day.index[-1] < self.last_exit[sym] + pd.Timedelta(minutes=5 * self.brain.cooldown):
                continue
            ref = float(day["close"].iloc[-1])
            if sig.side == -1:
                why = self.can_short(sym)
                if why:
                    self.journal("signal", symbol=sym, side="short", tag=sig.tag, ref=ref, result=why)
                    continue
            elif retry_call(self.broker.positions, sleep=self.sleep).get(sym, 0) < 0:
                self.journal("signal", symbol=sym, side="long", tag=sig.tag, ref=ref, result="another bot is short this symbol")
                continue
            verdict = self.exec.try_enter(sym, sig, ref, now_et)       # risk 1% of equity, spread guard, window, breaker, duplicate-id guard
            self.journal("signal", symbol=sym, side="short" if sig.side == -1 else "long", tag=sig.tag, ref=ref, result=verdict)
            if verdict == "ordered":
                bid, ask = retry_call(self.broker.quote, sym, sleep=self.sleep)
                qty = self.exec.last_qty
                fill = ask if sig.side == 1 else bid
                got = self.confirm_fill(self.exec.last_order)
                if got is not None:
                    status, filled_qty, price = got
                    if status != "filled" or filled_qty <= 0:
                        self.exec.opened.pop(sym, None)
                        self.journal("entry_not_filled", symbol=sym, status=status, filled_qty=filled_qty)
                        continue
                    qty, fill = filled_qty, price
                    ref_px = ask if sig.side == 1 else bid
                    slip_bps = (price - ref_px) / ref_px * 1e4 * sig.side         # + = we paid more / sold for less than the quote
                    self.journal("fill", symbol=sym, side=sig.side, qty=filled_qty, quote=ref_px, fill=price, slip_bps=round(slip_bps, 2))
                self.exits.open_signal(sym, qty, fill, sig, now_et, (day.index[-1] + pd.Timedelta(minutes=5)).isoformat())
                self.entries_today += 1
                self.trades_today[sym] = self.trades_today.get(sym, 0) + 1

    def confirm_fill(self, order, tries: int = 6):
        """(status, filled qty, average fill price) from the broker, or None when the broker gives no order record (simulations).
        A market order on these names fills in well under a second; a rejected or still-unfilled order never becomes a position."""
        if not isinstance(order, dict) or not order.get("id") or not hasattr(self.raw, "order"):
            return None
        final = ("filled", "rejected", "canceled", "expired")
        o = order
        for _ in range(tries):
            if o.get("status") in final:
                break
            self.sleep(0.5)
            try:
                o = self.raw.order(order["id"])
            except Exception:
                log.exception("order status check failed")
        if o.get("status") not in final and hasattr(self.raw, "cancel_order"):
            # still working (partly filled or not yet filled): cancel the rest so no shares can arrive later untracked, then read the final fill
            try:
                self.raw.cancel_order(order["id"])
            except Exception:
                log.exception("cancel of the unfilled remainder failed")
            for _ in range(6):
                self.sleep(0.5)
                try:
                    o = self.raw.order(order["id"])
                except Exception:
                    log.exception("order status check failed")
                if o.get("status") in final:
                    break
        qty = int(float(o.get("filled_qty") or 0))
        px = float(o.get("filled_avg_price") or 0)
        status = "filled" if qty > 0 else o.get("status", "unknown")
        return status, qty, px

    WEEKLY_LIMIT = 0.05      # own equity 5% below its peak of the last 7 days -> no new entries until a person deletes KILL_SWITCH

    def weekly_guard(self, now_et: datetime):
        """Weekly loss lock: compares this bot's own equity with the peak of its last 7 days of end-of-day snapshots."""
        if (self.dir / "KILL_SWITCH").exists():
            return
        snaps = [e for e in _jl(self.dir / "equity.jsonl") if e.get("date", "") >= (now_et.date() - timedelta(days=7)).isoformat()]
        if not snaps:
            return
        peak = max(e["equity"] for e in snaps)
        eq = self.own_equity()
        if eq < peak * (1 - self.WEEKLY_LIMIT):
            msg = f"weekly loss lock: own equity ${eq:,.0f} is {1 - eq / peak:.1%} below this week's peak ${peak:,.0f}"
            (self.dir / "KILL_SWITCH").write_text(msg + "\nDelete this file to resume new entries.\n")
            self.journal("weekly_lock", equity=round(eq, 2), peak=peak)
            send_telegram(f"ALPACA PAPER (intraday): {msg}. New entries stopped; open trades keep their exits. "
                          f"Resume: delete /opt/marv-bot/state/alpaca_paper/KILL_SWITCH")

    def check_session(self, now_et: datetime):
        """Kill switch file and today's real close (early-close days), from the broker's clock."""
        self.weekly_guard(now_et)
        killed = (self.dir / "KILL_SWITCH").exists()
        if killed != self.exec.kill_switch:
            self.exec.kill_switch = killed
            self.journal("kill_switch", on=killed)
            send_telegram(f"ALPACA PAPER (intraday): kill switch {'ON - no new entries' if killed else 'OFF - entries resume'}")
        if getattr(self, "_close_day", None) != now_et.date() and hasattr(self.raw, "clock"):
            try:
                nc = self.raw.clock().get("next_close")
                if nc:
                    close = pd.Timestamp(nc).tz_convert(ET).to_pydatetime()
                    self.exec.window.session_close = close
                    self._close_day = now_et.date()
                    if close.date() == now_et.date() and close.time() < dtime(16, 0):
                        self.journal("early_close", close=close.isoformat())
            except Exception:
                log.exception("clock check failed (keeping the regular 3:50 flatten)")

    def tick(self, now_et: datetime):
        self.now = now_et
        self.check_session(now_et)
        snapshot = dict(self.exits.positions)
        for action in self.exec.on_tick(now_et):
            self.journal("housekeeping", action=action)
            if "breaker" in action:
                send_telegram(f"ALPACA PAPER (intraday): daily circuit breaker tripped - own equity fell "
                              f"{self.exec.breaker.limit:.0%} today. Flattened; no new entries for {self.exec.breaker.lock_hours:.0f} h.")
            if "flatten" in action or "breaker" in action:      # OwnBook.flatten_all already recorded each exit
                self.exits.positions.clear()
            elif action.startswith("stale exit "):
                sym = action.split()[-1]
                if sym in snapshot:
                    self.record_exit(sym, self.last_px.get(sym, self.entry_px(snapshot[sym])), "stale", snapshot[sym])
                    self.exits.positions.pop(sym, None)
        self.store.save(self.exits.positions)
        self.poll_exits(now_et)

    def was_session(self, day) -> bool:
        """Was `day` a trading session? From the broker's calendar (survives restarts), else from what this process saw."""
        if day in self.traded_days:
            return True
        if day not in self._session_cache and hasattr(self.raw, "is_session"):
            try:
                self._session_cache[day] = bool(self.raw.is_session(day))
            except Exception:
                return False
        return self._session_cache.get(day, False)

    def end_of_day(self, now_et: datetime):
        """Snapshot equity and SPY, then send the daily report card (Telegram when configured, always saved to disk)."""
        try:
            bid, ask = self.broker.quote("SPY")
            snap = {"date": now_et.date().isoformat(), "equity": round(self.own_equity(), 2), "account_equity": self.raw.equity(), "spy": round((bid + ask) / 2, 2)}
            with open(self.dir / "equity.jsonl", "a") as f:
                f.write(json.dumps(snap) + "\n")
            text = report_card(self.dir, now_et.date())
            (self.dir / "report_card.txt").write_text(text)
            send_telegram(text)
            self.reported = now_et.date()
            self.report_marker.write_text(now_et.date().isoformat())       # on disk: a restart cannot send it twice or skip it
        except Exception:
            log.exception("end-of-day report failed (continuing)")

    def run(self):
        log.info("paper runner started: %s", self.symbols)
        self.reconcile()
        last_slot = None
        while True:
            now = datetime.now(ET)
            try:
                if now.time() >= dtime(16, 5) and self.reported != now.date() and self.was_session(now.date()):
                    self.end_of_day(now)                # after the bell the clock says closed, so the card is sent before that check
                if now.weekday() >= 5 or not self.broker.clock_open():
                    self.sleep(60)
                    continue
                self.tick(now)
                self.traded_days.add(now.date())
                slot = (now.hour, now.minute // 5)
                if slot != last_slot and now.second >= 5:       # give the feed a few seconds to publish the closed bar
                    last_slot = slot
                    self.on_bar_close(now)
            except Exception:
                log.exception("paper loop error (continuing)")
            self.sleep(15)


GATE = {"days": 30, "trades": 100, "profit_factor": 1.3, "max_dd": 0.10}


def _jl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def report_card(state_dir, day=None) -> str:
    """Win/loss, ROI, drawdown, SPY comparison and the go-live gate, from the trade ledger and the daily equity snapshots."""
    d = Path(state_dir)
    trades, eq = _jl(d / "trades.jsonl"), _jl(d / "equity.jsonl")
    day = (day or datetime.now(ET).date()).isoformat()
    today = [t for t in trades if t["date"] == day]
    def stats(ts):
        w = [t for t in ts if t["pnl"] > 0]
        gl = -sum(t["pnl"] for t in ts if t["pnl"] <= 0)
        return len(ts), len(w), len(ts) - len(w), (len(w) / len(ts) if ts else 0.0), (sum(t["pnl"] for t in w) / gl if gl else float("inf") if w else 0.0), (sum(t["r"] for t in ts) / len(ts) if ts else 0.0)
    n, w, l, wr, pf, ar = stats(today)
    N, W, L, WR, PF, AR = stats(trades)
    lines = ["ALPACA PAPER REPORT CARD " + day, "PAPER account only. Nothing here is a live result. Figures are this bot's own trades only (the account is shared with your other bots).", ""]
    lines.append(f"Today: {n} trades, {w}W/{l}L ({wr:.0%}), P&L ${sum(t['pnl'] for t in today):+,.0f}")
    days = len(eq)
    gate = []
    if len(eq) >= 1:
        start, cur = eq[0]["equity"], eq[-1]["equity"]
        roi = cur / start - 1
        dayret = (eq[-1]["equity"] / eq[-2]["equity"] - 1) if len(eq) > 1 else 0.0
        peak, dd = start, 0.0
        for e in eq:
            peak = max(peak, e["equity"])
            dd = max(dd, 1 - e["equity"] / peak)
        spy = eq[-1]["spy"] / eq[0]["spy"] - 1 if eq[0].get("spy") else 0.0
        avg_daily = ((cur / start) ** (1 / max(days - 1, 1)) - 1) if days > 1 else 0.0
        lines += [f"Equity ${cur:,.0f}  day {dayret:+.2%}",
                  f"ROI since start ({eq[0]['date']}): {roi:+.2%} over {days} days   SPY {spy:+.2%}   avg daily {avg_daily:+.2%} (goal 0.75-1.0%)",
                  f"Max drawdown {dd:.1%}"]
        gate = [("30+ trading days", days >= GATE["days"], f"{days}"), ("100+ trades", N >= GATE["trades"], f"{N}"),
                ("net ROI > 0", roi > 0, f"{roi:+.2%}"), ("profit factor >= 1.3", PF >= GATE["profit_factor"], f"{PF:.2f}"),
                ("max drawdown <= 10%", dd <= GATE["max_dd"], f"{dd:.1%}"), ("beats SPY", roi > spy, f"{roi:+.2%} vs {spy:+.2%}"),
                ("avg daily >= 0.75%", avg_daily >= 0.0075, f"{avg_daily:+.2%}")]
    lines += ["", f"All trades: {N}, {W}W/{L}L ({WR:.0%}), profit factor {PF:.2f}, avg R {AR:+.2f}"]
    for side in ("long", "short"):
        st = [t for t in trades if t.get("side", "long") == side]
        if st:
            n_, w_, l_, wr_, pf_, ar_ = stats(st)
            lines.append(f"  {side}s: {n_}, {w_}W/{l_}L ({wr_:.0%}), P&L ${sum(t['pnl'] for t in st):+,.0f}, profit factor {pf_:.2f}")
    by = {}
    for t in trades:
        by.setdefault(t["symbol"], 0.0)
        by[t["symbol"]] += t["pnl"]
    if by:
        best = sorted(by.items(), key=lambda x: -x[1])
        shown = best if len(best) <= 5 else best[:3] + best[-2:]
        lines.append("By symbol: " + ", ".join(f"{k} ${v:+,.0f}" for k, v in shown))
    if gate:
        lines += ["", "GO-LIVE GATE (every line must pass, then it is your decision):"] + [f"  {'PASS' if ok else 'wait'}  {name}: {val}" for name, ok, val in gate]
        lines.append("GATE MET: eligible to consider a small live test." if all(ok for _, ok, _ in gate) else "GATE NOT MET: keep paper trading.")
    else:
        lines.append("No equity snapshots yet; the first appears after today's close.")
    from .swing_paper import summary as swing_summary
    lines += swing_summary(Path(state_dir).parent / "alpaca_swing")
    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    import os
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not tok or not chat:
        return False
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text[:4000]}, timeout=20)
    return r.ok


def report(state_dir="state/alpaca_paper") -> str:
    return report_card(state_dir)


def main(a) -> int:
    from .data import credentials
    if not all(credentials()):
        print("WAITING FOR KEYS: add the paper account's key id and secret to /opt/marv-bot/.env")
        return 2
    setup_logging(Path(a.state) / "paper.log")
    PaperRunner(RestBroker(), state_dir=a.state).run()
    return 0
