# In-game NCAAB prop pacer backtest (test season 2026, never seen in training)
train rows 86,787 · test rows 101,034

## 1. Remaining-stat accuracy (MAE, lower is better)

| stat | break | n | naive | prop1 | v2 | ml |
|---|---|---|---|---|---|---|
| pts | half | 101,034 | 3.38 | 2.95 | 2.86 | 2.83 |
| reb | half | 101,034 | 1.44 | 1.25 | 1.19 | 1.18 |
| ast | half | 101,034 | 0.80 | 0.71 | 0.69 | 0.67 |
| fg3 | half | 101,034 | 0.54 | 0.49 | 0.47 | 0.47 |

## 2. Ladder probabilities (Brier, lower is better)

| stat | naive | prop1 | v2 | ml |
|---|---|---|---|---|
| pts | 0.0462 | 0.0406 | 0.0395 | 0.0383 |
| reb | 0.0608 | 0.0525 | 0.0502 | 0.0497 |
| ast | 0.0428 | 0.0355 | 0.0331 | 0.0328 |
| fg3 | 0.0696 | 0.0559 | 0.0496 | 0.0494 |
