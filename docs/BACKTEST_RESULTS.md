# Marv Predict Max - backtest results summary (2026-10-09)

Full write-up: ../ANALYSIS.md. PDF copy: MARV_BACKTEST_RESULTS.pdf.

# Summary table (walk-forward, seasons the models did not train on)

| Sport | Test seasons | All games | Experts agree | 80%+ confidence | 85%+ confidence |
|---|---|---|---|---|---|
| NCAA men's | 2023-25 | 73.9% | 78.0% | 90.8% (~2,300/yr) | 93.9% (~1,700/yr) |
| NCAA women's | 2023-25 | 77.4% | 81.9% | 91.5% (~2,700/yr) | 94.2% (~2,150/yr) |
| WNBA | 2023-25 | 66.6% | 70.0% | 84.1% (~38/yr) | 87.2% (~16/yr) |
| NBA | 2024-26 | 66.8% | 70.0% | 84.0% (~225/yr) | 87.2% (~104/yr) |
| NFL | 2023-25 | 64.8% | 68.1% | 79.5% (~26/yr) | 95.5% (22 picks) |
| College football | 2023-25 | 77.0% | 78.8% | 88.3% (~134/yr) | 91.9% (~102/yr) |

College basketball rows include lopsided games against lower-division teams; the D1-vs-D1-only figures were about 90.3% (men's) and 90.8% (women's). These are mostly heavy favorites, so profit after the vig is about break-even where lines exist.

# Market vs model (NFL 2018-25, n=2,219; college football 2018-25, n=2,336)

| Test | Result |
|---|---|
| Model beyond the closing spread (stacked logistic) | No. NFL model coefficient 0.00-0.01 vs market 0.14; college football 0.00 vs 0.12 |
| Brier score NFL | market 0.2108, stack 0.2109, model alone 0.2204 |
| Totals: model beyond the line | No. Top-20% edge hit 50.2% (NFL), 50.4% (college football) |
| NFL top 400 moneyline picks | model alone 76.5% (ROI -6.6%); market 84.3% (-0.4%); 20% model + 80% market 84.8% (+0.2%) |
| 85%+ confidence | NFL 88.6-90.5%, college football 93.4% (900 games) vs 88.6% / 91.5% model-only |

Engine change: stats-mode moneyline confidence for NFL and college football is now 0.2 x model + 0.8 x no-vig market, floor 85%.

# Pending VM backtests

College men's (2019-2025): no result, job killed after about 80 minutes (likely memory). College women's: running at time of writing. WNBA: held. These add historical-line ROI and over/under results. Nothing here is a proven profit edge; all signals are paper-tracked.

