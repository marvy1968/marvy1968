"""One pass: read the broker, protect stops, log or send brackets, write state.

No flag and no ``--dry-run`` places nothing. ``--paper`` is required to send
orders, and only to the paper host. ``--dry-run`` logs signals and does not send.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .bars import age_seconds
from .broker_guard import assert_paper_url
from .clock import as_et, before_hhmm, entry_cutoff, flatten_at
from .config import Config, config_hash
from .errors import AlreadyRunning, ConfigError
from .filters import liquidity_ok, load_earnings
from .news import news_decision
from .spyopen import spy_open_bias, spy_open_decision
from .journal import Journal, slippage
from .lockfile import run_lock
from .orders import build_bracket, duplicate_client_id, flatten_client_order_id, stop_client_order_id
from .protect import desired_lock_r, desired_stop, ensure_protected, favorable_r, is_tighter, replace_stop
from .reconcile import reconcile
from .regime import classify_regime
from .report import write_daily_report
from .risk import ExposureItem, exposure_ok, loss_stopped, mode_killed, share_qty, update_governor
from .signals import generate_signals, swing_hold_exit
from .state import State, apply_fills, day_pnl, load_state, roll_clock, save_state, sync_owned, week_pnl, win_rate
from .universe import sector_of
from .weights import recorded_weight, risk_dollars, update_ewma

log = logging.getLogger("cos1")


def _logger() -> logging.Logger:
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    return log


@dataclass
class Flags:
    paper: bool = False
    dry_run: bool = False


@dataclass
class RunResult:
    exit_code: int
    submitted: list = field(default_factory=list)
    signals: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    message: str = ""
    reconcile_flags: list = field(default_factory=list)


def run(
    cfg: Config,
    broker,
    data,
    flags: Flags,
    now: datetime,
    state_path,
    journal_path,
    report_dir,
    lock_path,
    news_fetcher=None,
) -> RunResult:
    logger = _logger()
    if flags.paper and flags.dry_run:
        return RunResult(2, message="choose only one of --paper or --dry-run")
    if not flags.paper and not flags.dry_run:
        logger.info("orders disabled: pass --paper to place paper orders or --dry-run to log signals")
        return RunResult(0, message="orders disabled")
    try:
        if list(cfg.rollout.live):
            raise ConfigError("rollout.live must be empty")
        assert_paper_url(getattr(broker, "base_url", ""))
        if getattr(data, "feed", "iex") != "iex":
            raise ConfigError("feed must be iex")
    except Exception as exc:
        logger.error("refusing run: %s", type(exc).__name__)
        return RunResult(2, message=type(exc).__name__)
    try:
        with run_lock(lock_path):
            return _run_locked(cfg, broker, data, flags, now, state_path, journal_path, report_dir, logger, news_fetcher)
    except AlreadyRunning:
        logger.error("overlapping run refused")
        return RunResult(3, message="already running")


def _run_locked(cfg, broker, data, flags: Flags, now: datetime, state_path, journal_path, report_dir, logger, news_fetcher=None) -> RunResult:
    errors: list[str] = []
    journal = Journal(journal_path)
    cfg_hash = config_hash(cfg)
    try:
        try:
            account = broker.get_account()
            positions = broker.get_positions()
            orders = broker.get_open_orders()
            week_ago = as_et(now) - timedelta(days=8)
            fills = broker.get_fills_since(week_ago)
            calendar = broker.get_calendar((as_et(now).date() - timedelta(days=10)), as_et(now).date() + timedelta(days=10))
        except Exception as exc:
            errors.append(type(exc).__name__)
            logger.error("fail closed: %s", type(exc).__name__)
            journal.event("read_failed", detail=type(exc).__name__, config_hash=cfg_hash, ts=now)
            _report(report_dir, now, 0.0, 0.0, 0, errors)
            return RunResult(2, message=f"fail closed: {type(exc).__name__}")

        state = load_state(state_path)
        roll_clock(state, now, account.equity)
        apply_fills(state, fills)
        state.equity_peak, state.drawdown, state.exposure_mult = update_governor(
            account.equity, state.equity_peak, state.exposure_mult
        )
        _update_kills(state, cfg)
        _update_weights(state, cfg)
        sync_owned(state, positions)
        if flags.dry_run:
            _log_foreign(positions, orders, set(state.positions), cfg.account.exclusive, logger)
        flags_found = reconcile(state.intended, orders, positions, cfg_hash)
        for item in flags_found:
            journal.event(
                "reconcile",
                symbol=item.get("symbol", ""),
                client_order_id=item.get("client_order_id", ""),
                detail=f"{item['kind']}: {item['detail']}",
                config_hash=cfg_hash,
                ts=now,
            )
        session = _session_today(calendar, as_et(now).date())
        if flags.paper and session is not None:
            _protect_all(broker, state, orders, journal, now, errors)
            orders = broker.get_open_orders()
            if _should_flatten_day(now, session, cfg, state):
                _flatten_mode(broker, state, orders, journal, now, {"day", "scalp"}, "session_or_daily_stop")

        bundle = None
        try:
            sessions = {item.day: item for item in calendar}
            symbols = list(dict.fromkeys(["SPY", *cfg.universe]))
            bundle = data.bundle(symbols, now, sessions)
        except Exception as exc:
            errors.append(type(exc).__name__)
            logger.error("fail closed: data %s", type(exc).__name__)
            journal.event("data_failed", detail=type(exc).__name__, config_hash=cfg_hash, ts=now)
            _finish(state, state_path, report_dir, now, errors, journal, cfg_hash)
            return RunResult(2, message=f"fail closed: {type(exc).__name__}", reconcile_flags=flags_found)

        _feed_health(state, now, bundle, logger)
        if flags.paper and session is not None:
            _trail(broker, state, bundle, journal, now, cfg)
            _swing_exits(broker, state, bundle, journal, now, cfg)

        signals = []
        skipped = []
        submitted = []
        if session is None:
            logger.info("market closed; no new entries")
        else:
            spy_bars = bundle.daily.get("SPY") or []
            regime = classify_regime(spy_bars, cfg.regime)
            logger.info("REGIME %s %s", regime.name, regime.reason)
            cutoff = entry_cutoff(session, cfg.day.entry_cutoff_min_before_close)
            day_entries = as_et(now) < cutoff
            blocked_daily = loss_stopped(day_pnl(state), state.day_start_equity, cfg.risk.daily_loss_pct)
            blocked_weekly = loss_stopped(week_pnl(state), state.week_start_equity, cfg.risk.weekly_loss_pct)
            blocked_topdown = loss_stopped(day_pnl(state), state.day_start_equity, cfg.topdown.daily_loss_pct)
            spy_bias = spy_open_bias(bundle.min5.get("SPY") or [], session.day)
            logger.info("SPY_OPEN enabled=%s bias=%s", int(cfg.spy_open.enabled), spy_bias)
            news_snap, news_failed = _load_news(cfg, news_fetcher, now, logger)
            earnings = load_earnings(cfg.paths.earnings_csv)
            session_days = [item.day for item in calendar]
            signals = generate_signals(bundle, cfg, regime, session.day)
            owned = set(state.positions)
            for sig in signals:
                decision = _consider(
                    sig,
                    cfg,
                    flags,
                    state,
                    broker,
                    positions,
                    orders,
                    bundle,
                    earnings,
                    session_days,
                    session.day,
                    day_entries,
                    blocked_daily,
                    blocked_weekly,
                    blocked_topdown,
                    spy_bias,
                    news_snap,
                    news_failed,
                    owned,
                    submitted,
                    account.equity,
                    now,
                    journal,
                    cfg_hash,
                    logger,
                )
                if decision.get("submitted"):
                    submitted.append(decision["plan"])
                    owned.add(sig.symbol)
                    orders = broker.get_open_orders()
                    positions = broker.get_positions()
                else:
                    skipped.append(decision)
        _remember_intended(state)
        state.last_config_hash = cfg_hash
        today_closed = [row for row in state.closed if str(row.get("ts", "")).startswith(state.day_key)]
        _finish(state, state_path, report_dir, now, errors, journal, cfg_hash, today_closed)
        return RunResult(0, submitted=submitted, signals=signals, skipped=skipped, reconcile_flags=flags_found)
    finally:
        try:
            journal.close()
        except Exception:
            pass


def _consider(
    sig,
    cfg,
    flags,
    state,
    broker,
    positions,
    orders,
    bundle,
    earnings,
    session_days,
    session_day,
    day_entries,
    blocked_daily,
    blocked_weekly,
    blocked_topdown,
    spy_bias,
    news_snap,
    news_failed,
    owned,
    pending,
    equity,
    now,
    journal,
    cfg_hash,
    logger,
) -> dict:
    log_key = f"{state.day_key}:{sig.signal_id}"
    if sig.mode in state.killed_modes or sig.mode in ("day", "swing", "scalp") and sig.mode in state.killed_modes:
        return {"signal": sig.signal_id, "reason": "killed"}
    if blocked_daily or blocked_weekly:
        return {"signal": sig.signal_id, "reason": "daily_stop" if blocked_daily else "weekly_stop"}
    if sig.mode in ("day", "scalp") and not day_entries:
        return {"signal": sig.signal_id, "reason": "cutoff"}
    if sig.signal_id in state.consumed_signals and not sig.log_only:
        return {"signal": sig.signal_id, "reason": "once"}
    if sig.expert == "topdown" and blocked_topdown:
        logger.info("TOPDOWN_DAILY symbol=%s decision=block", sig.symbol)
        return {"signal": sig.signal_id, "reason": "topdown_daily"}
    if sig.mode in ("day", "scalp"):
        allowed, why_spy = spy_open_decision(sig.side, spy_bias, cfg.spy_open.enabled)
        logger.info(
            "SPY_OPEN enabled=%s symbol=%s side=%s decision=%s",
            int(cfg.spy_open.enabled),
            sig.symbol,
            sig.side,
            why_spy,
        )
        if not allowed:
            return {"signal": sig.signal_id, "reason": "spy_open"}
    news_on = bool(cfg.news.enabled or cfg.news.log_only)
    if news_failed and news_on:
        allowed_news, why_news = False, "unread"
    elif news_on:
        allowed_news, why_news = news_decision(sig.side, sig.symbol, sector_of(sig.symbol), news_snap, True)
    else:
        allowed_news, why_news = True, "bypass"
    logger.info(
        "NEWS enabled=%s log_only=%s symbol=%s side=%s decision=%s",
        int(cfg.news.enabled),
        int(cfg.news.log_only),
        sig.symbol,
        sig.side,
        why_news,
    )
    # log_only records the would-be decision and does not block or change the order.
    if cfg.news.enabled and not cfg.news.log_only and not allowed_news:
        return {"signal": sig.signal_id, "reason": "news"}
    blocked, why = earnings.status(sig.symbol, session_day, session_days)
    if blocked:
        return {"signal": sig.signal_id, "reason": why}
    if _foreign(sig.symbol, positions, orders, owned, cfg.account.exclusive):
        return {"signal": sig.signal_id, "reason": "foreign"}
    bars = _bars_for(sig, bundle)
    quote = bundle.quotes.get(sig.symbol)
    daily = bundle.daily.get(sig.symbol) or []
    dollar = None
    if len(daily) >= 5:
        window = daily[-20:]
        dollar = sig.entry * sum(b.volume for b in window) / len(window)
    ok, reason = liquidity_ok(price=sig.entry, quote=quote, bars=bars, cfg=cfg.liquidity, dollar_volume=dollar)
    if not ok:
        return {"signal": sig.signal_id, "reason": reason}
    if sig.side == "short" or not sig.log_only:
        try:
            asset = broker.get_asset(sig.symbol)
        except Exception:
            return {"signal": sig.signal_id, "reason": "asset_read"}
        if asset is None or not asset.tradable:
            return {"signal": sig.signal_id, "reason": "not_tradable"}
        if sig.side == "short" and not (asset.shortable and asset.easy_to_borrow):
            return {"signal": sig.signal_id, "reason": "not_shortable"}
    send = flags.paper and not sig.log_only
    if send and cfg.day.no_entry_before and before_hhmm(now, cfg.day.no_entry_before):
        logger.info("NO_ENTRY_BEFORE %s symbol=%s mode=%s", cfg.day.no_entry_before, sig.symbol, sig.mode)
        return {"signal": sig.signal_id, "reason": "no_entry_before"}
    if not send:
        if log_key not in state.logged_signals:
            state.logged_signals[log_key] = sig.expert
            logger.info(
                "SIGNAL %s %s %s %s log_only=%s earnings=%s",
                sig.expert,
                sig.symbol,
                sig.side,
                sig.reason,
                sig.log_only or not flags.paper,
                why,
            )
            journal.event(
                "signal",
                ts=now,
                symbol=sig.symbol,
                mode=sig.mode,
                expert=sig.expert,
                price=sig.entry,
                detail=f"{sig.side} {sig.reason} earnings={why}",
                config_hash=cfg_hash,
            )
        return {"signal": sig.signal_id, "reason": "log_only" if sig.log_only or not flags.paper else "logged"}
    weight = recorded_weight(state.weights, sig.expert, cfg.weights)
    if sig.expert == "topdown":
        risk_pct = cfg.topdown.risk_pct
    elif sig.expert == "swing_donchian":
        risk_pct = cfg.swing.donchian_risk_pct
    elif sig.mode == "day":
        risk_pct = cfg.risk.risk_pct_day
    else:
        risk_pct = cfg.risk.risk_pct_swing
    dollars = risk_dollars(equity, risk_pct, weight, state.exposure_mult)
    if sig.mode == "day":
        distance = abs(sig.entry - sig.stop)
        atr_pct = (distance / sig.entry) / cfg.day.atr_mult if sig.entry else 0
        if atr_pct > 0:
            dollars *= min(1.0, cfg.day.vol_target_pct / atr_pct)
    qty = share_qty(equity, sig.entry, sig.stop, dollars, cfg.risk.max_symbol_frac)
    if qty < 1:
        return {"signal": sig.signal_id, "reason": "qty"}
    try:
        plan = build_bracket(sig, qty)
    except Exception as exc:
        return {"signal": sig.signal_id, "reason": type(exc).__name__}
    open_ids = {order.client_order_id for order in orders if order.client_order_id}
    if duplicate_client_id(plan.client_order_id, open_ids):
        state.consumed_signals[sig.signal_id] = state.day_key
        return {"signal": sig.signal_id, "reason": "duplicate"}
    current = _exposure_items(positions, state, orders, pending)
    proposal = ExposureItem(sig.symbol, sig.side, qty, sig.entry, plan.stop, sector_of(sig.symbol))
    allowed, why_exp = exposure_ok(current, proposal, equity, cfg.risk)
    if not allowed:
        logger.info("EXPOSURE symbol=%s sector=%s reason=%s", sig.symbol, proposal.sector, why_exp)
        return {"signal": sig.signal_id, "reason": why_exp}
    try:
        order = broker.submit_bracket(plan)
    except Exception as exc:
        journal.event(
            "submit_failed",
            ts=now,
            client_order_id=plan.client_order_id,
            symbol=sig.symbol,
            mode=sig.mode,
            expert=sig.expert,
            detail=type(exc).__name__,
            config_hash=cfg_hash,
        )
        return {"signal": sig.signal_id, "reason": "submit_failed"}
    state.consumed_signals[sig.signal_id] = state.day_key
    state.order_meta[plan.client_order_id] = {
        "expert": sig.expert,
        "mode": sig.mode,
        "risk": abs(plan.entry_hint - plan.stop),
    }
    stop_id = ""
    for leg in getattr(order, "legs", []) or []:
        if leg.type == "stop" or leg.stop_price is not None:
            stop_id = leg.id
    state.positions[sig.symbol] = {
        "symbol": sig.symbol,
        "mode": sig.mode,
        "expert": sig.expert,
        "side": sig.side,
        "qty": qty,
        "entry": plan.entry_hint,
        "stop": plan.stop,
        "tp": plan.take_profit,
        "risk": abs(plan.entry_hint - plan.stop),
        "signal_id": sig.signal_id,
        "entry_client_id": plan.client_order_id,
        "stop_order_id": stop_id,
        "stop_client_id": "",
        "opened_at": as_et(now).date().isoformat(),
        "extreme": plan.entry_hint,
        "sector": sector_of(sig.symbol),
    }
    journal.event(
        "submit",
        ts=now,
        client_order_id=plan.client_order_id,
        order_id=getattr(order, "id", ""),
        symbol=sig.symbol,
        mode=sig.mode,
        expert=sig.expert,
        status=getattr(order, "status", ""),
        qty=qty,
        price=plan.entry_hint,
        detail=f"stop={plan.stop} tp={plan.take_profit}",
        config_hash=cfg_hash,
    )
    logger.info("ORDER %s %s qty=%s stop=%s tp=%s", plan.client_order_id, sig.symbol, qty, plan.stop, plan.take_profit)
    return {"signal": sig.signal_id, "reason": "submitted", "submitted": True, "plan": plan}


def _load_news(cfg, news_fetcher, now, logger):
    if not (cfg.news.enabled or cfg.news.log_only):
        logger.info("NEWS enabled=0 log_only=0 decision=bypass")
        return None, False
    if news_fetcher is None:
        logger.error("NEWS_READ_FAILED reason=no_fetcher")
        return None, True
    try:
        symbols = list(dict.fromkeys(["SPY", *cfg.universe]))
        snap = news_fetcher(symbols, now, sector_of)
        logger.info(
            "NEWS enabled=1 articles=%s market=%s",
            getattr(snap, "articles", 0),
            getattr(snap, "market", 0),
        )
        return snap, False
    except Exception as exc:
        logger.error("NEWS_READ_FAILED %s", type(exc).__name__)
        return None, True


def _entry_order(order) -> bool:
    """Working cos1 entry, not a stop-replace or flatten leg."""
    client_id = str(getattr(order, "client_order_id", "") or "")
    if not client_id.startswith("cos1-"):
        return False
    return not (client_id.startswith("cos1-stp-") or client_id.startswith("cos1-flat-"))


def _book_item(symbol: str, side: str, qty: float, price: float, stop: float | None) -> ExposureItem:
    if side in ("buy", "long"):
        side = "long"
    else:
        side = "short"
    if price <= 0:
        price = 1.0
    if stop is None:
        stop = price * (0.98 if side == "long" else 1.02)
    return ExposureItem(symbol, side, abs(qty) or 1.0, price, stop, sector_of(symbol))


def _exposure_items(positions, state: State, orders=(), pending=()) -> list[ExposureItem]:
    """Positions, open cos1 entry orders, and orders submitted earlier in this run.

    An entry that has not filled yet is still a position for the sector cap.
    Stop and flatten orders do not add a second slot for a symbol already counted.
    """
    items = []
    seen = set()
    for pos in positions:
        owned = state.positions.get(pos.symbol) or {}
        price = pos.current_price or pos.avg_price
        stop = owned.get("stop")
        if stop is None and price:
            stop = price * (0.98 if pos.side == "long" else 1.02)
        items.append(_book_item(pos.symbol, pos.side, abs(pos.qty), price, stop))
        seen.add(pos.symbol)
    for order in orders:
        if not _entry_order(order) or order.symbol in seen:
            continue
        owned = state.positions.get(order.symbol) or {}
        price = float(owned.get("entry") or getattr(order, "limit_price", None) or getattr(order, "stop_price", None) or 0)
        items.append(_book_item(order.symbol, order.side, order.qty, price, owned.get("stop")))
        seen.add(order.symbol)
    for plan in pending:
        if plan.symbol in seen:
            continue
        items.append(_book_item(plan.symbol, plan.side, plan.qty, plan.entry_hint, plan.stop))
        seen.add(plan.symbol)
    return items


def list_foreign(positions, orders, owned: set[str], exclusive: bool) -> list[dict]:
    """Symbols held or working at the broker that this bot does not own.

    A position absent from our state, or an open order whose id does not start
    with ``cos1-``, blocks new entries in that symbol unless the account is exclusive.
    """
    if exclusive:
        return []
    found: dict[str, dict] = {}
    for pos in positions:
        if abs(getattr(pos, "qty", 0) or 0) <= 0 or pos.symbol in owned:
            continue
        found[pos.symbol] = {
            "symbol": pos.symbol,
            "source": "position",
            "side": pos.side,
            "qty": abs(pos.qty),
            "client_order_id": "",
        }
    for order in orders:
        client_id = str(order.client_order_id or "")
        if client_id.startswith("cos1-"):
            continue
        row = found.get(order.symbol)
        if row is None:
            found[order.symbol] = {
                "symbol": order.symbol,
                "source": "open_order",
                "side": order.side,
                "qty": abs(order.qty or 0),
                "client_order_id": client_id,
            }
        elif not row["client_order_id"]:
            row["client_order_id"] = client_id
    return list(found.values())


def _log_foreign(positions, orders, owned: set[str], exclusive: bool, logger) -> None:
    for row in list_foreign(positions, orders, owned, exclusive):
        logger.info(
            "EXCLUDED_FOREIGN symbol=%s source=%s side=%s qty=%s client_order_id=%s",
            row["symbol"],
            row["source"],
            row.get("side") or "",
            row.get("qty") or "",
            row.get("client_order_id") or "",
        )


def _foreign(symbol, positions, orders, owned: set[str], exclusive: bool) -> bool:
    return any(row["symbol"] == symbol for row in list_foreign(positions, orders, owned, exclusive))


def _bars_for(sig, bundle):
    if sig.mode == "scalp":
        return bundle.min1.get(sig.symbol) or []
    if sig.mode == "day":
        return bundle.min5.get(sig.symbol) or []
    return bundle.daily.get(sig.symbol) or []


def _session_today(calendar, day):
    for session in calendar:
        if session.day == day:
            return session
    return None


def _should_flatten_day(now, session, cfg, state: State) -> bool:
    if as_et(now) >= flatten_at(session, cfg.day.flatten_min_before_close):
        return True
    return loss_stopped(day_pnl(state), state.day_start_equity, cfg.risk.daily_loss_pct)


def _protect_all(broker, state: State, orders, journal, now, errors: list[str]) -> None:
    nonce = as_et(now).strftime("%m%d%H%M")
    notes = []

    def _note(event, symbol, detail):
        notes.append((event, symbol, detail))
        journal.event(event, ts=now, symbol=symbol, detail=str(detail), client_order_id="")

    for symbol, pos in list(state.positions.items()):
        result = ensure_protected(broker, pos, orders, _note, nonce)
        if result == "unprotected":
            errors.append(f"unprotected:{symbol}")
        if result == "flattened" or pos.get("flattened"):
            state.positions.pop(symbol, None)


def _flatten_mode(broker, state, orders, journal, now, modes: set[str], reason: str) -> None:
    for symbol, pos in list(state.positions.items()):
        if pos.get("mode") not in modes:
            continue
        for order in orders:
            if order.symbol == symbol and str(order.client_order_id or "").startswith("cos1-"):
                try:
                    broker.cancel_order(order.id)
                except Exception as exc:
                    journal.event("cancel_failed", ts=now, symbol=symbol, detail=type(exc).__name__)
        client_id = flatten_client_order_id(symbol, as_et(now).strftime("%m%d%H%M") + reason[:6])
        try:
            broker.close_position(symbol, int(abs(pos["qty"])), "sell" if pos["side"] == "long" else "buy", client_id)
            journal.event("flatten", ts=now, symbol=symbol, client_order_id=client_id, detail=reason, mode=pos.get("mode", ""))
            state.positions.pop(symbol, None)
        except Exception as exc:
            journal.event("flatten_failed", ts=now, symbol=symbol, detail=f"{reason}:{type(exc).__name__}")


def _trail(broker, state, bundle, journal, now, cfg) -> None:
    nonce = as_et(now).strftime("%m%d%H%M")
    for symbol, pos in list(state.positions.items()):
        bars = bundle.min5.get(symbol) if pos.get("mode") != "swing" else bundle.daily.get(symbol)
        bars = bars or []
        for bar in bars:
            if pos["side"] == "long":
                pos["extreme"] = max(float(pos.get("extreme", pos["entry"])), bar.high)
            else:
                pos["extreme"] = min(float(pos.get("extreme", pos["entry"])), bar.low)
        fav = favorable_r(float(pos["entry"]), float(pos["extreme"]), float(pos["risk"]), pos["side"])
        lock = desired_lock_r(fav, cfg.risk.breakeven_r, cfg.risk.staircase_step_r)
        if lock is None:
            continue
        new_stop = desired_stop(float(pos["entry"]), float(pos["risk"]), pos["side"], lock)
        if not is_tighter(new_stop, float(pos["stop"]), pos["side"]):
            continue
        client_id = stop_client_order_id(symbol, lock, nonce)

        def _note(event, sym, detail, _pos=pos):
            journal.event(event, ts=now, symbol=sym, detail=str(detail), mode=_pos.get("mode", ""))

        result = replace_stop(broker, pos, new_stop, client_id, _note)
        if result == "replaced":
            pos["stop_client_id"] = client_id
        if result in {"flattened", "unprotected"} or pos.get("flattened"):
            if result == "flattened" or pos.get("flattened"):
                state.positions.pop(symbol, None)


def _swing_exits(broker, state, bundle, journal, now, cfg) -> None:
    from datetime import date as date_cls

    for symbol, pos in list(state.positions.items()):
        if pos.get("mode") != "swing":
            continue
        bars = bundle.daily.get(symbol) or []
        opened = date_cls.fromisoformat(pos["opened_at"])
        reason = swing_hold_exit(bars, pos["side"], pos.get("expert", ""), opened, cfg)
        if not reason:
            continue
        client_id = flatten_client_order_id(symbol, as_et(now).strftime("%m%d%H%M") + reason[:4])
        try:
            broker.close_position(symbol, int(abs(pos["qty"])), "sell" if pos["side"] == "long" else "buy", client_id)
            journal.event("exit", ts=now, symbol=symbol, client_order_id=client_id, detail=reason, mode="swing", expert=pos.get("expert", ""))
            state.positions.pop(symbol, None)
        except Exception as exc:
            journal.event("exit_failed", ts=now, symbol=symbol, detail=f"{reason}:{type(exc).__name__}")


def _feed_health(state: State, now: datetime, bundle, logger) -> None:
    if state.last_feed_health_ts:
        prev = datetime.fromisoformat(state.last_feed_health_ts)
        if prev.tzinfo is None:
            from .clock import ET

            prev = prev.replace(tzinfo=ET)
        if (as_et(now) - as_et(prev)).total_seconds() < 300:
            return
    symbols = sorted(set(bundle.daily) | set(bundle.min5) | set(bundle.min1))
    ok = 0
    stale = []
    newest = None
    for symbol in symbols:
        bars = bundle.min5.get(symbol) or bundle.daily.get(symbol) or []
        if not bars:
            stale.append(symbol)
            continue
        ts = bars[-1].ts
        if newest is None or as_et(ts) > as_et(newest):
            newest = ts
        limit = 15 * 60 if bundle.min5.get(symbol) else 5 * 86400
        age = age_seconds(ts, now) or 1e12
        if age > limit:
            stale.append(symbol)
        else:
            ok += 1
    age = age_seconds(newest, now)
    logger.info(
        "FEED_HEALTH feed=iex last_bar=%s age_sec=%.0f symbols_ok=%d symbols_stale=%d",
        newest.isoformat() if newest else "none",
        -1 if age is None else age,
        ok,
        len(stale),
    )
    state.last_feed_health_ts = as_et(now).isoformat()


def _update_kills(state: State, cfg: Config) -> None:
    limits = {
        "day": cfg.risk.day_kill_dd_pct,
        "swing": cfg.risk.swing_kill_dd_pct,
        "scalp": cfg.risk.scalp_kill_dd_pct,
    }
    for mode, limit in limits.items():
        if mode_killed(float(state.mode_pnl.get(mode, 0.0)), float(state.mode_peak.get(mode, 0.0)), state.day_start_equity, limit):
            if mode not in state.killed_modes:
                state.killed_modes.append(mode)


def _update_weights(state: State, cfg: Config) -> None:
    """Fold newly closed trades into the EWMA once. The R is pnl / dollar risk when we still know the risk."""
    seen = state.weights.setdefault("_applied", [])
    if not isinstance(seen, list):
        seen = []
        state.weights["_applied"] = seen
    for index, row in enumerate(state.closed):
        key = f"{row.get('ts')}:{row.get('symbol')}:{index}"
        if key in seen:
            continue
        seen.append(key)
        risk = abs(float(row.get("risk_dollars") or 0))
        realized_r = (float(row["pnl"]) / risk) if risk else 0.0
        expert = row.get("expert") or row.get("mode") or "day"
        # weights dict also holds the bookkeeping list; update_ewma writes expert rows.
        stats = {k: v for k, v in state.weights.items() if k != "_applied"}
        update_ewma(stats, expert, realized_r, cfg.weights)
        state.weights.update(stats)
    if len(seen) > 2000:
        del seen[:-2000]


def _remember_intended(state: State) -> None:
    intended = []
    for symbol, pos in state.positions.items():
        if pos.get("stop_order_id") or pos.get("stop_client_id"):
            intended.append(
                {
                    "role": "stop",
                    "symbol": symbol,
                    "order_id": pos.get("stop_order_id") or "",
                    "client_order_id": pos.get("stop_client_id") or "",
                }
            )
    state.intended = intended


def _finish(state, state_path, report_dir, now, errors, journal, cfg_hash, today_closed=None) -> None:
    save_state(state_path, state)
    rows = today_closed if today_closed is not None else [row for row in state.closed if str(row.get("ts", "")).startswith(state.day_key)]
    journal.event("run_complete", ts=now, detail=f"errors={len(errors)}", config_hash=cfg_hash, status="ok" if not errors else "errors")
    _report(report_dir, now, win_rate(rows), day_pnl(state), len(rows), errors)


def _report(report_dir, now, win, pnl, trades, errors) -> None:
    write_daily_report(
        report_dir,
        as_et(now).date(),
        win_rate=win,
        pnl=pnl,
        trades=trades,
        errors=len(errors),
        extra=("; ".join(errors) if errors else ""),
    )
