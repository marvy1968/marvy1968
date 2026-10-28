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
from .strategies import BoxConfirm, ConsensusMR, F0Reversion, HybridBox, InBox, LienFader, OversoldConfluence, PristineFx, VelezFx, RangeBox200, RangeMR
from .playbook import PlaybookRules, live_setup

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
          # owner 2026-10-22 Velez hybrid: with the 20/200 SMA trend only (3 months PF 0.52 -> 1.63, 2 weeks -1.27% -> -0.53%; few trades)
          "confirm_h1": {"make": lambda: VelezFx(BoxConfirm(mode="continuation", vol_mult=1.5, atr_ratio=1.1, vote=CONFIRM_VOTE), env=True), "gran": "H1",
                         "count": 600, "minutes": 60},
          # owner 2026-10-19 "place my experts inside the range box" (fxbot.strategies.InBox: ranging pairs only, longs in the lower half / shorts in
          # the upper half, the box's stop and take-profit lines). Check (last 12 months): range mean-reversion alone PF 0.48, -98.6% -> inside the
          # box PF 0.66 (118 trades, -4.5%); consensus inside the box PF 0.44 (harmful) and F0 3 trades: those two only LOG their signals.
          # owner 2026-10-22 Velez hybrid: + look left (no prior 20-bar high / low in the way; 3 months PF 1.84 -> 2.21, +3.22% -> +3.83%)
          "boxmr_h1": {"make": lambda: VelezFx(InBox(RangeMR(bb_k=2.0, stop_atr=1.5, asian_only=False), tp_frac=0.5, label="range_mr"),
                                               env=False, look_left=True), "gran": "H1",
                       "count": 600, "minutes": 60},
          "boxcons_h1": {"make": lambda: InBox(ConsensusMR(votes=3), tp_frac=0.5, label="consensus"), "gran": "H1", "count": 600, "minutes": 60},
          "boxf0_h4": {"make": lambda: InBox(F0Reversion(n=100), tp_frac=0.5, label="f0"), "gran": "H4", "count": 400, "minutes": 240},
          # owner 2026-10-22 (daily_confluence_strategy.py): majority of 4 oversold / overbought readings + volume, daily and 4-hour bars, 0.5%
          "conf_d1": {"make": lambda: OversoldConfluence(), "gran": "D", "count": 300, "minutes": 1440},
          "conf_h4": {"make": lambda: OversoldConfluence(), "gran": "H4", "count": 400, "minutes": 240},
          # owner 2026-10-22 (Pristine / iFundTraders FX research): Changing of the Guard on 4-hour bars - 3 red bars then a green bar closing
          # above the prior high (shorts mirror), live exits. 2 weeks: 15 trades 73% PF 6.06 +10.7%; 3 months: 89 trades 38% PF 1.52 +13.8%
          # (the only Pristine rule profitable in both windows; on 1-hour bars it lost, PF 0.35). 0.5% risk.
          "cog_h4": {"make": lambda: PristineFx("cog", "stop"), "gran": "H4", "count": 400, "minutes": 240},
          # owner 2026-10-25 "Break & Retest Trading Playbook" (fxbot.playbook, built as written): 4-hour swing zones touched twice, a body close
          # beyond the zone, retest, pin bar / engulfing, buy-stop (sell-stop) beyond the confirmation candle valid 5 bars, stop under the whole
          # retest structure, target the next opposing 4-hour swing level (>= 2R or no trade), out on a close back into the zone. No break-even
          # move (it cut the winners: 3 years PF 1.27 -> 1.62 without it, 12 months 1.13 -> 1.42; 91 trades / 3 years, win 27%, +36.8R).
          # Owner approved: its OWN exits only - exempt from loss_cut_r, take_profit_r and the 10-minute neg exit. 0.5% risk.
          "retest_h1": {"playbook": PlaybookRules(be_at_r=0.0), "gran": "H1", "count": 1500, "minutes": 60}}     # owner's range box (ranging pairs); levels = the
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
          # owner 2026-10-27 ("analyze all OANDA experts and refine and optimize"): experts_review_fx (live exits, bid/ask, 12 months / 3 years, PF at the
          # normal spread): hybrid_h1 0.69 / 0.69, confirm_h1 0.79 / 0.80, boxmr_h1 0.68 / 0.64 (own exits 0.67 / 0.98), conf_d1 0.58 / 0.70, cog_h4 0.70 / 0.71
          # (-30% / -66%), every one worse at 2x the spread; the earlier 3-month passes were one good window. Live ledger: 18 closes, -$2,672. These five now
          # only LOG their signals (paused). lien_h1 stays (12 months PF 2.75, 3 years 0.94 - flat; the owner's 10-year fx-experts backtest PF 1.18) and
          # retest_h1 (the playbook, no live fills yet). UNDO: put the names back in this list (their signals are still logged and graded).
          "trade": ["lien_h1", "retest_h1"],   # conf_h4 log-only (owner 2026-10-22: lost 2 weeks PF 0.57, 3 months PF 0.76)
          "risk_by_strategy": {"hybrid_h1": 0.005, "box_h1": 0.005, "confirm_h1": 0.005, "boxmr_h1": 0.005, "conf_d1": 0.005, "conf_h4": 0.005, "cog_h4": 0.005, "retest_h1": 0.005},   # owner 2026-10-22: boxmr_h1 back to 0.5% (3 losses at 1%, -$255 live)
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
          "take_profit_r": 1.25,
          # owner 2026-10-22: "if a trade is negative for more than 10 minutes, exit the trade" (mid vs entry mid, continuously). None = off.
          "neg_exit_min": 10,                                # the hybrid trades at half the normal 1% (owner)
          # owner 2026-10-26 ("Do this": the fx-experts bot lost ~$700 overnight with no rails): every OPEN TRADE OF ANOTHER BOT on the shared
          # account gets a broker stop-loss if it has none (loss at the stop = stop_pct of NAV) and is closed at market once its unrealized loss
          # reaches cut_pct of NAV. Undo: "foreign_guard": None (or delete the keys) and redeploy.
          # owner 2026-10-27 ("OANDA going up nicely if [we] don't give back profit"): lock_start_pct / lock_keep - once one of the fx-experts bot's
          # trades has been up lock_start_pct of NAV, its broker stop is moved to keep lock_keep of its best gain so far (only ever tightened, its own
          # target untouched; the broker holds the stop, so it protects even if this runner is down). A guard, not backtested. Undo: drop the two keys.
          "foreign_guard": {"stop_pct": 0.004, "cut_pct": 0.0025, "lock_start_pct": 0.002, "lock_keep": 0.5},
          # owner 2026-10-27 ("implement the account profit lock on the OANDA side"): the Alpaca rule on this runner's book - once the book is up
          # start_pct on the trading day (17:00 New York to 17:00), giving back giveback_frac of that day's best gain stops NEW entries until the next
          # trading day; open trades keep their broker stops / targets and exits. A guard, not an edge (not backtested). Undo: "profit_lock": None.
          "profit_lock": {"start_pct": 0.004, "giveback_frac": 0.35},
          # owner 2026-10-28 ("if there's major news coming out, just pause trading, flattening ... until after, like the Fed"): from before_min
          # before to after_min after a HIGH-impact release for either currency of a pair (ForexFactory weekly calendar, cached 6 h; Fed + jobs
          # report if it cannot be read), this runner closes its own trades on that pair, drops its waiting playbook orders there and takes no
          # entries there. A guard, not an edge (fxbot.newsfilter: too few trades to prove a gain). Undo: "news_pause": None and redeploy.
          "news_pause": {"before_min": 60, "after_min": 60},
          # owner 2026-10-28 ("two trend filters ... a long trend filter so we can swing trade it ... if the dip is great enough the short trend
          # filter will pick it up ... ride it today only" and "if our short-term trend reverses against us, we have to immediately flatten those
          # trades"; "shorten the tests ... prove it in paper money"). fxbot.strongtrend, on PAPER without a finished backtest:
          #   long        an entry may go WITH the pair's long trend (EMA50 on one side of EMA200 for 60+ days and 2 of 3 mild readings that way)
          #   short       otherwise only on a hard move today (2 of 3 strict readings on the 1-hour bars and 0.75+ daily ATR from the day's open)
          #               - such a trade is a day trade: closed at day_close New York time (before the 17:00 rollover); entries until 15:00
          #   reversal    any open trade is closed once the 1-hour trend turns against it (2 of 3: ADX>=20 DI against, close/EMA20/EMA50
          #               against, Aroon 50+ against), checked at every completed 1-hour bar
          # Undo: "trend_filters": None (or set a key to False) and redeploy.
          # 2026-10-28 2-week test: no major has a long trend now, and over 92 days the filters let almost no live-expert trade through
          # (lien / retest 0 trades) - switched OFF until the owner picks; to switch on put the dict below back.
          "trend_filters": None}   # {"long": True, "short": True, "reversal": True, "day_close": "16:00"}


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
        self.foreign_best = st.get("foreign_best", {})     # fx-experts trade id -> best unrealized P&L seen (foreign_guard profit lock)
        self.pending = st.get("pending", {})              # owner 2026-10-25 playbook: virtual buy-stop / sell-stop orders waiting for their trigger
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
        tmp.write_text(json.dumps({"groups": self.groups, "last_bar": self.last_bar, "day": self.day, "pending": self.pending, "foreign_best": self.foreign_best}, indent=1, default=str))
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
        pl = self.cfg.get("profit_lock")
        if pl:                                         # keep the day's gains: see CONFIG["profit_lock"]
            gain = eq / self.day["start"] - 1
            self.day["peak"] = max(float(self.day.get("peak", 0.0)), gain)
            if not self.day.get("locked") and self.day["peak"] >= pl["start_pct"] and gain <= self.day["peak"] * (1 - pl["giveback_frac"]):
                self.day["locked"] = True
                self.journal("profit_lock", peak=round(self.day["peak"], 5), gain=round(gain, 5))
            if self.day.get("locked"):
                return f"profit lock (book peaked {self.day['peak']:+.2%} today, now {gain:+.2%}; open trades keep their exits)"
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
        flat = self.dir / "FLATTEN_NOW"
        if flat.exists():                                  # owner: close this runner's own groups now (never the fx-experts bot's trades)
            for gid in list(self.groups):
                self.close_group(gid, "owner flatten")
            flat.unlink(missing_ok=True)
            self.journal("flatten_now", closed=True)
            trades = self.api.open_trades()
        if not market_open(now):
            self.status(trades)
            return
        self.guard_foreign(trades)
        news = self.news_pause(now)
        if news:
            for gid in list(self.groups):
                if gid in self.groups and news.get(self.groups[gid]["instrument"]):
                    self.close_group(gid, news[self.groups[gid]["instrument"]])
            for key, p in list(self.pending.items()):
                if news.get(p["instrument"]):
                    self.pending.pop(key, None)
                    self.journal("news_pause_drop", strategy=p["strategy"], instrument=p["instrument"], why=news[p["instrument"]])
            trades = self.api.open_trades()
        self.day_close(now)
        eq = self.equity(trades)
        why = self.entries_allowed(eq)
        for gid in list(self.groups):
            self.manage(gid, eq, why)
        self.check_pending(eq, why, trades)
        for name in self.cfg["strategies"]:
            spec = STRATS[name]
            for inst in PAIRS:
                try:
                    self.scan(name, spec, inst, eq, why or news.get(inst, ""), trades)
                except OandaError as exc:
                    self.journal("error", where="scan", strategy=name, instrument=inst, error=str(exc)[:300])
        self.save()
        self.status(self.api.open_trades())

    # ---------------- owner's trend filters (CONFIG["trend_filters"], fxbot.strongtrend)
    def daily_trend(self, inst: str) -> int:
        """The pair's LONG-trend filter (+1 / -1 / 0) from completed daily candles, once per trading day."""
        key = ("trend", inst, trading_day(self.clock()))
        if key not in self.daily_cache:
            from .strongtrend import votes
            d = mid(self.bars(inst, "D", 320))
            self.daily_cache[key] = int(votes(d)["long_state"].iloc[-1]) if len(d) >= 210 else 0
        return self.daily_cache[key]

    def trend_check(self, inst: str, side: int, m: pd.DataFrame) -> tuple[str, str]:
        """(mode, block): mode 'swing' (with the long trend) or 'day' (hard move today: closed the same day); block = why not, '' = go."""
        cfg = self.cfg.get("trend_filters")
        if not cfg:
            return "", ""
        try:
            if cfg.get("long") and self.daily_trend(inst) == side:
                return "swing", ""
            if cfg.get("short"):
                from .strongtrend import daily_of, today_state, windows
                td = today_state(m, daily_of(m), True)
                ok, _ = windows(m.index[-1:], True)
                t = pd.Timestamp(self.clock()).tz_convert(NY)
                if td[-1] == side and bool(ok[0]) and not (15 <= t.hour < 17):
                    return "day", ""
        except Exception as exc:                          # a data problem never opens a trade the filter could not check
            self.journal("error", where="trend_check", instrument=inst, error=str(exc)[:300])
            return "", "trend filter: no data"
        return "", "trend filter: no long trend or hard move today that way"

    def mark_mode(self, inst: str, mode: str):
        for g in self.groups.values():
            if g["instrument"] == inst:
                g["trend"] = mode
        self.save()

    def reversal_exit(self, name: str, inst: str, m: pd.DataFrame):
        """Close our open trade of this strategy / pair once the 1-hour trend has turned against it (at this completed bar)."""
        cfg = self.cfg.get("trend_filters")
        if not cfg or not cfg.get("reversal"):
            return
        mine = [gid for gid, g in self.groups.items() if g["strategy"] == name and g["instrument"] == inst]
        if not mine:
            return
        from .strongtrend import reversal
        rv = int(reversal(m)[-1])
        for gid in mine:
            if gid in self.groups and rv == -self.groups[gid]["side"]:
                self.journal("trend_reversal", group=gid, side=self.groups[gid]["side"], bar=m.index[-1].isoformat())
                self.close_group(gid, "trend reversed")

    def day_close(self, now):
        """Day trades (opened on today's hard move) are closed at day_close New York time, before the rollover."""
        cfg = self.cfg.get("trend_filters")
        if not cfg or not cfg.get("short"):
            return
        t = pd.Timestamp(now).tz_convert(NY)
        hh, mm = (int(x) for x in cfg.get("day_close", "16:00").split(":"))
        if not (hh * 60 + mm <= t.hour * 60 + t.minute < 17 * 60):
            return
        for gid, g in list(self.groups.items()):
            if g.get("trend") == "day":
                self.close_group(gid, "day trade: closed before the rollover")

    def news_pause(self, now) -> dict:
        """{pair: reason} for the pairs inside a news window now (CONFIG["news_pause"]); {} when off or none."""
        cfg = self.cfg.get("news_pause")
        if not cfg:
            return {}
        from .newsfilter import live_events, paused
        ts = pd.Timestamp(now).tz_convert("UTC")
        if getattr(self, "news_fetch", None) is None:
            import requests

            def _get(url):
                r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                return r.json()
            self.news_fetch = _get
        events, src = live_events(self.dir / "news_calendar.json", ts, self.news_fetch)
        out = {i: paused(i, ts, events, cfg["before_min"], cfg["after_min"]) for i in PAIRS}
        out = {i: r for i, r in out.items() if r}
        if out and out != getattr(self, "_news_last", None):
            self.journal("news_pause", pairs=sorted(out), why=sorted(set(out.values())), source=src)
        self._news_last = out
        return out

    def guard_foreign(self, trades: list):
        """Rails for trades that are not ours (the owner's fx-experts bot): a stop at the broker if there is none, a market close at -cut_pct of NAV."""
        cfg = self.cfg.get("foreign_guard")
        others = [t for t in trades if not self.is_ours(t)]
        if not cfg or not others:
            return
        try:
            nav = float(self.api.summary()["NAV"])
        except (OandaError, KeyError, ValueError) as exc:
            self.journal("error", where="foreign_guard", error=str(exc)[:300])
            return
        for t in others:
            try:
                units, upl = float(t.get("currentUnits", 0) or 0), float(t.get("unrealizedPL", 0) or 0)
                if not units:
                    continue
                if upl <= -cfg["cut_pct"] * nav:
                    got = self.api.close(t["id"])
                    self.journal("foreign_cut", trade=t["id"], instrument=t["instrument"], units=units, upl=round(upl, 2), pl=got["pl"])
                elif not t.get("stopLossOrder"):
                    px = float(t["price"])
                    sl = px - (1 if units > 0 else -1) * cfg["stop_pct"] * nav / abs(units)
                    self.api.set_stop(t["id"], fmt(t["instrument"], sl))
                    self.journal("foreign_stop", trade=t["id"], instrument=t["instrument"], units=units, stop=fmt(t["instrument"], sl))
                elif cfg.get("lock_start_pct") and upl > 0:
                    self._lock_foreign(t, units, upl, nav, cfg)
            except (OandaError, KeyError, ValueError) as exc:
                self.journal("error", where="foreign_guard", trade=t.get("id"), error=str(exc)[:300])
        open_ids = {t["id"] for t in others}
        self.foreign_best = {k: v for k, v in self.foreign_best.items() if k in open_ids}

    def _lock_foreign(self, t: dict, units: float, upl: float, nav: float, cfg: dict):
        """Move an fx-experts trade's broker stop to keep lock_keep of its best unrealized gain (see CONFIG foreign_guard)."""
        tid, side = t["id"], (1 if units > 0 else -1)
        best = max(float(self.foreign_best.get(tid, 0.0)), upl)
        self.foreign_best[tid] = round(best, 2)
        if best < cfg["lock_start_pct"] * nav:
            return
        bid, ask = self.api.price(t["instrument"])
        cur, entry = (bid if side == 1 else ask), float(t["price"])
        if side * (cur - entry) <= 0:
            return
        usd_per_px = upl / (cur - entry)                         # USD per 1.0 of price for this position, from its own current P&L
        lock = float(fmt(t["instrument"], entry + cfg["lock_keep"] * best / usd_per_px))     # compare at the broker's precision
        old = float((t.get("stopLossOrder") or {}).get("price") or 0.0) or None
        if side * (cur - lock) <= 0 or (old is not None and side * (lock - old) <= 0):
            return                                                # never at or through the market, never loosened
        self.api.set_stop(tid, fmt(t["instrument"], lock))
        self.journal("foreign_lock", trade=tid, instrument=t["instrument"], best_upl=round(best, 2), stop=fmt(t["instrument"], lock),
                     old_stop=old, keeps=round(cfg["lock_keep"] * best, 2))

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
        if spec.get("playbook"):
            return self.scan_playbook(name, spec, inst, bars, m, last)
        strat = spec["make"]()
        f = strat.frame(m)
        # exits on the bar close for our open group of this strategy/pair: the strategy's own exit and its time stop
        for gid, g in list(self.groups.items()):
            if g["strategy"] == name and g["instrument"] == inst:
                held = int((m.index > pd.Timestamp(g["opened"])).sum())
                ex = bool(f["exit_long"].iloc[-1] if g["side"] == 1 else f["exit_short"].iloc[-1])
                if ex or held >= strat.max_bars:
                    self.close_group(gid, "signal" if ex else "time")
        self.reversal_exit(name, inst, m)
        row = f.iloc[-1]
        side = 1 if row["long"] else -1 if row["short"] else 0
        if not side:
            return
        sd = float(row["stop"])
        if name not in self.cfg.get("trade", list(STRATS)):
            self.journal("signal", strategy=name, instrument=inst, side="long" if side == 1 else "short", bar=last,
                         close=float(m["close"].iloc[-1]), stop_dist=sd, result="paused (signal logged)")
            return
        mode, block = self.trend_check(inst, side, m)
        if block:
            self.journal("signal", strategy=name, instrument=inst, side="long" if side == 1 else "short", bar=last, result=block)
            return
        res = self.try_open(name, inst, side, sd, float(row.get("target", float("nan"))), m, eq, why, trades, last,
                            zone_px=float(row.get("zone_px", float("nan"))), zone2_px=float(row.get("zone2_px", float("nan"))))
        if res == "opened":
            self.mark_mode(inst, mode)
        self.journal("signal", strategy=name, instrument=inst, side="long" if side == 1 else "short", bar=last, result=res, trend=mode)

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

    # ---------------- owner's Break & Retest Playbook (fxbot.playbook): virtual stop orders, its own exits
    def scan_playbook(self, name: str, spec: dict, inst: str, bars: pd.DataFrame, m: pd.DataFrame, last: str):
        self.reversal_exit(name, inst, m)
        close = float(m["close"].iloc[-1])
        low, high = float(m["low"].iloc[-1]), float(m["high"].iloc[-1])
        for gid, g in list(self.groups.items()):            # an open trade: out at market on a completed close back into the zone
            if g["strategy"] == name and g["instrument"] == inst and g.get("zu") is not None and g["side"] * (close - g["zu"]) <= 0:
                self.journal("playbook_invalidated", group=gid, close=close, zone_edge=g["zu"])
                self.close_group(gid, "invalidated")
        key = f"{name}:{inst}"
        p = self.pending.get(key)
        if p:                                                # a waiting order: expiry, close back into the zone, beyond the retest extreme
            p["bars"] = p.get("bars", 0) + 1
            sg = p["side"]
            why = ("expired" if p["bars"] > spec["playbook"].expiry else "closed back into the zone" if sg * (close - p["zu"]) <= 0 else
                   "traded beyond the retest extreme" if (low < p["rlow"] if sg == 1 else high > p["rlow"]) else "")
            if why:
                self.journal("playbook_cancel", strategy=name, instrument=inst, reason=why)
                self.pending.pop(key, None)
        st = live_setup(bars, spec["playbook"])
        if not st:
            self.save()
            return
        side_txt = "long" if st["side"] == 1 else "short"
        if name not in self.cfg.get("trade", list(STRATS)):
            self.journal("signal", strategy=name, instrument=inst, side=side_txt, bar=last, result="paused (signal logged)", **st)
            return
        try:
            bid, ask = self.api.price(inst)
        except OandaError as exc:
            self.journal("error", where="price", instrument=inst, error=str(exc)[:300])
            return
        now_px = ask if st["side"] == 1 else bid
        if st["side"] * (now_px - st["trig"]) >= 0:          # chase rule: price is already through the trigger
            self.journal("signal", strategy=name, instrument=inst, side=side_txt, bar=last, result="chase: already through the trigger", **st)
            return
        mode, block = self.trend_check(inst, st["side"], m)
        if block:
            self.journal("signal", strategy=name, instrument=inst, side=side_txt, bar=last, result=block, **st)
            self.save()
            return
        self.pending[key] = {**st, "strategy": name, "instrument": inst, "bar": last, "bars": 0, "trend": mode}
        self.journal("signal", strategy=name, instrument=inst, side=side_txt, bar=last, result="stop order waiting", trend=mode, **st)
        self.save()

    def check_pending(self, eq: float, why: str, trades: list):
        for key, p in list(self.pending.items()):
            inst, sg = p["instrument"], p["side"]
            if (getattr(self, "_news_last", None) or {}).get(inst):
                continue                                     # news pause on this pair
            try:
                bid, ask = self.api.price(inst)
            except OandaError as exc:
                self.journal("error", where="price", instrument=inst, error=str(exc)[:300])
                continue
            px = ask if sg == 1 else bid
            if sg * (px - p["trig"]) < 0:
                continue                                     # not triggered yet
            self.pending.pop(key, None)
            res = self.open_playbook(p, px, bid, ask, eq, why, trades)
            if res == "opened":
                self.mark_mode(inst, p.get("trend", ""))
            self.journal("playbook_trigger", strategy=p["strategy"], instrument=inst, side="long" if sg == 1 else "short", px=px, result=res)
        self.save()

    def open_playbook(self, p: dict, px: float, bid: float, ask: float, eq: float, why: str, trades: list) -> str:
        name, inst, sg = p["strategy"], p["instrument"], p["side"]
        if why:
            return why
        if any(g["instrument"] == inst for g in self.groups.values()):
            return "already in this pair"
        if any(t["instrument"] == inst and not self.is_ours(t) for t in trades):
            return "another bot holds this pair"
        if len(self.groups) >= self.cfg["max_groups"]:
            return "max open trades"
        sd = sg * (px - p["stop"])
        if sd <= 0 or sg * (p["tgt"] - px) <= 0:
            return "past the stop or the target"
        if ask - bid > self.cfg["max_spread_frac"] * sd:
            return f"spread {ask - bid:.5f} too wide"
        risk_pct = float(self.cfg.get("risk_by_strategy", {}).get(name, 0.005))
        units = self.size(inst, eq, risk_pct, 0.0, px, p["stop"])
        if units < 1:
            return "size 0"
        gid = f"{name}-{inst}-{self.clock():%Y%m%dT%H%M}"
        got = self.api.market(inst, sg * units, fmt(inst, p["stop"]), fmt(inst, p["tgt"]), f"mx-{gid}-0"[:60])
        if "rejected" in got:
            return f"rejected: {got['rejected']}"
        fill = got["price"]
        from alpacabot.indicators import atr as atr_fn
        try:
            a = float(atr_fn(mid(self.bars(inst, "H1", 60)), 14).iloc[-1])
        except Exception:
            a = sd
        self.groups[gid] = {"id": gid, "strategy": name, "instrument": inst, "side": sg, "opened": self.clock().isoformat(timespec="seconds"),
                            "signal_close": px, "atr": a, "stop_px": p["stop"], "stop0": p["stop"], "fill": fill, "mid0": (bid + ask) / 2,
                            "tp_steps": 0, "zone_tp": p["tgt"], "tp_px": p["tgt"], "tier": 0, "avg": fill, "trades": [got["trade_id"]],
                            "units": {got["trade_id"]: abs(got["units"])}, "peak_units": abs(got["units"]), "sold": 0, "be": False, "bias": 0,
                            "own_exits": True, "zu": p["zu"]}
        self.journal("open", group=gid, side=sg, units=got["units"], fill=fill, stop=fmt(inst, p["stop"]), tp=fmt(inst, p["tgt"]),
                     risk_pct=risk_pct, playbook=True)
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
        if g.get("own_exits"):              # the playbook's own exits only (OANDA stop + target, close-back exit at the bar close)
            return
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
        neg_min = self.cfg.get("neg_exit_min")
        if neg_min:                              # owner 2026-10-22: a trade under water (mid vs entry mid) for neg_exit_min minutes in a row is closed
            mid_now, mid0 = (bid + ask) / 2, g.get("mid0", g.get("fill", g["avg"]) - side * (ask - bid) / 2)
            now = self.clock()
            if side * (mid_now - mid0) < 0:
                since = g.get("neg_since")
                if not since:
                    g["neg_since"] = now.isoformat()
                    self.save()
                elif (now - datetime.fromisoformat(since)).total_seconds() >= 60 * float(neg_min):
                    self.journal("neg_exit", group=gid, minutes=float(neg_min), mid=round(mid_now, 6), entry_mid=round(mid0, 6))
                    self.close_group(gid, "neg_exit")
                    return
            elif g.get("neg_since"):
                g["neg_since"] = None
                self.save()
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
