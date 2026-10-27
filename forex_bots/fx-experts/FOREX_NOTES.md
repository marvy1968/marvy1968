# Forex notes that matter for these systems

General market facts come from standard references: the BIS Triennial Survey 2022, OANDA and CME education pages, and Kathy Lien's book (chapters on sessions and pairs). Numbers marked **measured** I worked out myself from the OANDA candles on the VM (2016-01 to 2026-09) or from Dukascopy H1 data.

## Sessions (New York time; CT is one hour earlier)
| Session | NY time (approx.) | Character |
|---|---|---|
| Sydney/Tokyo (Asia) | 17:00/19:00 – 03:00/04:00 | Quietest for EUR, GBP, CHF and CAD. JPY, AUD and NZD are active. Ranges are often *set* here and then tested in London. |
| London | 03:00 – 12:00 | Largest share of turnover (BIS 2022: UK about 38%). Most daily highs and lows are made here. Breakouts of the Asian range start here. |
| New York | 08:00 – 17:00 | US data comes out at 08:30 NY. London/NY overlap 08:00–12:00 is the most liquid window. Liquidity dries up after the London close (about 11:00–12:00). |
| Rollover | 16:55 – 17:15 | Daily candle boundary, financing booked, **spreads widen several-fold**. Weekend: market closes Fri 17:00 and reopens Sun about 17:00. |

**Measured:** GBP/USD average hourly range (2024-26, Dukascopy H1, by NY hour):
- 9-11 pips from 16:00 to 01:00
- 17-21 pips in early London (02:00-07:00)
- 24-28 pips at 08:00-10:00 (the overlap)
- Tapering to about 12 pips by 15:00

How this shapes the systems:
- Every entry is a resting **stop order at OANDA**, so fills happen whenever the price gets there, in any session.
- The runner never places orders during the rollover. It waits until 17:15 NY.
- Crabel's "open" is the 17:00 NY daily open. That is a thin, wide-spread moment, so it is a known weak point of moving an equity-futures ORB to FX.

## Spreads and costs
Typical OANDA US spreads in liquid hours (what the backtest uses, deliberately a bit wide):

| Pair | Spread (pips) |
|---|---|
| EUR/USD | 1.4 |
| USD/JPY | 1.4 |
| AUD/USD | 1.4 |
| USD/CHF | 1.8 |
| GBP/USD | 2.0 |
| NZD/USD | 2.0 |
| EUR/JPY | 2.0 |
| USD/CAD | 2.2 |
| GBP/JPY | 3.0 |

- Spreads are 2-10× wider at rollover, at the Sunday open, and in the seconds around NFP, CPI, FOMC and central-bank decisions.
- Spread as a fraction of the stop is what matters. A 30-pip stop on GBP/JPY pays about 10% of its risk in spread alone. A 150-pip Turtle stop pays about 1-2%.

## Pips and pip values
- A pip is 0.0001, or 0.01 for JPY pairs. OANDA quotes to 1/10 pip.
- For a USD account, 10,000 units of a XXX/USD pair = $1 per pip.
- For USD/XXX and crosses the dollar value moves with the rate: pip $ = units × pip ÷ (USD/quote rate).
- The sizing code (`fxexp/sizing.py`) converts every pair to USD risk.

## Majors vs crosses, trending vs mean-reverting (measured, 2016-2026 daily)
| Pair | ADR (pips, 2024-26) | 5-day variance ratio | Note |
|---|---|---|---|
| EUR/USD | 74 | 0.97 | slight mean reversion |
| GBP/USD | 89 | 1.03 | slight trending |
| USD/JPY | 128 | 0.97 | |
| AUD/USD | 58 | 0.99 | |
| USD/CAD | 69 | 0.95 | mean-reverting |
| USD/CHF | 61 | 1.01 | |
| NZD/USD | 54 | 1.00 | |
| EUR/JPY | 126 | **0.91** | most mean-reverting |
| GBP/JPY | 158 | **1.04** | most trending / widest |

Variance ratio below 1 means mean reversion; above 1 means trending.

All ratios are close to 1. Daily FX is close to a random walk, so no system should be expected to have a big edge after costs. Lien's fixed 15/30/60-pip rules were written when EUR/USD ranged about 100 pips a day. Today it is about 74, while GBP/JPY is about 158 and USD/JPY about 128. The same pip numbers are relatively tight on the JPY crosses.

These ratios are **in-sample observations**. I did **not** use them to pick pairs per system, because that would be fitting to the backtest.

## News risk
- The biggest scheduled movers:
  - US NFP (first Friday, 08:30 NY)
  - CPI
  - FOMC (14:00 NY)
  - ECB, BoE and BoJ decisions
  - Canada jobs (with NFP)
  - RBA and RBNZ
- Lien says the Fader "works best when there are no significant economic reports scheduled". We have **no economic-calendar feed**, so that filter is *not* applied. Expect some false-breakout fades to be run over by news.
- Stops are broker-side, so news gaps cause slippage, not unprotected positions. OANDA STOP entries use `priceBound` (3× typical spread) to refuse very bad fills.

## Weekend gaps and holidays
- Prices can gap at the Sunday open. Stops fill at the first available price, so a loss can exceed 1R. The backtest models this: if a bar opens beyond a level, the fill is at the open.
- Day trades (Crabel) are flat by 16:50 NY, so they never hold over a weekend. Swing systems (Turtle, Bollinger, Raschke up to 6 days, Fader until stop or target) can hold over weekends, as the books allow.
- Christmas and New Year trade thin with short sessions. Candle builders drop single-bar fragments.

## Rollover and financing
- Positions held through 17:00 NY earn or pay the interest differential minus the broker's markup. Wednesday's rollover counts three days.
- With today's rate differentials, carry is positive on one side of most USD pairs and negative on the other. The backtest charges a flat conservative 0.3 pip per night, whichever side you're on.
- Only the Turtle (multi-week holds) is materially affected.
