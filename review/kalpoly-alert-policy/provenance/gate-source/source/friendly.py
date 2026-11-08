"""User-friendly Telegram HTML cards (phone-first format, Marvin 2026-10-02 11 PM CT; see ajbet/tgfmt.py).

    💰 PROP VALUE — <b>Caitlin Clark OVER 21.5 pts (−130)</b> @ Bovada
    🏀 Fever @ Aces · 8:00 PM CT
    Edge at Bovada <b>+8%</b> ⭐⭐
    Win chance 60% vs price 57% · good to −145
    Stake $100 to win $77
    💡 Bovada is softer than the other books on this prop

No jargon, short, all times CT. Every dynamic string is HTML-escaped."""
from __future__ import annotations

import html
import re
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .oddsmath import american_to_decimal

CT = ZoneInfo("America/Chicago")
SPORT_EMOJI = {"nfl": "🏈", "ncaaf": "🏈", "wnba": "🏀", "nba": "🏀", "ncaab": "🏀", "mlb": "⚾", "nhl": "🏒"}
BOOKS = {"betrivers": "BetRivers", "draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM",
         "espnbet": "ESPN BET", "pinnacle": "Pinnacle", "bovada": "Bovada", "betonlineag": "BetOnline",
         "lowvig": "LowVig", "hardrockbet": "Hard Rock", "hardrock": "Hard Rock", "williamhill_us": "Caesars", "fanatics": "Fanatics",
         "ballybet": "Bally Bet", "betus": "BetUS", "mybookieag": "MyBookie", "espn": "ESPN BET"}
STAT = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "3-pointers made",
        "points_rebounds_assists": "pts+reb+ast", "points_rebounds": "pts+reb", "points_assists": "pts+ast",
        "pass_yds": "passing yds", "rush_yds": "rushing yds", "reception_yds": "receiving yds",
        "receptions": "catches", "pass_tds": "passing TDs", "rush_attempts": "rush attempts",
        "pass_rush_yds": "pass+rush yds", "rush_reception_yds": "rush+rec yds", "pass_completions": "completions",
        "pass_attempts": "pass attempts", "blocks": "blocks", "steals": "steals",
        # MLB / NHL / soccer (Oct 3 2:22 PM: props on for every sport)
        "hits": "hits", "runs_scored": "runs", "rbis": "RBIs", "home_runs": "home runs", "walks": "walks",
        "total_bases": "total bases", "hits_runs_rbis": "hits+runs+RBIs", "strikeouts": "strikeouts",
        "hits_allowed": "hits allowed", "earned_runs": "earned runs", "outs": "outs recorded",
        "shots_on_goal": "shots on goal", "total_saves": "saves", "goals": "goals", "blocked_shots": "blocked shots",
        "shots_on_target": "shots on target", "shots": "shots"}
PERIOD = {"h1": "1st half", "h2": "2nd half", "q1": "1st quarter", "q2": "2nd quarter", "q3": "3rd quarter",
          "q4": "4th quarter"}
PRO = {"nfl", "wnba", "nba", "mlb", "nhl"}
MINUS = "\u2212"


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def emoji(sport: str) -> str:
    return SPORT_EMOJI.get((sport or "").lower(), "⚽")


def am(p: Optional[float]) -> str:
    """American odds with a real minus sign: −125 / +120."""
    if p is None:
        return "-"
    p = round(float(p))
    return f"+{p}" if p > 0 else f"{MINUS}{abs(p)}"


def book(key: Optional[str]) -> str:
    k = (key or "").lower()
    return BOOKS.get(k, (key or "?").replace("_", " ").title())


def implied(price: float) -> float:
    return 1.0 / american_to_decimal(price)


def ev_at(p: float, price: float) -> float:
    return p * american_to_decimal(price) - 1.0


def max_price(p: float, ev_min: float = 0.05) -> Optional[float]:
    """Worst American price that still gives >= ev_min EV at win probability p (rounded inward)."""
    if not p or p <= 0:
        return None
    dec = (1.0 + ev_min) / p
    if dec <= 1.0:
        return None
    if dec >= 2.0:
        return float(math.ceil((dec - 1.0) * 100 - 1e-9))
    return float(-math.floor(100.0 / (dec - 1.0) + 1e-9))


LIVE_MAX_STARS = 1      # round 2 (Marvin 2026-10-02): live picks never show more than 1 star


def stars(ev: float, live: bool = False) -> str:
    if ev is None or ev < 0.05:
        return ""
    s = "⭐" if ev < 0.07 else ("⭐⭐" if ev <= 0.10 else "⭐⭐⭐")
    return s[:LIVE_MAX_STARS] if live else s


def pct(p: Optional[float]) -> str:
    return "-" if p is None else f"{p * 100:.0f}%"


def nick(team: str, sport: str) -> str:
    """'Indiana Fever' -> 'Fever' for pro leagues; college keeps the school name."""
    t = (team or "").strip()
    if (sport or "").lower() in PRO and " " in t:
        two = {"Trail Blazers", "Red Sox", "White Sox", "Blue Jays", "Maple Leafs", "Golden Knights"}
        last2 = " ".join(t.split()[-2:])
        return last2 if last2 in two else t.split()[-1]
    return t


def score_name(team, sport: str) -> str:
    """Short name for a live score: nickname (pro) or abbreviation (college)."""
    if (sport or "").lower() in PRO:
        return nick(team.name, sport)
    return team.abbr or team.name


def when(iso: Optional[str], now: Optional[datetime] = None) -> str:
    if not iso:
        return "time TBD"
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(CT)
    except ValueError:
        return esc(iso)
    n = (now or datetime.now(timezone.utc)).astimezone(CT)
    hm = t.strftime("%I:%M %p").lstrip("0")
    if t.date() == n.date():
        return f"{hm} CT"
    return f"{t.strftime('%a %b')} {t.day}, {hm} CT"


def game_line(sport: str, away: str, home: str, start: Optional[str] = None, live: Optional[str] = None,
              now: Optional[datetime] = None) -> str:
    g = f"{esc(nick(away, sport))} @ {esc(nick(home, sport))}"
    return f"{g}, {esc(live)}" if live else f"{g}, {when(start, now)}"


def pick_label(sport: str, market: str, selection: str, point: Optional[float], player: Optional[str] = None,
               home: str = "", away: str = "") -> str:
    """Plain-English bet label: 'Caitlin Clark OVER 21.5 pts', 'Bears to WIN', 'OVER 44.5 total points'."""
    mk = market or ""
    sel = selection or ""
    per = None
    for suf, name in PERIOD.items():
        if mk.endswith("_" + suf):
            per, mk = name, mk[: -len(suf) - 1]
            break
    ptxt = "" if point is None else f"{point:g}"
    m = re.fullmatch(r"(Over|Under)\s+([\d.]+)", sel.strip(), re.I)
    if m:                                   # soccer alt totals store selection "Under 9.5"
        sel = m.group(1).capitalize()
        ptxt = ptxt or m.group(2)
    side = sel.upper() if sel in ("Over", "Under") else sel
    if mk.startswith(("batter_", "pitcher_")):
        stat = mk.split("_", 1)[1]
        return f"{player} {side} {ptxt} {STAT.get(stat, stat.replace('_', ' '))}".strip()
    if mk.startswith("player_"):
        stat = mk[len("player_"):]
        if stat in ("goal_scorer_anytime", "to_receive_card"):
            what = {"goal_scorer_anytime": "to score a goal", "to_receive_card": "to get a card"}[stat]
            return f"{player} {what}" if sel in ("Yes", "Over") else f"{player} NOT {what}"
        if stat in ("anytime_td", "1st_td", "last_td"):
            what = {"anytime_td": "to score a TD", "1st_td": "to score the first TD", "last_td": "to score the last TD"}[stat]
            return f"{player} {what}" if sel in ("Yes", "Over") else f"{player} NOT {what}"
        if stat == "double_double":
            return f"{player} {'to get' if sel == 'Yes' else 'NOT to get'} a double-double"
        return f"{player} {side} {ptxt} {STAT.get(stat, stat.replace('_', ' '))}".strip()
    if "team_totals" in mk:
        team = nick(player, sport) if player else (sel if sel not in ("Over", "Under") else "Team")
        unit = "runs" if sport == "mlb" else ("goals" if sport == "nhl" or (sport and sport not in SPORT_EMOJI) else "points")
        return f"{team}{' ' + per if per else ''} {side} {ptxt} {unit}".replace("  ", " ")
    pre = f"{per}: " if per else ""
    if mk in ("h2h", "h2h_3_way"):
        return f"{pre}Draw" if sel.lower() == "draw" else f"{pre}{nick(sel, sport)} to WIN"
    if "spreads" in mk:
        sp = "" if point is None else (f"+{point:g}" if point > 0 else f"{MINUS}{abs(point):g}" if point < 0 else "pick'em")
        return f"{pre}{nick(sel, sport)} {sp}"
    if "totals" in mk:
        unit = "runs" if sport == "mlb" else ("goals" if sport == "nhl" or (sport and sport not in SPORT_EMOJI) else "points")
        return f"{pre}{side} {ptxt} total {unit}"
    return f"{pre}{sel} {ptxt}".strip()


def my_book_line(win_p: Optional[float], ev_min: float, my_price: Optional[float]) -> str:
    """'Bovada: −120 ✅ good buy' / '❌ too expensive' vs the good-buy-up-to price."""
    from . import mybook
    name = esc(book(mybook.key()))
    if my_price is None:
        return f"{name}: not offered for this pick"
    mp = max_price(win_p, ev_min) if win_p is not None else None
    good = mp is not None and american_to_decimal(my_price) >= american_to_decimal(mp) - 1e-9
    return f"{name}: <b>{am(my_price)}</b> {'✅ good buy' if good else '❌ too expensive'}"


def card(sport: str, label: str, price: Optional[float], book_key: Optional[str], win_p: float,
         ev_min: float, stake: float, unit: float, why: str, game: str,
         warnings: Optional[List[str]] = None, header: str = "", my_price: Optional[float] = None,
         show_my_book: bool = True, live: bool = False, kind: str = "💰 VALUE",
         edge_open: Optional[Dict[str, Any]] = None, paper=None) -> str:
    """Phone-first card (Marvin 2026-10-02 11 PM CT, ajbet/tgfmt.py):

        💰 VALUE — <b>Valkyries ML (−150)</b> @ Bovada
        🏀 Wings 28–26 Valkyries · 4:50 2nd
        Edge at Bovada +8% ⭐ · vs open +5% (ignores score)
        Win chance 65% vs price 60% · good to −167
        Stake $20 to win $13
        ⚠️ live, low confidence · playoffs
        💡 Bovada pays more than Pinnacle's fair price

    Bovada's price leads (edge / stake at Bovada); the best other book is shown only when Bovada lacks it or is worse.
    `warnings` = short low-confidence reasons, merged into ONE tag.
    `paper` (ajbet/paper_conf.Assessment, Marvin 2026-10-03): once enough graded paper bets back it, the shown win
    chance is the paper-calibrated one, the label moves at most one step and a '📒 Paper ...' line is added."""
    from . import mybook
    from . import tgfmt as tf
    pshown = paper is not None and getattr(paper, "shown", False)
    if pshown and paper.p_cal is not None and win_p is not None:
        win_p = paper.p_cal
    if price is not None and mybook.is_mine(book_key) and my_price is None:
        my_price = price
    pick = tf.short_pick(label)
    mine = show_my_book and my_price is not None
    out = [header] if header else []
    out.append(tf.headline(kind, pick, price, book_key, my_price if mine else None))
    out.append(game)
    eo = tf.edge_open(edge_open, "vs open")
    if price is None and not mine:
        mp = max_price(win_p, ev_min)
        out.append(tf.join(f"Win chance {pct(win_p)}", f"take {am(mp)} or better" if mp is not None else ""))
    else:
        px, where = (my_price, esc(book(mybook.key()))) if mine else (price, esc(book(book_key)))
        ev = ev_at(win_p, px)
        st = stars(ev, live and not kind.startswith("💰 PROP"))   # Oct 11: live props show ⭐⭐ (not capped at 1)
        out.append(tf.join(f"Edge at {where} <b>{ev * 100:+.0f}%</b>{' ' + st if st else ''}", eo))
        mp = max_price(win_p, ev_min)
        good = ""
        if mp is not None:
            ok = american_to_decimal(px) >= american_to_decimal(mp) - 1e-9
            good = f"good to {am(mp)}" + ("" if ok else (" ⚠️ small edge" if ev > 0 else " ❌ too pricey"))
        elsewhere = ""
        if mine and price is not None and not mybook.is_mine(book_key) and \
                american_to_decimal(price) > american_to_decimal(my_price) + 1e-9:
            elsewhere = f"{esc(book(book_key))} has {am(price)}"
        out.append(tf.join(tf.win_vs_price(win_p, px), good, elsewhere))
        out.append(tf.stake_line(stake, px))
    if pshown:
        out.append(paper.line())
    reasons = [w for w in (warnings or []) if w]
    lab = paper.label if pshown else "low"
    if live:
        out.append(tf.low_conf(f"live, {lab} confidence", *reasons))
    elif reasons:
        out.append(tf.low_conf(f"{lab} confidence", *reasons))
    if why:
        out.append(f"💡 {esc(why)}")
    return "\n".join(x for x in out if x)


def short_line(sport: str, label: str, price: Optional[float], book_key: Optional[str], win_p: Optional[float],
               flag: str = "", my_price: Optional[float] = None, ev_min: float = 0.05,
               edge_cap: Optional[float] = None) -> str:
    """One-line compact version for lists (/game, /props, /edges, /recs):
    '🏀 <b>Valkyries ML</b> −150 @ Bovada · edge +8% ⭐' / '... −125 @ BetRivers · edge +6% · Bovada −140 ❌'."""
    from . import mybook
    from . import tgfmt as tf
    if mybook.is_mine(book_key):
        my_price = price
    lab = esc(tf.short_pick(label))
    if price is None or win_p is None:
        return f"{emoji(sport)} <b>{lab}</b>: no price"
    mine = mybook.is_mine(book_key)
    px = price
    s = f"{emoji(sport)} <b>{lab}</b> {am(px)} @ {esc(book(book_key))}"
    ev = ev_at(win_p, px)
    if edge_cap is not None and ev > edge_cap:     # Marvin 2026-10-03: absurd edge = stale quote, not value
        s += f" · edge &gt;{edge_cap * 100:.0f}% ⚠️ likely stale"
    else:
        st = stars(ev)
        s += f" · edge {ev * 100:+.0f}%{' ' + st if st else ''}{flag}"
    if not mine:
        name = esc(book(mybook.key()))
        if my_price is None:
            s += f" · not on {name}"
        else:
            mp = max_price(win_p, ev_min)
            good = mp is not None and american_to_decimal(my_price) >= american_to_decimal(mp) - 1e-9
            s += f" · {name} {am(my_price)} {'✅' if good else '❌'}"
    return s


# ------------------------------------------------------------------ value finder findings
def why_value(f, model: bool = False) -> str:
    b = book(f.book)
    if model:
        return "our team model rates this side higher than the market"
    m = str(getattr(f, "method", "") or "")
    if getattr(f, "player", None) or "team_totals" in (f.market or ""):
        return f"{b} is softer than the other books on this prop"
    if m.startswith("sharp"):
        return f"{b} pays more than Pinnacle's fair price"
    return f"{b} is softer than the other books"


def finding_card(f, g=None, ev_min: float = 0.05, stake: float = 100.0, unit: float = 100.0,
                 header: str = "", now: Optional[datetime] = None, edge_open: Optional[Dict[str, Any]] = None,
                 paper=None) -> str:
    from . import tgfmt as tf
    model = bool(getattr(f, "model_flag", False) and not getattr(f, "market_flag", False) and f.model_p)
    win_p = f.model_p if model else f.fair_p
    home = g.home.name if g is not None else f.home
    away = g.away.name if g is not None else f.away
    warn = []
    ex = getattr(f, "extra", {}) or {}
    if ex.get("low_confidence") == "playoffs" or (g is not None and getattr(g, "season_type", None) == 3):
        warn.append("playoffs")
    in_game = bool(ex.get("in_game"))
    label = pick_label(f.sport, f.market, f.selection, f.point, f.player, home, away)
    if g is not None and (in_game or g.state == "in"):
        gl = tf.score_line(f.sport, g, now)
    else:
        gl = tf.matchup_line(f.sport, away, home, f.commence, now)
    kind = "🧠 MODEL PICK" if model else ("💰 PROP VALUE" if (f.player or "team_totals" in (f.market or ""))
                                         else ("💰 LIVE VALUE" if in_game else "💰 VALUE"))
    txt = card(f.sport, label, f.price, f.book, win_p, ev_min, stake, unit, why_value(f, model), gl, warn, header,
               my_price=ex.get("my_price"), live=in_game, kind=kind, edge_open=edge_open, paper=paper)
    extra = [esc(ex["injury"]) if ex.get("injury") else "",
             f"🌬️ {esc(ex['context_warn'])}" if ex.get("context_warn") else "",
             f"🧭 {esc(ex['context'])}" if ex.get("context") else ""]
    extra = [tf.injury_bit(x) if x.startswith("🩺") else x for x in extra]
    return "\n".join([txt] + [x for x in extra if x])


# ------------------------------------------------------------------ live strategies (A / B)
def signal_card(sig, stake: Optional[float] = None, unit: float = 100.0, ev_min: float = 0.05, paper=None) -> str:
    from . import tgfmt as tf
    g = sig.game
    d = sig.details or {}
    sport = g.league
    gl = tf.score_line(sport, g)
    st = stake if stake is not None else (sig.stake or 100.0)
    if sig.strategy == "soccer_grind":
        label = f"UNDER {sig.line:g} total goals" if sig.line is not None else "UNDER total goals"
        why = f"{g.home.score + g.away.score} goals already, books hang a high alternate under"
        return card(sport, label, sig.price, sig.book, sig.fair_p, ev_min, st, unit, why, gl,
                    [] if g.state == "in" else ["live soccer model"], my_price=d.get("my_price"),
                    live=g.state == "in", kind="⚽ LATE UNDER", paper=paper)
    team = sig.selection
    down = abs(int(d.get("margin", 0) or 0))
    left = d.get("frac_left")
    state = f"down {down}" if down else "tied"
    when_txt = f", {left:.0%} of the game left" if isinstance(left, (int, float)) else ""
    why = f"big pregame favorite {state}{when_txt}; model says they come back {pct(sig.fair_p)}"
    warns = []
    if d.get("low_confidence") or "low confidence (playoffs)" in " ".join(d.get("notes") or []):
        warns.append("playoffs")
    if g.state != "in":
        warns.insert(0, "comeback model")
    return card(sport, f"{nick(team, sport)} to WIN", sig.price, sig.book, sig.fair_p, ev_min, st, unit, why, gl,
                warns, my_price=d.get("my_price"), live=g.state == "in", kind="🔄 COMEBACK", paper=paper)


TG_TAGS = ("b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a", "tg-spoiler",
           "blockquote", "span", "tg-emoji")


def tg_safe(html_text: str) -> str:
    """Make text safe for Telegram parse_mode=HTML: keep the supported tags, escape every other '<', '>'
    and bare '&' (e.g. '/props <player|team>' in the help text caused HTTP 400 'Unsupported start tag')."""
    import re
    if not html_text:
        return html_text
    tag_rx = re.compile(r"</?(%s)(\s[^<>]*)?>" % "|".join(re.escape(t) for t in TG_TAGS), re.I)
    out, pos = [], 0
    for m in tag_rx.finditer(html_text):
        out.append(_esc_frag(html_text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_esc_frag(html_text[pos:]))
    return "".join(out)


def _esc_frag(s: str) -> str:
    import re
    s = re.sub(r"&(?!(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)", "&amp;", s)
    return s.replace("<", "&lt;").replace(">", "&gt;")


def plain(html_text: str) -> str:
    """HTML card -> plain text (logs / dry-run console)."""
    import re
    return html.unescape(re.sub(r"<[^>]+>", "", html_text))
