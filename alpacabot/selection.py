"""The owner's daily stock funnel for trend_stairs (drawings 2026-10-17: "Trend Stock" for longs, "Short Trend" for shorts).

For each session, from COMPLETED daily bars only (the day before):
  LONG  list: Weinstein stage 2 -> the sector's news is not negative -> one of the 3 STRONGEST stocks in its sector (33 in all) -> trend
              strength positive on at least `need` of 3 trend indicators (ADX >= 25 with +DI above -DI, a rising 150-day average,
              stronger than SPY over 3 months).
  SHORT list: Weinstein stage 4 -> the sector's news IS negative -> one of the 3 WEAKEST stocks in its sector (33) -> trend strength
              negative on at least `need` of 3 (ADX >= 25 with -DI on top, a falling 150-day average, weaker than SPY).
trend_stairs then only buys stocks on the long list and only shorts stocks on the short list (paper.on_bar_close / research).
Sector news comes from alpacabot.news (headline word score over 5 sessions); without a news feed the sector trend stands in.
"""

import numpy as np
import pandas as pd

from .indicators import atr


def strength(d: pd.DataFrame, spy: pd.DataFrame, adx_min: float = 25.0, rs_days: int = 63) -> pd.DataFrame:
    """Per day: up / down votes (0-3) of the three trend-strength indicators, the stage (1-4) and rs (3-month return minus SPY's)."""
    h, l, c = (d[k].astype(float) for k in ("high", "low", "close"))
    up_, dn_ = h.diff(), -l.diff()
    pdm = ((up_ > dn_) & (up_ > 0)) * up_
    mdm = ((dn_ > up_) & (dn_ > 0)) * dn_
    a = atr(d, 14)
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / a
    mdi = 100 * mdm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx_ = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    m150 = c.rolling(150, min_periods=150).mean()
    rising, falling = m150 > m150.shift(20), m150 < m150.shift(20)
    sc = spy["close"].astype(float)
    by_day = dict(zip(sc.index.date, sc.to_numpy()))
    spy_al = pd.Series([by_day.get(x, np.nan) for x in d.index.date], index=d.index).ffill()
    rs = c.pct_change(rs_days) - spy_al.pct_change(rs_days)
    stage = pd.Series(np.where(m150.isna(), 0, np.where(c > m150, np.where(rising, 2, 3), np.where(rising, 1, 4))), index=d.index)
    up_votes = ((adx_ >= adx_min) & (pdi > mdi)).astype(int) + rising.astype(int) + (rs > 0).astype(int)
    dn_votes = ((adx_ >= adx_min) & (mdi > pdi)).astype(int) + falling.astype(int) + (rs < 0).astype(int)
    return pd.DataFrame({"up": up_votes, "down": dn_votes, "stage": stage, "rs": rs}, index=d.index)


def _by_date(s: pd.Series) -> pd.Series:
    return pd.Series(s.to_numpy(), index=pd.DatetimeIndex(pd.to_datetime([x for x in s.index.date])))


def lists(daily: dict, spy: pd.DataFrame, sector_of: dict, sector_neg: dict | None, top: int = 3, need: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(long_ok, short_ok): DataFrames (rows = dates, columns = symbols) of booleans, as of each day's CLOSE (use the previous row for a
    session). sector_neg = {sector ETF: bool Series by date} (negative news); None = no news filter. Longs need the sector news NOT
    negative (a word-score sum rarely reads clearly positive), shorts need it negative."""
    st = {s: strength(d, spy) for s, d in daily.items() if s in sector_of and len(d) > 170}
    if not st:
        return pd.DataFrame(), pd.DataFrame()
    rs = pd.DataFrame({s: _by_date(v["rs"]) for s, v in st.items()})
    up = pd.DataFrame({s: _by_date(v["up"]) for s, v in st.items()}).reindex(rs.index)
    dn = pd.DataFrame({s: _by_date(v["down"]) for s, v in st.items()}).reindex(rs.index)
    stg = pd.DataFrame({s: _by_date(v["stage"]) for s, v in st.items()}).reindex(rs.index)
    strong_rank = pd.DataFrame(index=rs.index, columns=rs.columns, dtype=float)
    weak_rank = pd.DataFrame(index=rs.index, columns=rs.columns, dtype=float)
    for etf in set(sector_of.values()):
        cols = [s for s in rs.columns if sector_of.get(s) == etf]
        if cols:
            strong_rank[cols] = rs[cols].rank(axis=1, ascending=False, method="first")
            weak_rank[cols] = rs[cols].rank(axis=1, ascending=True, method="first")
    neg = pd.DataFrame(False, index=rs.index, columns=rs.columns)
    if sector_neg is not None:
        for s in rs.columns:
            ser = sector_neg.get(sector_of.get(s, ""))
            if ser is not None:
                neg[s] = _by_date(ser).reindex(rs.index).fillna(False).astype(bool).to_numpy()
    long_ok = (stg == 2) & (up >= need) & (strong_rank <= top) & rs.notna()
    short_ok = (stg == 4) & (dn >= need) & (weak_rank <= top) & rs.notna()
    if sector_neg is not None:
        long_ok &= ~neg
        short_ok &= neg
    return long_ok.fillna(False).astype(bool), short_ok.fillna(False).astype(bool)


def for_session(ok: pd.DataFrame, sym: str, session) -> bool | None:
    """The list as of the last close BEFORE `session` (a date). None when the symbol or the history is unknown."""
    if ok is None or ok.empty or sym not in ok.columns:
        return None
    prior = ok.index[ok.index < pd.Timestamp(session)]
    return bool(ok.at[prior[-1], sym]) if len(prior) else None
