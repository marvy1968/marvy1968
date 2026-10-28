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
