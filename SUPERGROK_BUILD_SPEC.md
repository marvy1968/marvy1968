# Build spec: swing trend bot (for SuperGrok)

Marvin's paper-trading bot. It uses what our tests proved and leaves out what they disproved.

## 1. Scope
- **Account:** Alpaca **paper** account only. Long only. Daily bars. Positions are held for days or weeks.
- **Universe:** 55 large-cap US stocks, the 5 largest S&P 500 names in each of the 11 sectors.
  - Benchmark: SPY.
  - Store a fixed "hindsight-free" list too: the same rule, applied to the 2016 sector leaders. Validate on it as well as on today's list.
- **Schedule:**
  - Scan after the close (about 16:20 ET) on completed daily bars only.
  - Place orders at the next open (about 09:20 ET).
- **No intraday trading.** Every intraday system we tested lost after costs.

## 2. Entry (the "Top-Down" rules, tested since 2016)
All of these must be true on the completed daily bar:
- `close > EMA20 > SMA50`
- The stock's 20-day return beats SPY's 20-day return by at least **3 points**.
- **SPY gate:** SPY's close is above its 200-day SMA. When it isn't, take no new longs.

When there are more signals than free slots, rank them by that 20-day relative strength, strongest first.

## 3. Exits (one bracket at the broker, placed with the entry)
- **Stop:** EMA20 − 1 × ATR14 at the signal bar, as a fixed price. Risk per share = fill − stop.
- **Take-profit:** fill + **2R**, meaning 2 × (fill − stop). This is the only exit change that passed on unseen data.

  | Stock list | PF with 2R | PF with old exit |
  |---|---|---|
  | Today's list | 1.50 | 1.22 |
  | 2016 list | 2.16 | 1.35 |

- **Signal exit:** a daily close below EMA20 means sell at the next open.
- **Don't add** trailing stops, partial take-profits, break-even moves, or Turtle / Raschke / Ponsi / weekly-stair exits. All were tested and none beat the plain 2R.

## 4. Sizing and risk
- **Per trade:** risk **0.5% of equity**. Max **5** positions. Max **1/3 of equity** in any one position. Never exceed the cash on hand.
- **Don't size up.** In the last 3 months, 0.75% risk and 8 slots were worse than this in every test.
- **Safety rails:**
  - The stop and take-profit live **at the broker** (an OCO order) from the moment of the fill.
  - At startup and every few minutes, compare positions with the broker. Any position without a stop gets a new stop at once, and the bot checks that the stop exists.
  - Stop new entries for the day after a −1% account day.
  - Keep a kill switch: a file or flag that blocks all new entries.

## 5. Do NOT build (tested and failed)
- **Intraday multi-expert bots** (VWAP, opening-range breakout, Velez setups, RSI2/Bollinger day trades). The Brain ran 306 trades at PF 0.74 (−39.5R).
- **"Shorts only" intraday.** It lost to random-timing shorts (+0.05R vs +0.16R a trade).
- **S&P filters beyond the 200-day gate:**
  - SPY's 20-day trend regime lowered PF (1.51 → 1.46, and 2.17 → 1.96 on the 2016 list).
  - SPY vs today's open also hurt.
- **Top-Down short side.** It failed every test.
- **Range / mean-reversion forex strategies.** box_h1: 0 wins in 5 live trades. boxmr_h1: PF 0.51 over 5 years.

## 6. Testing rules (mandatory before any rule change goes live)
1. **Write the pass rule before seeing any result.** One change at a time.
2. **Split the period into two halves by date.** The change must not be worse on the second, unseen half.
3. **Re-run with costs doubled** (use 10 bps a side as the base). PF must stay ≥ 1.0.
4. **Beat a random-timing control:** the same number of entries, at random times.
5. **Need 30+ trades.** Start with a short test (2 weeks to 3 months). Run a longer test only if the short one passes.
6. **Never filter on what happens after entry**, such as hold time, how far a trade ran, or "day holds win". You only know those after the trade closes.

## 7. Honest expectations
- **Top-Down's long-run edge is modest:** PF about 1.5 over 2016–2025, profitable in 9 of 11 years.
- **The last 3 months were losing:** PF about 0.55 to 0.6.
- Judge the bot over months, not days. Paper trading is the test.
- 1%/day or a 62% win rate has never been reached in any test. Treat those as targets, not expectations.
