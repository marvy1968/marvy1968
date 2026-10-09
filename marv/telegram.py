"""Telegram alerts via the Bot API."""

import html
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .models import Pick, Prediction
from .sports import Sport

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram's hard limit is 4096 characters per message
ET = ZoneInfo("America/New_York")
esc = html.escape


LABEL = "👽 <b>MARV</b>"  # first line of every Marv message, so it's never confused with another bot's alerts


def mirror_token() -> str:
    """MIRROR_BOT_TOKEN from the environment, forgiving copy-paste slips: spaces, quotes, a Windows line ending, or
    the variable name pasted twice (MIRROR_BOT_TOKEN=MIRROR_BOT_TOKEN=123:abc)."""
    raw = os.environ.get("MIRROR_BOT_TOKEN", "")
    raw = "".join(raw.split()).strip("\"'")
    return raw.split("=")[-1].strip("\"'")


def token_shape(token: str) -> str:
    """Describe a token without revealing it (length, parts, bot id digits)."""
    if not token:
        return "empty"
    left, _, right = token.partition(":")
    return (f"length {len(token)}, {len(left)} digits before the colon" if left.isdigit() else "no digits-then-colon start") + \
        (f", {len(right)} characters after it" if right else ", nothing after the colon") + \
        " (a real token is about 8-10 digits, a colon, then 35 characters)"


def _post(token: str, chat_id: str, text: str) -> None:
    for chunk in split_message(text):
        resp = requests.post(
            API.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
        resp.raise_for_status()


def send_message(token: str, chat_id: str, text: str, mirror: bool = True) -> None:
    """Send a Marv message, labelled "👽 MARV". With MIRROR_BOT_TOKEN (and optionally MIRROR_CHAT_ID) set in
    Marv's .env by the owner (their other alert chat, e.g. March_edge's) it is also delivered there; mirror=False keeps a message in the
    Marv chat only (command replies, failure alerts, the watchdog). A mirror failure never blocks the
    original message."""
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    text = f"{LABEL}\n{text}"
    _post(token, chat_id, text)
    # A private chat's id is the user's own Telegram id, so one account's two bot chats share it: when only
    # MIRROR_BOT_TOKEN is set, the mirror chat is the same id as the Marv chat.
    m_token = mirror_token()
    m_chat = os.environ.get("MIRROR_CHAT_ID", "") or chat_id
    if mirror and m_token and (m_token, m_chat) != (token, chat_id):
        try:
            _post(m_token, m_chat, text)
        except Exception as exc:
            log.warning("telegram mirror failed: %s", exc)


def send_document(token: str, chat_id: str, path, caption: str = "") -> None:
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    with open(path, "rb") as fh:
        resp = requests.post(API.format(token=token, method="sendDocument"),
                             data={"chat_id": chat_id, "caption": caption[:1000]},
                             files={"document": (str(path).rsplit("/", 1)[-1], fh, "application/pdf")}, timeout=60)
    resp.raise_for_status()


def get_chat_ids(token: str) -> list[tuple[str, str]]:
    """Chats that recently messaged the bot, as (chat_id, name)."""
    resp = requests.get(API.format(token=token, method="getUpdates"), timeout=30)
    resp.raise_for_status()
    seen = {}
    for update in resp.json().get("result", []):
        chat = (update.get("message") or update.get("channel_post") or {}).get("chat")
        if chat:
            seen[str(chat["id"])] = chat.get("title") or chat.get("username") or chat.get("first_name", "")
    return list(seen.items())


def split_message(text: str) -> list[str]:
    chunks, current = [], ""
    for block in text.split("\n\n"):
        while len(block) > MAX_LEN:  # a single oversized block: hard-split it
            chunks.append(block[:MAX_LEN])
            block = block[MAX_LEN:]
        if current and len(current) + len(block) + 2 > MAX_LEN:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return chunks


def _price(p: float) -> str:
    return f"{p:+.0f}"


def describe(pick: Pick, sport: Sport) -> str:
    if pick.market == "spread":
        line = "PK" if not pick.line else f"{pick.line + 0.0:+g}"
        return f"{esc(pick.side)} {line} ({_price(pick.price)}) [{sport.spread_label}]"
    if pick.market == "total":
        return f"{pick.side} {pick.line:g} ({_price(pick.price)})"
    if pick.market == "draw":
        return f"Draw ({_price(pick.price)})"
    return f"{esc(pick.side)} ML ({_price(pick.price)})"


def stake(pick: Pick, fraction: float = 0.25, cap: float = 0.03) -> str:
    """Quarter-Kelly stake as % of bankroll, capped at 3%; tells you to skip when the price has no value."""
    b = pick.price / 100 if pick.price > 0 else 100 / -pick.price
    kelly = (b * pick.prob - (1 - pick.prob)) / b
    if kelly <= 0:
        return "stake: skip (price too short for the win chance)"
    return f"stake: {min(kelly * fraction, cap):.1%} of bankroll"


def _model_call(pred: Prediction) -> str:
    g = pred.game
    if round(pred.model_margin) == 0:
        return "toss-up"
    leader = g.home if pred.model_margin > 0 else g.away
    return f"{esc(leader)} by {abs(pred.model_margin):.0f}"


def format_card(sport: Sport, preds: list[Prediction], record: dict | None = None,
                now: datetime | None = None, paper: bool = False, label: str = "") -> str:
    plays = [(p, pk) for p in preds for pk in p.picks if pk.active]
    core = [pk for p in preds for pk in p.picks]
    passed = sum(1 for pk in core if not pk.active)
    pass_rate = passed / len(core) if core else 0.0
    when = (now or datetime.now(ET)).astimezone(ET)

    blocks = [
        f"{sport.emoji} <b>Marv {esc(sport.name)} Predict Max</b> · {when:%a %b %d}"
        + (f" · <b>{esc(label)}</b> (fresh injuries &amp; stats)" if label else ""),
        f"{len(preds)} games simulated · {len(plays)} qualified plays · veto pass rate {pass_rate:.0%}",
    ]
    if paper:
        blocks.append("📝 <i>Paper trading: these picks are tracked, not bet, until the record proves an edge.</i>")
    if plays:
        rows = ["<b>✅ Qualified plays</b>"]
        for pred, pk in sorted(plays, key=lambda x: -x[1].edge):
            g = pred.game
            start = g.start.astimezone(ET).strftime("%a %-I:%M%p ET")
            notes = f" · {esc('; '.join(pred.notes))}" if pred.notes else ""
            league = f"{esc(g.league)} · " if sport.key == "soccer" and g.league else ""
            rows.append(
                f"• <b>{describe(pk, sport)}</b>\n"
                f"   {league}{esc(g.away)} @ {esc(g.home)} · {start}\n"
                f"   {stake(pk)}\n"
                f"   win {pk.prob:.1%} · edge {pk.edge:+.1%} · EV {pk.ev:+.1%} · "
                f"model: {_model_call(pred)}, total {pred.model_total:.1f}{notes}"
            )
        blocks.append("\n".join(rows))
    else:
        blocks.append("No plays cleared the veto stack.")

    # Paper-tracked spots and line moves for every game (shown even when nothing qualified).
    spots = [(p, [n for n in p.notes if n.startswith(("O/U spots", "spots (tracked", "line move", "roster:"))]) for p in preds]
    spots = [(p, ns) for p, ns in spots if any(n.startswith(("O/U", "spots")) for n in ns)] + \
            [(p, ns) for p, ns in spots if ns and not any(n.startswith(("O/U", "spots")) for n in ns)]
    if spots:
        rows = ["<b>📌 Rosters, tracked spots &amp; line moves</b> (paper, not picks)"]
        for pred, ns in spots[:30]:
            rows.append(f"• {esc(pred.game.away)} @ {esc(pred.game.home)} (model total {pred.model_total:.1f}): "
                        f"{esc('; '.join(ns))}")
        blocks.append("\n".join(rows))

    vetoed = [(p, pk) for p in preds for pk in p.picks if not pk.active and (pk.edge >= 0.03 or pk.prob >= 0.7)]
    if vetoed:
        rows = ["<b>🚫 Vetoed edges</b>"]
        for pred, pk in sorted(vetoed, key=lambda x: -x[1].edge)[:12]:
            rows.append(f"• {describe(pk, sport)} — {esc(pred.game.away)} @ {esc(pred.game.home)}: "
                        f"{esc('; '.join(pk.vetoes))}")
        blocks.append("\n".join(rows))

    if record:
        parts = []
        for market, (w, l, p, units) in sorted(record.items()):
            label = {"spread": sport.spread_label, "total": "O/U", "ml": "ML", "draw": "Draw"}[market]
            parts.append(f"{label} {w}-{l}" + (f"-{p}" if p else "") + f" ({units:+.1f}u)")
        blocks.append("📈 Last 30 days: " + " · ".join(parts))
    blocks.append("<i>Model output, not a guarantee. Bet responsibly.</i>")
    return "\n\n".join(blocks)
