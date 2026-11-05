# Alpaca Crypto ROI Bot

A paper-first, long-only relative-value bot for Alpaca spot crypto.

## Why long-only?
Alpaca crypto is spot/cash trading. Classic pairs trading buys one leg and shorts the other; this bot does not claim market neutrality. It estimates a rolling log-price relationship against BTC, buys only a qualified statistically cheap asset, and exits when the spread normalizes or risk controls trigger.

## Default universe
BTC/USD is the benchmark. ETH/USD, DOGE/USD, LINK/USD, and AVAX/USD are candidate legs. The bot should always confirm current tradability using Alpaca's Assets API before live use. Symbols are editable in `config/config.yaml`.

## Signal stack
1. Rolling OLS hedge ratio on log prices.
2. Spread z-score.
3. Augmented Dickey-Fuller stationarity screen.
4. Mean-reversion half-life filter.
5. Return-correlation floor.
6. Fast/slow EMA market regime filter.
7. RSI and volatility sanity checks.
8. Bid/ask spread execution filter.

## Risk stack
- 0.5% equity risk budget per trade.
- 15% maximum per asset.
- 55% maximum aggregate crypto exposure.
- Three simultaneous positions maximum.
- ATR stop, ATR trailing stop, and 2.5R target.
- 2% daily loss lockout.
- 8% peak-to-equity drawdown lockout.
- 90-minute cooldown after three consecutive losses.
- Paper mode and dry-run enabled by default.

## Installation
```bash
cd ~/alpaca_crypto_roi_bot
bash install.sh
nano .env
.venv/bin/python roi_bot.py scan
.venv/bin/python roi_bot.py backtest
```

To permit Alpaca paper orders, set `execution.dry_run: false` while leaving `ALPACA_PAPER=true`.

## Systemd timer
```bash
mkdir -p ~/.config/systemd/user
cp alpaca-crypto-roi.service alpaca-crypto-roi.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now alpaca-crypto-roi.timer
systemctl --user status alpaca-crypto-roi.timer
```

## Book principles incorporated
- *Trading in the Zone* — predefined risk and probabilistic thinking.
- *Market Wizards* — protect capital and specialize in a repeatable edge.
- *Reminiscences of a Stock Operator* — trade with the broader trend; avoid averaging down.
- *Technical Analysis of the Financial Markets* — regime, momentum, and volatility confirmation.
- *Systematic Trading* — explicit forecasts, position sizing, diversification, and robustness.
- *Quantitative Trading* — testable rules, transaction costs, and operational simplicity.
- *Algorithmic Trading* — mean reversion, stationarity, and execution-aware design.
- *Pairs Trading* — spread construction, hedge ratio, z-score entry/exit.
- *Advances in Financial Machine Learning* — leakage avoidance and walk-forward validation principles.
- *Fooled by Randomness* — distrust small samples and optimize for survival, not a pretty backtest.

## Important
No bot can guarantee ROI. Backtest with realistic costs, then paper trade for a statistically meaningful sample before considering live capital.
