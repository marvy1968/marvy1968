"""Daily swing study: 5-indicator overbought/oversold engine on mid & small caps.

    python swing.py --start 2016-01-01 --end 2021-12-31          # discovery backtest
    python swing.py --scan                                        # today's setups (no trading)

SETUP ("promising"): long-term uptrend (close > SMA200 and SMA50 > SMA200) AND an oversold pullback:
at least OBOS_MIN_VOTES of these 5 indicators oversold on the day's close:
    RSI(2) < 10   Stochastic %K(14,3) < 20   Bollinger %B(20,2) < 0   CCI(20) < -100   MFI(14) < 20
ENTRY next day's open. EXITS (fixed up front): RSI(2) > 60 or close > SMA5 -> sell next open;
stop 3 x ATR(14) below entry (intraday, gap-aware); 10-trading-day time stop. Long only.
UNIVERSE (point-in-time, monthly): US stocks ranked by 20-day dollar volume, EXCLUDING the top 500
(~large caps), price >= $5, 20-day dollar volume >= $5M. Costs scale with liquidity.
"""
import argparse
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from indicators import NY, atr, rsi

# ---------------- preregistered parameters ----------------
OBOS_MIN_VOTES = 4
RSI2_LO, RSI2_HI = 10, 90
STOCH_LO, STOCH_HI = 20, 80
PCTB_LO, PCTB_HI = 0.0, 1.0
CCI_LO, CCI_HI = -100, 100
MFI_LO, MFI_HI = 20, 80
EXIT_RSI2 = 60
STOP_ATR = 3.0
MAX_HOLD_DAYS = 10
MAX_POSITIONS = 15
POSITION_PCT = 0.05
LARGE_CAP_RANK = 500
MIN_ADV = 5e6
MIN_PRICE = 5.0
DISCOVERY_END = date(2021, 12, 31)       # 2022 onward is held out
COST_TIERS = [(50e6, 5.0), (20e6, 10.0), (0.0, 20.0)]   # (min 20-day $ volume, bps per side)


# ---------------- indicators (all use only the current and earlier daily bars) ----------------
def obos_frame(df):
    c, h, l, v = (df[k].astype(float) for k in ("close", "high", "low", "volume"))
    out = pd.DataFrame(index=df.index)
    out["rsi2"] = rsi(c, 2)
    ll, hh = l.rolling(14).min(), h.rolling(14).max()
    out["stoch"] = (100 * (c - ll) / (hh - ll).replace(0, np.nan)).rolling(3).mean()
    m20, sd20 = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    out["pctb"] = (c - (m20 - 2 * sd20)) / (4 * sd20).replace(0, np.nan)
    tp = (h + l + c) / 3
    mad = np.full(len(tp), np.nan)
    if len(tp) >= 20:
        w = np.lib.stride_tricks.sliding_window_view(tp.to_numpy(), 20)
        mad[19:] = np.abs(w - w.mean(axis=1, keepdims=True)).mean(axis=1)
    out["cci"] = (tp - tp.rolling(20).mean()) / (0.015 * pd.Series(mad, index=df.index)).replace(0, np.nan)
    flow = tp * v
    up = flow.where(tp > tp.shift(1), 0.0).rolling(14).sum()
    dn = flow.where(tp < tp.shift(1), 0.0).rolling(14).sum()
    mfi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    out["mfi"] = mfi.where(dn != 0, 100.0).where(up.notna())
    out["os_votes"] = ((out.rsi2 < RSI2_LO).astype(int) + (out.stoch < STOCH_LO) + (out.pctb < PCTB_LO)
                       + (out.cci < CCI_LO) + (out.mfi < MFI_LO))
    out["ob_votes"] = ((out.rsi2 > RSI2_HI).astype(int) + (out.stoch > STOCH_HI) + (out.pctb > PCTB_HI)
                       + (out.cci > CCI_HI) + (out.mfi > MFI_HI))
    out["obos_score"] = out["ob_votes"] - out["os_votes"]          # -5 very oversold ... +5 very overbought
    s50, s200 = c.rolling(50).mean(), c.rolling(200).mean()
    out["uptrend"] = (c > s200) & (s50 > s200)
    out["sma5"] = c.rolling(5).mean()
    out["atr14"] = atr(df.astype(float), 14)
    out["adv20"] = (c * v).rolling(20).mean()
    return out


def _wide(panel, col, frames=None):
    src = frames if frames is not None else panel
    return pd.DataFrame({s: d[col] for s, d in src.items()}).sort_index()


def membership(adv, close, large_rank=LARGE_CAP_RANK, min_adv=MIN_ADV, min_price=MIN_PRICE):
    """Point-in-time monthly universe: uses the PREVIOUS day's 20-day $ volume and close."""
    a, c = adv.shift(1), close.shift(1)
    rank = a.rank(axis=1, ascending=False)
    ok = (rank > large_rank) & (a >= min_adv) & (c >= min_price)
    month = ok.index.to_period("M")
    first = ~pd.Series(month).duplicated().to_numpy()
    snap = ok.astype(float)
    snap.loc[~first, :] = np.nan
    return snap.ffill().fillna(0).astype(bool)


def slip_bps(adv_value, cost_mult=1.0):
    for floor, bps in COST_TIERS:
        if adv_value >= floor:
            return bps * cost_mult
    return COST_TIERS[-1][1] * cost_mult


# ---------------- daily portfolio backtest ----------------
def run(panel, start, end, cost_mult=1.0, equity0=100_000.0, large_rank=LARGE_CAP_RANK, min_votes=OBOS_MIN_VOTES,
        allow=None):
    """panel: {symbol: daily OHLCV DataFrame indexed by date}. Returns (trades DataFrame, equity Series)."""
    feats = {s: obos_frame(d) for s, d in panel.items() if len(d) >= 30}
    syms = sorted(feats)
    O, H, L, C = (_wide(panel, k).reindex(columns=syms) for k in ("open", "high", "low", "close"))
    F = {k: _wide(None, k, feats).reindex(columns=syms) for k in
         ("os_votes", "rsi2", "sma5", "atr14", "adv20", "uptrend", "stoch", "pctb", "cci", "mfi")}
    member = membership(F["adv20"], C, large_rank)
    dates = C.index
    di0 = int(np.searchsorted(dates, pd.Timestamp(start)))
    di1 = int(np.searchsorted(dates, pd.Timestamp(end), side="right"))
    o, h, l, c = O.to_numpy(), H.to_numpy(), L.to_numpy(), C.to_numpy()
    votes, r2, s5, at, adv = (F[k].to_numpy() for k in ("os_votes", "rsi2", "sma5", "atr14", "adv20"))
    up, mem = F["uptrend"].fillna(False).to_numpy(bool), member.reindex(columns=syms).fillna(False).to_numpy(bool)
    alw = None if allow is None else allow.reindex(index=dates, columns=syms).fillna(True).to_numpy(bool)

    cash, pos, pend_in, pend_out = equity0, {}, [], {}
    trades, curve, last_px = [], [], {}

    def close_pos(j, px, di, why):
        nonlocal cash
        p = pos.pop(j)
        cash += p["qty"] * px
        trades.append(dict(symbol=syms[j], signal_date=dates[p["sig_di"]].date(), entry_date=dates[p["di"]].date(),
                           exit_date=dates[di].date(), entry=round(p["px"], 4), exit=round(px, 4),
                           ret_pct=round((px / p["px"] - 1) * 100, 3), days=di - p["di"], reason=why,
                           votes=int(p["votes"]), cost_bps=p["bps"],
                           pnl=round(p["qty"] * (px - p["px"]), 2)))

    for di in range(max(di0, 1), di1):
        # 1) exits decided yesterday -> today's open
        for j in list(pend_out):
            if j not in pos:
                pend_out.pop(j)
            elif np.isfinite(o[di, j]):
                close_pos(j, o[di, j] * (1 - pos[j]["bps"] / 1e4), di, pend_out.pop(j))
        # 2) entries decided yesterday -> today's open
        eq_prev = curve[-1][1] if curve else equity0
        for j, sig_di in pend_in:
            if j in pos or not np.isfinite(o[di, j]) or not np.isfinite(at[sig_di, j]):
                continue
            bps = slip_bps(adv[sig_di, j], cost_mult)
            px = o[di, j] * (1 + bps / 1e4)
            qty = int(POSITION_PCT * eq_prev // px)
            if qty < 1 or qty * px > cash:
                continue
            cash -= qty * px
            pos[j] = dict(px=px, qty=qty, di=di, sig_di=sig_di, stop=px - STOP_ATR * at[sig_di, j],
                          bps=bps, votes=votes[sig_di, j])
        pend_in = []
        # 3) intraday stops (gap-aware)
        for j in list(pos):
            if np.isfinite(l[di, j]) and l[di, j] <= pos[j]["stop"]:
                px = min(o[di, j], pos[j]["stop"]) if di > pos[j]["di"] else pos[j]["stop"]
                close_pos(j, px * (1 - pos[j]["bps"] / 1e4), di, "stop")
        # 4) end-of-day exit signals
        for j, p in pos.items():
            if not np.isfinite(c[di, j]):
                continue
            last_px[j] = c[di, j]
            if di - p["di"] + 1 >= MAX_HOLD_DAYS:
                pend_out[j] = "time"
            elif r2[di, j] > EXIT_RSI2 or c[di, j] > s5[di, j]:
                pend_out[j] = "engine_neutral"
        # 5) new setups at today's close -> enter tomorrow
        slots = MAX_POSITIONS - len(pos) + len(pend_out)
        if slots > 0:
            ok = mem[di] & up[di] & (np.nan_to_num(votes[di]) >= min_votes) & np.isfinite(c[di])
            if alw is not None:
                ok &= alw[di]                      # optional external filter (e.g. news engine)
            cand = [j for j in np.flatnonzero(ok) if j not in pos]
            cand.sort(key=lambda j: (-votes[di, j], r2[di, j]))
            pend_in = [(j, di) for j in cand[:slots]]
        # 6) mark to market
        eq = cash + sum(p["qty"] * last_px.get(j, p["px"]) for j, p in pos.items())
        curve.append((dates[di], eq))
    for j in list(pos):
        close_pos(j, last_px.get(j, pos[j]["px"]) * (1 - pos[j]["bps"] / 1e4), di1 - 1, "period_end")
    eqs = pd.Series(dict(curve), dtype=float)
    return pd.DataFrame(trades), eqs


# ---------------- reporting ----------------
def perf(eq):
    eq = eq.dropna()
    if len(eq) < 2:
        return dict(cagr_pct=np.nan, max_dd_pct=np.nan, sharpe=np.nan)
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    return dict(cagr_pct=round(((eq.iloc[-1] / eq.iloc[0]) ** (1 / max(yrs, 1e-9)) - 1) * 100, 2),
                max_dd_pct=round((eq / eq.cummax() - 1).min() * 100, 2),
                sharpe=round(r.mean() / r.std() * np.sqrt(252), 2) if r.std() > 0 else np.nan)


def report(trades, eq, bench, out="swing_results"):
    os.makedirs(out, exist_ok=True)
    print("\n================ SWING STUDY: oversold pullbacks in uptrends, mid & small caps ================")
    print("NOTE: universe = stocks listed TODAY (delisted names missing) -> survivorship bias flatters results.")
    if trades.empty:
        print("No trades.")
        return
    trades.to_csv(os.path.join(out, "trades.csv"), index=False)
    eq.to_csv(os.path.join(out, "equity.csv"))
    r = trades["ret_pct"]
    pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else float("inf")
    print(f"trades {len(trades)} | win {(r > 0).mean() * 100:.1f}% | avg {r.mean():+.2f}% | median {r.median():+.2f}% "
          f"| profit factor {pf:.2f} | avg hold {trades['days'].mean():.1f} days | t-stat "
          f"{r.mean() / (r.std() / np.sqrt(len(r))):.2f}")
    rows = {"strategy": perf(eq)}
    for name, s in bench.items():
        s = s.reindex(eq.index).ffill()
        rows[f"buy & hold {name}"] = perf(s / s.iloc[0] * eq.iloc[0])
    print(pd.DataFrame(rows).T.to_string())
    yrs = sorted({d.year for d in eq.index})
    cut = yrs[int(len(yrs) * 2 / 3)] if len(yrs) >= 3 else yrs[-1]
    ty = pd.to_datetime(trades["entry_date"]).dt.year
    for label, m in (("first 2/3 (pick)", ty < cut), (f"from {cut} (check)", ty >= cut)):
        rr = r[m]
        if len(rr):
            print(f"  {label:<18} trades {len(rr):>5}  avg {rr.mean():+.2f}%  win {(rr > 0).mean() * 100:.1f}%")
    print("\n--- by exit reason ---")
    print(trades.groupby("reason")["ret_pct"].agg(["count", "mean"]).round(3).to_string())
    print("\n--- by oversold votes at signal ---")
    print(trades.groupby("votes")["ret_pct"].agg(["count", "mean"]).round(3).to_string())
    print("\n--- by cost tier (bps per side) ---")
    print(trades.groupby("cost_bps")["ret_pct"].agg(["count", "mean"]).round(3).to_string())
    print(f"\nFiles written to {out}/")


# ---------------- Alpaca data ----------------
def _symbols(universe_file):
    if universe_file:
        s = pd.read_csv(universe_file, header=None).iloc[:, 0].astype(str).str.strip().str.upper()
        return sorted(set(s[s.str.fullmatch(r"[A-Z.]{1,6}")]))
    import config
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest
    tc = TradingClient(config.API_KEY, config.API_SECRET, paper=True)
    assets = tc.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY))
    bad = ("ETF", "ETN", " FUND", "WARRANT", "UNIT", "RIGHT", "PREFERRED", "DEPOSITARY SH", "NOTES DUE")
    keep = []
    for a in assets:
        ex = str(getattr(a.exchange, "value", a.exchange)).upper()
        name = (a.name or "").upper()
        if a.tradable and ex in ("NYSE", "NASDAQ", "AMEX") and a.symbol.isalpha() and len(a.symbol) <= 5 \
                and not any(b in name for b in bad):
            keep.append(a.symbol)
    return sorted(keep)


def load_panel(symbols, start, end, feed="sip", cache_dir="data_cache"):
    from alpaca.data.timeframe import TimeFrame
    from data import fetch_bars, make_data_client
    os.makedirs(cache_dir, exist_ok=True)
    key = hashlib.md5(json.dumps([symbols, str(start), str(end), feed]).encode()).hexdigest()[:16]
    path = os.path.join(cache_dir, f"daily_{key}.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    print(f"Downloading daily bars for {len(symbols)} symbols (cached after first run)...")
    t0 = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    t1 = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc)
    raw = fetch_bars(make_data_client(), symbols, TimeFrame.Day, t0, t1, feed, "all", chunk=100)
    panel = {}
    for s, d in raw.items():
        d = d[["open", "high", "low", "close", "volume"]].astype(float)
        d.index = pd.DatetimeIndex(d.index.tz_convert(NY).date)
        panel[s] = d[~d.index.duplicated(keep="last")]
    pd.to_pickle(panel, path)
    return panel


def scan(panel, top=25):
    feats = {s: obos_frame(d) for s, d in panel.items() if len(d) >= 210}
    last = {s: f.iloc[-1] for s, f in feats.items()}
    adv = pd.Series({s: r["adv20"] for s, r in last.items()})
    rank = adv.rank(ascending=False)
    rows = []
    for s, r in last.items():
        px = panel[s]["close"].iloc[-1]
        if rank[s] <= LARGE_CAP_RANK or r["adv20"] < MIN_ADV or px < MIN_PRICE or not r["uptrend"]:
            continue
        if r["os_votes"] >= OBOS_MIN_VOTES - 1:
            rows.append(dict(symbol=s, votes=int(r["os_votes"]), close=round(px, 2), rsi2=round(r["rsi2"], 1),
                             stoch=round(r["stoch"], 1), pctb=round(r["pctb"], 2), cci=round(r["cci"], 0),
                             mfi=round(r["mfi"], 1), adv_musd=round(r["adv20"] / 1e6, 1),
                             stop=round(px - STOP_ATR * r["atr14"], 2)))
    asof = max(d.index[-1] for d in panel.values())
    print(f"\n=== SWING SCAN as of {asof}: uptrend + oversold, mid/small caps (not a recommendation) ===")
    if not rows:
        print("No setups today.")
        return
    t = pd.DataFrame(rows).sort_values(["votes", "rsi2"], ascending=[False, True]).head(top)
    print(t.to_string(index=False))
    print(f"\nvotes >= {OBOS_MIN_VOTES} = a setup under the rules; {OBOS_MIN_VOTES - 1} = watch list. "
          f"'stop' = close - {STOP_ATR:g} x ATR(14).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--universe-file", help="optional: one ticker per line (e.g. from an index fund's holdings)")
    ap.add_argument("--cost-mult", type=float, default=1.0, help="scale all trading costs (robustness)")
    ap.add_argument("--use-holdout", action="store_true", help=f"allow dates after {DISCOVERY_END}")
    ap.add_argument("--out", default="swing_results")
    a = ap.parse_args()
    if not a.scan:
        if not (a.start and a.end):
            raise SystemExit("give --start and --end (or --scan)")
        if pd.Timestamp(a.end).date() > DISCOVERY_END and not a.use_holdout:
            raise SystemExit(f"--end is after {DISCOVERY_END}: that data is HELD OUT. Freeze the rules first, "
                             f"then re-run with --use-holdout exactly once.")
    syms = _symbols(a.universe_file)
    print(f"universe candidates: {len(syms)} symbols")
    if a.scan:
        today = date.today()
        panel = load_panel(syms, today - timedelta(days=420), today)
        scan(panel)
        return
    start, end = pd.Timestamp(a.start).date(), pd.Timestamp(a.end).date()
    panel = load_panel(syms, start - timedelta(days=420), end + timedelta(days=1))
    bench = {}
    for b in ("SPY", "IWM", "IJH"):
        p = load_panel([b], start - timedelta(days=5), end + timedelta(days=1))
        if b in p:
            bench[b] = p[b]["close"]
    trades, eq = run(panel, start, end, a.cost_mult)
    report(trades, eq, bench, a.out)


if __name__ == "__main__":
    main()
