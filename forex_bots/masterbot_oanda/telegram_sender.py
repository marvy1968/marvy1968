import logging
import os
from typing import Any, Iterable

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_ENABLED = os.getenv("TELEGRAM_ALERTS_ENABLED", "true").lower() == "true"
TELEGRAM_TIMEOUT = float(os.getenv("TELEGRAM_TIMEOUT", "10") or 10)

UNIT_SIZE_PCT = float(os.getenv("UNIT_SIZE_PCT", "1.0") or 1.0)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

UNIT_SIZE_PCT = float(os.getenv("UNIT_SIZE_PCT", "1.0"))

def _calc_bankroll_pct(signal: dict[str, Any]) -> float:
    units = float(
        signal.get("capped_units")
        or signal.get("recommended_units")
        or signal.get("aj_units")
        or signal.get("bet_size")
        or 0.0
    )
    return round(units * UNIT_SIZE_PCT, 2)


def _to_raw_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return default
            if s.endswith("%"):
                return float(s[:-1])
            return float(s)
        return float(v)
    except Exception:
        return default


def _to_ratio(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return default
            if s.endswith("%"):
                return float(s[:-1]) / 100.0
            val = float(s)
        else:
            val = float(v)
        if val > 1.0:
            val /= 100.0
        return val
    except Exception:
        return default


def _norm(v: Any) -> str:
    return " ".join(str(v or "").strip().split())


def _escape(text: Any) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _market(item: dict[str, Any]) -> str:
    raw = _norm(item.get("market", "")).lower()
    aliases = {
        "h2h": "MONEYLINE",
        "ml": "MONEYLINE",
        "money line": "MONEYLINE",
        "moneyline": "MONEYLINE",
        "spread": "SPREAD",
        "spreads": "SPREAD",
        "total": "TOTAL",
        "totals": "TOTAL",
        "team total": "TEAM_TOTAL",
        "team totals": "TEAM_TOTAL",
        "team_total": "TEAM_TOTAL",
        "tt": "TEAM_TOTAL",
        "middle": "MIDDLE",
        "middles": "MIDDLE",
    }
    return aliases.get(raw, raw.upper())


def _game(item: dict[str, Any]) -> str:
    return _norm(item.get("game", ""))


def _team(item: dict[str, Any]) -> str:
    return _norm(item.get("team", item.get("display_side", item.get("side", item.get("selection", "")))))


def _side(item: dict[str, Any]) -> str:
    return _norm(item.get("side", item.get("selection", "")))


def _strength_text(item: dict[str, Any]) -> str:
    strength = _norm(item.get("strength", item.get("tier", "")))
    return strength if strength else "?"


def _line_text(item: dict[str, Any]) -> str:
    line = item.get("line")
    if line is None or line == "":
        return ""
    try:
        f = _to_raw_float(line)
        if float(f).is_integer():
            return str(int(f))
        return str(round(f, 2))
    except Exception:
        return str(line)


def _edge_text(item: dict[str, Any]) -> str:
    val = _to_ratio(item.get("display_edge", item.get("edge", 0.0)), 0.0)
    return f"{round(val * 100.0, 2)}%"


def _conf_text(item: dict[str, Any]) -> str:
    val = _to_ratio(item.get("confidence", 0.0), 0.0)
    return f"{round(val * 100.0, 1)}%"


def _ev_text(item: dict[str, Any]) -> str:
    if item.get("ev_percent") not in (None, ""):
        return f"{round(_to_raw_float(item.get('ev_percent'), 0.0), 2)}%"
    if item.get("ev") not in (None, ""):
        return f"{round(_to_ratio(item.get('ev'), 0.0) * 100.0, 2)}%"
    return "0.0%"


def _extract_bankroll_pct(signal: dict[str, Any]) -> float:
    for key in (
        "recommended_bankroll_pct",
        "bankroll_pct",
        "recommended_pct",
        "bet_percent",
        "risk_pct",
    ):
        if signal.get(key) not in (None, ""):
            return round(_to_raw_float(signal.get(key), 0.0), 2)

    if signal.get("kelly_quarter") not in (None, ""):
        kq = _to_raw_float(signal.get("kelly_quarter"), 0.0)
        return round(kq * 100.0 if kq <= 1.0 else kq, 2)

    units = _to_raw_float(
        signal.get("capped_units")
        or signal.get("recommended_units")
        or signal.get("aj_units")
        or signal.get("bet_size")
        or 0.0,
        0.0,
    )
    return round(units * UNIT_SIZE_PCT, 2)


def _bankroll_text(item: dict[str, Any]) -> str:
    return f"{_extract_bankroll_pct(item)}%"


def _send_message(text: str) -> bool:
    if not TELEGRAM_ENABLED:
        logger.info("Telegram alerts disabled; skipping send.")
        return False

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram token/chat id missing; cannot send alert.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=TELEGRAM_TIMEOUT)
        if resp.status_code == 429:
            print("🛡️ 429 Rate Limit Hit. Shield active, sleeping 60s...")
            import time; time.sleep(60)
        else:
            resp.raise_for_status()
        logger.info("Telegram alert sent.")
        return True
    except Exception as e:
        logger.exception("Telegram send failed: %s", e)
        return False


def _format_edge(item: dict[str, Any]) -> str:
    game = _escape(_game(item))
    market = _escape(_market(item))
    team = _escape(_team(item))
    line = _escape(_line_text(item))
    edge = _escape(_edge_text(item))
    conf = _escape(_conf_text(item))
    ev = _escape(_ev_text(item))
    bankroll = _escape(_bankroll_text(item))
    strength = _escape(_strength_text(item))
    suspicious = bool(item.get("suspicious", False))

    suspicious_text = " | suspicious=True" if suspicious else ""
    line_text = f" {line}" if line else ""

    sys_val = item.get("system_value")
    mkt_val = item.get("market_value")
    raw_market = str(item.get("market", "")).lower()

    if sys_val is None:
        if "total" in raw_market:
            sys_val = item.get("model_total")
            mkt_val = item.get("market_total", item.get("line"))
        elif "spread" in raw_market:
            sys_val = item.get("model_margin")
            mkt_val = item.get("home_spread_line", item.get("line"))

    sport_raw = str(item.get("sport_key", item.get("league", ""))).upper().replace("BASKETBALL_", "")
    if "WNCAAB" in sport_raw:
        sport_tag = "🏀 NCAAW"
    elif "NCAAB" in sport_raw:
        sport_tag = "🏀 NCAAB"
    elif "NBA" in sport_raw:
        sport_tag = "🏀 NBA"
    else:
        sport_tag = f"🏀 {sport_raw}" if sport_raw else "🏀"

    diff_text = ""
    if sys_val is not None and mkt_val is not None:
        try:
            diff_text = (
                f"\n\n📊 <b>THE LINE</b>\n"
                f"🏛️ Market: {round(float(mkt_val), 2)}\n"
                f"🤖 Bot Fair: {round(float(sys_val), 2)}"
            )
        except (ValueError, TypeError):
            pass

    return (
        f"🔥 <b>GRADE [{strength}]</b> | 📈 <b>EDGE: {edge}</b>\n"
        f"{sport_tag} | <b>{game}</b>\n"
        f"🎯 <b>PLAY: {market} | {team}{line_text}</b>{diff_text}\n\n"
        f"💰 <b>THE MATH</b>\n"
        f"EV: {ev} | Conf: {conf}\n"
        f"💵 <b>Stake: {bankroll} Bankroll</b>{suspicious_text}"
    )


def _format_team_prop(item: dict[str, Any]) -> str:
    game = _escape(_game(item))
    team = _escape(_team(item))
    side = _escape(_side(item))
    line = _escape(_line_text(item))
    edge = _escape(_edge_text(item))
    conf = _escape(_conf_text(item))
    ev = _escape(_ev_text(item))
    bankroll = _escape(_bankroll_text(item))
    strength = _escape(_strength_text(item))
    suspicious = bool(item.get("suspicious", False))

    proj = item.get("proj", item.get("projection"))
    proj_text = ""
    if proj is not None:
        try:
            proj_text = f" | proj={round(_to_raw_float(proj), 2)}"
        except Exception:
            proj_text = f" | proj={proj}"

    suspicious_text = " | suspicious=True" if suspicious else ""

    return (
        f"🔥 <b>TEAM PROP [{strength}]</b> | 📈 <b>EDGE: {edge}</b>\n"
        f"⚔️ <b>{game}</b>\n"
        f"🎯 <b>PLAY: {team} Total {side} {line}</b>\n\n"
        f"💰 <b>THE MATH</b>\n"
        f"EV: {ev} | Conf: {conf}\n"
        f"💵 <b>Stake: {bankroll} Bankroll</b>{proj_text}{suspicious_text}"
    )


def _format_middle(item: dict[str, Any]) -> str:
    game = _escape(_game(item))
    strength = _escape(_strength_text(item))
    original_line = _escape(item.get("original_line", ""))
    live_line = _escape(item.get("live_line", ""))
    window = _escape(item.get("window", ""))
    conf = _escape(_conf_text(item))

    return (
        f"🎯 <b>MIDDLE [{strength}]</b>\n"
        f"{game}\n"
        f"original={original_line} | live={live_line} | window={window} | conf={conf}"
    )


def _format_generic(item: dict[str, Any]) -> str:
    game = _escape(_game(item))
    market = _escape(_market(item))
    selection = _escape(_side(item))
    edge = _escape(_edge_text(item))
    conf = _escape(_conf_text(item))
    ev = _escape(_ev_text(item))
    bankroll = _escape(_bankroll_text(item))
    strength = _escape(_strength_text(item))

    return (
        f"📌 <b>ALERT [{strength}]</b> | 📈 <b>EDGE: {edge}</b>\n"
        f"⚔️ <b>{game}</b>\n"
        f"🎯 <b>PLAY: {market} | {selection}</b>\n\n"
        f"💰 <b>THE MATH</b>\n"
        f"EV: {ev} | Conf: {conf}\n"
        f"💵 <b>Stake: {bankroll} Bankroll</b>"
    )


def format_alert(item: dict[str, Any]) -> str:
    market = _market(item)
    if market == "TEAM_TOTAL":
        return _format_team_prop(item)
    if market == "MIDDLE":
        return _format_middle(item)
    if market in {"TOTAL", "SPREAD", "MONEYLINE"}:
        return _format_edge(item)
    return _format_generic(item)


def send_alert(item: dict[str, Any]) -> bool:
    return _send_message(format_alert(item))


def send_alerts(items: Iterable[dict[str, Any]]) -> int:
    sent = 0
    for item in items:
        if send_alert(item):
            sent += 1
    return sent


def send_game_alerts(items: Iterable[dict[str, Any]]) -> int:
    return send_alerts(items)


def send_system_alert(message: str) -> bool:
    return _send_message(_escape(message))
