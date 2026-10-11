"""Paper-trading runner: the backtest's Brain decides, Alpaca's PAPER account fills, the virtual exit manager closes.

PAPER ONLY. The trading endpoint is hard-wired to paper-api.alpaca.markets; there is no setting for a live account.
Nothing here is validated yet: this runner exists to collect real forward results while the backtests run. Every decision and fill is
appended to state/alpaca_paper/journal.jsonl so the forward record can be compared with the backtest later (`python -m alpacabot paper-report`).
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .brain import Brain
from .data import NY, _fetch, _headers
from .engine import resample_bars
from .exits import StateStore
from .execution import CircuitBreaker, EntryWindow, Executor, VirtualExitManager, retry_call, setup_logging, spread_ok
from .experts import features_all

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
        return r

    def equity(self) -> float:
        return float(self._req("GET", f"{TRADE_URL}/account").json()["equity"])

    def clock_open(self) -> bool:
        return bool(self._req("GET", f"{TRADE_URL}/clock").json()["is_open"])

    def quote(self, symbol: str):
        q = self._req("GET", f"{DATA_URL}/stocks/{symbol}/quotes/latest", params={"feed": "iex"}).json()["quote"]
        return float(q["bp"]), float(q["ap"])

    def positions(self) -> dict[str, int]:
        return {p["symbol"]: int(float(p["qty"])) for p in self._req("GET", f"{TRADE_URL}/positions").json()}

    def submit_market(self, symbol: str, qty: int, client_order_id: str | None = None):
        body = {"symbol": symbol, "qty": str(qty), "side": "buy", "type": "market", "time_in_force": "day"}
        if client_order_id:
            body["client_order_id"] = client_order_id
        r = self._req("POST", f"{TRADE_URL}/orders", json=body)
        if r.status_code == 422 and "client_order_id" in r.text:
            return None                                    # duplicate id: this order was already sent
        r.raise_for_status()
        return r.json()

    def close(self, symbol: str):
        return self._req("DELETE", f"{TRADE_URL}/positions/{symbol}")

    def flatten_all(self):
        self._req("DELETE", f"{TRADE_URL}/orders")
        return self._req("DELETE", f"{TRADE_URL}/positions", params={"cancel_orders": "true"})


def today_frame(sym: str, now_et: datetime, brain: Brain, fetch=_fetch):
    """Today's completed 5-minute bars with causal features (computed on the last 5 sessions so indicators are warm)."""
    start = (now_et - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
    raw = fetch(sym, "1Min", start, (now_et + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"), "iex")
    raw = raw.between_time("09:30", "15:59") if len(raw) else raw
    if raw.empty:
        return None
    df = resample_bars(raw, 5)
    df = df[df.index + timedelta(minutes=5) <= now_et]       # a bar is usable only once it has closed
    if len(df) < 20:
        return None
    df = df.join(features_all(df))
    day = df[df.index.date == now_et.date()]
    return day if len(day) >= 3 else None


class PaperRunner:
    def __init__(self, broker, brain: Brain | None = None, state_dir="state/alpaca_paper", risk_pct=0.01, symbols=None, fetch=_fetch, sleep=time.sleep):
        self.broker, self.brain, self.symbols, self.fetch, self.sleep = broker, brain or Brain(), symbols or SYMBOLS, fetch, sleep
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store = StateStore(self.dir / "positions.json")
        self.exits = VirtualExitManager(self.store, broker, sleep=sleep)
        self.exec = Executor(broker, EntryWindow(), CircuitBreaker(), risk_pct=risk_pct, max_positions=6, sleep=sleep)
        self.exec.opened = self.exits.opened_at()
        self.last_bar: dict[str, object] = {}
        self.entries_today = 0

    def journal(self, kind: str, **kw):
        with open(self.dir / "journal.jsonl", "a") as f:
            f.write(json.dumps({"t": datetime.now(ET).isoformat(timespec="seconds"), "kind": kind, **kw}, default=str) + "\n")

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
        for sym in list(self.exits.positions):
            bid, ask = retry_call(self.broker.quote, sym, sleep=self.sleep)
            reason = self.exits.on_quote(sym, bid, ask)
            if reason:
                self.exec.opened.pop(sym, None)
                self.journal("exit", symbol=sym, reason=reason, bid=bid)

    def on_bar_close(self, now_et: datetime):
        """Once per new 5-minute bar: ask the Brain about each symbol and enter on a fresh signal."""
        for sym in self.symbols:
            if sym in self.exits.positions:
                continue
            day = today_frame(sym, now_et, self.brain, self.fetch)
            if day is None or day.index[-1] == self.last_bar.get(sym):
                continue
            self.last_bar[sym] = day.index[-1]
            sig = next((s for s in self.brain.signals(day) if s.i == len(day) - 1 and s.side == 1), None)
            if not sig:
                continue
            ref = float(day["close"].iloc[-1])
            verdict = self.exec.try_enter(sym, sig, ref, now_et)       # risk 1% of equity, spread guard, window, breaker, duplicate-id guard
            self.journal("signal", symbol=sym, tag=sig.tag, ref=ref, result=verdict)
            if verdict == "ordered":
                bid, ask = retry_call(self.broker.quote, sym, sleep=self.sleep)
                qty = self.exec.last_qty
                self.exits.open(sym, qty, ask, sig.atr, sig.adx, now_et, sig.trail, sig.tp_scale)
                self.entries_today += 1

    def tick(self, now_et: datetime):
        for action in self.exec.on_tick(now_et):
            self.journal("housekeeping", action=action)
            if "flatten" in action or "breaker" in action:
                self.exits.positions.clear()
                self.store.save(self.exits.positions)
        self.poll_exits(now_et)

    def run(self):
        log.info("paper runner started: %s", self.symbols)
        self.reconcile()
        last_slot = None
        while True:
            now = datetime.now(ET)
            try:
                if now.weekday() >= 5 or not self.broker.clock_open():
                    self.sleep(60)
                    continue
                self.tick(now)
                slot = (now.hour, now.minute // 5)
                if slot != last_slot and now.second >= 5:       # give the feed a few seconds to publish the closed bar
                    last_slot = slot
                    self.on_bar_close(now)
            except Exception:
                log.exception("paper loop error (continuing)")
            self.sleep(15)


def report(state_dir="state/alpaca_paper") -> str:
    p = Path(state_dir) / "journal.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    sig = [r for r in rows if r["kind"] == "signal"]
    ex = [r for r in rows if r["kind"] == "exit"]
    by = {}
    for r in ex:
        by[r["reason"]] = by.get(r["reason"], 0) + 1
    return (f"paper journal: {len(sig)} signals ({sum(r['result'] == 'ordered' for r in sig)} ordered), {len(ex)} exits {by}\n"
            "P&L: see the Alpaca paper dashboard (account equity); this journal records decisions, not marks.")


def main(a) -> int:
    from .data import credentials
    if not all(credentials()):
        print("WAITING FOR KEYS: add the paper account's key id and secret to /opt/marv-bot/.env")
        return 2
    setup_logging(Path(a.state) / "paper.log")
    PaperRunner(RestBroker(), state_dir=a.state).run()
    return 0
