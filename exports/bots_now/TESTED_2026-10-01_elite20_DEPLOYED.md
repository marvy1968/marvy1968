# TESTED + KEPT 2026-10-01 (CT) - live_equities_elite20_hardened.py (prefix e20-)

Walk-forward on Alpaca SIP daily bars, the 20-symbol Elite-20 list, 2016-2026, IS 24m / OOS 6m,
10 bps per side, next-open entry, GTC bracket (stop = MA50-k*sd, target = MA50), max 3 positions,
1 per sector, 0.5% risk. Grid entry band 2.0/2.5 sd x stop band 3.0/3.5 sd.
OOS 2019-03..2026-09: 174 trades, PF 1.36, win 49%, +16.3% (fixed $100k), maxDD -9.5%, PF at 2x cost 1.26 -> PASS.
Caveat: the live config alone (2.5/3.5 sd) over the same OOS span: 121 trades, PF 1.17 (2x cost 1.09);
the pass leans on the walk-forward switching to 2.0/3.0 sd in 2020-2024. The universe is today's mega-caps
(survivorship bias). Treat as "passed, watch closely".
Change deployed: hard 20% NAV notional cap per position (MAX_NOTIONAL_PCT). Ownership (e20- tags, own-position
accounting) and GTC brackets were already in place. Service equities-elite20 restarted 2026-10-01 17:03 ET.
