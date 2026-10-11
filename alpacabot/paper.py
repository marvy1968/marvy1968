"""Paper-trading runner: the backtest's Brain decides, Alpaca's PAPER account fills, the virtual exit manager closes.

PAPER ONLY. The trading endpoint is hard-wired to paper-api.alpaca.markets; there is no setting for a live account.
Nothing here is validated yet: this runner exists to collect real forward results while the backtests run. Every decision and fill is
appended to state/alpaca_paper/journal.jsonl so the forward record can be compared with the backtest later (`python -m alpacabot paper-report`).
"""

import json
import logging
import time
from datetime import datetime, time as dtime, timedelta
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
        self.reported = None

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
            pos = self.exits.positions.get(sym)
            entry, qty, opened = (pos["state"].entry, pos["qty"], pos["opened"]) if pos else (0, 0, "")
            reason = self.exits.on_quote(sym, bid, ask)
            if reason:
                self.exec.opened.pop(sym, None)
                self.journal("exit", symbol=sym, reason=reason, bid=bid)
                risk = pos["state"].atr * pos["state"].trail * qty if pos else 0
                pnl = (bid - entry) * qty
                with open(self.dir / "trades.jsonl", "a") as f:
                    f.write(json.dumps({"date": now_et.date().isoformat(), "symbol": sym, "qty": qty, "entry": entry, "exit": bid, "pnl": round(pnl, 2),
                                        "r": round(pnl / risk, 3) if risk else 0.0, "reason": reason, "opened": opened}) + "\n")

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

    def end_of_day(self, now_et: datetime):
        """Snapshot equity and SPY, then send the daily report card (Telegram when configured, always saved to disk)."""
        try:
            bid, ask = self.broker.quote("SPY")
            snap = {"date": now_et.date().isoformat(), "equity": self.broker.equity(), "spy": round((bid + ask) / 2, 2)}
            with open(self.dir / "equity.jsonl", "a") as f:
                f.write(json.dumps(snap) + "\n")
            text = report_card(self.dir, now_et.date())
            (self.dir / "report_card.txt").write_text(text)
            send_telegram(text)
        except Exception:
            log.exception("end-of-day report failed (continuing)")

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
                if now.time() >= dtime(16, 5) and self.reported != now.date():
                    self.reported = now.date()
                    self.end_of_day(now)
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
    lines = ["ALPACA PAPER REPORT CARD " + day, "PAPER account only. Nothing here is a live result.", ""]
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
    by = {}
    for t in trades:
        by.setdefault(t["symbol"], 0.0)
        by[t["symbol"]] += t["pnl"]
    if by:
        best = sorted(by.items(), key=lambda x: -x[1])
        lines.append("By symbol: " + ", ".join(f"{k} ${v:+,.0f}" for k, v in best[:3] + best[-2:]))
    if gate:
        lines += ["", "GO-LIVE GATE (every line must pass, then it is your decision):"] + [f"  {'PASS' if ok else 'wait'}  {name}: {val}" for name, ok, val in gate]
        lines.append("GATE MET: eligible to consider a small live test." if all(ok for _, ok, _ in gate) else "GATE NOT MET: keep paper trading.")
    else:
        lines.append("No equity snapshots yet; the first appears after today's close.")
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
