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
