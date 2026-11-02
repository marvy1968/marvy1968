# VWAP / ORB Day-Trade Spec v1 — Marvin's Alpaca stock bot
Written 2026-11-02 (ET). Research and spec only: nothing traded, nothing changed on the VM. Claude implements and backtests this; paper trading comes before any live money.

Owner's request: "an alpaca style day trading bot with a 62% win rate. Based on VWAP or what works best. Trade 10 to 350 [10:00–15:50 ET], then sell off unless it decides to swing overnight. Deep search what works." Added later: universe = top 3 stocks per S&P 500 sector, point-in-time, rebuilt monthly; shorting allowed; score longs and shorts separately.

---
## 0. Evidence summary (every number below is from the cited source)
| Idea | Source | What it reports | Win rate / payoff | Caveats |
|---|---|---|---|---|
| VWAP trend (long above VWAP, short below, stop on a 1-min close across VWAP, flat at the close) | Zarattini & Aziz 2023, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4631351 (PDF: https://concretumgroup.com/wp-content/uploads/2026/02/Volume-Weighted-Average-Price.pdf) | QQQ 2018–2023: +671% net of $0.0005/sh, Sharpe 2.1, max DD 9.4%, 21,967 trades | **Hit ratio 17%**, avg gain:loss 5.67 | An independent QuantConnect replication (https://www.quantconnect.com/terminal/cache/embedded_backtest_b115d0894231d55994b1068202f6c0ae.html) gets a 17% win rate, but its notes say out-of-sample Sharpe decayed to about 0.7 after Sep 2023 and **the edge disappears under realistic retail costs** (the break-even cost is below ~1 bp round trip). |
| Opening-range breakout, QQQ | Zarattini & Aziz 2023, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622 | 5-min ORB 2016–2023: +676% on QQQ, 33% annualized alpha net of commissions | (low hit rate, trend payoff) | Results depend on leverage. Published by a trading-education group. |
| ORB on Stocks in Play | Zarattini, Barbon & Aziz 2024, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284 | 7,000+ US stocks 2016–2023: the top-20 stocks in play by relative volume earned +1,600%, Sharpe 2.81. **Limiting trades to stocks in play was the key benefit**, and they also tested 15/30/60-min ranges. | trend payoff | It is a small-cap-heavy universe. On 33 mega-caps the effect will be much weaker. |
| Intraday momentum for SPY/QQQ with dynamic trailing stops | Zarattini, Aziz & Barbon 2024, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4824172 | SPY 2007–2024: +1,985% net, 19.6%/yr, Sharpe 1.33 | n/a | The authors include a cost-sensitivity section. |
| First half-hour return predicts the last half hour | Gao, Han, Li & Zhou 2018, JFE, https://doi.org/10.1016/j.jfineco.2018.05.009 | SPY 1993–2013: predictive R² 1.6%, timing Sharpe 1.08 (6.67%/yr). Stronger on high-vol and high-volume days. | n/a | The signal sits in 15:30–16:00. Our 15:50 flat cuts that to 20 minutes. |
| RSI(2) short-term reversal (daily, SPY) | QuantifiedStrategies, https://www.quantifiedstrategies.com/rsi2-on-spy/ (rules: https://www.quantifiedstrategies.com/product/strategy38/) | Buy at RSI(2) < 15, exit on a close above the prior high: 402 trades, 0.6% average | **Win 73%, PF 2.3** | This is a daily, multi-day hold on an index ETF, not intraday. A high win rate comes with small wins and occasional large losses. |
| Gap fade | TraderPier, https://traderpier.com/market-replay/studies/how-often-do-gaps-fill and https://traderpier.com/backtesting/strategies/gap-fill-fade | 41.8% of 0.5%+ gaps filled the same day, only 9.1% of 4%+ gaps. A textbook gap fade lost to buy-and-hold on SPY, QQQ, IWM, AAPL and MSFT after costs. | n/a | Blog-grade evidence. Gap-and-go only works with a catalyst plus relative volume, which is the stocks-in-play idea above. |
| Overnight vs intraday | Cooper, Cliff & Gulen, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1004081 ; Lou, Polk & Skouras 2019 JFE, https://doi.org/10.1016/j.jfineco.2019.03.011 | The US equity premium is earned mostly overnight (close to open), while open-to-close is near zero. Many factor returns split oppositely between overnight and intraday. | n/a | This supports a selective long overnight hold. It argues against overnight shorts. |
| Retail day-trading base rate | Chague, De-Losso & Giovannetti 2019, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3423101 | 97% of persistent Brazilian day traders lost money, with no learning effect | — | A reality check. |

**Is a 62% win rate realistic?** Yes, but only as a byproduct of mean-reversion geometry (target closer than the stop, e.g. RSI(2)-type exits at 73%). It does not come with an edge on its own. The best-documented intraday edges (VWAP trend, ORB) win **17–45%** of the time and make their money on 2–6x payoffs. The bot's own history makes the same point: Pacer v1 showed 59–60% wins in 2023–24, and that was survivorship bias (see §1). **Design rule: optimize expectancy and PF after costs. Report win rate, never target it.** Variant B below is the honest route to about 60%. Its expected profile is a small average win and an average loss larger than the average win.

---
## 1. What's been tried on the VM (read-only review, 2026-11-02)
Sources: `/opt/marv-bot/state/pacer_pack/RESULTS.md`, `/workspace/pacer_review_20261102.md`, `/opt/marv-bot/state/reports/` (245 files), and the docstrings in `/opt/marv-bot/alpacabot/` (dayride.py, daytp.py, trendride.py, pacer.py, anglepacer.py).
- **Pacer v1** (RS leaders, daily, ATR TP zones). Since 2023: 170 trades, PF 1.18–1.22. Win rate 59–60% in 2023–24, falling to 49% in 2025 (PF 0.79). It beat random stocks only 12–13/20 times. Cause: a **survivorship-biased universe** (today's top 5 per sector) plus noise and no holdout. → *Design response: point-in-time universe (§2), a true holdout, controls drawn from the PIT universe, and sector-ETF/SPY controls.*
- **Trend-Angle Pacer** (limit entries on pullbacks to a fitted trend line). PF 0.50–0.65 across every market and timeframe. 25–31% of stock trades **filled and stopped on the same bar**, and late fills had PF ~0.2. Random market entries with the same geometry beat it. → *Design response: **no resting limit entries into a move.** Enter with a marketable order only after a bar closes in the signal direction (confirmation). Orders expire after 1 bar.*
- **Live "ten experts"** (reports/alpaca_validate_year_summary_orb_candidate.md): PF 0.65–0.78. Standalone ORB was PF 1.17 on 163 trades in train (2021H2) and PF 0.84 in validation (2022H1). → *ORB on all names unfiltered is not enough. Add a relative-volume (in-play) filter and the VWAP side, and test on ≥2 years.*
- **"day" family ORB grid** (alpaca_validate_day.json, train 2016–2020): 15-min ORB with a 1.5–2R target ran ~5,900 trades, win 44–47%, PF 1.01–1.06, avg R slightly negative. → *Fixed-R targets on an unfiltered ORB are about break-even. Use VWAP/trailing exits so the trend payoff can grow.*
- **dayride.py** (5-min strong-trend ride, 10:00–15:00, paper): a 2-week check gave +5.8R at 5 bps and **−9.4R at 10 bps**. That is cost-fragile. → *The 2x-cost test is mandatory.*
- Other failures to note: shorts were worse everywhere against an uptrending survivor list (PF 0.42–0.45), the steep-angle filter picked late or exhausted trends, and the bot has a history of grid-searching combos. → *Freeze the parameters below. Allow at most 3 parameters × 3 values on train only.*
- Forex (box200, Trend-Angle H1): random-entry PF 0.91, so the geometry plus costs is negative. Out of scope here.

---
## 2. Universe (point-in-time, no survivorship)
- At each **month-end M**, rank S&P 500 members **as of M** by market cap (historical shares outstanding × close) within each of the 11 GICS sectors. Take the **top 3 per sector**, about 33 names, and trade them during month M+1. Membership comes from a historical-constituents file (e.g. github.com/fja05680/sp500). Shares outstanding come from SEC XBRL `dei:EntityCommonStockSharesOutstanding` or Polygon/Alpaca fundamentals. Use the sector **as classified at M** (GICS changed in 2018 when Communication Services was created, and in 2023).
- Include names that were later delisted or acquired wherever bar data exists, and log any month where data is missing (`universe_log.csv`: month, sector, rank, symbol, mcap, data_ok).
- Also report results on today's static top-3 list, for comparison only. **A strategy that passes only on the static list is rejected.**
- **In-play filter (daily, within the 33):** at 10:00 ET, RVOL30 = volume 09:30–10:00 ÷ the average 09:30–10:00 volume of the prior 14 sessions. Eligible if RVOL30 ≥ 1.5. Rank by RVOL30 and take at most the top 5. Also require price > $5 and 20-day ADV > $50M (all 33 should qualify).
- Market and regime proxies: SPY and the 11 sector SPDRs (XLK, XLF, XLV, XLY, XLP, XLE, XLI, XLB, XLU, XLRE, XLC; XLC only exists from 2018-06).

## 3. Session rules
- New entries **only from 10:00:00 to 15:50:00 ET**, with the last entry at 15:45 (no entries in the final 5 min). **All positions flat by 15:50 ET** (marketable order at 15:50, escalate to market by 15:52) except those that pass the overnight swing rule (§6).
- No entries on FOMC decision days after 13:30. Half-days: entries until 12:45, flat by 12:50.
- **Max 4 new trades/day**, max 3 open at once, max 1 trade per symbol per day (a second is allowed only for the variant that explicitly permits re-entry).
- **Daily loss limit: −2R or −1.5% of equity**, whichever is hit first. When hit, flatten everything and stop for the day. Weekly limit −4%: halve size the next week.

## 4. Strategy definitions (parameters frozen; train-only tuning of at most the three marked ★)
All timestamps use 1-min bars, regular-session only. VWAP = cumulative Σ(typical price × vol)/Σvol from 09:30. ATRd = the 14-day daily ATR as of the prior close. Session σ = the stdev of (close − VWAP) since 09:30.

### PRIMARY — "ORB-30 + VWAP, in-play" (trend; expected win rate ~35–45%)
- Opening range (OR) = high/low from 09:30 to 10:00 ★(15/30/60; 30 is the default because entries start at 10:00).
- **Long:** an in-play name, then a 5-min bar **closes** above the OR high AND above VWAP. Enter with a marketable limit (ask + 0.05%, IOC) at the next bar open. If not filled within 1 minute, cancel. No resting buy-stop or limit orders.
- **Short:** a mirror image, plus the name must be shortable and easy to borrow (§7). Report it separately.
- Market filter: longs only if SPY > its VWAP at signal time. Shorts only if SPY < VWAP.
- **Stop:** the tighter of (the OR low for longs) and (entry − 0.10 × ATRd ★(0.08/0.10/0.15)), placed as a broker stop on fill. Skip the trade if stop distance < 0.15% of price (noise) or > 1.5%.
- **Exit:** a trailing stop. After +1R, move the stop to max(break-even, VWAP − 0.5σ). Exit on a 5-min close back through VWAP. Otherwise flat at 15:50 or swing (§6). **No fixed TP** (fixed 1.5–2R targets were PF ~1.0 in alpaca_validate_day.json).
- Signal valid until 12:00 ET (ORB edges decay through the day), at most one entry per name.

### VARIANT A — "VWAP reclaim, confirmed" (trend continuation; expected win rate ~40–50%)
- Setup: after 10:00, a name with price > VWAP and SPY > VWAP pulls back, so a 1-min low touches VWAP or VWAP − 0.5σ.
- **Trigger (the anglepacer fix):** enter only after a 5-min bar closes back **above VWAP and above the prior 5-min bar's high**, within 3 bars of the touch. Enter at the next open with a marketable order. Cancel the setup if price closes below VWAP − 1.5σ first.
- Stop: the low of the pullback − 0.05 × ATRd. Risk cap 1.0 × σ.
- Exit: a 50% scale-out at +1R, the rest trailing at VWAP (5-min close below) or flat at 15:50. Shorts mirror this; report separately.

### VARIANT B — "VWAP-band mean reversion" (this is the high-win-rate candidate; expected ~55–65%, avg win < avg loss)
- Only on non-trend days: |SPY return 09:30→10:00| < 0.3% and the name's ADX(14, 5-min) < 20 ★(20/25).
- **Long:** price closes a 5-min bar below VWAP − 2σ ★(1.5/2/2.5) and the **next** 5-min bar closes up (confirmation, no limit catching). Enter at the next open.
- Target: VWAP (a limit order at VWAP, updated every minute). Stop: entry − 1.0 × (VWAP − entry), i.e. about 1:1 geometry, or the session low − 0.05 × ATRd, whichever is closer. Time stop: 60 min.
- Shorts mirror this (above VWAP + 2σ). Report separately. It is expected to be worse in an uptrend.
- This is the most cost-sensitive variant (small wins). It must clear the 2x-cost gate.

(Optional diagnostic, not a tradable variant: Gao et al. intraday momentum on SPY. If the 09:30–15:30 return is > 0, long SPY 15:30–15:50. Report the result only to check whether the effect survives the 15:50 cut.)

## 5. Sizing
- Risk per trade = **0.5% of equity** (allowed range 0.5–1.0%; use 1.0% only after 60 paper days at PF > 1.1). Shares = floor(risk$ / stop distance).
- Caps: notional per trade ≤ 25% of equity. Total gross ≤ 100% of equity without margin (4x intraday buying power exists, but don't use it). Correlation cap: at most 2 positions in the same sector.

## 6. Overnight swing rule (LONGS ONLY; shorts are always flat by 15:50)
At 15:45 a long open position may be held overnight only if **all** of these hold:
1. Close at 15:45 is in the top 20% of the day's range (≥ HOD − 0.2 × (HOD − LOD)) and above VWAP + 0.5σ.
2. Strong trend: the trade is ≥ +1R open, daily close > the 20-day EMA > the 50-day SMA, and the 20-day return beats the sector ETF's.
3. Market regime: SPY > its 200-day SMA, SPY closes above its VWAP today, and VIX < 25 (or SPY 20-day realized vol < its 1-year median).
4. No earnings (before or after the bell) **tonight or tomorrow**, no FOMC/CPI/NFP the next morning, and no ex-dividend tomorrow.
5. **Size cap:** cut the held position to ≤ 50% of the original shares and ≤ 0.5% of equity at risk measured from the overnight stop. Total overnight exposure ≤ 30% of equity, at most 2 names.
6. **Gap stop:** the overnight stop = max(today's VWAP, entry). At 09:30 the next day, if the open < the prior close − 0.5 × ATRd, exit at market at 09:31. Otherwise manage it with the 10:00 OR-low stop and flatten by 15:50 day 2 (max 1 night, no rolling).
Log every swing decision with all criteria values, including declined ones. Report swing trades as their own bucket, comparing the overnight P&L against flattening at 15:50.

## 7. Alpaca specifics
- **Data feed:** the free plan is **IEX only** (one exchange, ~2–3% of volume). VWAP, RVOL and minute OHLC will differ from consolidated values. **Backtest on SIP** minute bars (historical SIP is available more than 15 min delayed; the VM already has `*_sip` caches). For live/paper signals, either pay for SIP (Algo Trader Plus) or log IEX and SIP values side by side for 20 paper days and measure the drift. Docs: https://docs.alpaca.markets/us/docs/about-market-data-api
- **PDT:** FINRA retired the PDT rule effective **2026-06-04**. Alpaca removed the $25K minimum and day-trade counting, replacing them with intraday buying power, intraday margin-deficit calls and pre-trade rejects (https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/). An account under $25K can therefore day trade. Code must use `buying_power`, **not** `daytrade_count`/`pattern_day_trader`/`daytrading_buying_power` (those fields were removed 2026-07-06). Handle order rejects for margin deficit. In a cash account, unsettled funds (T+1) still limit re-use, so check the account type.
- **Shorting:** requires a margin account (≥ $2K). Before every short, check `GET /v2/assets/{sym}` for `shortable` and `easy_to_borrow`. **Skip HTB names** and log "skipped_htb". In backtest, there is no historical borrow data, so apply a borrow fee of 0.25%/yr for intraday shorts (zero if flat, kept as a placeholder) and exclude any month where the name was on a known HTB list if available. Otherwise flag "borrow unverified". Short-sale restriction (SSR, a −10% day) means no short entries that day.
- Orders: bracket or OTO orders with a stop on fill, `time_in_force=day`. Swing holds get a GTC stop placed at 15:50. Use client_order_id = strategy-symbol-date-seq.
- **Paper first:** at least 30 trading days on paper with the frozen spec. Go live only if paper PF > 1.0, fills match the backtest slippage assumptions within 2x, and there are no unhandled rejects.

## 8. Backtest protocol
- Data: SIP 1-min bars, **2018-01-02 → 2026-10-30** (≥ 2 years is required; use all available data). Also daily bars for ATR, regime and earnings dates (earnings calendar point-in-time, e.g. from SEC 8-K item 2.02 dates or Alpaca/Polygon corporate actions). Split- and dividend-adjust consistently.
- **Walk-forward:** train 2018-01 → 2023-12 (parameters ★ chosen here only, ≤ 27 combos, chosen by train PF with a neighborhood check). **Holdout 2024-01-01 → 2026-10-30, touched once.** Also report rolling 12-month train → 6-month OOS folds.
- **Costs:** commission $0. Slippage 2 bps per side on entries/exits for large caps plus half the spread (use 1 bp if there are no quotes) = **3 bps/side base**. Stops fill at the stop price ± 3 bps or at the next bar open if it gapped through. SEC/TAF fees on sells. Borrow fee on shorts. **Stress at 2x costs (6 bps/side).**
- **Fill realism:** signals are computed on closed bars and filled at the next bar open. If the stop and target both fall within one bar, assume the stop was hit first. No look-ahead in VWAP/σ/RVOL.
- **Controls (20 seeds each):** (a) random-entry: the same number of trades, same names-per-day, same entry times-of-day distribution, same stop/exit rules, random direction-consistent entries from the PIT universe; (b) shuffled direction; (c) the same rules on the sector ETF and on SPY. **Must beat (a) and (b) in ≥ 16 of 20 seeds.**
- **PASS bar (each strategy, longs and shorts judged separately):** holdout PF > 1.1, ≥ 100 holdout trades, PF > 1.0 at 2x costs, train PF > 1.0, ≥ 16/20 random beats, and no single symbol > 25% of net R (drop the top 3 symbols and re-score; it must stay > 1.0). Must pass on the PIT universe.
- Report for each: trades, win rate, avg win R, avg loss R, expectancy R, PF, Sharpe (daily), max DD, trades/day, by year, by sector, by long/short, by entry hour, swing vs non-swing, PF at 1x/2x cost. **Win rate is reported, never the selection criterion.**

## 9. Logging (save everything)
Put all outputs under `/opt/marv-bot/state/vwap_daytrade/<run_id>/` with a `config.json` (exact params, git hash, data range, cost model).
- `trades.csv`: run_id, strategy, variant, side, symbol, sector, universe_month, signal_ts, entry_ts, entry_px, intended_px, slippage_bps, shares, stop_px, initial_risk$, R_multiple, exit_ts, exit_px, exit_reason (stop/trail/vwap/target/time/eod_1550/gap_stop/daily_limit), mfe_R, mae_R, fees$, borrow$, swing_flag, overnight_pnl$, rvol30, atr_d, vwap_at_entry, sigma, spy_vs_vwap, regime_flags, split (train/holdout), cost_mult.
- `equity_daily.csv`: date, start_equity, end_equity, realized$, unrealized_overnight$, gross_exposure_max, n_trades, n_wins, day_R, daily_limit_hit, overnight_positions, drawdown_pct.
- `signals.csv` (**every signal, including skipped ones**): ts, symbol, strategy, side, signal_values (OR hi/lo, vwap, σ, rvol30, adx), taken (bool), skip_reason (max_trades, daily_limit, htb, not_shortable, ssr, risk_too_small/large, sector_cap, outside_window, no_fill, regime_off, earnings), would-be R (simulated, for skipped signals).
- `swing_decisions.csv`: date, symbol, each §6 criterion value and pass/fail, decision, next-day gap %, outcome.
- `universe_log.csv` (§2), `controls.csv` (seed, strategy, PF, net R, beat), `summary.md` (pass/fail table per strategy × side × universe).
- Paper/live runs use the same schemas plus `orders.csv` (broker order ids, status, reject reasons, fill latency) and an IEX-vs-SIP VWAP drift column. **Never write API keys to any log.**
