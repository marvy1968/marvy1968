# RETIRED 2026-10-01 (CT) - heatmap_news_bot_standalone.py (prefix hm-)

Unit: system `heatmap_bot.service` stopped + disabled (code, ledger kept; backup
~/backups/stockbots_pre_fix_20261001_165013.tgz).

Why: walk-forward on Alpaca SIP 5-min bars resampled to 60-min (RTH), 16-symbol watchlist, sector-ETF
5-day "hot" filter, hourly close > SMA20, stop max(k*ATR_h, 0.4%), target max(m*ATR_h, 1.5x stop),
flat by the close; IS 24m / OOS 6m, 3 bps per side; grid stop 1.0/1.5 ATR x target 1.5/2.0 ATR.
OOS 2021-01..2026-09: 11,419 trades, PF 0.87, win 46%, -4.9% (positions capped at 1% notional as in code),
maxDD -5.1%, PF at 2x cost 0.73. Best IS PF in any window 0.91. Pass bar PF >= 1.2.
(The headline keyword news filter could not be backtested; it only removes trades, it cannot fix a
PF this far below 1.)
