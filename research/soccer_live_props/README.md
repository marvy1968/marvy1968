# Soccer in-game props (Marv-priced) — SHADOW only (2026-10-16)

Pricer: `marv/soccer_max/live_props.py` (bridge `GET /soccer_live?league=&team=<home>&other=<away>&hg=&ag=&minute=&ht=`).
Remaining goals per team ~ Poisson(Marv pregame xG × rem_frac(minute) × lead/trail multiplier). Params in
`state/marv_predict/soccer/live_props_calib.json` (copy here), fit on HT/FT results before 2026-08-21.

## Market availability (Odds API probe, 2 in-play games, 9 credits)
Bovada quoted none of btts / team_totals / alternate_team_totals / alternate_totals / player_goal_scorer_anytime /
draw_no_bet / double_chance live (FanDuel, BetMGM, DK, BetRivers, Pinnacle did). Bovada live soccer via the API =
featured h2h / spreads / totals only. Event-odds cost = 1 credit per market returned × 1 region unit (10 books = 1).

## Calibration (calib.py) — unseen 2026-08-21..09-20, 416 matches, HT state, 8 markets (2,442 open market-states)
Pooled Brier / log loss: Marv fit 0.1982 / 0.5799 · Marv with overlay's old 48/93 rule 0.2008 / 0.5860 ·
league-average xG baseline 0.2045 / 0.5949. Under-predicts goals in this window (2H goals 1.145× expected; train 1.02×).

## Backtest (backtest_bovada_live.py) — real Bovada live main-total quotes from March_edge alerts, Oct 9-14 2026
86 unique quotes (point-in-time Marv ratings). Marv EV>=5%: 44 bets 28-15-1, +6.00u, ROI +13.7% (43 Unders, 1 Over);
EV>=7% (⭐⭐): 40, 25-14-1, +4.69u, +11.7%; Marv EV<0: 31, -3.13u, -10.1%; all 86 quotes: +2.24u, +2.6%.
Small, 6-day, selection-biased sample (only swing-triggered quotes) — tracking evidence, not proof.
