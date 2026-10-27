# fx-experts backtest summary (honest, untuned)

Prepared Fri 2026-10-02 (CT). Book rules only. No parameter was changed after seeing results, and the pair list was fixed in advance.

## Main run
- **Data:** Dukascopy H1 bid/ask mid, 2016-01-04 to 2026-09-29, 9 pairs. Daily candles are built at 17:00 NY, the same way OANDA builds them.
- **Costs:** OANDA-typical spread, 0.3 pip slippage on stop and market fills, ×2 spread at the 17:00 open, 0.3 pip/night financing.

| System (expert) | Trades | Win | PF | Net R | MaxDD R | Net % / MaxDD % at 0.5% risk |
|---|---|---|---|---|---|---|
| lien-fader (Lien) | 611 | 37.8% | 1.18 | +71.1 | 41.7 | +35.6% / 20.9% |
| raschke-soup (Raschke/Connors) | 1941 | 21.1% | 1.02 | +34.8 | 83.0 | +17.4% / 41.5% |
| faith-turtle (Faith) | 941 | 31.6% | 0.72 | −128.0 | 131.4 | −64.0% / 65.7% |
| bollinger-squeeze (Bollinger) | 179 | 33.0% | 0.86 | −10.4 | 20.3 | −5.2% / 10.1% |
| crabel-idnr4 (Crabel) | 2122 | 42.0% | 0.96 | −39.1 | 67.7 | −19.5% / 33.9% |
| all five together | 5794 | 32.6% | 0.98 | −71.5 | 156.9 | −35.8% / 78.5% |

Per-pair and per-year tables are in BACKTEST_duka_h1.md.

## Cross-checks
- **ZERO_COST.md** (gross, before costs):
  - Fader PF 1.34, Soup PF 1.27, Crabel PF 1.13, Bollinger PF 0.96, Turtle PF 0.81.
  - Costs turn Soup and Crabel from clearly positive to roughly flat or negative, because their stops are small.
  - Turtle loses even before costs over 2016-2026. Trend following in FX has had a poor decade.
- **H1_VALIDATION.md** (OANDA H4 vs OANDA H1, last 10 months):
  - On H4 bars, Soup and Fader look much better than they really are; the H4 intrabar ordering flatters tight stops.
  - The OANDA-H4 full run (BACKTEST_oanda_h4.md: Soup +471R, Fader +126R) is therefore **not trustworthy for those two**.
  - For Turtle, Bollinger and Crabel, H4 and H1 gave identical results.
- **M5_VALIDATION.md** (H1 vs M5, EUR/USD 2024, GBP/USD and EUR/JPY 2024 Q1-Q3): finer bars again lower Fader (+10.8 → +4.8 R) and Soup (−7.0 → −13.3 R). The H1 numbers for these two are probably still somewhat optimistic.

## Plain-English read
- **Fader (Lien)** is the only system positive in every check. Its edge shrinks as the data gets finer. Realistic expectation: small positive at best.
  - Best pairs: EUR/USD, EUR/JPY, GBP/JPY, USD/CHF, USD/CAD.
  - Losing pairs: AUD/USD, NZD/USD.
- **Turtle Soup (Raschke)** is about breakeven after costs, with deep drawdowns, and is sensitive to intrabar detail. Unproven.
- **Turtle (Faith)** is the clear loser: −128R over 10 years, every pair except USD/JPY negative.
- **Bollinger squeeze** is slightly negative on few trades (about 17/yr).
- **Crabel ID/NR4** is slightly negative after costs.
- **All five together** is slightly negative.

Paper forward-testing is the honest next step. Nothing here justifies live money.
