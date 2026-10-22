"""Paper-trading runner: the backtest's Brain decides, Alpaca's PAPER account fills, the virtual exit manager closes.

PAPER ONLY. The trading endpoint is hard-wired to paper-api.alpaca.markets; there is no setting for a live account.
Nothing here is validated yet: this runner exists to collect real forward results while the backtests run. Every decision and fill is
appended to state/alpaca_paper/journal.jsonl so the forward record can be compared with the backtest later (`python -m alpacabot paper-report`).
"""

import json
import logging
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .brain import Brain
from .data import NY, _fetch, _headers
from .engine import resample_bars
from .exits import StateStore
from .execution import CircuitBreaker, EntryWindow, Executor, ProfitLock, VirtualExitManager, retry_call, setup_logging, spread_ok
from .experts import ALL_EXPERTS, features_both
from . import gates, universe

ET = ZoneInfo(NY)
TRADE_URL = "https://paper-api.alpaca.markets/v2"
DATA_URL = "https://data.alpaca.markets/v2"
SYMBOLS = ["SPY", "QQQ", "IWM"] + universe.STOCKS          # the 5 largest S&P 500 stocks in each of the 11 sectors (universe.py)
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
            raise requests.HTTPError(f"{r.status_code} {r.text[:200]} for {method} {url}", response=r)      # (with Alpaca's reason)
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

    def submit_oco(self, symbol: str, qty: int, limit_price: float, stop_price: float, client_order_id: str, side: str = "sell") -> dict:
        """Exit bracket for a held position (side "sell" for a long, "buy" for a short): a GTC take-profit limit and a GTC stop,
        one-cancels-other (Alpaca order_class "oco")."""
        body = {"symbol": symbol, "qty": str(qty), "side": side, "type": "limit", "time_in_force": "gtc", "order_class": "oco",
                "client_order_id": client_order_id, "take_profit": {"limit_price": f"{limit_price:.2f}"},
                "stop_loss": {"stop_price": f"{stop_price:.2f}"}}
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
        return self._req("GET", f"{TRADE_URL}/orders/{order_id}", params={"nested": "true"}).json()    # nested: OCO legs included

    def positions(self) -> dict[str, int]:
        """symbol -> signed quantity (negative = short)."""
        return {p["symbol"]: abs(int(float(p["qty"]))) * (-1 if p.get("side") == "short" else 1) for p in self._req("GET", f"{TRADE_URL}/positions").json()}

    def shortable(self, symbol: str) -> bool:
        a = self._req("GET", f"{TRADE_URL}/assets/{symbol}").json()
        return bool(a.get("shortable")) and bool(a.get("easy_to_borrow"))

    def daily_history(self, symbol: str, days: int = 220):
        """Completed daily bars from the consolidated (SIP) history; the free plan serves it up to 15 minutes ago."""
        now = datetime.now(timezone.utc) - timedelta(minutes=20)
        return _fetch(symbol, "1Day", (now - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z"), now.strftime("%Y-%m-%dT%H:%M:%SZ"), "sip")

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
    for sym in universe.STOCKS:                          # each stock rates its own sector ETF in the heat map
        gates.add_sector(sym, universe.SECTOR_OF[sym])
    if ENGAGED.exists():
        raw = json.loads(ENGAGED.read_text())
        for sym, etf in (raw.get("sectors") or {}).items():
            gates.add_sector(sym, etf)
        syms += [x for x in raw.get("symbols", []) if x not in syms]
    return syms


class PaperRunner:
    def __init__(self, broker, brain: Brain | None = None, state_dir="state/alpaca_paper", risk_pct=0.01, symbols=None, fetch=_fetch, sleep=time.sleep,
                 breaker_limit: float = 0.02, max_positions: int = 6, profit_arm: float = 0.005, profit_keep: float = 0.5):
        self.broker, self.brain, self.symbols, self.fetch, self.sleep = broker, brain or default_brain(state_dir), symbols or engaged_symbols(), fetch, sleep
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store = StateStore(self.dir / "positions.json")
        self.raw = broker
        self.broker = OwnBook(broker, self)
        self.exits = VirtualExitManager(self.store, self.broker, sleep=sleep)
        # no 45-minute stale exit: each signal carries the backtest's own time stop (max_bars), checked at every bar close
        self.exec = Executor(self.broker, EntryWindow(), CircuitBreaker(limit=breaker_limit), risk_pct=risk_pct, max_positions=max_positions,
                             stale_minutes=10 ** 9, sleep=sleep, max_spread_pct=0.001,       # relative guard: 0.1% of price
                             profit_lock=ProfitLock(arm=profit_arm, keep=profit_keep))
        self.last_px: dict[str, float] = {}      # last exit-side price: bid for longs, ask for shorts
        self.heat, self.heat_day, self.borrow = {}, None, {}
        self.day, self.trades_today, self.last_exit = None, {}, {}
        self.last_exit_by = {}          # (symbol, expert) -> last exit time (owner 2026-10-22: same expert waits before the same stock)
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
        self.restore_day_guards()

    def day_guards(self) -> dict:
        b, pl = self.exec.breaker, self.exec.profit_lock
        return {"day": str(b.day) if b.day else None, "day_start_equity": b.day_start_equity,
                "locked_until": b.locked_until.isoformat() if b.locked_until else None,
                **({"pl_day": str(pl.day) if pl.day else None, "pl_start": pl.start, "pl_peak_gain": pl.peak_gain,
                    "pl_locked_day": str(pl.locked_day) if pl.locked_day else None} if pl else {})}

    def save_day_guards(self):
        """The daily breaker and the profit lock survive a restart (a deploy mid-session must not unlock a locked day or move its
        opening-equity baseline)."""
        g = self.day_guards()
        if g != getattr(self, "_guards_saved", None):
            (self.dir / "day_guards.json").write_text(json.dumps(g))
            self._guards_saved = g

    def restore_locks_from_journal(self):
        """No day_guards.json yet (first run of this version): today's profit lock / breaker from the journal, so a restart can't undo them."""
        p, today = self.dir / "journal.jsonl", datetime.now(ET).date().isoformat()
        if not p.exists():
            return
        with open(p) as f:
            for line in f:
                if not line.startswith('{"t": "' + today) or '"housekeeping"' not in line:
                    continue
                act = json.loads(line).get("action", "")
                if act.startswith("profit lock") and self.exec.profit_lock:
                    self.exec.profit_lock.locked_day = date.fromisoformat(today)
                elif "breaker" in act:
                    self.exec.breaker.locked_until = datetime.fromisoformat(json.loads(line)["t"]) + timedelta(hours=self.exec.breaker.lock_hours)

    def restore_day_guards(self):
        p = self.dir / "day_guards.json"
        try:
            g = json.loads(p.read_text()) if p.exists() else None
        except Exception:
            log.exception("day_guards.json unreadable")
            g = None
        if not g:
            self.restore_locks_from_journal()
            return
        b, pl = self.exec.breaker, self.exec.profit_lock
        if g.get("day"):
            b.day, b.day_start_equity = date.fromisoformat(g["day"]), float(g["day_start_equity"])
        if g.get("locked_until"):
            b.locked_until = datetime.fromisoformat(g["locked_until"])
        if pl and g.get("pl_day"):
            pl.day, pl.start, pl.peak_gain = date.fromisoformat(g["pl_day"]), float(g["pl_start"]), float(g["pl_peak_gain"])
            pl.locked_day = date.fromisoformat(g["pl_locked_day"]) if g.get("pl_locked_day") else None
        self._guards_saved = g

    def journal(self, kind: str, **kw):
        now = self.clock()
        line = json.dumps({"t": now.isoformat(timespec="seconds"), "kind": kind, **kw}, default=str) + "\n"
        with open(self.dir / "journal.jsonl", "a") as f:
            f.write(line)
        with open(self.dir / f"journal-{now:%Y%m%d}.jsonl", "a") as f:        # one small file per day (the VM link can only read a file's start)
            f.write(line)

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
        self.last_exit_by[(sym, str(pos.get("expert")))] = self.clock()
        qty, side, entry = pos["qty"], pos.get("side", 1), self.entry_px(pos)
        risk = pos["state"].atr * pos["state"].trail * qty
        pnl = side * (px - entry) * qty
        best = pos.get("best_px")
        peak = max(side * ((best if best is not None else px) - entry) * qty, pnl, 0.0)    # peak open profit ($), never below the result
        z = pos.get("zone") or {}
        extreme = px if best is None else (max(px, best) if side == 1 else min(px, best))     # furthest price reached in the trade's favour
        z_hit = bool(z.get("target") is not None and side * (extreme - z["target"]) >= 0)
        self.journal("exit", symbol=sym, reason=reason, px=px, side=side)
        with open(self.dir / "trades.jsonl", "a") as f:
            f.write(json.dumps({"date": self.clock().date().isoformat(), "symbol": sym, "side": "short" if side == -1 else "long", "qty": qty,
                                "entry": entry, "exit": px, "pnl": round(pnl, 2), "r": round(pnl / risk, 3) if risk else 0.0,
                                "reason": reason, "opened": pos["opened"], "closed": self.clock().isoformat(), "expert": pos.get("expert"),
                                "peak_r": round(peak / risk, 3) if risk else 0.0,
                                "mae_r": round(max(side * (entry - (pos.get("worst_px") if pos.get("worst_px") is not None else px)) * qty, 0.0) / risk, 3)
                                if risk else 0.0, **({"ver": pos["ver"]} if pos.get("ver") else {}),
                                **({"zone_chase": bool(z.get("chase")), "zone_hit": z_hit, "zone_profile": z.get("profile"),
                                    "zone_pnl": round(side * (z["target"] - entry) * qty, 2) if z_hit else None} if z else {}),
                                **({"bias": z["bias"], "against": bool(z["against"]), "map_target": z.get("map_target"), "stage": z.get("stage")} if "bias" in z else {})}) + "\n")

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
            side = pos.get("side", 1)                         # best exit-side price seen while open (peak open profit)
            pos["best_px"] = px if pos.get("best_px") is None else (max(pos["best_px"], px) if side == 1 else min(pos["best_px"], px))
            pos["worst_px"] = px if pos.get("worst_px") is None else (min(pos["worst_px"], px) if side == 1 else max(pos["worst_px"], px))
            lad = pos.get("ladder")
            if lad:
                self.ladder_add(sym, pos, bid, ask, now_et)   # optional scale-in tiers (owner's current plan: one 1%-risk entry, none)
                if self.ladder_cut(sym, pos, px, now_et):     # against the trade: sell 25%, then 50%, the rest at the stop
                    continue
            neg_min = self.zone_config().get("neg_exit_min")
            if neg_min:                                       # owner 2026-10-22: under water (mid vs the mid at entry) for neg_exit_min minutes in a row -> out
                mid, mid0 = (bid + ask) / 2, abs(self.entry_px(pos)) - side * (ask - bid) / 2   # fill less half the spread
                if side * (mid - mid0) < 0:
                    since = pos.get("neg_since")
                    if not since:
                        pos["neg_since"] = now_et.isoformat()
                        self.store.save(self.exits.positions)  # the 10-minute clock survives a restart
                    elif (now_et - datetime.fromisoformat(since)).total_seconds() >= 60 * float(neg_min):
                        self.journal("neg_exit", symbol=sym, minutes=float(neg_min), mid=round(mid, 4), entry_mid=round(mid0, 4))
                        self.close_now(sym, "neg_exit", bar_start(now_et) + pd.Timedelta(minutes=5))
                        continue
                elif pos.get("neg_since"):
                    pos["neg_since"] = None
                    self.store.save(self.exits.positions)
            tp = pos.get("zone_tp")
            if tp is not None and side * (px - tp) >= 0:     # LIVE take-profit zone reached
                if lad and self.ladder_take(sym, pos, px, now_et):    # scaled out: 25% at the zone, 50% beyond; the rest rides the trend
                    continue
                if not lad:
                    self.close_now(sym, "zone", bar_start(now_et) + pd.Timedelta(minutes=5))
                    continue
            if sym not in self.exits.positions:
                continue
            if lad and lad["cfg"].get("trail_atr") and lad.get("tp_steps", 0) >= len(lad["cfg"]["tp_fracs"]):
                # owner: what is left rides the trend, followed by a virtual stop for safety (trail_atr x ATR behind the best price)
                self.lock_stop(sym, pos, pos["best_px"] - side * lad["cfg"]["trail_atr"] * lad["atr"], quiet=True)
            if lad and lad["cfg"].get("trail_from_entry_r") and side * (pos["best_px"] - lad["fill"]) > 0 and \
                    side * (pos["best_px"] - lad["fill"]) >= lad["cfg"].get("trail_start_r", 0.0) * abs(lad["fill"] - lad["stop_px"]):   # trail / stair-step
                self.lock_stop(sym, pos, pos["best_px"] - side * lad["cfg"]["trail_from_entry_r"] * abs(lad["fill"] - lad["stop_px"]), quiet=True)
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
        self.refresh_zonemap(now_et)
        self.journal("heat_map", **{k: gates.state_on(v, now_et.date()) for k, v in self.heat.items()})

    def zone_config(self) -> dict:
        """Live zone settings: <state>/zones.json (written with the defaults on first use; the 2-hour optimizer edits it). LIVE by the
        owner's decision 2026-10-14 (forward-tested instead of backtest-gated)."""
        from .engine import ZONE_PROFILES
        p = self.dir / "zones_override.json"
        default = {"live": True, "direction_first": True, "use_map": True, "profiles": ZONE_PROFILES}
        try:
            repo = Path(__file__).with_name("zones.json")              # source of truth, edited by the optimizer and deployed
            cfg = {**default, **(json.loads(repo.read_text()).get("intraday", {}) if repo.exists() else {})}
            if p.exists():                                             # a local override in the state folder wins (manual switch-off)
                cfg.update(json.loads(p.read_text()))
            else:
                p.write_text(json.dumps({"_note": "local override (e.g. {\"live\": false} to switch zones off here); otherwise alpacabot/zones.json rules"}, indent=1))
            prof = cfg["profiles"]
            cfg["profiles"] = {k: {**ZONE_PROFILES[k], **prof.get(k, {})} for k in ZONE_PROFILES}
            for k, v in prof.items():                                  # per-expert zones (trend experts' own philosophy)
                if k not in ZONE_PROFILES and isinstance(v, dict):
                    cfg["profiles"][k] = {**ZONE_PROFILES["trend"], **v}
            vh = cfg.get("velez_hybrid") or {}
            if vh.get("live") and vh.get("ladder") and isinstance(cfg.get("ladder"), dict):   # the win-rate tune: one take-profit, no cuts
                cfg["ladder"] = {**cfg["ladder"], "by_expert": {**(cfg["ladder"].get("by_expert") or {}),
                                                                **{n: dict(vh["ladder"]) for n in vh.get("experts", [])}}}
            return cfg
        except Exception:
            log.exception("zones.json unreadable: zones off until fixed")
            return {**default, "live": False}

    def ladder_tier(self, sym: str, sig, ref: float, lad: dict) -> int:
        """Entry tier from how far the quote already is INTO the entry zone (against the signal's direction from its close), in ATRs."""
        bid, ask = retry_call(self.broker.quote, sym, sleep=self.sleep)
        px = ask if sig.side == 1 else bid
        atr_i = sig.atr if sig.atr > 0 else sig.stop_dist / max(sig.trail, 1e-9)
        depth = (-1 if sig.side == -1 else 1) * (ref - px) / atr_i if atr_i > 0 else 0.0
        return max(k for k, d in enumerate(lad["entry_depth_atr"]) if depth >= d or k == 0)

    def ladder_add(self, sym: str, pos: dict, bid: float, ask: float, now_et: datetime):
        """Scale in: when price goes one tier deeper into the entry zone (and is still beyond the stop), add shares so the trade's total
        risk reaches that tier's % of equity (capped by the per-position notional cap). Entry window, breaker, profit lock and the kill
        switch all apply to adds exactly as to new trades."""
        lad, side = pos["ladder"], pos.get("side", 1)
        cfg, k = lad["cfg"], lad["tier"] + 1
        if k >= len(cfg["entry_risk"]) or lad.get("sold"):
            return
        ex = self.exec
        if ex.kill_switch or not ex.window.entries_allowed(now_et) or not ex.breaker.entries_allowed(now_et) or \
                (ex.profit_lock and not ex.profit_lock.entries_allowed(now_et)):
            return
        px = ask if side == 1 else bid
        depth = side * (lad["ref"] - px) / lad["atr"] if lad["atr"] > 0 else 0.0
        if depth < cfg["entry_depth_atr"][k] or side * (px - lad["stop_px"]) <= 0:
            return
        eq, entry, qty = self.own_equity(), self.entry_px(pos), pos["qty"]
        dist = abs(px - lad["stop_px"])
        add = int((eq * cfg["entry_risk"][k] - qty * abs(entry - lad["stop_px"])) / dist) if dist > 0 else 0
        add = min(add, int(eq * ex.max_pos_frac / px) - qty)
        lad["tier"] = k                                          # one step per tier, even when the cap leaves nothing to add
        if add < 1:
            self.store.save(self.exits.positions)
            return
        coid = f"ab-a-{sym}-{now_et:%Y%m%d}-{k}"
        order = self.broker.submit_market(sym, add, coid, side="buy" if side == 1 else "sell")
        got = self.confirm_fill(order)
        filled, fpx = (add, px) if got is None else (got[1], got[2] or px)
        if got is not None and (got[0] != "filled" or filled <= 0):
            self.journal("ladder_add_not_filled", symbol=sym, tier=k, status=got[0])
            self.store.save(self.exits.positions)
            return
        new_avg = (qty * entry + filled * fpx) / (qty + filled)
        pos["state"].entry = side * new_avg                      # shorts are tracked on -price
        pos["qty"], lad["peak_qty"] = qty + filled, qty + filled
        self.journal("ladder_add", symbol=sym, tier=k, risk_pct=cfg["entry_risk"][k], added=filled, fill=fpx, avg=round(new_avg, 4))
        self.store.save(self.exits.positions)

    def ladder_take(self, sym: str, pos: dict, px: float, now_et: datetime) -> bool:
        """Scale out in the take-profit zone (owner's plan, alpacabot/zones.json "ladder"): tp_fracs of the peak position at tp_depth_atr ATRs
        beyond the zone (25% at the zone, 50% in all a little beyond). After each step the virtual stop is locked (lock: "breakeven" = the
        entry, "tp" = the take-profit zone itself) and what is left rides the trend. Returns True when the position is fully closed."""
        lad, side = pos["ladder"], pos.get("side", 1)
        cfg = lad["cfg"]
        beyond = side * (px - pos["zone_tp"]) / lad["atr"] if lad["atr"] > 0 else 99.0
        steps = [k for k, d in enumerate(cfg["tp_depth_atr"]) if beyond >= d]
        if not steps:
            return False
        k = max(steps)
        if self.scale_out(sym, pos, px, now_et, cfg["tp_fracs"][k], f"zone{int(cfg['tp_fracs'][k] * 100)}"):
            return True                                          # (None = nothing left to sell at this step: the stop still locks)
        locks = cfg.get("lock") or []
        for step in range(lad.get("tp_steps", 0), k + 1):
            how = locks[step] if step < len(locks) else None
            level = self.entry_px(pos) if how == "breakeven" else pos["zone_tp"] if how == "tp" else None
            if level is not None:
                self.lock_stop(sym, pos, level)
        lad["tp_steps"] = k + 1
        self.store.save(self.exits.positions)
        return False

    def ladder_cut(self, sym: str, pos: dict, px: float, now_et: datetime) -> bool:
        """Against the trade, inside the sell zone: cut_fracs of the peak position once price is cut_depth_r of the way to the stop
        (25% at half way, 50% in all at three quarters); the stop itself takes the rest. Returns True when nothing is left."""
        lad, side = pos["ladder"], pos.get("side", 1)
        cfg = lad["cfg"]
        if not cfg.get("cut_fracs"):
            return False
        risk = abs(lad["fill"] - lad["stop_px"]) if "fill" in lad else abs(self.entry_px(pos) - lad["stop_px"])
        depth = side * (lad.get("fill", self.entry_px(pos)) - px) / risk if risk > 0 else 0.0
        steps = [k for k, d in enumerate(cfg["cut_depth_r"]) if depth >= d]
        if not steps:
            return False
        frac = cfg["cut_fracs"][max(steps)]
        return bool(self.scale_out(sym, pos, px, now_et, frac, f"cut{int(frac * 100)}"))

    def scale_out(self, sym: str, pos: dict, px: float, now_et: datetime, frac: float, reason: str):
        """Sell down to (1 - frac) of the peak position (fractions are cumulative and shared by the cut and take-profit ladders).
        None = nothing to sell, True = position closed, False = partly sold."""
        lad = pos["ladder"]
        sell = min(int(round(lad["peak_qty"] * frac)) - lad["sold"], pos["qty"])
        if sell < 1:
            return None
        if sell >= pos["qty"]:
            self.close_now(sym, reason, bar_start(now_et) + pd.Timedelta(minutes=5))
            return True
        if hasattr(self.raw, "close_tagged"):
            self.raw.close_tagged(sym, sell, pos.get("side", 1), f"ab-p-{sym}-{int(time.time() * 1000)}")
        else:
            self.raw.close(sym, sell)
        self.record_partial(sym, pos, px, sell, reason)
        pos["qty"] -= sell
        lad["sold"] += sell
        self.store.save(self.exits.positions)
        return False

    def lock_stop(self, sym: str, pos: dict, level: float, quiet: bool = False):
        """Move the virtual stop to `level` (real price) if that tightens it; never loosens."""
        st, side = pos["state"], pos.get("side", 1)
        new = side * level                                       # shorts are tracked on -price
        if new > st.stop:
            st.stop = new
            if not quiet:
                self.journal("stop_locked", symbol=sym, stop=round(level, 4))

    def record_partial(self, sym: str, pos: dict, px: float, qty: int, reason: str):
        """A partial take-profit: its own ledger row (partial=true) so realized P&L and the report card stay exact."""
        side, entry = pos.get("side", 1), self.entry_px(pos)
        risk = pos["state"].atr * pos["state"].trail * qty
        pnl = side * (px - entry) * qty
        self.journal("partial_exit", symbol=sym, reason=reason, px=px, qty=qty)
        with open(self.dir / "trades.jsonl", "a") as f:
            f.write(json.dumps({"date": self.clock().date().isoformat(), "symbol": sym, "side": "short" if side == -1 else "long", "qty": qty,
                                "entry": entry, "exit": px, "pnl": round(pnl, 2), "r": round(pnl / risk, 3) if risk else 0.0, "reason": reason,
                                "opened": pos["opened"], "closed": self.clock().isoformat(), "partial": True, "expert": pos.get("expert"),
                                "zone_profile": (pos.get("zone") or {}).get("profile"), "stage": (pos.get("zone") or {}).get("stage")}) + "\n")

    def zone_entry_block(self, sym: str, sig, ref: float, zcfg: dict) -> str:
        """'' to allow the entry; otherwise why the zones block it (against the stock's direction, or chasing past the entry zone)."""
        from .brain import DirectionFirst
        from .engine import zone_profile
        zm = getattr(self, "zonemap", {}).get(sym)
        if zcfg.get("direction_first") and zm is not None and not DirectionFirst(None).keep(sig, zm.get("bias", 0)):
            return f"against direction (bias {zm.get('bias', 0):+d})"
        if zcfg.get("stage_filter") and zm is not None and zm.get("stage"):            # Weinstein: longs in stage 2, shorts in stage 4
            from .brain import stage_ok                                                # (box strategies also in the sideways stages 1 / 3)
            if not stage_ok(sig, int(zm["stage"])):
                return f"stage {zm['stage']} (needs {4 if sig.side == -1 else 2})"
        zp = zone_profile({"profiles": zcfg["profiles"]}, sig.tag)
        bid, ask = retry_call(self.broker.quote, sym, sleep=self.sleep)
        px = ask if sig.side == 1 else bid
        atr_i = sig.atr if sig.atr > 0 else sig.stop_dist / max(sig.trail, 1e-9)
        side = -1 if sig.side == -1 else 1
        if atr_i > 0 and side * (px - ref) > zp["chase_atr"] * atr_i:
            return f"chase: {px:.2f} is past the entry zone ({ref:.2f} + {zp['chase_atr']} ATR)"
        return ""

    def swing_symbols(self) -> set:
        """Stocks the swing bot holds (or has stops resting on). Trading the same stock intraday would hit Alpaca's wash-trade rule (403) and
        mix the two books, so the intraday bot leaves them alone."""
        try:
            st = json.loads((self.dir.parent / "alpaca_swing" / "state.json").read_text())
            return set((st.get("positions") or {}).keys())
        except Exception:
            return set()

    def mtf_block(self, sym: str, sig, day, zcfg: dict) -> str:
        """Owner (2026-10-15): the trend's direction AND strength confirmed on several timeframes before entry (weekly, 1 hour, 15 minutes;
        brain.tf_confirm_reason). '' = confirmed or switched off."""
        m = zcfg.get("mtf") or {}
        if not (zcfg.get("live") and m.get("entry")):
            return ""
        from .brain import MTF, tf_confirm_reason
        wb = int((getattr(self, "zonemap", {}).get(sym) or {}).get("wbias", 0))
        return tf_confirm_reason(day, sig, wb, float(m.get("adx_min", MTF["adx_min"])), float(m.get("range_adx_max", MTF["range_adx_max"])))

    def regime_block(self, sig, zcfg: dict) -> str:
        """Owner 2026-10-16 ("build the regime switch"): the mean-reversion experts (range experts, not the box strategies) trade only
        while the market is NOT trending, i.e. SPY's daily ADX (completed days) is below adx_max. Unknown ADX = allowed. '' = allowed."""
        rs = zcfg.get("regime_switch") or {}
        from .experts import BOX_STRATEGIES, RANGE_EXPERTS
        nm = (sig.tag or "").removeprefix("S:")
        if not rs.get("live") or nm not in RANGE_EXPERTS or nm in BOX_STRATEGIES:
            return ""
        a = (getattr(self, "zonemap", {}).get(gates.MARKET) or {}).get("adx")
        lim = float(rs.get("adx_max", 20.0))
        return f"regime: SPY daily ADX {a:.1f} >= {lim:g} (trending, mean reversion off)" if a is not None and a >= lim else ""

    def refresh_zonemap(self, now_et: datetime):
        """Once a day: each stock's direction (bias) and support/resistance zone map from its completed daily bars (consolidated SIP
        history). Saved to zonemap.json; used in SHADOW mode (logged on each trade) until the zones study passes."""
        from .zonemap import bias_for_session, level_map, range_box, stage, weekly_for_session
        zm, dailies = {}, {}
        for sym in self.symbols:
            try:
                d = self.raw.daily_history(sym, days=330) if hasattr(self.raw, "daily_history") else None
                if d is None or not len(d):
                    continue
                dailies[sym] = d[d.index.date < now_et.date()]
                prior = d[d.index.date < now_et.date()]
                zm[sym] = {"bias": bias_for_session(d, now_et.date()), "levels": level_map(prior), "stage": stage(prior),
                         "wbias": weekly_for_session(d, now_et.date())}
                try:                                                  # the owner's daily range box (as of the last completed day)
                    bx = range_box(d)
                    bx = bx[bx.index.date < now_et.date()].iloc[-1]
                    zm[sym]["box"] = [bool(bx["valid"]), float(bx["top"]), float(bx["bot"])]
                    zm[sym]["box_atr"] = float(bx["atr"])
                except Exception:
                    zm[sym]["box"], zm[sym]["box_atr"] = [False, 0.0, 0.0], 0.0
                if sym == gates.MARKET:                               # SPY's daily ADX (completed days): the mean-reversion regime switch
                    try:
                        from .indicators import adx
                        a = float(adx(prior).iloc[-1])
                        zm[sym]["adx"] = round(a, 2) if a == a else None
                    except Exception:
                        zm[sym]["adx"] = None
            except Exception:
                log.exception("zone map: %s skipped", sym)
        if ((self.zone_config().get("trend_stairs") or {}).get("select")):
            self.trend_lists(zm, dailies, now_et)
        self.zonemap = zm
        try:
            (self.dir / "zonemap.json").write_text(json.dumps({"date": now_et.date().isoformat(), "stocks": zm}))
        except Exception:
            log.exception("zone map not saved")
        n = {b: sum(1 for v in zm.values() if v["bias"] == b) for b in (1, -1, 0)}
        st = {k: sum(1 for v in zm.values() if v.get("stage") == k) for k in (1, 2, 3, 4)}
        self.journal("zonemap", up=n[1], down=n[-1], range=n[0], stocks=len(zm), stage2=st[2], stage4=st[4], stage1=st[1], stage3=st[3])

    def trend_lists(self, zm: dict, dailies: dict, now_et: datetime):
        """The owner's daily funnel for trend_stairs (alpacabot.selection): today's long list (stage 2, sector news not negative, 3 strongest
        per sector, trend strength up) and short list (stage 4, sector news negative, 3 weakest, trend strength down), from completed daily
        bars. Sector news from Alpaca headlines (alpacabot.news); if the feed fails, the sector ETF trend stands in. Saved on the zone map."""
        from . import universe as _u
        from .news import fetch as news_fetch, sector_news_negative, sector_trend_negative
        from .selection import for_session, lists
        try:
            spy = dailies.get(gates.MARKET)
            if spy is None or not len(spy):
                raise ValueError("no SPY daily bars")
            try:
                neg = sector_news_negative(news_fetch(list(_u.STOCKS), now_et.date() - timedelta(days=14), now_et.date(), self.dir / "news_cache"),
                                           _u.SECTOR_OF, spy.index.append(pd.DatetimeIndex([pd.Timestamp(now_et.date())], tz=spy.index.tz)))
                src = "news"
            except Exception:
                log.exception("sector news unavailable: the sector ETF trend stands in")
                etf = {e: self.raw.daily_history(e, days=330) for e in _u.SECTOR_ETFS}
                neg = sector_trend_negative({e: d[d.index.date < now_et.date()] for e, d in etf.items() if d is not None and len(d)})
                src = "sector trend"
            lok, sok = lists(dailies, spy, _u.SECTOR_OF, neg)
            day_after = now_et.date()
            for sym, z in zm.items():
                z["ts_long"] = bool(for_session(lok, sym, day_after))
                z["ts_short"] = bool(for_session(sok, sym, day_after))
            self.journal("trend_lists", source=src, long=[s for s, z in zm.items() if z.get("ts_long")], short=[s for s, z in zm.items() if z.get("ts_short")])
        except Exception:
            log.exception("trend_stairs lists failed: no list today (trend_stairs takes no new trades)")
            for z in zm.values():
                z["ts_long"] = z["ts_short"] = False

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
        if pos.get("exhaust_r") and pos.get("risk") and len(day) >= 2 and held >= 2:   # LIVE exhaustion bar: a +exhaust_r trade
            side, entry = pos.get("side", 1), self.entry_px(pos)                         # closes past the previous bar's extreme
            best = pos.get("best_px")
            gained = side * ((best if best is not None else entry) - entry) >= pos["exhaust_r"] * pos["risk"]
            rev = day["close"].iloc[-1] < day["low"].iloc[-2] if side == 1 else day["close"].iloc[-1] > day["high"].iloc[-2]
            if gained and rev:
                self.close_now(sym, "exhausted", day.index[-1] + pd.Timedelta(minutes=5))
                return
        zc = self.zone_config()
        if zc.get("live") and (zc.get("mtf") or {}).get("trend_exit"):                 # re-confirm the trend while the trade runs (owner)
            from .experts import TREND_EXPERTS
            tcol = "f_h1_dir" if pos.get("side", 1) == 1 else "m_f_h1_dir"
            if (pos.get("tag") or "").removeprefix("S:") in TREND_EXPERTS and tcol in day and int(day[tcol].iloc[-1]) == -1:
                self.close_now(sym, "trend_lost", day.index[-1] + pd.Timedelta(minutes=5))   # the 1-hour trend turned against it
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
            brain = self.brain
            zbox = getattr(self, "zonemap", {}).get(sym) or {}
            if (self.zone_config().get("box") or {}).get("live") and zbox.get("box"):   # the owner's range box strategy (live by his decision 2026-10-16)
                from .brain import BoxSignals, FibBoxSignals
                day.attrs["box"], day.attrs["box_atr"] = tuple(zbox["box"]), float(zbox.get("box_atr", 0.0))
                brain = BoxSignals(self.brain)
                if (self.zone_config().get("fib") or {}).get("live"):           # the Fibonacci box beside it (owner, 2026-10-16)
                    brain = FibBoxSignals(brain)
                bc = self.zone_config().get("box_confirm") or {}
                if bc.get("live"):                                               # candle + volume + volatility box (owner, 2026-10-16)
                    from .brain import BoxConfirmSignals
                    brain = BoxConfirmSignals(brain, bc.get("mode", "both"), float(bc.get("vol_mult", 1.5)), float(bc.get("atr_ratio", 1.1)),
                                              float(bc.get("vote", 0.0)))
                if (self.zone_config().get("hybrid") or {}).get("live"):        # hybrid zones: box + Fibonacci + swing levels (owner, 2026-10-16)
                    from .brain import HybridBoxSignals
                    day.attrs["levels"] = zbox.get("levels") or []
                    brain = HybridBoxSignals(brain)
            ts_cfg = self.zone_config().get("trend_stairs") or {}
            if ts_cfg.get("live") and self.zone_config().get("live"):                                              # owner's trend_stairs design (2026-10-17), its own tag
                from .brain import TrendStairsSignals
                if not day.attrs.get("levels"):
                    day.attrs["levels"] = zbox.get("levels") or []                # the stock's daily S/R zones (for the S/R vote)
                day.attrs["ts_ok"] = (bool(zbox.get("ts_long")), bool(zbox.get("ts_short")))   # today's long / short list (owner's funnel)
                brain = TrendStairsSignals(brain, int(ts_cfg.get("need", 3)), float(ts_cfg.get("h1_stoch_max", 40.0)),
                                           float(ts_cfg.get("near_ma_atr", 0.8)), float(ts_cfg.get("vwap_atr", 0.75)),
                                           float(ts_cfg.get("tp1_atr", 1.3)), float(ts_cfg.get("stop_atr", 1.2)),
                                           bool(ts_cfg.get("sr_rsi", False)), int(ts_cfg.get("need6", 4)),
                                           tuple(ts_cfg.get("sides", [1, -1])), float(ts_cfg.get("confirm", 0.0)),
                                           bool(ts_cfg.get("select", False)), bool(ts_cfg.get("tp_zone", False)))
            sc_cfg = self.zone_config().get("scalper") or {}
            if sc_cfg.get("live") and self.zone_config().get("live"):                                              # owner's scalper redesign (2026-10-19)
                from .brain import OnlyTags, ScalperSignals
                brain = ScalperSignals(brain, int(sc_cfg.get("need", 3)), float(sc_cfg.get("stop_r", 0.5)), float(sc_cfg.get("tp_r", 1.25)),
                                       int(sc_cfg.get("hold", 6)), float(sc_cfg.get("vol_mult", 1.2)))
                if sc_cfg.get("only"):
                    brain = OnlyTags(brain, {ScalperSignals.TAG})
            vh_cfg = self.zone_config().get("velez_hybrid") or {}
            if vh_cfg.get("live") and self.zone_config().get("live"):                                       # owner 2026-10-21: Velez rules on every expert
                from .brain import VelezHybrid
                brain = VelezHybrid(brain, bool(vh_cfg.get("env", True)), bool(vh_cfg.get("look_left", True)), False, False,
                                    float(vh_cfg.get("stop_r", 0.0)), float(vh_cfg.get("target_r", 0.0)),
                                    tags=set(self.brain.experts) | set(self.brain.short_experts))
            vz_cfg = self.zone_config().get("velez") or {}
            if vz_cfg.get("live") and self.zone_config().get("live"):                                              # owner's Velez rules (2026-10-20)
                from .brain import VelezSignals
                brain = VelezSignals(brain, tuple(vz_cfg.get("setups") or ("velez_halt", "velez_vbs", "velez_rbi")), float(vz_cfg.get("tp_r", 1.5)),
                                     int(vz_cfg.get("hold", 18)), sides=tuple(vz_cfg.get("sides") or [1]))
                rp = vz_cfg.get("repair") or {}
                if rp.get("setups"):                                    # owner 2026-10-22: the repaired Velez setups (ATR stop, next-bar
                    brain = VelezSignals(brain, tuple(rp["setups"]), float(rp.get("tp_r", 1.5)), int(rp.get("hold", 18)),   # confirmation,
                                         sides=tuple(rp.get("sides") or [1]), stop_atr=float(rp.get("stop_atr", 0.0)),    # one per day)
                                         confirm=bool(rp.get("confirm", False)), first_only=bool(rp.get("first_only", False)))
            vzs = self.zone_config().get("vz_systems") or {}
            if vzs.get("live") and self.zone_config().get("live"):                      # owner 2026-10-22: the redesigned Velez systems
                from .brain import VZ_DAY, VZ_SCALP, VelezSignals
                if vzs.get("scalp", True):
                    brain = VelezSignals(brain, **VZ_SCALP)
                if vzs.get("day", True):
                    brain = VelezSignals(brain, **VZ_DAY)
            sig = next((s for s in brain.signals(day) if s.i == len(day) - 1), None)
            if not sig:
                continue
            _es = self.zone_config().get("entry_symbols")
            if _es and sym not in _es:                                    # owner 2026-10-22: scalp the mid / small caps only; the
                self.journal("signal", symbol=sym, side="short" if sig.side == -1 else "long", tag=sig.tag,   # big caps' signals are
                             ref=float(day["close"].iloc[-1]), result="shadow: not in entry_symbols")          # kept for comparison
                continue
            _cd = self.zone_config().get("expert_cooldown_min")
            _lx = self.last_exit_by.get((sym, str(sig.tag)))
            if _cd and _lx is not None and now_et < _lx + timedelta(minutes=float(_cd)):   # owner 2026-10-22: the same expert waits
                self.journal("signal", symbol=sym, side="short" if sig.side == -1 else "long", tag=sig.tag,          # before trading the
                             ref=float(day["close"].iloc[-1]), result=f"same expert cooldown ({float(_cd):.0f} min after its last exit)")   # same stock
                continue
            zcfg = self.zone_config()
            cap = int(zcfg.get("repeat_max", self.brain.max_trades)) if zcfg["live"] else self.brain.max_trades
            if self.trades_today.get(sym, 0) >= cap:      # owner: repeat through the day while the stock clears trend + stage + filters
                continue
            if sym in self.last_exit and day.index[-1] < self.last_exit[sym] + pd.Timedelta(minutes=5 * self.brain.cooldown):
                continue
            ref = float(day["close"].iloc[-1])
            if sym in self.swing_symbols():
                self.journal("signal", symbol=sym, side="short" if sig.side == -1 else "long", tag=sig.tag, ref=ref, result="swing bot holds this stock")
                continue
            if sig.side == -1:
                why = self.can_short(sym)
                if why:
                    self.journal("signal", symbol=sym, side="short", tag=sig.tag, ref=ref, result=why)
                    continue
            elif retry_call(self.broker.positions, sleep=self.sleep).get(sym, 0) < 0:
                self.journal("signal", symbol=sym, side="long", tag=sig.tag, ref=ref, result="another bot is short this symbol")
                continue
            zcfg = self.zone_config()
            if zcfg["live"]:                                            # owner's zones, LIVE: direction first, then no chasing
                why = self.zone_entry_block(sym, sig, ref, zcfg)
                if why:
                    self.journal("signal", symbol=sym, side="short" if sig.side == -1 else "long", tag=sig.tag, ref=ref, result=why)
                    continue
                why = self.mtf_block(sym, sig, day, zcfg)
                if why:
                    self.journal("signal", symbol=sym, side="short" if sig.side == -1 else "long", tag=sig.tag, ref=ref, result=why)
                    continue
                rc = zcfg.get("reversal_confirm") or {}
                from .experts import BOX_STRATEGIES, RANGE_EXPERTS
                _nm = (sig.tag or "").removeprefix("S:")
                if rc.get("live") and _nm in RANGE_EXPERTS and _nm not in BOX_STRATEGIES:   # owner: a mean-reversion entry needs the
                    from .confirm import reversal_votes                                       # move against it to be failing, not just stretched
                    k, failed = reversal_votes(day, len(day) - 1, -1 if sig.side == -1 else 1, float(rc.get("vola_max", 1.2)),
                                               float(rc.get("stretch_atr", 1.0)))
                    if k < int(rc.get("need", 2)):
                        self.journal("signal", symbol=sym, side="short" if sig.side == -1 else "long", tag=sig.tag, ref=ref,
                                     result=f"reversal not confirmed ({k}/4; failed: {', '.join(failed)})")
                        continue
                og = zcfg.get("oversold_gate") or {}
                if og.get("live") and sig.side == 1 and (sig.tag or "") not in (og.get("exempt") or []):   # owner: only buy when most indicators say oversold
                    from .confirm import oversold_votes
                    k, names = oversold_votes(day, len(day) - 1)
                    if k < int(og.get("need", 3)):
                        self.journal("signal", symbol=sym, side="long", tag=sig.tag, ref=ref,
                                     result=f"not oversold ({k}/5 say oversold{': ' + ', '.join(names) if names else ''}; need {int(og.get('need', 3))})")
                        continue
                why = self.regime_block(sig, zcfg)
                if why:
                    self.journal("signal", symbol=sym, side="short" if sig.side == -1 else "long", tag=sig.tag, ref=ref, result=why)
                    continue
                tc = zcfg.get("trend_confirm") or {}
                from .experts import TREND_EXPERTS
                if tc.get("live") and (sig.tag or "").removeprefix("S:") in TREND_EXPERTS:   # owner: candles / volume / volatility must
                    from .confirm import trend_votes                                          # show the trend continuing on the signal bar
                    k, failed = trend_votes(day, len(day) - 1, -1 if sig.side == -1 else 1, float(tc.get("vol_mult", 1.2)))
                    if k / 5.0 < float(tc.get("vote", 0.6)) - 1e-9:
                        self.journal("signal", symbol=sym, side="short" if sig.side == -1 else "long", tag=sig.tag, ref=ref,
                                     result=f"trend not confirmed ({k}/5; failed: {', '.join(failed)})")
                        continue
            if (sig.tag or "") in (zcfg.get("shadow_tags") or []):     # owner 2026-10-22: losing setups keep signalling, no orders
                self.journal("signal", symbol=sym, side="short" if sig.side == -1 else "long", tag=sig.tag, ref=ref,
                             result="shadow (passed every gate; signal logged, no order)")
                continue
            risk_before, tier = self.exec.risk_pct, None
            lad_cfg = zcfg.get("ladder") if zcfg["live"] else None
            if lad_cfg:                                                  # owner's scaling: size by how deep price is into the entry zone
                tier = self.ladder_tier(sym, sig, ref, lad_cfg)
                self.exec.risk_pct = lad_cfg["entry_risk"][tier]
            if zcfg.get("max_pos_frac") is not None:                     # owner 2026-10-21: no position above this share of equity
                self.exec.max_pos_frac = float(zcfg["max_pos_frac"])
            _rbe = (zcfg.get("risk_by_expert") or {}).get((sig.tag or "").removeprefix("S:"))
            if _rbe is not None:                                        # per-expert size from the evidence (e.g. halved for clear losers)
                self.exec.risk_pct = float(_rbe)
            if (sig.tag or "").removeprefix("S:") == "fib_box":         # the Fibonacci box trades at reduced size (owner: "decrease equity use")
                self.exec.risk_pct = float((zcfg.get("fib") or {}).get("risk_pct", 0.005))
            if (sig.tag or "").removeprefix("S:") == "box_confirm":     # the confirmed box, at reduced size
                self.exec.risk_pct = float((zcfg.get("box_confirm") or {}).get("risk_pct", 0.005))
            if (sig.tag or "").removeprefix("S:") == "hybrid_box":      # the hybrid zones, also at reduced size
                self.exec.risk_pct = float((zcfg.get("hybrid") or {}).get("risk_pct", 0.005))
            # owner 2026-10-21: signal-strength sizing. The score (0-1) = how many of the 5 candle / volume / volatility checks agree with the
            # trade on its signal bar (confirm.trend_votes). Always journaled so the live record shows whether stronger signals win more;
            # with zones.json score_sizing.live the risk is min_risk + score x (max_risk - min_risk) (the notional cap still applies).
            try:
                from .confirm import trend_votes
                _score = trend_votes(day, len(day) - 1, -1 if sig.side == -1 else 1)[0] / 5.0
            except Exception:
                _score = None
            _ss = zcfg.get("score_sizing") or {}
            if _ss.get("live") and _score is not None:
                lo_, hi_ = float(_ss.get("min_risk", 0.001)), float(_ss.get("max_risk", 0.005))
                self.exec.risk_pct = lo_ + _score * (hi_ - lo_)
            self.journal("signal_score", symbol=sym, tag=sig.tag, score=_score, risk_pct=round(self.exec.risk_pct, 5))
            try:
                verdict = self.exec.try_enter(sym, sig, ref, now_et)   # spread guard, window, breaker, duplicate-id guard
            except requests.HTTPError as exc:                          # a rejected order must not abort the rest of this bar's symbols
                self.exec.opened.pop(sym, None)
                self.journal("order_rejected", symbol=sym, side="short" if sig.side == -1 else "long", error=str(exc)[:300])
                log.warning("order for %s rejected: %s", sym, str(exc)[:200])
                continue
            finally:
                self.exec.risk_pct = risk_before
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
                zcfg = self.zone_config()
                zone = shadow_zone(day, sig, fill, ref, zcfg["profiles"])                 # this trade's zones (logged always)
                zone.update(shadow_direction(getattr(self, "zonemap", {}).get(sym), sig, fill, zcfg["profiles"]))
                pos = self.exits.positions[sym]
                zone["stage"] = (getattr(self, "zonemap", {}).get(sym) or {}).get("stage")
                pos["zone"], pos["risk"] = zone, float(sig.stop_dist)
                pos["expert"] = str(sig.tag)
                _rp = (zcfg.get("velez") or {}).get("repair") or {}
                if (sig.tag or "").removeprefix("S:") in (_rp.get("setups") or []) and _rp.get("version"):
                    pos["ver"] = str(_rp["version"])                    # owner 2026-10-22: each forward-test version keeps its own record
                if zcfg["live"]:                                         # LIVE: take profit at the zone-map level, else the style zone
                    pos["zone_tp"] = zone.get("map_target") if (zcfg.get("use_map") and zone.get("map_target") is not None) else zone["target"]
                    if getattr(sig, "target_px", 0) > 0:                  # the range box's far edge is this trade's take-profit zone
                        pos["zone_tp"] = float(sig.target_px)
                    _p = zcfg["profiles"].get((sig.tag or "").removeprefix("S:")) or zcfg["profiles"][zone.get("profile", "trend")]
                    pos["exhaust_r"] = float(_p["exhaust_r"])
                    if lad_cfg and tier is not None:
                        side_ = -1 if sig.side == -1 else 1
                        atr_i = sig.atr if sig.atr > 0 else sig.stop_dist / max(sig.trail, 1e-9)
                        from .engine import ladder_for
                        lcfg = ladder_for(lad_cfg, sig.tag)
                        if getattr(sig, "target2_px", 0) > 0:             # Fibonacci box: TP1 at the 0.5 level, TP2 at the 0.236 level
                            from .brain import fib_ladder_cfg
                            lcfg = fib_ladder_cfg(lcfg, float(sig.target_px), float(sig.target2_px), atr_i)
                        pos["ladder"] = {"ref": ref, "atr": atr_i, "tier": tier, "stop_px": fill - side_ * sig.stop_dist, "fill": fill,
                                         "peak_qty": qty, "sold": 0, "tp_steps": 0, "cfg": lcfg}
                        if lad_cfg.get("ride"):                  # the zones take profit; the expert's fixed target would close it all
                            pos["state"].tp = float("inf")
                        self.journal("ladder_open", symbol=sym, tier=tier, risk_pct=lad_cfg["entry_risk"][tier], qty=qty)
                self.store.save(self.exits.positions)
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
        fl = self.dir / "FLATTEN_NOW"
        if fl.exists():                              # owner's "flatten now" (python -m alpacabot paper-flatten): close this bot's own positions
            fl.unlink()
            for sym in list(self.exits.positions):
                try:
                    self.close_now(sym, "flatten_owner", bar_start(now_et) + pd.Timedelta(minutes=5))
                except Exception:
                    log.exception("owner flatten: %s", sym)
            self.journal("owner_flatten", left=list(self.exits.positions))
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
            if action.startswith("profit lock"):
                pl = self.exec.profit_lock
                send_telegram(f"ALPACA PAPER (intraday): profit lock - today's gain peaked at ${pl.peak_gain:,.0f} and fell to half of it. "
                              f"Flattened to keep the rest; no new entries until tomorrow.")
            if "flatten" in action or "breaker" in action:      # OwnBook.flatten_all already recorded each exit
                self.exits.positions.clear()
            elif action.startswith("stale exit "):
                sym = action.split()[-1]
                if sym in snapshot:
                    self.record_exit(sym, self.last_px.get(sym, self.entry_px(snapshot[sym])), "stale", snapshot[sym])
                    self.exits.positions.pop(sym, None)
        self.store.save(self.exits.positions)
        self.save_day_guards()
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
            ok = send_telegram(text)
            self.journal("report_card", sent=bool(ok))              # False = no Telegram settings, or Telegram refused it
            if not ok:
                log.warning("report card NOT delivered to Telegram (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
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


SCALP_MINUTES = 30


def trade_style(t: dict) -> str:
    """'scalp' (held <= 30 min), 'day' (longer, same session), or 'unclassified' (older records without a close time)."""
    try:
        held = (datetime.fromisoformat(t["closed"]) - datetime.fromisoformat(t["opened"])).total_seconds() / 60
    except (KeyError, TypeError, ValueError):
        return "unclassified"
    return "scalp" if held <= SCALP_MINUTES else "day"


def _style_lines(trades: list, today: list) -> list:
    """Scalps vs day trades (the swing runner has its own section below)."""
    out = ["", f"BY STYLE (scalp = held <= {SCALP_MINUTES} min, day = longer, flat by 3:50 pm)"]
    for style in ("scalp", "day", "unclassified"):
        allt = [t for t in trades if trade_style(t) == style]
        if not allt:
            continue
        tod = [t for t in today if trade_style(t) == style]
        w = sum(t["pnl"] > 0 for t in allt)
        gl = -sum(t["pnl"] for t in allt if t["pnl"] <= 0)
        pf = sum(t["pnl"] for t in allt if t["pnl"] > 0) / gl if gl else float("inf")
        label = "unclassified (before close times were logged)" if style == "unclassified" else style
        out.append(f"  {label}: today {len(tod)} trades ${sum(t['pnl'] for t in tod):+,.0f} | all {len(allt)}, {w}W/{len(allt) - w}L, "
                   f"P&L ${sum(t['pnl'] for t in allt):+,.0f}, profit factor {pf:.2f}")
    return out


def shadow_zone(day, sig, fill: float, ref: float, profiles: dict | None = None) -> dict:
    """The owner's trade zones for a live trade (engine.ZONES rules), recorded for comparison only: would the entry zone have skipped it
    as a chase, and where is its exhaustion take-profit (prior 20-bar high/low if >= 1R away, else 2R)."""
    from .engine import ZONE_PROFILES, zone_profile
    side = -1 if sig.side == -1 else 1
    zp = zone_profile({"profiles": profiles or ZONE_PROFILES}, sig.tag)   # this expert's style decides the profile; levels are this stock's
    atr_i = sig.atr if sig.atr > 0 else sig.stop_dist / max(sig.trail, 1e-9)
    bars = day.iloc[-zp["lookback"]:]
    if zp.get("target") == "mean":
        zone = float(bars["close"].mean())
    else:
        zone = float(bars["high"].max()) if side == 1 else float(bars["low"].min())
    far = side * (zone - fill) >= zp["min_r"] * sig.stop_dist
    target = zone if far else fill + side * zp["default_r"] * sig.stop_dist
    return {"chase": bool(side * (fill - ref) > zp["chase_atr"] * atr_i), "target": round(target, 4),
            "profile": "range" if zp.get("target") == "mean" else "trend"}


def shadow_direction(zm: dict | None, sig, fill: float, profiles: dict | None = None) -> dict:
    """Direction-first check and the zone-map take-profit for a live trade (shadow only)."""
    if not zm:
        return {}
    from .brain import DirectionFirst
    from .engine import ZONE_PROFILES, zone_profile
    from .zonemap import zone_target
    side = -1 if sig.side == -1 else 1
    zp = zone_profile({"profiles": profiles or ZONE_PROFILES}, sig.tag)
    tgt = zone_target(zm.get("levels") or [], fill, side, sig.stop_dist, zp["min_r"])
    return {"bias": zm.get("bias", 0), "against": not DirectionFirst(None).keep(sig, zm.get("bias", 0)),
            "map_target": round(tgt, 4) if tgt is not None else None}


def _zone_lines(trades: list) -> list:
    """Shadow comparison: what the trade zones would have changed (not traded)."""
    zs = [t for t in trades if "zone_hit" in t]
    if not zs:
        return []
    chase = [t for t in zs if t.get("zone_chase")]
    hit = [t for t in zs if t.get("zone_hit")]
    actual_hit = sum(t["pnl"] for t in hit)
    zone_hit = sum(t["zone_pnl"] or 0 for t in hit)
    out = ["", "TRADE ZONES (logged on every trade; LIVE when zones.json says live)",
           f"  entry zone: {len(chase)} of {len(zs)} trades were chases the zone would have skipped (their P&L ${sum(t['pnl'] for t in chase):+,.0f})",
           f"  exhaustion zone: {len(hit)} trades reached it; taking profit there would have made ${zone_hit:+,.0f} vs ${actual_hit:+,.0f} actual"]
    dz = [t for t in zs if "against" in t]
    if dz:
        ag = [t for t in dz if t["against"]]
        out.append(f"  direction first: {len(ag)} of {len(dz)} trades went against their stock's daily direction "
                   f"(their P&L ${sum(t['pnl'] for t in ag):+,.0f}; with the direction ${sum(t['pnl'] for t in dz if not t['against']):+,.0f})")
    return out


def _ladder_lines(raw: list, day: str) -> list:
    """The owner's drawn plan at work (raw ledger rows, partial exits included): P&L by exit step, and by Weinstein stage."""
    def block(rows, label):
        if not rows:
            return []
        kinds = {"sell zone cuts (25/50% before the stop)": lambda t: t["reason"].startswith("cut"),
                 "take-profit steps (25/50% in the zone)": lambda t: t["reason"].startswith("zone"),
                 "stops (full stop / locked stop / trail)": lambda t: t["reason"] in ("stop", "trail", "lock"),
                 "other exits (time, signal, close)": lambda t: not (t["reason"].startswith("cut") or t["reason"].startswith("zone")
                                                                    or t["reason"] in ("stop", "trail", "lock"))}
        out = [f"  {label}:"]
        for name, f in kinds.items():
            ts = [t for t in rows if f(t)]
            if ts:
                out.append(f"    {name}: {len(ts)} fills, ${sum(t['pnl'] for t in ts):+,.0f}")
        st = {}
        for t in rows:
            if t.get("stage"):
                st.setdefault(t["stage"], []).append(t["pnl"])
        if st:
            out.append("    by stage: " + ", ".join(f"stage {k} ${sum(v):+,.0f} ({len(v)})" for k, v in sorted(st.items())))
        return out
    if not any(t.get("partial") or t.get("stage") for t in raw):
        return []
    return ["", "ZONE PLAN (owner's drawing: 1% risk, cut 25/50%, take profit 25/50%, the rest rides a trailing stop)"] + \
        block([t for t in raw if t["date"] == day], "today") + block(raw, "all time")


def _velez_forward_lines(trades: list) -> list:
    """Owner 2026-10-22 Velez forward-test scorecard, per expert and config version: stop-outs, never green (peak open profit <= 0.05R),
    10-minute exits, average winner / loser, MFE / MAE (R), net R and repeat entries (same stock, same day, after the first)."""
    ts = [t for t in trades if t.get("ver")]
    if not ts:
        return []
    out = ["", "VELEZ FORWARD TEST (paper, per expert and config version)"]
    for key in sorted({(t.get("expert"), t["ver"]) for t in ts}):
        g = [t for t in ts if (t.get("expert"), t["ver"]) == key]
        w, l = [t["r"] for t in g if t["pnl"] > 0], [t["r"] for t in g if t["pnl"] <= 0]
        seen, rep = set(), 0
        for t in g:
            k = (t["symbol"], t["date"])
            rep += k in seen
            seen.add(k)
        avg = lambda xs: sum(xs) / len(xs) if xs else 0.0          # noqa: E731
        out.append(f"  {key[0]} [{key[1]}]: {len(g)} trades, win {len(w) / len(g):.0%}, stopped {sum('stop' in str(t['reason']) for t in g)}, "
                   f"never green {sum(t.get('peak_r', 0) <= 0.05 for t in g)}, 10-min exits {sum(t['reason'] == 'neg_exit' for t in g)}, "
                   f"avg win {avg(w):+.2f}R / loss {avg(l):+.2f}R, MFE {avg([t.get('peak_r', 0) for t in g]):+.2f}R, "
                   f"MAE {avg([t.get('mae_r', 0) for t in g]):.2f}R, net {sum(t['r'] for t in g):+.2f}R (${sum(t['pnl'] for t in g):+,.0f}), "
                   f"repeat entries {rep}")
    return out


def _giveback_lines(trades: list, today: list) -> list:
    """How much open profit was kept: peak open profit (R) vs the result (R), and trades that were +1R up but closed at <= 0."""
    def block(ts, label):
        ts = [t for t in ts if "peak_r" in t]
        if not ts:
            return []
        peak = sum(t["peak_r"] for t in ts) / len(ts)
        kept = sum(t["r"] for t in ts) / len(ts)
        flipped = [t for t in ts if t["peak_r"] >= 1.0 and t["r"] <= 0]
        up = [t for t in ts if t["peak_r"] >= 1.0]
        return [f"  {label}: {len(ts)} trades, avg peak open profit {peak:+.2f}R, avg kept {kept:+.2f}R, gave back {peak - kept:.2f}R; "
                f"{len(flipped)} of {len(up)} trades that reached +1R closed at break-even or a loss"]
    out = block(today, "today") + block(trades, "all")
    return (["", "PROFIT GIVEBACK (peak open profit vs what was kept)"] + out) if out else []


def whole_trades(rows: list[dict]) -> list[dict]:
    """Ledger rows -> one row per trade: a scaled-out trade's partial take-profits (partial=true) are folded into its final row (same
    symbol and open time), so counts, win rate and profit factor are per trade, while the P&L stays the exact sum."""
    out, part = [], {}
    for t in rows:
        k = (t["symbol"], t.get("opened"))
        if t.get("partial"):
            part.setdefault(k, []).append(t)
            continue
        ps = part.pop(k, [])
        if ps:
            t = {**t, "pnl": round(t["pnl"] + sum(p["pnl"] for p in ps), 2), "r": round(t["r"] + sum(p["r"] for p in ps), 3),
                 "qty": t["qty"] + sum(p["qty"] for p in ps), "partials": len(ps)}
        out.append(t)
    return out + [ps[0] | {"pnl": round(sum(p["pnl"] for p in ps), 2), "r": round(sum(p["r"] for p in ps), 3),
                           "qty": sum(p["qty"] for p in ps), "partials": len(ps)} for ps in part.values()]   # still open: what is banked


def report_card(state_dir, day=None) -> str:
    """Win/loss, ROI, drawdown, SPY comparison and the go-live gate, from the trade ledger and the daily equity snapshots."""
    d = Path(state_dir)
    trades, eq = whole_trades(_jl(d / "trades.jsonl")), _jl(d / "equity.jsonl")
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
        base_f = d / "base_equity.json"                          # ROI and drawdown start from the bot's starting capital, so day 1 counts
        start = float(json.loads(base_f.read_text())["equity"]) if base_f.exists() else eq[0]["equity"]
        periods = days if base_f.exists() else days - 1
        cur = eq[-1]["equity"]
        roi = cur / start - 1
        dayret = (eq[-1]["equity"] / (eq[-2]["equity"] if len(eq) > 1 else start) - 1)
        peak, dd = start, 0.0
        for e in eq:
            peak = max(peak, e["equity"])
            dd = max(dd, 1 - e["equity"] / peak)
        spy = eq[-1]["spy"] / eq[0]["spy"] - 1 if eq[0].get("spy") else 0.0
        avg_daily = ((cur / start) ** (1 / max(periods, 1)) - 1) if periods >= 1 else 0.0
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
    ex = {}
    for t in trades:
        if t.get("expert"):
            ex.setdefault(t["expert"], []).append(t)
    if ex:
        lines.append("BY EXPERT (trades opened since the expert was logged)")
        for k, ts in sorted(ex.items(), key=lambda x: -len(x[1])):
            n_, w_, l_, wr_, pf_, ar_ = stats(ts)
            lines.append(f"  {k}: {n_}, {w_}W/{l_}L, P&L ${sum(t['pnl'] for t in ts):+,.0f}, profit factor {pf_:.2f}")
    lines += _style_lines(trades, today)
    lines += _giveback_lines(trades, today)
    lines += _velez_forward_lines(trades)
    lines += _zone_lines(trades)
    lines += _ladder_lines(_jl(d / "trades.jsonl"), day)
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
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text[:4000]}, timeout=20)
    except requests.RequestException:
        log.exception("telegram send failed")
        return False
    if not r.ok:
        log.warning("telegram refused the message: HTTP %s", r.status_code)
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
