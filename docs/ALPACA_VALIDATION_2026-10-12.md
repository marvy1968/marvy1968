# Alpaca bot: validation run (SIP data), 2026-10-12

Train 2016-2020, validate 2021-2022, 10 symbols (SPY QQQ IWM AAPL MSFT NVDA AMZN META TSLA AMD), costs 2 bps slippage per side + sell
fees, 1% risk per trade. Run stopped at its 10-hour cap during the last family (scalp); every other family finished.

| Family | Validation result | Verdict |
|---|---|---|
| swing RSI pullback | 257 trades, 63% win, +0.003R avg, CAGR +0.4% (SPY +3.3%) | no edge |
| swing breakout | 47 trades, +0.12R avg, CAGR +2.7% (SPY +3.3%), max DD -9% | no edge (too few trades, below SPY) |
| day (opening-range break) | 2,289 trades, +0.014R, CAGR -1.2%; random entries did better (beat share 0%) | no edge |
| dual regime (staircase exits) | 9,422 trades, -0.13R, -0.34%/day, max DD -82% | loses |
| brain (10 experts, long) | no expert positive on the train years -> stays flat | no edge |
| brain long+short with heat map | no expert positive long or short -> stays flat | no edge |
| scalp (1-minute VWAP) | not finished (time cap) | pending |

Every expert, train years 2016-2020, after costs (expectancy per trade, % of notional; profit factor):

| Expert | Long | Short (heat-map gated) |
|---|---|---|
| velez_elephant | -0.038%, PF 0.82 (7,073) | -0.094%, PF 0.70 (1,176) |
| velez_180 | -0.032%, PF 0.79 (20,375) | -0.024%, PF 0.91 (3,017) |
| raschke_grail | -0.044%, PF 0.72 (4,541) | -0.053%, PF 0.78 (662) |
| brooks_pullback | -0.043%, PF 0.68 (13,248) | -0.017%, PF 0.92 (1,874) |
| connors_rsi2 | -0.030%, PF 0.69 (18,856) | -0.030%, PF 0.82 (2,515) |
| bollinger_bounce | -0.049%, PF 0.77 (10,997) | -0.053%, PF 0.79 (1,926) |
| williams_r | -0.059%, PF 0.67 (27,315) | -0.065%, PF 0.71 (4,305) |
| donchian_turtle | -0.047%, PF 0.70 (9,661) | -0.069%, PF 0.70 (1,652) |
| vwap_dev | -0.053%, PF 0.72 (15,477) | -0.051%, PF 0.76 (2,941) |
| dual_regime | -0.046%, PF 0.70 (27,804) | -0.062%, PF 0.70 (4,713) |

Round-trip cost is about 0.044% of notional, so before costs the experts are near zero (roughly -0.02% to +0.01%): the 5-minute technical
signals have no measurable gross edge on these liquid names, and costs turn that into a steady loss. Nothing passed, so nothing was engaged and
the final 2023-today test was not run (it is spent only on a configuration that passes validation).
