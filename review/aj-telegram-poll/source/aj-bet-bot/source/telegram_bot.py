"""Two-way Telegram bot (@March_betbot): long-polling getUpdates inside the `run` service.

Security: answers ONLY the chat id from the env (TELEGRAM_CHAT_ID); every other chat is ignored
silently (no reply, nothing echoed). The token is never logged (scrubbed from errors).

Threading: a daemon thread does nothing but HTTP long-polling and puts authorised messages on a
queue; commands are answered on the main thread between scans (`serve_until`), so SQLite, the
budget guards and the scanners are only ever touched from one thread.

Data: cache first. Replies show the data age; a fresh Odds API fetch happens only when the cache
is older than `fresh_minutes`, and only within the per-day query credit allowance
(`odds_api.allocations.query`, billed as strategy 'query')."""
from __future__ import annotations

import json
import difflib
import logging
import queue
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from . import friendly as fr
from . import valuefinder as vf
from .oddsmath import american_to_decimal
from .db import local_day, today_local
from .markets import match_event, name_sim
from .oddsapi import BudgetExceeded

log = logging.getLogger(__name__)
CT = ZoneInfo("America/Chicago")
MAX_LEN = 3500          # Telegram hard limit is 4096 (counted after entity parsing; emoji = 2 UTF-16 units)

COMMANDS = ("today", "upcoming", "game", "props", "edges", "recs", "swings", "trends", "injuries", "grade", "report", "budget", "parlay", "card", "reportcard", "betcard", "paper", "pace", "pacer", "help")
HELP = """Commands (alert-only / paper-only, no bets are placed):
/today - today's in-scope games (NFL, NCAAF FBS, WNBA, NBA)
/upcoming [sport] - next 7 days
/game <team> - score, best prices vs fair, team model (info)
/props <player|team> - player props or team totals (consensus EV)
/edges - latest alerts
/parlay [league] - Marv daily 2-leg parlays (cfb/ncaaf, nfl, wnba, soccer, mlb...)
/card - Marv daily action plan (image + text)
/reportcard [sport] - Marv daily card (parlays + best ML + O/U)
/betcard (/bet_card, /card) - full daily card now, games today (CT) + live: Ranked Action Plan + Value Plays + MARV PREDICT MAX bet card
/card_1day, /card_2day - same card through today+1 / today+2
/recs - ranked current edges with honesty tags
/pace <player> <stat> <line> - live pacer: now, proj, O/U, win chance, Bovada edge
/pacer - live props the pacer is pricing that clear the gate (best first)
/swings - live games whose ESPN line swung a lot (spread/moneyline)
/trends <team> - team ATS/over-under/slow-start/comeback/rest trends + qualifying ones
/injuries <team> - key injuries (ESPN) + how they move our numbers
/grade [date] - alert report card; default yesterday (/grade full = details)
/report - paper results (/report full = details)
/budget - Odds API credits
/paper (/kalpoly) - Kalshi DEMO + Polymarket PAPER report card (FAKE MONEY)
/help - this list
Free text works too: "bears game", "caitlin clark points", "texas team total"."""

PROP_WORDS: List[Tuple[str, List[str]]] = [
    (r"\bpra\b|points rebounds assists|pts reb ast", ["player_points_rebounds_assists"]),
    (r"\bpoints?\b|\bpts\b", ["player_points"]),
    (r"\brebounds?\b|\brebs?\b", ["player_rebounds"]),
    (r"\bassists?\b|\bast\b", ["player_assists"]),
    (r"\bthrees?\b|\b3s\b|3 ?pointers?|3pm", ["player_threes"]),
    (r"pass(ing)? (yds|yards)|\bpass\b", ["player_pass_yds"]),
    (r"rush(ing)? (yds|yards)|\brush\b", ["player_rush_yds"]),
    (r"rec(eiving)? (yds|yards)|receiving", ["player_reception_yds"]),
    (r"\breceptions?\b|\bcatches\b", ["player_receptions"]),
    (r"\btds?\b|touchdowns?|anytime", ["player_anytime_td"]),
    (r"team totals?|\btt\b|team points", ["team_totals", "alternate_team_totals", "team_totals_h1", "team_totals_q1"]),
]
PROP_RX = re.compile("|".join(p for p, _ in PROP_WORDS) + r"|\bprops?\b|\bover\b|\bunder\b", re.I)
SPORT_WORDS = {"nfl": "nfl", "football": "nfl", "ncaaf": "ncaaf", "cfb": "ncaaf", "college": "ncaaf",
               "wnba": "wnba", "nba": "nba"}


# ------------------------------------------------------------------ parsing / auth (pure)
def parse_command(text: str, bot_username: Optional[str] = None) -> Tuple[str, str]:
    """('cmd', 'arg'). Slash commands (with optional @bot suffix) or fuzzy free text."""
    t = (text or "").strip()
    if not t:
        return "help", ""
    if t.startswith("/"):
        head, _, arg = t[1:].partition(" ")
        cmd, _, at = head.partition("@")
        if at and bot_username and at.lower() != bot_username.lower().lstrip("@"):
            return "ignore", ""                   # addressed to another bot in a group
        cmd = cmd.lower().replace("-", "").replace("_", "")
        if cmd == "report" and re.match(r"card\b", arg.strip().lower()):
            cmd, arg = "reportcard", arg.strip()[4:]
        if cmd == "card":
            cmd = "betcard"
        if cmd in ("kalpoly", "kalshi", "poly", "polymarket"):
            cmd = "paper"
        mday = re.fullmatch(r"(?:bet)?card\+?([12])days?", cmd)
        if mday:
            cmd, arg = "betcard", mday.group(1)
        if cmd in ("dailycard", "marv", "marvcard"):
            cmd = "reportcard"
        if cmd == "start":
            cmd = "help"
        if cmd not in COMMANDS:
            close = difflib.get_close_matches(cmd, COMMANDS, n=1, cutoff=0.7) if cmd else []
            if close:
                return close[0], arg.strip()
            rest = t[1:].strip()
            if at:
                rest = (cmd + " " + arg).strip()
            # "/ steelers", "/steelers", "/caitlin clark" -> treat as a free-text lookup
            return parse_command(rest, bot_username) if rest else ("help", "")
        return cmd, arg.strip()
    low = t.lower()
    if re.fullmatch(r"(help|\?|commands|menu)", low):
        return "help", ""
    if re.search(r"\b(recs?|recommend\w*|best bets?|picks?)\b", low):
        return "recs", ""
    m = re.fullmatch(r"(?:report\s*card|daily\s*card|marv(?:\s*card)?)\s*(.*)", low)
    if m:
        return "reportcard", m.group(1).strip()
    if re.search(r"\b(grade|accuracy|how did (we|the alerts) do)\b", low):
        return "grade", ""
    m = re.fullmatch(r"(?:injur(?:y|ies)\s+(?:for\s+)?(.+)|(.+?)\s+injur(?:y|ies))", low)
    if m:
        return "injuries", (m.group(1) or m.group(2)).strip()
    m = re.fullmatch(r"(?:trends?\s+(?:for\s+)?(.+)|(.+?)\s+trends?)", low)
    if m:
        return "trends", (m.group(1) or m.group(2)).strip()
    if re.search(r"\b(swings?|line moves?|line swings?|movers?)\b", low):
        return "swings", ""
    if re.search(r"\b(edges?|alerts?|value bets?)\b", low):
        return "edges", ""
    if re.search(r"\b(report|p&l|pnl|profit|results)\b", low):
        return "report", ""
    if re.search(r"\b(budget|credits?|quota)\b", low):
        return "budget", ""
    if re.search(r"\b(upcoming|schedule|this week|next week)\b", low):
        sport = next((v for k, v in SPORT_WORDS.items() if re.search(rf"\b{k}\b", low)), "")
        return "upcoming", sport
    if re.fullmatch(r"(today|tonight|games( today)?|what'?s on( today)?)", low):
        return "today", ""
    if PROP_RX.search(low):
        return "props", t
    m = re.fullmatch(r"(.*?)\s*\b(game|match|matchup|score|odds|line|spread)\b\s*(.*)", low)
    if m and (m.group(1) or m.group(3)):
        return "game", (m.group(1) + " " + m.group(3)).strip()
    return "search", t


def authorized(update: Dict[str, Any], chat_id) -> bool:
    """Only configured chat ids (str or collection). Missing config => nobody is authorised."""
    allowed = {str(chat_id).strip()} if isinstance(chat_id, (str, int)) else {str(c).strip() for c in (chat_id or [])}
    allowed.discard("")
    if not allowed:
        return False
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    return str(chat.get("id", "")) in allowed


def _chat_of(update: Dict[str, Any]) -> str:
    msg = update.get("message") or update.get("edited_message") or {}
    return str((msg.get("chat") or {}).get("id", ""))


def prop_markets_in(text: str) -> List[str]:
    out: List[str] = []
    for rx, mk in PROP_WORDS:
        if re.search(rx, text, re.I):
            out += [m for m in mk if m not in out]
            if mk[0] in ("player_points_rebounds_assists",):
                break                             # 'pra' should not also match points/rebounds
    return out


def strip_prop_words(text: str) -> str:
    t = PROP_RX.sub(" ", text)
    t = re.sub(r"\b(props?|line|lines|odds|for|the|what|is|are|s)\b|[?!.,']", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def age_str(ts: Optional[str], now: datetime) -> str:
    if not ts:
        return "no data"
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return "unknown age"
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    m = int((now - t).total_seconds() // 60)
    return "just now" if m < 1 else (f"{m} min old" if m < 120 else f"{m // 60} h old")


def chunks(text: str, n: int = MAX_LEN) -> List[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > n:
            out.append(line[:n])
            line = line[n:]
        if len(cur) + len(line) + 1 > n:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out or [""]


def fmt_price(p: Optional[float]) -> str:
    if p is None:
        return "-"
    return f"+{p:.0f}" if p > 0 else f"{p:.0f}"


def _fuzzy(q: str, name: str) -> float:
    q, n = q.lower().strip(), name.lower()
    if not q:
        return 0.0
    if q in n:
        return 1.0 if len(q) >= 3 else 0.5
    toks = q.split()
    if toks and all(any(w.startswith(t) for w in n.split()) for t in toks):
        return 0.95
    return max(name_sim(q, name), difflib.SequenceMatcher(None, q, n).ratio())


# ------------------------------------------------------------------ bot
_VP_PY, _VP_CWD = "/opt/marv-bot/.venv/bin/python", "/opt/marv-bot"
_VP_BOV = "/opt/marv-bot/state/bovada_prices.json"
_VP_STALE_S = 15 * 60


def _vp_w(t) -> str:
    return (str(t or "").lower().split() or [""])[-1]


def _vp_from_event(ev, hn, an) -> Optional[Dict[str, Any]]:
    """Bovada prices from a stored Odds API event (no new credits): spread/ml/total + last_update."""
    from . import mybook
    bk = next((b for b in ev.get("bookmakers", []) or [] if mybook.is_mine(b.get("key"))), None)
    if bk is None:
        return None
    mk = {m.get("key"): {o.get("name"): o for o in m.get("outcomes", []) or []} for m in bk.get("markets", []) or []}
    out: Dict[str, Any] = {"at": bk.get("last_update"), "src": "March_edge odds snapshot"}
    sp, ml, tt = mk.get("spreads") or {}, mk.get("h2h") or {}, mk.get("totals") or {}
    if hn in sp and an in sp:
        out["spread"] = [sp[hn].get("point"), sp[hn].get("price"), sp[an].get("price")]
    if hn in ml and an in ml:
        out["ml"] = {"home": ml[hn].get("price"), "away": ml[an].get("price")}
    if "Over" in tt and "Under" in tt:
        out["total"] = [tt["Over"].get("point"), tt["Over"].get("price"), tt["Under"].get("price")]
    return out if len(out) > 2 else None


def _vp_from_marv(sport: str, home: str, away: str) -> Optional[Dict[str, Any]]:
    try:
        d = json.load(open(_VP_BOV))[{"ncaaf": "cfb"}.get(sport, sport)]
    except Exception:  # noqa: BLE001
        return None
    for e in d.get("events", []):
        if _vp_w(e.get("home")) == _vp_w(home) and _vp_w(e.get("away")) == _vp_w(away):
            return {"spread": e.get("spread"), "ml": e.get("ml"), "total": e.get("total"), "at": d.get("at"),
                    "src": "Marv Bovada snapshot"}
    return None


def _vp_ts(x) -> float:
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _vp_eval(reqs: List[Dict[str, Any]]) -> List[Optional[Dict[str, Any]]]:
    """Recompute Value Plays NOW in Marv (current probability, freshest price); Marv logs every shown play."""
    if not reqs:
        return []
    import subprocess
    try:
        r = subprocess.run([_VP_PY, "-m", "marv.value_play", "eval"], cwd=_VP_CWD, input=json.dumps(reqs),
                           capture_output=True, text=True, timeout=25)
        return json.loads(r.stdout or "[]")
    except Exception:  # noqa: BLE001 - a display extra must never break a command
        log.debug("value play eval failed", exc_info=True)
        return [None] * len(reqs)


def _vp_fmt(r: Dict[str, Any]) -> str:
    at = _vp_ts(r.get("price_at"))
    when = datetime.fromtimestamp(at, CT).strftime("%-I:%M %p CT") if at else "time unknown"
    stale = " · stale price" if not at or time.time() - at > _VP_STALE_S else ""
    return (f"     💎 Value: {fr.esc(r['pick'])} {fr.am(r['price'])} · EV +{float(r['ev']) * 100:.1f}% · "
            f"{fr.esc(r['tier'])} (tracking, unproven) · as of {when}{stale}")



def _parlay_pause_filter(txt):
    """2026-10-23 (Marvin approved): Marv 2-leg parlays paused -> the bridge's /card text shows a PAUSED line instead.
    Logic + off switch live in /opt/marv-feed/parlay_pause.py (MARV_PARLAY_PAUSE=off or PARLAY_PAUSE_OFF file)."""
    try:
        import sys as _s
        if "/opt/marv-feed" not in _s.path:
            _s.path.append("/opt/marv-feed")
        import parlay_pause
        return parlay_pause.filter_text(txt)
    except Exception:  # noqa: BLE001
        return txt

class TelegramBot:
    def __init__(self, cfg: Dict[str, Any], db, scanner, vs, notifier, http=None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.cfg, self.db, self.scanner, self.vs, self.notifier = cfg, db, scanner, vs, notifier
        tc = cfg.get("telegram", {}) or {}
        self.bc = tc.get("bot", {}) or {}
        self.http = http or requests.Session()
        # long-polling gets its own session: requests.Session is not thread-safe across poll/send threads
        self.poll_http = http or requests.Session()
        self.clock = clock
        self._token = getattr(notifier, "_token", "")
        self.chat_id = str(getattr(notifier, "_chat", "") or "")
        self.username = self.bc.get("username", "March_betbot")
        self.fresh_min = float(self.bc.get("fresh_minutes", 15))
        self.games_ttl = float(self.bc.get("games_ttl_s", 300))
        self.q: "queue.Queue[Tuple[str, str]]" = queue.Queue()      # (chat_id, text)
        self.offset: Optional[int] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._games: Tuple[float, Dict[str, List[Any]]] = (0.0, {})
        self.ignored = 0
        self.last_poll = 0.0           # time.time() of the last successful getUpdates
        self.last_attempt = 0.0        # time.time() just before each getUpdates, including failures
        self._poll_stop = threading.Event()
        self._logged_409 = False
        self.handled = 0

    # --- transport ---
    @property
    def enabled(self) -> bool:
        return bool(self.bc.get("enabled", True) and self._token and self.chat_id)

    def _scrub(self, s: str) -> str:
        return s.replace(self._token, "***") if self._token else s

    def _api(self, method: str, http_timeout: float = 20, _session=None, **params):
        r = (_session or self.http).post(f"https://api.telegram.org/bot{self._token}/{method}", data=params, timeout=http_timeout)
        if getattr(r, "status_code", 200) != 200:
            try:                               # Telegram explains 4xx in the body ("can't parse entities ...")
                desc = str((r.json() or {}).get("description", ""))[:160]
            except Exception:  # noqa: BLE001
                desc = ""
            raise RuntimeError(f"Telegram {method}: HTTP {r.status_code} {desc}".rstrip())
        body = r.json()
        if not body.get("ok", False):
            raise RuntimeError(f"Telegram {method}: {str(body.get('description', 'not ok'))[:120]}")
        return body.get("result")

    def allowed(self) -> List[str]:
        rec = getattr(self.notifier, "recipients", None)
        return list(rec()) if callable(rec) else ([self.chat_id] if self.chat_id else [])

    def _note_unknown(self, u: Dict[str, Any]) -> None:
        """Log (never authorise) other chats, e.g. a new /start, so the owner can add them on purpose."""
        msg = u.get("message") or u.get("edited_message") or {}
        chat, frm = msg.get("chat") or {}, msg.get("from") or {}
        rec = {"ts": time.time(), "chat_id": str(chat.get("id", "")), "type": chat.get("type"),
               "first_name": frm.get("first_name") or chat.get("first_name"),
               "username": frm.get("username") or chat.get("username"), "text": str(msg.get("text") or "")[:40]}
        log.info("telegram: message from unknown chat id=%s type=%s first_name=%s username=%s text=%r (ignored)",
                 rec["chat_id"], rec["type"], rec["first_name"], rec["username"], rec["text"])
        try:
            import json as _json
            import os as _os
            p = _os.path.expanduser(self.bc.get("unknown_log", "~/.config/aj-bet-bot/tg_unknown.jsonl"))
            _os.makedirs(_os.path.dirname(p), exist_ok=True)
            with open(p, "a") as fh:
                fh.write(_json.dumps(rec) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def reply(self, text: str, chat_id: Optional[str] = None) -> None:
        """Replies (Telegram HTML) go to the configured chat only (never to an unknown sender)."""
        text = fr.tg_safe(text)               # round 2: '<player|team>' etc. caused HTTP 400s
        parts = _html_chunks(text)
        if getattr(self.notifier, "dry_run", True):
            from .friendly import plain
            self.notifier.printer("----- [DRY RUN: Telegram reply not sent] -----\n" + plain(text))
            return
        from .friendly import plain
        ok = 0
        for part in parts:
            try:
                self._api("sendMessage", chat_id=chat_id or self.chat_id, text=part, parse_mode="HTML",
                          disable_web_page_preview=True)
                ok += 1
                continue
            except Exception as e:  # noqa: BLE001
                log.error("telegram reply failed (HTML, %d chars): %s; retrying as plain text",
                          len(part), self._scrub(str(e)))
            try:                               # never leave Marvin without an answer
                for p in chunks(plain(part)):
                    self._api("sendMessage", chat_id=chat_id or self.chat_id, text=p, disable_web_page_preview=True)
                ok += 1
            except Exception as e:  # noqa: BLE001
                log.error("telegram reply failed (plain): %s", self._scrub(str(e)))
        log.info("telegram reply: %d/%d part(s) sent", ok, len(parts))

    def push(self, text: str) -> None:
        if not self.notifier.send(text, html=True):
            log.info("push not sent (dry-run or error)")

    def poll_once(self, timeout: int = 25) -> int:
        params = {"timeout": timeout, "allowed_updates": '["message","edited_message"]'}
        if self.offset is not None:
            params["offset"] = self.offset
        ups = self._api("getUpdates", http_timeout=timeout + 10, _session=self.poll_http, **params) or []
        self.last_poll = time.time()
        n = 0
        for u in ups:
            self.offset = max(self.offset or 0, int(u.get("update_id", 0)) + 1)
            if not authorized(u, self.allowed()):
                self.ignored += 1             # never answered; logged so the owner can add a chat on purpose
                self._note_unknown(u)
                continue
            msg = u.get("message") or u.get("edited_message") or {}
            text = msg.get("text")
            if text:
                self.q.put((_chat_of(u), text))
                n += 1
        if n:
            log.info("telegram: %d command(s) received", n)
        return n

    def take_backlog(self, max_age_s: float) -> int:
        ups = self._api("getUpdates", http_timeout=15, timeout=0, _session=self.poll_http) or []
        cutoff = time.time() - max_age_s
        n = 0
        for u in ups:
            self.offset = max(self.offset or 0, int(u.get("update_id", 0)) + 1)
            if not authorized(u, self.allowed()):
                self._note_unknown(u)
                continue
            msg = u.get("message") or u.get("edited_message") or {}
            if msg.get("text") and float(msg.get("date", 0) or 0) >= cutoff:
                self.q.put((_chat_of(u), msg["text"]))
                n += 1
        return n

    def drain(self, max_s: float = 15.0) -> int:
        """On shutdown: answer anything already received so a restart never swallows a command."""
        n = 0 if self.q.empty() else self.serve_until(time.time() + max_s, stop_when_empty=True)
        if self.offset is not None:
            try:      # confirm what we already took so the next process does not answer it twice
                self._api("getUpdates", http_timeout=10, timeout=0, offset=self.offset, limit=1,
                          _session=self.http)
            except Exception:  # noqa: BLE001
                pass
        return n

    def _loop(self, stop) -> None:
        """One getUpdates loop. `stop` retires this thread; `self._stop` stops the bot."""
        backoff = 1.0
        while not stop.is_set() and not self._stop.is_set():
            try:
                self.last_attempt = time.time()
                self.poll_once()
                backoff = 1.0
            except Exception as e:  # noqa: BLE001 - never kill the service
                msg = self._scrub(str(e))[:200]
                conflict = "409" in msg
                if conflict:
                    # Two getUpdates on one token 409 each other. Log once and don't sit in a 300s backoff.
                    backoff = min(backoff, 30.0)
                    if not self._logged_409:
                        log.warning("telegram poll 409 Conflict (retry capped at 30s): %s", msg)
                        self._logged_409 = True
                else:
                    self._logged_409 = False
                    log.warning("telegram poll error (retry in %.0fs): %s", backoff, msg)
                if stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 30.0 if conflict else 300.0)

    def start(self) -> bool:
        if not self.enabled:
            log.info("telegram bot disabled (no token/chat id or telegram.bot.enabled false)")
            return False
        try:
            self._api("deleteWebhook", http_timeout=10)    # getUpdates does not work while a webhook is set
        except Exception as e:  # noqa: BLE001
            log.warning("deleteWebhook: %s", self._scrub(str(e))[:120])
        if self.bc.get("skip_backlog", True):
            try:   # answer commands sent in the last few minutes (e.g. during a restart); drop older ones
                n = self.take_backlog(float(self.bc.get("backlog_max_age_s", 600)))
                if n:
                    log.info("telegram: answering %d recent command(s) sent while restarting", n)
            except Exception as e:  # noqa: BLE001
                log.warning("getUpdates backlog: %s", self._scrub(str(e))[:120])
        self._spawn_poll()
        log.info("telegram bot polling started (commands answered for the configured chat only)")
        return True

    def _spawn_poll(self) -> bool:
        """Start one poll thread on a fresh stop event. Never called while another poller is alive."""
        self._poll_stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(self._poll_stop,), name="telegram-poll", daemon=True)
        self._thread.start()
        return True

    def ensure_polling(self, max_silence_s: float = 180.0) -> bool:
        """Watchdog (main loop): restart the poll thread if it died or is hung.

        A live thread waiting out a 502/timeout backoff still owns getUpdates. Starting a
        second loop in this process makes Telegram answer both with 409 Conflict. Treat the
        thread as hung only when neither a success nor an attempt has happened for
        max_silence_s plus the longest backoff and one long-poll (345s).
        """
        thread = self._thread
        alive = thread is not None and thread.is_alive()
        if not alive:
            log.warning("telegram poll thread not running; restarting it")
            return self._spawn_poll()
        now = time.time()
        last = max(self.last_poll, self.last_attempt)
        if now - last <= max_silence_s + 345:
            return False
        log.warning("telegram poll thread hung (no attempt for %.0fs); restarting it", now - last)
        self._poll_stop.set()
        old_http = self.poll_http
        self.poll_http = requests.Session()
        close = getattr(old_http, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                log.debug("closing hung poll session failed", exc_info=True)
        thread.join(timeout=5)
        if thread.is_alive():
            log.warning("telegram poll thread still alive after restart; not starting a second poller")
            return False
        return self._spawn_poll()

    def stop(self) -> None:
        self._stop.set()

    def serve_until(self, deadline: float, stop_when_empty: bool = False) -> int:
        """Answer queued messages on the calling (main) thread until `deadline` (time.time())."""
        n = 0
        while True:
            left = deadline - time.time()
            if left <= 0 or (stop_when_empty and self.q.empty()):
                return n
            try:
                item = self.q.get(timeout=min(left, 1.0))
            except queue.Empty:
                continue
            chat, text = item if isinstance(item, tuple) else (self.chat_id, item)
            t0 = time.time()
            cmd = "?"
            try:
                cmd = parse_command(text, self.username)[0]
                out = self.handle(text)
            except Exception:  # noqa: BLE001
                log.exception("command failed")
                out = "Sorry, that command failed (logged)."
            self.handled += 1
            log.info("telegram command %s handled in %.1fs (%d chars)", cmd, time.time() - t0, len(out or ""))
            if out:
                self.reply(out, chat)          # reply to whoever asked
            n += 1

    # --- dispatch ---
    def handle(self, text: str) -> str:
        cmd, arg = parse_command(text, self.username)
        if cmd == "ignore":
            return ""
        fn = getattr(self, f"cmd_{cmd}", None)
        return fn(arg) if fn else f"Unknown command.\n\n{fr.esc(HELP)}"

    def _marv_card(self, arg: str, full: bool) -> str:
        """/parlay and /reportcard belong to Marv Predict: fetch Marv's daily card from the local Marv bridge."""
        import os
        try:
            url = os.environ.get("MARV_OVERLAY_URL", "http://127.0.0.1:8787/overlay").rsplit("/", 1)[0] + "/card"
            r = requests.get(url, params={"sport": (arg or "").split(" ")[0], "full": "1" if full else "0"}, timeout=60)
            txt = (r.json() or {}).get("text", "") if r.status_code == 200 else ""
            return _parlay_pause_filter(txt) or "Marv daily card unavailable right now (Marv bridge returned nothing)."
        except Exception as e:  # noqa: BLE001
            log.warning("Marv card failed: %s", e)
            return "Marv daily card unavailable right now (Marv bridge not reachable)."

    def _marv_images(self, kind: str, arg: str = "") -> int:
        """2026-10-17 (Marvin): send Marv's PNG bet cards (sendPhoto) before the text. MARV_CARD_IMAGES=off disables;
        any failure -> 0 and the text still goes out."""
        import os
        if os.environ.get("MARV_CARD_IMAGES", "on").lower() in ("0", "off", "false", "no"):
            return 0
        sp = (arg or "").split(" ")[0].lower()
        if sp == "nba":
            return 0
        try:
            paths = (self._marv_rules("img", kind, sp) or {}).get("paths") or []   # 2026-10-18 rules overlay
            if not paths:
                url = os.environ.get("MARV_OVERLAY_URL", "http://127.0.0.1:8787/overlay").rsplit("/", 1)[0] + "/card_img"
                paths = (requests.get(url, params={"kind": kind, "sport": sp}, timeout=120).json() or {}).get("paths", [])
        except Exception as e:  # noqa: BLE001
            log.warning("Marv card images failed: %s", e)
            return 0
        if getattr(self.notifier, "dry_run", True):
            self.notifier.printer(f"----- [DRY RUN: {len(paths)} card image(s) not sent] -----")
            return 0
        n = 0
        for p in paths[:4]:
            try:
                with open(p, "rb") as fh:
                    r = self.http.post(f"https://api.telegram.org/bot{self._token}/sendPhoto",
                                       data={"chat_id": self.chat_id}, files={"photo": fh}, timeout=60)
                if getattr(r, "status_code", 0) == 200:
                    n += 1
                else:  # big tall cards: fall back to a document upload (no recompression limits)
                    with open(p, "rb") as fh:
                        r = self.http.post(f"https://api.telegram.org/bot{self._token}/sendDocument",
                                           data={"chat_id": self.chat_id}, files={"document": fh}, timeout=60)
                    n += int(getattr(r, "status_code", 0) == 200)
            except Exception as e:  # noqa: BLE001
                log.warning("sendPhoto failed: %s", self._scrub(str(e)))
        log.info("Marv card images: %d/%d sent", n, len(paths))
        return n

    def _marv_rules(self, *args):
        """2026-10-18 (Marvin): /opt/marv-feed/marv_rules.py overlay (NFL no-MX parlays, NEAR3 first, no kicked-off games)."""
        import json, os, subprocess
        if not os.path.exists("/opt/marv-feed/marv_rules.py"):
            return None
        try:
            r = subprocess.run(["/opt/marv-bot/.venv/bin/python", "/opt/marv-feed/marv_rules.py", *args], cwd="/opt/marv-bot",
                               capture_output=True, text=True, timeout=180)
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception as e:  # noqa: BLE001
            log.warning("marv_rules overlay failed: %s", e)
            return None

    def cmd_parlay(self, arg: str = "") -> str:
        self._marv_images("parlay", arg)
        # 2026-10-19: plain /parlay too goes through the marv-feed overlay (today-card check, no finished games)
        t = (self._marv_rules("text", (arg or "").strip().split(" ")[0] if (arg or "").strip() else "") or {}).get("text")
        if t:
            return t
        return self._marv_card(arg, full=False)

    def cmd_card(self, arg: str = "") -> str:
        """/card - Marv daily action plan (image + text)."""
        import os
        self._marv_images("plan", arg)
        try:
            url = os.environ.get("MARV_OVERLAY_URL", "http://127.0.0.1:8787/overlay").rsplit("/", 1)[0] + "/card"
            r = requests.get(url, params={"plan": "1", "sport": (arg or "").split(" ")[0]}, timeout=60)
            return _parlay_pause_filter((r.json() or {}).get("text", "") if r.status_code == 200 else "") or "Marv daily plan unavailable right now."
        except Exception as e:  # noqa: BLE001
            log.warning("Marv plan failed: %s", e)
            return "Marv daily plan unavailable right now (Marv bridge not reachable)."

    def cmd_reportcard(self, arg: str = "") -> str:
        txt = self._marv_card(arg, full=True)
        try:  # Script O/U tracking line (2026-10-13); remove this try block to roll back
            import json as _j
            line = _j.load(open("/opt/marv-bot/state/script_ou/summary.json")).get("line", "")
            if line:
                txt += "\n\n" + fr.esc(line)
        except Exception:  # noqa: BLE001
            pass
        try:  # Master architecture tracking line (2026-10-13); remove this try block to roll back
            import json as _j
            line = _j.load(open("/opt/marv-bot/state/master/summary.json")).get("line", "")
            if line:
                txt += "\n" + fr.esc(line)
        except Exception:  # noqa: BLE001
            pass
        try:  # 2026-10-17 (Marvin): recommendation tracker + CLV (ajbet/enrich.py)
            from . import enrich as _en
            txt += "\n\n" + fr.esc(_en.reportcard_block(self.db))
        except Exception:  # noqa: BLE001
            pass
        try:  # Oct 16 2026 (Marvin): CFB +200..+299 ML dog shadow (tracking-only)
            import json as _j
            line = _j.load(open("/opt/marv-bot/state/cfb_dog_shadow/summary.json")).get("line", "")
            if line:
                txt += "\n" + fr.esc(line)
        except Exception:  # noqa: BLE001
            pass
        return txt

    def cmd_betcard(self, arg: str = "") -> str:
        """2026-10-16: on-demand full daily card from Marv (marv.cardnow): stored morning build re-rendered with
        Value Plays/Master re-priced from the current Bovada snapshot + today's MAX bet card. 0 Odds API credits."""
        import os, subprocess
        try:
            # 2026-10-18 (Marvin): no double dipping on /card (one pick per game+side) via /opt/marv-feed/cardnow_nodip.py
            # (wraps marv.cardnow; lives outside /opt/marv-bot, which deploy/install.sh resets). Falls back to plain cardnow.
            nd = "/opt/marv-feed/cardnow_nodip.py"
            cmd = [_VP_PY, nd] if os.path.exists(nd) else [_VP_PY, "-m", "marv.cardnow"]
            r = subprocess.run(cmd + [str(int(arg) if str(arg).strip() in ("1", "2") else 0)], cwd=_VP_CWD, capture_output=True, text=True, timeout=60)
            if not (json.loads(r.stdout or "{}") or {}).get("text") and cmd[1] == nd:
                r = subprocess.run([_VP_PY, "-m", "marv.cardnow", str(int(arg) if str(arg).strip() in ("1", "2") else 0)], cwd=_VP_CWD, capture_output=True, text=True, timeout=60)
            txt = (json.loads(r.stdout or "{}") or {}).get("text", "")
            return txt or "Marv daily card unavailable right now (Marv returned nothing)."
        except Exception as e:  # noqa: BLE001
            log.warning("betcard failed: %s", e)
            return "Marv daily card unavailable right now."

    # 2026-10-18 (Marvin): /paper (/kalpoly) - fake-money trader report card, read live from the traders' summary.json
    PAPER_SUMMARIES = (("Kalshi DEMO", "/opt/kalshi-demo/data/summary.json"),
                       ("Polymarket PAPER", "/opt/polymarket-paper/data/summary.json"))

    def cmd_paper(self, arg: str = "") -> str:
        from datetime import timedelta
        def money(x):
            return ("-" if float(x) < 0 else "+") + f"${abs(float(x)):,.2f}"
        def rec(r):
            r = r or {}
            return f"{r.get('W', 0)}-{r.get('L', 0)}" + (f"-{r['P']}" if r.get("P") else "")
        now = datetime.now(CT); yday = (now - timedelta(days=1)).date().isoformat()
        accts, worked, total = [], [], 0.0
        for name, path in self.PAPER_SUMMARIES:
            try:
                d = json.load(open(path))
            except Exception as e:  # noqa: BLE001
                accts.append(f"<b>{fr.esc(name)}</b>: summary unavailable ({fr.esc(type(e).__name__)})"); continue
            a, t = d.get("all_time") or {}, d.get("today_ct") or {}
            if t.get("date") != now.date().isoformat():
                t = {}
            y = d.get("yesterday_ct") if (d.get("yesterday_ct") or {}).get("date") == yday else (d.get("daily_by_settle_date") or {}).get(yday) or {}
            bal = d.get("account_total") if d.get("account_total") is not None else d.get("bankroll_settled")
            total += float(a.get("pnl") or 0)
            types = sorted((d.get("by_alert_type") or {}).items(), key=lambda kv: -float(kv[1].get("pnl") or 0))
            for k, v in types:
                if float(v.get("pnl") or 0) > 0:
                    worked.append(f"{name.split()[0]} {k} {rec(v)} {money(v['pnl'])}")
            try:
                gen = datetime.fromisoformat(d["generated"]).astimezone(CT).strftime("%-I:%M %p CT")
            except Exception:  # noqa: BLE001
                gen = "?"
            L = [f"<b>{fr.esc(name)}</b> · updated {gen}",
                 f"Today: {rec(t)} {money(t.get('pnl') or 0)} · Yesterday: {rec(y)} {money(y.get('pnl') or 0)}",
                 f"All-time: {rec(a)} · risked ${float(a.get('risked') or 0):,.2f} · P/L {money(a.get('pnl') or 0)} · ROI {float(a.get('roi_pct') or 0):+.1f}%",
                 f"Balance ${float(bal or 0):,.2f} · open {int((d.get('open') or {}).get('count') or 0)}"]
            L += [f"  • {fr.esc(k)}: {rec(v)} {money(v.get('pnl') or 0)}" for k, v in types]
            accts.append("\n".join(L))
        head = ["🧪 <b>KALSHI + POLYMARKET PAPER REPORT</b> · FAKE MONEY (demo/paper, no real bets)", ""]
        head.append("✅ Worked: " + (fr.esc("; ".join(worked[:4])) if worked else "nothing profitable yet"))
        tail = []
        try:   # 2026-10-26 (Marvin): top lanes by ROI (ajbet/lane_top.py). Off: KALPOLY_LANES=off | data/KALPOLY_LANES_OFF
            from . import lane_top
            lt = lane_top.text()
            if lt:
                tail = ["", lt]
        except Exception:  # noqa: BLE001
            log.debug("kalpoly lanes failed", exc_info=True)
        return "\n".join(head + [""] + ["\n\n".join(accts), "", f"<b>Combined all-time P/L: {money(total)}</b> (FAKE MONEY)"] + tail)

    def cmd_pace(self, arg: str = "") -> str:
        """2026-10-31 (Marvin): /pace <player> <stat> <line>. Read-only; ESPN + prop pacer + cached Bovada, 0 Odds API credits."""
        try:
            from . import pace_cmd
            return pace_cmd.pace(arg, self.db)
        except Exception as e:  # noqa: BLE001
            log.exception("pace failed")
            return f"Pace lookup failed ({type(e).__name__})."

    def cmd_pacer(self, arg: str = "") -> str:
        """2026-11-01 (Marvin): /pacer - live pacer props clearing the gate. Read-only, 0 Odds API credits."""
        try:
            from . import pace_cmd
            return pace_cmd.pacer_list()
        except Exception as e:  # noqa: BLE001
            log.exception("pacer list failed")
            return f"Pacer list failed ({type(e).__name__})."

    def cmd_help(self, arg: str = "") -> str:
        return fr.esc(HELP)

    def cmd_unknown(self, arg: str = "") -> str:
        return "Sorry, I don't know that one.\n\n" + fr.esc(HELP)

    def now(self) -> datetime:
        return self.clock()

    def fmt_time(self, iso: str) -> str:
        try:
            t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(CT)
        except (ValueError, AttributeError):
            return iso or "?"
        return t.strftime("%a %b %d %I:%M %p CT").replace(" 0", " ")

    def games(self, force: bool = False) -> Dict[str, List[Any]]:
        ts, g = self._games
        if force or not g or time.time() - ts > self.games_ttl:
            g = self.vs.scope_games(us_only=True) if self.vs is not None else {}   # soccer props: scan only
            self._games = (time.time(), g)
        return g

    def find_game(self, q: str):
        best, score = None, 0.0
        for sport, gs in self.games().items():
            for g in gs:
                for name in (g.home.name, g.away.name, g.home.abbr, g.away.abbr):
                    s = _fuzzy(q, name) if len(q) > 3 or q.upper() != name else 1.0
                    if q.upper() == (name or "").upper():
                        s = 1.0
                    if s > score:
                        best, score = (sport, g), s
        return best if score >= 0.6 else None

    def allowance_left(self) -> float:
        a = ((self.cfg.get("odds_api") or {}).get("allocations") or {}).get("query") or {}
        daily = a.get("daily", self.bc.get("daily_query_credits", 300))
        return float(daily) - self.db.credits_used_on(today_local(), "query")

    def cached(self, key: str):
        return self.db.kv_get(key)

    def is_stale(self, ts: Optional[str]) -> bool:
        if not ts:
            return True
        try:
            t = datetime.fromisoformat(ts)
        except ValueError:
            return True
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return self.now() - t > timedelta(minutes=self.fresh_min)

    LIVE_FRESH_S = 120.0

    def featured_event(self, sport: str, g) -> Tuple[Optional[Dict[str, Any]], str]:
        """(odds event, provenance). Cache first; fresh fetch if stale and within allowance.
        Marvin 2026-10-03: a LIVE game never uses a quote older than LIVE_FRESH_S (was the 15-min lookup cache):
        the scanner's live pull first (0 credits), else the featured cache if that young, else a fresh fetch."""
        ts, evs = self.cached(f"lastfeatured:{sport}")
        ev = match_event(g.home.name, g.away.name, g.start, evs or [], self.vs.aliases) if evs else None
        note = f"cache, {age_str(ts, self.now())}" if ev else "no cached odds"
        stale = ev is None or self.is_stale(ts)
        if g.state == "in":
            try:
                key = (self.vs.sports.get(sport) or {}).get("odds_key") or ""
                hit = getattr(self.scanner, "_live_events", {}).get(key) if key else None
                if hit and time.time() - float(hit[0]) <= self.LIVE_FRESH_S:
                    lev = match_event(g.home.name, g.away.name, g.start, hit[1] or [], self.vs.aliases)
                    if lev:
                        return lev, f"live, {time.time() - float(hit[0]):.0f}s old"
            except Exception:  # noqa: BLE001
                log.debug("live cache lookup failed", exc_info=True)
            age = None
            try:
                if ts:
                    t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
                    age = (self.now() - t).total_seconds()
            except (TypeError, ValueError):
                age = None
            stale = ev is None or age is None or age > self.LIVE_FRESH_S
            if stale:
                ev, note = None, "no fresh live prices (need under 2 min)"
        if stale and g.state != "post":
            cost = 3.0
            if self.allowance_left() >= cost:
                try:
                    fresh = self.vs.odds.live_odds(self.vs.sports[sport]["odds_key"], strategy="query") or []
                    self.db.kv_set(f"lastfeatured:{sport}", fresh)
                    fev = match_event(g.home.name, g.away.name, g.start, fresh, self.vs.aliases)
                    if fev:
                        ev, note = fev, "fresh"
                except BudgetExceeded as e:
                    note += f"; fresh fetch blocked ({str(e)[:60]})"
                except Exception as e:  # noqa: BLE001
                    note += f"; fresh fetch failed ({type(e).__name__})"
            else:
                note += "; daily lookup limit reached" if g.state == "in" else "; daily query allowance used up"
        return ev, note

    def _score_changed_ts(self, g) -> Optional[float]:
        try:
            hit = getattr(self.scanner, "_scores", {}).get(g.event_id)
            return float(hit[1]) if hit else getattr(g, "score_changed_ts", None)
        except Exception:  # noqa: BLE001
            return None

    def _odds_event(self, sport: str, g) -> Optional[Dict[str, Any]]:
        """Cached Odds API event for an ESPN game: featured cache, else the free /events list cached by
        props-only sports (NBA has no featured scans)."""
        for k in (f"lastfeatured:{sport}", f"lastevents:{sport}"):
            _, evs = self.cached(k)
            oev = match_event(g.home.name, g.away.name, g.start, evs or [], self.vs.aliases) if evs else None
            if oev is not None:
                return oev
        return None

    def props_event(self, sport: str, g, kind: str, markets: List[str]) -> Tuple[Optional[Dict[str, Any]], str]:
        """Cached per-event props (kind 'player'|'team'); fresh fetch if stale + within allowance."""
        oev = self._odds_event(sport, g)
        if oev is None:
            try:
                oev = match_event(g.home.name, g.away.name, g.start,
                                  self.vs.odds.events(self.vs.sports[sport]["odds_key"]) or [], self.vs.aliases)
            except Exception:  # noqa: BLE001 - events endpoint is free; ignore failures
                oev = None
        if oev is None:
            return None, "game not on the odds board yet"
        key = f"lastprops:{sport}:{oev['id']}:{kind}"
        pts, pev = self.cached(key)
        note = f"cache, {age_str(pts, self.now())}" if pev else "no cached props"
        if (pev is None or self.is_stale(pts)) and g.state == "pre" and markets:
            cost = float(len(markets))
            if self.allowance_left() >= cost:
                try:
                    fresh = self.vs.odds.event_odds(self.vs.sports[sport]["odds_key"], oev["id"], ",".join(markets),
                                                    strategy="query")
                    if fresh:
                        if pev and kind == "player":   # keep other cached markets for this event
                            have = {m["key"] for b in fresh.get("bookmakers", []) for m in b.get("markets", [])}
                            for b in pev.get("bookmakers", []):
                                fb = next((x for x in fresh["bookmakers"] if x.get("key") == b.get("key")), None)
                                keep = [m for m in b.get("markets", []) if m["key"] not in have]
                                if fb is None and keep:
                                    fresh["bookmakers"].append(dict(b, markets=keep))
                                elif fb is not None:
                                    fb["markets"] += keep
                        self.db.kv_set(key, fresh)
                        pev, note = fresh, "fresh"
                except BudgetExceeded as e:
                    note += f"; fresh fetch blocked ({str(e)[:60]})"
                except Exception as e:  # noqa: BLE001
                    note += f"; fresh fetch failed ({type(e).__name__})"
            else:
                note += "; daily query allowance used up"
        return pev, note

    # --- commands ---
    def _game_line(self, sport: str, g) -> str:
        if g.state == "in":
            return fr.game_line(sport, g.away.name, g.home.name, live=f"LIVE {g.away.score}-{g.home.score} ({g.detail})")
        if g.state == "post":
            return fr.game_line(sport, g.away.name, g.home.name, live=f"FINAL {g.away.score}-{g.home.score}")
        return fr.game_line(sport, g.away.name, g.home.name, g.start, now=self.now())

    def _today_odds(self, sport: str) -> Dict[str, Any]:
        """Stored Odds API events (0 credits): {'pre': featured snapshot (<= 24 h old), 'live': live pull (<= 10 min)}."""
        out: Dict[str, Any] = {"pre": [], "live": []}
        try:
            ts, evs = self.cached(f"lastfeatured:{sport}")
            if evs and ts and self.now() - datetime.fromisoformat(ts) <= timedelta(hours=24):
                out["pre"] = evs
        except (TypeError, ValueError):
            pass
        try:
            key = ((self.vs.sports.get(sport) or {}).get("odds_key") if self.vs is not None else None) or ""
            hit = getattr(self.scanner, "_live_events", {}).get(key) if key else None
            if hit and time.time() - float(hit[0]) <= 600:
                out["live"] = hit[1] or []
        except Exception:  # noqa: BLE001
            pass
        return out

    def _bovada_bits(self, sport: str, g, odds: Dict[str, Any]) -> str:
        """'Bovada: CHI −3.5 · ML PIT +150 / CHI −180' from stored data only ('' when not cached)."""
        from . import mybook
        from .markets import outcome_name_for
        if g.state == "post":
            return ""
        evs = odds["live"] if g.state == "in" else odds["pre"]
        if not evs:
            return ""
        aliases = getattr(self.vs, "aliases", None) or {}
        try:
            ev = match_event(g.home.name, g.away.name, g.start, evs, aliases)
            if not ev:
                return ""
            hn, an = outcome_name_for(g.home.name, ev, aliases), outcome_name_for(g.away.name, ev, aliases)
            bk = next((b for b in ev.get("bookmakers", []) or [] if mybook.is_mine(b.get("key"))), None)
            if bk is None:
                return ""
            mk = {m.get("key"): {o.get("name"): o for o in m.get("outcomes", []) or []} for m in bk.get("markets", []) or []}
            bits = []
            sp = mk.get("spreads") or {}
            if hn in sp and an in sp and sp[hn].get("point") is not None:
                hp = float(sp[hn]["point"])
                fav, pt = (g.home.abbr, hp) if hp <= 0 else (g.away.abbr, -hp)
                bits.append(f"{fr.esc(fav)} {fr.MINUS + format(abs(pt), 'g') if pt else 'PK'}")
            ml = mk.get("h2h") or {}
            if hn in ml and an in ml:
                bits.append(f"ML {fr.esc(g.away.abbr)} {fr.am(ml[an]['price'])} / {fr.esc(g.home.abbr)} {fr.am(ml[hn]['price'])}")
            if not bits:
                return ""
            return f"{fr.esc(fr.book(mybook.key()))}{' live' if g.state == 'in' else ''}: " + " · ".join(bits)
        except Exception:  # noqa: BLE001 - a display extra must never break /today
            log.debug("today bovada line failed", exc_info=True)
            return ""

    def _vp_lines(self, sport: str, gs, odds: Dict[str, Any], where: str) -> Dict[str, str]:
        """{event_id: '💎 Value ...'} for pregame NFL/CFB games, recomputed at query time."""
        if sport not in ("nfl", "ncaaf"):
            return {}
        from .markets import outcome_name_for
        aliases = getattr(self.vs, "aliases", None) or {}
        reqs, ids = [], []
        for g in gs:
            if g.state != "pre":
                continue
            cands = [_vp_from_marv(sport, g.home.name, g.away.name)]
            try:
                ev = match_event(g.home.name, g.away.name, g.start, (odds or {}).get("pre") or [], aliases)
                if ev:
                    cands.append(_vp_from_event(ev, outcome_name_for(g.home.name, ev, aliases),
                                                outcome_name_for(g.away.name, ev, aliases)))
            except Exception:  # noqa: BLE001
                pass
            cands = [c for c in cands if c]
            if not cands:
                continue
            best = max(cands, key=lambda c: _vp_ts(c.get("at")))   # freshest price wins
            reqs.append({"sport": {"ncaaf": "cfb"}.get(sport, sport), "home": g.home.name, "away": g.away.name,
                         "prices": best, "where": where})
            ids.append(g.event_id)
        return {i: _vp_fmt(r) for i, r in zip(ids, _vp_eval(reqs)) if r}

    def cmd_today(self, arg: str = "") -> str:
        games = self.games()
        day = self.now().astimezone(CT).date()
        lines = [f"📅 <b>Today, {day:%a %b} {day.day}</b>"]
        n = 0
        for sport, gs in games.items():
            todays = [g for g in gs if _local_date(g.start) == day or g.state == "in"]
            if not todays:
                continue
            odds = self._today_odds(sport)
            lines.append(f"\n{fr.emoji(sport)} <b>{sport.upper()}</b>")
            vp = self._vp_lines(sport, todays, odds, "today")
            for g in sorted(todays, key=lambda x: x.start):
                lines.append("  " + self._game_line(sport, g))
                bb = self._bovada_bits(sport, g, odds)
                if bb:
                    lines.append(f"     <i>{bb}</i>")
                if vp.get(g.event_id):
                    lines.append(vp[g.event_id])
                n += 1
        if not n:
            lines.append("No games we follow today.")
        lines.append(f"\nOpen paper bets: {sum(1 for _ in self.db.open_bets())} · /edges for alerts")
        return "\n".join(lines)

    def cmd_upcoming(self, arg: str = "") -> str:
        want = SPORT_WORDS.get(arg.strip().lower(), arg.strip().lower()) if arg else ""
        if self.vs is None:
            return "Game finder is turned off."
        start = self.now().astimezone(CT).date()
        lines = [f"🗓 <b>Next 7 days{' - ' + fr.esc(want.upper()) if want else ''}</b>"]
        for sport, sc in self.vs.sports.items():
            if want and sport != want:
                continue
            if sc.get("kind", "us") != "us":          # soccer props-only leagues: not listed here
                continue
            from .espn import parse_scoreboard
            seen: Dict[str, Any] = {}
            failed = 0
            # ESPN's NFL/NCAAF scoreboards ignore a YYYYMMDD-YYYYMMDD range (0 events) -> ask day by day
            for i in range(8):
                day = start + timedelta(days=i)
                params = dict(sc.get("params") or {}, dates=f"{day:%Y%m%d}")
                try:
                    for g in parse_scoreboard(self.vs.fetch(f"{sc['espn']}/scoreboard", params) or {}, sport, "us"):
                        if g.state != "post":
                            seen.setdefault(g.event_id, g)
                except Exception:  # noqa: BLE001
                    failed += 1
            if failed == 8:
                lines.append(f"{fr.emoji(sport)} {sport.upper()}: schedule unavailable right now")
                continue
            gs = list(seen.values())
            if sc.get("scope", "all") != "all":
                if not self.vs.top30:
                    self.vs.load_top30()
                gs = [g for g in gs if self.vs.registry.in_scope(sport, g, {"top30": self.vs.top30})]
            lines.append(f"\n{fr.emoji(sport)} <b>{sport.upper()}</b> ({len(gs)})")
            vp = self._vp_lines(sport, gs[:60], self._today_odds(sport), "upcoming")
            for g in sorted(gs, key=lambda x: x.start)[:25]:
                lines.append("  " + fr.game_line(sport, g.away.name, g.home.name, g.start, now=self.now()))
                if vp.get(g.event_id):
                    lines.append(vp[g.event_id])
            if len(gs) > 25:
                lines.append(f"  ... and {len(gs) - 25} more")
        return "\n".join(lines)

    def cmd_game(self, arg: str = "") -> str:
        if not arg:
            return "Send a team, e.g. <b>/game bears</b>"
        hit = self.find_game(arg)
        if hit is None:
            return f"I couldn't find a game for <b>{fr.esc(arg)}</b> today or tomorrow. Try /upcoming."
        sport, g = hit
        out = [f"{fr.emoji(sport)} <b>{fr.esc(fr.nick(g.away.name, sport))} @ {fr.esc(fr.nick(g.home.name, sport))}</b>",
               self._game_line(sport, g).split(", ", 1)[-1]]
        ev, note = self.featured_event(sport, g)
        if ev:
            from . import live_quality as lq
            from . import mybook
            live = g.state == "in"
            if live and getattr(g, "score_changed_ts", None) is None:
                g.score_changed_ts = self._score_changed_ts(g)
            stale = lq.stale_books(ev, g) if live else {}       # Marvin 2026-10-03: lagging/pre-score books out
            m, ok, info = (None, False, {}) if live else self.vs.model_for(sport, g)
            fs = vf.featured_findings(ev, sport, self.vs.vc, m, info.get("sigma") or self.vs.sports[sport]["sigma"],
                                      ok, registry=self.vs.registry, exclude_books=stale)
            main: Dict[Tuple[str, str], vf.Finding] = {}
            for f in vf.best_per_selection(fs):
                if f.market not in ("h2h", "spreads", "totals"):
                    continue
                k = (f.market, f.selection)
                if k not in main or f.n_books > main[k].n_books:
                    main[k] = f
            out.append(f"Best prices ({fr.esc(friendly_age(note))}):")
            cap = lq.edge_cap(live, self.vs.vc)
            frac = None
            if live:
                try:
                    from .espn import time_left_frac
                    lg = (self.scanner.leagues.get(sport) if self.scanner is not None else None) or {}
                    if lg.get("periods"):
                        frac = time_left_frac(g, int(lg["periods"]), float(lg["period_minutes"]))
                except Exception:  # noqa: BLE001
                    frac = None
            order = {"h2h": 0, "spreads": 1, "totals": 2}
            for (mk, sel), f in sorted(main.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])):
                label = fr.pick_label(sport, mk, sel, f.point, None, g.home.name, g.away.name)
                flag = " ✅" if f.market_flag else ""
                sl = fr.short_line(sport, label, f.price, f.book, f.fair_p, flag,
                                   my_price=f.extra.get("my_price"), ev_min=self.vs.ev_min_for(f), edge_cap=cap)
                extra = ""
                if mk in ("spreads", "totals") and f.extra.get("my_price") is None and not mybook.is_mine(f.book):
                    mine = lq.my_line(ev, mk, sel, mybook.is_mine)
                    sd = lq.sd_for(sport, mk, frac if live else None)
                    if mine is not None and f.point is not None:
                        extra = "     " + self._my_line_bit(sport, mk, sel, f, mine, sd, g, cap)
                        sl = sl.replace(f" · not on {fr.esc(fr.book(mybook.key()))}", "")
                out.append(sl)
                if extra:
                    out.append(extra)
            if stale:
                names = ", ".join(f"{fr.esc(fr.book(b))} ({fr.esc(why)})" for b, why in sorted(stale.items()))
                out.append(f"⚠️ Skipped stale live prices: {names}")
                if any(mybook.is_mine(b) for b in stale):
                    out.append(f"⚠️ {fr.esc(fr.book(mybook.key()))}'s live price above looks stale; re-check it in the app")
            if m is not None:
                fav = fr.nick(g.home.name if m >= 0 else g.away.name, sport)
                out.append(f"Our model: {fr.esc(fav)} by {abs(m):.1f} (for info only)"
                           + (" ⚠️ low confidence: playoffs" if info.get("low_confidence") else "")
                           + ("" if ok else " (early season, few games)"))
        else:
            out.append(f"Prices: {fr.esc(friendly_age(note))}")
        posted = {"home_spread": None, "total": None}
        cands = []
        if ev:
            from . import sweetspot as ss
            cands = list(main.values())
            posted = ss.posted_lines(cands, g.home.name)
        mres = self._marv_h2h_full(sport, g, posted)
        mh = mres.get("line", "")
        if mh:
            out.append(fr.esc(mh))
            if (mres.get("models") or {}).get("line_source") != "posted" and any(v is not None for v in posted.values()):
                bits = []
                if posted.get("home_spread") is not None:
                    bits.append(f"{fr.nick(g.home.name, sport)} {posted['home_spread']:+g}")
                if posted.get("total") is not None:
                    bits.append(f"total {posted['total']:g}")
                out.append(f"⚠️ Marv's reads above could not be confirmed at the posted line ({fr.esc(', '.join(bits))}); "
                           f"any other spread/total in them is Marv's stored line. Not used for the sweet spot.")
        try:  # 2026-10-23 (Marvin approved Oct 20): CFB H2H matchup model line (adj = Model+SRS headline)
            from . import h2h_model as _h2h
            hl = _h2h.line(sport, event_id=g.event_id, home=g.home.name, away=g.away.name, marv_pick=(mres.get("pick") or ""))
            if hl:
                out.append(fr.esc(hl))
        except Exception:  # noqa: BLE001
            pass
        if ev:
            out.append(self._sweet_spot(sport, g, cands, mres.get("models") or {}, posted))
        return "\n".join(out)

    def _sweet_spot(self, sport, g, cands, models, posted) -> str:
        """SWEET SPOT line, computed fresh from this card's findings (latest cached odds) + Marv's reads at the
        posted lines; never cached. Paper-tracked as its own type ('sweet_spot'), never sent as an alert."""
        from . import sweetspot as ss
        from . import mybook
        c = ss.cfg(self.cfg)
        if not c.get("enabled", True):
            return ""
        try:
            res = ss.evaluate(cands, models, g.home.name, posted, c)
            txt = ss.text(res, lambda f: fr.pick_label(sport, f.market, f.selection, f.point, None, g.home.name,
                                                       g.away.name),
                          lambda b: fr.esc(fr.book(b)), fr.am, fr.esc(fr.book(mybook.key())), c)
            if res.get("pick") and c.get("paper_track", True):
                self._paper_sweet_spot(sport, g, res["pick"], c)
            return txt
        except Exception:  # noqa: BLE001 - never break /game
            log.exception("sweet spot failed")
            return "ð¯ No sweet spot: pass"

    def _paper_sweet_spot(self, sport, g, b, c) -> None:
        f = b["f"]
        side = (f.selection or "").lower() if f.market == "totals" else ("home" if f.selection == g.home.name else "away")
        key = f"{g.event_id}:sweet_spot:{f.market}:{f.selection}:{'live' if g.state == 'in' else 'pre'}"
        if self.db.has_alert(key):
            return
        stake = float(c.get("paper_stake", 100))
        aid = self.db.insert_alert(dict(
            dedupe_key=key, event_id=g.event_id, league=sport, strategy="sweet_spot", market=f.market,
            selection=f.selection, line=f.point, price=f.price, book=f.book, fair_p=f.fair_p, market_p=None,
            edge=f.ev, value=1, trigger="paper", message="sweet spot (paper)",
            details_json=json.dumps({"votes": b["votes"], "my_price": f.extra.get("my_price"),
                                     "matchup": f"{g.away.name} @ {g.home.name}", "live": g.state == "in"},
                                    default=str)))
        if aid is None:
            return
        self.db.insert_paper_bet(dict(
            alert_id=aid, event_id=g.event_id, league=sport, strategy="sweet_spot", market=f.market,
            selection=f.selection, side=side, line=f.point, price=f.price, book=f.book, stake=stake,
            to_win=round(stake * (american_to_decimal(f.price) - 1), 2), tag="paper:sweet_spot", sport=sport,
            commence=g.start, ev=f.ev, fair_p=f.fair_p))

    def _marv_h2h_full(self, sport, g, posted=None) -> dict:
        """Marv H2H line + structured model reads, asked at the posted spread/total so every Marv model uses the
        line this card shows. {} on any problem."""
        import os
        try:
            if os.environ.get("MARV_OVERLAY", "on").lower() in ("0", "off", "false", "no"):
                return {}
            leagues = {x.strip() for x in os.environ.get("MARV_OVERLAY_LEAGUES", "ncaaf").lower().split(",") if x.strip()}
            if (sport or "").lower() not in leagues:
                return {}
            url = os.environ.get("MARV_OVERLAY_URL", "http://127.0.0.1:8787/overlay").rsplit("/", 1)[0] + "/h2h"
            params = {"sport": sport, "team": g.home.name, "other": g.away.name}
            for k in ("home_spread", "total"):
                if (posted or {}).get(k) is not None:
                    params[k] = posted[k]
            r = requests.get(url, params=params, timeout=float(os.environ.get("MARV_H2H_TIMEOUT", "3")))
            return (r.json() or {}) if r.status_code == 200 else {}
        except Exception as e:  # noqa: BLE001
            log.debug("Marv H2H skipped: %s", e)
            return {}

    def _marv_h2h(self, sport, g) -> str:
        """Marv Predict's game-level H2H line (pick + why; a % only when Marv's rule is backtest-proven) from the
        local Marv bridge, shown next to March_edge's own prices. Read-only, 1.5 s timeout, '' on any problem.
        Same switches as the alert overlay: MARV_OVERLAY=off, MARV_OVERLAY_LEAGUES (default ncaaf)."""
        import os
        try:
            if os.environ.get("MARV_OVERLAY", "on").lower() in ("0", "off", "false", "no"):
                return ""
            leagues = {x.strip() for x in os.environ.get("MARV_OVERLAY_LEAGUES", "ncaaf").lower().split(",") if x.strip()}
            if (sport or "").lower() not in leagues:
                return ""
            url = os.environ.get("MARV_OVERLAY_URL", "http://127.0.0.1:8787/overlay").rsplit("/", 1)[0] + "/h2h"
            r = requests.get(url, params={"sport": sport, "team": g.home.name, "other": g.away.name}, timeout=1.5)
            return (r.json() or {}).get("line", "") if r.status_code == 200 else ""
        except Exception as e:  # noqa: BLE001
            log.debug("Marv H2H skipped: %s", e)
            return ""

    def _my_line_bit(self, sport, mk, sel, f, mine, sd, g, cap) -> str:
        """Marvin 2026-10-03: Bovada's own spread/total even at a different number (e.g. -6.5 vs the market's -5.5),
        priced against the consensus moved to Bovada's number (normal margin), instead of 'not on Bovada'."""
        from . import live_quality as lq
        from . import mybook
        pt, px = mine
        lab = fr.pick_label(sport, mk, sel, pt, None, g.home.name, g.away.name)
        bname = fr.esc(fr.book(mybook.key()))
        if sd is None:
            return f"{bname} has <b>{fr.esc(lab)}</b> {fr.am(px)} (market {f.point:+g})"
        p = lq.at_line(f.fair_p, float(f.point), pt, mk, sel, sd)
        ev = p * american_to_decimal(px) - 1.0
        diff = pt - float(f.point)
        d = f"{diff:+g}".replace("-", fr.MINUS)
        num = f"{d} vs market" if mk == "spreads" else f"{d} vs market total"
        e = (f"edge &gt;{cap * 100:.0f}% ⚠️ likely stale" if ev > cap else f"edge {ev * 100:+.0f}%")
        return f"{bname} has <b>{fr.esc(lab)}</b> {fr.am(px)} ({num}) · win chance {p * 100:.0f}% · {e}"

    def cmd_props(self, arg: str = "") -> str:
        if not arg:
            return "Usage: /props &lt;player|team&gt;  e.g. /props caitlin clark points, /props bears"
        mkts = prop_markets_in(arg)
        q = strip_prop_words(arg) or arg
        team_hit = self.find_game(q)
        team_q = team_hit is not None and (any(m.startswith(("team_", "alternate_team")) for m in mkts) or not mkts
                                           and _team_like(q, team_hit[1]))
        if team_q:
            sport, g = team_hit
            markets = self.vs.team_markets(sport)
            if not markets:
                return f"Team props are not enabled for {sport.upper()}."
            pev, note = self.props_event(sport, g, "team", markets)
            team = g.home.name if _fuzzy(q, g.home.name) >= _fuzzy(q, g.away.name) else g.away.name
            return self._props_reply(sport, g, pev, note, who=team, markets=markets, title=f"Team props: {team}")
        # player: search cached player props first, then the projection index for the team
        cand = self._find_player(q)
        if cand is None:
            return (f"No player or team found for '{q}'. Player props are cached at the 10:00 CT and pre-kick "
                    f"snapshots for today's in-scope games.")
        sport, g, player = cand
        markets = mkts or (self.vs.vc.get("props_markets", {}) or {}).get(sport, [])
        markets = [m for m in markets if m.startswith(("player_", "batter_", "pitcher_"))] or (self.vs.vc.get("props_markets", {}) or {}).get(sport, [])
        pev, note = self.props_event(sport, g, "player", markets)
        return self._props_reply(sport, g, pev, note, who=player, markets=markets, title=f"Props: {player}")

    def _find_player(self, q: str):
        best, score = None, 0.0
        for sport, gs in self.games().items():
            for g in gs:
                for key_kind in ("player",):
                    oev = self._odds_event(sport, g)
                    ids = [oev["id"]] if oev else []
                    for eid in ids:
                        _, pev = self.cached(f"lastprops:{sport}:{eid}:{key_kind}")
                        for (mk, player) in vf.pair_props(pev or {}).keys():
                            s = _fuzzy(q, player)
                            if s > score:
                                best, score = (sport, g, player), s
        if score >= 0.75:
            return best
        # not cached yet: projection index (no market data) tells us the player's team
        store = getattr(self.vs, "store", None)
        for sport, gs in self.games().items():
            snap = store.players(sport) if store is not None and sport != "ncaaf" else None
            for p in ((snap or {}).get("players") or {}).values():
                s = _fuzzy(q, p.get("name", ""))
                if s >= 0.85 and s > score:
                    team = ((p.get("logs") or [{}])[-1] or {}).get("team", "")
                    g = next((x for x in gs if name_sim(team, x.home.name) >= 0.6 or name_sim(team, x.away.name) >= 0.6
                              or team in (x.home.abbr, x.away.abbr)), None)
                    if g is not None:
                        best, score = (sport, g, p["name"]), s
        return best if score >= 0.75 else None

    def _props_reply(self, sport, g, pev, note, who, markets, title) -> str:
        head = (f"{fr.emoji(sport)} <b>{fr.esc(title)}</b>\n"
                f"{fr.game_line(sport, g.away.name, g.home.name, g.start, now=self.now())}"
                f" · prices {fr.esc(friendly_age(note))}")
        if not pev:
            return head + "\nNo prices posted yet."
        fs = vf.prop_findings(pev, sport, self.vs.vc, markets, registry=self.vs.registry)
        fs = [f for f in fs if f.player and _fuzzy(who, f.player) >= 0.75]
        if not fs:
            return head + f"\nNo prices for {fr.esc(who)} yet."
        try:
            if any(f.market.startswith(("player_", "batter_", "pitcher_")) for f in fs):
                self.vs.apply_player_model(sport, g, [f for f in fs if f.market.startswith(("player_", "batter_", "pitcher_"))])
            self.vs.apply_prop_rules(sport, fs)
        except Exception:  # noqa: BLE001
            log.debug("prop model/rules failed", exc_info=True)
        best = vf.best_per_selection(fs)
        by: Dict[Tuple[str, Optional[float]], List[vf.Finding]] = {}
        for f in best:
            by.setdefault((f.market, f.point), []).append(f)
        rows = sorted(by.items(), key=lambda kv: (kv[0][0], -max(x.n_books for x in kv[1])))
        shown: Dict[str, int] = {}
        out = [head]
        for (mk, pt), lst in rows:
            if shown.get(mk, 0) >= (3 if mk.startswith("alternate_") else 1):
                continue
            shown[mk] = shown.get(mk, 0) + 1
            for f in sorted(lst, key=lambda x: x.selection):
                flag = " ✅" if f.market_flag else (" 📋" if f.extra.get("watchlist") else "")
                label = fr.pick_label(sport, f.market, f.selection, f.point, f.player, g.home.name, g.away.name)
                out.append(fr.short_line(sport, label, f.price, f.book, f.fair_p, flag,
                                         my_price=f.extra.get("my_price"), ev_min=self.vs.ev_min_for(f)))
            mp = next((f.model_p for f in lst if f.model_p is not None and f.selection == "Over"), None)
            if mp is not None:
                out.append(f"   our projection: {mp * 100:.0f}% for the over (info only)")
        out.append("✅ = alert-worthy · 📋 = paper-only watchlist. Props have no proven long-run edge yet.")
        return "\n".join(out)

    def _alerts(self, hours: float, include_watch: bool = False) -> List[Dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self.db.conn.execute(
            "SELECT a.*, p.commence AS commence, p.ev AS pev, p.tag AS ptag, p.player AS player, "
            "p.stake AS stake FROM alerts a LEFT JOIN paper_bets p ON p.alert_id = a.id WHERE a.ts >= ? "
            "ORDER BY a.ts DESC", (since,)).fetchall()
        out = [dict(r) for r in rows]
        return out if include_watch else [r for r in out if (r.get("trigger") or "") != "watchlist"]

    def _row_line(self, r: Dict[str, Any], extra: str = "") -> str:
        sport = (r.get("league") or "").lower()
        label = fr.pick_label(sport, r.get("market") or "", r.get("selection") or "", r.get("line"), r.get("player"))
        p = r.get("fair_p")
        if r.get("strategy") == "value" and p is None and r.get("edge") is not None and r.get("price"):
            p = (1 + r["edge"]) / american_to_decimal(r["price"])
        try:
            det = json.loads(r.get("details_json") or "{}")
        except (TypeError, ValueError):
            det = {}
        line = fr.short_line(sport, label, r.get("price"), r.get("book"), p, extra, my_price=det.get("my_price"),
                             ev_min=float(det.get("ev_min") or _default_ev_min(r)))
        if det.get("matchup"):
            line += f" · {fr.esc(det['matchup'])}"
        return line

    @staticmethod
    def _details(r: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return json.loads(r.get("details_json") or "{}") or {}
        except (TypeError, ValueError):
            return {}

    def _real_alert(self, r: Dict[str, Any]) -> bool:
        """Only priced, positive-edge alerts belong in /edges (no info lines, model-only rows, no-price rows)."""
        if r.get("price") is None:
            return False
        if r.get("strategy") in ("line_swing",) or (r.get("trigger") or "") in ("info", "watchlist"):
            return False
        if r.get("strategy") == "value":
            v = int(r.get("value") or 0)
            if not (v & 1):                       # model-only flag (model alerts are off) -> not an alert
                vc = (self.cfg.get("strategies") or {}).get("value_finder") or {}
                if not vc.get("model_alerts", False):
                    return False
            if r.get("edge") is None or float(r["edge"]) <= 0:
                return False
        elif r.get("edge") is not None and float(r["edge"]) <= 0 and not r.get("value"):
            return False
        return True

    def _at_my_book(self, r: Dict[str, Any]) -> bool:
        from . import mybook
        if mybook.is_mine(r.get("book")):
            return True
        det = self._details(r)
        mp = det.get("my_price")
        if mp is None or r.get("price") is None:
            return False
        if r.get("strategy") == "value" and r.get("edge") is not None:
            p = (1 + float(r["edge"])) / american_to_decimal(r["price"])
        else:
            p = r.get("fair_p")
        return mybook.clears(p, mp, float(det.get("ev_min") or _default_ev_min(r)))

    def cmd_edges(self, arg: str = "") -> str:
        from . import mybook
        rows = [r for r in self._alerts(24) if self._real_alert(r)]
        if not rows:
            return "No alerts in the last 24 hours."
        when = lambda r: f"  <i>{fr.esc(fr.when(r['ts'], self.now()))}</i>"   # noqa: E731
        name = fr.esc(fr.book(mybook.key()))
        if not mybook.only():
            out = [f"🔔 <b>Alerts, last 24 hours</b> ({len(rows)})"]
            out += [self._row_line(r) + when(r) for r in rows[:10]]
            if len(rows) > 10:
                out.append(f"... and {len(rows) - 10} more (/report)")
            return "\n".join(out)
        mine = [r for r in rows if self._at_my_book(r)]
        other = [r for r in rows if not self._at_my_book(r)]
        out = [f"🔔 <b>Alerts at {name}, last 24 hours</b> ({len(mine)})"]
        out += [self._row_line(r) + when(r) for r in mine[:10]] or [f"None at {name} yet."]
        if other:
            out.append(f"\n📋 <b>Not at {name} (watchlist only)</b> ({len(other)})")
            out += [self._row_line(r) + when(r) for r in other[:5]]
            if len(other) > 5:
                out.append(f"... and {len(other) - 5} more")
        return "\n".join(out)

    def _vp_recs(self) -> List[str]:
        out: List[str] = []
        try:
            soon = self.now() + timedelta(hours=36)
            for sport, gs in self.games().items():
                gs = [g for g in gs if g.state == "pre" and g.start and g.start <= soon]
                vp = self._vp_lines(sport, gs, self._today_odds(sport), "recs")
                for g in sorted(gs, key=lambda x: x.start):
                    if vp.get(g.event_id):
                        out += ["  " + self._game_line(sport, g), vp[g.event_id]]
        except Exception:  # noqa: BLE001
            log.debug("recs value plays failed", exc_info=True)
        return (["", "💎 <b>Value Plays</b> (Marv, recomputed now; tracking, unproven)"] + out) if out else []

    def cmd_recs(self, arg: str = "") -> str:
        now = datetime.now(timezone.utc)
        rows = []
        for r in self._alerts(36, include_watch=True):
            c = r.get("commence")
            try:
                if c and datetime.fromisoformat(c.replace("Z", "+00:00")) <= now:
                    continue
            except ValueError:
                pass
            rows.append(r)
        if not rows:
            return "\n".join(["Nothing to recommend right now (no alerts for games that haven't started)."] + self._vp_recs())
        from . import policy
        rows = [r for r in rows if policy.status(_row_key(r)) != "off"]
        rows.sort(key=lambda r: (policy.RANK.get(policy.status(_row_key(r)), 1), -(r.get("edge") or 0)))
        out = ["🏆 <b>Best current edges</b> (proven types first)"]
        for i, r in enumerate(rows[:12], 1):
            out.append(f"{i}. " + self._row_line(r))
            out.append(f"   <i>{fr.esc(honesty_tag(r))}</i>")
        out += self._vp_recs()
        return "\n".join(out)

    def cmd_injuries(self, arg: str = "") -> str:
        ck = getattr(self.scanner, "injuries", None)
        if ck is None:
            return "Injury check is not available right now."
        return ck.team_text({}, arg)

    def cmd_trends(self, arg: str = "") -> str:
        from .trends import trends_text
        st = getattr(self.scanner, "trend_store", None)
        if st is None:
            return "Team trends are not available right now."
        return trends_text(st, arg)

    def cmd_swings(self, arg: str = "") -> str:
        from .line_swing import DEFAULTS, swings_text
        ts, rows = self.db.kv_get("swings:current")
        c = {**DEFAULTS, **((self.cfg.get("strategies") or {}).get("line_swing") or {})}
        txt = swings_text(rows or [], f"updated {friendly_age('cache, ' + age_str(ts, self.now())).replace('from ', '')}"
                          if ts else "no live scan yet", c["ml_trigger"], c["spread_trigger"])
        from .total_swing import tswings_text
        _, trows = self.db.kv_get("tswings:current")
        t = tswings_text(trows or [])
        return f"{txt}\n\n{t}" if t else txt

    def _key_label(self) -> Optional[str]:
        for o in (getattr(self.vs, "odds", None), getattr(self.scanner, "odds", None)):
            lab = getattr(o, "key_label", None) if o is not None else None
            if lab:
                return lab
        return None

    def cmd_report(self, arg: str = "") -> str:
        """/report = simple summary + per-type table; /report full adds the daily table, watchlist and notes."""
        from .paper import report, report_summary
        unit = (self.cfg.get("strategies", {}).get("value_finder", {}) or {}).get("unit", 100)
        kl = self._key_label()
        today = self.now().astimezone(CT).date()
        txt = report_summary(self.db, today=today, unit=unit, key_label=kl)
        if arg.strip().lower() not in ("full", "all", "detail", "details"):
            return txt
        return "<pre>" + fr.esc(report(self.db, self.cfg.get("paper", {}).get("goal_per_day", 120), days=7,
                                       unit=unit, today=today, key_label=kl)) + "</pre>"

    def cmd_budget(self, arg: str = "") -> str:
        from .budget import budget_report
        return ("<pre>" + fr.esc(budget_report(self.cfg, self.db)) + "</pre>\n"
                f"Lookups left today: <b>{self.allowance_left():.0f}</b> credits")

    def cmd_search(self, arg: str = "") -> str:
        if self.find_game(arg) is not None:
            return self.cmd_game(arg)
        if self._find_player(arg) is not None:
            return self.cmd_props(arg)
        return "Sorry, I didn't catch that.\n\n" + fr.esc(HELP)

    def _settle_now(self) -> None:
        """Settle any open paper bets from free ESPN data before grading (no Odds API credits)."""
        sc = self.scanner
        if sc is None:
            return
        try:
            from .scanner import settle_open
            settle_open(self.db, {}, sc.leagues, sc.summary_fn)
        except Exception:  # noqa: BLE001
            log.warning("pre-grade settle failed", exc_info=True)

    def cmd_grade(self, arg: str = "") -> str:
        from .grader import report_card
        today = self.now().astimezone(CT).date()
        a = (arg or "").strip().lower()
        full = a.endswith("full") or a.startswith("full")
        a = a.replace("full", "").strip()
        day = today - timedelta(days=1)
        if a in ("today", "tonight"):
            day = today
        elif a and a != "yesterday":
            d = _parse_day(a, today)
            if d is None:
                return "Send a date like <b>/grade 2026-10-01</b>, <b>/grade oct 1</b> or <b>/grade yesterday</b>."
            day = d
        self._settle_now()
        return report_card(self.db, day, today, full=full)

    def maybe_grade(self) -> bool:
        """Morning report card for yesterday, once per CT day at telegram.bot.grade_time (default 08:45)."""
        if not self.bc.get("grade_report", True):
            return False
        local = self.now().astimezone(CT)
        hh, mm = map(int, str(self.bc.get("grade_time", "08:45")).split(":"))
        start = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if not (start <= local < start + timedelta(hours=3)):
            return False
        k = f"grade:{local.date().isoformat()}"
        if self.db.kv_get(k)[1]:
            return False
        self.db.kv_set(k, True)
        self._settle_now()
        from .grader import report_card
        self.push(report_card(self.db, local.date() - timedelta(days=1), local.date()))
        return True

    def maybe_digest(self) -> bool:
        """Morning digest once per CT day at telegram.bot.digest_time (default 09:00)."""
        if not self.bc.get("digest", True):
            return False
        local = self.now().astimezone(CT)
        hh, mm = map(int, str(self.bc.get("digest_time", "09:00")).split(":"))
        start = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if not (start <= local < start + timedelta(hours=float(self.bc.get("digest_window_h", 3)))):
            return False                          # e.g. a 2 pm restart does not send a "morning" digest
        k = f"digest:{local.date().isoformat()}"
        if self.db.kv_get(k)[1]:
            return False
        self.db.kv_set(k, True)
        self.push(self.digest_text())
        return True

    def digest_text(self) -> str:
        local = self.now().astimezone(CT)
        y = (local.date() - timedelta(days=1)).isoformat()
        bets = [dict(b) for b in self.db.all_bets()]
        main = [b for b in bets if not str(b.get("tag") or "").startswith("watchlist")]
        yd = [b for b in main if local_day(b.get("settled_at")) == y or b.get("bet_date") == y]   # CT days
        st = [b for b in yd if b["status"] in ("won", "lost", "push")]
        w, l = sum(b["status"] == "won" for b in st), sum(b["status"] == "lost" for b in st)
        pnl = sum(b["pnl"] for b in st)
        lines = [f"☀️ <b>Good morning! {local:%A, %b} {local.day}</b>"]
        games = self.games(force=True)
        counts = [(s, sum(1 for g in gs if _local_date(g.start) == local.date())) for s, gs in games.items()]
        counts = [(s, n) for s, n in counts if n]
        lines.append("Today: " + (" · ".join(f"{fr.emoji(s)} {s.upper()} {n}" for s, n in counts)
                                  or "no games we follow"))
        if st:
            lines.append(f"Yesterday: {w} won, {l} lost, <b>{'+' if pnl >= 0 else '-'}${abs(pnl):,.0f}</b> (paper)")
        else:
            lines.append("Yesterday: no bets settled")
        wl_open = sum(b["status"] == "open" for b in bets) - sum(b["status"] == "open" for b in main)
        lines.append(f"Open paper bets: {sum(b['status'] == 'open' for b in main)}"
                     + (f" (+{wl_open} on the watchlist)" if wl_open else ""))
        lu = self.db.last_usage(getattr(self.vs.odds, "key_label", None)) if self.vs else None
        if lu and lu["remaining"] is not None:
            lines.append(f"Odds credits left this month: {lu['remaining']:,.0f}")
        try:
            tr = getattr(self.scanner, "trends", None)
            if tr is not None:
                lines += tr.digest_lines(games, local.date())
        except Exception:  # noqa: BLE001
            log.exception("trend digest failed")
        try:  # 2026-10-23 (Marvin): Marv Predict LIVE picks paper line (ajbet/marv_live.py)
            from . import marv_live as _ml
            _l = _ml.digest_line(local.date() - timedelta(days=1))
            if _l:
                lines.append(_l)
        except Exception:  # noqa: BLE001
            log.warning("marv live digest line failed", exc_info=True)
        lines.append("Prop prices are checked at 10:00 AM CT and again before each game. /today · /recs · /help")
        return "\n".join(lines)

    def push_results(self, settled: List[str]) -> bool:
        """Compact results grouped by game + the running total (ajbet/results_fmt.py). The total counts every
        non-watchlist bet SETTLED today (CT) - the same rule /report uses for 'Today'."""
        if not settled or not self.bc.get("results", True):
            return False
        from .results_fmt import ids_from, results_text
        day = today_local()
        rows = [dict(b) for b in self.db.all_bets()]      # sqlite3.Row has no .get(): convert first
        ids = ids_from(settled)
        by_id = {int(b["id"]): b for b in rows}
        just = [by_id[i] for i in ids if i in by_id]
        today_main = [b for b in rows
                      if local_day(b.get("settled_at")) == day and not str(b.get("tag") or "").startswith("watchlist")]
        from . import recap_split as _rs   # 2026-10-24 (Marvin): AJ's copy hides h2h_cfb; H2H_HIDE_FROM_AJ=off
        if just:
            _rs.push(self, just, today_main, results_text)
        else:                                             # unparsable settle lines: show them as-is + the total
            body = "\n".join(f"• {fr.esc(s)}" for s in settled[:15])
            _rs.push(self, [], today_main,
                     lambda j, t: results_text([], t).replace("🧾 <b>Results</b>", f"🧾 <b>Results</b>\n{body}", 1))
        return True

def _row_key(r: Dict[str, Any]) -> str:
    from .recalibrate import paper_key
    return paper_key({**r, "tag": r.get("ptag")}) or "value"


def honesty_tag(r: Dict[str, Any]) -> str:
    """Plain-English caveat for /recs."""
    trig = (r.get("trigger") or "").lower()
    strat = (r.get("strategy") or "").lower()
    mk = r.get("market") or ""
    if trig == "watchlist":
        return "📋 watchlist, not sent"
    from . import policy
    if policy.status(_row_key(r)) == "kept":
        return "✅ proven in backtests (no guarantee)"
    if strat == "fav_comeback":
        return "⚠️ live comeback, low confidence"
    if strat == "soccer_grind":
        return "⚠️ live soccer, low confidence"
    if "model" in trig:
        return "model pick, lost money in testing"
    if mk.startswith(("player_", "batter_", "pitcher_")) or "team_totals" in mk:
        return "prop line shopping, unproven"
    return "line shopping, unproven"


def _parse_day(a: str, today):
    from datetime import date as _date
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d", "%b %d", "%B %d", "%b %d %Y", "%B %d %Y"):
        txt = a.title() if "%b" in fmt or "%B" in fmt else a
        try:
            d = (datetime.strptime(txt, fmt) if "%Y" in fmt
                 else datetime.strptime(f"{txt} {today.year}", fmt + " %Y")).date()
        except ValueError:
            continue
        if "%Y" not in fmt:
            d = _date(today.year, d.month, d.day)
            if d > today:
                d = _date(today.year - 1, d.month, d.day)
        return d
    return None


def _default_ev_min(r: Dict[str, Any]) -> float:
    m = r.get("market") or ""
    return 0.05 if (m.startswith(("player_", "batter_", "pitcher_")) or "team_totals" in m) else 0.03


def _html_chunks(text: str, n: int = MAX_LEN) -> List[str]:
    """Chunk without breaking a <pre> block."""
    if text.startswith("<pre>") and text.count("<pre>") == 1 and "</pre>" in text:
        inner, _, rest = text[5:].partition("</pre>")
        parts = [f"<pre>{c}</pre>" for c in chunks(inner, n - 11)]
        if rest.strip():
            parts[-1] += rest
        return parts
    return chunks(text, n)


def friendly_age(note: str) -> str:
    """'cache, 12 min old' -> 'from 12 min ago'; 'fresh' -> 'just updated'."""
    if not note:
        return ""
    if note.startswith("fresh"):
        return "just updated"
    out = note.replace("cache, just now", "just updated").replace("cache, ", "from ").replace(" old", " ago")
    out = out.replace("daily query allowance used up", "daily lookup limit reached, showing saved prices")
    out = out.replace("no cached odds", "no saved prices").replace("no cached props", "no saved prices")
    return out


def _local_date(iso: str):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(CT).date()
    except (ValueError, AttributeError):
        return None


def _team_like(q: str, g) -> bool:
    return max(_fuzzy(q, g.home.name), _fuzzy(q, g.away.name), 1.0 if q.upper() in (g.home.abbr, g.away.abbr) else 0) >= 0.8
