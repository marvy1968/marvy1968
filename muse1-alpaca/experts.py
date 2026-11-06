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

def _signal(expert, side, entry, stop, tp_r, exit_logic, reason, target=None):
    """A signal dict. `target` is an explicit take-profit PRICE when the
    expert computes one (Brooks range midpoint, Dalton opposite value
    edge). When present it is THE target: bot.py uses this exact price
    for the submitted order — the same price _rr_ok() checked — so the
    checked target and the submitted target can never disagree
    (round-two fix, 2026-11-05)."""
    d = {"expert": expert, "side": side, "entry": entry, "stop": stop,
         "tp_r": tp_r, "exit": exit_logic, "reason": reason}
    if target is not None:
        d["target"] = target
    return d

def _need(bars, n):
    return len(bars) >= n

# Minimum reward:risk for the range/value fades (Brooks, Dalton), frozen
# 2026-11-05 (Claude fix list). Computed on the ACTUAL stop distance,
# not the 1-ATR proxy. 1.5 is stricter than the locks' own reward>=risk
# (1.0) because round-trip costs eat the edge at 1.0. bot.py overrides
# this from config.yaml `strategy.min_reward_risk` before scanning.
MIN_REWARD_RISK = 1.5

def _rr_ok(entry, stop, target):
    """True if the trade's reward (entry->target) is at least
    MIN_REWARD_RISK times its risk (entry->stop)."""
    risk = abs(entry - stop)
    reward = abs(entry - target)
    if risk <= 0 or reward <= 0:
        return False
    return reward / risk >= MIN_REWARD_RISK

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

def _bandwidth(closes, n=20, k=2.0):
    """Bollinger bandwidth (upper - lower) / middle on the last n closes.
    Same 20-bar window as bollinger() — the squeeze test must compare
    like with like."""
    if len(closes) < n:
        return None
    w = closes[-n:]
    m = sum(w) / n
    var = sum((x - m) ** 2 for x in w) / n
    sd = math.sqrt(var)
    return (2 * k * sd) / m if m else None

def bollinger_squeeze(bars):
    """Volatility expansion: Bollinger bandwidth at its lowest in 10 bars
    (squeeze), then a close outside the band. Exit is a PSAR-style trail,
    proxied here by a 2-ATR trail (the original PSAR run on Yahoo was buggy
    and discarded — see README). No fixed TP.

    FIX 2026-11-05 (squeeze definition): the old code compared the
    CURRENT 20-bar band std against the minimum of rolling 10-close
    stds — different window lengths, so the comparison was meaningless:
    a 20-bar std is systematically larger than a 10-bar std on a
    trending series, so genuine squeezes were missed and the test fired
    on the wrong thing. Now bandwidth is computed on the SAME 20-bar
    window for the current bar and each of the prior 10 completed bars,
    and the squeeze requires the current bandwidth to be at or near
    (within 5%) the 10-bar low."""
    if not _need(bars, 31):
        return None
    a = atr(bars)
    bb = bollinger(bars)
    if a is None or bb is None or a <= 0:
        return None
    m, up, lo, sd = bb
    c = _c(bars)
    # Squeeze judged on the market BEFORE the breakout bar: bandwidth
    # through the prior bar must be at/near its 10-bar low. (Judging it
    # on the current bar would fail by construction — the expansion bar
    # itself widens the band.)
    prior_closes = c[:-1]
    squeeze_bw = _bandwidth(prior_closes)
    if squeeze_bw is None:
        return None
    prior = []
    for j in range(1, 11):
        bw = _bandwidth(prior_closes[:-j])
        if bw is None:
            return None
        prior.append(bw)
    if squeeze_bw > min(prior) * 1.05:  # not squeezed: bandwidth not at 10-bar low
        return None
    if c[-1] > up:
        return _signal("bollinger_squeeze", 1, c[-1], c[-1] - 2.0 * a, None,
                       "atr_trail_2", "squeeze (bandwidth at 10-bar low) then close above upper band")
    if c[-1] < lo:
        return _signal("bollinger_squeeze", -1, c[-1], c[-1] + 2.0 * a, None,
                       "atr_trail_2", "squeeze (bandwidth at 10-bar low) then close below lower band")
    return None

# ---------------------------------------------------------------- 5 Crabel IDNR4

def crabel_idnr4(bars):
    """Narrow-range breakout: the SETUP bar is the narrowest in 4 bars
    (IDNR4) and inside the prior bar (inside day). The trade is entered
    ONLY on the NEXT bar, AFTER it closes beyond the narrow bar's high
    (long) or low (short). The setup alone is never a trade — the old
    code signalled long on the setup bar itself, before any breakout,
    and only ever long. Stop = opposite side of the narrow bar (lock).
    Flat at close, no fixed TP."""
    if not _need(bars, 7):
        return None
    a = atr(bars)
    if a is None or a <= 0:
        return None
    rng = [b["h"] - b["l"] for b in bars]
    setup = bars[-2]   # the narrow inside day
    prior = bars[-3]
    # setup: narrowest range of the 4 bars before it, and inside the prior bar
    if not (rng[-2] <= min(rng[-6:-2])
            and setup["h"] <= prior["h"] and setup["l"] >= prior["l"]):
        return None
    cur = bars[-1]     # the breakout bar — must CLOSE beyond the narrow bar
    if cur["c"] > setup["h"]:
        return _signal("crabel_idnr4", 1, cur["c"], setup["l"], None,
                       "flat_at_close",
                       "IDNR4 inside day, next bar closed above narrow-bar high")
    if cur["c"] < setup["l"]:
        return _signal("crabel_idnr4", -1, cur["c"], setup["h"], None,
                       "flat_at_close",
                       "IDNR4 inside day, next bar closed below narrow-bar low")
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
    No fixed TP.

    FIX 2026-11-05 (EMA timing): the pullback test must compare the
    PRIOR close against the PRIOR bar's EMA10, not against today's EMA10.
    The old code used ema(all closes, 10) — which includes the last bar —
    for both. A big up bar lifts today's EMA10 toward the last close, so
    the old test could "see" a pullback that never happened (prior close
    below today's EMA10 while it was above the EMA10 of its own bar).
    Now: e10_prev = ema(closes[:-1], 10)."""
    if not _need(bars, 51):
        return None
    a = atr(bars)
    c = _c(bars)
    if a is None or a <= 0:
        return None
    e20, e50, e10 = ema(c, 20), ema(c, 50), ema(c, 10)
    e10_prev = ema(c[:-1], 10)
    if e20 is None or e50 is None or e10 is None or e10_prev is None:
        return None
    last = c[-1]
    # pullback: prior close was at/below the PRIOR bar's EMA10, now back above it
    if last > e20 > e50 and c[-2] <= e10_prev and last > e10:
        return _signal("ponsi_trend", 1, last, last - a, None,
                       "ponsi_be_then_ema_trail",
                       "uptrend, pullback to EMA10 then back above it")
    if last < e20 < e50 and c[-2] >= e10_prev and last < e10:
        return _signal("ponsi_trend", -1, last, last + a, None,
                       "ponsi_be_then_ema_trail",
                       "downtrend, pullback to EMA10 then back below it")
    return None

# ---------------------------------------------------------------- 9 Brooks Range Fade

def brooks_range_fade(bars):
    """Range fade: 20-bar range, a bar pokes beyond the edge then closes
    back inside — the breakout failed, fade to the range midpoint. TP is
    the midpoint if reward >= risk, stop 1 ATR beyond the signal bar
    (lock, pips -> ATR). Flat at close.

    FIX 2026-11-05 (reward:risk): the old code only checked that the
    midpoint was >= 1 ATR away — it never compared the reward against
    the ACTUAL stop distance. A far stop can make a "reward >= 1 ATR"
    trade a bad bet. Now the entry is refused unless
    reward / actual-risk >= MIN_REWARD_RISK (1.5, frozen in config)."""
    if not _need(bars, 21):
        return None
    a = atr(bars)
    if a is None or a <= 0:
        return None
    h, l, c = _h(bars), _l(bars), _c(bars)
    hi20, lo20 = max(h[-21:-1]), min(l[-21:-1])
    mid = (hi20 + lo20) / 2.0
    # The target carried on the signal IS the midpoint that _rr_ok()
    # just checked (round-two fix): bot.py submits this price, it does
    # not recompute a different one from tp_r.
    if h[-1] > hi20 and c[-1] < hi20 and _rr_ok(c[-1], h[-1] + a, mid):
        return _signal("brooks_range_fade", -1, c[-1], h[-1] + a, None,
                       "midpoint_tp_flat_close",
                       "failed upside breakout, fade to range midpoint",
                       target=mid)
    if l[-1] < lo20 and c[-1] > lo20 and _rr_ok(c[-1], l[-1] - a, mid):
        return _signal("brooks_range_fade", 1, c[-1], l[-1] - a, None,
                       "midpoint_tp_flat_close",
                       "failed downside breakout, fade to range midpoint",
                       target=mid)
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
    # FIX 2026-11-05 (reward:risk): same as Brooks — the old code never
    # checked the target against the actual stop distance. Refuse the
    # entry unless reward / actual-risk >= MIN_REWARD_RISK (1.5).
    # The target carried on the signal IS the opposite value edge that
    # _rr_ok() just checked (round-two fix): bot.py submits this price.
    if bars[-1]["h"] > up and last < up and _rr_ok(last, bars[-1]["h"] + a, lo):
        return _signal("dalton_value", -1, last, bars[-1]["h"] + a, None,
                       "opposite_value_edge_flat_close",
                       "failed break above value area, trade to lower edge",
                       target=lo)
    if bars[-1]["l"] < lo and last > lo and _rr_ok(last, bars[-1]["l"] - a, up):
        return _signal("dalton_value", 1, last, bars[-1]["l"] - a, None,
                       "opposite_value_edge_flat_close",
                       "failed break below value area, trade to upper edge",
                       target=up)
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
