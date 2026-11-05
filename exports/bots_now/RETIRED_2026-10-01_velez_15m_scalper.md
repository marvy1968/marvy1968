# RETIRED 2026-10-01 (CT) - alpaca_velez_15m_scalper.py (prefix vs-)

Service: user unit `alpaca-velez-scalper.service` stopped + disabled (code kept; backup
~/backups/stockbots_pre_fix_20261001_165013.tgz).

Why: the fixed strategy (1% risk / 20% notional cap, real bracket SL+TP, own-leg cancel before
time stop) failed the walk-forward test on Alpaca SIP 1-min bars resampled to 2-min,
NVDA/TSLA/AMD, 2019-2026, IS 24m / OOS 6m, 3 bps per side cost, 8-config pre-declared grid
(target 1.35/2.0R x stop 1.1/1.5 ATR x 20-min time stop on/off).
OOS 2021-01..2026-09: 4,990 trades, PF 0.76, win 33%, -63% (fixed $100k, non-compounded), maxDD -64%,
PF at 2x cost 0.59. No config had IS PF > 0.94 in any window. Pass bar was PF >= 1.2.
Live audit also found: 25% equity sizing, entries sent as plain market orders with no stop,
time-stop exit loop (~29k 'insufficient qty' errors), and closing other bots' positions pre-ownership fix.
