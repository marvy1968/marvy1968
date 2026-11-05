# RETIRED 2026-10-01 (CT) - multi_scalper_shadow.py (prefix ms-)

Unit: system `masterbrain.service` stopped + disabled (code, ledger, breaker state kept; backup
~/backups/stockbots_pre_fix_20261001_165013.tgz). `shadowbot.service` (same script) was already disabled.

Why: fixed version (live-price stop/target checks were already in FIX_V2; target raised from 0.85R to
>=1.5R, breakeven moved from 0.4R to >=1R or off, trail lock 0.5-0.75R) failed walk-forward on Alpaca
SIP 5-min bars, full 74-ticker universe, 2020-2026, IS 24m / OOS 6m, 4 bps per side, portfolio rules
(10 slots, 2/sector, 15-min entry cooldown, 1% risk with the code's stop floor, 5% notional cap).
Grid: target 1.5/2.0R x breakeven 1R/off.
OOS 2022-01..2026-09: 28,460 trades, PF 0.30, win 23%, PF before costs 0.89, PF at 2x cost 0.12.
Stops sit 2-3 five-minute bars away (typical move ~10 bps), so ~8 bps round-trip costs eat the edge
and the raw signal is negative even before costs. Pass bar PF >= 1.2.
