"""Top 10 experts, adapted from the fx-experts systems to stocks.

Source of the locks (see oanda-trading-bot/backtest_expert_locks.py, which
quotes forex_bots/fx-experts/FX_EXPERTS.md, branch claude/analysis-ak180w):
 1 lien-fader    stop 30p, TP 2R, no trail
 2 raschke-soup  trail stop to prior-day extreme, flat day 6, no TP
 3 faith-turtle  stop 2N, exit at 10-day low/high, no TP
 4 bollinger     Parabolic SAR trail, no TP
 5 crabel-idnr4  flat at close, stop = opposite side, no TP
 6 lien-zeros    stop 20p, TP 2R, no trail (book trails half, code takes whole at 2R)
 7 williams-vbo  bailout at first profitable close, stop 50% of yesterday's range, no TP
 8 ponsi-trend   breakeven after >=1R close, then trail EMA10 - 0.5 ATR, never loosened
 9 brooks-range  TP range midpoint if reward>=risk, stop 1p beyond signal bar, flat before 17:00
10 dalton-value  TP opposite value-area edge, stop 1p beyond failed extreme, flat before 17:00
tharp-random is EXCLUDED: it is a coin flip, not an expert system.

FX -> stock adaptation (documented, not hidden):
- "pips" become ATR units (stocks have no pips). A stop of "30p" is ~1 ATR.
- "daily" in the FX locks becomes the bar timeframe in use here: 5-min bars
  for scalp/day, daily bars for swing. A "day-6 flat" for Raschke becomes
  "flat after 6 bars of the entry timeframe" in the exit logic.
- Round-number levels for Lien Zeros are whole dollars ($1.00) and half
  dollars ($0.50) for stocks, instead of FX double-zero prices.
- Value area for Dalton is proxied by mean +/- 1 ATR (no order-book data).

Every expert is a pure function: signal(bars) -> dict | None.
bars: list of dicts with keys o, h, l, c, v (floats), oldest first.
A signal dict: {expert, side (+1 long / -1 short), entry, stop,
tp_r (or None), exit (exit-logic name), reason}.
No expert places orders, touches a broker, or reads keys.
"""
import math

# ---------------------------------------------------------------- helpers

def _c(bars):
    return [float(b["c"]) for b in bars]

def _h(bars):
    return [float(b["h"]) for b in bars]

def _l(bars):
    return [float(b["l"]) for b in bars]

def sma(xs, n):
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n

def ema(xs, n):
    if len(xs) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(xs[:n]) / n
    for x in xs[n:]:
        e = x * k + e * (1 - k)
    return e

def atr(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n

def bollinger(bars, n=20, k=2.0):
    c = _c(bars)
    if len(c) < n:
        return None
    m = sum(c[-n:]) / n
    var = sum((x - m) ** 2 for x in c[-n:]) / n
    sd = math.sqrt(var)
    return m, m + k * sd, m - k * sd, sd

def _signal(expert, side, entry, stop, tp_r, exit_logic, reason):
    return {"expert": expert, "side": side, "entry": entry, "stop": stop,
            "tp_r": tp_r, "exit": exit_logic, "reason": reason}

def _need(bars, n):
    return len(bars) >= n

# ---------------------------------------------------------------- 1 Lien Fader

def lien_fader(bars):
    """Mean-reversion fade: close beyond the Bollinger band, fade back to the
    mean. Stop ~1 ATR, TP 2R, no trail (lock)."""
    if not _need(bars, 21):
        return None
    a = atr(bars)
    bb = bollinger(bars)
    if a is None or bb is None or a <= 0:
        return None
    m, up, lo, _sd = bb
    c = bars[-1]["c"]
    if c > up:
        return _signal("lien_fader", -1, c, c + a, 2.0, "fixed_tp_2r",
                       "close above upper Bollinger band, fade to mean")
    if c < lo:
        return _signal("lien_fader", 1, c, c - a, 2.0, "fixed_tp_2r",
                       "close below lower Bollinger band, fade to mean")
    return None

# ---------------------------------------------------------------- 2 Raschke Soup

def raschke_soup(bars):
    """Turtle Soup: new 20-bar extreme, then a close back inside the range —
    the breakout failed, trade the other way. Stop beyond the extreme;
    exit trails to the prior extreme, flat after 6 bars (lock, timeframe-
    adapted). No fixed TP."""
    if not _need(bars, 21):
        return None
    a = atr(bars)
    if a is None or a <= 0:
        return None
    h, l, c = _h(bars), _l(bars), _c(bars)
    hi20, lo20 = max(h[-21:-1]), min(l[-21:-1])
    if h[-2] >= hi20 and c[-1] < hi20:  # failed upside breakout
        return _signal("raschke_soup", -1, c[-1], hi20 + 0.5 * a, None,
                       "soup_trail_flat_6",
                       "new 20-bar high then close back inside, short the failure")
    if l[-2] <= lo20 and c[-1] > lo20:  # failed downside breakout
        return _signal("raschke_soup", 1, c[-1], lo20 - 0.5 * a, None,
                       "soup_trail_flat_6",
                       "new 20-bar low then close back inside, long the failure")
    return None

# ---------------------------------------------------------------- 3 Faith Turtle

def faith_turtle(bars):
    """Trend breakout: close beyond the 20-bar high/low. Stop 2N (2 ATR),
    exit at the 10-bar low/high (lock). No fixed TP."""
    if not _need(bars, 21):
        return None
    a = atr(bars)
    if a is None or a <= 0:
        return None
    h, l, c = _h(bars), _l(bars), _c(bars)
    hi20, lo20 = max(h[-21:-1]), min(l[-21:-1])
    if c[-1] > hi20:
        return _signal("faith_turtle", 1, c[-1], c[-1] - 2.0 * a, None,
                       "turtle_10bar_exit",
                       "close above 20-bar high, trend breakout long")
    if c[-1] < lo20:
        return _signal("faith_turtle", -1, c[-1], c[-1] + 2.0 * a, None,
                       "turtle_10bar_exit",
                       "close below 20-bar low, trend breakout short")
    return None

# ---------------------------------------------------------------- 4 Bollinger Squeeze

def bollinger_squeeze(bars):
    """Volatility expansion: Bollinger bandwidth at its lowest in 10 bars
    (squeeze), then a close outside the band. Exit is a PSAR-style trail,
    proxied here by a 2-ATR trail (the original PSAR run on Yahoo was buggy
    and discarded — see README). No fixed TP."""
    if not _need(bars, 21):
        return None
    a = atr(bars)
    bb = bollinger(bars)
    if a is None or bb is None or a <= 0:
        return None
    m, up, lo, sd = bb
    c = _c(bars)
    if len(c) < 11:
        return None
    # bandwidth proxy: rolling std over the last 10 bars vs the current one
    widths = []
    for i in range(10):
        w = c[-11 - i:-1 - i] if len(c) >= 11 + i else []
        if len(w) >= 10:
            mm = sum(w) / len(w)
            widths.append(math.sqrt(sum((x - mm) ** 2 for x in w) / len(w)))
    if widths and sd <= min(widths) * 1.05:  # squeezed, at/near the 10-bar low
        if c[-1] > up:
            return _signal("bollinger_squeeze", 1, c[-1], c[-1] - 2.0 * a, None,
                           "atr_trail_2", "squeeze then close above upper band")
        if c[-1] < lo:
            return _signal("bollinger_squeeze", -1, c[-1], c[-1] + 2.0 * a, None,
                           "atr_trail_2", "squeeze then close below lower band")
    return None

# ---------------------------------------------------------------- 5 Crabel IDNR4

def crabel_idnr4(bars):
    """Narrow-range breakout: today's range is the narrowest in 4 bars
    (IDNR4) and the bar is inside the prior bar (inside day). Breakout of
    the narrow bar's high/low. Flat at close, stop = opposite side (lock).
    No fixed TP."""
    if not _need(bars, 5):
        return None
    a = atr(bars)
    if a is None or a <= 0:
        return None
    rng = [b["h"] - b["l"] for b in bars]
    last = bars[-1]
    if rng[-1] <= min(rng[-5:-1]) and last["h"] <= bars[-2]["h"] and last["l"] >= bars[-2]["l"]:
        # narrow + inside: signal on the NEXT bar's breakout is the trade;
        # here we report the setup with the breakout levels as entry/stop.
        return _signal("crabel_idnr4", 1, last["h"], last["l"], None,
                       "flat_at_close",
                       "IDNR4 inside day, long on break of narrow-bar high")
    return None

# ---------------------------------------------------------------- 6 Lien Zeros

def lien_zeros(bars):
    """Double Zeros: price within 0.25 ATR of a whole/half dollar level,
    then turns away from it — trade the bounce. Stop ~0.75 ATR, TP 2R
    (lock; the book trails half, the code takes the whole at 2R)."""
    if not _need(bars, 3):
        return None
    a = atr(bars)
    if a is None or a <= 0:
        return None
    c = bars[-1]["c"]
    prev = bars[-2]["c"]
    # nearest whole or half dollar
    level = round(c * 2) / 2.0
    if abs(c - level) > 0.25 * a:
        return None
    if prev < level <= c:  # bounced up off the level
        return _signal("lien_zeros", 1, c, c - 0.75 * a, 2.0, "fixed_tp_2r",
                       f"bounce up off round level ${level:.2f}")
    if prev > level >= c:  # rejected down off the level
        return _signal("lien_zeros", -1, c, c + 0.75 * a, 2.0, "fixed_tp_2r",
                       f"rejection down off round level ${level:.2f}")
    return None

# ---------------------------------------------------------------- 7 Williams VBO

def williams_vbo(bars):
    """Volatility breakout: today's range > 1.5x the 10-bar average range
    and a close beyond the prior bar's extreme. Bailout at the first
    profitable close, stop = 50% of the prior bar's range (lock). No TP."""
    if not _need(bars, 11):
        return None
    rng = [b["h"] - b["l"] for b in bars]
    avg_rng = sum(rng[-11:-1]) / 10
    if avg_rng <= 0:
        return None
    c = bars[-1]["c"]
    if rng[-1] > 1.5 * avg_rng and c > bars[-2]["h"]:
        return _signal("williams_vbo", 1, c, c - 0.5 * rng[-2], None,
                       "bailout_first_profitable_close",
                       "volatility breakout long, range > 1.5x average")
    if rng[-1] > 1.5 * avg_rng and c < bars[-2]["l"]:
        return _signal("williams_vbo", -1, c, c + 0.5 * rng[-2], None,
                       "bailout_first_profitable_close",
                       "volatility breakout short, range > 1.5x average")
    return None

# ---------------------------------------------------------------- 8 Ponsi Trend

def ponsi_trend(bars):
    """Trend: close above EMA20 and EMA20 above EMA50 (long), pullback to
    EMA10, then a close back in the trend direction. Stop to breakeven
    after a >=1R close, then trail EMA10 - 0.5 ATR, never loosened (lock).
    No fixed TP."""
    if not _need(bars, 51):
        return None
    a = atr(bars)
    c = _c(bars)
    if a is None or a <= 0:
        return None
    e20, e50, e10 = ema(c, 20), ema(c, 50), ema(c, 10)
    if e20 is None or e50 is None or e10 is None:
        return None
    last = c[-1]
    # pullback: prior close was at/below EMA10, now back above it
    if last > e20 > e50 and c[-2] <= e10 < last:
        return _signal("ponsi_trend", 1, last, last - a, None,
                       "ponsi_be_then_ema_trail",
                       "uptrend, pullback to EMA10 then back above it")
    if last < e20 < e50 and c[-2] >= e10 > last:
        return _signal("ponsi_trend", -1, last, last + a, None,
                       "ponsi_be_then_ema_trail",
                       "downtrend, pullback to EMA10 then back below it")
    return None

# ---------------------------------------------------------------- 9 Brooks Range Fade

def brooks_range_fade(bars):
    """Range fade: 20-bar range, a bar pokes beyond the edge then closes
    back inside — the breakout failed, fade to the range midpoint. TP is
    the midpoint if reward >= risk, stop 1 ATR beyond the signal bar
    (lock, pips -> ATR). Flat at close."""
    if not _need(bars, 21):
        return None
    a = atr(bars)
    if a is None or a <= 0:
        return None
    h, l, c = _h(bars), _l(bars), _c(bars)
    hi20, lo20 = max(h[-21:-1]), min(l[-21:-1])
    mid = (hi20 + lo20) / 2.0
    if h[-1] > hi20 and c[-1] < hi20 and (hi20 - mid) >= a:
        return _signal("brooks_range_fade", -1, c[-1], h[-1] + a, None,
                       "midpoint_tp_flat_close",
                       "failed upside breakout, fade to range midpoint")
    if l[-1] < lo20 and c[-1] > lo20 and (mid - lo20) >= a:
        return _signal("brooks_range_fade", 1, c[-1], l[-1] - a, None,
                       "midpoint_tp_flat_close",
                       "failed downside breakout, fade to range midpoint")
    return None

# ---------------------------------------------------------------- 10 Dalton Value

def dalton_value(bars):
    """Value-area edge: value area proxied by the 20-bar mean +/- 1 ATR.
    Price pokes beyond the value edge then closes back inside — trade to
    the opposite value edge. TP is the opposite edge, stop beyond the
    failed extreme (lock). Flat at close."""
    if not _need(bars, 21):
        return None
    a = atr(bars)
    c = _c(bars)
    if a is None or a <= 0:
        return None
    m = sma(c, 20)
    if m is None:
        return None
    up, lo = m + a, m - a
    last = c[-1]
    if bars[-1]["h"] > up and last < up:
        return _signal("dalton_value", -1, last, bars[-1]["h"] + a, None,
                       "opposite_value_edge_flat_close",
                       "failed break above value area, trade to lower edge")
    if bars[-1]["l"] < lo and last > lo:
        return _signal("dalton_value", 1, last, bars[-1]["l"] - a, None,
                       "opposite_value_edge_flat_close",
                       "failed break below value area, trade to upper edge")
    return None

EXPERTS = {
    "lien_fader": lien_fader,
    "raschke_soup": raschke_soup,
    "faith_turtle": faith_turtle,
    "bollinger_squeeze": bollinger_squeeze,
    "crabel_idnr4": crabel_idnr4,
    "lien_zeros": lien_zeros,
    "williams_vbo": williams_vbo,
    "ponsi_trend": ponsi_trend,
    "brooks_range_fade": brooks_range_fade,
    "dalton_value": dalton_value,
}

# Sleeve assignment (config.yaml mirrors this).
SLEEVE_EXPERTS = {
    "scalp": ["crabel_idnr4", "williams_vbo", "lien_zeros", "bollinger_squeeze"],
    "day": ["lien_fader", "raschke_soup", "faith_turtle", "ponsi_trend",
            "brooks_range_fade", "dalton_value"],
    "swing": ["faith_turtle", "ponsi_trend", "raschke_soup", "dalton_value"],
}

def scan(bars, expert_names):
    """Run the named experts on one symbol's bars; return all signals."""
    out = []
    for name in expert_names:
        fn = EXPERTS.get(name)
        if fn is None:
            continue
        try:
            sig = fn(bars)
        except Exception:
            sig = None
        if sig:
            out.append(sig)
    return out
