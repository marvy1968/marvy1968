# Backtest and forward-test findings: everything we tested (for SuperGrok)

Marvin's Alpaca stock bots and OANDA forex bots, October 12 to November 6, 2026.

**How to read this file**
- **PF** = profit factor (gross wins ÷ gross losses; above 1 makes money).
- **R** = multiples of the risk taken on a trade.
- **"Halves" / "train / validate"** = the test period split by date; the second half is data the rule never saw.
- **"2x costs"** = the test re-run with spreads or slippage doubled.
- **"Live"** = the paper or practice-account record, which is the forward test.
- **Stock costs** are 10 bps a side unless noted. **Forex** uses real bid/ask prices.

---

## A. Stocks: daily swing trading (the only place an edge showed up)

| What | Test | Result | Verdict |
|---|---|---|---|
| **Sector Top-Down**: `close > EMA20 > SMA50`, 20-day return ≥ SPY's + 3 pts, SPY above its 200-day; stop EMA20 − 1 ATR; exit on a close below EMA20 | 2016–2025, 55 stocks + the hindsight-free 2016 list | Long-run PF ≈ 1.54, 9 of 11 years profitable | **Real but modest edge** |
| Top-Down + **fixed 2R take-profit** (instead of its own exit) | Long test, unseen half, 2x slippage | Today's list 206 trades PF **1.50**, +58.9R (vs 327 trades PF 1.22, +25.6R). 2016 list 73 trades PF **2.16**, +39.2R (vs 1.35, +15.4R). Beats random entries on both lists | **PASSED, the best result we have** |
| Top-Down + 2R, short test | Last 3 months, halves | PF 1.25 (1.08 / 1.78) vs 0.77 for its own exit | Passed |
| Top-Down, recent stretch | Last 3 months (Aug 5 – Nov 5, 2026) | Today's list PF 0.55 / 0.59, 2016 list 1.49 then 0.46. An earlier 3-month window: 162 trades PF 0.58, −34.7R | **Weak recently.** Long-run edge, bad current regime |
| Top-Down sizing: 0.5% × 5 slots (base) vs 0.5% × 8, 0.75% × 5, 0.75% × 8 | Last 3 months, both lists | Every larger setting did worse | **Failed: don't size up** |
| SPY regime filter: longs only above a rising SMA20 | Top-Down 2R, long test | OOS PF 1.51 → 1.46 (today's list), 2.17 → 1.96 (2016 list), lower net R | **Failed** |
| Top-Down **short side** (mirrored rules, Weinstein stage 4, weak market) | 2016–2022 | Long + short worse than long only in every row | **Failed** |
| Top-Down short on SPY-down days only | Long test | Failed the pre-registered bar | **Failed** |
| **Oversold confluence** swing (3 of 4 oversold readings, 1.25R target) | 2016 – mid-2025 | Today's list PF **0.56** over 2,877 trades (9 of 11 years losing); 2016 list PF 0.48. A 3-month backtest had shown PF 1.55, a false positive | **Failed** |
| Oversold entered at 10:00 instead of the open | 3 months | PF 1.27 → 1.57 (but the base strategy fails long-term) | Not useful |
| **Stock Funnel** (stage 2, 3 of 4 votes, 1.5R) | Last 3 months + live | 33 trades PF 0.90, −1.3R. Live 4 trades, −$417 | **Failed** |
| **Weekly beat-SPY picks** (relative-return model, 5-day hold, OCO) | 1-year walk-forward | 226 trades PF 1.28, +30.8R. Live: 4 trades, 0 wins, −$148 | Mixed: backtest OK, live poor |
| Weekly picks take-profit 0.5% lower | Pre-registered | Did not beat the base | Failed |
| **Stair-step** (Grok variant A) | Holdout | PF 1.04 on 18 longs (0.61 both sides) | **Failed the bar** |
| SPY/QQQ 200-day index sleeve | Live paper | Paused after losses | Not proven |
| Swing book overall (all strategies) | Live paper | 32 closed trades, 11 wins / 21 losses, −$1,826 | Losing |

## B. Stocks: intraday (nothing worked)

| What | Test | Result | Verdict |
|---|---|---|---|
| **"Brain"** multi-expert intraday bot (RSI2, VWAP deviation, Bollinger, Williams %R, Brooks pullback, ORB, dual regime, Raschke, Velez, Turtle) | Live paper Oct 12–26 (audit) | 306 trades, PF **0.74**, −39.5R. Longs −50.2R, shorts +10.7R | **Failed** |
| Each expert alone, live filters | 3 months, halves | velez_180 PF 0.42 / 0.38, williams_r 0.46 / 0.60, dual_regime 0.43 / 0.38, connors_rsi2 0.30 / 0.81 | All losing |
| **Shorts only** (the 6 best short experts) | 2 weeks, halves, 2x costs, random control | 56 trades PF 1.27 (week 1 **0.80**, week 2 2.23), PF **0.97** at 2x costs, avg R **+0.05 vs +0.16 for random-timing shorts** | **Failed: the falling market did the work, not the experts** |
| Velez setups (halt, VBS, red-bar-ignored, 20/200) | 3 months + live | rbi validate PF 0.76 (0.97 with next-bar confirmation), halt 0.27. Live: rbi 12 trades PF 0.01, halt 13 trades PF 0.36 | **Failed** |
| Velez hybrid filters on every expert (trade with the 20/200, look left) | 3 months | Did not lift the Brain above PF 1 | Failed |
| VWAP reclaim / VWAP band / opening-range breakout sleeve | 5 months + live log | Lost. ORB removed | **Failed** |
| **Optimized Trend** (5-minute trend sleeve) | 3 months, halves | PF ≈ 0.40–0.44 | **Failed** |
| SPY-vs-today's-open filter (above the open → longs only, below → shorts only) | Pre-registered, halves | Trend halves 0.32 / 0.55 → 0.51 / 0.36 (flipped); VWAP reclaim / band worse | **Failed** |
| Range box / Fibonacci box / hybrid zones (fade the box) | Tests + live | The fade side lost in every test | **Failed** |
| Box breakout with candle + volume + volatility confirmation | Tests | Not enough to lift intraday above PF 1 | Failed |
| Exits: owner's ladder (cut 25 / 50%, TP 25 / 50%, trail) and profit lock (0.75R trail) | 1 month, halves | Validate PF 0.77 → 0.84: less bad, still losing | Guard only |
| Minute-bar exit lab (VWAP, Velez): 14 classic + 7 Muse exits | Pre-registered | None passed | **Failed** |
| 15:00–15:30 ET entries | Audit | −0.30R on 28 trades | Thin sample, hypothesis only |

## C. Forex (OANDA, hourly / 4-hour, bid/ask)

| What | Test | Result | Verdict |
|---|---|---|---|
| **Lien fader** (lien_h1, break 20 pips) | 27-point grid, 2016–20 / 2021–22 | PF 1.46 / 1.27 but only 85% vs the 90% placebo bar. 12 months PF 2.75, 3 years 0.94 (flat). Owner's 10-year test PF 1.18 | **Provisional**, still trading |
| **Break & retest playbook** (retest_h1: 4-hour zones, retest + pin / engulfing, stop-entry, ≥ 2R target) | 3 years | 91 trades, 27% win, PF **1.62**, +36.8R without a break-even move (PF 1.27 with one) | **Best forex result**, still trading (no live fills yet) |
| Plain range-box fade (box_h1) | 2016–22 + live | PF 0.68 on 1,308 trades. Live **0 of 5**, −$2,217 | **Failed** |
| Range mean-reversion in the box (boxmr_h1) | 5 years H1 + live + autopsy | 292 trades PF **0.51**. Every one of 22 exits lost out of sample (best 0.89). The fade was PF 0.98, so reversing it doesn't help. No loser filter found. Live 6 trades PF 0.46 | **Failed** |
| Candlestick hybrid on range experts | Pre-registered | Failed | Failed |
| Box breakout continuation (confirm_h1) | 2016–22 (before costs) then 12 months / 3 years | PF 1.54 before costs, then 0.79 / 0.80 with live exits and costs | **Failed** |
| Hybrid zones (hybrid_h1) | 12 months / 3 years | PF 0.69 / 0.69 | Failed |
| Oversold confluence D1 / H4 | 12 months / 3 years | D1 PF 0.58 / 0.70; H4 2 weeks 0.57, 3 months 0.76 | Failed |
| Pristine "Changing of the Guard" H4 (cog_h4) | 2 weeks / 3 months, then 12 months / 3 years | PF 6.06 (15 trades) and 1.52 (89) looked great, then 0.70 / 0.71 (−30% / −66%) | **Failed: a short-window mirage** |
| 7 trend experts (Holy Grail, TTM, MACD-200, inside bar, WVB …) | Last 92 days | 1,090 trades PF **0.30**, −690R at 2x spread | **Failed** |
| SMA200 ± 2σ envelope instead of the 100-bar box | 2016–20 | PF 0.52 vs 0.60 | Failed |
| Tight cuts: out at −0.10R / +0.30R | 12 months | −28.5% (noise stops nearly every trade). 0.25R / 0.75R −24.2%; 0.50R / 1.50R −9.5% (old ladder −14.8%) | Tight stops destroy forex |
| Muse / classic exit list on boxmr / lien | Pre-registered | None passed | Failed |
| Forex runner overall | Live practice since Oct 14 | 19 trades, −$2,693 (box_h1 −$2,217, boxmr −$412, cog −$63, hybrid +$32) | Losses came from strategies now off |

## D. Risk guards (protection, not edge, so none were "backtested" as profit makers)
- **Broker-side stop on every position** (an OCO with the target). Keep it, and re-check it at startup and every few minutes.
- **Account −1% day → no new entries**, bot-level −2% breaker, kill switch. Keep these.
- **1% notional cap per intraday position.** Keep it. A wider ATR stop with fixed dollar risk runs into this cap.
- **10-minute under-water exit, 10-minute same-expert cooldown, 3:50 PM flatten.** Intraday-only guards.
- **Drawdown size-down steps.** Not recommended; in August 2007 this kind of rule locked in quant funds' losses.
- **News gates** (48-hour SPY/QQQ headline score, per-stock headlines) and the Seeking Alpha log. **Not backtested** (no history), so they're optional.

## E. Lessons about testing (the most valuable part)
1. **Short good windows lie.** cog_h4 looked like PF 6.06 over 2 weeks, then lost over 3 years. Oversold swing looked like PF 1.55 over 3 months, then 0.56 over 9 years. **A short test only says "worth a longer test".**
2. **Always compare with random-timing entries.** The shorts-only Brain looked profitable until random entries beat it.
3. **Always re-run with costs doubled.** Many strategies flip below PF 1.
4. **Fix the pass rule before seeing any result, change one thing at a time,** and don't re-tune on the test data.
5. **Never filter on what happens after entry** (hold time, how far a trade ran, "trades held > 30 min win"). You only know those after the trade closes.
6. **Validate stocks on a hindsight-free list** (e.g. the 2016 sector leaders), not only today's winners.
7. **Intraday costs and noise kill small edges.** Every intraday stock system and every tight-stop forex rule lost.
8. **Simple beats complex.** A fixed 2R target beat every trailing, partial, break-even and classic exit we tried.
9. **Even the best edge is modest and cyclical** (Top-Down PF ≈ 1.5 long-run, about 0.55 over the last 3 months). 1%/day or a 62% win rate was never reached in any test.

## F. What to build first (summary)
1. Top-Down swing on daily bars, long only, SPY 200-day gate, 2R take-profit plus stop as an OCO, 0.5% risk, max 5 positions.
2. For forex: retest_h1 (break & retest) and optionally lien_h1 at small risk, with their own exits.
3. Everything else stays off until it passes section E.
