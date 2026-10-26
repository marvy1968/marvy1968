## 2026-09-25 — Scalp bot v1 preregistration
Spec: default config.py and experts.py as installed (hashes verified).
Discovery: 2024 only. Held out: 2025-01-01 to 2026-09-23 (untouched).
Costs: 2 bps per side (targets: 0 on exit).
Advance an expert only if, on 2024: break-even slippage >= 5 bps,
profit factor >= 1.2 at 2 bps, and it survives removing its best 5% of trades.
Variants tested on 2024 to date: 1

## 2026-09-30 - Weinstein breakout: FROZEN rules, held-out test (run once)
Discovery 2017-2021: base t=3.84 (+0.59R, 13.9%/yr, DD -21.4%); market filter t=3.75 (+0.59R, 13.0%/yr, DD -19.8%).
Other results: Velez swing A t=-4.18, B t=-4.06 (reject). 5 experts + zones -$0.145/trade (reject). Zones t=1.05 (reject).
PRIMARY held-out test: weinstein.py --market-filter, 2022-01-01 to 2026-09-25, rules unchanged.
PASS if: avg R > 0 with t >= 2.0, max drawdown smaller than SPY's over the same period,
and profits not coming from a single year. Otherwise: no edge; stay with the index.
