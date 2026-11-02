# Pacer + Trend-Angle Pacer: backtest results and code (2026-11-02)

Both research only, neither traded. Data: Alpaca SIP daily/5-min bars (55 stocks = top 5 per sector by 2026 market cap, survivorship-biased), OANDA bid/ask H1 for forex.
Files: pacer.py (Pacer v1), anglepacer.py (Trend-Angle Pacer, spec v1). Run on the VM: python -m alpacabot run-all --phase validate --families pacer | anglepacer

## 1. Pacer v1 (2026-10-31): relative-strength leaders ridden to ATR take-profit zones
Rules are in the docstring of pacer.py. Pass bar: since-2023 net R > 0, PF >= 1.2, beat 18/20 random-stock runs and SPY, last 12 months net R > 0.
```
## pacer: relative-strength leaders (top 20% 50-day RS vs SPY, 20-day RS > 0, ADX > 25, up-day volume) ridden to ATR TP zones; 55 stocks, 177 pacer signals, 10 bps a side
### T2 = +3 ATR (T1 +2 ATR half off, stop 1.5 ATR -> break-even, trail after +1R, 20 sessions max)
- since 2023: 170 t, win 56%, PF 1.18, +13.8R | random stocks median +9.7R, beaten 12/20 | SPY same days 68 t, win 51%, PF 0.91, -3.0R
- 2023: 46 t, win 59%, PF 1.70, +12.3R
- 2024: 52 t, win 60%, PF 1.33, +6.6R
- 2025: 41 t, win 49%, PF 0.79, -5.4R
- last 12 months: 36 t, win 58%, PF 1.04, +0.7R | random stocks median -0.2R, beaten 11/20 | SPY same days 20 t, win 45%, PF 0.51, -5.8R
- last 6 months: 18 t, win 56%, PF 0.83, -1.7R
- last 2 weeks: 2 t, win 0%, PF 0.00, -2.1R | random stocks median -0.0R, beaten 2/20 | SPY same days 1 t, win 100%, PF 99.00, +0.5R
- verdict: fail
### T2 = measured move (20-day range, at least +2.5 ATR) (T1 +2 ATR half off, stop 1.5 ATR -> break-even, trail after +1R, 20 sessions max)
- since 2023: 170 t, win 56%, PF 1.22, +17.3R | random stocks median +12.1R, beaten 13/20 | SPY same days 61 t, win 56%, PF 1.02, +0.6R
- 2023: 46 t, win 59%, PF 1.77, +13.6R
- 2024: 52 t, win 60%, PF 1.36, +7.3R
- 2025: 41 t, win 49%, PF 0.81, -5.0R
- last 12 months: 36 t, win 58%, PF 1.17, +2.9R | random stocks median +0.3R, beaten 12/20 | SPY same days 19 t, win 47%, PF 0.53, -5.1R
- last 6 months: 18 t, win 56%, PF 0.78, -2.1R
- last 2 weeks: 2 t, win 0%, PF 0.00, -2.1R | random stocks median -0.0R, beaten 2/20 | SPY same days 1 t, win 100%, PF 99.00, +0.5R
- verdict: fail
```

## 2. Trend-Angle Pacer (spec v1, 2026-11-01, /opt/marv-bot/state/trend_angle_pacer_SPEC.md)
Train 2018-2023 (27-combo sweep, picked by train PF), holdout 2024-01-01..2026-11-01. Pass bar: holdout PF > 1.1, 100+ trades, train PF > 1.0, beat random-time 16/20 AND shuffled-direction 16/20, PF > 1.0 at 2x costs.
```
## Trend-Angle Pacer (spec v1, /opt/marv-bot/state/trend_angle_pacer_SPEC.md) - train 2018-2023, holdout 2024-01-01..2026-11-01
| Market/TF | Params | Train PF | Train Ret% | Holdout PF | Holdout Ret% | Win% | MaxDD% | Holdout trades | PF @2x cost | Random beats /20 | Shuffled beats /20 | Zone hit% vs chance (p) | 2025 PF (n) | Last 6m PF (n) | WF OOS PF (n) | PASS/FAIL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| stocks-daily (weekly filter) | thr 0.08, N 30, R2 0.25 | 0.61 | -61.4 | 0.70 | -31.2 | 38% | 36.5 | 386 | 0.68 | 0/20 | 0/20 | 60% vs 46% (p=0.000); random zones 69% (p=0.999), n=570 | 0.55 (116) | 0.38 (41) | 0.66 (756) | FAIL |
| stocks-daily (weekly filter) LONG ONLY | same | 0.70 | -45.3 | 0.75 | -24.4 | 40% | 30.5 | 351 | 0.73 | 0/20 | 0/20 | - | - | - | - | FAIL |
| stocks-1H (daily filter) | thr 0.08, N 20, R2 0.5 | 0.48 | -100.0 | 0.55 | -99.6 | 32% | 99.7 | 3328 | 0.49 | 0/20 | 0/20 | 52% vs 44% (p=0.000); random zones 60% (p=1.000), n=4878 | 0.58 (1079) | 0.57 (549) | 0.49 (7310) | FAIL |
| stocks-1H (daily filter) LONG ONLY | same | 0.56 | -99.9 | 0.61 | -97.0 | 34% | 97.0 | 2487 | 0.56 | 0/20 | 0/20 | - | - | - | - | FAIL |
| forex-H1 (H4 filter) | thr 0.08, N 14, R2 0.5 | 0.65 | -99.7 | 0.60 | -95.9 | 37% | 96.1 | 2393 | 0.49 | 0/20 | 0/20 | 48% vs 42% (p=0.000); random zones 52% (p=1.000), n=2586 | 0.63 (861) | 0.50 (400) | 0.60 (5334) | FAIL |

- stocks-daily (weekly filter): per-year PF 2018 0.54 (123), 2019 0.75 (115), 2020 0.56 (128), 2021 0.57 (149), 2022 0.52 (116), 2023 0.83 (106), 2024 0.89 (181), 2025 0.55 (116), 2026 0.57 (89) | holdout exits {'tp': 128, 'stop': 236, 'time': 22, 'flip': 0} | long 346 t PF 0.74, short 40 t PF 0.42 | random median PF 1.42, shuffled median PF 1.01
- stocks-1H (daily filter): per-year PF 2018 0.48 (1075), 2019 0.50 (1154), 2020 0.56 (1105), 2021 0.48 (1263), 2022 0.46 (1241), 2023 0.44 (1169), 2024 0.55 (1271), 2025 0.58 (1079), 2026 0.50 (978) | holdout exits {'tp': 881, 'stop': 2201, 'time': 246, 'flip': 0} | long 2260 t PF 0.59, short 1068 t PF 0.45 | random median PF 1.41, shuffled median PF 0.93
- forex-H1 (H4 filter): per-year PF 2018 0.72 (879), 2019 0.65 (896), 2020 0.74 (896), 2021 0.63 (854), 2022 0.67 (856), 2023 0.51 (817), 2024 0.58 (842), 2025 0.63 (861), 2026 0.59 (690) | holdout exits {'tp': 627, 'stop': 1333, 'time': 433, 'flip': 0} | long 1291 t PF 0.62, short 1102 t PF 0.58 | random median PF 0.91, shuffled median PF 0.76
- HOURLY CHECK: stocks-daily holdout PF 0.70 on daily bars vs 0.69 replayed on 1H bars (386 trades, 0 with no hourly fill) -> daily result holds on hourly bars; stocks-1H strategy holdout PF 0.55
```

Verdicts: Pacer v1 FAIL (edge fading: 2025 PF 0.79, last 6 months 0.83, beats random only 12/20). Trend-Angle Pacer FAIL everywhere (every year < 1.0, 0/20 vs random; limit buys at pullback zones fill mostly on pullbacks that keep falling).
