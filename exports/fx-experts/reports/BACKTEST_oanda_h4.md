# Backtest results (real OANDA H4 mid candles, 2016-01-04 .. 2026-09-07; pairs: EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, USD_CHF, NZD_USD, EUR_JPY, GBP_JPY)

Costs: typical spread per pair, +0.3 pip slippage on stop/market fills, 2x spread at the 17:00 NY open, 0.3 pip/night financing (x3 Wed). R = multiple of initial risk. % columns assume 0.5% risk per trade, non-compounding.

## Per system (all pairs)

| System | Trades | Win | PF | Net R | Avg R | MaxDD R | MaxDD % @0.5% | Net % @0.5% |
|---|---|---|---|---|---|---|---|---|
| lien-fader | 612 | 40.8% | 1.34 | +126.3 | +0.206 | 30.4 | 15.2% | +63.1% |
| faith-turtle | 944 | 32.3% | 0.73 | -122.0 | -0.129 | 124.7 | 62.3% | -61.0% |
| raschke-soup | 1567 | 26.9% | 1.44 | +470.7 | +0.300 | 40.2 | 20.1% | +235.3% |
| bollinger-squeeze | 179 | 33.5% | 0.90 | -7.5 | -0.042 | 16.4 | 8.2% | -3.7% |
| crabel-idnr4 | 2090 | 42.2% | 0.98 | -16.6 | -0.008 | 62.6 | 31.3% | -8.3% |
| **all 5 combined** | 5392 | 35.6% | 1.15 | +450.8 | +0.084 | 75.2 | 37.6% | +225.4% |

## Per system per pair

| System | Pair | Trades | Win | PF | Net R | Avg R | MaxDD R | MaxDD % | Net % |
|---|---|---|---|---|---|---|---|---|---|
| lien-fader | EUR_USD | 51 | 49.0% | 1.87 | +23.2 | +0.454 | 4.2 | 2.1% | +11.6% |
| lien-fader | GBP_USD | 102 | 35.3% | 1.07 | +4.5 | +0.044 | 15.5 | 7.8% | +2.3% |
| lien-fader | USD_JPY | 79 | 39.2% | 1.26 | +12.9 | +0.163 | 7.2 | 3.6% | +6.5% |
| lien-fader | AUD_USD | 48 | 33.3% | 0.97 | -0.9 | -0.020 | 9.4 | 4.7% | -0.5% |
| lien-fader | USD_CAD | 70 | 38.6% | 1.16 | +7.4 | +0.105 | 15.0 | 7.5% | +3.7% |
| lien-fader | USD_CHF | 44 | 47.7% | 1.77 | +18.2 | +0.414 | 5.1 | 2.5% | +9.1% |
| lien-fader | NZD_USD | 40 | 27.5% | 0.73 | -8.0 | -0.199 | 16.7 | 8.3% | -4.0% |
| lien-fader | EUR_JPY | 72 | 48.6% | 1.86 | +32.2 | +0.447 | 5.2 | 2.6% | +16.1% |
| lien-fader | GBP_JPY | 106 | 45.3% | 1.62 | +36.9 | +0.348 | 6.1 | 3.0% | +18.4% |
| faith-turtle | EUR_USD | 112 | 29.5% | 0.70 | -16.1 | -0.144 | 21.1 | 10.6% | -8.1% |
| faith-turtle | GBP_USD | 103 | 35.9% | 0.77 | -10.0 | -0.098 | 11.0 | 5.5% | -5.0% |
| faith-turtle | USD_JPY | 91 | 37.4% | 1.35 | +13.5 | +0.148 | 8.9 | 4.5% | +6.7% |
| faith-turtle | AUD_USD | 110 | 31.8% | 0.55 | -25.7 | -0.234 | 28.7 | 14.3% | -12.9% |
| faith-turtle | USD_CAD | 106 | 29.2% | 0.72 | -15.3 | -0.144 | 18.9 | 9.4% | -7.6% |
| faith-turtle | USD_CHF | 104 | 33.7% | 0.77 | -11.8 | -0.113 | 24.2 | 12.1% | -5.9% |
| faith-turtle | NZD_USD | 112 | 33.0% | 0.66 | -17.7 | -0.158 | 19.0 | 9.5% | -8.9% |
| faith-turtle | EUR_JPY | 112 | 29.5% | 0.50 | -30.8 | -0.275 | 30.9 | 15.5% | -15.4% |
| faith-turtle | GBP_JPY | 94 | 31.9% | 0.83 | -8.1 | -0.086 | 11.3 | 5.6% | -4.0% |
| raschke-soup | EUR_USD | 158 | 31.6% | 2.57 | +158.9 | +1.006 | 17.0 | 8.5% | +79.5% |
| raschke-soup | GBP_USD | 181 | 21.5% | 0.99 | -1.1 | -0.006 | 35.8 | 17.9% | -0.5% |
| raschke-soup | USD_JPY | 155 | 28.4% | 1.27 | +28.4 | +0.183 | 23.3 | 11.6% | +14.2% |
| raschke-soup | AUD_USD | 157 | 25.5% | 1.30 | +33.4 | +0.213 | 26.0 | 13.0% | +16.7% |
| raschke-soup | USD_CAD | 183 | 26.2% | 1.54 | +67.1 | +0.367 | 12.4 | 6.2% | +33.6% |
| raschke-soup | USD_CHF | 190 | 28.4% | 1.48 | +60.7 | +0.320 | 18.5 | 9.3% | +30.4% |
| raschke-soup | NZD_USD | 182 | 24.2% | 1.24 | +29.6 | +0.163 | 31.8 | 15.9% | +14.8% |
| raschke-soup | EUR_JPY | 191 | 30.4% | 1.60 | +75.8 | +0.397 | 17.4 | 8.7% | +37.9% |
| raschke-soup | GBP_JPY | 170 | 26.5% | 1.15 | +17.8 | +0.105 | 14.1 | 7.0% | +8.9% |
| bollinger-squeeze | EUR_USD | 18 | 55.6% | 3.56 | +9.1 | +0.506 | 1.4 | 0.7% | +4.6% |
| bollinger-squeeze | GBP_USD | 20 | 40.0% | 0.79 | -1.7 | -0.083 | 4.0 | 2.0% | -0.8% |
| bollinger-squeeze | USD_JPY | 16 | 31.2% | 0.47 | -3.5 | -0.220 | 5.2 | 2.6% | -1.8% |
| bollinger-squeeze | AUD_USD | 22 | 13.6% | 0.26 | -8.5 | -0.385 | 11.3 | 5.6% | -4.2% |
| bollinger-squeeze | USD_CAD | 22 | 40.9% | 1.68 | +6.3 | +0.285 | 4.3 | 2.1% | +3.1% |
| bollinger-squeeze | USD_CHF | 23 | 34.8% | 0.83 | -1.8 | -0.076 | 5.7 | 2.8% | -0.9% |
| bollinger-squeeze | NZD_USD | 15 | 33.3% | 0.45 | -3.3 | -0.221 | 3.3 | 1.7% | -1.7% |
| bollinger-squeeze | EUR_JPY | 19 | 42.1% | 1.54 | +2.7 | +0.144 | 1.4 | 0.7% | +1.4% |
| bollinger-squeeze | GBP_JPY | 24 | 16.7% | 0.46 | -6.8 | -0.285 | 11.8 | 5.9% | -3.4% |
| crabel-idnr4 | EUR_USD | 226 | 36.7% | 0.74 | -32.4 | -0.143 | 34.0 | 17.0% | -16.2% |
| crabel-idnr4 | GBP_USD | 239 | 43.1% | 1.00 | -0.4 | -0.002 | 17.4 | 8.7% | -0.2% |
| crabel-idnr4 | USD_JPY | 246 | 42.3% | 1.05 | +5.7 | +0.023 | 13.2 | 6.6% | +2.8% |
| crabel-idnr4 | AUD_USD | 237 | 44.3% | 1.12 | +13.5 | +0.057 | 14.4 | 7.2% | +6.8% |
| crabel-idnr4 | USD_CAD | 204 | 38.7% | 0.84 | -17.1 | -0.084 | 20.4 | 10.2% | -8.5% |
| crabel-idnr4 | USD_CHF | 223 | 37.7% | 0.84 | -18.5 | -0.083 | 33.1 | 16.5% | -9.2% |
| crabel-idnr4 | NZD_USD | 244 | 38.9% | 0.70 | -40.1 | -0.165 | 43.2 | 21.6% | -20.1% |
| crabel-idnr4 | EUR_JPY | 223 | 48.4% | 1.38 | +34.6 | +0.155 | 9.5 | 4.8% | +17.3% |
| crabel-idnr4 | GBP_JPY | 248 | 48.4% | 1.34 | +38.0 | +0.153 | 12.8 | 6.4% | +19.0% |

## Per system per year (net R)

| system | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bollinger-squeeze | 2.3 | -4.7 | 1.0 | -8.7 | 11.1 | 1.3 | -3.0 | -2.8 | -3.2 | -4.9 | 4.0 |
| crabel-idnr4 | 10.1 | -13.0 | -8.7 | -10.8 | 39.3 | -11.0 | 9.6 | -35.2 | 5.2 | -1.7 | -0.6 |
| faith-turtle | -23.5 | -11.6 | -4.9 | -18.1 | -11.9 | -9.1 | -3.0 | -5.2 | 0.7 | -22.7 | -12.7 |
| lien-fader | 47.0 | 15.2 | 1.4 | -10.6 | -4.6 | 0.1 | 27.0 | 21.9 | 20.3 | -2.8 | 11.5 |
| raschke-soup | 62.4 | 70.9 | 17.3 | 51.0 | 27.8 | 5.0 | 51.3 | 115.9 | 14.8 | 34.3 | 19.9 |

## Exit reasons

| system | eod | stop | target | time(6d) |
|---|---|---|---|---|
| bollinger-squeeze | 0 | 179 | 0 | 0 |
| crabel-idnr4 | 1170 | 920 | 0 | 0 |
| faith-turtle | 0 | 944 | 0 | 0 |
| lien-fader | 0 | 362 | 250 | 0 |
| raschke-soup | 0 | 1508 | 0 | 59 |
