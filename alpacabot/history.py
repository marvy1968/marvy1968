"""Read-only account history report: which bot traded what, and how it did.

Pulls every order (for its client_order_id, which carries each bot's prefix: ab- = this bot, sx-, xc-, xp-, ...) and every fill from the
PAPER account, then matches fills first-in-first-out per bot and symbol into round trips. Nothing is ordered, cancelled or changed.

Style is read from the holding time of each round trip: scalp <= 30 min, day trade = closed the same session, swing = held overnight.
"""

import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import pandas as pd

ET = "America/New_York"
SCALP_MIN = 30


def bot_label(client_order_id: str | None) -> str:
    """Prefix before the first '-' or '_' (e.g. 'sx-AAPL-...' -> 'sx'); Alpaca's default UUID ids -> 'untagged'."""
    cid = client_order_id or ""
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", cid):
        return "untagged"
    m = re.match(r"([A-Za-z]+)[-_]", cid)
    return m.group(1).lower() if m else ("untagged" if not cid else cid[:6].lower())


def round_trips(fills: pd.DataFrame) -> pd.DataFrame:
    """fills: columns bot, symbol, time (tz-aware), side ('buy'/'sell'/'sell_short'), qty, price.
    FIFO per (bot, symbol); a fill that crosses zero closes the old lots and opens the rest the other way."""
    rows = []
    for (bot, sym), g in fills.sort_values("time").groupby(["bot", "symbol"]):
        lots: deque = deque()                      # (signed qty, price, time)
        for _, f in g.iterrows():
            q = float(f["qty"]) * (1 if f["side"] == "buy" else -1)
            while q and lots and (lots[0][0] > 0) != (q > 0):
                lq, lp, lt = lots[0]
                take = min(abs(q), abs(lq))
                side = 1 if lq > 0 else -1
                rows.append({"bot": bot, "symbol": sym, "side": "long" if side == 1 else "short", "qty": take, "entry": lp,
                             "exit": float(f["price"]), "opened": lt, "closed": f["time"], "pnl": side * (float(f["price"]) - lp) * take})
                lq = lq - side * take
                q = q + side * take
                if abs(lq) < 1e-9:
                    lots.popleft()
                else:
                    lots[0] = (lq, lp, lt)
            if abs(q) > 1e-9:
                lots.append((q, float(f["price"]), f["time"]))
    rt = pd.DataFrame(rows)
    if len(rt):
        rt["hold_min"] = (rt["closed"] - rt["opened"]).dt.total_seconds() / 60
        same = rt["opened"].dt.tz_convert(ET).dt.date == rt["closed"].dt.tz_convert(ET).dt.date
        rt["style"] = ["scalp" if (s and h <= SCALP_MIN) else "day" if s else "swing" for s, h in zip(same, rt["hold_min"])]
        rt["ret_pct"] = rt["pnl"] / (rt["entry"] * rt["qty"]) * 100
    return rt


def summarize(rt: pd.DataFrame, fills: pd.DataFrame) -> str:
    if not len(fills):
        return "No fills on this account in the period."
    out = [f"ACCOUNT HISTORY (paper) {fills['time'].min():%Y-%m-%d} .. {fills['time'].max():%Y-%m-%d}: {len(fills)} fills, "
           f"{fills['symbol'].nunique()} symbols, bots by order-id prefix: {', '.join(sorted(fills['bot'].unique()))}", ""]
    out.append("== BY BOT (closed round trips, before fees; 'ab' = the new Marv bot)")
    out.append(f"{'bot':10s} {'trips':>6s} {'win%':>5s} {'P&L $':>10s} {'avg $':>8s} {'avg %':>6s} {'med hold':>9s} {'scalp/day/swing':>16s} "
               f"{'short%':>6s}  top symbols (trips)")
    for bot, g in rt.groupby("bot") if len(rt) else []:
        st = g["style"].value_counts()
        top = g["symbol"].value_counts().head(6)
        out.append(f"{bot:10s} {len(g):6d} {100 * (g.pnl > 0).mean():5.0f} {g.pnl.sum():10,.0f} {g.pnl.mean():8,.1f} {g.ret_pct.mean():6.2f} "
                   f"{fmt_hold(g.hold_min.median()):>9s} {st.get('scalp', 0):>5d}/{st.get('day', 0)}/{st.get('swing', 0):<5d} "
                   f"{100 * (g.side == 'short').mean():6.0f}  " + ", ".join(f"{s} ({n})" for s, n in top.items()))
    if len(rt):
        out += ["", "== BY STYLE (all bots)"]
        for style, g in rt.groupby("style"):
            out.append(f"{style:6s} trips {len(g):5d}  win {100 * (g.pnl > 0).mean():3.0f}%  P&L ${g.pnl.sum():>10,.0f}  avg {g.ret_pct.mean():+.3f}%/trip  "
                       f"symbols: {', '.join(g['symbol'].value_counts().head(8).index)}")
        out += ["", "== BY SYMBOL (all bots, best and worst by P&L)"]
        sym = rt.groupby("symbol").agg(trips=("pnl", "size"), win=("pnl", lambda x: 100 * (x > 0).mean()), pnl=("pnl", "sum"),
                                       bots=("bot", lambda x: "+".join(sorted(set(x)))), styles=("style", lambda x: "+".join(sorted(set(x)))))
        sym = sym.sort_values("pnl", ascending=False)
        show = pd.concat([sym.head(10), sym.tail(10)]).drop_duplicates() if len(sym) > 20 else sym
        for s, r in show.iterrows():
            out.append(f"{s:9s} trips {int(r.trips):4d}  win {r.win:3.0f}%  P&L ${r.pnl:>9,.0f}  bots {r.bots:12s} styles {r.styles}")
        shared = sym[sym["bots"].str.contains(r"\+")]
        if len(shared):
            out += ["", f"== SYMBOLS TRADED BY MORE THAN ONE BOT ({len(shared)}): " + ", ".join(f"{s} [{r.bots}]" for s, r in shared.head(20).iterrows())]
    open_lots = fills.groupby(["bot", "symbol"]).apply(lambda g: (g["qty"] * g["side"].map({"buy": 1}).fillna(-1)).sum(), include_groups=False)
    open_lots = open_lots[open_lots.abs() > 1e-9]
    if len(open_lots):
        out += ["", "== STILL OPEN (net shares by bot, from fills): " + ", ".join(f"{b}:{s} {q:+g}" for (b, s), q in open_lots.items())]
    out += ["", "Notes: P&L is from fill prices, before regulatory fees; crypto fills are included under their pair symbol.",
            "A round trip whose opening fill is older than the period start is not counted."]
    return "\n".join(out)


def fmt_hold(m: float) -> str:
    if m != m:
        return "-"
    return f"{m:.0f}m" if m < 120 else f"{m / 60:.1f}h" if m < 60 * 24 else f"{m / 1440:.1f}d"


# ------------------------------------------------------------------ fetching (GET only)
def fetch(days: int = 365) -> tuple[pd.DataFrame, pd.DataFrame]:
    import requests

    from .data import _headers
    api = "https://paper-api.alpaca.markets/v2"
    h, s = _headers(), requests.Session()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    orders, after = [], since.isoformat()
    while True:
        r = s.get(f"{api}/orders", headers=h, params={"status": "all", "limit": 500, "direction": "asc", "after": after, "nested": "true"}, timeout=60)
        r.raise_for_status()
        page = r.json()
        flat = []
        for o in page:
            flat.append(o)
            flat += o.get("legs") or []
        orders += flat
        if len(page) < 500:
            break
        after = page[-1]["submitted_at"] or page[-1]["created_at"]
    cid = {o["id"]: o.get("client_order_id") for o in orders}
    acts, token = [], None
    while True:
        params = {"direction": "asc", "page_size": 100, "after": since.strftime("%Y-%m-%d")}
        if token:
            params["page_token"] = token
        r = s.get(f"{api}/account/activities/FILL", headers=h, params=params, timeout=60)
        r.raise_for_status()
        page = r.json()
        acts += page
        if len(page) < 100:
            break
        token = page[-1]["id"]
    fills = pd.DataFrame([{"bot": bot_label(cid.get(a.get("order_id"))), "symbol": a["symbol"], "time": pd.Timestamp(a["transaction_time"]),
                           "side": "buy" if a["side"] == "buy" else "sell", "qty": float(a["qty"]), "price": float(a["price"])} for a in acts])
    if len(fills):
        fills["time"] = pd.to_datetime(fills["time"], utc=True)
    return fills, pd.DataFrame(orders)


def by_month(rt: pd.DataFrame) -> str:
    if not len(rt):
        return ""
    m = rt.assign(month=rt["closed"].dt.tz_convert(ET).dt.strftime("%Y-%m")).groupby("month")
    lines = ["", "== BY MONTH (closed trips)", f"{'month':8s} {'trips':>6s} {'win%':>5s} {'P&L $':>10s}   scalp $ / day $ / swing $"]
    for mon, g in m:
        st = g.groupby("style")["pnl"].sum()
        lines.append(f"{mon:8s} {len(g):6d} {100 * (g.pnl > 0).mean():5.0f} {g.pnl.sum():10,.0f}   "
                     f"{st.get('scalp', 0):,.0f} / {st.get('day', 0):,.0f} / {st.get('swing', 0):,.0f}")
    return "\n".join(lines)


def report(days: int = 365, stocks_only: bool = False) -> str:
    fills, _ = fetch(days)
    if stocks_only and len(fills):
        fills = fills[~fills["symbol"].str.contains("/")]
    rt = round_trips(fills) if len(fills) else pd.DataFrame()
    head = "STOCKS ONLY (crypto removed)\n" if stocks_only else ""
    return head + summarize(rt, fills) + by_month(rt)
