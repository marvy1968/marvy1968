"""Same-day ride on stocks (owner 2026-10-28: "if the dip is great enough, the short trend filter will pick it up ... we are trending hard
today ... ride it today only"; "turn on your suggestion for stocks"). PAPER, runs inside the stock paper runner's loop.

Signal (fxbot.strongtrend.today_state on 5-minute bars): 2 of 3 strict trend readings on the 5-minute bars (ADX>=30, EMA 20/50/200 stack,
Aroon 70/30) AND the stock 0.75+ of its daily ATR14 from today's open the same way. 2-week check (57 stocks, 2026-10-14..28): +5.8R at 5 bps,
-9.4R at 10 bps, beat 19 of 20 shuffled-day controls - thin, so it runs small and is judged on paper.
Rules:
  entries      10:00-15:00 ET, the first signal of the day per stock, long or short (shorts only when easy to borrow); never a stock the
               account already holds (the swing book shares the account)
  size         0.25% of account equity at risk to a stop 0.5 daily ATR away; all rides together at most 5% of equity (owner's cap), 3 open
  stop         a stop order at Alpaca on fill
  exits        the 5-minute trend turns against the ride (2 of 3: ADX>=20 DI against, close/EMA20/EMA50 against, Aroon 50+ against);
               15:55 flat; Fed decision day: no entries and flat from 13:00
Undo: RIDE = None (alpacabot/dayride.py) and redeploy.
"""

import json
import logging
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import universe
from .data import _fetch

log = logging.getLogger("alpacabot.dayride")
RIDE = {"symbols": sorted(set(universe.STOCKS) | {"SPY", "QQQ"}), "risk_pct": 0.0025, "gross_cap": 0.05, "max_open": 3,
        "entry_from": dtime(10, 0), "entry_until": dtime(15, 0), "flat_at": dtime(15, 55), "fed_from": dtime(13, 0), "stop_atr": 0.5}


class DayRide:
    def __init__(self, broker, state_dir="state/alpaca_ride", cfg=None, fetch=_fetch):
        self.b, self.cfg, self.fetch = broker, cfg or RIDE, fetch
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.last_slot = None

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, default=str))
        tmp.replace(self.path)

    def journal(self, kind: str, now: datetime, **kw):
        with open(self.dir / "journal.jsonl", "a") as f:
            f.write(json.dumps({"t": now.isoformat(timespec="seconds"), "kind": kind, **kw}, default=str) + "\n")

    # ---------------------------------------------------------------- data
    def bars5(self, sym: str, now: datetime):
        end = now.astimezone(timezone.utc)
        return self.fetch(sym, "5Min", (end - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"), "iex")

    def daily(self, sym: str, now: datetime):
        end = now.astimezone(timezone.utc) - timedelta(minutes=20)
        return self.fetch(sym, "1Day", (end - timedelta(days=60)).strftime("%Y-%m-%dT00:00:00Z"), end.strftime("%Y-%m-%dT%H:%M:%SZ"), "sip")

    @staticmethod
    def completed(m, now: datetime):
        """Only 5-minute bars that have closed (a bar stamped t covers t..t+5 min)."""
        return m[m.index + timedelta(minutes=5) <= now.astimezone(timezone.utc)] if len(m) else m

    # ---------------------------------------------------------------- loop
    def step(self, now: datetime):
        """Call often during the session; acts once per completed 5-minute bar (20 s after it closes)."""
        if not self.cfg:
            return
        day = now.date().isoformat()
        if self.state.get("date") != day:
            self.state = {"date": day, "done": [], "open": self.state.get("open", {})}
        slot = (now.hour, now.minute // 5)
        if slot == self.last_slot or now.minute % 5 == 0 and now.second < 20:
            return
        self.last_slot = slot
        from fxbot.newsfilter import FOMC
        t = now.time()
        fed = day in FOMC and t >= self.cfg["fed_from"]
        if t >= self.cfg["flat_at"] or fed:
            for sym in list(self.state["open"]):
                self.exit(sym, now, "15:55 flat" if t >= self.cfg["flat_at"] else "Fed decision: flat")
            self.save()
            return
        self.manage(now)
        if self.cfg["entry_from"] <= t < self.cfg["entry_until"]:
            self.scan(now)
        self.save()

    def manage(self, now: datetime):
        from fxbot.strongtrend import reversal
        for sym, pos in list(self.state["open"].items()):
            st = self.b.order(pos["stop_id"]) if pos.get("stop_id") else {}
            if st.get("status") == "filled":
                self.record(sym, pos, float(st.get("filled_avg_price") or pos["stop"]), "stop", now)
                continue
            try:
                m = self.completed(self.bars5(sym, now), now)
            except Exception as exc:
                self.journal("error", now, where="bars", symbol=sym, error=str(exc)[:200])
                continue
            if len(m) >= 60 and int(reversal(m)[-1]) == -pos["side"]:
                self.exit(sym, now, "trend reversed")

    def scan(self, now: datetime):
        from fxbot.strongtrend import today_state
        cfg = self.cfg
        if len(self.state["open"]) >= cfg["max_open"]:
            return
        held = self.b.positions()
        equity = self.b.equity()
        used = sum(p["qty"] * p["entry"] for p in self.state["open"].values())
        room = cfg["gross_cap"] * equity - used
        for sym in cfg["symbols"]:
            if len(self.state["open"]) >= cfg["max_open"] or room <= 0:
                break
            if sym in self.state["done"] or sym in held or sym in self.state["open"]:
                continue
            try:
                m = self.completed(self.bars5(sym, now), now)
                d = self.daily(sym, now)
            except Exception as exc:
                self.journal("error", now, where="data", symbol=sym, error=str(exc)[:200])
                continue
            if len(m) < 250 or len(d) < 16:
                continue
            d = d[d.index.tz_convert("America/New_York").date < now.date()] if d.index.tz is not None else d
            d1 = pd.concat([d, d.iloc[[-1]].set_axis([pd.Timestamp(now.date(), tz="America/New_York").tz_convert("UTC")])])   # today's row:
            side = int(today_state(m.astype(float), d1, False)[-1])       # today_state reads the ATR of the completed days before it
            if not side:
                continue
            self.state["done"].append(sym)
            h, l, c = (d[k].astype(float) for k in ("high", "low", "close"))
            pc = c.shift(1)
            atr = float(np.nanmean(np.maximum.reduce([(h - l).to_numpy(), (h - pc).abs().to_numpy(), (l - pc).abs().to_numpy()])[-14:]))
            px = float(m["close"].iloc[-1])
            sd = cfg["stop_atr"] * atr
            if not (sd > 0 and px > 0):
                continue
            if side == -1 and not self.b.shortable(sym):
                self.journal("skip", now, symbol=sym, why="not easy to borrow")
                continue
            qty = int(min(cfg["risk_pct"] * equity / sd, min(room, cfg["gross_cap"] * equity / cfg["max_open"]) / px))
            if qty < 1:
                continue
            self.enter(sym, side, qty, px, sd, now)
            room -= qty * px

    def enter(self, sym: str, side: int, qty: int, px: float, sd: float, now: datetime):
        tag = now.strftime("%Y%m%d%H%M")
        o = self.b.submit_market(sym, qty, f"rd-o-{sym}-{tag}", side="buy" if side == 1 else "sell")
        st = self._wait(o, now)
        if st.get("status") != "filled":
            self.journal("entry_not_filled", now, symbol=sym, status=st.get("status"))
            return
        fill = float(st.get("filled_avg_price") or px)
        stop = round(fill - side * sd, 2)
        so = self.b.submit_order(sym, qty, "sell" if side == 1 else "buy", "stop", "day", f"rd-s-{sym}-{tag}", stop_price=stop)
        self.state["open"][sym] = {"side": side, "qty": qty, "entry": fill, "stop": stop, "stop_id": so.get("id"), "opened": now.isoformat()}
        self.journal("open", now, symbol=sym, side=side, qty=qty, fill=fill, stop=stop)

    def exit(self, sym: str, now: datetime, reason: str):
        pos = self.state["open"].get(sym)
        if not pos:
            return
        if pos.get("stop_id"):
            st = self.b.order(pos["stop_id"])
            if st.get("status") == "filled":
                self.record(sym, pos, float(st.get("filled_avg_price") or pos["stop"]), "stop", now)
                return
            try:
                self.b.cancel_order(pos["stop_id"])
            except Exception:
                log.exception("cancel ride stop %s", sym)
        o = self.b.submit_market(sym, pos["qty"], f"rd-c-{sym}-{now.strftime('%Y%m%d%H%M')}", side="sell" if pos["side"] == 1 else "buy")
        st = self._wait(o, now)
        px = float(st.get("filled_avg_price") or 0) if st.get("status") == "filled" else 0.0
        if not px:
            self.journal("exit_not_filled", now, symbol=sym, status=st.get("status"), reason=reason)
            return
        self.record(sym, pos, px, reason, now)

    def _wait(self, o, now, tries: int = 10) -> dict:
        import time
        if not o or not o.get("id"):
            return {"status": "rejected"}
        st = o
        for _ in range(tries):
            st = self.b.order(o["id"])
            if st.get("status") in ("filled", "rejected", "canceled", "expired"):
                break
            time.sleep(1)
        return st

    def record(self, sym: str, pos: dict, px: float, reason: str, now: datetime):
        pl = pos["side"] * (px - pos["entry"]) * pos["qty"]
        risk = abs(pos["entry"] - pos["stop"]) * pos["qty"]
        with open(self.dir / "trades.jsonl", "a") as f:
            f.write(json.dumps({"symbol": sym, "side": "long" if pos["side"] == 1 else "short", "qty": pos["qty"], "entry": pos["entry"],
                                "exit": px, "pl": round(pl, 2), "r": round(pl / risk, 3) if risk else None, "reason": reason,
                                "opened": pos["opened"], "closed": now.isoformat()}) + "\n")
        self.state["open"].pop(sym, None)
        self.journal("close", now, symbol=sym, px=px, pl=round(pl, 2), reason=reason)


def summary(state_dir="state/alpaca_ride") -> list[str]:
    p = Path(state_dir) / "trades.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    if not rows:
        return ["DAY RIDE: no trades yet"]
    pl = sum(r["pl"] for r in rows)
    w = sum(1 for r in rows if r["pl"] > 0)
    return [f"DAY RIDE: {len(rows)} trades, {w}W/{len(rows) - w}L, P&L ${pl:,.2f}, net {sum(r['r'] or 0 for r in rows):+.1f}R"]
