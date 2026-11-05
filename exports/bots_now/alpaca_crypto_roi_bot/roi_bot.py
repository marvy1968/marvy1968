#!/usr/bin/env python3
"""Alpaca Crypto ROI Bot.

Long-only relative-value system designed for Alpaca spot crypto. It does not
pretend to be market neutral: Alpaca crypto is cash-only, so the bot buys the
statistically cheap leg only and exits on normalization, stop, target, or
portfolio risk limits.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from collections import defaultdict

import numpy as np
import pandas as pd
import yaml
from dotenv import load_dotenv
from statsmodels.tsa.stattools import adfuller

from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, CryptoLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "runtime.json"
TRADE_LOG = ROOT / "logs" / "trades.csv"


@dataclass
class Signal:
    symbol: str
    benchmark: str
    timestamp: str
    price: float
    zscore: float
    beta: float
    correlation: float
    adf_pvalue: float
    half_life: float
    atr: float
    atr_pct: float
    rsi: float
    trend_ok: bool
    hurst: float
    kaufman_er: float
    regime: str
    regime_ok: bool
    action: str
    reason: str


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg


def setup_logging() -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "logs" / "roi_bot.log")],
    )


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {
            "peak_equity": 0.0,
            "day": "",
            "day_start_equity": 0.0,
            "consecutive_losses": 0,
            "cooldown_until": None,
            "positions": {},
        }
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(STATE_FILE)


def normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "")


def bars_to_frame(barset: Any, symbols: list[str]) -> pd.DataFrame:
    df = barset.df.copy()
    if df.empty:
        return pd.DataFrame()
    df = df.reset_index()
    # Alpaca SDK may return symbol as BTC/USD or BTCUSD depending on release.
    df["symbol"] = df["symbol"].astype(str)
    piv = df.pivot(index="timestamp", columns="symbol", values="close").sort_index()
    mapping = {normalize_symbol(s): s for s in symbols}
    piv = piv.rename(columns={c: mapping.get(normalize_symbol(c), c) for c in piv.columns})
    return piv.ffill().dropna(how="all")


def fetch_prices(client: CryptoHistoricalDataClient, symbols: list[str], cfg: dict[str, Any]) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(cfg["history_days"]))
    tf = TimeFrame(int(cfg["timeframe_minutes"]), TimeFrameUnit.Minute)
    request = CryptoBarsRequest(symbol_or_symbols=symbols, timeframe=tf, start=start, end=end)
    return bars_to_frame(client.get_crypto_bars(request), symbols)


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(s: pd.Series, period: int) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr_proxy(s: pd.Series, period: int = 14) -> pd.Series:
    # Close-to-close robust proxy because pair selection only requires consistent volatility sizing.
    return s.diff().abs().ewm(alpha=1 / period, adjust=False).mean()


def hurst_exponent(series: pd.Series, max_lag: int = 20) -> float:
    values = np.asarray(series.dropna(), dtype=float)
    if len(values) < max_lag * 3:
        return 0.5
    lags = np.arange(2, max_lag)
    tau = np.array([np.std(values[lag:] - values[:-lag]) for lag in lags])
    valid = np.isfinite(tau) & (tau > 0)
    if valid.sum() < 5:
        return 0.5
    slope = np.polyfit(np.log(lags[valid]), np.log(tau[valid]), 1)[0]
    return float(np.clip(slope, 0.0, 1.0))


def kaufman_er_value(series: pd.Series, period: int = 20) -> float:
    s = series.dropna()
    if len(s) < period + 1:
        return 0.0
    change = abs(float(s.iloc[-1]) - float(s.iloc[-period - 1]))
    volatility = float(s.diff().abs().iloc[-period:].sum())
    return change / volatility if volatility > 0 else 0.0


def half_life(spread: pd.Series) -> float:
    lag = spread.shift(1).dropna()
    delta = spread.diff().dropna()
    idx = lag.index.intersection(delta.index)
    if len(idx) < 30:
        return math.inf
    x = lag.loc[idx].to_numpy()
    y = delta.loc[idx].to_numpy()
    beta = np.polyfit(x, y, 1)[0]
    if beta >= 0:
        return math.inf
    return float(-math.log(2) / beta)


def build_signal(prices: pd.DataFrame, symbol: str, benchmark: str, cfg: dict[str, Any]) -> Signal:
    scfg = cfg["signal"]
    n = min(int(cfg["formation_bars"]), len(prices))
    pair = prices[[symbol, benchmark]].tail(n).dropna()
    if len(pair) < int(cfg["minimum_bars"]):
        raise ValueError(f"insufficient bars for {symbol}: {len(pair)}")

    y = np.log(pair[symbol].astype(float))
    x = np.log(pair[benchmark].astype(float))
    beta, intercept = np.polyfit(x.to_numpy(), y.to_numpy(), 1)
    spread = y - (intercept + beta * x)
    sigma = float(spread.std(ddof=1))
    z = float((spread.iloc[-1] - spread.mean()) / sigma) if sigma > 0 else 0.0
    corr = float(y.diff().corr(x.diff()))
    try:
        pvalue = float(adfuller(spread.dropna(), autolag="AIC")[1])
    except Exception:
        pvalue = 1.0
    hl = half_life(spread)

    px = pair[symbol]
    latest = float(px.iloc[-1])
    atr_s = atr_proxy(px)
    atr_v = float(atr_s.iloc[-1])
    atr_pct = atr_v / latest if latest else 0.0
    rsi_v = float(rsi(px, int(scfg["rsi_period"])).iloc[-1])
    fast = float(ema(px, int(scfg["ema_fast"])).iloc[-1])
    slow = float(ema(px, int(scfg["ema_slow"])).iloc[-1])
    bench_fast = float(ema(pair[benchmark], int(scfg["ema_fast"])).iloc[-1])
    bench_slow = float(ema(pair[benchmark], int(scfg["ema_slow"])).iloc[-1])
    trend_ok = latest > slow and fast > slow and bench_fast >= bench_slow

    rcfg = cfg.get("regime", {})
    log_px = np.log(px.astype(float))
    hurst_v = hurst_exponent(log_px.tail(int(rcfg.get("hurst_window", 160))))
    er_v = kaufman_er_value(px, int(rcfg.get("er_period", 20)))
    if hurst_v >= float(rcfg.get("trend_hurst_min", 0.52)) and er_v >= float(rcfg.get("trend_er_min", 0.25)):
        regime = "trend"
        regime_ok = trend_ok
    elif hurst_v <= float(rcfg.get("range_hurst_max", 0.48)) and er_v <= float(rcfg.get("range_er_max", 0.30)):
        regime = "range"
        regime_ok = bench_fast >= bench_slow * float(rcfg.get("benchmark_floor_ratio", 0.985))
    else:
        regime = "transition"
        regime_ok = bool(rcfg.get("allow_transition", False)) and trend_ok

    quality = (
        corr >= float(scfg["min_correlation"])
        and pvalue <= float(scfg["max_adf_pvalue"])
        and hl <= float(scfg["max_half_life_bars"])
        and float(scfg["min_atr_pct"]) <= atr_pct <= float(scfg["max_atr_pct"])
        and regime_ok
    )
    oversold_ok = float(scfg["rsi_min"]) <= rsi_v <= float(scfg["rsi_max"])

    if z <= float(scfg["emergency_z"]):
        action, reason = "avoid", "spread dislocation exceeds emergency threshold"
    elif z <= float(scfg["entry_z"]) and quality and trend_ok and oversold_ok:
        action, reason = "buy", "cheap relative spread with stationary relationship and positive regime"
    elif z >= float(scfg["exit_z"]):
        action, reason = "exit", "spread normalized"
    else:
        action, reason = "hold", "no qualified entry or exit"

    return Signal(
        symbol=symbol, benchmark=benchmark, timestamp=str(pair.index[-1]), price=latest,
        zscore=z, beta=float(beta), correlation=corr, adf_pvalue=pvalue,
        half_life=hl, atr=atr_v, atr_pct=atr_pct, rsi=rsi_v,
        trend_ok=trend_ok, hurst=hurst_v, kaufman_er=er_v,
        regime=regime, regime_ok=regime_ok, action=action, reason=reason,
    )


def risk_gate(state: dict[str, Any], equity: float, cfg: dict[str, Any]) -> tuple[bool, str]:
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    if state.get("day") != today:
        state["day"] = today
        state["day_start_equity"] = equity
        state["consecutive_losses"] = 0
    state["peak_equity"] = max(float(state.get("peak_equity", 0)), equity)

    peak = float(state["peak_equity"] or equity)
    dd = 1 - equity / peak if peak else 0
    daily = 1 - equity / float(state["day_start_equity"] or equity)
    if dd >= float(cfg["risk"]["max_drawdown_pct"]):
        return False, f"max drawdown gate {dd:.2%}"
    if daily >= float(cfg["risk"]["daily_loss_limit_pct"]):
        return False, f"daily loss gate {daily:.2%}"
    cooldown = state.get("cooldown_until")
    if cooldown and now < datetime.fromisoformat(cooldown):
        return False, f"cooldown until {cooldown}"
    return True, "risk gates clear"


def position_notional(equity: float, signal: Signal, cfg: dict[str, Any]) -> float:
    risk = cfg["risk"]
    stop_distance = max(signal.atr * float(risk["atr_stop_multiple"]), signal.price * 0.005)
    units = equity * float(risk["risk_per_trade_pct"]) / stop_distance
    raw = units * signal.price
    return max(0.0, min(raw, equity * float(risk["max_position_pct"])))


def latest_spread_bps(data_client: CryptoHistoricalDataClient, symbol: str) -> float:
    req = CryptoLatestQuoteRequest(symbol_or_symbols=[symbol])
    quote_set = data_client.get_crypto_latest_quote(req)
    q = quote_set[symbol]
    mid = (float(q.ask_price) + float(q.bid_price)) / 2
    return (float(q.ask_price) - float(q.bid_price)) / mid * 10_000 if mid else math.inf


def submit_market(trading: TradingClient, symbol: str, side: OrderSide, notional: float | None = None, qty: float | None = None) -> Any:
    request = MarketOrderRequest(
        symbol=symbol,
        side=side,
        time_in_force=TimeInForce.GTC,
        notional=round(notional, 2) if notional is not None else None,
        qty=qty,
    )
    return trading.submit_order(order_data=request)


def append_trade(row: dict[str, Any]) -> None:
    TRADE_LOG.parent.mkdir(exist_ok=True)
    pd.DataFrame([row]).to_csv(TRADE_LOG, mode="a", header=not TRADE_LOG.exists(), index=False)


def cycle(cfg: dict[str, Any], execute: bool) -> list[Signal]:
    load_dotenv(ROOT / ".env")
    key, secret = os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in .env")
    paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"
    if not paper and cfg.get("mode") != "live":
        raise RuntimeError("Refusing live credentials unless config mode is live")

    data_client = CryptoHistoricalDataClient(key, secret)
    trading = TradingClient(key, secret, paper=paper)
    symbols = list(dict.fromkeys(cfg["symbols"] + [cfg["benchmark"]]))
    prices = fetch_prices(data_client, symbols, cfg)
    state = load_state()
    account = trading.get_account()
    equity = float(account.equity)
    allowed, gate_reason = risk_gate(state, equity, cfg)
    logging.info("equity=%.2f %s", equity, gate_reason)

    positions = {p.symbol.replace("USD", "/USD"): p for p in trading.get_all_positions() if getattr(p, "asset_class", "") == "crypto" or "/" in p.symbol}
    total_exposure = sum(abs(float(p.market_value)) for p in positions.values())
    signals: list[Signal] = []

    for symbol in cfg["symbols"]:
        if symbol == cfg["benchmark"]:
            continue
        try:
            sig = build_signal(prices, symbol, cfg["benchmark"], cfg)
            signals.append(sig)
            logging.info("%s action=%s z=%.2f p=%.3f hl=%.1f corr=%.2f rsi=%.1f", symbol, sig.action, sig.zscore, sig.adf_pvalue, sig.half_life, sig.correlation, sig.rsi)
            sdk_symbol = normalize_symbol(symbol)
            pos = positions.get(symbol)

            if pos:
                entry = float(pos.avg_entry_price)
                current = sig.price
                qty = abs(float(pos.qty))
                r_stop = entry - sig.atr * float(cfg["risk"]["atr_stop_multiple"])
                target = entry + (entry - r_stop) * float(cfg["risk"]["profit_target_r"])
                trailing = current - sig.atr * float(cfg["risk"]["atr_trailing_multiple"])
                tracked = state["positions"].setdefault(symbol, {"highest": current, "entry": entry})
                tracked["highest"] = max(float(tracked.get("highest", current)), current)
                trail_stop = max(r_stop, float(tracked["highest"]) - sig.atr * float(cfg["risk"]["atr_trailing_multiple"]))
                must_exit = sig.action == "exit" or current <= trail_stop or current >= target
                if must_exit and execute:
                    order = submit_market(trading, sdk_symbol, OrderSide.SELL, qty=qty)
                    pnl = (current - entry) * qty
                    state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1 if pnl < 0 else 0
                    if state["consecutive_losses"] >= int(cfg["risk"]["consecutive_loss_limit"]):
                        state["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(minutes=int(cfg["risk"]["cooldown_minutes"]))).isoformat()
                    append_trade({"timestamp": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "side": "sell", "qty": qty, "price_est": current, "pnl_est": pnl, "order_id": str(order.id), "reason": sig.reason})
                    state["positions"].pop(symbol, None)
                continue

            if sig.action != "buy" or not allowed:
                continue
            if len(positions) >= int(cfg["risk"]["max_open_positions"]):
                continue
            if total_exposure / equity >= float(cfg["risk"]["max_total_exposure_pct"]):
                continue
            spread_bps = latest_spread_bps(data_client, symbol)
            if spread_bps > float(cfg["execution"]["max_spread_bps"]):
                logging.warning("skip %s spread %.1fbps", symbol, spread_bps)
                continue
            notional = position_notional(equity, sig, cfg)
            notional = min(notional, equity * float(cfg["risk"]["max_total_exposure_pct"]) - total_exposure)
            if notional < float(cfg["execution"]["min_notional"]):
                continue
            if execute:
                order = submit_market(trading, sdk_symbol, OrderSide.BUY, notional=notional)
                append_trade({"timestamp": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "side": "buy", "notional": notional, "price_est": sig.price, "order_id": str(order.id), "reason": sig.reason})
                state["positions"][symbol] = {"highest": sig.price, "entry": sig.price}
                total_exposure += notional
        except Exception as exc:
            logging.exception("symbol cycle failed for %s: %s", symbol, exc)

    save_state(state)
    report = ROOT / "reports" / "latest_signals.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps([asdict(s) for s in signals], indent=2), encoding="utf-8")
    return signals


def _metrics(starting_equity: float, ending_equity: float, max_dd: float, records: list[dict[str, Any]], open_count: int) -> dict[str, Any]:
    pnls = [float(r["pnl"]) for r in records]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "starting_equity": starting_equity,
        "ending_equity": ending_equity,
        "roi_pct": (ending_equity / starting_equity - 1) * 100,
        "max_drawdown_pct": max_dd * 100,
        "closed_trades": len(records),
        "win_rate_pct": len(wins) / len(records) * 100 if records else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "expectancy_dollars": sum(pnls) / len(pnls) if pnls else 0.0,
        "average_win": sum(wins) / len(wins) if wins else 0.0,
        "average_loss": sum(losses) / len(losses) if losses else 0.0,
        "average_hold_bars": sum(float(r["hold_bars"]) for r in records) / len(records) if records else 0.0,
        "open_positions": open_count,
    }


def _run_backtest(prices: pd.DataFrame, cfg: dict[str, Any], start_i: int, end_i: int, starting_equity: float = 10_000.0) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cash, peak, max_dd = starting_equity, starting_equity, 0.0
    open_pos: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    costs = (float(cfg["execution"]["slippage_bps_backtest"]) + float(cfg["execution"]["fee_bps_backtest"])) / 10_000
    step = int(cfg.get("backtest", {}).get("step_bars", 4))

    for i in range(start_i, min(end_i, len(prices)), step):
        window = prices.iloc[: i + 1]
        now = str(window.index[-1])
        equity = cash + sum(p["qty"] * float(window[s].iloc[-1]) for s, p in open_pos.items())
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak if peak else 0.0)

        for symbol in cfg["symbols"]:
            if symbol == cfg["benchmark"] or symbol not in window.columns:
                continue
            try:
                sig = build_signal(window, symbol, cfg["benchmark"], cfg)
            except Exception:
                continue

            if symbol in open_pos:
                p = open_pos[symbol]
                stop = p["entry"] - p["atr"] * float(cfg["risk"]["atr_stop_multiple"])
                target = p["entry"] + (p["entry"] - stop) * float(cfg["risk"]["profit_target_r"])
                p["highest"] = max(float(p["highest"]), sig.price)
                trail = max(stop, float(p["highest"]) - sig.atr * float(cfg["risk"]["atr_trailing_multiple"]))
                exit_reason = None
                if sig.price <= trail:
                    exit_reason = "stop_or_trailing"
                elif sig.price >= target:
                    exit_reason = "profit_target"
                elif sig.action == "exit":
                    exit_reason = "spread_normalized"
                elif i - int(p["entry_i"]) >= int(cfg.get("backtest", {}).get("max_hold_bars", 192)):
                    exit_reason = "time_exit"

                if exit_reason:
                    proceeds = p["qty"] * sig.price * (1 - costs)
                    pnl = proceeds - p["cost"]
                    cash += proceeds
                    records.append({
                        "symbol": symbol, "entry_time": p["entry_time"], "exit_time": now,
                        "entry_price": p["entry"], "exit_price": sig.price, "pnl": pnl,
                        "return_pct": pnl / p["cost"] * 100 if p["cost"] else 0.0,
                        "hold_bars": i - int(p["entry_i"]), "entry_regime": p["regime"],
                        "entry_z": p["zscore"], "exit_reason": exit_reason,
                    })
                    del open_pos[symbol]
            elif sig.action == "buy" and len(open_pos) < int(cfg["risk"]["max_open_positions"]):
                equity = cash + sum(p["qty"] * float(window[s].iloc[-1]) for s, p in open_pos.items())
                current_exposure = sum(p["cost"] for p in open_pos.values())
                room = max(0.0, equity * float(cfg["risk"]["max_total_exposure_pct"]) - current_exposure)
                notional = min(position_notional(equity, sig, cfg), cash, room)
                if notional >= float(cfg["execution"]["min_notional"]):
                    qty = notional * (1 - costs) / sig.price
                    cash -= notional
                    open_pos[symbol] = {
                        "qty": qty, "entry": sig.price, "atr": sig.atr, "highest": sig.price,
                        "cost": notional, "entry_i": i, "entry_time": now,
                        "regime": sig.regime, "zscore": sig.zscore,
                    }

    final_prices = prices.iloc[min(end_i, len(prices)) - 1]
    final_equity = cash + sum(p["qty"] * float(final_prices[s]) for s, p in open_pos.items())
    return _metrics(starting_equity, final_equity, max_dd, records, len(open_pos)), records


def _group_metrics(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[str(row.get(key, "unknown"))].append(row)
    out: dict[str, Any] = {}
    for name, rows in sorted(grouped.items()):
        pnls = [float(r["pnl"]) for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        out[name] = {
            "trades": len(rows), "net_pnl": sum(pnls),
            "win_rate_pct": len(wins) / len(rows) * 100 if rows else 0.0,
            "profit_factor": sum(wins) / (-sum(losses)) if losses else None,
            "expectancy": sum(pnls) / len(rows) if rows else 0.0,
        }
    return out


def backtest(cfg: dict[str, Any]) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    client = CryptoHistoricalDataClient(os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY"))
    symbols = list(dict.fromkeys(cfg["symbols"] + [cfg["benchmark"]]))
    prices = fetch_prices(client, symbols, cfg)
    start_i = int(cfg["minimum_bars"])
    metrics, records = _run_backtest(prices, cfg, start_i, len(prices))
    result = {
        **metrics,
        "per_symbol": _group_metrics(records, "symbol"),
        "per_regime": _group_metrics(records, "entry_regime"),
        "warning": "Research backtest only; no guarantee of future returns.",
    }
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "backtest_latest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    pd.DataFrame(records).to_csv(reports / "backtest_trades_latest.csv", index=False)
    print(json.dumps(result, indent=2))
    return result


def walkforward(cfg: dict[str, Any]) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    client = CryptoHistoricalDataClient(os.getenv("APCA_API_KEY_ID"), os.getenv("APCA_API_SECRET_KEY"))
    symbols = list(dict.fromkeys(cfg["symbols"] + [cfg["benchmark"]]))
    prices = fetch_prices(client, symbols, cfg)
    wcfg = cfg.get("walkforward", {})
    train_bars = int(wcfg.get("train_bars", 2880))
    test_bars = int(wcfg.get("test_bars", 960))
    step_bars = int(wcfg.get("step_bars", test_bars))
    minimum = int(cfg["minimum_bars"])
    windows: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    cursor = max(minimum, train_bars)
    number = 1
    while cursor + test_bars <= len(prices):
        start = cursor
        end = cursor + test_bars
        metrics, records = _run_backtest(prices, cfg, start, end)
        window = {
            "window": number,
            "train_start": str(prices.index[max(0, cursor - train_bars)]),
            "train_end": str(prices.index[cursor - 1]),
            "test_start": str(prices.index[start]),
            "test_end": str(prices.index[end - 1]),
            **metrics,
        }
        windows.append(window)
        for r in records:
            r["window"] = number
        all_records.extend(records)
        cursor += step_bars
        number += 1

    eligible = [w for w in windows if w["closed_trades"] > 0]
    result = {
        "windows": windows,
        "summary": {
            "total_windows": len(windows),
            "eligible_windows": len(eligible),
            "profitable_windows": sum(w["roi_pct"] > 0 for w in eligible),
            "profitable_window_pct": sum(w["roi_pct"] > 0 for w in eligible) / len(eligible) * 100 if eligible else 0.0,
            "average_test_roi_pct": sum(w["roi_pct"] for w in eligible) / len(eligible) if eligible else 0.0,
            "worst_test_drawdown_pct": max((w["max_drawdown_pct"] for w in eligible), default=0.0),
            "total_closed_trades": sum(w["closed_trades"] for w in eligible),
            "per_symbol": _group_metrics(all_records, "symbol"),
            "per_regime": _group_metrics(all_records, "entry_regime"),
        },
        "warning": "Fixed-parameter rolling out-of-sample validation; not a guarantee of future results.",
    }
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "walkforward_latest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    pd.DataFrame(all_records).to_csv(reports / "walkforward_trades_latest.csv", index=False)
    print(json.dumps(result, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["scan", "paper", "backtest", "walkforward"])
    parser.add_argument("--config", default=str(ROOT / "config" / "config.yaml"))
    args = parser.parse_args()
    setup_logging()
    cfg = load_config(args.config)
    if args.command == "backtest":
        backtest(cfg)
    elif args.command == "walkforward":
        walkforward(cfg)
    else:
        execute = args.command == "paper" and not bool(cfg["execution"].get("dry_run", True))
        cycle(cfg, execute=execute)
        if args.command == "paper" and not execute:
            logging.warning("paper command is dry-run because execution.dry_run=true")


if __name__ == "__main__":
    main()
