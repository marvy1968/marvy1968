# RETIRED 2026-10-01 (CT) - alpaca_orb_morning_daemon.py (prefix orb-, TSLA 5-min ORB retest)

Units: `alpaca-orb-morning.timer` + `.service` stopped + disabled (code kept; backup
~/backups/stockbots_pre_fix_20261001_165013.tgz).

Why: fixed version (1% risk with 20% notional cap instead of $300 risk / ~94% notional; exits only
reduce own ledger qty; bracket stop/target) failed walk-forward on Alpaca SIP 1-min TSLA bars
resampled to 5-min, 2019-2026, IS 24m / OOS 6m, 3 bps per side, grid target 1.4/2.0R x stop 0.8/1.2 ATR.
OOS 2021-01..2026-09: 1,019 trades, PF 1.00 (0.999), win 42%, return -0.1%, maxDD -7.5%,
PF at 2x cost 0.88 -> no edge after costs. Pass bar was PF >= 1.2 and positive at 2x cost.
Live audit also found: TSLA short flipped into a long (exit sent after another bot had already
closed the position), P&L logged from signal prices instead of fills.
