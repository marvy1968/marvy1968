"""Telegram report card for the stock and forex paper bots: `/report` (current status) and `/report 7` (last 7 days).

Reads only the bots' own state files (alpaca_paper/trades.jsonl, equity.jsonl, fx_paper/trades.jsonl, status.txt), so it never
calls a broker and never changes anything. The stock and forex sections stay separate. Paper money only."""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path


def _jl(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for ln in path.read_text().splitlines():
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
    return out


def _money(v: float) -> str:
    return f"{'-' if v < 0 else '+'}${abs(v):,.0f}"


def _pf(pnls: list[float]) -> str:
    gl = -sum(p for p in pnls if p <= 0)
    gw = sum(p for p in pnls if p > 0)
    return "n/a" if not pnls else ("inf" if gl == 0 and gw > 0 else f"{gw / gl:.2f}" if gl else "0.00")


def parse_days(text: str) -> int | None:
    """'/report' -> None (current), '/report 7' or '/report card 7' -> 7 (1-90)."""
    for w in text.replace(",", " ").split()[1:]:
        if w.isdigit():
            return max(1, min(90, int(w)))
    return None


def stock_section(state: Path, today: date, days: int | None) -> str:
    from alpacabot.paper import report_card, whole_trades

    d = state / "alpaca_paper"
    raw = _jl(d / "trades.jsonl")
    if not raw:
        return "STOCKS (Alpaca paper)\n  no trades recorded yet"
    if days is None:
        card = report_card(d, today).splitlines()
        keep = []
        for ln in card[2:]:                         # skip the title and the paper-only line
            if ln.startswith(("BY STYLE", "PROFIT GIVEBACK", "TRADE ZONES", "GO-LIVE GATE")):
                break
            if ln.startswith("Equity "):
                ln = "Own-book " + ln[0].lower() + ln[1:] + "  (a daily snapshot; the Alpaca app shows the live account)"
            if ln.strip():
                keep.append(ln)
        return "STOCKS (Alpaca paper)\n" + "\n".join(keep[:14])
    start = (today - timedelta(days=days - 1)).isoformat()
    rows = [t for t in raw if start <= t["date"] <= today.isoformat()]
    whole = [t for t in whole_trades(raw) if start <= t["date"] <= today.isoformat()]
    if not whole:
        return f"STOCKS (Alpaca paper), last {days} days\n  no trades"
    pnls = [t["pnl"] for t in whole]
    wins = sum(1 for p in pnls if p > 0)
    lines = [f"STOCKS (Alpaca paper), last {days} days ({start} to {today.isoformat()})",
             f"  {len(whole)} trades, {wins}W/{len(whole) - wins}L ({wins / len(whole):.0%}), P&L {_money(sum(pnls))}, profit factor {_pf(pnls)}"]
    by_day: dict[str, list[float]] = {}
    for t in whole:
        by_day.setdefault(t["date"], []).append(t["pnl"])
    for k in sorted(by_day):
        v = by_day[k]
        lines.append(f"  {k}: {len(v)} trades, {_money(sum(v))}")
    for label, key in (("long", "long"), ("short", "short")):
        v = [t["pnl"] for t in whole if t["side"] == key]
        if v:
            lines.append(f"  {label}s: {len(v)}, {_money(sum(v))}, PF {_pf(v)}")
    ex: dict[str, list[float]] = {}
    for t in rows:
        ex.setdefault(t.get("reason", "?"), []).append(t["pnl"])
    lines.append("  by exit: " + ", ".join(f"{k} {len(v)} {_money(sum(v))}" for k, v in sorted(ex.items(), key=lambda x: sum(x[1]))))
    ep: dict[str, list[float]] = {}
    for t in whole:
        if t.get("expert"):
            ep.setdefault(t["expert"], []).append(t["pnl"])
    if ep:
        lines.append("  by expert: " + ", ".join(f"{k} {len(v)} {_money(sum(v))}" for k, v in sorted(ep.items(), key=lambda x: -len(x[1]))))
    sym: dict[str, float] = {}
    for t in whole:
        sym[t["symbol"]] = sym.get(t["symbol"], 0.0) + t["pnl"]
    ranked = sorted(sym.items(), key=lambda x: x[1])
    best, worst = ranked[-1], ranked[0]
    lines.append(f"  best {best[0]} {_money(best[1])}, worst {worst[0]} {_money(worst[1])}")
    return "\n".join(lines)


def fx_section(state: Path, today: date, days: int | None) -> str:
    d = state / "fx_paper"
    if days is None:
        p = d / "status.txt"
        if not p.exists():
            return "FOREX (OANDA practice)\n  no status yet"
        ls = [ln for ln in p.read_text().splitlines() if ln.strip()]
        return "FOREX (OANDA practice)\n" + "\n".join("  " + ln for ln in ls[1:3] + ls[4:][:4])
    start = (today - timedelta(days=days - 1)).isoformat()
    led = [t for t in _jl(d / "trades.jsonl") if start <= str(t.get("closed", t.get("date", "")))[:10] <= today.isoformat()]
    if not led:
        return f"FOREX (OANDA practice), last {days} days\n  no closed trades"
    pl = [t["pl"] for t in led]
    lines = [f"FOREX (OANDA practice), last {days} days",
             f"  {len(led)} closed fills, {sum(1 for p in pl if p > 0)}W, P&L {_money(sum(pl))}"]
    for t in led[-8:]:
        lines.append(f"  {str(t.get('closed', ''))[:10]} {t.get('strategy', '?')} {t['instrument']} {t['side']} {_money(t['pl'])} ({t.get('reason', '?')})")
    return "\n".join(lines)


def report(state_dir, days: int | None = None, today: date | None = None) -> str:
    state = Path(state_dir)
    today = today or datetime.now(ZoneInfo("America/New_York")).date()
    head = "REPORT CARD - current status" if days is None else f"REPORT CARD - last {days} days"
    parts = [head + " (paper money only)"]
    for fn in (stock_section, fx_section):
        try:
            parts.append(fn(state, today, days))
        except Exception as exc:  # one section failing must not hide the other
            parts.append(f"{fn.__name__.split('_')[0].upper()}: unavailable ({type(exc).__name__})")
    return "\n\n".join(parts)[:3900]
