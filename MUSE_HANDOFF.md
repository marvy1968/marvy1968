# Alpaca stock bots: handoff to Muse (2026-11-05)

Written by Claude Code for Marvin's new builder. No keys or secrets are in this file. Everything is **paper trading**.

## 1. State right now (Nov 5, 12:25 ET)

- Alpaca paper equity **$51,417**, **all cash: 0 positions, 0 open orders**. Flattened on the owner's order: "Flatten and pause alpaca bots it losing again."
- **Everything that places Alpaca orders is paused**:
  - Brain (intraday): kill switch ON and `paper-pause on`.
  - Top-Down swing: `swing.topdown.new_entries = false`.
  - Stair-step swing: `swing.stairstep.new_entries = false`.
  - Funnel, oversold, weekly picks, day ride and the SPY/QQQ 200-day sleeve were already off.
- **Log-only** (they log signals and never place orders): VWAP reclaim/band, Velez, Optimized Trend.
- **Not Alpaca, still running**:
  - OANDA forex practice bot (`fxbot/`, `marv-fx-paper.service`).
  - The owner's betting bots: Marv (`marv/`), the March_edge bridge, Kalshi/Poly/AJ.

  **Do not touch the betting bots.** The owner has asked repeatedly that they are not restarted or changed.
- Swing book record since it started: 32 closed trades, 11 W / 21 L, **-$1,826**.
- Brain record (audit): 306 trades, PF 0.74, -39.5R.
  - Longs -50.2R, shorts +10.7R.
  - Kill switch on since Oct 26.

## 2. Where everything lives

- **Repository**: github.com/marvy1968/marvy1968, branch `claude/analysis-ak180w`. Stock code is in `alpacabot/`.
- **VM**: Google Compute Engine, bot installed at `/opt/marv-bot`, runs as user `marvbot`. Installed by `deploy/install.sh`.
- **Keys**: `/opt/marv-bot/.env`, never committed. The code reads `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`, or the `ALPACA_KEY_ID` / `ALPACA_SECRET` variants, plus optional `ALPACA_DATA_KEY_ID` / `ALPACA_DATA_SECRET_KEY` for market data.
- **State, journals and ledgers**: `/opt/marv-bot/state/`.
  - `alpaca_paper/`: intraday Brain. `journal-YYYYMMDD.jsonl` and the trade ledger.
  - `alpaca_swing/`: swing book.
  - VWAP, Velez and Optimized Trend sleeves each have their own folders.

### Services (systemd)

| Unit | What | When |
|---|---|---|
| `marv-alpaca-paper.service` | `python -m alpacabot paper`: the intraday Brain, plus the VWAP / Velez / Trend sleeves and swing stop upkeep (`swing_paper.ensure_stops`) | always on |
| `marv-swing-scan.timer` | `python -m alpacabot swing scan`: Top-Down / stair-step candidate scan | Mon-Fri 16:20 ET |
| `marv-swing-open.timer` | `python -m alpacabot swing open`: places the planned swing orders (OCO stop + take-profit) | Mon-Fri 09:20 ET |

### Main files

- `alpacabot/paper.py`: the intraday runner.
  - Safety rails: 2% bot breaker, account -1% breaker, 1% position cap, 10-minute under-water exit, same-expert 10-minute cooldown, 3:50 flatten.
  - Gates: sector heat map and sector news.
- `alpacabot/brain.py`, `experts.py`, `engine.py`: the Brain, its experts, and the shared backtest engine. `simulate_day` and `ladder_walk` are bar-by-bar exits; shorts are simulated on the mirrored series.
- `alpacabot/brain_config.json`: which experts run, long and short.
- `alpacabot/zones.json`: **all live switches**, under `intraday` and `swing`. Every change carries a dated `_note` with its evidence and an UNDO.
- `alpacabot/swing_paper.py`: the swing book.
  - Top-Down entries with a 2R take-profit (OCO at the broker).
  - Broker stops on every position.
  - Gates: SPY 200-day long gate, SPY regime (on by owner order Nov 5), market-news gate, Seeking Alpha log.
- `alpacabot/strategies.py`: `SectorTopDown` / `GatedSectorTopDown`.
- `alpacabot/stairlive.py`, `stairstep.py`: stair-step.
- `alpacabot/vwaplive.py`, `velez.py`, `trendopt.py`: the log-only sleeves.
- `alpacabot/research.py` and `cli.py`: every backtest family. `python -m alpacabot run-all --phase validate --families <name>`.
  - Short-test flags: `--weeks2`, `--month1`, `--months3`.
- `tests/`: about 790 unit tests. Run `python -m unittest discover -s tests` (about 9 minutes) **before every deploy**.

### Switching things on and off

- **Brain**: `python -m alpacabot paper-pause on|off`. The kill switch and its expert lists are in `brain_config.json`.
- **Swing strategies**: `zones.json` → `swing.topdown.new_entries` / `swing.stairstep.new_entries` (true/false). Held positions keep their broker stops.
- **Close the swing book**: `python -m alpacabot swing close [--symbols A,B]`.
- **Close intraday positions**: `python -m alpacabot paper-flatten`.
- **Read-only checks**: `python -m alpacabot positions`, `paper-report`, `paper-trades --by-expert`, `equity-vs-spy`.

## 3. Owner's rules (please keep them)

1. **Paper only.** Keep `PAPER_MODE` on unless Marvin explicitly turns it off.
2. **Never print or commit secrets** from `.env`.
3. **Never edit `deploy/install.sh` or the betting code; don't restart the betting bots.**
   - `install.sh` leaves the betting services running only when every changed file matches its stock/forex list: `alpacabot/`, `fxbot/`, `tests/test_alpaca*` / `test_fx*` / …, `deploy/vm_link.py`, root `*.md`.
   - Name new stock test files `tests/test_alpaca_*.py`.
   - Never modify `tests/test_brain_shorts.py`, because any change to it restarts the betting services.
4. **Keep tests short** (owner, Nov 5): 2 weeks to 3 months, split into two halves. Run a longer test only after a short one passes.
5. **Pre-register every test.** Fix the pass rule before seeing results, and include:
   - both halves
   - costs doubled
   - a random or shuffled control
   - 30+ trades where possible

   If a test fails, reject the idea ("if it lowers PF, reject it, no second guessing").
6. Changes to the owner's other bots only with his approval, a backup and an undo.

## 4. What has been tested (so you don't repeat it)

### Passed
- **Top-Down fixed 2R take-profit** (long test, Nov 4).
  - Unseen data: PF 1.50 (+58.9R) vs 1.22 for its old exit.
  - Hindsight-free 2016 stock list: PF 2.16 vs 1.35.
  - Applied.
- **Velez first target 1.9R** (small edge). Applied, but Velez is log-only.

### Failed (rejected)
- **Shorts-only Brain, 2 weeks (Nov 5).** 56 trades, PF 1.27 overall but:
  - week 1 PF 0.80
  - PF 0.97 with costs doubled
  - avg R +0.05 vs **+0.16 for random-timing shorts**

  The falling market did the work, not the experts.
- **Top-Down sizing, last 3 months (Nov 5).** No larger size passed. **Top-Down itself lost in the last 3 months**:
  - today's list: PF 0.55 / 0.59 in the two halves
  - 2016 list: 1.49 then 0.46

  More size made it worse every time.
- **SPY regime filter** (long only above a rising SMA20, short only below a falling one): Top-Down OOS PF 1.51 → 1.46 and 2.17 → 1.96; Optimized Trend 0.40 → 0.33.
  - It is nonetheless ON in the swing config by owner order (Nov 5).
- **SPY vs today's open**: Trend's halves flipped (0.32/0.55 → 0.51/0.36); VWAP reclaim/band got worse. Off.
- **Top-Down short side on SPY-down days**: failed. Top-Down stays long-only.
- **Exit studies**: Muse's exit list (Ponsi, Raschke, Turtle, weekly stair, Crabel/Williams controls) and 14 older exits.
  - Daily swing: only the fixed 2R passed.
  - Minute bars (VWAP / Velez): all failed.
- **Optimized Trend**: PF about 0.40-0.44. Log-only.
- **VWAP reclaim/band and ORB**: lost; ORB removed. Log-only.
- **Velez sleeve**: lost. Log-only.
- **Swing funnel / oversold / weekly picks**: lost; off.
- **Stair-step**: holdout PF 1.04 on 18 longs. On by owner override, now paused.

### Built but not finished
- **Brain targets/stops test** (`brain_exits` family, `research.brain_exits_study`, last 3 months, live configuration). Pre-registered from the hedge-fund plan (Man AHL ideas):
  - Test 1: 2.5R target, 3R target, or no target with break-even at +1R then a 1R trail.
  - Test 2: 1.5 ATR or 2.0 ATR stops with risk-based size.

  Pass rule:
  - PF ≥ 1.0 in both halves and above live in both halves
  - PF ≥ 1.0 with costs doubled
  - avg R above random timing
  - at least half live's trades

  The first VM run hit its 2-hour limit. It is now set to the long + short book only, and it checkpoints, so a rerun resumes.

### Known traps
- **Hold-time and MFE splits are after-the-fact.** You only know how long a trade lasted, or how far it ran, after it closes. "Day holds >30 min win, scalps lose" is survivorship and can't be used as an entry filter.
- **The live Brain's real target is about 0.33R**, not 1R. The Velez-hybrid rule puts the stop at 1.5× the expert's risk and the target at 0.5× it.
- **The 1% notional cap** (`max_pos_frac`) limits position size. A wider ATR stop with fixed dollar risk would hit this cap, so real risk per trade shrinks. The backtests don't apply the cap.
- **Account vs SPY**: the account has not beaten simply holding SPY.

## 5. Honest summary

No Alpaca strategy currently has a positive recent record that holds up on unseen data and against random entries:
- Top-Down's long-run edge (PF 1.5) has been negative for the last 3 months.
- The Brain loses on the long side and shows no timing skill on the short side.

The owner's goals of 1%/day or a 62% win rate have never been met in any test, and should be treated as targets, not results.
