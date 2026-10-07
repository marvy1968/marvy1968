"""Telegram alerts via the Bot API."""

import html

import requests

from .predict import GamePrediction

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram's hard limit is 4096 characters per message


def send_message(token: str, chat_id: str, text: str) -> None:
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    for chunk in split_message(text):
        resp = requests.post(
            API.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
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
        if current and len(current) + len(block) + 2 > MAX_LEN:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return chunks


def _fmt_line(market: str, line: float | None) -> str:
    if line is None:
        return ""
    if market == "spread":
        return f" {line + 0.0:+g}" if line else " PK"
    if market == "ml":
        return f" ({line:+.0f})"
    return f" {line:g}"


def _model_call(pred: GamePrediction) -> str:
    if round(pred.model_margin) == 0:
        return "toss-up"
    leader = pred.home if pred.model_margin > 0 else pred.away
    return f"{html.escape(leader)} by {abs(pred.model_margin):.0f}"


def format_slate(year: int, week: int, preds: list[GamePrediction]) -> str:
    esc = html.escape
    plays = [(p, pk) for p in preds for pk in p.picks if pk.active]
    core = [pk for p in preds for pk in p.picks if pk.market in ("spread", "total")]
    passed = sum(1 for pk in core if not pk.active)
    pass_rate = passed / len(core) if core else 0.0

    lines = [
        f"🏈 <b>Marv CFB Predict Max — {year} Week {week}</b>",
        f"{len(preds)} marquee games simulated · {len(plays)} active plays · veto pass rate {pass_rate:.0%}",
    ]
    if plays:
        rows = ["<b>✅ Active plays</b>"]
        for pred, pk in sorted(plays, key=lambda x: -x[1].edge):
            label = {"spread": "ATS", "total": "O/U", "ml": "ML"}[pk.market]
            rows.append(
                f"• <b>{esc(pk.side)}{_fmt_line(pk.market, pk.line)}</b> [{label}] — "
                f"{esc(pred.away)} @ {esc(pred.home)}\n"
                f"   win prob {pk.prob:.1%} · edge {pk.edge:+.1%} · model: {_model_call(pred)}, total {pred.model_total:.0f}"
            )
        lines.append("\n".join(rows))
    else:
        lines.append("No plays cleared the veto this week.")

    vetoed = [(p, pk) for p in preds for pk in p.picks if pk.market != "ml" and not pk.active and pk.edge >= 0.03]
    if vetoed:
        rows = ["<b>🚫 Vetoed edges</b>"]
        for pred, pk in vetoed:
            rows.append(f"• {esc(pk.side)}{_fmt_line(pk.market, pk.line)} ({esc(pred.away)} @ {esc(pred.home)}): "
                        f"{esc('; '.join(pk.vetoes))}")
        lines.append("\n".join(rows))
    lines.append("<i>Model output, not a guarantee. Bet responsibly.</i>")
    return "\n\n".join(lines)
