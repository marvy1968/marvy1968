"""Walk-forward isotonic calibration for the Kalshi NBA pacer gate and Kelly check.

Off unless KALSHI_PACER_ISOTONIC=on (or data/KALSHI_PACER_ISOTONIC). The only
lane it can turn on is kalshi nba_prop_live_pacer. Polymarket nba_prop_live is
excluded: out of sample the same rule cost about $2,000 there.

Fit uses only prior settled fills (settled before this decision). The calibrated
p is applied to the existing 62% / 5c gate and to the Kelly check. A failure
skips the bet. The price is not changed and a kept bet's stake is not resized.
There is no per-game exposure cap in this module; the walk-forward did not
support picking one.

This is not a re-enable. On the 2026-11-12 study the Kalshi pacer cell was
+$82.74 ΔP&L with CI [16.79, 179.85], 1 of 120 tests, and the kept book was
still -$68.66. Brier and log-loss did not improve.
"""
import csv
import json
import os

FEE = 0.07
CAP = 0.65
P_GATE = 0.62
MIN_EDGE = 0.05
MIN_N = 30
ALLOWED = {("kalshi", "nba_prop_live_pacer")}
# Explicitly not eligible. Do not drop this exclusion.
EXCLUDED = {("pm", "nba_prop_live")}


def _norm_venue(venue):
    v = str(venue or "").strip().lower()
    if v in ("kalshi", "kalshi_demo"):
        return "kalshi"
    if v in ("pm", "polymarket", "polymarket_paper"):
        return "pm"
    return v


def enabled(venue="kalshi", lane="nba_prop_live_pacer"):
    key = (_norm_venue(venue), lane)
    if key in EXCLUDED or key not in ALLOWED:
        return False
    flag = str(os.environ.get("KALSHI_PACER_ISOTONIC", "off")).lower()
    if flag in ("on", "1", "true", "yes"):
        return True
    data = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "KALSHI_PACER_ISOTONIC")
    return os.path.exists(data)


def fit_isotonic(ps, ys):
    """PAV blocks. Each block is (mean p, mean outcome), clipped to [0, 1]."""
    pairs = sorted(zip(ps, ys), key=lambda t: (t[0], t[1]))
    merged = []
    for x, y in pairs:
        x = float(x)
        y = min(1.0, max(0.0, float(y)))
        if merged and abs(merged[-1][0] - x) <= 1e-12:
            merged[-1][1].append(y)
        else:
            merged.append([x, [y]])
    blocks = []
    for x, group in merged:
        blocks.append({"w": float(len(group)), "sy": sum(group), "sx": x * len(group)})
        while len(blocks) >= 2 and (blocks[-2]["sy"] / blocks[-2]["w"]) > (blocks[-1]["sy"] / blocks[-1]["w"]) + 1e-15:
            b = blocks.pop()
            a = blocks.pop()
            blocks.append({"w": a["w"] + b["w"], "sy": a["sy"] + b["sy"], "sx": a["sx"] + b["sx"]})
    return [(b["sx"] / b["w"], min(1.0, max(0.0, b["sy"] / b["w"]))) for b in blocks]


def predict_isotonic(centers, x):
    """Linear interpolation between adjacent block means. Ends are clipped, not extrapolated."""
    x = float(x)
    if not centers:
        return min(1.0, max(0.0, x))
    if x <= centers[0][0]:
        return centers[0][1]
    if x >= centers[-1][0]:
        return centers[-1][1]
    for i in range(len(centers) - 1):
        x0, y0 = centers[i]
        x1, y1 = centers[i + 1]
        if x0 <= x <= x1:
            if x1 <= x0:
                return y0
            w = (x - x0) / (x1 - x0)
            return min(1.0, max(0.0, y0 + w * (y1 - y0)))
    return centers[-1][1]


def calibrate(p, prior):
    """prior rows are {p, won} and must already be settled before this decision.
    Fewer than 30 probabilities is a pass-through of the raw p."""
    usable = [(float(r["p"]), 1.0 if r.get("won") else 0.0) for r in prior if r.get("p") is not None]
    if len(usable) < MIN_N:
        return float(p), "passthrough"
    centers = fit_isotonic([x for x, _ in usable], [y for _, y in usable])
    return predict_isotonic(centers, float(p)), "isotonic"


def gate_ok(p, price, fee_rate=FEE):
    pc = min(float(p), CAP)
    q = float(price)
    edge = pc - q - fee_rate * q * (1.0 - q)
    return (edge >= MIN_EDGE or (pc >= P_GATE and edge > 0)), edge


def decide(p, price, prior, fee_rate=FEE):
    """Skip or keep. price is echoed unchanged. resized is always false."""
    p_cal, how = calibrate(p, prior)
    q = float(price)
    ok, edge = gate_ok(p_cal, q, fee_rate)
    if not ok:
        reason = "isotonic gate"
        action = "skip"
    elif p_cal <= q:
        # Quarter-Kelly would be 0. Unreachable when the gate above already failed, kept anyway.
        reason = "isotonic sizing"
        action = "skip"
    else:
        reason = None
        action = "keep"
    return {"action": action, "reason": reason, "p_cal": p_cal, "price": q, "resized": False,
            "how": how, "edge": edge}


def maybe_skip(p, price, prior, venue="kalshi", lane="nba_prop_live_pacer"):
    """None when the flag is off, the lane is not the Kalshi pacer, or the bet still passes."""
    if not enabled(venue, lane):
        return None
    decision = decide(p, price, prior)
    if decision["action"] == "skip":
        return decision["reason"]
    return None


def walk_forward(fills):
    """Chronological skip-only replay. fills need filled, settled, p, won, price, pnl.
    Training rows are those with settled < this filled. Kept pnl is the original pnl."""
    ordered = sorted(fills, key=lambda r: r["filled"])
    out = []
    for i, row in enumerate(ordered):
        prior = []
        for j, other in enumerate(ordered):
            if j == i:
                continue
            if other.get("settled") and other["settled"] < row["filled"] and other.get("p") is not None:
                prior.append({"p": other["p"], "won": other.get("won")})
        decision = decide(row["p"], row["price"], prior)
        decision["pnl"] = row.get("pnl") if decision["action"] == "keep" else None
        decision["filled"] = row["filled"]
        out.append(decision)
    return out


def _jsonl(path):
    try:
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except OSError:
        return []


def load_prior_kalshi(ledger_path, demo_log_path, lane="nba_prop_live_pacer"):
    """Settled win/loss rows for one lane. p comes from demo_log p_side, else fair_c/100.
    Missing files yield an empty prior (the calibrator then pass-throughs)."""
    p_by_id = {}
    for row in _jsonl(demo_log_path):
        order = row.get("order") or {}
        oid = order.get("order_id") or row.get("order_id")
        p = row.get("p_side")
        if p is None and row.get("fair_c") is not None:
            p = float(row["fair_c"]) / 100.0
        if oid and p is not None:
            p_by_id[oid] = float(p)
    if not ledger_path or not os.path.exists(ledger_path):
        return []
    out = []
    with open(ledger_path, newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("me_type") != lane and row.get("bet_type") != lane:
                continue
            if row.get("result") not in ("win", "loss"):
                continue
            if row.get("status") not in (None, "", "settled"):
                continue
            out.append({"p": p_by_id.get(row.get("order_id")), "won": row["result"] == "win",
                        "settled": row.get("settled_ts") or "", "filled": row.get("filled_ts") or "",
                        "order_id": row.get("order_id")})
    return out
