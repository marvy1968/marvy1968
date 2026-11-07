# In-game NBA prop pacer backtest (test season 2026, never seen in training)
train rows 137,604 · test rows 73,443

## 1. Remaining-stat accuracy (MAE, lower is better)

| stat | break | n | naive pacer | Claude Prop 1 | minutes model | ML |
|---|---|---|---|---|---|---|
| pts | Q1 | 23,367 | 7.30 | 4.71 | 4.21 | 4.20 |
| pts | Q2 | 24,896 | 4.13 | 3.50 | 3.28 | 3.28 |
| pts | Q3 | 25,180 | 2.58 | 2.37 | 2.33 | 2.29 |
| reb | Q1 | 23,367 | 2.99 | 1.91 | 1.70 | 1.70 |
| reb | Q2 | 24,896 | 1.67 | 1.41 | 1.31 | 1.32 |
| reb | Q3 | 25,180 | 1.04 | 0.96 | 0.94 | 0.92 |
| ast | Q1 | 23,367 | 2.16 | 1.39 | 1.23 | 1.22 |
| ast | Q2 | 24,896 | 1.17 | 1.01 | 0.95 | 0.93 |
| ast | Q3 | 25,180 | 0.71 | 0.66 | 0.65 | 0.63 |
| fg3 | Q1 | 23,367 | 1.40 | 0.92 | 0.80 | 0.80 |
| fg3 | Q2 | 24,896 | 0.76 | 0.67 | 0.63 | 0.63 |
| fg3 | Q3 | 25,180 | 0.44 | 0.41 | 0.42 | 0.41 |

## 2. Ladder probabilities (Brier, lower is better)

| stat | naive pacer | Claude Prop 1 | minutes model | ML |
|---|---|---|---|---|
| pts | 0.0716 | 0.0576 | 0.0536 | 0.0533 |
| reb | 0.0829 | 0.0672 | 0.0624 | 0.0625 |
| ast | 0.0762 | 0.0578 | 0.0533 | 0.0530 |
| fg3 | 0.1074 | 0.0780 | 0.0696 | 0.0696 |

## 3. Real Kalshi prices at the breaks (68 games, 10,758 contracts; evaluation part 6,455 rows, 2026-03-05 to 2026-05-25)
Kalshi settlement agrees with the play-by-play box score on 100.0% of contracts.

Brier on the evaluation part (lower is better; the market's own mid price is the bar):

- Kalshi mid price: 0.1638
- naive pacer: 0.1712
- naive pacer + market blend: 0.1461
- Claude Prop 1: 0.1419
- Claude Prop 1 + market blend: 0.1347
- minutes model: 0.1318
- minutes model + market blend: 0.1304
- ML: 0.1302
- ML + market blend: 0.1285

Paper bets on the evaluation part (YES at ask / NO at 1 - bid, after Kalshi fee):

| model | edge >= | bets | win rate | staked $ | P&L $ | ROI | (calibration part ROI) |
|---|---|---|---|---|---|---|---|
| naive pacer | 0.04 | 1005 | 51.5% | 551 | -32.7 | -5.9% | -13.9% |
| naive pacer | 0.06 | 890 | 50.4% | 475 | -26.1 | -5.5% | -16.2% |
| naive pacer | 0.08 | 791 | 49.4% | 414 | -23.2 | -5.6% | -15.1% |
| naive pacer | 0.1 | 705 | 47.2% | 359 | -26.2 | -7.3% | -13.5% |
| Claude Prop 1 | 0.04 | 760 | 50.1% | 399 | -17.9 | -4.5% | -8.1% |
| Claude Prop 1 | 0.06 | 644 | 48.4% | 334 | -21.8 | -6.5% | -7.0% |
| Claude Prop 1 | 0.08 | 527 | 47.4% | 266 | -16.3 | -6.1% | -7.0% |
| Claude Prop 1 | 0.1 | 432 | 45.6% | 211 | -14.2 | -6.7% | -6.5% |
| minutes model | 0.04 | 505 | 54.1% | 249 | +24.4 | +9.8% | -1.7% |
| minutes model | 0.06 | 359 | 53.5% | 173 | +19.1 | +11.0% | +2.0% |
| minutes model | 0.08 | 257 | 50.2% | 115 | +14.4 | +12.6% | -4.1% |
| minutes model | 0.1 | 181 | 45.9% | 77 | +5.7 | +7.3% | -4.0% |
| ML | 0.04 | 573 | 58.5% | 304 | +31.3 | +10.3% | -1.4% |
| ML | 0.06 | 430 | 56.0% | 219 | +22.0 | +10.1% | -3.1% |
| ML | 0.08 | 315 | 55.2% | 158 | +15.7 | +9.9% | +0.3% |
| ML | 0.1 | 236 | 55.9% | 118 | +13.8 | +11.6% | +3.1% |
| naive pacer + market blend | 0.04 | 569 | 64.0% | 355 | +9.2 | +2.6% | -9.5% |
| naive pacer + market blend | 0.06 | 407 | 62.9% | 249 | +6.7 | +2.7% | -11.7% |
| naive pacer + market blend | 0.08 | 304 | 61.5% | 183 | +3.7 | +2.0% | -9.4% |
| naive pacer + market blend | 0.1 | 241 | 62.2% | 142 | +7.6 | +5.3% | -10.1% |
| Claude Prop 1 + market blend | 0.04 | 634 | 59.8% | 382 | -2.8 | -0.7% | -4.7% |
| Claude Prop 1 + market blend | 0.06 | 504 | 57.3% | 295 | -6.0 | -2.0% | -4.5% |
| Claude Prop 1 + market blend | 0.08 | 397 | 54.9% | 224 | -6.0 | -2.7% | -1.8% |
| Claude Prop 1 + market blend | 0.1 | 303 | 54.8% | 168 | -1.8 | -1.1% | -8.2% |
| minutes model + market blend | 0.04 | 546 | 64.1% | 321 | +29.1 | +9.1% | -0.8% |
| minutes model + market blend | 0.06 | 403 | 62.5% | 230 | +21.9 | +9.5% | +5.0% |
| minutes model + market blend | 0.08 | 299 | 60.2% | 166 | +13.6 | +8.2% | +1.4% |
| minutes model + market blend | 0.1 | 205 | 60.5% | 110 | +14.0 | +12.8% | -2.5% |
| ML + market blend | 0.04 | 517 | 65.2% | 307 | +29.7 | +9.7% | -0.7% |
| ML + market blend | 0.06 | 369 | 64.2% | 211 | +26.0 | +12.3% | +0.9% |
| ML + market blend | 0.08 | 265 | 60.4% | 148 | +12.2 | +8.3% | +0.8% |
| ML + market blend | 0.1 | 198 | 62.6% | 109 | +15.3 | +14.0% | +1.4% |

Only quotes refreshed after the period ended (4,377 rows of the evaluation part):

- Brier Kalshi mid price: 0.1673
- Brier naive pacer: 0.2004
- Brier Claude Prop 1: 0.1682
- Brier minutes model: 0.1555
- Brier ML: 0.1541

| model | edge >= | bets | win rate | P&L $ | ROI |
|---|---|---|---|---|---|
| naive pacer | 0.04 | 887 | 50.7% | -39.8 | -8.1% |
| naive pacer | 0.06 | 783 | 49.4% | -33.9 | -8.0% |
| naive pacer | 0.08 | 695 | 48.5% | -29.1 | -7.9% |
| naive pacer | 0.1 | 621 | 46.2% | -32.3 | -10.1% |
| Claude Prop 1 | 0.04 | 680 | 48.1% | -29.6 | -8.3% |
| Claude Prop 1 | 0.06 | 577 | 47.0% | -28.7 | -9.6% |
| Claude Prop 1 | 0.08 | 470 | 46.0% | -22.7 | -9.5% |
| Claude Prop 1 | 0.1 | 390 | 44.6% | -18.4 | -9.6% |
| minutes model | 0.04 | 458 | 52.6% | +16.7 | +7.5% |
| minutes model | 0.06 | 332 | 52.4% | +14.3 | +9.0% |
| minutes model | 0.08 | 234 | 48.3% | +9.4 | +9.1% |
| minutes model | 0.1 | 163 | 43.6% | +2.4 | +3.6% |
| ML | 0.04 | 510 | 57.3% | +22.9 | +8.5% |
| ML | 0.06 | 384 | 54.9% | +16.2 | +8.3% |
| ML | 0.08 | 281 | 53.7% | +9.3 | +6.6% |
| ML | 0.1 | 211 | 54.5% | +8.0 | +7.5% |
