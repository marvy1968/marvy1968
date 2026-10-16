"""Candlestick + volume + volatility confirmation at the range box's edges (owner, 2026-10-16: "use candlesticks, volume, volatility to
confirm trends are continuing or failing").

The box diagnosis (OANDA, 2016-22) showed the problem: price breaks out of the box on ~2 of 3 edge touches, so fading every touch loses.
This module reads each close OUTSIDE the box and classifies it:

CONTINUING  the breakout bar is a strong candle (body >= `body_min` of its range, closing in the outer quarter of the bar in the breakout
            direction), on expanding volume (>= vol_mult x the prior 20-bar average) and expanding volatility (ATR14 / ATR100 >= atr_ratio).
            -> trade WITH the breakout at the next open. Stop 0.25 ATR back inside the box edge (a breakout that falls back in has failed);
            TP1 = half the box height beyond the edge, target = the full box height beyond it (measured move).
FAILING     the breakout bar is weak (body < body_min, or its close sits in the inner half of the bar = a rejection wick) or comes without
            volume, and within `look` bars price closes back INSIDE the box (no deeper than the outer third) -> fade it at that close.
            Stop 0.25 ATR beyond the false break's extreme; TP1 = the box middle, target = the middle ("mid") or the far edge ("edge").

mode = "continuation", "failure" or "both". The box (top, bot) for bar i must be the box as it stood BEFORE bar i (no look-ahead), and
ok[i] says the box was valid then. Works on any bar arrays: forex hourly bars (fxbot.strategies.BoxConfirm) and stock 5-minute bars
against the stock's daily box (brain.BoxConfirmSignals)."""

import numpy as np


def confirm_signals(o, h, l, c, v, atr_s, atr_l, top, bot, ok, mode: str = "both", vol_mult: float = 1.5, atr_ratio: float = 1.1,
                    body_min: float = 0.6, look: int = 6, target: str = "mid", stop_buf: float = 0.25, allow=None, vote: float = 0.0) -> dict:
    """Arrays in, arrays out: long, short (bool), stop (distance), tp1 (price), tgt (distance), kind ("cont"/"fail"/"")."""
    n = len(c)
    o, h, l, c = (np.asarray(x, float) for x in (o, h, l, c))
    v = np.asarray(v, float) if v is not None else np.full(n, np.nan)
    A, AL = np.asarray(atr_s, float), np.asarray(atr_l, float)
    T, B = np.asarray(top, float), np.asarray(bot, float)
    okb = np.asarray(ok, bool)
    allow = np.ones(n, bool) if allow is None else np.asarray(allow, bool)
    vavg = np.full(n, np.nan)
    if np.isfinite(v).any():
        cs = np.concatenate([[0.0], np.cumsum(np.nan_to_num(v))])
        for i in range(20, n):
            vavg[i] = (cs[i] - cs[i - 20]) / 20.0                      # prior 20 bars, excluding bar i
    out = {"long": np.zeros(n, bool), "short": np.zeros(n, bool), "stop": np.full(n, np.nan), "tp1": np.full(n, np.nan),
           "tgt": np.full(n, np.nan), "kind": np.array([""] * n, dtype=object)}
    rng = np.maximum(h - l, 1e-12)
    body = np.abs(c - o) / rng
    for b in range(1, n - 1):
        if not okb[b] or not (np.isfinite(T[b]) and np.isfinite(B[b])) or not np.isfinite(A[b]) or A[b] <= 0:
            continue
        up, dn = c[b] > T[b], c[b] < B[b]
        if not (up or dn):
            continue
        side = 1 if up else -1                                         # the breakout's direction
        edge, far, hh = (T[b], B[b], T[b] - B[b]) if up else (B[b], T[b], T[b] - B[b])
        loc = (c[b] - l[b]) / rng[b] if up else (h[b] - c[b]) / rng[b]  # 1 = closed at the extreme in the breakout direction
        strong = body[b] >= body_min and loc >= 0.75
        vol_up = np.isfinite(vavg[b]) and vavg[b] > 0 and v[b] >= vol_mult * vavg[b]
        vola_up = np.isfinite(AL[b]) and AL[b] > 0 and A[b] / AL[b] >= atr_ratio
        if vote > 0:   # owner 2026-10-16: continuing when at least `vote` of the 5 indicators agree (body, close location, volume,
            #            volatility, a decisive close >= 0.25 ATR beyond the edge) instead of requiring all of them
            votes = [body[b] >= body_min, loc >= 0.75, vol_up, vola_up, side * (c[b] - edge) >= 0.25 * A[b]]
            confirmed = sum(bool(x) for x in votes) / 5.0 >= vote - 1e-9
        else:
            confirmed = strong and vol_up and vola_up
        if mode in ("continuation", "both") and confirmed and allow[b]:
            stop_px = edge - side * stop_buf * A[b]
            sd = side * (c[b] - stop_px)
            if sd > 0:
                out["long" if side == 1 else "short"][b] = True
                out["stop"][b], out["tp1"][b] = sd, edge + side * 0.5 * hh
                out["tgt"][b], out["kind"][b] = side * (edge + side * hh - c[b]), "cont"
            continue
        weak = (body[b] < body_min) or (loc < 0.5) or not vol_up
        if mode in ("failure", "both") and weak:
            for i in range(b + 1, min(n, b + 1 + look)):
                if out["long"][i] or out["short"][i]:
                    break
                back_in = (c[i] < edge and c[i] >= edge - hh / 3) if side == 1 else (c[i] > edge and c[i] <= edge + hh / 3)
                if back_in and allow[i]:
                    ext = h[b:i + 1].max() if side == 1 else l[b:i + 1].min()
                    fside = -side                                     # fade: against the failed breakout
                    stop_px = ext + side * stop_buf * A[i]
                    sd = fside * (c[i] - stop_px)
                    mid = (T[b] + B[b]) / 2
                    if sd > 0 and fside * (mid - c[i]) > 0:
                        out["long" if fside == 1 else "short"][i] = True
                        out["stop"][i], out["tp1"][i] = sd, mid
                        out["tgt"][i] = fside * ((mid if target == "mid" else far) - c[i])
                        out["kind"][i] = "fail"
                    break
                if (side == 1 and c[i] < edge - hh / 3) or (side == -1 and c[i] > edge + hh / 3):
                    break
    return out



def trend_votes(day, i: int, side: int, vol_mult: float = 1.2) -> tuple[int, list[str]]:
    """Owner 2026-10-16: a TREND expert's entry must show the trend continuing on its signal bar. Five checks (in the trade's direction):
    body (|close-open| >= 50% of the bar's range, closing the right way), close location (outer 25% of the bar), volume (>= vol_mult x the
    prior 20 bars), volatility (5-minute ATR >= its session average so far), follow-through (close beyond the previous bar's high / low).
    Returns (how many agree, the names of those that failed)."""
    o, h, l, c = (float(day[k].iloc[i]) for k in ("open", "high", "low", "close"))
    rng = max(h - l, 1e-12)
    names, ok = ["body", "close", "volume", "volatility", "follow-through"], []
    ok.append(side * (c - o) >= 0.5 * rng)
    ok.append(((c - l) / rng >= 0.75) if side == 1 else ((h - c) / rng >= 0.75))
    if "volume" in day and i >= 20:
        avg = float(day["volume"].iloc[i - 20:i].mean())
        ok.append(avg > 0 and float(day["volume"].iloc[i]) >= vol_mult * avg)
    else:
        ok.append(False)
    if "f_atr" in day and i >= 12:
        a = day["f_atr"].iloc[:i]
        ok.append(float(day["f_atr"].iloc[i]) >= float(a.mean()))
    else:
        ok.append(False)
    if i >= 1:
        ok.append(c > float(day["high"].iloc[i - 1]) if side == 1 else c < float(day["low"].iloc[i - 1]))
    else:
        ok.append(False)
    return sum(ok), [n for n, x in zip(names, ok) if not x]



def reversal_votes(day, i: int, side: int, vola_max: float = 1.2, stretch_atr: float = 1.0) -> tuple[int, list[str]]:
    """Owner 2026-10-16 ("does trend ID help the mean reversion? fix it" + "use Velez's philosophy on mean reversion"): a MEAN-REVERSION
    entry must show the move against it FAILING, not just stretched. Four checks for a trade in direction `side`:
    reversal candle - Velez: a BOTTOMING TAIL (lower wick >= 50% of the bar, close in its upper third), a 180 (the previous bar a wide
                      red bar, this one green and closing above its middle), or a plain reversal bar (body >= 40% the trade's way, close
                      in the outer half);
    push weakening  - none of the last 3 bars was a strong continuation AGAINST the trade (trend_votes(-side) <= 2 of 5 on each);
    no panic        - 5-minute ATR <= vola_max x its session average (Velez: never catch a wide-range "elephant" falling);
    stretched       - Velez's rubber band: the close is at least stretch_atr ATR beyond the 20 EMA against the trade (room to snap back).
    Returns (how many agree, the names of those that failed)."""
    o, h, l, c = (float(day[k].iloc[i]) for k in ("open", "high", "low", "close"))
    rng = max(h - l, 1e-12)
    names, ok = ["reversal candle", "push weakening", "no panic", "stretched"], []
    loc = (c - l) / rng if side == 1 else (h - c) / rng
    plain = side * (c - o) >= 0.4 * rng and loc >= 0.5
    tail = ((min(o, c) - l) if side == 1 else (h - max(o, c))) >= 0.5 * rng and loc >= 2 / 3
    v180 = False
    if i >= 1:
        po, ph, pl, pc = (float(day[k].iloc[i - 1]) for k in ("open", "high", "low", "close"))
        prng = max(ph - pl, 1e-12)
        avg_rng = float((day["high"] - day["low"]).iloc[max(0, i - 20):i].mean()) if i >= 2 else prng
        v180 = side * (po - pc) >= 0.5 * prng and prng >= avg_rng and side * (c - o) > 0 and side * (c - (ph + pl) / 2) > 0
    ok.append(plain or tail or v180)
    ok.append(all(trend_votes(day, j, -side)[0] <= 2 for j in range(max(1, i - 3), i)))
    a_i = float(day["f_atr"].iloc[i]) if "f_atr" in day else float("nan")
    if "f_atr" in day and i >= 12:
        ok.append(a_i <= vola_max * float(day["f_atr"].iloc[:i].mean()))
    else:
        ok.append(True)
    if "f_ema20" in day and a_i == a_i and a_i > 0:
        ref = float(day["low"].iloc[i]) if side == 1 else float(day["high"].iloc[i])
        ok.append(side * (float(day["f_ema20"].iloc[i]) - ref) >= stretch_atr * a_i)
    else:
        ok.append(False)
    return sum(ok), [n for n, x in zip(names, ok) if not x]
