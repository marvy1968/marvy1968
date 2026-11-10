"""Graded-bet comparison for the old blend, pacer v1, and pacer v2.

A bet is a contract that costs ``price`` and pays 1 if the side wins.
ROI is total profit over total cost. Slippage buys the same contract at
``price + 0.02`` (capped at 0.99). Row-level ``pnl`` is not the numerator:
books stake in different units, and a 2-cent move has to be applied to the
same price the edge gate used.

Only graded, non-push bets count. ``n_ge_30`` is false when n is under 30.
"""

from __future__ import annotations

from . import EDGE_MIN, SLIPPAGE_CENTS

DEFINITION = (
    "ROI is contract profit over capital: cost is price, payout is 1 on a win. "
    "roi_slip_2c uses cost min(0.99, price+0.02). "
    "old bets when p_old - price >= 0.15 (p_old is the row field, else 0.5*p_model_raw+0.5*market_mid). "
    "v1 bets when p_model_raw - price >= 0.15. "
    "v2 bets when action is bet (blend, isotonic, 0.15 edge, overs band, timing). "
    "Pushes and ungraded rows are excluded."
)


def evaluate(decisions: list[dict]) -> dict:
    return {
        "old": summarize(_select(decisions, "old")),
        "v1": summarize(_select(decisions, "v1")),
        "v2": summarize(_select(decisions, "v2")),
        "definition": DEFINITION,
    }


def summarize(bets: list[tuple[bool, float]]) -> dict:
    n = len(bets)
    if n == 0:
        return {"n": 0, "wins": 0, "wr": None, "roi": None, "roi_slip_2c": None, "n_ge_30": False}
    wins = sum(1 for won, _ in bets if won)
    cost = sum(price for _, price in bets)
    pnl = sum((1.0 if won else 0.0) - price for won, price in bets)
    slipped = [min(0.99, price + SLIPPAGE_CENTS) for _, price in bets]
    scost = sum(slipped)
    spnl = sum((1.0 if won else 0.0) - sp for (won, _), sp in zip(bets, slipped))
    return {
        "n": n,
        "wins": wins,
        "wr": wins / n,
        "roi": pnl / cost if cost else None,
        "roi_slip_2c": spnl / scost if scost else None,
        "n_ge_30": n >= 30,
    }


def _select(decisions: list[dict], system: str) -> list[tuple[bool, float]]:
    out = []
    for d in decisions:
        if d.get("push") or d.get("result") is None:
            continue
        price = d.get("price")
        if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
            continue
        if system == "v2":
            if d.get("action") != "bet":
                continue
        elif system == "v1":
            p = d.get("p_model_raw")
            if not _edge(p, price):
                continue
        elif system == "old":
            p = d.get("p_old")
            if not _edge(p, price):
                continue
        else:
            raise ValueError(system)
        out.append((bool(d["result"]), float(price)))
    return out


def _edge(p, price: float) -> bool:
    if not isinstance(p, (int, float)) or isinstance(p, bool):
        return False
    return (float(p) - price) >= EDGE_MIN - 1e-9


def format_table(metrics: dict) -> str:
    header = f"{'system':<8} {'n':>5} {'wins':>5} {'wr':>8} {'roi':>8} {'roi+2c':>8}  n>=30"
    lines = [header]
    for name in ("old", "v1", "v2"):
        m = metrics[name]
        flag = "yes" if m["n_ge_30"] else "no"
        lines.append(
            f"{name:<8} {m['n']:5d} {m['wins']:5d} {_fmt(m['wr'])} {_fmt(m['roi'])} {_fmt(m['roi_slip_2c'])}  {flag}"
        )
    return "\n".join(lines)


def _fmt(x) -> str:
    if x is None:
        return f"{'n/a':>8}"
    return f"{x:8.3f}"
