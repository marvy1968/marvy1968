# muse1/alpaca — Stock Bot, Top 10 Experts + Top 5 Hedge-Fund Principles

**Paper only. Live trading is ALWAYS blocked (`--live` exits 2).**
**UNVALIDATED — see the honest caveats at the bottom before you run this
on any account, paper or otherwise.**

A new bot, built 2026-11-05 in `~/workspace/stock-bot-experts/`. It does
NOT touch `~/workspace/trading-bot/`, `~/workspace/oanda-trading-bot/`, or
any Claude VM code. One runner (`bot.py`) with three sleeves — scalp,
day-trade, swing — driven by 10 expert signal functions and a hedge-fund
principles overlay.

**Version 0.2.0 (2026-11-05) — Claude fix list applied.** See
"v0.2.0 — what changed" below. Real bracket exits, `state.json` +
lock file, fail-closed broker reads, `feed=iex` + completed bars only,
market calendar, one expert log-only (`raschke_soup`), flatten 15:50 ET.
The bot MUST run on its OWN Alpaca paper account, separate from Brain
(see DEPLOY.md) — running it on Brain's account is not supported.

## How to run

```bash
python3 bot.py --help
python3 bot.py                      # NO FLAG: does nothing (exit 0, no scan, no orders)
python3 bot.py --dry-run            # real Alpaca bars if keys are present, synthetic demo bars if not; places no orders, writes no state
python3 bot.py --paper              # paper keys via ALPACA_API_KEY / ALPACA_API_SECRET env or .env; takes the lock file
python3 bot.py --live               # ALWAYS refuses, exit code 2
```

Keys are never hard-coded and never printed. No `.env` file is shipped
in this directory. **This bot must run on its OWN Alpaca paper account,
separate from Brain's** — the VM `.env` must hold muse1-only paper keys
(see DEPLOY.md). Running it on Brain's account is not supported.

## Files

| File | What it does |
| --- | --- |
| `config.yaml` | Watchlist, sectors, sleeve settings, risk caps, overlay params, SPY filter (ON, vs_open), strategy (min_reward_risk 1.5), rollout (log-only: raschke_soup), data (feed=iex) |
| `experts.py` | The 10 expert signal functions + sleeve assignment + `scan()` |
| `hedge_fund_overlay.py` | Hedge-fund principles as code: vol sizing, portfolio limits, stress test, exit policy, signal-decay exit, cost model |
| `risk.py` | Shared `RiskState`: max open, max new day trades, daily loss cap, day P&L in R and $ (seedable from state.json) |
| `bot.py` | Runner: argparse, live-block, Alpaca paper broker / stub broker, bracket orders, state + lock, calendar, one scan over all sleeves |
| `state.json` | LOCAL ONLY, never committed: day P&L, trade count, Muse1 positions with order ids, swing signals seen. See `.gitignore`. |
| `test_fixes.py` | Unit tests for the v0.2.0 fix list (37 checks, no keys, no network, no orders). Run: `python3 test_fixes.py` |
| `DEPLOY.md` | VM install, schedule, MSFT handover, separate-account requirement |

## Expert-by-expert mapping

Adapted from the fx-experts systems (see `experts.py` header for the
locks). FX "pips" become ATR units; FX "daily" becomes the bar timeframe
in use (5-min for scalp/day, daily for swing). `tharp-random` is excluded:
it is a coin flip, not an expert system.

| # | Expert | What it does | Timeframe | Sleeve | Stop / exit (lock) |
| --- | --- | --- | --- | --- | --- |
| 1 | Lien Fader | Fade a close beyond the Bollinger band back to the mean | 5-min / daily | day | Stop ~1 ATR, TP 2R, no trail |
| 2 | Raschke Soup | Failed 20-bar breakout, trade the failure | 5-min / daily | day, swing | Stop beyond extreme, trail to prior extreme, flat after 6 bars, no TP |
| 3 | Faith Turtle | 20-bar breakout trend | 5-min / daily | day, swing | Stop 2 ATR (2N), exit at 10-bar low/high, no TP |
| 4 | Bollinger Squeeze | Squeeze (bandwidth at 10-bar low) then expansion breakout | 5-min | scalp | 2-ATR trail (PSAR proxy — the original Yahoo PSAR run was buggy and discarded), no TP |
| 5 | Crabel IDNR4 | Narrowest range in 4 bars + inside day, then the NEXT bar must CLOSE beyond the narrow bar (long above its high, short below its low) | 5-min | scalp | Flat at close, stop = opposite side of the narrow bar, no TP |
| 6 | Lien Zeros | Bounce / rejection off a whole- or half-dollar level | 5-min | scalp | Stop ~0.75 ATR, TP 2R |
| 7 | Williams VBO | Range >1.5× average + breakout | 5-min | scalp | Bailout at first profitable close, stop 50% of prior bar's range, no TP |
| 8 | Ponsi Trend | Uptrend (EMA20>EMA50), pullback to the PRIOR bar's EMA10, close back in trend direction | 5-min / daily | day, swing | Breakeven after ≥1R close, then trail EMA10 − 0.5 ATR, never loosened, no TP |
| 9 | Brooks Range Fade | Failed breakout beyond a 20-bar range, fade to midpoint — only if reward / actual-stop-risk ≥ 1.5 | 5-min | day | TP range midpoint, stop beyond signal bar, flat at close |
| 10 | Dalton Value | Failed break beyond value area (mean ± 1 ATR), trade to opposite edge — only if reward / actual-stop-risk ≥ 1.5 | 5-min / daily | day, swing | TP opposite value edge, stop beyond failed extreme, flat at close |

## Hedge-fund principle mapping

The top 5 funds publish **no entry rules**. What follows is principles
only — do not read any of it as "Renaissance trades this way on this bot."

| Fund | Principle (documented) | Where in code |
| --- | --- | --- |
| Renaissance Medallion | Many small signals combined, diversified, optimizer-driven | `experts.py: combine via scan()` + `hedge_fund_overlay.combine_signals()` — signals ranked by agreement, no single expert decides the book |
| Two Sigma | Factor forecasts + factor-exposure control | `hedge_fund_overlay.check_portfolio_limits()` — sector caps, net-exposure cap (sector = factor proxy here) |
| D.E. Shaw | Market-neutral / stat-arb, integrated risk | `check_portfolio_limits()` net long-minus-short cap; risk checked at portfolio level in `risk.py`, not just per trade |
| Citadel | Stress tests, exposure limits, dynamic hedging | `hedge_fund_overlay.stress_test()` (worst case if all stops hit), `max_notional_pct` 25% cap, daily loss cap = flatten / no-new-trades |
| Man AHL / AQR | Trend following, volatility-targeted sizing, no fixed TP, exit on signal fade | `vol_scaled_size()` (fixed $ risk / ATR stop), `exit_policy()` (no fixed 1R TP; 2.5–3R or no-TP + breakeven after +1R), `signal_decay_exit()` (time exit + wide 2-ATR disaster stop), `costs()` (costs modeled before trading) |

## Sleeves

- **Scalp** — 5-min, intended hold ≤30 min, risk 0.25% (smallest).
  ⚠️ **Weakest sleeve by evidence:** the 2026-11-05 Brain audit found
  scalps ≤30 min expectancy **-0.308R** (n=180) vs day holds >30 min
  **+0.554R** (n=52). Scalp is included because you asked for it, not
  because the evidence supports it. Consider keeping it off, or paper-
  testing it separately, until it proves itself out-of-sample.
- **Day-trade** — 5-min, window 10:00–15:45 ET, flatten 15:50 ET,
  risk 0.5%, max 5 open, max 5 new/day.
- **Swing** — daily bars, signals after close, fills at next open,
  risk 0.5%, max 5 slots, stop EMA20 − 1 ATR, take-profit 2R
  (the Top-Down-style 2R TP, the one keep-style result in the swing lab),
  max hold 10 days, EMA20-break exit.
- **Shared risk:** daily loss cap 2% (no new trades after it), max 5 open
  day/scalp, max 5 open swing, max 2 per sector, max 25% notional per
  position, max 60% net exposure (net counts the new order too).

## v0.2.0 — what changed (Claude fix list, 2026-11-05)

1. **Real exits.** Every entry is submitted as ONE bracket order
   (entry + stop + target, `order_class="bracket"`, unique
   `client_order_id` = `muse1-<uuid>`). If the submit call fails, the
   order status is verified by `client_order_id` before deciding what
   happened — never assumed. Fills and open positions are tracked
   between runs in `state.json` (see below). Breakeven move (stop to
   entry after +1R), stair trail (stop tightens to peak − 0.5R), max
   hold (scalp 30 min, swing 10 days), signal-decay exit, and swing
   2R/EMA20 exits are enforced every run via stop-leg replacement.
   Flatten for day/scalp is **15:50 ET** (config/README/DEPLOY all
   agree; the entry window stays 10:00–15:45 ET).
2. **Saved state, no duplicates.** `state.json` (local only, in
   `.gitignore`, never committed) holds day P&L in $ and R, trade
   count, Muse1 positions with entry/stop/target order ids, and swing
   signals already acted on. Realized P&L is read from Alpaca
   (closed-order fills) so the 2% daily stop works across runs.
   Every order gets its own `client_order_id`; open orders are checked
   before placing new ones; a `bot.lock` file (flock) stops overlapping
   runs; repeat or opposite orders on a held stock are blocked; swing
   enters once per signal (deduped in `state.json`).
3. **Stop on errors.** If reading positions or the account fails, the
   run is skipped (exit 3) — the account is never assumed empty.
   The exposure check counts the new order's size AND direction
   (net exposure after the order). A stock is checked `shortable`
   before shorting (fail-closed: if the check fails, no short).
   API calls retry (up to 3 attempts, backoff) and order status is
   checked after any timeout. Note: alpaca-py 0.44.0 exposes no
   per-call HTTP timeout, so "timeout" here means retries +
   verification by `client_order_id`, not a socket timeout.
4. **Data and sessions.** Bar requests use `feed=iex` explicitly, with
   start/end windows (5 days of 5-min bars, 90 days of daily) and
   completed bars only (the forming bar is dropped). Market calendar:
   Alpaca `get_calendar()` first (via `GetCalendarRequest`), static
   NYSE holiday/half-day list as fallback; holidays skip the run,
   half-days flatten at 13:00 ET. `--paper` is required to trade;
   with no flag the bot does nothing (exit 0). `--dry-run` uses REAL
   Alpaca bars when keys are present (synthetic demo bars only when
   they are not) and places no orders, writes no state, takes no lock.
5. **Strategy fixes (the actual defects found in `experts.py`):**
   - **Crabel** signalled long on the setup bar alone (the narrow
     inside day), before any breakout — and never signalled short.
     Now it waits for the NEXT bar to CLOSE beyond the narrow bar
     (long above its high, short below its low, stop = opposite side).
   - **Brooks / Dalton** had no check that the target was worth the
     risk (the locks' "reward ≥ risk" was never enforced). Now both
     refuse entry unless reward / actual-stop-risk ≥ **1.5**
     (`strategy.min_reward_risk`, frozen in `config.yaml`). 1.5, not
     1.0, because round-trip costs eat the edge at 1.0.
   - **Ponsi EMA timing:** the pullback test compared the prior close
     to TODAY's EMA10 (which already includes today's close) — a
     bar could "pull back" to an EMA that had already moved to meet
     it. Now the prior close is compared to the PRIOR bar's EMA10.
   - **Bollinger squeeze:** the bandwidth comparison used mismatched
     windows (current 20-bar window vs prior windows ending 1–10 bars
     ago, computed on different bar counts) and judged the squeeze on
     the current bar, which the expansion bar itself widens — so a
     genuine squeeze + breakout could fail by construction. Now both
     sides use the same 20-bar window, and the squeeze is judged on
     the bandwidth through the PRIOR bar (at/near its 10-bar low),
     with the breakout judged on the current close vs the band.
   - Cost checks use the actual size and stop (`qty × stop distance`),
     not a fixed estimate.
   - **Rollout: ONE expert in log-only mode — `raschke_soup`.** It is
     the only expert with a real-data record on two engines (Yahoo H1
     PF 1.17–1.37; 10-year Dukascopy PF 1.02, n=1,941). Every other
     expert is off (`rollout.live: []`) until it passes a
     pre-registered backtest. **The backtest with realistic costs is
     still owed — it has not been run and nothing here claims it has.**
6. **Account.** The bot MUST use its own Alpaca paper account,
   separate from Brain. `DEPLOY.md` documents that the VM `.env` must
   hold muse1-only paper keys. **Creating that paper account is the
   one thing only the user/Claude can do** — Muse cannot create it.
7. **MSFT short (pre-existing).** Short x17 @ $537.05, entered
   2026-11-05, with a real parent-placed BUY STOP order
   `9a5b7fb5-ada0-4c34-b143-43411cca822b` @ $539.83 (GTC, ACCEPTED).
   Recorded in `state.json` as a PROTECTED position: the bot monitors
   it only, never cancels/replaces its stop, and it is exempt from
   auto-flatten. TSLA's closed short is recorded too (realized
   −$81.67, −1.31R, 2026-11-05).

**Bugs found by testing v0.2.0 and fixed in the same pass:**
- `AlpacaBroker.get_calendar()` called `client.get_calendar(start, end)`,
  which alpaca-py 0.44.0 rejects (`TypeError: unexpected keyword
  argument 'start'`) — every run silently fell back to the static
  calendar. Fixed with `GetCalendarRequest`; verified live (Nov 27
  half-day close 13:00, Nov 26 holiday empty).
- The stress test read $0 risk for broker-tracked positions (their
  `risk_dollars` was hardcoded 0.0); it now takes the risk from the
  state record when the position is tracked (MSFT: −$47.26).
- `RiskState.seed()` did not restore `day_pnl_r`, so the risk report
  showed 0.0R while the state held −1.31R. Fixed.

**Tests run 2026-11-05 (all passed, no real orders placed):**
`python3 test_fixes.py` — 37/37 checks (Crabel/Ponsi/squeeze/R:R
cases, exposure counting the new order, virtual trail math, lock
overlap, state save/load/rollover, SPY filter, window, CLI, RiskState
seed, calendar incl. Alpaca-first + static fallback). `bot.py --help`
exit 0; no-flag exit 0 doing nothing; `--live` exit 2; `--dry-run`
with real Alpaca bars (feed=iex, equity $51,449, SPY short_only, 0
live signals, 3 log-only raschke_soup, MSFT PROTECTED holding, risk
day_pnl −$81.67 / −1.31R, stress −$47.26); `--paper --dry-run`
identical, no state write; two overlapping `--paper` runs — the
second exited 0 with "Another run holds the lock"; a second dry run
saw the first run's state (MSFT tracked once, trades_today still 2).
Open orders after all tests: only the parent-placed MSFT stop.

## HONEST CAVEATS — read before running this anywhere

1. **These experts were tested on FX, and many FAILED out-of-sample.**
   On Yahoo FX H1 they looked good (Ponsi PF 1.57–1.62, Raschke 1.17–1.37);
   on Claude's real OANDA engine they did not transfer (Ponsi PF 0.46–0.55
   on real boxmr entries; all 22 exits in `fx_exits_muse_all` lost OOS;
   boxmr_h1 5-year PF 0.51). The daily Raschke Yahoo PF 4.54 contradicts
   the 10-year Dukascopy PF 1.02. Do not assume FX results carry to stocks.
2. **The stock sleeves this borrows from are also losing.** Optimised Trend
   PF 0.40 (Claude minute lab) / 0.44 (66 sessions), Velez PF 0.59, VWAP
   reclaim 0.50 / band 0.64 — all log-only since 2026-11-04/05 for that
   reason. No exit of 18 fixed any of them.
3. **Hedge funds publish no entry rules.** Renaissance, Two Sigma,
   D.E. Shaw and Citadel disclose philosophy and risk practice, not
   tradable signals. The overlay here is principles (sizing, limits,
   no tight TP, costs first) — it does not make this bot a fund.
4. **This bot is UNVALIDATED.** No out-of-sample backtest has been run
   on these experts on stocks, and no paper forward test has been run.
   The `--dry-run` uses real Alpaca bars when keys are present
   (synthetic demo bars only without keys) — it exercises the code, it
   proves nothing about edge. It needs: (a) an out-of-sample backtest
   on Alpaca bars with costs, pre-registered pass bar (PF ≥1 on both
   validation and holdout, beats baseline and random, trade count does
   not collapse); (b) a paper forward test on a *separate* paper
   account; (c) only then any discussion of live use.
5. **SPY filter is ON by owner override 2026-11-05** in `config.yaml`
   (`spy_filter.enabled: true`, mode `vs_open`). Rule, implemented in
   `bot.py:spy_side()`: SPY up (last close > today's open) = longs only,
   SPY down (last close < today's open) = shorts only, flat = both sides.
   Applies to all three sleeves; blocked signals are logged as
   `spy_filter` skips. WARNING: both SPY ideas failed pre-registered
   tests on the OTHER sleeves on 2026-11-05 (SMA20 regime: Trend
   0.40 → 0.33, Top-Down 1.51 → 1.46; SPY vs today's open: Trend
   halves 0.32/0.55 → 0.51/0.36). This filter has NOT been proven on
   this bot either — it is on because the owner asked for it.
   Undo: `spy_filter.enabled: false` in `config.yaml`.
6. **SPY flat = range-trade / scalp** (owner 2026-11-05). Flat is
   |SPY last close - today's open| / open < **0.15%**. When flat:
   trend + breakout experts are off (ponsi_trend, faith_turtle,
   raschke_soup, bollinger_squeeze, crabel_idnr4, williams_vbo);
   range experts only (lien_fader, brooks_range_fade, dalton_value,
   lien_zeros); scalp preferred, day range-only, swing off.
   This is a new, untested rule on an unvalidated bot — it has not
   been backtested. Threshold is in `config.yaml`
   (`flat_threshold_pct: 0.15`).
7. **Sizing on available equity** (owner 2026-11-05): available equity
   = account equity − notional already committed to open positions
   (in-scan + real broker positions). Risk dollars and the 25%
   notional cap are computed on available equity, so each new trade
   is smaller as the book fills. In the dry-run, the 2nd signal sized
   off ~$75k (after the 1st $24,879 was committed), the 3rd off less.
8. **Day/scalp window 10:00–15:45 ET** (owner 2026-11-05, was 09:45).
   No new intraday trades outside it; flatten 15:50 ET (v0.2.0).
9. **Swing carryover** (owner 2026-11-05): swing trades are NOT
   flattened at day end. They carry to the next day while price is
   still on the right side of EMA20, and exit on stop, 2R TP, EMA20
   break, or signal decay.
10. **Do NOT deploy, do NOT run live, do NOT start it on a real account.**
    Paper only, separate account, backtest first.
