"""Bovada buy edge for AJ (Marvin 2026-10-25: "AJ is betting from telegram alerts so alert him their buy edge").
Display only, on the SAME alert: "💵 AJ's alert: Buy to −125 (edge +6% at −110)" = the worst Bovada price that still clears our EV bar
for this alert type, and the EV at the alert's price. Uses only the alert row (fair_p / fair_ev / ev_min) - no API calls.
EV bar: the alert's own ev_min (value), total_swing 10% (config total_swing min_ev), otherwise break-even (0%).
Max 2 extra lines per alert: if 2+ extra lines are already on the card, folds onto the last one with " · ".
Never blocks: any problem -> text unchanged. OFF: env BUY_EDGE_LINE=off or touch data/BUY_EDGE_LINE_OFF."""
import json, math, os
from pathlib import Path

OFF_FILE = Path(__file__).resolve().parent.parent / "data" / "BUY_EDGE_LINE_OFF"
BAR = {"total_swing": 0.10}


def on() -> bool:
    return os.environ.get("BUY_EDGE_LINE", "on").strip().lower() not in ("off", "0", "false", "no") and not OFF_FILE.exists()


def fix_on() -> bool:
    """2026-10-25 fix (shown win chance + Bovada-only price). OFF: env BUY_EDGE_BOVADA_FIX=off or data/BUY_EDGE_BOVADA_FIX_OFF."""
    return (os.environ.get("BUY_EDGE_BOVADA_FIX", "on").strip().lower() not in ("off", "0", "false", "no")
            and not (OFF_FILE.parent / "BUY_EDGE_BOVADA_FIX_OFF").exists())


def dec(a: float) -> float:
    return 1 + a / 100 if a > 0 else 1 + 100 / abs(a)


def am_floor(d: float) -> int:
    """Decimal -> American, rounded to the worst price that still clears (ceil works for both signs)."""
    if d >= 2:
        return math.ceil((d - 1) * 100 - 1e-9)
    return math.ceil(-100 / (d - 1) - 1e-9)


def fmt_am(a: float) -> str:
    a = int(round(a))
    return f"+{a}" if a > 0 else f"−{abs(a)}"


def line_for(strategy, price, fair_p, det, book=None) -> str:
    if price is None:
        return ""
    fe = det.get("fair_ev") if isinstance(det.get("fair_ev"), dict) else {}
    p = fe.get("p_win") if fe.get("p_win") is not None else fair_p
    if fix_on():
        # 2026-10-25 bug fix: use the win chance the alert SHOWS (calibrated paper_conf.p_cal) and price at BOVADA only
        # (AJ bets Bovada). Rows priced at the best book (BetRivers...) carry Bovada in my_book/my_price.
        pc = det.get("paper_conf") if isinstance(det.get("paper_conf"), dict) else {}
        if fe.get("p_win") is None and pc.get("p_cal") is not None:
            p = pc["p_cal"]
        if not (book and "bovada" in str(book).lower()):
            if "bovada" in str(det.get("my_book") or "").lower() and det.get("my_price") is not None:
                price, book = det["my_price"], "bovada"
            else:
                return ""                       # no Bovada price -> no AJ line
    if p is None:
        return ""
    p, push = float(p), float(fe.get("p_push") or 0)
    if not 0 < p < 1:
        return ""
    lose = max(0.0, 1 - p - push)
    bar = float(det["ev_min"]) if det.get("ev_min") is not None else BAR.get(str(strategy), 0.05 if fix_on() else 0.0)
    # (fix: the card's "good to" uses friendly.max_price default ev_min 5% when the row has none -> same bar here)
    ev = p * (dec(float(price)) - 1) - lose
    worst = am_floor(1 + (bar + lose) / p)
    where = "" if not book or "bovada" in str(book).lower() else f" {book}"
    if ev >= bar:
        return f"💵 AJ's alert: Buy to {fmt_am(worst)} (edge {ev:+.0%} at {fmt_am(price)}{where})"
    bar_txt = f", bar {bar:+.0%}" if fix_on() and bar > 0 else ""   # positive edge below the type's EV bar -> say why
    return f"💵 AJ's alert: Needs {fmt_am(worst)} or better (edge {ev:+.0%} at {fmt_am(price)}{where}{bar_txt})"


def append(db, alert_id, text: str, base_lines: int) -> str:
    try:
        if not on() or db is None or alert_id is None or "💵 AJ's alert" in text:
            return text
        ids = alert_id if isinstance(alert_id, (list, tuple)) else [alert_id]
        r = db.conn.execute("SELECT strategy, price, fair_p, details_json, book FROM alerts WHERE id=?", (int(ids[0]),)).fetchone()
        if r is None:
            return text
        ln = line_for(r[0], r[1], r[2], json.loads(r[3] or "{}") or {}, r[4])
        if not ln:
            return text
        if text.count("\n") - base_lines >= 2:
            return f"{text} · {ln}"
        return f"{text}\n{ln}"
    except Exception:  # noqa: BLE001
        return text
