"""VWAP / ORB day-trade sleeve, PAPER (owner 2026-11-02: "do a 2 week backtest then run on vm with .05% per trade").

Runs inside the stock paper runner's minute loop (alpacabot.paper), next to the day ride. Same signal code as the backtest (alpacabot.vwapday):
PRIMARY ORB-30 + VWAP, VARIANT A VWAP reclaim, VARIANT B VWAP-band reversion, longs and shorts, spec DEFAULT parameters. NOT PROVEN: the
full 2018-2026 backtest has not been run; the owner chose paper trading after a 2-week sample.
  universe  PIT-approx: the vwapday pool ranked by 60-day dollar volume at the last close -> top 3 per sector (about 33 names)
  in play   at 10:00: RVOL30 (09:30-10:00 volume vs the prior 14 sessions, IEX minute bars) >= 1.5, price > $5, 20-day ADV > $50M, top 5
  entries   10:00-15:45 ET, on the minute after a closed 5-min signal bar, market order; no entries after 13:30 on a Fed decision day
  size      0.05% of account equity at risk to the stop; notional <= 25% of equity; max 4 new trades a day, 3 open, 2 per sector, 1 per symbol
  stop      a broker stop on fill; ORB trails it after +1R (max(break-even, VWAP - 0.5 sigma)); variant A sells 50% at +1R
  exits     5-min close back through VWAP (ORB / A), VWAP target or 60-minute time stop (B), daily limit -2R (everything out), flat 15:50
  data      IEX minute bars (the free plan has no real-time SIP): VWAP / RVOL differ from the consolidated tape - logged, judged on paper
  never     a symbol the account already holds (other sleeves share it); a short that is not easy to borrow; an overnight hold
Undo: VWAP_LIVE["paused"] = True (exits still run) or VWAP_LIVE = None, and redeploy.
"""

import json
import logging
import math
import time
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import vwapday as vd
from .data import _fetch

log = logging.getLogger("alpacabot.vwaplive")
ET = "America/New_York"
VWAP_LIVE = {"risk_pct": 0.0005, "notional_cap": 0.25, "max_new": 4, "max_open": 3, "sector_cap": 2, "daily_limit_r": 2.0,
             # owner 2026-11-04 ("do what gives the best ROI"): ORB off - it lost in every test: 2-week sample, the 89-session test (PF 0.26),
             # the minute-bar exit lab (133 trades PF 0.24, best of 18 exits 0.71). Open ORB trades keep their exits. UNDO: put "orb" back.
             "strategies": ["reclaim", "band"], "sides": [1, -1], "paused": False,
             # owner 2026-11-04 ("switch the losing sleeves to log only"): reclaim PF 0.50, band 0.64 in the 5-month test, no exit of 18 fixed
             # either. Signals are still scanned and journalled (skip_reason log_only); open trades keep their exits. UNDO: "log_only": False.
             "log_only": True,
             # owner 2026-11-05 ("if the S&P 500 is down overall let it short only, if up look for longs"): SPY prev close vs its rising /
             # falling SMA20 picks the side (VwapLive.spy_side_ok). UNDO: "spy_regime": False.
             "spy_vs_open": False,     # OFF 2026-11-05: failed (Trend halves 0.32/0.55 -> 0.51/0.36; reclaim, band worse). owner 2026-11-05: SPY above today's open -> longs only, below -> shorts only (log-only sleeve). UNDO: False
             "spy_regime": False,      # 2026-11-05 OFF: failed its tests (Top-Down PF 1.51 -> 1.46 OOS, Trend 0.40 -> 0.33)
             "start_date": "2026-11-03"}     # first session it may trade (after the owner sees the 2-week sample)


class VwapLive:
    def __init__(self, broker, state_dir="state/alpaca_vwap", cfg=None, fetch=_fetch, sleep=time.sleep):
        self.b, self.cfg, self.fetch, self.sleep = broker, cfg or VWAP_LIVE, fetch, sleep
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.last_min = None
        self.adx_cache = {}
        self.prefix = "vw"                                   # client order id prefix (the Velez sleeve subclasses this class)

    # ---------------------------------------------------------------- plumbing
    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, default=str))
        tmp.replace(self.path)

    def journal(self, kind: str, now: datetime, **kw):
        with open(self.dir / "journal.jsonl", "a") as f:
            f.write(json.dumps({"t": now.isoformat(timespec="seconds"), "kind": kind, **kw}, default=str) + "\n")

    @staticmethod
    def minute_of(now: datetime) -> int:
        n = now.astimezone(ZoneInfo(ET))
        return (n.hour * 60 + n.minute) - 570

    def session_len(self, now: datetime) -> int:
        """390, or 210 on a 13:00 half day (Alpaca calendar; 390 if it cannot be read)."""
        if "sess" not in self.state:
            n = 390
            try:
                day = now.date().isoformat()
                from .paper import TRADE_URL
                cal = self.b._req("GET", f"{TRADE_URL}/calendar", params={"start": day, "end": day}).json()
                if cal and str(cal[0].get("close", "16:00")).startswith("13:"):
                    n = 210
            except Exception:
                pass
            self.state["sess"] = n
        return int(self.state["sess"])

    # ---------------------------------------------------------------- data
    def daily_refs(self, sym: str, now: datetime) -> dict:
        start = (now - timedelta(days=150)).astimezone(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        end = pd.Timestamp(now.date(), tz=ET).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")       # completed days only
        d = self.fetch(sym, "1Day", start, end, "sip")
        if len(d) < 61:
            return {}
        c, h, l, v = (d[k].to_numpy(float) for k in ("close", "high", "low", "volume"))
        pc = np.r_[np.nan, c[:-1]]
        tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
        return {"atrd": float(vd._wilder(tr)[-1]), "prev_close": float(c[-1]), "adv20": float(np.mean((c * v)[-20:])),
                "dvol60": float(np.mean((c * v)[-60:])), "advsh20": float(np.mean(v[-20:]))}

    def bars(self, sym: str, now: datetime, m: int, days: int = 0) -> pd.DataFrame:
        """IEX minute bars from 09:30 (days back) to now, completed bars only, as vwapday.minute_frame rows."""
        o = pd.Timestamp(now.date(), tz=ET) - pd.Timedelta(days=days) + pd.Timedelta(hours=9, minutes=30)
        df = self.fetch(sym, "1Min", o.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
                        now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "iex")
        mf = vd.minute_frame(df) if len(df) else pd.DataFrame(columns=["o", "h", "l", "c", "v", "m", "d"])
        if len(mf):
            today = now.date()
            mf = mf[(mf["d"] != today) | (mf["m"] < m)]
        return mf

    def today_sd(self, sym: str, now: datetime, m: int, last_px: float | None = None):
        """Completed minutes 0..m-1 on a full grid (+ the current minute at last_px when given: the entry price for the signal code)."""
        key = (sym, now.date(), m)
        if getattr(self, "_mcache_key", None) != (now.date(), m):
            self._mcache, self._mcache_key = {}, (now.date(), m)
        if key not in self._mcache:
            mf = self.bars(sym, now, m)
            self._mcache[key] = mf[mf["d"] == now.date()] if len(mf) else mf
        mf = self._mcache[key]
        if not len(mf):
            return None
        sd = vd.symbol_days(mf, {now.date()}, {now.date(): m}).get(now.date())
        if sd is None:
            return None
        sd["n"] = self.session_len(now)
        if last_px is not None:
            for k in ("o", "h", "l", "c"):
                sd[k] = np.r_[sd[k], np.float32(last_px)]
            sd["v"] = np.r_[sd["v"], np.float32(0)]
        return sd

    def adx5(self, sym: str, now: datetime) -> np.ndarray:
        key = (sym, now.date(), self.minute_of(now) // 5)
        if key not in self.adx_cache:
            mf = self.bars(sym, now, self.minute_of(now), days=7)
            arr = vd.adx5_by_day(mf).get(now.date()) if len(mf) else None
            self.adx_cache = {k: v for k, v in self.adx_cache.items() if k[1] == now.date()}
            self.adx_cache[key] = arr if arr is not None else np.full(78, np.nan, dtype=np.float32)
        return self.adx_cache[key]

    # ---------------------------------------------------------------- daily setup
    def build_universe(self, now: datetime):
        refs, by_sec = {}, {}
        for s in sorted(vd.SECTOR):
            if s in vd.MEMBER_FROM and now.date() < vd.MEMBER_FROM[s]:
                continue
            try:
                r = self.daily_refs(s, now)
            except Exception as exc:
                self.journal("error", now, where="daily", symbol=s, error=str(exc)[:160])
                continue
            if r:
                refs[s] = r
                by_sec.setdefault(vd.sector_at(s, now.date()), []).append((r["dvol60"], s))
        uni = [s for sec in vd.ETFS for _, s in sorted(by_sec.get(sec, []), reverse=True)[:3]]
        self.state.update(universe=uni, refs={s: refs[s] for s in uni})
        self.journal("universe", now, names=uni)

    def build_inplay(self, now: datetime):
        cand = []
        for s in self.state.get("universe", []):
            try:
                mf = self.bars(s, now, 30, days=30)
            except Exception as exc:
                self.journal("error", now, where="rvol", symbol=s, error=str(exc)[:160])
                continue
            f30 = mf[mf["m"] < 30].groupby("d")["v"].sum()
            today = now.date()
            if today not in f30.index:
                continue
            prev = f30[f30.index < today].tail(14)
            if len(prev) < 10 or prev.mean() <= 0:
                continue
            r = float(f30[today] / prev.mean())
            ref = self.state["refs"].get(s, {})
            if r >= 1.5 and ref.get("prev_close", 0) > 5 and ref.get("adv20", 0) > 50e6:
                cand.append((r, s))
        self.state["inplay"] = [s for _, s in sorted(cand, reverse=True)[:5]]
        self.state["rvol"] = {s: round(r, 2) for r, s in cand}
        self.journal("inplay", now, names=self.state["inplay"], rvol=self.state["rvol"])

    # ---------------------------------------------------------------- loop
    def step(self, now: datetime):
        if not self.cfg:
            return
        day = now.date().isoformat()
        if self.state.get("date") != day:
            carried = self.state.get("open", {})
            self.state = {"date": day, "open": carried, "carried": sorted(carried), "done": [], "traded": [], "n_new": 0, "realized_R": 0.0}
            if carried:
                self.journal("carried_over", now, symbols=sorted(carried))
        m = self.minute_of(now)
        if m < 0 or m >= 400 or now.second < 8 or m == self.last_min:
            return
        self.last_min = m
        sess = self.session_len(now)
        last_entry, flat = (195, 200) if sess <= 210 else (375, 380)
        for sym in [x for x in self.state.get("carried", []) if x in self.state["open"]]:
            self.safe_exit(sym, now, "carried over (missed flat)")
        self.state["carried"] = []
        if m >= 20 and "universe" not in self.state:
            self.build_universe(now)
        if m >= 30 and "inplay" not in self.state and "universe" in self.state:
            self.build_inplay(now)
        if m >= flat:
            for sym in list(self.state["open"]):
                self.safe_exit(sym, now, "eod_1550")
            self.save()
            return
        self.manage(now, m)
        if self.cfg.get("paused") or self.state.get("stopped") or "inplay" not in self.state or day < self.cfg.get("start_date", ""):
            self.save()
            return
        from fxbot.newsfilter import FOMC
        if day in FOMC and now.time() >= dtime(13, 30):
            self.save()
            return
        if 30 <= m <= last_entry:
            self.scan(now, m)
        self.save()

    def scan(self, now: datetime, m: int):
        cfg, st = self.cfg, self.state
        if st["n_new"] >= cfg["max_new"] or len(st["open"]) >= cfg["max_open"]:
            return
        spy_last = None
        try:
            bid, ask = (float(x) for x in self.b.quote("SPY")[:2])
            spy_last = (bid + ask) / 2
            spy = self.today_sd("SPY", now, m, spy_last)
        except Exception as exc:
            self.journal("error", now, where="spy", error=str(exc)[:160])
            return
        if spy is None:
            return
        held = self.b.positions()
        cands = []
        for sym in st["inplay"]:
            if sym in st["traded"] or sym in st["open"] or held.get(sym):
                continue
            try:
                bid, ask = (float(x) for x in self.b.quote(sym)[:2])
            except Exception as exc:
                self.journal("error", now, where="quote", symbol=sym, error=str(exc)[:160])
                continue
            dd = st["refs"].get(sym, {})
            for strat in cfg["strategies"]:
                for side in cfg["sides"]:
                    key = f"{strat}:{side}:{sym}"
                    if key in st["done"]:
                        continue
                    try:
                        sd = self.today_sd(sym, now, m, ask if side == 1 else bid)
                        if sd is None:
                            continue
                        if strat == "band":
                            sd["adx5"] = self.adx5(sym, now)
                        sig, _ = vd.SIGNALS[strat](sd, spy, dd, side, vd.DEFAULT[strat])
                    except Exception as exc:
                        self.journal("error", now, where="signal", symbol=sym, strategy=strat, error=str(exc)[:160])
                        continue
                    if not sig:
                        continue
                    if sig["j"] < m:                        # its first signal of the day was earlier: missed (the backtest takes only that one)
                        st["done"].append(key)
                        self.journal("signal_missed", now, symbol=sym, strategy=strat, side=side, signal_minute=sig["s"])
                        continue
                    cands.append((strat, side, sym, sig))
        for strat, side, sym, sig in cands:
            key = f"{strat}:{side}:{sym}"
            why = None
            if st["n_new"] >= self.cfg["max_new"]:
                why = "max_trades"
            elif len(st["open"]) >= self.cfg["max_open"]:
                why = "max_open"
            elif sym in st["traded"] or sym in st["open"]:
                why = "one_per_symbol"
            elif sum(1 for p in st["open"].values() if p["sector"] == vd.sector_at(sym, now.date())) >= self.cfg["sector_cap"]:
                why = "sector_cap"
            elif side == -1 and not self.b.shortable(sym):
                why = "skipped_htb"
            st["done"].append(key)
            if why:
                self.journal("signal", now, symbol=sym, strategy=strat, side=side, taken=False, skip_reason=why, entry=sig["entry"], stop=sig["stop"])
                continue
            self.enter(sym, strat, side, sig, now, m)

    def spy_side_ok(self, side: int, now) -> bool:
        """Owner 2026-11-05: SPY's previous close over its rising SMA20 -> longs only; under a falling one -> shorts only; else both.
        Cached per day; if SPY cannot be read the signal is allowed (the gate never blocks on a data error)."""
        day = now.date().isoformat()
        if self.state.get("spy_regime_day") != day:
            try:
                from .trendopt import daily_flags
                start = (now - timedelta(days=90)).astimezone(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
                end = pd.Timestamp(now.date(), tz=ET).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
                lo, sh = daily_flags(self.fetch("SPY", "1Day", start, end, "sip")).get("_next", (False, False))
                self.state["spy_regime"] = "long" if lo and not sh else "short" if sh and not lo else "both"
            except Exception as exc:
                self.journal("error", now, where="spy_regime", error=str(exc)[:160])
                self.state["spy_regime"] = "both"
            self.state["spy_regime_day"] = day
        reg = self.state.get("spy_regime", "both")
        return reg == "both" or (reg == "long" and side == 1) or (reg == "short" and side == -1)

    def spy_open_side(self, now) -> int:
        """Owner 2026-11-05: +1 when SPY's last closed minute is above today's open, -1 below, 0 equal / unreadable (then both sides)."""
        key = now.strftime("%Y-%m-%d %H:%M")
        if self.state.get("spy_open_key") != key:
            v = 0
            try:
                start = pd.Timestamp(now.date(), tz=ET).replace(hour=9, minute=30).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
                df = self.fetch("SPY", "1Min", start, now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "iex")
                idx = pd.DatetimeIndex(df.index)
                df = df[(idx + pd.Timedelta(minutes=1)) <= pd.Timestamp(now)]
                if len(df):
                    o0, last = float(df["open"].iloc[0]), float(df["close"].iloc[-1])
                    v = 1 if last > o0 else -1 if last < o0 else 0
            except Exception as exc:
                self.journal("error", now, where="spy_vs_open", error=str(exc)[:160])
            self.state["spy_open_key"], self.state["spy_open_side"] = key, v
        return int(self.state.get("spy_open_side", 0))

    def enter(self, sym, strat, side, sig, now, m):
        if self.cfg.get("spy_vs_open"):
            so = self.spy_open_side(now)
            if so and so != side:
                self.journal("signal", now, symbol=sym, strategy=strat, side=side, taken=False, skip_reason="spy_vs_open",
                             spy_vs_open=so, entry=sig["entry"], stop=sig["stop"])
                return
        if self.cfg.get("spy_regime") and not self.spy_side_ok(side, now):
            self.journal("signal", now, symbol=sym, strategy=strat, side=side, taken=False, skip_reason="spy_regime",
                         regime=self.state.get("spy_regime"), entry=sig["entry"], stop=sig["stop"])
            return
        if self.cfg.get("log_only"):                         # owner 2026-11-04 ("switch the losing sleeves to log only"): signal logged, no order
            self.journal("signal", now, symbol=sym, strategy=strat, side=side, taken=False, skip_reason="log_only", entry=sig["entry"],
                         stop=sig["stop"])
            return
        equity = self.b.equity()
        R0 = side * (sig["entry"] - sig["stop"])
        if R0 <= 0:
            return
        qty = int(min(self.cfg["risk_pct"] * equity / R0, self.cfg["notional_cap"] * equity / sig["entry"]))
        self.journal("signal", now, symbol=sym, strategy=strat, side=side, taken=qty >= 1, skip_reason="" if qty >= 1 else "qty_zero",
                     entry=sig["entry"], stop=sig["stop"], rvol30=self.state.get("rvol", {}).get(sym))
        if qty < 1:
            return
        tag = now.strftime("%Y%m%d%H%M")
        o = self.b.submit_market(sym, qty, f"{self.prefix}-o-{sym}-{tag}", side="buy" if side == 1 else "sell")
        stt = self._wait(o)
        if stt.get("status") != "filled":
            self.journal("entry_not_filled", now, symbol=sym, status=stt.get("status"))
            return
        fill = float(stt.get("filled_avg_price") or sig["entry"])
        qty = int(float(stt.get("filled_qty") or qty))
        self.state["n_new"] += 1
        self.state["traded"].append(sym)
        pos = {"strategy": strat, "side": side, "qty": qty, "qty0": qty, "entry": fill, "stop": sig["stop"], "R0": side * (fill - sig["stop"]),
               "signal_entry": sig["entry"], "entry_min": m, "opened": now.isoformat(), "sector": vd.sector_at(sym, now.date()), "parts": 0,
               "best": fill, "trail": False, "realized_R": 0.0}
        if pos["R0"] <= 0:                                   # filled at / through its stop: out at once
            self.state["open"][sym] = pos
            self.safe_exit(sym, now, "filled_through_stop")
            return
        so = self.b.submit_order(sym, qty, "sell" if side == 1 else "buy", "stop", "day", f"{self.prefix}-s-{sym}-{tag}", stop_price=round(sig["stop"], 2))
        pos["stop_id"] = so.get("id") if isinstance(so, dict) else None
        self.state["open"][sym] = pos
        self.journal("open", now, symbol=sym, strategy=strat, side=side, qty=qty, fill=fill, stop=sig["stop"], slippage_bps=round(
            side * (fill - sig["entry"]) / sig["entry"] * 1e4, 2))
        self.save()

    def manage(self, now: datetime, m: int):
        for sym, pos in list(self.state["open"].items()):
            if pos.get("stop_id"):
                try:
                    so = self.b.order(pos["stop_id"])
                except Exception:
                    so = {}
                if so.get("status") == "filled":
                    self.record(sym, pos, float(so.get("filled_avg_price") or pos["stop"]), pos["qty"], "stop", now)
                    continue
            try:
                sd = self.today_sd(sym, now, m)
                bid, ask = (float(x) for x in self.b.quote(sym)[:2])
            except Exception as exc:
                self.journal("error", now, where="manage", symbol=sym, error=str(exc)[:160])
                continue
            if sd is None or len(sd["c"]) < 1:
                continue
            side, e, R0 = pos["side"], pos["entry"], pos["R0"]
            vw, sg = vd.vwap_sigma(sd)
            L = len(sd["c"]) - 1
            j0 = min(pos["entry_min"], L)
            best = float(np.max(sd["h"][j0:])) if side == 1 else float(np.min(sd["l"][j0:]))
            pos["best"] = max(pos["best"], best) if side == 1 else min(pos["best"], best)
            px = bid if side == 1 else ask
            strat = pos["strategy"]
            if strat == "band":
                if side * (px - float(vw[L])) >= 0:
                    self.safe_exit(sym, now, "target")
                    continue
                if m >= pos["entry_min"] + 60:
                    self.safe_exit(sym, now, "time")
                    continue
            if strat == "reclaim" and pos["parts"] == 0 and side * (px - (e + side * R0)) >= 0:
                self.partial(sym, pos, now)
                if sym not in self.state["open"]:
                    continue
            if strat == "orb":
                if not pos["trail"] and side * (pos["best"] - e) >= R0:
                    pos["trail"] = True
                if pos["trail"]:
                    new = max(e, float(vw[L]) - 0.5 * float(sg[L])) if side == 1 else min(e, float(vw[L]) + 0.5 * float(sg[L]))
                    if side * (new - pos["stop"]) >= max(0.01, 0.1 * R0):      # step >= 0.1R: no cancel/replace every minute
                        self.move_stop(sym, pos, round(new, 2), now)
            if strat in ("orb", "reclaim") and m % 5 == 0 and m > pos["entry_min"] and side * (float(sd["c"][L]) - float(vw[L])) < 0:
                self.safe_exit(sym, now, "vwap")

    # ---------------------------------------------------------------- orders
    def _wait(self, o, tries: int = 15, pause: float = 1.0) -> dict:
        if not o or not o.get("id"):
            return {"status": "rejected"}
        st = o
        for _ in range(tries):
            st = self.b.order(o["id"])
            if st.get("status") in ("filled", "rejected", "canceled", "expired"):
                break
            self.sleep(pause)
        return st

    def _cancel_stop(self, pos) -> str:
        if not pos.get("stop_id"):
            return ""
        try:
            self.b.cancel_order(pos["stop_id"])
        except Exception:
            log.exception("cancel stop")
        st = self._wait({"id": pos["stop_id"]}, tries=20, pause=0.5)
        return st.get("status", "")

    def move_stop(self, sym, pos, new, now):
        if self._cancel_stop(pos) == "filled":
            so = self.b.order(pos["stop_id"])
            self.record(sym, pos, float(so.get("filled_avg_price") or pos["stop"]), pos["qty"], "stop", now)
            return
        try:
            so = self.b.submit_order(sym, pos["qty"], "sell" if pos["side"] == 1 else "buy", "stop", "day",
                                     f"{self.prefix}-t-{sym}-{now:%Y%m%d%H%M}", stop_price=new)
            pos.update(stop=new, stop_id=so.get("id") if isinstance(so, dict) else None)
            self.journal("trail", now, symbol=sym, stop=new)
        except Exception as exc:
            pos["stop_id"] = None
            self.journal("error", now, where="move_stop", symbol=sym, error=str(exc)[:160])
            self.safe_exit(sym, now, "stop_replace_failed")

    def partial(self, sym, pos, now):
        part = pos["qty"] // 2
        if part < 1:
            return
        if self._cancel_stop(pos) == "filled":
            so = self.b.order(pos["stop_id"])
            self.record(sym, pos, float(so.get("filled_avg_price") or pos["stop"]), pos["qty"], "stop", now)
            return
        o = self.b.submit_market(sym, part, f"{self.prefix}-p-{sym}-{now:%Y%m%d%H%M}", side="sell" if pos["side"] == 1 else "buy")
        st = self._wait(o)
        if st.get("status") == "filled":
            self.record(sym, pos, float(st.get("filled_avg_price") or 0), part, "tp1", now)
        pos["parts"] = 1
        if pos["qty"] > 0 and sym in self.state["open"]:
            so = self.b.submit_order(sym, pos["qty"], "sell" if pos["side"] == 1 else "buy", "stop", "day", f"{self.prefix}-s2-{sym}-{now:%Y%m%d%H%M}",
                                     stop_price=round(pos["stop"], 2))
            pos["stop_id"] = so.get("id") if isinstance(so, dict) else None

    def safe_exit(self, sym, now, reason):
        try:
            self.exit(sym, now, reason)
        except Exception as exc:
            log.exception("vwap exit %s", sym)
            self.journal("error", now, where="exit", symbol=sym, error=str(exc)[:200])

    def exit(self, sym, now, reason):
        pos = self.state["open"].get(sym)
        if not pos:
            return
        if self._cancel_stop(pos) == "filled":
            so = self.b.order(pos["stop_id"])
            self.record(sym, pos, float(so.get("filled_avg_price") or pos["stop"]), pos["qty"], "stop", now)
            return
        if pos.get("exit_id"):
            st = self.b.order(pos["exit_id"])
            if st.get("status") == "filled":
                self.record(sym, pos, float(st.get("filled_avg_price") or 0), pos["qty"], reason, now)
                return
            if st.get("status") not in ("canceled", "expired", "rejected"):
                return
        try:
            held = int(self.b.positions().get(sym, 0))
        except Exception:
            held = None
        if held is not None and (held == 0 or (held > 0) != (pos["side"] == 1)):
            self.journal("gone_at_broker", now, symbol=sym, held=held, reason=reason)
            self.state["open"].pop(sym, None)
            return
        o = self.b.submit_market(sym, pos["qty"], f"{self.prefix}-c-{sym}-{now:%Y%m%d%H%M%S}", side="sell" if pos["side"] == 1 else "buy")
        pos["exit_id"] = o.get("id") if isinstance(o, dict) else None
        self.save()
        st = self._wait(o, tries=30)
        if st.get("status") != "filled":
            self.journal("exit_not_filled", now, symbol=sym, status=st.get("status"), reason=reason)
            return
        self.record(sym, pos, float(st.get("filled_avg_price") or 0), pos["qty"], reason, now)

    def record(self, sym, pos, px, qty, reason, now):
        side = pos["side"]
        pl = side * (px - pos["entry"]) * qty
        r = side * (px - pos["entry"]) / pos["R0"] * qty / pos["qty0"] if pos["R0"] > 0 else 0.0
        pos["realized_R"] = pos.get("realized_R", 0.0) + r
        pos["qty"] -= qty
        done = pos["qty"] <= 0
        with open(self.dir / "trades.jsonl", "a") as f:
            f.write(json.dumps({"date": now.date().isoformat(), "symbol": sym, "strategy": pos["strategy"], "side": "long" if side == 1 else "short",
                                "qty": qty, "entry": pos["entry"], "exit": px, "pl": round(pl, 2), "r_part": round(r, 4),
                                "r_trade": round(pos["realized_R"], 4) if done else None, "reason": reason, "opened": pos["opened"],
                                "closed": now.isoformat(), "final": done}) + "\n")
        self.journal("close" if done else "partial", now, symbol=sym, px=px, qty=qty, pl=round(pl, 2), reason=reason)
        self.state["realized_R"] = self.state.get("realized_R", 0.0) + r
        if done:
            self.state["open"].pop(sym, None)
        if self.state["realized_R"] <= -self.cfg["daily_limit_r"] and not self.state.get("stopped"):
            self.state["stopped"] = True
            self.journal("daily_limit", now, realized_R=round(self.state["realized_R"], 3))
            for s2 in list(self.state["open"]):
                if s2 != sym:
                    self.safe_exit(s2, now, "daily_limit")


def summary(state_dir="state/alpaca_vwap") -> list[str]:
    p = Path(state_dir) / "trades.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    fin = [r for r in rows if r.get("final")]
    if not fin:
        return ["", "VWAP / ORB day-trade sleeve (paper, 0.05% risk): no closed trades yet"]
    rs = [r["r_trade"] for r in fin]
    w = sum(x > 0 for x in rs)
    lo = -sum(x for x in rs if x < 0)
    pf = sum(x for x in rs if x > 0) / lo if lo > 0 else float("inf")
    out = ["", f"VWAP / ORB day-trade sleeve (paper, 0.05% risk): {len(fin)} trades, {w}W/{len(fin) - w}L, PF {pf:.2f}, "
               f"net {sum(rs):+.2f}R, P&L ${sum(r['pl'] for r in rows):+,.0f}"]
    for strat in ("orb", "reclaim", "band"):
        x = [r["r_trade"] for r in fin if r["strategy"] == strat]
        if x:
            out.append(f"  {strat}: {len(x)} trades, {sum(v > 0 for v in x)}W, net {sum(x):+.2f}R")
    return out
