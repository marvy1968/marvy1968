"""Daily pregame bet card: the best 2-10 bets across every sport, ranked, each with a one-line reason.

Every candidate comes from something Marv already computes, in this order of trust:
  1. HIGH CONFIDENCE  picks that passed the whole veto stack (backtested confidence floors, injuries,
                      eliminations); stake from quarter Kelly, capped.
  2. LEAN (signal)    paper-tracked signals with a positive backtest: college O/U trend fades, the NFL
                      inflated-total under, Bovado totals off Pinnacle, MARV4 spreads.
  3. LEAN (model)     Marv's own edge (3%+) on a market that failed only a "soft" rule (confidence floor,
                      unproven market), never one blocked for injuries, a backup QB, missing starters,
                      weather or stale data.
Leans are 0.5 unit. At least MIN_BETS make the card when that many candidates exist (the card says when
there are fewer), at most MAX_BETS, and at most 2 per game. Every card is logged to
state/betcard_log.json and graded from final scores, so its real record shows up next to the others.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import edges as E
from .data.teams import similarity

MIN_BETS, MAX_BETS, PER_GAME, MAX_UNTESTED = 2, 10, 2, 2
HARD = ("backup QB", "key injury", "starters out", "snaps", "elimination", "wind", "no current injury",
        "games of stats", "stale", "unconfirmed", "trap line", "gap:", "missing")
SIGNAL_RANK = {"OVER-FADE+MOVE": 56.7, "OVER-FADE+INFLATED": 56.5, "TOTAL-INFLATED": 55.8, "OVER-FADE": 54.9,
               "GAP": 54.4, "MARV4": 54.4, "UNDER-FADE": 52.5}


@dataclass
class Bet:
    sport: str
    game: str
    start: str
    home: str
    away: str
    market: str  # ml | spread | total
    side: str
    line: float | None
    price: float
    tier: str  # HIGH | SIGNAL | MODEL
    score: float  # ranking key within the tier
    units: float
    why: str
    result: str | None = None

    @property
    def label(self) -> str:
        if self.market == "ml":
            return f"{self.side} ML ({self.price:+.0f})"
        if self.market == "spread":
            return f"{self.side} {self.line:+g} ({self.price:+.0f})"
        return f"{self.side.upper()} {self.line:g} ({self.price:+.0f})"


def _hard(vetoes: list[str]) -> bool:
    return any(h in v for v in vetoes for h in HARD)


def _game_blocked(pred) -> bool:
    return any(_hard(pk.vetoes) and any(w in " ".join(pk.vetoes) for w in ("backup QB", "key injury", "stale"))
               for pk in pred.picks)


def candidates(sport: str, preds, projections: dict | None = None) -> list[Bet]:
    out = []
    for pred in preds:
        g = pred.game
        o = g.odds
        name = f"{g.away} @ {g.home}"
        base = dict(sport=sport, game=name, start=g.start.isoformat(), home=g.home, away=g.away)
        blocked = _game_blocked(pred)
        for pk in pred.picks:
            if pk.market not in ("ml", "spread", "total"):
                continue
            if pk.active:
                b = (pk.price / 100) if pk.price > 0 else 100 / -pk.price
                kelly = max((b * pk.prob - (1 - pk.prob)) / b, 0) / 4
                out.append(Bet(**base, market=pk.market, side=pk.side, line=pk.line, price=pk.price, tier="HIGH",
                               score=pk.edge, units=round(min(max(kelly * 100, 0.5), 3.0), 1),
                               why=f"Marv {pk.prob:.0%} vs price {E.implied(pk.price):.0%}: edge {pk.edge:+.1%}, passed every check"))
            elif not blocked and not _hard(pk.vetoes) and pk.edge >= 0.03:
                out.append(Bet(**base, market=pk.market, side=pk.side, line=pk.line, price=pk.price, tier="MODEL",
                               score=pk.edge, units=0.5,
                               why=f"Marv {pk.prob:.0%} vs price {E.implied(pk.price):.0%} (edge {pk.edge:+.1%}); "
                                   f"held back by: {'; '.join(pk.vetoes)[:90]}"))
        if blocked or not o:
            continue
        proj = (projections or {}).get(g.id)
        for tag, side in getattr(proj, "ou_tags", []) or []:
            if o.total is None:
                continue
            price = o.under_price if side == "Under" else o.over_price
            out.append(Bet(**base, market="total", side=side, line=o.total, price=price or -110, tier="SIGNAL",
                           score=SIGNAL_RANK.get(tag, 52.5), units=0.5,
                           why=f"{tag}: teams' recent over/under trend overshot by the market "
                               f"({SIGNAL_RANK.get(tag, 52.5):.1f}% in backtests)"))
        for note in pred.notes:
            if note.startswith("spots (tracked") and "MARV4" in note and o.spread is not None:
                team = note.split("MARV4→")[1].split(" [")[0]
                line = o.spread if team == g.home else -o.spread
                price = o.home_spread_price if team == g.home else o.away_spread_price
                out.append(Bet(**base, market="spread", side=team, line=line, price=price or -110, tier="SIGNAL",
                               score=SIGNAL_RANK["MARV4"], units=0.5,
                               why=f"Marv {abs(pred.model_margin + o.spread):.1f} pts off the spread (MARV4, 54.4% ATS backtest)"))
    return out


def gap_candidates(gaps: list[dict]) -> list[Bet]:
    """Bovado vs Pinnacle total gaps (college backtested; NFL untested, ranked lower)."""
    out = []
    for e in gaps:
        if e["market"] != "total":
            continue
        tested = e["sport"] == "cfb"
        out.append(Bet(sport=e["sport"], game=f"{e['away']} @ {e['home']}", start=e["start"], home=e["home"],
                       away=e["away"], market="total", side=e["side"], line=e["line"], price=e["price"], tier="SIGNAL",
                       score=SIGNAL_RANK["GAP"] if tested else 52.0, units=0.5,
                       why=f"Bovado {e['line']:g} vs sharp Pinnacle {e['pinnacle_line']:g}"
                           + (" (54.4%, +4.6% ROI backtest)" if tested else " (untested in NFL)")))
    return out


def select(cands: list[Bet], n_max: int = MAX_BETS) -> list[Bet]:
    """Rank (HIGH, then SIGNAL, then MODEL), merge duplicates of the same bet, max PER_GAME per game."""
    order = {"HIGH": 0, "SIGNAL": 1, "MODEL": 2}
    merged: dict[tuple, Bet] = {}
    for b in sorted(cands, key=lambda b: (order[b.tier], -b.score)):
        key = (b.sport, b.game, b.market, b.side)
        if key in merged:
            first = merged[key]
            if b.why not in first.why and len(first.why) < 200:
                first.why += f" + {b.why.split(':')[0] if b.tier == 'SIGNAL' else 'model agrees'}"
            continue
        merged[key] = b
    out, per_game, untested = [], {}, 0
    for b in merged.values():
        if per_game.get(b.game, 0) >= PER_GAME:
            continue
        if "untested" in b.why:  # NFL Bovado-vs-Pinnacle gaps have no backtest: at most 2 per card
            if untested >= MAX_UNTESTED:
                continue
            untested += 1
        per_game[b.game] = per_game.get(b.game, 0) + 1
        out.append(b)
        if len(out) >= n_max:
            break
    return out


def text(bets: list[Bet], when: datetime) -> str:
    if not bets:
        return "👽 MARV PREDICT MAX — BET CARD\nNo bets today: nothing has an edge worth a stake."
    tiers = {"HIGH": "🔥 HIGH CONFIDENCE", "SIGNAL": "📈 LEAN (backtested signal)", "MODEL": "🧮 LEAN (model edge)"}
    lines = [f"👽 <b>MARV PREDICT MAX — BET CARD</b> · {when:%a %b %d}", ""]
    for i, b in enumerate(bets, 1):
        t = datetime.fromisoformat(b.start.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Chicago"))
        lines.append(f"{i}) <b>{b.label}</b> · {b.units:g}u · {tiers[b.tier]}")
        lines.append(f"   {b.sport.upper()} {b.game} · {t:%a %-I:%M %p} CT")
        lines.append(f"   {b.why}")
    risk = sum(b.units for b in bets)
    lines += ["", f"Total risk: {risk:g} units"]
    if len(bets) < MIN_BETS:
        lines.append(f"Only {len(bets)} bet{'s' if len(bets) != 1 else ''} had any edge today.")
    if not any(b.tier == "HIGH" for b in bets):
        lines.append("No high-confidence plays today: everything here is a lean, keep stakes small.")
    lines.append("<i>Paper-tracked and graded. Model output, not a guarantee.</i>")
    return "\n".join(lines)


def log(state: Path, bets: list[Bet], when: datetime) -> None:
    path = state / "betcard_log.json"
    book = json.loads(path.read_text()) if path.exists() else []
    seen = {(b["sport"], b["game"], b["market"], b["side"], b["start"]) for b in book}
    for b in bets:
        if (b.sport, b.game, b.market, b.side, b.start) not in seen:
            book.append({**asdict(b), "card": when.date().isoformat()})
    path.write_text(json.dumps(book, indent=1))


def grade(state: Path, sport: str, finished) -> None:
    path = state / "betcard_log.json"
    if not path.exists():
        return
    book = json.loads(path.read_text())
    done = [g for g in finished if g.completed and g.home_score is not None and g.away_score is not None]
    for b in book:
        if b["sport"] != sport or b["result"] is not None:
            continue
        start = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
        g = next((x for x in done if abs(x.start - start) < timedelta(hours=12)
                  and min(similarity(x.home, b["home"]), similarity(x.away, b["away"])) >= 0.75), None)
        if not g:
            continue
        hs, as_ = g.home_score, g.away_score
        if b["market"] == "total":
            v = (hs + as_ - b["line"]) * (1 if b["side"] == "Over" else -1)
        elif b["market"] == "spread":
            v = (hs - as_ + b["line"]) if b["side"] == b["home"] else (as_ - hs + b["line"])
        else:
            v = (hs - as_) if b["side"] == b["home"] else (as_ - hs)
        b["result"] = "push" if v == 0 else ("win" if v > 0 else "loss")
        b["profit"] = 0.0 if v == 0 else (b["units"] * E.payout(b["price"]) if v > 0 else -b["units"])
    path.write_text(json.dumps(book, indent=1))


def record(state: Path) -> str:
    path = state / "betcard_log.json"
    book = json.loads(path.read_text()) if path.exists() else []
    lines = []
    for tier in ("HIGH", "SIGNAL", "MODEL"):
        g = [b for b in book if b["tier"] == tier and b["result"] in ("win", "loss")]
        if g:
            w = sum(b["result"] == "win" for b in g)
            lines.append(f"{tier}: {w}-{len(g) - w} ({w / len(g):.0%}), {sum(b['profit'] for b in g):+.1f}u")
    return "🃏 Bet card record: " + (" · ".join(lines) if lines else "nothing graded yet")
