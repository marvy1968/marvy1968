"""Strong-trend gate before any expert fires (owner 2026-10-28: "two of three indicators ... a strong up or down trend so that we can ride it.
I don't want a mild or moderate trend ... two of three. And then fire").

PRE-DECLARED before any run. Three trend indicators on DAILY bars, read from the last completed day (no look-ahead), each votes up / down /
nothing, and only a STRONG reading counts:
  1 ADX(14) >= 30 (above 25 is a trend, 30+ a strong one), direction from +DI vs -DI
  2 EMA stack: close > EMA20 > EMA50 > EMA200 and EMA50 higher than 10 days ago (down: the mirror)
  3 Aroon(25): Aroon-up >= 70 and Aroon-down <= 30 (down: the mirror)
STRONG UP = 2 or 3 of them vote up; STRONG DOWN = 2 or 3 vote down; anything else = no trade. An expert's long fires only in a strong up
trend, its short only in a strong down trend.
Test: every OANDA expert (live exits: 0.5R cut / 1.25R take-profit; retest_h1 with its own exits) on the 7 majors and the stock swing
strategies (long only) on their universe, gated vs not, last 14 days (the owner's 2-week test) at 1x and 2x costs; the last 92 days are
shown as context. Control: the same gate with the daily trend states shuffled in 20-day blocks (same share of strong days), 20 seeds.
OWNER REFINEMENT (same day, before any run): "if it's a short trend, make it strong. Longer trends can be moderate to mild." -> the AGE-SCALED
gate: trend age = days in a row the EMA50 has been above (below) the EMA200. Under 60 days (short): the strict votes above. 60+ days (long):
mild votes, in the long trend's direction only: ADX >= 20 with +DI/-DI agreeing, close > EMA50 > EMA200 (no EMA20, no slope), Aroon-up >= 50
and above Aroon-down (down: mirror). Still 2 of 3. Both gates are reported; the age-scaled one is the owner's rule.
OWNER, SAME DAY ("give me two trend filters ... a long trend filter for each stock so we can swing trade it ... if the dip is great enough
the short trend filter will pick it up ... trending hard today ... ride it today only"):
  LONG filter  (swing): the long-trend branch above alone (EMA50 on one side of EMA200 for 60+ days and 2 of 3 mild readings that way).
  SHORT filter (today only): on the intraday bars (forex 1-hour / 4-hour, stocks 5-minute), 2 of the 3 STRICT indicators computed on those
               bars agree AND price has moved 0.75+ of the daily ATR14 (completed days) from today's open the same way. A trade it lets
               through is closed the same day (forex: at 16:00 New York, before the rollover; stocks: 15:55); entries until 15:00. It fires
               only where the LONG filter does not already allow that side (so it adds the hard counter-trend / young-trend days).
  BOTH: the two together. Plus a plain RIDE with no expert: enter the next bar after the SHORT filter first turns on that day, stop 0.5
  daily ATR, out at the day's close (one ride per instrument per day). Control for these: the same filters with the trading days shuffled
  (each day's readings moved to another day) and the long filter shuffled in 20-day blocks, 20 seeds. News is not in the rule (the news
  test had too few trades to say anything); today's Fed decision will show up as a hard-move day if it moves the market.
A 2-week PASS: gated net R > 0 at 2x costs, better than ungated, beats 18 of 20 shuffled gates, 5+ trades. A pass goes to the 3-month test
next; nothing goes live on two weeks alone."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

ADX_MIN, AROON_HI, AROON_LO, NEED = 30.0, 70.0, 30.0, 2
LONG_AGE, MILD_ADX, MILD_AROON = 60, 20.0, 50.0


def _wilder(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def votes(d: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLC -> columns adx, ema, aroon (+1 strong up, -1 strong down, 0) and state (2 of 3), as of each day's close."""
    h, l, c = (d[k].astype(float) for k in ("high", "low", "close"))
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=d.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=d.index)
    atr = _wilder(tr, 14)
    pdi, mdi = 100 * _wilder(pdm, 14) / atr, 100 * _wilder(mdm, 14) / atr
    adx = _wilder(100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan), 14)
    v_adx = np.where(adx >= ADX_MIN, np.sign(pdi - mdi), 0)
    e20, e50, e200 = (c.ewm(span=n, adjust=False, min_periods=n).mean() for n in (20, 50, 200))
    v_ema = np.where((c > e20) & (e20 > e50) & (e50 > e200) & (e50 > e50.shift(10)), 1,
                     np.where((c < e20) & (e20 < e50) & (e50 < e200) & (e50 < e50.shift(10)), -1, 0))
    n = 25
    hi_age = h.rolling(n + 1).apply(lambda x: n - int(np.argmax(x)), raw=True)
    lo_age = l.rolling(n + 1).apply(lambda x: n - int(np.argmin(x)), raw=True)
    au, ad = 100 * (n - hi_age) / n, 100 * (n - lo_age) / n
    v_ar = np.where((au >= AROON_HI) & (ad <= AROON_LO), 1, np.where((ad >= AROON_HI) & (au <= AROON_LO), -1, 0))
    out = pd.DataFrame({"adx": v_adx, "ema": v_ema, "aroon": v_ar}, index=d.index).fillna(0).astype(int)
    ups, dns = (out == 1).sum(axis=1), (out == -1).sum(axis=1)
    out["state"] = np.where(ups >= NEED, 1, np.where(dns >= NEED, -1, 0))
    # age-scaled (owner): a long-running trend only needs mild readings in its own direction
    above = (e50 > e200) & e200.notna()
    below = (e50 < e200) & e200.notna()
    run_up = above.groupby((~above).cumsum()).cumsum()
    run_dn = below.groupby((~below).cumsum()).cumsum()
    out["age"] = np.where(above, run_up, np.where(below, -run_dn, 0)).astype(int)
    m_adx = np.where(adx >= MILD_ADX, np.sign(pdi - mdi), 0)
    m_ema = np.where((c > e50) & above, 1, np.where((c < e50) & below, -1, 0))
    m_ar = np.where((au >= MILD_AROON) & (au > ad), 1, np.where((ad >= MILD_AROON) & (ad > au), -1, 0))
    mild = pd.DataFrame({"a": m_adx, "e": m_ema, "r": m_ar}, index=d.index).fillna(0).astype(int)
    lt = np.sign(out["age"]) * (out["age"].abs() >= LONG_AGE)
    agree = ((mild.to_numpy() == lt.to_numpy()[:, None]) & (lt.to_numpy()[:, None] != 0)).sum(axis=1)
    out["state_age"] = np.where(lt != 0, np.where(agree >= NEED, lt, 0), out["state"])
    out["long_state"] = np.where(lt != 0, np.where(agree >= NEED, lt, 0), 0)          # the LONG-trend (swing) filter alone
    return out


def shuffled(state: pd.Series, seed: int, block: int = 20) -> pd.Series:
    """The control: the same states in shuffled 20-day blocks (keeps how often and how long strong trends last, breaks their timing)."""
    v = state.to_numpy()
    blocks = [v[i:i + block] for i in range(0, len(v), block)]
    order = np.random.default_rng(seed).permutation(len(blocks))
    return pd.Series(np.concatenate([blocks[k] for k in order]), index=state.index)


def daily_of(m: pd.DataFrame) -> pd.DataFrame:
    """Daily bars from a mid frame (forex day = 17:00 New York); a daily frame is returned as it is."""
    if len(m) > 2 and (m.index[1:] - m.index[:-1]).median() >= pd.Timedelta(hours=20):
        return m
    from .consensus import daily_from_h1
    d = daily_from_h1(m)
    d.index = pd.DatetimeIndex(pd.to_datetime(d.index))
    return d


def state_on_bars(m: pd.DataFrame, state: pd.Series) -> np.ndarray:
    """The last COMPLETED day's state on every bar of m (forex day key = New York time + 7 h, as consensus.daily_from_h1)."""
    if len(m) > 2 and (m.index[1:] - m.index[:-1]).median() >= pd.Timedelta(hours=20):
        return state.shift(1).fillna(0).to_numpy(int)
    key = pd.to_datetime((m.index.tz_convert("America/New_York") + pd.Timedelta(hours=7)).date)
    prev = state.shift(1)
    return prev.reindex(key).fillna(0).to_numpy(int)


class StrongTrendFx:
    """Any forex expert behind the gate: longs only on strong-up days, shorts only on strong-down days. state_fn(daily) -> state Series."""

    def __init__(self, base, state_fn=None):
        self.base, self.state_fn = base, state_fn or (lambda d: votes(d)["state"])
        self.max_bars = base.max_bars
        self.name = getattr(base, "name", type(base).__name__) + " +strong2of3"

    def frame(self, m):
        f = self.base.frame(m).copy()
        st = state_on_bars(m, self.state_fn(daily_of(m)))
        f["long"] = f["long"].fillna(False).astype(bool) & (st == 1)
        f["short"] = f["short"].fillna(False).astype(bool) & (st == -1)
        return f


def summary(rs: list[float]) -> dict:
    rs = np.asarray(rs, float)
    if not len(rs):
        return {"trades": 0, "net": 0.0, "pf": 0.0, "win": 0.0}
    w, lo = rs[rs > 0].sum(), -rs[rs <= 0].sum()
    return {"trades": len(rs), "net": float(rs.sum()), "pf": float(w / lo) if lo > 0 else 99.0, "win": float((rs > 0).mean())}


def fmt(s: dict) -> str:
    return f"{s['trades']} t, win {s['win']:.0%}, PF {s['pf']:.2f}, {s['net']:+.1f}R"


def verdict(gated2: dict, plain2: dict, beat: int, seeds: int) -> str:
    ok = gated2["trades"] >= 5 and gated2["net"] > 0 and gated2["net"] > plain2["net"] and beat >= round(0.9 * seeds)
    return "PASS" if ok else "fail"


# ---- forex ------------------------------------------------------------------------------------------------------------------------------

def fx_trades(name: str, bars: dict, a: date, b: date, spread: float = 1.0, state_fn=False) -> list[float]:
    """R of every trade of one live OANDA expert on the 7 majors (bars = {inst: bid/ask on its granularity}).
    state_fn: False = ungated, None = the real gate, callable = a control gate."""
    from .engine import FIN_PER_NIGHT, simulate
    from .exitlab import _spread
    from .live import STRATS
    from .strategies import TightStop
    rs = []
    for inst, df in bars.items():
        dfx = _spread(df, spread) if spread != 1.0 else df
        if name == "retest_h1":
            from .data import mid
            from .playbook import PlaybookRules, simulate_playbook
            st = None if state_fn is False else state_on_bars(mid(df), (state_fn or (lambda d: votes(d)["state"]))(daily_of(mid(df))))
            pos = {t: i for i, t in enumerate(df.index)}
            for t in simulate_playbook(inst, dfx, PlaybookRules(be_at_r=0.0), a, b):
                side = 1 if t["side"] == "long" else -1
                if st is not None:
                    k = pos.get(t["entry"])
                    if k is None or st[k] != side:
                        continue
                rs.append(t["r"])
            continue
        base = STRATS[name]["make"]()
        strat = TightStop(base if state_fn is False else StrongTrendFx(base, state_fn), 0.50, 1.25)
        for t in simulate(inst, dfx, strat):
            if a <= t.entry_time.date() <= b:
                rs.append(t.side * (t.exit_px - t.entry_px) / t.stop_dist - t.nights * FIN_PER_NIGHT * t.entry_px / t.stop_dist)
    return rs


def fx_report(by_gran: dict, today: date, seeds: int = 20) -> list[str]:
    """by_gran = {"H1"|"H4"|"D": {inst: bid/ask}} with 300+ days of history before the test."""
    from .data import mid
    from .live import STRATS
    a14, a92 = today - timedelta(days=14), today - timedelta(days=92)
    out = [f"## strong_trend (forex): 2 of 3 strong-trend indicators (ADX>={ADX_MIN:g}, EMA 20/50/200 stack, Aroon {AROON_HI:g}/{AROON_LO:g}) "
           f"before every OANDA expert; 2-week test {a14}..{today} (context: 92 days)"]
    h1 = by_gran["H1"]
    now = []
    for inst, df in h1.items():
        v = votes(daily_of(mid(df))).iloc[-2]
        w = lambda x: "UP" if x == 1 else "DOWN" if x == -1 else "none"   # noqa: E731
        now.append(f"{inst} strict {w(v['state'])} / age-scaled {w(v['state_age'])} (trend age {v['age']:+d} d; adx {v['adx']:+d} ema {v['ema']:+d} aroon {v['aroon']:+d})")
    out.append("- trend today (last completed day): " + " | ".join(now))
    for col in ("state", "state_age"):
        share = np.mean([(votes(daily_of(mid(df)))[col].iloc[-260:] != 0).mean() for df in h1.values()])
        out.append(f"- share of days the {col} gate is open (last 12 months, 7 majors): {share:.0%}")
    lab = {"state": "STRICT", "state_age": "AGE-SCALED (owner)"}
    for name, cfg in STRATS.items():
        bars = by_gran[cfg["gran"]]
        live = " (LIVE)" if name in ("lien_h1", "retest_h1") else ""
        try:
            p1, p2 = fx_trades(name, bars, a14, today), fx_trades(name, bars, a14, today, 2.0)
            q_p = fx_trades(name, bars, a92, today, 2.0)
            out.append(f"- {name}{live}: 2 weeks plain {fmt(summary(p1))} / 2x {fmt(summary(p2))} | 92 days 2x plain {fmt(summary(q_p))}")
            for col in ("state", "state_age"):
                fn = lambda d, c_=col: votes(d)[c_]   # noqa: E731
                g1, g2 = fx_trades(name, bars, a14, today, 1.0, fn), fx_trades(name, bars, a14, today, 2.0, fn)
                ctrl = [summary(fx_trades(name, bars, a14, today, 2.0, lambda d, s=s, c_=col: shuffled(votes(d)[c_], s)))["net"] for s in range(seeds)]
                q_g = fx_trades(name, bars, a92, today, 2.0, fn)
                sg2 = summary(g2)
                beat = sum(sg2["net"] > x for x in ctrl)
                out.append(f"    {lab[col]}: 2 weeks {fmt(summary(g1))} / 2x {fmt(sg2)} | shuffled gates median {np.median(ctrl):+.1f}R, beaten {beat}/{seeds} | "
                           f"92 days 2x {fmt(summary(q_g))} | {verdict(sg2, summary(p2), beat, seeds)}")
        except Exception as exc:
            out.append(f"- {name}: error {type(exc).__name__}: {str(exc)[:120]}")
    return out


# ---- stocks -----------------------------------------------------------------------------------------------------------------------------

def stock_report(bars: dict, spy_close, today: date, seeds: int = 20) -> list[str]:
    """The swing strategies (long only) with and without the gate: an entry needs a STRONG UP stock (2 of 3) on the signal day."""
    from alpacabot import exitlab as xl
    a14, a92 = today - timedelta(days=14), today - timedelta(days=92)
    out = [f"## strong_trend (stocks): swing strategies, long entries only in a strong up trend (2 of 3), own exits; 2-week test {a14}..{today}"]
    vt = {s: votes(d) for s, d in bars.items() if len(d) > 220}
    for col, lab in (("state", "strict"), ("state_age", "age-scaled")):
        up = sorted(s for s, v in vt.items() if v[col].iloc[-1] == 1)
        dn = sorted(s for s, v in vt.items() if v[col].iloc[-1] == -1)
        out.append(f"- {lab} UP today ({len(up)}): {' '.join(up) or '-'} | DOWN ({len(dn)}): {' '.join(dn) or '-'}")

    def trades(name, a, slip, gate):
        rs = []
        for sym, d in bars.items():
            if len(d) < 220 or (name == "index200") != (sym in ("SPY", "QQQ")):
                continue
            sig = xl.signals(name, d, spy_close)
            mask = sig["entry"].to_numpy(bool)
            if gate is not None:
                mask = mask & (gate(sym).to_numpy() == 1)
            rs += [t["r"] for t in xl.simulate(d, sig, "own", a, today, slip, mask)]
        return rs
    for name in xl.STRATEGIES:
        p1, p2 = trades(name, a14, 5.0, None), trades(name, a14, 10.0, None)
        q_p = trades(name, a92, 10.0, None)
        out.append(f"- {name}: 2 weeks plain {fmt(summary(p1))} / 10 bps {fmt(summary(p2))} | 92 days 10 bps plain {fmt(summary(q_p))}")
        for col, lab in (("state", "STRICT"), ("state_age", "AGE-SCALED (owner)")):
            g = lambda s_, c_=col: vt[s_][c_]   # noqa: E731
            g1, g2 = trades(name, a14, 5.0, g), trades(name, a14, 10.0, g)
            ctrl = [summary(trades(name, a14, 10.0, lambda s_, k=k, c_=col: shuffled(vt[s_][c_], k)))["net"] for k in range(seeds)]
            sg2 = summary(g2)
            beat = sum(sg2["net"] > x for x in ctrl)
            q_g = trades(name, a92, 10.0, g)
            out.append(f"    {lab}: 2 weeks {fmt(summary(g1))} / 10 bps {fmt(sg2)} | shuffled gates median {np.median(ctrl):+.1f}R, beaten {beat}/{seeds} | "
                       f"92 days 10 bps {fmt(summary(q_g))} | {verdict(sg2, summary(p2), beat, seeds)}")
    return out


# ---- the two filters: LONG (swing) and SHORT (today only) ---------------------------------------------------------------------------------

TODAY_MOVE_ATR, RIDE_STOP_ATR = 0.75, 0.5


def day_keys(idx: pd.DatetimeIndex, forex: bool) -> np.ndarray:
    ny = idx.tz_convert("America/New_York")
    return np.asarray(pd.to_datetime((ny + pd.Timedelta(hours=7)).date if forex else ny.date))


def today_state(m: pd.DataFrame, daily: pd.DataFrame, forex: bool) -> np.ndarray:
    """SHORT filter on every intraday bar (as of its close): +1 hard up today, -1 hard down today, 0 otherwise."""
    v = votes(m)["state"].to_numpy(int)
    key = day_keys(m.index, forex)
    h, l, c = (daily[k].astype(float) for k in ("high", "low", "close"))
    pc = c.shift(1)
    atr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1).rolling(14).mean().shift(1)   # completed days only
    atr.index = pd.to_datetime([pd.Timestamp(x).tz_convert("America/New_York").date() if pd.Timestamp(x).tzinfo else pd.Timestamp(x).date()
                                for x in atr.index])
    a_bar = atr.reindex(key).to_numpy(float)
    day_open = pd.Series(m["open"].to_numpy(float)).groupby(key).transform("first").to_numpy(float)
    move = m["close"].to_numpy(float) - day_open
    out = np.where((v == 1) & (move >= TODAY_MOVE_ATR * a_bar), 1, np.where((v == -1) & (-move >= TODAY_MOVE_ATR * a_bar), -1, 0))
    return np.where(np.isfinite(a_bar), out, 0).astype(int)


def permute_days(st_: np.ndarray, key: np.ndarray, seed: int) -> np.ndarray:
    """Control: every day's readings moved onto another (random) day."""
    days, inv = np.unique(key, return_inverse=True)
    groups = [np.flatnonzero(inv == i) for i in range(len(days))]
    perm = np.random.default_rng(seed).permutation(len(days))
    out = np.zeros_like(st_)
    for i, g in enumerate(groups):
        src = st_[groups[perm[i]]]
        k = min(len(g), len(src))
        out[g[:k]] = src[:k]
    return out


def windows(idx: pd.DatetimeIndex, forex: bool) -> tuple[np.ndarray, np.ndarray]:
    """(entry allowed on this bar's close, last bar of the day: exit at the next open)."""
    ny = idx.tz_convert("America/New_York")
    mins = ny.hour * 60 + ny.minute
    if forex:
        return (mins < 15 * 60) | (mins >= 17 * 60), (mins >= 15 * 60) & (mins < 16 * 60)
    return (mins >= 10 * 60) & (mins < 15 * 60), mins == 15 * 60 + 50


class FilterFx:
    """An expert behind LONG (mode='long': swing, own exits) or SHORT (mode='short': hard move today, out the same day) - forex."""

    def __init__(self, base, mode: str, long_fn=None, today_fn=None):
        self.base, self.mode = base, mode
        self.long_fn = long_fn or (lambda d: votes(d)["long_state"])
        self.today_fn = today_fn or (lambda m, d: today_state(m, d, True))
        self.max_bars = base.max_bars
        self.name = getattr(base, "name", type(base).__name__) + f" +{mode}filter"

    def frame(self, m):
        f = self.base.frame(m).copy()
        d = daily_of(m)
        lng = state_on_bars(m, self.long_fn(d))
        lg, sh = f["long"].fillna(False).astype(bool).to_numpy(), f["short"].fillna(False).astype(bool).to_numpy()
        if self.mode == "long":
            f["long"], f["short"] = lg & (lng == 1), sh & (lng == -1)
            return f
        td = self.today_fn(m, d)
        ok, last = windows(m.index, True)
        f["long"], f["short"] = lg & (td == 1) & (lng != 1) & ok, sh & (td == -1) & (lng != -1) & ok
        f["exit_long"] = f["exit_long"].fillna(False).astype(bool).to_numpy() | last
        f["exit_short"] = f["exit_short"].fillna(False).astype(bool).to_numpy() | last
        return f


def ride(df: pd.DataFrame, td: np.ndarray, atr_bar: np.ndarray, forex: bool) -> list[dict]:
    """Plain ride: first SHORT-filter bar of a day -> next open, stop RIDE_STOP_ATR x daily ATR, out at the day's last bar."""
    key = day_keys(df.index, forex)
    ok, last = windows(df.index, forex)
    bo, bh, bl, ao, ah, al = (df[k].to_numpy(float) for k in ("bo", "bh", "bl", "ao", "ah", "al"))
    out, done, n, i = [], set(), len(df), 0
    while i < n - 1:
        side = int(td[i])
        if not side or not ok[i] or key[i] in done or not np.isfinite(atr_bar[i]):
            i += 1
            continue
        done.add(key[i])
        j = i + 1
        fill = ao[j] if side == 1 else bo[j]
        sd = RIDE_STOP_ATR * atr_bar[i]
        stop = fill - side * sd
        k, px = j, None
        while k < n:
            lo, hi, op = (bl[k], bh[k], bo[k]) if side == 1 else (al[k], ah[k], ao[k])
            if key[k] != key[i]:
                px = op
            elif (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
                px = op if side * (op - stop) <= 0 and k > j else stop
            elif last[k] and k + 1 < n:
                k += 1
                px = bo[k] if side == 1 else ao[k]
            if px is not None:
                break
            k += 1
        if px is None:
            px = bo[n - 1] if side == 1 else ao[n - 1]
        out.append({"date": key[i], "side": side, "r": side * (px - fill) / sd})
        i = k
    return out


def daily_atr_on(idx: pd.DatetimeIndex, daily: pd.DataFrame, forex: bool) -> np.ndarray:
    h, l, c = (daily[k].astype(float) for k in ("high", "low", "close"))
    pc = c.shift(1)
    atr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1).rolling(14).mean().shift(1)
    atr.index = pd.to_datetime([pd.Timestamp(x).tz_convert("America/New_York").date() if pd.Timestamp(x).tzinfo else pd.Timestamp(x).date()
                                for x in atr.index])
    return atr.reindex(day_keys(idx, forex)).to_numpy(float)


def _r_of(t) -> float:
    from .engine import FIN_PER_NIGHT
    return t.side * (t.exit_px - t.entry_px) / t.stop_dist - t.nights * FIN_PER_NIGHT * t.entry_px / t.stop_dist


def fx_filter_trades(name: str, bars: dict, a: date, b: date, spread: float, mode: str, seed=None) -> list[float]:
    """mode: plain | long | short | both. seed = the shuffled control (long filter in 20-day blocks, today's readings on other days)."""
    from .data import mid
    from .engine import simulate
    from .exitlab import _spread
    from .live import STRATS
    from .strategies import TightStop
    rs = []
    for inst, df in bars.items():
        dfx = _spread(df, spread) if spread != 1.0 else df
        m = mid(df)
        lf = (lambda d: votes(d)["long_state"]) if seed is None else (lambda d: shuffled(votes(d)["long_state"], seed))
        tf = (lambda m_, d_: today_state(m_, d_, True)) if seed is None else \
            (lambda m_, d_: permute_days(today_state(m_, d_, True), day_keys(m_.index, True), seed))
        if name == "retest_h1":
            from .playbook import PlaybookRules, simulate_playbook
            d = daily_of(m)
            lng = state_on_bars(m, lf(d))
            td = tf(m, d)
            ok, last = windows(m.index, True)
            pos = {t: i for i, t in enumerate(df.index)}
            for t in simulate_playbook(inst, dfx, PlaybookRules(be_at_r=0.0), a, b):
                side, k = (1 if t["side"] == "long" else -1), pos.get(t["entry"])
                if k is None:
                    continue
                if mode == "plain" or (mode in ("long", "both") and lng[k - 1] == side):
                    rs.append(t["r"])
                elif mode in ("short", "both") and td[k - 1] == side and lng[k - 1] != side and ok[k - 1]:
                    j = pos.get(t["exit"], k)
                    ends = np.flatnonzero(last[k:j + 1])
                    if len(ends) and k + ends[0] + 1 < len(df):                   # still open at the day's last bar: out at the next open
                        e = k + ends[0] + 1
                        px = side * (dfx["bo"].iloc[e] if side == 1 else dfx["ao"].iloc[e])
                        rs.append((px - t["fill"]) / t["risk"])
                    else:
                        rs.append(t["r"])
            continue
        if STRATS[name]["gran"] == "D" and mode in ("short", "both"):
            mode_ = "long" if mode == "both" else None
            if mode_ is None:
                continue
        else:
            mode_ = mode
        base = STRATS[name]["make"]
        runs = {"plain": [TightStop(base(), 0.50, 1.25)], "long": [TightStop(FilterFx(base(), "long", lf), 0.50, 1.25)],
                "short": [TightStop(FilterFx(base(), "short", lf, tf), 0.50, 1.25)]}
        runs["both"] = runs["long"] + runs["short"]
        for strat in runs[mode_]:
            rs += [_r_of(t) for t in simulate(inst, dfx, strat) if a <= t.entry_time.date() <= b]
    return rs


def fx_ride(h1: dict, a: date, b: date, spread: float, seed=None) -> list[float]:
    from .data import mid
    from .exitlab import _spread
    rs = []
    for inst, df in h1.items():
        m = mid(df)
        d = daily_of(m)
        td = today_state(m, d, True)
        if seed is not None:
            td = permute_days(td, day_keys(m.index, True), seed)
        dfx = _spread(df, spread) if spread != 1.0 else df
        rs += [t["r"] for t in ride(dfx, td, daily_atr_on(m.index, d, True), True) if a <= pd.Timestamp(t["date"]).date() <= b]
    return rs


def fx_filters_report(by_gran: dict, today: date, seeds: int = 20) -> list[str]:
    from .data import mid
    from .live import STRATS
    a14, a92 = today - timedelta(days=14), today - timedelta(days=92)
    out = [f"## two_filters (forex): LONG filter (swing, long trends only) + SHORT filter (hard move today: 2 of 3 strict on the bars and "
           f"{TODAY_MOVE_ATR:g}+ daily ATR from the open; out by 16:00 New York); 2-week test {a14}..{today}, 2x spread for the verdict"]
    now = []
    for inst, df in by_gran["H1"].items():
        m = mid(df)
        d = daily_of(m)
        ls = int(votes(d)["long_state"].iloc[-2])
        td = today_state(m, d, True)
        now.append(f"{inst} long {'UP' if ls == 1 else 'DOWN' if ls == -1 else '-'} / today {'HARD UP' if td[-1] == 1 else 'HARD DOWN' if td[-1] == -1 else '-'}")
    out.append("- now: " + " | ".join(now))
    r2 = fx_ride(by_gran["H1"], a14, today, 2.0)
    rc = [summary(fx_ride(by_gran["H1"], a14, today, 2.0, s))["net"] for s in range(seeds)]
    sr = summary(r2)
    br = sum(sr["net"] > x for x in rc)
    ok = sr["trades"] >= 5 and sr["net"] > 0 and br >= round(0.9 * seeds)
    out.append(f"- plain RIDE (no expert, 7 majors): 2 weeks 1x {fmt(summary(fx_ride(by_gran['H1'], a14, today, 1.0)))} / 2x {fmt(sr)} | shuffled days "
               f"median {np.median(rc):+.1f}R, beaten {br}/{seeds} | 92 days 2x {fmt(summary(fx_ride(by_gran['H1'], a92, today, 2.0)))} | {'PASS' if ok else 'fail'}")
    for name, cfg in STRATS.items():
        bars = by_gran[cfg["gran"]]
        live = " (LIVE)" if name in ("lien_h1", "retest_h1") else ""
        try:
            p2 = summary(fx_filter_trades(name, bars, a14, today, 2.0, "plain"))
            row = [f"plain {fmt(p2)}"]
            for mode in ("long", "short", "both"):
                row.append(f"{mode.upper()} {fmt(summary(fx_filter_trades(name, bars, a14, today, 2.0, mode)))}")
            b2 = summary(fx_filter_trades(name, bars, a14, today, 2.0, "both"))
            ctrl = [summary(fx_filter_trades(name, bars, a14, today, 2.0, "both", s))["net"] for s in range(seeds)]
            beat = sum(b2["net"] > x for x in ctrl)
            q = summary(fx_filter_trades(name, bars, a92, today, 2.0, "both"))
            qp = summary(fx_filter_trades(name, bars, a92, today, 2.0, "plain"))
            out.append(f"- {name}{live} 2 weeks, 2x: " + " | ".join(row) + f" | BOTH vs shuffled: median {np.median(ctrl):+.1f}R, beaten {beat}/{seeds} | "
                       f"92 days 2x plain {fmt(qp)} -> BOTH {fmt(q)} | {verdict(b2, p2, beat, seeds)}")
        except Exception as exc:
            out.append(f"- {name}: error {type(exc).__name__}: {str(exc)[:120]}")
    return out


# ---- stocks: LONG filter on the swing book, SHORT filter as a same-day ride on 5-minute bars ---------------------------------------------

def stock_filters_report(daily: dict, five: dict, spy_close, today: date, seeds: int = 20) -> list[str]:
    """daily = {symbol: daily bars (2+ years)}, five = {symbol: 5-minute bars (last ~2 months)}."""
    from alpacabot import exitlab as xl
    from alpacabot.trendzones import to_bidask
    a14, a92 = today - timedelta(days=14), today - timedelta(days=92)
    out = [f"## two_filters (stocks): LONG filter on the swing strategies (long entries in long up trends) + SHORT filter ride on 5-minute bars "
           f"(hard move today, out at 15:55); 2-week test {a14}..{today}, 10 bps a side for the verdict"]
    vt = {s: votes(d) for s, d in daily.items() if len(d) > 220}
    up = sorted(s for s, v in vt.items() if v["long_state"].iloc[-1] == 1)
    dn = sorted(s for s, v in vt.items() if v["long_state"].iloc[-1] == -1)
    out.append(f"- LONG filter now: up ({len(up)}) {' '.join(up) or '-'} | down ({len(dn)}) {' '.join(dn) or '-'}")

    def swing(name, a, slip, gate):
        rs = []
        for sym, d in daily.items():
            if len(d) < 220 or (name == "index200") != (sym in ("SPY", "QQQ")):
                continue
            sig = xl.signals(name, d, spy_close)
            mask = sig["entry"].to_numpy(bool)
            if gate is not None:
                mask = mask & (gate(sym).to_numpy() == 1)
            rs += [t["r"] for t in xl.simulate(d, sig, "own", a, today, slip, mask)]
        return rs
    for name in xl.STRATEGIES:
        p2 = summary(swing(name, a14, 10.0, None))
        g = lambda s_: vt[s_]["long_state"]   # noqa: E731
        g2 = summary(swing(name, a14, 10.0, g))
        ctrl = [summary(swing(name, a14, 10.0, lambda s_, k=k: shuffled(vt[s_]["long_state"], k)))["net"] for k in range(seeds)]
        beat = sum(g2["net"] > x for x in ctrl)
        out.append(f"- swing {name}: 2 weeks plain {fmt(p2)} -> LONG filter {fmt(g2)} | shuffled median {np.median(ctrl):+.1f}R, beaten {beat}/{seeds} | "
                   f"92 days plain {fmt(summary(swing(name, a92, 10.0, None)))} -> LONG filter {fmt(summary(swing(name, a92, 10.0, g)))} | "
                   f"{verdict(g2, p2, beat, seeds)}")
    pre = {}
    for sym, m5 in five.items():
        if sym not in daily or len(m5) < 300:
            continue
        m5 = m5.astype(float)
        pre[sym] = (m5, today_state(m5, daily[sym], False), daily_atr_on(m5.index, daily[sym], False), day_keys(m5.index, False))

    def rides(a, half_bps, seed=None):
        rs, n_up, n_dn = [], 0, 0
        for sym, (m5, td, at, key) in pre.items():
            t_ = td if seed is None else permute_days(td, key, seed)
            for t in ride(to_bidask(m5, half_bps), t_, at, False):
                if a <= pd.Timestamp(t["date"]).date() <= today:
                    rs.append(t["r"])
                    n_up += t["side"] == 1
                    n_dn += t["side"] == -1
        return rs, n_up, n_dn
    r1, u, dn_ = rides(a14, 5.0)
    r2, _, _ = rides(a14, 10.0)
    rc = [summary(rides(a14, 10.0, s)[0])["net"] for s in range(seeds)]
    s2 = summary(r2)
    br = sum(s2["net"] > x for x in rc)
    ok = s2["trades"] >= 5 and s2["net"] > 0 and br >= round(0.9 * seeds)
    hard = sorted(s for s, (m5, td, at, key) in pre.items() if td[-1] != 0)
    out.append(f"- SHORT filter ride ({len(pre)} stocks): 2 weeks 5 bps {fmt(summary(r1))} ({u} up, {dn_} down) / 10 bps {fmt(s2)} | shuffled days median "
               f"{np.median(rc):+.1f}R, beaten {br}/{seeds} | {'PASS' if ok else 'fail'} | hard-move on the last bar: {' '.join(hard) or '-'}")
    return out
