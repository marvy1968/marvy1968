# Backtest results (Dukascopy H1 bid/ask-mid candles, 2016-01-04 .. 2026-09-29; pairs: EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, USD_CHF, NZD_USD, EUR_JPY, GBP_JPY)

Costs: typical spread per pair, +0.3 pip slippage on stop/market fills, 2x spread at the 17:00 NY open, 0.3 pip/night financing (x3 Wed). R = multiple of initial risk. % columns assume 0.5% risk per trade, non-compounding.

## Per system (all pairs)

| System | Trades | Win | PF | Net R | Avg R | MaxDD R | MaxDD % @0.5% | Net % @0.5% |
|---|---|---|---|---|---|---|---|---|
| lien-fader | 611 | 37.8% | 1.18 | +71.1 | +0.116 | 41.7 | 20.9% | +35.6% |
| faith-turtle | 941 | 31.6% | 0.72 | -128.0 | -0.136 | 131.4 | 65.7% | -64.0% |
| raschke-soup | 1941 | 21.1% | 1.02 | +34.8 | +0.018 | 83.0 | 41.5% | +17.4% |
| bollinger-squeeze | 179 | 33.0% | 0.86 | -10.4 | -0.058 | 20.3 | 10.1% | -5.2% |
| crabel-idnr4 | 2122 | 42.0% | 0.96 | -39.1 | -0.018 | 67.7 | 33.9% | -19.5% |
| **all 5 combined** | 5794 | 32.6% | 0.98 | -71.5 | -0.012 | 156.9 | 78.5% | -35.8% |

## Per system per pair

| System | Pair | Trades | Win | PF | Net R | Avg R | MaxDD R | MaxDD % | Net % |
|---|---|---|---|---|---|---|---|---|---|
| lien-fader | EUR_USD | 52 | 46.2% | 1.67 | +19.1 | +0.368 | 5.2 | 2.6% | +9.6% |
| lien-fader | GBP_USD | 100 | 35.0% | 1.05 | +3.6 | +0.036 | 16.6 | 8.3% | +1.8% |
| lien-fader | USD_JPY | 77 | 33.8% | 1.00 | -0.0 | -0.001 | 11.2 | 5.6% | -0.0% |
| lien-fader | AUD_USD | 48 | 29.2% | 0.80 | -6.9 | -0.144 | 12.6 | 6.3% | -3.5% |
| lien-fader | USD_CAD | 72 | 37.5% | 1.13 | +6.1 | +0.085 | 12.1 | 6.0% | +3.0% |
| lien-fader | USD_CHF | 45 | 44.4% | 1.56 | +14.2 | +0.316 | 4.3 | 2.1% | +7.1% |
| lien-fader | NZD_USD | 36 | 27.8% | 0.74 | -6.9 | -0.191 | 14.5 | 7.3% | -3.4% |
| lien-fader | EUR_JPY | 74 | 44.6% | 1.58 | +24.1 | +0.326 | 7.1 | 3.6% | +12.1% |
| lien-fader | GBP_JPY | 107 | 39.3% | 1.27 | +17.8 | +0.166 | 10.3 | 5.1% | +8.9% |
| faith-turtle | EUR_USD | 111 | 28.8% | 0.69 | -16.5 | -0.148 | 20.7 | 10.4% | -8.2% |
| faith-turtle | GBP_USD | 103 | 35.9% | 0.77 | -9.9 | -0.096 | 11.0 | 5.5% | -4.9% |
| faith-turtle | USD_JPY | 94 | 35.1% | 1.29 | +11.9 | +0.127 | 9.9 | 4.9% | +6.0% |
| faith-turtle | AUD_USD | 110 | 31.8% | 0.58 | -23.7 | -0.215 | 27.4 | 13.7% | -11.8% |
| faith-turtle | USD_CAD | 102 | 28.4% | 0.68 | -16.6 | -0.162 | 20.5 | 10.3% | -8.3% |
| faith-turtle | USD_CHF | 101 | 32.7% | 0.81 | -9.5 | -0.094 | 22.5 | 11.2% | -4.7% |
| faith-turtle | NZD_USD | 114 | 32.5% | 0.64 | -19.6 | -0.172 | 21.0 | 10.5% | -9.8% |
| faith-turtle | EUR_JPY | 112 | 29.5% | 0.50 | -30.4 | -0.271 | 30.4 | 15.2% | -15.2% |
| faith-turtle | GBP_JPY | 94 | 29.8% | 0.73 | -13.8 | -0.147 | 13.8 | 6.9% | -6.9% |
| raschke-soup | EUR_USD | 198 | 23.7% | 1.68 | +97.1 | +0.491 | 23.1 | 11.6% | +48.6% |
| raschke-soup | GBP_USD | 217 | 17.5% | 0.76 | -42.4 | -0.195 | 49.5 | 24.8% | -21.2% |
| raschke-soup | USD_JPY | 194 | 22.7% | 0.99 | -2.0 | -0.010 | 35.2 | 17.6% | -1.0% |
| raschke-soup | AUD_USD | 194 | 19.6% | 0.95 | -7.8 | -0.040 | 39.2 | 19.6% | -3.9% |
| raschke-soup | USD_CAD | 215 | 22.3% | 1.23 | +36.6 | +0.170 | 22.0 | 11.0% | +18.3% |
| raschke-soup | USD_CHF | 241 | 22.0% | 0.97 | -4.8 | -0.020 | 33.8 | 16.9% | -2.4% |
| raschke-soup | NZD_USD | 221 | 17.6% | 0.79 | -34.7 | -0.157 | 57.8 | 28.9% | -17.4% |
| raschke-soup | EUR_JPY | 237 | 23.6% | 1.11 | +18.9 | +0.080 | 31.9 | 16.0% | +9.4% |
| raschke-soup | GBP_JPY | 224 | 20.5% | 0.85 | -26.1 | -0.116 | 39.2 | 19.6% | -13.0% |
| bollinger-squeeze | EUR_USD | 18 | 55.6% | 3.16 | +8.6 | +0.478 | 1.4 | 0.7% | +4.3% |
| bollinger-squeeze | GBP_USD | 21 | 38.1% | 0.92 | -0.7 | -0.032 | 4.3 | 2.2% | -0.3% |
| bollinger-squeeze | USD_JPY | 16 | 37.5% | 0.61 | -2.3 | -0.141 | 3.9 | 2.0% | -1.1% |
| bollinger-squeeze | AUD_USD | 22 | 13.6% | 0.29 | -8.1 | -0.369 | 10.3 | 5.1% | -4.1% |
| bollinger-squeeze | USD_CAD | 21 | 38.1% | 1.61 | +5.6 | +0.265 | 4.8 | 2.4% | +2.8% |
| bollinger-squeeze | USD_CHF | 23 | 34.8% | 0.73 | -2.9 | -0.125 | 6.4 | 3.2% | -1.4% |
| bollinger-squeeze | NZD_USD | 16 | 25.0% | 0.34 | -5.2 | -0.327 | 5.2 | 2.6% | -2.6% |
| bollinger-squeeze | EUR_JPY | 18 | 38.9% | 1.27 | +1.4 | +0.079 | 1.4 | 0.7% | +0.7% |
| bollinger-squeeze | GBP_JPY | 24 | 20.8% | 0.48 | -6.8 | -0.285 | 11.9 | 6.0% | -3.4% |
| crabel-idnr4 | EUR_USD | 223 | 37.2% | 0.82 | -21.0 | -0.094 | 29.1 | 14.5% | -10.5% |
| crabel-idnr4 | GBP_USD | 240 | 43.8% | 0.97 | -3.9 | -0.016 | 19.7 | 9.9% | -1.9% |
| crabel-idnr4 | USD_JPY | 247 | 43.3% | 1.07 | +8.1 | +0.033 | 12.4 | 6.2% | +4.1% |
| crabel-idnr4 | AUD_USD | 242 | 44.2% | 1.11 | +12.7 | +0.053 | 11.7 | 5.9% | +6.4% |
| crabel-idnr4 | USD_CAD | 213 | 37.1% | 0.76 | -27.0 | -0.127 | 32.5 | 16.2% | -13.5% |
| crabel-idnr4 | USD_CHF | 228 | 37.7% | 0.76 | -28.1 | -0.123 | 35.5 | 17.8% | -14.1% |
| crabel-idnr4 | NZD_USD | 254 | 39.0% | 0.73 | -34.8 | -0.137 | 37.1 | 18.6% | -17.4% |
| crabel-idnr4 | EUR_JPY | 227 | 48.5% | 1.30 | +28.2 | +0.124 | 12.4 | 6.2% | +14.1% |
| crabel-idnr4 | GBP_JPY | 248 | 46.4% | 1.24 | +26.7 | +0.107 | 11.5 | 5.8% | +13.3% |

## Per system per year (net R)

| system | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bollinger-squeeze | 2.5 | -3.8 | 3.6 | -9.6 | 9.5 | 0.5 | -3.0 | -3.0 | -3.3 | -5.3 | 1.5 |
| crabel-idnr4 | 3.0 | -14.8 | -13.1 | -12.5 | 40.9 | 3.4 | 9.0 | -39.5 | -2.4 | -0.2 | -12.7 |
| faith-turtle | -26.1 | -9.1 | -5.8 | -17.8 | -11.8 | -8.4 | -2.0 | -7.6 | 1.8 | -28.4 | -12.7 |
| lien-fader | 30.0 | 15.2 | 3.4 | -14.9 | -11.7 | 2.2 | 21.1 | 15.9 | 14.3 | -9.8 | 5.5 |
| raschke-soup | 31.6 | 41.7 | -25.7 | 10.8 | -13.9 | -21.6 | -1.8 | 67.2 | -20.9 | -12.7 | -19.9 |

## Exit reasons

| system | eod | stop | target | time(6d) |
|---|---|---|---|---|
| bollinger-squeeze | 0 | 179 | 0 | 0 |
| crabel-idnr4 | 1203 | 919 | 0 | 0 |
| faith-turtle | 0 | 941 | 0 | 0 |
| lien-fader | 0 | 380 | 231 | 0 |
| raschke-soup | 0 | 1884 | 0 | 57 |
