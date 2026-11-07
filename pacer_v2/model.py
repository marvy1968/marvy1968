"""v2 decision: blend, edge gate, overs band, minute/context shift, timing, isotonic.

Fit uses training rows only. ``decide`` applies a frozen model. No order is sent.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import expit, logit
from sklearn.isotonic import IsotonicRegression

from . import DEFAULT_OVERS, DEFAULT_W, EARLY_SKIP_MIN, EDGE_MIN, LATE_SKIP_MIN, MIN_FIT_N
from .adjust import (
    B2B_FACTOR,
    blowout_factor,
    context_multipliers,
    foul_factor,
    foul_limit_of,
    game_clock,
    per_minute_rate,
    production_shift,
    sport_of,
    timing_reason,
)
from .parse import Row

_LO = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55)
_HI = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
_MINP = (0.50, 0.55, 0.58, 0.60, 0.65, 0.70)


def default_model() -> dict:
    return {
        "version": 2,
        "w": DEFAULT_W,
        "w_fitted": False,
        "overs": dict(DEFAULT_OVERS),
        "isotonic": {"x": [0.0, 1.0], "y": [0.0, 1.0], "n": 0, "identity": True},
        "edge_min": EDGE_MIN,
        "early_skip_min": EARLY_SKIP_MIN,
        "late_skip_min": LATE_SKIP_MIN,
        "fit_rows": 0,
        "note": "log-only pacer v2; never places orders",
    }


def row_key(row: Row) -> str:
    line = "" if row.line is None else f"{row.line:.4f}"
    ts = row.ts or ""
    parts = [row.game_id or "", row.player or "", row.stat or "", row.side or "", line, ts]
    if not ts:
        blob = json.dumps(row.raw, sort_keys=True, default=str)
        parts.append(hashlib.sha1(blob.encode()).hexdigest()[:12])
    return "|".join(parts)


def project(row: Row, team_stats: dict | None) -> dict:
    """p_pacer after minutes, fouls, back-to-back, defense and pace. No blend yet."""
    gaps = list(row.gaps)
    sport = sport_of(row)
    clock = game_clock(row, sport)
    gaps.extend(clock["gaps"])
    limit = foul_limit_of(row, sport, clock["reg"])
    bfactor, bgap = blowout_factor(row, clock, sport)
    if bgap:
        gaps.append(bgap)
    ffactor, fgap = foul_factor(row, clock, sport, limit)
    if fgap:
        gaps.append(fgap)
    def_mult, pace_mult, b2b, b2b_known, cgaps = context_multipliers(row, team_stats)
    gaps.extend(cgaps)
    b2b_mult = B2B_FACTOR if b2b else 1.0
    minute_ratio = bfactor * ffactor * b2b_mult
    rate = per_minute_rate(row, sport, gaps)
    shift, delta = production_shift(row, clock, rate, minute_ratio, def_mult, pace_mult)
    mid = market_mid(row)
    price = pay_price(row, mid)
    p_pacer = None
    if row.p_model_raw is not None and row.side in {"over", "under"}:
        p_pacer = _shift_side(row.p_model_raw, row.side, shift)
    p_old = row.p_old_field
    if p_old is None and row.p_model_raw is not None and mid is not None:
        p_old = 0.5 * row.p_model_raw + 0.5 * mid
    base_rem = clock["remaining"]
    adj_rem = None if base_rem is None else base_rem * minute_ratio
    return {
        "key": row_key(row),
        "ts": row.ts,
        "ts_epoch": row.ts_epoch,
        "game_id": row.game_id,
        "player": row.player,
        "stat": row.stat,
        "side": row.side,
        "line": row.line,
        "cur": row.cur,
        "price": price,
        "bid": row.bid,
        "ask": row.ask,
        "market_mid": mid,
        "p_model_raw": row.p_model_raw,
        "p_old": p_old,
        "p_pacer": p_pacer,
        "shift": shift,
        "delta_stat": delta,
        "rate_per_min": rate,
        "elapsed_min": clock["elapsed"],
        "ot": bool(clock["ot"]),
        "remaining_min_base": base_rem,
        "remaining_min_adj": adj_rem,
        "blowout_factor": bfactor,
        "foul_factor": ffactor,
        "b2b_factor": b2b_mult,
        "b2b": b2b,
        "b2b_known": b2b_known,
        "def_mult": def_mult,
        "pace_mult": pace_mult,
        "result": row.result,
        "push": row.push,
        "pnl_row": row.pnl,
        "y": None if row.result is None else (1.0 if row.result else 0.0),
        "gaps": gaps,
        "sport": sport,
    }


def decide(row: Row, model: dict, team_stats: dict | None = None) -> dict:
    proj = project(row, team_stats)
    return apply_model(proj, model)


def apply_model(proj: dict, model: dict) -> dict:
    w = float(model.get("w", DEFAULT_W))
    edge_min = float(model.get("edge_min", EDGE_MIN))
    early = float(model.get("early_skip_min", EARLY_SKIP_MIN))
    late = float(model.get("late_skip_min", LATE_SKIP_MIN))
    overs = model.get("overs") or DEFAULT_OVERS
    iso = model.get("isotonic") or default_model()["isotonic"]

    p_blend = None
    p = None
    if proj["p_pacer"] is not None and proj["market_mid"] is not None:
        p_blend = w * proj["p_pacer"] + (1.0 - w) * proj["market_mid"]
        p = apply_isotonic(iso, p_blend)
    elif proj["p_pacer"] is not None:
        # No market quote: the blend has nothing to mix, so the weight is ignored.
        p_blend = proj["p_pacer"]
        p = apply_isotonic(iso, p_blend)

    edge = None if p is None or proj["price"] is None else p - proj["price"]
    clock = {
        "elapsed": proj["elapsed_min"],
        "remaining": proj["remaining_min_base"],
        "ot": bool(proj.get("ot")),
    }
    reason = _skip_reason(proj, p, edge, edge_min, overs, early, late, clock)
    out = {
        "key": proj["key"],
        "ts": proj["ts"],
        "game_id": proj["game_id"],
        "player": proj["player"],
        "stat": proj["stat"],
        "side": proj["side"],
        "line": _r(proj["line"]),
        "cur": _r(proj["cur"]),
        "price": _r(proj["price"]),
        "bid": _r(proj["bid"]),
        "ask": _r(proj["ask"]),
        "market_mid": _r(proj["market_mid"]),
        "p_model_raw": _r(proj["p_model_raw"]),
        "p_old": _r(proj["p_old"]),
        "p_pacer": _r(proj["p_pacer"]),
        "p_blend": _r(p_blend),
        "p": _r(p),
        "edge": _r(edge),
        "action": "skip" if reason else "bet",
        "skip_reason": reason,
        "w": _r(w),
        "def_mult": _r(proj["def_mult"]),
        "pace_mult": _r(proj["pace_mult"]),
        "elapsed_min": _r(proj["elapsed_min"]),
        "ot": bool(proj.get("ot")),
        "remaining_min_base": _r(proj["remaining_min_base"]),
        "remaining_min_adj": _r(proj["remaining_min_adj"]),
        "blowout_factor": _r(proj["blowout_factor"]),
        "foul_factor": _r(proj["foul_factor"]),
        "b2b_factor": _r(proj["b2b_factor"]),
        "b2b": bool(proj["b2b"]),
        "b2b_known": bool(proj["b2b_known"]),
        "rate_per_min": _r(proj["rate_per_min"]),
        "delta_stat": _r(proj["delta_stat"]),
        "result": proj["result"],
        "push": bool(proj["push"]),
        "pnl_row": _r(proj["pnl_row"]),
        "gaps": sorted(set(proj["gaps"])),
    }
    return out


def _skip_reason(proj, p, edge, edge_min, overs, early, late, clock) -> str | None:
    if proj["side"] not in {"over", "under"}:
        return "no_side"
    if proj["p_model_raw"] is None or proj["p_pacer"] is None or p is None:
        return "no_model"
    why = timing_reason(clock, early, late)
    if why:
        return why
    if proj["price"] is None:
        return "no_price"
    if edge is None or edge < edge_min - 1e-9:
        return "edge"
    if proj["side"] == "over":
        lo = float(overs.get("price_lo", DEFAULT_OVERS["price_lo"]))
        hi = float(overs.get("price_hi", DEFAULT_OVERS["price_hi"]))
        mh = float(overs.get("min_hit_p", DEFAULT_OVERS["min_hit_p"]))
        if proj["price"] < lo or proj["price"] > hi:
            return "overs_band"
        if p < mh:
            return "overs_min_p"
    return None


def market_mid(row: Row) -> float | None:
    if row.bid is not None and row.ask is not None:
        return 0.5 * (row.bid + row.ask)
    if row.price is not None:
        return row.price
    if row.bid is not None:
        return row.bid
    if row.ask is not None:
        return row.ask
    return None


def pay_price(row: Row, mid: float | None) -> float | None:
    if row.price is not None:
        return row.price
    if row.ask is not None:
        return row.ask
    return mid


def _shift_side(p_raw: float, side: str, shift: float) -> float:
    p_over = p_raw if side == "over" else 1.0 - p_raw
    p_over = float(np.clip(p_over, 1e-4, 1.0 - 1e-4))
    moved = float(expit(float(logit(p_over)) + shift))
    moved = float(np.clip(moved, 0.01, 0.99))
    return moved if side == "over" else 1.0 - moved


def fit_w(p_pacer, mid, y, min_n: int = MIN_FIT_N) -> tuple[float, bool]:
    """Blend weight minimizing Brier score. Stays at 0.5 below min_n."""
    p_pacer = np.asarray(p_pacer, dtype=float)
    mid = np.asarray(mid, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(y) < min_n:
        return DEFAULT_W, False
    # Unidentified only when pacer and market are the same number on every row.
    # A constant gap is still identified: w just slides the blend between two points.
    if float(np.max(np.abs(p_pacer - mid))) < 1e-8:
        return DEFAULT_W, False

    def brier(w: float) -> float:
        p = w * p_pacer + (1.0 - w) * mid
        return float(np.mean((p - y) ** 2))

    res = minimize_scalar(brier, bounds=(0.0, 1.0), method="bounded", options={"xatol": 1e-4})
    if not res.success:
        return DEFAULT_W, False
    return float(res.x), True


def fit_isotonic(p, y, min_n: int = MIN_FIT_N) -> dict:
    """Map blended p to observed hit rate. Identity when the sample is thin or one-sided."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    n = int(len(y))
    if n < min_n or len(np.unique(y)) < 2:
        return {"x": [0.0, 1.0], "y": [0.0, 1.0], "n": n, "identity": True}
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    iso.fit(p, y)
    xs = [round(float(v), 6) for v in iso.X_thresholds_]
    ys = [round(float(v), 6) for v in iso.y_thresholds_]
    return {"x": xs, "y": ys, "n": n, "identity": False}


def apply_isotonic(iso: dict, p: float) -> float:
    xs = iso.get("x") or [0.0, 1.0]
    ys = iso.get("y") or [0.0, 1.0]
    if len(xs) == 1:
        return float(ys[0])
    return float(np.interp(p, xs, ys))


def fit_overs(p, price, y, is_over, min_n: int = MIN_FIT_N) -> dict:
    """Price band and minimum hit probability for overs, maximizing train ROI.

    A cell must contain at least ``min_n`` graded overs that also clear the edge gate.
    With no such cell the unfitted default band is kept.
    """
    p = np.asarray(p, dtype=float)
    price = np.asarray(price, dtype=float)
    y = np.asarray(y, dtype=float)
    is_over = np.asarray(is_over, dtype=bool)
    best = None
    for lo in _LO:
        for hi in _HI:
            if hi <= lo:
                continue
            for mh in _MINP:
                mask = (
                    is_over
                    & (price >= lo)
                    & (price <= hi)
                    & (p >= mh)
                    & ((p - price) >= EDGE_MIN - 1e-9)
                )
                n = int(mask.sum())
                if n < min_n:
                    continue
                cost = float(price[mask].sum())
                if cost <= 0:
                    continue
                roi = float((y[mask] - price[mask]).sum() / cost)
                # Higher ROI, then more bets, then a wider band, then a lower hit floor.
                key = (round(roi, 6), n, round(hi - lo, 6), -mh)
                if best is None or key > best[0]:
                    best = (key, lo, hi, mh, n, roi)
    if best is None:
        out = dict(DEFAULT_OVERS)
        out["fitted"] = False
        return out
    _, lo, hi, mh, n, roi = best
    return {
        "price_lo": lo,
        "price_hi": hi,
        "min_hit_p": mh,
        "fitted": True,
        "n": n,
        "train_roi": round(roi, 6),
    }


def fit_model(rows: list[Row], team_stats: dict | None = None, min_n: int = MIN_FIT_N) -> dict:
    """Fit blend weight, isotonic map and overs rule on these rows. Chronology is the caller's job."""
    projs = [project(r, team_stats) for r in rows]
    graded = [
        p for p in projs
        if p["y"] is not None and p["p_pacer"] is not None and p["market_mid"] is not None
    ]
    model = default_model()
    model["fit_rows"] = len(graded)
    if not graded:
        return model
    w, fitted = fit_w(
        [p["p_pacer"] for p in graded],
        [p["market_mid"] for p in graded],
        [p["y"] for p in graded],
        min_n=min_n,
    )
    w = round(w, 6)
    model["w"] = w
    model["w_fitted"] = fitted
    blends = [w * p["p_pacer"] + (1.0 - w) * p["market_mid"] for p in graded]
    model["isotonic"] = fit_isotonic(blends, [p["y"] for p in graded], min_n=min_n)
    cal_p, cal_price, cal_y, cal_over = [], [], [], []
    for p, b in zip(graded, blends):
        if p["price"] is None:
            continue
        # The overs rule is a betting filter, so it is fit on rows the clock would still allow.
        clock = {"elapsed": p["elapsed_min"], "remaining": p["remaining_min_base"], "ot": p["ot"]}
        if timing_reason(clock, EARLY_SKIP_MIN, LATE_SKIP_MIN):
            continue
        cal_p.append(apply_isotonic(model["isotonic"], b))
        cal_price.append(p["price"])
        cal_y.append(p["y"])
        cal_over.append(p["side"] == "over")
    if cal_p:
        model["overs"] = fit_overs(cal_p, cal_price, cal_y, cal_over, min_n=min_n)
    return model


def recalibrate(rows: list[Row], model: dict, team_stats: dict | None = None,
                days: int = 28, min_n: int = MIN_FIT_N) -> dict:
    """Refit only the isotonic map on graded rows in the last ``days`` before the newest ts.

    Blend weight and the overs band stay as they are. A window under ``min_n`` leaves
    the saved isotonic map untouched.
    """
    model = deepcopy(model)
    epochs = [r.ts_epoch for r in rows if r.ts_epoch is not None]
    model["recalib_days"] = days
    if not epochs:
        model["recalib_rows"] = 0
        model["recalib_skipped"] = "no timestamps"
        return model
    tmax = max(epochs)
    cutoff = tmax - days * 86400.0
    recent = [r for r in rows if r.ts_epoch is not None and r.ts_epoch >= cutoff]
    projs = [project(r, team_stats) for r in recent]
    graded = [
        p for p in projs
        if p["y"] is not None and p["p_pacer"] is not None and p["market_mid"] is not None
    ]
    model["recalib_rows"] = len(graded)
    model["recalib_through"] = tmax
    if len(graded) < min_n:
        model["recalib_skipped"] = f"fewer than {min_n} graded rows"
        return model
    w = float(model.get("w", DEFAULT_W))
    blends = [w * p["p_pacer"] + (1.0 - w) * p["market_mid"] for p in graded]
    fitted = fit_isotonic(blends, [p["y"] for p in graded], min_n=min_n)
    previous = model.get("isotonic") or {}
    if fitted["identity"] and not previous.get("identity", True):
        model["recalib_skipped"] = "window is one-sided; kept the previous isotonic map"
        return model
    model["isotonic"] = fitted
    model.pop("recalib_skipped", None)
    return model


def split_games(rows: list[Row], train_frac: float = 0.6) -> tuple[list[Row], list[Row], list[str], list[str]]:
    """Chronological game-disjoint split. Games are ordered by their first timestamp.

    The first ``train_frac`` of games are train; the rest are test. A game id is never in both.
    Rows with no game_id are their own group so they cannot leak into another game.
    """
    groups: dict[str, list[Row]] = {}
    for i, row in enumerate(rows):
        gid = row.game_id or f"__row_{i}_{row_key(row)}"
        groups.setdefault(gid, []).append(row)

    def first_ts(items: list[Row]) -> float:
        ts = [r.ts_epoch for r in items if r.ts_epoch is not None]
        return min(ts) if ts else 0.0

    ordered = sorted(groups.items(), key=lambda kv: (first_ts(kv[1]), kv[0]))
    n = len(ordered)
    n_train = int(n * train_frac)
    if n >= 2:
        n_train = min(max(n_train, 1), n - 1)
    train_ids = [gid for gid, _ in ordered[:n_train]]
    test_ids = [gid for gid, _ in ordered[n_train:]]
    train_set = set(train_ids)
    train, test = [], []
    for gid, items in ordered:
        (train if gid in train_set else test).extend(items)
    return train, test, train_ids, test_ids


def _r(v):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float, np.floating, np.integer)):
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            return None
        return round(x, 6)
    return v
