# fx-experts: eleven book systems for OANDA forex (practice)

Prepared Fri 2026-10-02 (CT). Labels work the same way as in xp-test:
- **BOOK**: I checked the wording in the author's text, or in a reprint by the author or publisher.
- **SUMMARY**: comes from a reputable secondary restatement.
- **DEFAULT**: our own choice, made where the book gives no number.

I didn't rely on pirated full-text mirrors. Some search results pointed to PDF copies of Lien's and Bollinger's books. I only used them to confirm wording that is also in the legitimate sources cited here.

## Why these five
Marvin asked for mean reversion **and** breakout systems that suit forex. I kept authors who give **mechanical rules with explicit stops**, and preferred authors who wrote for, or tested on, currencies.

| # | System id | Expert / book | Edge type | Why it fits FX |
|---|---|---|---|---|
| 1 | `lien-fader` | **Kathy Lien**, *Day Trading and Swing Trading the Currency Market* (Wiley; 1st ed. *Day Trading the Currency Market* 2005, 3rd ed. 2015), ch. "Technical Trading Strategies – The Fader" | mean reversion (false-breakout fade) | Written *for* FX by a former JPMorgan FX trader. Built on FX's tendency to spend most of its time in ranges. |
| 2 | `raschke-soup` | **Linda Bradford Raschke & Laurence Connors**, *Street Smarts* (1995), ch. 4 "Turtle Soup" | mean reversion (failed 20-day breakout) | Fades exactly the breakouts the Turtles bought. Written for futures, including currency futures. |
| 3 | `faith-turtle` | **Curtis Faith**, *Way of the Turtle* (McGraw-Hill 2007) and his *Original Turtle Trading Rules* | breakout / trend following | The Turtles traded currency futures as a core market group. The rules are fully published. |
| 4 | `bollinger-squeeze` | **John Bollinger**, *Bollinger on Bollinger Bands* (McGraw-Hill 2001), ch. 16 "Method I – Volatility Breakout" | breakout (volatility expansion) | FX has long quiet compressions (holidays, pre-central-bank) followed by expansions. |
| 5 | `crabel-idnr4` | **Toby Crabel**, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (Traders Press 1990) | breakout (contraction → expansion, ORB) | Crabel's statistics included currency futures (Swiss franc, D-mark). The inside-day/NR4 pattern is common in FX. |

Considered and not chosen:
- **Larry Connors RSI(2)**: it is a stock/ETF strategy, and Connors argues *against* stops, which conflicts with "book stops at the broker".
- **Ed Ponsi**, **Boris Schlossberg**, **Jared Martinez**: I could not verify mechanical rules with exact stops from legitimate sources.
- **Lien's Inside-Day Breakout** and **Double Bollinger Bands**: same author as #1; I wanted five different experts.
- **Larry Williams**: rules vary a lot between his editions.

## Common rules (all five systems)
- **Data and clock:** OANDA daily candles aligned to 17:00 New York. Signals come only from *completed* daily candles. Orders for "today" are placed after 17:15 NY, once the rollover spread has settled.
- **Stops and targets live at OANDA:**
  - Stop-entry orders carry `stopLossOnFill` and, where the book has a target, `takeProfitOnFill`.
  - Trailing exits are done by moving the broker stop once a day.
  - Day trades are closed at 16:50 NY.
- **Sizing (DEFAULT):** 0.5% of NAV at risk per trade, per system. Configurable from 0.5% to 1% (`risk_pct`). Margin cap: at most 25% of NAV in margin per position.
- **One trade per pair per system.** When one side of an OCO fills, the other side is cancelled.
- **Spread-aware:** no order is placed or sent while the live spread is more than 2.5× the pair's typical spread. Stop entries carry `priceBound` = level ± 3× typical spread, so a gap through the level cancels the order instead of filling it badly.
- **Pairs:** EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF, NZD/USD, EUR/JPY, GBP/JPY. The same universe for every system; not chosen by backtest.
- **No news filter.** We have no calendar feed; see FOREX_NOTES.
- **US account rules:** FIFO and no hedging. The recommended setup is **one OANDA practice sub-account per system**.

---

## 1. Kathy Lien – The Fader (`lien-fader`)
**BOOK** (strategy rules, reprinted verbatim at TradeWins Daily, "The Forex Fader Strategy"; exa.ai abstract of Lien's chapter, 2015):

Long:
1. "Locate a currency pair whose 14-period ADX is less than 35. Ideally the ADX should also be trending downward."
2. "Wait for the market to break below the previous day's low by at least 15 pips."
3. "Place an entry order to buy 15 pips above the previous day's high."
4. "After getting filled, place your initial stop no more than 30 pips away."
5. "Take profit on the position when prices increase by double your risk, or 60 pips."

Short is the mirror image. The chapter adds: "It uses the daily charts to identify the range-bound environment and the hourly charts to pinpoint entry levels." It also says it works best without major data releases.

**Ours:**
- **Filter:** daily ADX(14) < 35. The "ideally falling" part is optional and not coded.
- **Arm:** price trades 15 pips beyond the prior day's low (or high).
- **Entry:** stop order 15 pips beyond the opposite prior-day extreme.
- **Stop and target:** stop exactly 30 pips from the fill (`stopLossOnFill.distance`); target 60 pips (2R).
- **Timeframe and validity:** daily setup, intraday stop entry, order good for the day (GTD 17:00 NY, DEFAULT). The trade is held until stop or target; the book has no time exit.

## 2. Raschke & Connors – Turtle Soup (`raschke-soup`)
**BOOK** (Street Smarts ch. 4 rules as reprinted by TradingMarkets/Connors Group, 2003):
1. "The futures contract must make a new 20-day low."
2. "The previous 20-day low must have occurred at least four trading sessions earlier."
3. "After the market falls below the previous 20-day low, place an entry buy stop 5-10 ticks above the prior 20-day low. This buy stop will be good for today only."
4. "If the trade is triggered, place an initial good-until-cancelled sell stop one tick under today's low."

Also from the book:
- The chapter text: trail the stop as the trade becomes profitable. If stopped out on day 1 or 2, re-enter at the original entry level.
- The Turtle Soup Plus One text: "take profits within two to six days and trail stops."

**Ours:**
- **Entry:** buy stop 5 pips above the prior 20-day low (1 tick = 1 pip, the historical currency-futures tick; 5 is the book's lower bound). Armed only after price trades below that low. Shorts mirror.
- **Stop:** 1 pip under today's low, kept current until the fill.
- **Trail:** move the stop to the prior day's low − 1 pip at each close (DEFAULT reading of "trail").
- **Time exit:** flat at the close of day 6 (book's "two to six days").
- **Re-entry:** one re-entry if stopped on day 1 or 2 (BOOK).
- **No fixed take-profit:** the book has none; the exit is the trail plus the 6-day limit.

## 3. Curtis Faith – Turtle System 1 (`faith-turtle`)
**BOOK** (*Original Turtle Trading Rules*, Curtis Faith, the same rules as in *Way of the Turtle*):
- **Entry:** a breakout above or below the 20-day high or low by 1 tick.
- **Skip filter:** skip the signal "if the last breakout would have resulted in a winning trade." A breakout "would be considered a losing breakout if the price subsequent to the date of the breakout moved 2N against the position before a profitable 10-day exit occurred."
- **Failsafe:** if a signal was skipped, take the 55-day breakout instead.
- **Stop:** 2N (N = (19×PDN + TR)/20).
- **Exit:** "a 10 day low for long positions and a 10 day high for short positions."

**Ours:**
- Exactly the above. Hypothetical breakouts are tracked so the skip filter works.
- The stop at OANDA = the tighter of entry ± 2N and the 10-day extreme ∓ 1 pip, updated each day.
- **No take-profit:** the 10-day exit is the book's profit exit.
- **Deviation (DEFAULT):** one unit only. No pyramiding (the book adds up to 4 units every ½N). Sized at 0.5% risk rather than the Turtles' "1 N = 1% of equity" unit.

## 4. John Bollinger – Method I Squeeze (`bollinger-squeeze`)
**BOOK/SUMMARY** (Bollinger on Bollinger Bands ch. 15-16, as quoted by StockCharts ChartSchool, Investopedia "How to Profit from the Bollinger Squeeze", Schwab, TradingSetupsReview):
- A Squeeze is when BandWidth (20-period, 2 sd) reaches its lowest level in six months (about 125 bars).
- "Just wait for a Squeeze and go with the first breakout."
- Exit: "the initial stop is set just below the range of the breakout formation and then incremented upward each day the trade is open" (Wilder's Parabolic). The alternative for larger moves is "a tag of the opposite band".
- Beware head fakes. Volume indicators (II, AD, MFI) help.

**Ours:**
- **Setup:** a squeeze reading within the last 20 sessions (DEFAULT). Then the *first* close outside a band.
- **Entry:** market at the next day's open (after 17:15 NY).
- **Stop:** starts just beyond the formation's extreme (squeeze bar to breakout bar, at least 5 bars, ±1 pip, DEFAULT). It then follows Parabolic SAR 0.02/0.02/0.20, updated daily at OANDA.
- **No take-profit:** none in the book.
- **Not used:** volume confirmation (FX has no central volume) and discretionary head-fake trading.

## 5. Toby Crabel – ID/NR4 opening-range breakout (`crabel-idnr4`)
**BOOK/SUMMARY** (S&C V7:2 / V7:4 abstracts; book summaries on DaytradingBias and TradeLossTracker; pattern definitions as quoted from the 1990 book; same sources as xp-test's EXPERT_BOOKS.md):
- An ID/NR4 is an inside day that is also the narrowest range of the last 4 days. After an NR4 or inside day, ORBs "tend to coincide with trend day activity."
- **Entry:** stop orders a "stretch" above and below the open. Stretch = 10-day average of min(high − open, open − low).
- **Stop:** the opposite stop is the protective stop.
- **Exit:** on the close.

**Ours:**
- **Setup:** the day after an ID/NR4.
- **Orders:** OCO buy stop at open + stretch / sell stop at open − stretch. Open = 17:00 NY daily open.
- **Stop:** the other side.
- **Exit:** flat at 16:50 NY.
- **No take-profit:** none in the book.
- **Not used:** stop-and-reverse.

## Data used for the backtests
- **OANDA H4 mid candles** (from the VM, `~/bots/data_h4_extended`): 9 pairs, 2016-01-04 to 2026-09-07. These are real OANDA prices.
- **OANDA H1 candles** (`~/bots/oanda_data/h1`): 2025-11 to 2026-09 only. Used to check the intrabar path model.
- **Dukascopy H1 bid/ask** (public datafeed): 2016-01 to 2026-09, true bid/ask mid. This is the **main** dataset, because the H4 path model proved unreliable for the two tight-stop mean-reversion systems (reports/H1_VALIDATION.md). Dukascopy M5 for EUR/USD, GBP/USD and EUR/JPY 2024 was used for a second check (reports/M5_VALIDATION.md).

There was no working OANDA token (401 on all VM tokens), so I could not pull fresh candles from the practice API.

---

# Six more book systems (added 2026-10-02), forward test only

Marvin's direction: run all of them independently on the practice account and judge them on live
(practice) results — **no backtests were run for these six**. Unit tests cover the rules (71 tests total).
As of 2026-10-02 **all 11 systems are ON** and **risk is 0.25% of NAV per trade for every system**
(down from 0.5%; `config/config.json` `risk_pct`).

Shared mechanics: one trade per system per pair; every entry carries its stop (and target, where the
book has one) at OANDA on fill (`stopLossOnFill`/`takeProfitOnFill`); trades are tagged with the system
id (clientExtensions comment) for the ledger and the 4:20 PM CT Telegram scorecard; same-pair guard
(a pair held by any other system/bot is skipped). Intraday systems read OANDA M5 candles (mid, tick
volume) and resample them. "DEFAULT" = our numeric choice where the book is not specific.

| id | expert / book | entry | stop | take-profit | exit otherwise |
|---|---|---|---|---|---|
| `lien-zeros` | Kathy Lien (BKForex w/ Boris Schlossberg), *Day Trading & Swing Trading the Currency Market*, "Fading the Double Zeros" | 15-min close ≥10 pips beyond its 20-SMA (DEFAULT for "well below/above") and a "00" figure 3–30 pips away → LIMIT 5 pips beyond the figure (book: ≤10), valid 60 min | 20 pips (book: "no more than 20") | 2R = 40 pips (book's first target) | — |
| `williams-vbo` | Larry Williams, *Long-Term Secrets to Short-Term Trading*, ch. 4 | buy/sell STOP at daily open ± 60% of yesterday's range (book: "a portion"; 60% is DEFAULT); first fill cancels the other | 50% of yesterday's range (book) | none (book has none) | "bailout": close at the first profitable daily open (book) |
| `ponsi-trend` | Ed Ponsi, *Forex Patterns and Probabilities*, trend strategy | daily EMAs 10>20>50>200 and last 10 closes above the 10-EMA → LIMIT at the 10-EMA (mirror for shorts) | 10-EMA − ½ ATR(14), trailed daily, never loosened (book; ATR period DEFAULT) | none (book: partial exit at 1R; single ticket here) | stop to breakeven after a ≥1R daily close (book step without the partial) |
| `brooks-range-fade` | Al Brooks, *Trading Price Action: Trading Ranges* | H1 range (prior 24 bars: width ≤8× avg bar, flat 20-EMA, ≥4 EMA crosses — all DEFAULT); bar pokes beyond range high/low, closes back inside as a reversal bar → STOP 1 pip beyond the signal bar, valid 60 min | 1 pip beyond the signal bar | range midpoint, only if reward ≥ risk | flat before 17:00 NY (day trade) |
| `tharp-random` | Van K. Tharp, *Trade Your Way to Financial Freedom*, random-entry test (with Tom Basso) | coin flip (hash of pair+date), market order, always in | 3× 10-day EMA of true range, trailed daily from the close, only tightens (book) | none (book has none) | re-enter next day after a stop-out |
| `dalton-value` | James Dalton, *Mind Over Markets*, Value-Area Rule (p.279) + value-area calc (App. 1) | previous FX day's volume profile (M5 tick volume, 1-pip bins, 70%); today opened outside value and two consecutive 30-min periods closed inside it (before 12:00 NY) → market order toward the far edge | 1 pip beyond today's extreme on the failed side (DEFAULT); skip if risk > reward | opposite value-area edge | flat before 17:00 NY |

Sources / uncertainty flags:
* **Lien**: rules quoted from the book chapter (reprinted on forexstrategiesresources.com; Investopedia
  cites ch. 10 of the 2015 edition). *Millionaire Traders* (Lien & Schlossberg) is an interview book and
  *High Probability Trading Strategies* rules could not be verified, so the documented "Double Zeros"
  setup was used instead of a London-open breakout. The book trails half the trade; we take the whole
  ticket off at the 2R first target.
* **Williams**: stop and bailout quoted from p.61-62 (via tradingresearchub.com quote); the breakout
  percentage is not fixed in the book — 60% is our choice. He also used day-of-week filters; not used.
* **Ponsi**: rules from secondary summaries of the book (thepatternsite.com review, desiretotrade.com),
  not checked against the page text; ATR period assumed 14.
* **Brooks**: the book is discretionary; principles are his, every number is ours. Lowest fidelity of the six.
* **Tharp**: rules widely reproduced (backtrader replication, the7circles notes). Deviations: 0.25%
  risk instead of 1%; run on six crosses (EUR_GBP, EUR_CHF, AUD_JPY, CAD_JPY, AUD_NZD, EUR_AUD) that no
  other system trades, so an always-in system cannot block the other ten on this FIFO account.
* **Dalton**: Value-Area Rule and value-area calculation from the book (O'Reilly appendix; teamlbr quote
  of p.279-280). Dalton says the rule alone is "little better than a flip of a coin" without context;
  no context filter is coded. FX has no exchange session; we use the 17:00-17:00 NY day and tick volume.

Same-pair guard / blocked entries (rough estimate, not measured): ten systems share the nine majors in
one US FIFO account. The longest holders are the trend systems (Turtle, Ponsi, Bollinger) which can sit
in a pair for weeks. Expect roughly **15–30% of signals to be skipped** because another system already
holds that pair, with the intraday systems (Lien zeros, Brooks, Dalton) and Crabel/Williams losing the
most. Tharp is unaffected (own pairs). Skips show in `logs/runner.log` as "pair held by another system/bot".

Switch any system: `cd /opt/fx-experts && venv/bin/python -m fxexp.switch off <id>` (or `on`).

Account-level safety (added with the six): the runner stops placing new entries while OANDA
`marginUsed` exceeds 50% of NAV (`max_margin_used` in config); open trades keep their OANDA stops.
