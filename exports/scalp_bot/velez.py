"""Oliver Velez-style study (separate preregistered study, backtest-only).

2-minute bars. Setup: 20/200 SMA "narrow state" in the last few bars, price on the trend side of both
SMAs with the 20 SMA sloping that way, and an entry bar: Elephant Bar (EB), Tail Bar (TB) or 180.
Entry: stop order 1 cent beyond the entry bar's high (long) / low (short), valid for 3 bars.
Stop: 1 cent beyond the entry bar's tail. Exit rule (fixed up front): 2R target, breakeven at +1R,
90-minute time stop, flat by 15:55.
"""
import numpy as np
import pandas as pd

import config
from common import Signal
from experts import Expert, _cents, _nan
from indicators import prepare_intraday

VZ_NARROW_PCT = 0.0020      # 20/200 SMA gap below 0.20% of price = narrow state
VZ_NARROW_LOOKBACK = 10     # ... at some point in the last 10 bars (20 minutes)
VZ_EB_MULT = 2.0            # elephant bar: body >= 2x the average body of the prior 20 bars
VZ_TRIGGER_BARS = 3         # stop-entry order stays live for 3 bars
VZ_MAX_POSITIONS = 10       # one expert here, so allow 10 concurrent positions (0.5% each)
VZ_TF = 2                   # bar size in minutes (--tf); rules are identical at every timeframe


def warmup_days():
    """Calendar days of 1-min history needed before the start so the 200-bar SMA is ready."""
    return max(6, int(200 * VZ_TF / 390 * 1.6) + 4)


def to_bars(df1, tf):
    """Session-anchored tf-minute bars (09:30 + k*tf) from 1-minute bars."""
    x = df1[["open", "high", "low", "close", "volume"]].sort_index()
    x = x[~x.index.duplicated(keep="last")]
    ny = x.index.tz_convert("America/New_York")
    mod = np.asarray(ny.hour * 60 + ny.minute - 570)
    x = x[(mod >= 0) & (mod < 390)]
    ny = ny[(mod >= 0) & (mod < 390)]
    mod = mod[(mod >= 0) & (mod < 390)]
    start = x.index - pd.to_timedelta(mod % tf, unit="min")      # bar start, anchored to 09:30
    g = x.groupby(start)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum()})
    out.index.name = x.index.name
    return out.dropna(subset=["open", "close"])


def to_2min(df1):
    return to_bars(df1, 2)


def add_velez(df):
    c, o, h, l = df["close"], df["open"], df["high"], df["low"]
    s20, s200 = c.rolling(20).mean(), c.rolling(200).mean()
    df["vz_s20"], df["vz_s200"] = s20, s200
    gap = (s20 - s200).abs() / c
    df["vz_narrow"] = gap.rolling(VZ_NARROW_LOOKBACK).min() < VZ_NARROW_PCT
    df["vz_slope"] = s20 - s20.shift(5)
    df["vz_body_avg"] = (c - o).abs().rolling(20).mean().shift(1)
    return df


def prepare_2min(df1):
    """Name kept for compatibility: builds VZ_TF-minute bars (2 by default)."""
    return add_velez(prepare_intraday(to_bars(df1, VZ_TF)))


def pattern(a, i):
    """('EB'|'TB'|'180', side) for bar i, or (None, None)."""
    o, h, l, c = a["open"][i], a["high"][i], a["low"][i], a["close"][i]
    po, pc = a["open"][i - 1], a["close"][i - 1]
    rng, body, avg = h - l, abs(c - o), a["vz_body_avg"][i]
    if rng <= 0 or _nan(avg) or avg <= 0:
        return None, None
    if body >= VZ_EB_MULT * avg and body >= 0.7 * rng:
        return "EB", (1 if c > o else -1)
    lower, upper = min(o, c) - l, h - max(o, c)
    if lower >= 0.5 * rng and c >= l + 0.66 * rng:
        return "TB", 1
    if upper >= 0.5 * rng and c <= l + 0.34 * rng:
        return "TB", -1
    if pc < po and c > o and c >= po and o <= pc and body >= 0.6 * rng:
        return "180", 1
    if pc > po and c < o and c <= po and o >= pc and body >= 0.6 * rng:
        return "180", -1
    return None, None


class VelezExpert(Expert):
    id, name, style = "VELEZ", "Velez 20/200 narrow-state breakout", "breakout"
    DEFAULTS = dict(start_mod=10, last_mod=config.LAST_ENTRY_MOD, rr=2.0, max_hold=90)

    def entry(self, sym, a, i, dirs):
        if i < 2 or not self.in_window(a, i) or not a["vz_narrow"][i]:
            return None
        kind, side = pattern(a, i)
        if side is None or side not in dirs:
            return None
        c, s20, s200, slope = a["close"][i], a["vz_s20"][i], a["vz_s200"][i], a["vz_slope"][i]
        if _nan(s20, s200, slope):
            return None
        if side > 0 and not (c > s20 and c > s200 and slope > 0):
            return None
        if side < 0 and not (c < s20 and c < s200 and slope < 0):
            return None
        h, l = float(a["high"][i]), float(a["low"][i])
        trigger = round(h + 0.01, 2) if side > 0 else round(l - 0.01, 2)
        stop = round(l - 0.01, 2) if side > 0 else round(h + 0.01, 2)
        risk = abs(trigger - stop)
        target = _cents(trigger + side * self.p["rr"] * risk, down=side < 0)
        return Signal(self.id, sym, side, trigger, stop, target, risk,
                      f"{kind} narrow-state @{trigger}", trigger=trigger)


def activate():
    """Study-specific engine settings."""
    config.MAX_POSITIONS_PER_EXPERT = VZ_MAX_POSITIONS
    config.PENDING_EXPIRY_S = VZ_TRIGGER_BARS * VZ_TF * 60
    print(f"STUDY: Velez {VZ_TF}-min (narrow < {VZ_NARROW_PCT:.2%}, EB/TB/180, stop-entry, 2R target, "
          f"max {VZ_MAX_POSITIONS} positions)")
    return [VelezExpert()]


def cost_check(arrays, sym_sector, start, end, slip_bps):
    """Before any P&L test: how wide are the stops, and how much of 1R do costs eat?"""
    e = VelezExpert()
    lo, hi = int(start.strftime("%Y%m%d")), int(end.strftime("%Y%m%d"))
    rows = []
    for s in sym_sector:
        a = arrays.get(s)
        if a is None:
            continue
        for i in np.flatnonzero((a["session"] >= lo) & (a["session"] <= hi)):
            if i < 200:
                continue
            sig = e.entry(s, a, i, {1, -1})
            if sig:
                rows.append((a["session"][i], s, sig.side, sig.reason.split()[0], sig.risk / sig.price * 1e4))
    print("\n================ VELEZ COST CHECK (no trades simulated) ================")
    if not rows:
        print("No setups found in this period.")
        return
    d = pd.DataFrame(rows, columns=["day", "symbol", "side", "kind", "stop_bps"])
    d["cost_R"] = 2 * slip_bps / d["stop_bps"]
    days = d["day"].nunique()
    q = [0.1, 0.25, 0.5, 0.75, 0.9]
    print(f"setups {len(d)} over {days} days  (~{len(d) / max(days, 1):.0f}/day across the universe)")
    print(d["kind"].value_counts().to_string())
    print("\nstop width, basis points:  " + "  ".join(f"p{int(x * 100)}={d.stop_bps.quantile(x):.0f}" for x in q))
    print(f"round-trip cost as a share of 1R at {slip_bps:g} bps/side:  "
          + "  ".join(f"p{int(x * 100)}={d.cost_R.quantile(x):.2f}" for x in q))
    ok = (d.cost_R <= 0.10).mean() * 100
    med = d.cost_R.median()
    print(f"setups where costs are <= 10% of 1R: {ok:.0f}%")
    print(f"VERDICT: {'PASS' if med <= 0.10 else 'FAIL'} - median cost {med:.2f}R "
          f"({'worth a P&L backtest' if med <= 0.10 else 'costs too large vs risk, same trap as the scalper'})")


STOP_GRID_BPS = [5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200]


def _trades_for_scan(arrays, sym_sector, frames, start, end, slip, horizon_min):
    """Every Velez setup that actually triggered, with its full price path to the horizon.
    One trade per symbol at a time (a new setup is ignored until the previous path ends), so every
    stop in the grid is tested on exactly the same trades."""
    from common import allowed_dirs
    from regime import build_heatmap
    from datetime import date as _date
    e = VelezExpert()
    lo, hi = int(start.strftime("%Y%m%d")), int(end.strftime("%Y%m%d"))
    regime_cache, out = {}, []
    for sym, sector in sym_sector.items():
        a = arrays.get(sym)
        if a is None:
            continue
        mod, sess = a["mod"], a["session"]
        o, h, l, c = a["open"], a["high"], a["low"], a["close"]
        busy_until = -1
        for i in np.flatnonzero((sess >= lo) & (sess <= hi)):
            if i < 200 or i <= busy_until:
                continue
            d = int(sess[i])
            if d not in regime_cache:
                heat = build_heatmap(frames, _date(d // 10000, d // 100 % 100, d % 100))
                regime_cache[d] = heat["regime"].to_dict()
            dirs = allowed_dirs(e.style, regime_cache[d][sector])
            sig = e.entry(sym, a, i, dirs) if dirs else None
            if not sig:
                continue
            side, trig = sig.side, sig.trigger
            j = None
            for k in range(i + 1, min(i + 1 + VZ_TRIGGER_BARS, len(c))):
                if sess[k] != d:
                    break
                if (side > 0 and h[k] >= trig) or (side < 0 and l[k] <= trig):
                    j = k
                    break
            if j is None:
                continue
            fill = (max(o[j], trig) if side > 0 else min(o[j], trig)) * (1 + side * slip)
            last_mod = min(mod[j] + horizon_min, config.FLATTEN_MOD)
            k = j
            while k + 1 < len(c) and sess[k + 1] == d and mod[k + 1] <= last_mod:
                k += 1
            busy_until = k
            tail_bps = abs(trig - sig.stop) / trig * 1e4
            out.append(dict(day=d, symbol=sym, side=side, fill=fill, tail_bps=tail_bps,
                            o=o[j:k + 1].copy(), h=h[j:k + 1].copy(), l=l[j:k + 1].copy(), exit_close=c[k]))
    return out


def _result_bps(t, stop_bps, slip):
    """Net return in bps for one trade with a stop stop_bps away (None = no stop)."""
    side, fill = t["side"], t["fill"]
    if stop_bps is not None:
        stop = fill * (1 - side * stop_bps / 1e4)
        hit = (t["l"] <= stop) if side > 0 else (t["h"] >= stop)
        if hit.any():
            m = int(np.argmax(hit))
            px = stop if m == 0 else (min(t["o"][m], stop) if side > 0 else max(t["o"][m], stop))
            return side * (px * (1 - side * slip) - fill) / fill * 1e4, True
    px = t["exit_close"] * (1 - side * slip)
    return side * (px - fill) / fill * 1e4, False


def _grid(trades, slip, stops):
    rows = []
    for s in stops:
        pairs = [_result_bps(t, s if s != "tail" else t["tail_bps"], slip) for t in trades]
        res = np.array([p[0] for p in pairs])
        hit = np.mean([p[1] for p in pairs])
        loss = -res[res < 0].sum()
        rows.append(dict(stop=("none" if s is None else "bar tail (v1 rule)" if s == "tail" else f"{s} bps"),
                         stopped_pct=round(hit * 100, 1), avg_net_bps=round(res.mean(), 2),
                         win_pct=round((res > 0).mean() * 100, 1),
                         profit_factor=round(res[res > 0].sum() / loss, 3) if loss > 0 else float("inf"),
                         usd_per_trade=round(res.mean() / 1e4 * 500, 3)))
    return pd.DataFrame(rows)


def stop_scan(arrays, sym_sector, frames, start, end, slip_bps, horizon_min=90):
    """Run every triggered setup with NO stop to the horizon, then replay each stop width on the same
    trades. The stop is PICKED on the first 2/3 of days and CHECKED on the last 1/3."""
    slip = slip_bps / 1e4
    trades = _trades_for_scan(arrays, sym_sector, frames, start, end, slip, horizon_min)
    print(f"\n================ VELEZ STOP SCAN ({VZ_TF}-min bars, exit after {horizon_min} min "
          f"or 15:55, {slip_bps:g} bps/side) ================")
    if len(trades) < 30:
        print(f"Only {len(trades)} triggered setups - too few to judge.")
        return
    days = sorted({t["day"] for t in trades})
    cut = days[int(len(days) * 2 / 3)]
    pick = [t for t in trades if t["day"] < cut]
    check = [t for t in trades if t["day"] >= cut]
    print(f"triggered trades: {len(trades)} over {len(days)} days "
          f"(pick set {len(pick)} trades before {cut}, check set {len(check)} from {cut})")

    mae = np.array([(t["fill"] - t["l"].min()) / t["fill"] * 1e4 if t["side"] > 0
                    else (t["h"].max() - t["fill"]) / t["fill"] * 1e4 for t in trades])
    mfe = np.array([(t["h"].max() - t["fill"]) / t["fill"] * 1e4 if t["side"] > 0
                    else (t["fill"] - t["l"].min()) / t["fill"] * 1e4 for t in trades])
    q = [0.25, 0.5, 0.75, 0.9]
    print("worst move against (MAE), bps: " + "  ".join(f"p{int(x * 100)}={np.quantile(mae, x):.0f}" for x in q))
    print("best move in favour (MFE), bps: " + "  ".join(f"p{int(x * 100)}={np.quantile(mfe, x):.0f}" for x in q))

    stops = [None, "tail"] + STOP_GRID_BPS
    g_all = _grid(trades, slip, stops)
    print("\n--- all trades ---")
    print(g_all.to_string(index=False))
    g_pick = _grid(pick, slip, stops)
    best = g_pick.loc[g_pick["avg_net_bps"].idxmax()]
    g_check = _grid(check, slip, stops)
    chk = g_check.loc[g_check["stop"] == best["stop"]].iloc[0]
    none_chk = g_check.loc[g_check["stop"] == "none"].iloc[0]
    print(f"\nBEST STOP on pick set: {best['stop']}  ({best['avg_net_bps']:+.2f} bps/trade, "
          f"PF {best['profit_factor']})")
    print(f"same stop on CHECK set: {chk['avg_net_bps']:+.2f} bps/trade, PF {chk['profit_factor']}  "
          f"(no stop on check set: {none_chk['avg_net_bps']:+.2f})")
    gross = np.mean([_result_bps(t, None, 0.0)[0] for t in trades])
    print(f"\nno-stop result BEFORE costs: {gross:+.2f} bps/trade  |  round-trip cost: {2 * slip_bps:.1f} bps")
    ok = chk["avg_net_bps"] > 0 and best["avg_net_bps"] > 0
    print("VERDICT: " + ("PROMISING - best stop is profitable on both halves; next step is a full backtest "
                         "with this stop, then 2025 held-out data" if ok else
                         "NO EDGE - no stop is profitable on data it was not chosen on"))
