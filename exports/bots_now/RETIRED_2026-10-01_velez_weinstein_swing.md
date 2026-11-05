# RETIRED 2026-10-01 (CT) - trade_velez_weinstein_swing.py (prefix vws-)

Units disabled: system `alpaca-swing-daily.timer/.service`, system `stockbot_scan.timer` (both ran this
script, 15:55 and 16:05 ET), user `alpaca-swing-daily.timer/.service`. Code + ledger kept; backup
~/backups/stockbots_pre_fix_20261001_165013.tgz.

Why: walk-forward on Alpaca SIP daily bars (72-name universe in the script, 2016-2026), IS 24m / OOS 6m,
10 bps per side, entry at signal close, 0.5% risk, max 10 slots, grid stop 1.5/2.0 ATR x
TP1 0.75R(+2R runner)/single 2R. OOS 2019-01..2026-09: 6,675 legs, PF 0.96, -39.7% (fixed $100k),
maxDD -58.6%, PF at 2x cost 0.81. Best IS PF in any window was 1.05. Pass bar PF >= 1.2.
Main reason: 'Stage 2 + close above SMA20' fires on almost every leader every day and the stop
max(close-1.5ATR, 0.985*SMA20) is often very tight -> high turnover, stopped out by noise.

OPEN POSITIONS LEFT UNTOUCHED (ARM, CAT, CRWD, NET, PANW, SHOP, SNOW as of 2026-10-01 close).
NOTE: the entry brackets were time_in_force=day, so their stop/target legs EXPIRED at the close:
these positions currently have NO protective orders. Marvin must decide how to manage/close them.

## Addendum 2026-10-01 (evening): Weinstein Stage-2 rework re-test — still FAIL (kept retired)
Diagnosis of the original vws (live config, 72-name Alpaca universe, 10 bps):
- 17,126 legs, ~7.3 entries/day; gross PF 1.12 but costs ($262k) > gross profit ($142k) -> net PF 0.91.
- 58% of legs exit via the "trend extension" rule (avg +$30): ranking picks the MOST extended names, which then
  trip the extension exit a day later -> churn. Stops (1.5xATR, median 3.0%) cost -$879k; the regime filter barely
  matters (PF 0.91 in SPY>30wk vs 0.95 below).
Pre-declared rework (8 configs, WF 24m IS / 6m OOS): breakout above prior 50-day closing high, close > rising
150d (30-wk) MA, volume >= 1.5x/2.0x 50d avg, RS vs SPY (126d) > 0, SPY > its 150d MA, enter next open,
stop 2xATR or below 20-day low (skip if risk > 4 ATR), exit on weekly close below 30-wk or 10-wk MA, no partials,
max 10 positions, 1% risk, 20% notional cap.
OOS walk-forward, single portfolio, mark-to-market:
- 915-stock S&P500+400 universe (Yahoo daily, 2010-02..2026-09): 553 trades, PF 1.52, +171%, PF@2x 1.46, MTM max DD -32.2% -> FAIL (DD >= 25%)
- 72-stock Alpaca universe (2019-02..2026-09): 176 trades, PF 2.31, +143%, PF@2x 2.21, MTM max DD -32.8% -> FAIL (DD)
Post-hoc (NOT pre-declared, not a pass): max 5 positions -> Yahoo PF 1.62 / DD -18.3%, but Alpaca DD -34.6%.
Candidate for a fresh pre-declared test with a hard gross-exposure cap. Files: box /workspace/bt2/wein_*.py, table_wein_yahoo.csv
