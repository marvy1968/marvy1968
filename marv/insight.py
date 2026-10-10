"""Marv Predict insight: compare every metric Marv has on a game and turn it into ONE read.

Used by the March_edge H2H overlay (bridge.overlay) and Marv's game queries (bridge.game_h2h). Inputs, all
read from files Marv already writes (no network, so the 1.5 s overlay timeout is never at risk):
  * ratings-only H2H card (state/marv_predict/h2h_<sport>.json): ratings margin/total, O/D category points,
    star H2H tally (QB/RB/WR[/K]), O/U trend fade, total line
  * Marv engine projection (state/predictions.json): its own margin/total ("Marv model"), roster/injury notes
    (QB OUT, starters out, backup-QB injury trend, CFB QB change), O/U trend tags, line-move note
  * line history (state/lines.json): Bovada open -> latest spread and total
  * Bovada vs Pinnacle gaps (state/sharp_gap_log.json)
  * O/U trend tag log (state/ou_tags_log.json)
  * defense quality + recent defensive trend (marv/defense.py: points / yards allowed last 3 vs season, from the
    cached nflverse / CFBD box scores)
  * hybrid engine (marv/hybrid.py): trend-catcher modifier (last-3 turnover spike / ypp-margin drop), full-spectrum
    winner-take-all category matrix (off/def rating, net EPA, ypp, success, TO rate, explosive, ypp margin, pace) and
    its ATS side; totals get the restricted Monte Carlo median / p25 / p75. All Marv sports: football categories as
    above, basketball (NBA/WNBA/NCAAB/NCAAW/EuroLeague) off/def rating, net rating, eFG%, TS%, TOV rate, OREB%, reb
    margin, pace from the ESPN / EuroLeague box
Heavy-favourite gate: when the side is a heavy favourite (ML -200 or shorter, spread / Marv margin large) and ANY
metric is off (bad or fading defense, or any AGAINST factor), the read is capped at mixed, and at bad when the
defense is both bad and fading or a defense flag comes with another AGAINST factor -- even if ratings/spread favour it.
Each metric becomes a Factor that is FOR or AGAINST the bet (or the game's H2H pick), with a small weight.
The verdict is the weighted net: >= +2 good, <= -2 bad, otherwise mixed. None of this is a probability;
marv/proven.py still decides whether any % may print (only rules that passed the walk-forward bar).
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .data.teams import similarity

STAR_KEYS = {"nfl": "QB/RB/WR/K"}
GOOD_AT = 2  # weighted net needed for a good / bad call


@dataclass
class Factor:
    label: str
    side: int  # +1 for the bet, -1 against, 0 info only
    w: int = 1
    key: bool = False  # shown first in the why (heavy-favourite gate reason)


@dataclass
class Insight:
    factors: list = field(default_factory=list)
    signal: str | None = None  # proven-registry rule name (marv/proven.py), if one applies
    cap: str | None = None  # "not" / "mixed": heavy-favourite gate overrides a better weighted verdict

    @property
    def net(self) -> int:
        return sum(f.side * f.w for f in self.factors)

    @property
    def tally(self) -> tuple[int, int]:
        return (sum(f.w for f in self.factors if f.side > 0), sum(f.w for f in self.factors if f.side < 0))

    @property
    def verdict(self) -> str:  # good | not | mixed (bridge.Overlay codes)
        v = "good" if self.net >= GOOD_AT else "not" if self.net <= -GOOD_AT else "mixed"
        if self.cap == "not":
            return "not"
        if self.cap == "mixed" and v == "good":
            return "mixed"
        return v

    def why(self, n_for: int = 4, n_against: int = 3) -> str:
        pro = sorted((f for f in self.factors if f.side > 0), key=lambda f: (not f.key, -f.w))
        con = sorted((f for f in self.factors if f.side < 0), key=lambda f: (not f.key, -f.w))
        info = [f for f in self.factors if f.side == 0]
        parts = []
        if any(f.key for f in con):  # heavy-favourite gate: lead with the weak metric
            k = [f for f in con if f.key]
            con = [f for f in con if not f.key]
            parts.append(" + ".join(f.label for f in k))
        if pro:
            parts.append("for: " + ", ".join(f.label for f in pro[:n_for]))
        if con:
            parts.append("against: " + ", ".join(f.label for f in con[:n_against]))
        if info and len(parts) < 2:
            parts.append(", ".join(f.label for f in info[:2]))
        a, b = self.tally
        return "; ".join(parts) + f" ({a}-{b})" if parts else "no usable metrics"


# ---------------------------------------------------------------- data (all optional, never raises)
def _load(path: Path):
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except (ValueError, OSError):
        return None


def _match(a: str, b: str, x: str, y: str) -> bool:
    return min(max(similarity(a, x), similarity(a, y)), max(similarity(b, x), similarity(b, y))) >= 0.75


def lines(state_dir: Path, sport: str, gid: str | None, home: str, away: str) -> dict | None:
    data = _load(state_dir / "lines.json") or {}
    if gid and f"{sport}:{gid}" in data:
        return data[f"{sport}:{gid}"]
    for k, v in data.items():
        if k.startswith(f"{sport}:") and v.get("home") and _match(v["home"], v.get("away", ""), home, away):
            return v
    return None


def moves(state_dir: Path, sport: str, rec: dict, card: dict | None) -> dict:
    """{'spread': (open, now) home spread, 'total': (open, now)}: the engine's line-move note when present
    (what the cards show), else Bovada open -> latest from lines.json."""
    out = {}
    for n in rec.get("notes", []):
        if n.startswith("line move:"):
            m = re.search(r"spread .*? ([+-]?[\d.]+|PK) → ([+-]?[\d.]+|PK)", n)
            if m:
                out["spread"] = tuple(0.0 if x == "PK" else float(x) for x in m.groups())
            m = re.search(r"total ([\d.]+) → ([\d.]+)", n)
            if m:
                out["total"] = (float(m.group(1)), float(m.group(2)))
    ln = lines(state_dir, sport, rec.get("game_id") or (card or {}).get("game_id"), rec["home"], rec["away"])
    if ln:
        if "spread" not in out and ln.get("spread") is not None and ln.get("spread_last") is not None:
            out["spread"] = (ln["spread"], ln["spread_last"])
        if "total" not in out and ln.get("total") is not None and ln.get("total_last") is not None:
            out["total"] = (ln["total"], ln["total_last"])
        out["now"] = ln
    return out


def gaps(state_dir: Path, sport: str, home: str, away: str) -> list[dict]:
    data = _load(state_dir / "sharp_gap_log.json") or {}
    rows = list(data.values()) if isinstance(data, dict) else list(data)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    return [r for r in rows if r.get("sport") == sport and r.get("result") is None
            and str(r.get("start", "")) >= cutoff[:19] and _match(r.get("home", ""), r.get("away", ""), home, away)]


def ou_tags(state_dir: Path, sport: str, gid: str | None, notes: list[str]) -> list[tuple[str, str]]:
    """[(tag, side)] from the engine notes, else the O/U tag log."""
    out = []
    for n in notes:
        if n.startswith("O/U spots"):
            out += re.findall(r"([A-Z][A-Z+\-]+)→(Over|Under)", n)
    if not out and gid:
        data = _load(state_dir / "ou_tags_log.json") or {}
        rows = data.values() if isinstance(data, dict) else data
        out = [(r["tag"], r["side"]) for r in rows if str(r.get("game_id")) == str(gid) and r.get("sport") == sport]
    return out


def qb_issues(notes: list[str], home: str, away: str) -> dict[str, list[tuple[str, int]]]:
    """{'home'/'away': [(short label, weight)]} from roster / injury-trend notes."""
    out: dict[str, list[tuple[str, int]]] = {"home": [], "away": []}

    def who(text: str) -> str | None:
        hs, as_ = similarity(text, home), similarity(text, away)
        if max(hs, as_) < 0.5:
            hl, al = home.lower() in text.lower(), away.lower() in text.lower()
            return "home" if hl and not al else "away" if al and not hl else None
        return "home" if hs >= as_ else "away"

    for n in notes:
        if n.startswith("roster:"):
            for part in n[len("roster:"):].split(";"):
                part = part.strip()
                if "QB OUT" in part:
                    t = who(part.split(" starters out")[0].rsplit(" ", 1)[0])
                    if t:
                        out[t].append(("QB out", 2))
                elif "elimination" in part:
                    t = who(part.split(" starters out")[0].rsplit(" ", 1)[0])
                    if t:
                        out[t].append(("key starters out", 1))
                elif "QB change?" in part:
                    m = re.search(r"#?\d*\s*([\w.'\-]+) threw most last game, season leader .*?([\w.'\-]+)\s*(\(|$)", part)
                    if m and m.group(1).lower() == m.group(2).lower():
                        continue  # same passer, formation label differs: no change
                    t = who(part.split(":")[0])
                    if t:
                        out[t].append(("QB change?", 1))
        elif n.startswith("injury trend:") and "starting at QB" in n:
            t = who(n[len("injury trend:"):].split(" starting")[0].strip().rsplit(" ", 2)[0])
            if t and not any(lbl.startswith("QB") for lbl, _ in out[t]):
                out[t].append(("backup QB", 2))
        elif n.startswith("injury trend:") and " WR1 " in n:
            t = who(n[len("injury trend:"):].split(" WR1 ")[0].strip())
            if t:
                out[t].append(("WR1 out", 1))
    return out


# ---------------------------------------------------------------- factors
def _pts(m: float) -> str:
    return f"{abs(m):.1f}" if abs(m) < 3 else f"{abs(m):.0f}"


def side_insight(state_dir: Path, sport: str, rec: dict, card: dict | None, t: str, line: float | None = None,
                 price: float | None = None, live: bool = False, spread: bool = False) -> Insight:
    """Insight for one team (t = 'home' / 'away') on a moneyline (spread=False) or spread bet."""
    ins = Insight()
    home, away = rec["home"], rec["away"]
    name, opp = (home, away) if t == "home" else (away, home)
    sgn = 1 if t == "home" else -1
    margin = card["margin"] if card and card.get("margin") is not None else rec.get("model_margin") or 0.0
    src = "ratings" if card else "Marv model"
    m = sgn * margin
    use_line = spread and line is not None and not live
    short = lambda s: s.split()[-1] if len(s.split()) > 1 and sport == "nfl" else s  # noqa: E731
    if use_line:
        gap = m + line
        ins.factors.append(Factor(f"{src}: {short(name if m > 0 else opp)} by {_pts(m)} vs {short(name)} {line:+g} "
                                  f"({'covers' if gap > 0 else 'no cover'})", 1 if gap > 0 else -1, 3 if abs(gap) >= 3 else 1))
    else:
        ins.factors.append(Factor(f"{src}: {short(name if m > 0 else opp)} by {_pts(m)}", 1 if m > 0 else -1,
                                  2 if abs(m) >= 7 else 1))
    if card and rec.get("model_margin") is not None and rec.get("_engine"):
        m2 = sgn * rec["model_margin"]
        ok = (m2 + line > 0) if use_line else m2 > 0
        ins.factors.append(Factor(f"Marv model {'agrees' if ok else 'disagrees'} ({short(name if m2 > 0 else opp)} by {_pts(m2)})",
                                  1 if ok else -1))
    if card:
        mine = card.get("cat_home") if t == "home" else card.get("cat_away")
        if mine == 2:
            ins.factors.append(Factor("better O+D ratings 2-0", 1))
        elif mine == 0:
            ins.factors.append(Factor(f"{short(opp)} better O+D 0-2", -1))
        elif mine == 1:
            ins.factors.append(Factor("O/D split 1-1", 0))
        a, b = card.get("stars_home"), card.get("stars_away")
        if a is not None and b is not None:
            a, b = (a, b) if t == "home" else (b, a)
            ins.factors.append(Factor(f"stars {STAR_KEYS.get(sport, 'QB/RB/WR')} {a}-{b}", (a > b) - (a < b)))
    qb = qb_issues(rec.get("notes", []), home, away)
    for lbl, w in qb[t]:
        ins.factors.append(Factor(f"{short(name)} {lbl}", -1, w))
    for lbl, w in qb["away" if t == "home" else "home"]:
        ins.factors.append(Factor(f"{short(opp)} {lbl}", 1, w))
    if live:
        _hybrid(ins, state_dir, sport, rec, t, name, opp, short, None, False, True)
        _defense(ins, state_dir, sport, rec, t, name, opp, short, None, None, False, margin, live=True)
        return ins
    mv_ = moves(state_dir, sport, rec, card)
    ln = mv_.get("now")
    if "spread" in mv_:
        so, sn = mv_["spread"]
        if abs(sn - so) >= 0.5:  # home spread; moving down = toward home
            ok = (sn < so) == (t == "home")
            ins.factors.append(Factor(f"line moved {'toward' if ok else 'against'} {short(name)} "
                                      f"({sgn * so:+g}→{sgn * sn:+g})", 1 if ok else -1))
    if not spread:  # moneyline: who does the market favour?
        fav = None
        if price is not None:
            fav = price < 0
        elif ln and ln.get("spread_last") is not None and ln["spread_last"] != 0:
            fav = (ln["spread_last"] < 0) == (t == "home")
        if fav is not None:
            ins.factors.append(Factor(f"Bovada {'favours' if fav else 'has as dog'} {short(name)}", 1 if fav else -1))
    for g in gaps(state_dir, sport, home, away):
        if g.get("market") != "spread":
            continue
        ok = similarity(g.get("side", ""), name) >= similarity(g.get("side", ""), opp)
        ins.factors.append(Factor(f"Bovada vs Pinnacle {g['line']:+g}/{g['pinnacle_line']:+g} favours "
                                  f"{short(name if ok else opp)}", 1 if ok else -1))
    _hybrid(ins, state_dir, sport, rec, t, name, opp, short, line, spread, False, ln)
    _defense(ins, state_dir, sport, rec, t, name, opp, short, line, price, spread, margin, ln=ln)
    return ins


def hybrid_read(state_dir: Path, sport: str, rec: dict, ln: dict | None = None, home_spread: float | None = None,
                total: float | None = None) -> dict | None:
    """marv/hybrid.py read for this game with the latest Bovada spread / total (None when data is missing)."""
    from . import hybrid as HY
    if sport not in HY.SPORTS:
        return None
    try:
        ln = ln if ln is not None else lines(state_dir, sport, rec.get("game_id"), rec["home"], rec["away"])
        if home_spread is None and ln and ln.get("spread_last") is not None:
            home_spread = float(ln["spread_last"])
        if total is None and ln and ln.get("total_last") is not None:
            total = float(ln["total_last"])
        return HY.game(state_dir, sport, rec["home"], rec["away"], home_spread, total, bool(rec.get("neutral")))
    except Exception:  # noqa: BLE001 - never break the overlay
        return None


def _hybrid(ins: Insight, state_dir: Path, sport: str, rec: dict, t: str, name: str, opp: str, short, line,
            spread: bool, live: bool, ln: dict | None = None) -> None:
    """Hybrid engine factors: category matrix side, trend-catcher modifiers (both teams), ATS side on spread bets."""
    from . import hybrid as HY
    hs = None
    if spread and line is not None and not live:
        hs = float(line) if t == "home" else -float(line)
    r = hybrid_read(state_dir, sport, rec, ln, hs)
    if not r:
        return
    o = "away" if t == "home" else "home"
    mine, theirs = r[f"{t}_pts"], r[f"{o}_pts"]
    if mine != theirs:
        ok = mine > theirs
        lbl = (f"hybrid categories {mine:g}-{theirs:g}" if ok else f"{short(opp)} wins hybrid categories {theirs:g}-{mine:g}")
        top = [HY.label(sport, c) for c in r["won"][t if ok else o][:3]]
        ins.factors.append(Factor(lbl + (f" ({', '.join(top)})" if top else ""), 1 if ok else -1,
                                  2 if abs(mine - theirs) >= 3 else 1))
    for who, nm, sd in ((t, name, -1), (o, opp, 1)):
        mod = r[f"mod_{who}"]
        if mod <= HY.WEAK_MOD:
            why = "; ".join(r[f"why_{who}"]) or "last 3 down"
            ins.factors.append(Factor(f"{short(nm)} trend catcher x{mod:.2f} ({why})", sd))
    if spread and not live and r.get("ats"):
        ok = r["ats"] == t
        ins.factors.append(Factor(f"hybrid ATS {'backs' if ok else 'fades'} {short(name)} (proj {short(rec['home'])} "
                                  f"{r['proj_margin']:+.1f} vs {r['spread']:+g})", 1 if ok else -1))


def heavy_fav(state_dir: Path, sport: str, rec: dict, t: str, line: float | None, price: float | None, spread: bool,
              margin: float, ln: dict | None = None, live: bool = False) -> str | None:
    """Why team t counts as a heavy favourite ('ML -250', 'spread -9.5', 'Marv by 11'), else None.
    Pregame Bovada spread (lines.json) first; the bet's own line/price only pregame (live prices move in-game)."""
    from . import defense as DF
    sgn = 1 if t == "home" else -1
    ln = ln or lines(state_dir, sport, rec.get("game_id"), rec["home"], rec["away"])
    if not live and price is not None and price <= DF.HEAVY_ML:
        return f"ML {price:+g}"
    if not live and spread and line is not None and -line >= DF.HEAVY.get(sport, 99):
        return f"spread {line:+g}"
    if ln and ln.get("spread_last") is not None and -sgn * ln["spread_last"] >= DF.HEAVY.get(sport, 99):
        return f"spread {sgn * ln['spread_last']:+g}"
    if sgn * (margin or 0) >= DF.HEAVY_MARGIN.get(sport, 99):
        return f"Marv by {sgn * margin:.0f}"
    return None


def _defense(ins: Insight, state_dir: Path, sport: str, rec: dict, t: str, name: str, opp: str, short, line, price,
             spread: bool, margin: float, ln: dict | None = None, live: bool = False) -> None:
    """Defense quality / trend factors + the heavy-favourite gate (never raises)."""
    from . import defense as DF
    try:
        mine, theirs = DF.profile(state_dir, sport, name), DF.profile(state_dir, sport, opp)
        fav = heavy_fav(state_dir, sport, rec, t, line, price, spread, margin, ln, live)
        opp_fav = heavy_fav(state_dir, sport, rec, "away" if t == "home" else "home", None, None, False, margin, ln, True)
    except Exception:  # noqa: BLE001 - the overlay must never fail on this
        return
    flags = 0
    if mine and (mine.get("bad") or mine.get("fading")):
        flags = int(bool(mine["bad"])) + int(bool(mine["fading"]))
        ins.factors.append(Factor(DF.describe(mine, short(name)) + (" — fade the favorite" if fav else ""), -1,
                                  2 if flags == 2 else 1, key=bool(fav)))
    if theirs and (theirs.get("bad") or theirs.get("fading")):
        ins.factors.append(Factor(DF.describe(theirs, short(opp)) + (" — fade the favorite" if opp_fav else ""), 1,
                                  2 if opp_fav else 1))
    if not fav:
        return
    off = [f for f in ins.factors if f.side < 0 and not f.key]
    if flags == 0 and not off:
        return
    if flags == 0:  # heavy favourite with some other metric off: name it first, never a clean good
        for f in off:
            f.key = True
        off[0].label = f"heavy fav ({fav}) but {off[0].label}"
        ins.cap = "mixed" if len(off) == 1 else "not" if sum(f.w for f in off) >= 3 else "mixed"
    else:
        ins.cap = "not" if flags == 2 or off else "mixed"


def total_insight(state_dir: Path, sport: str, rec: dict, card: dict | None, over: bool, line: float) -> Insight:
    ins = Insight()
    home, away = rec["home"], rec["away"]
    total = card["rating_total"] if card and card.get("rating_total") is not None else rec.get("model_total") or 0.0
    src = "ratings" if card else "Marv model"
    d = 1 if over else -1
    if total:
        ins.factors.append(Factor(f"{src} total {total:.1f} vs {line:g}", d if total > line else -d,
                                  2 if abs(total - line) >= 4 else 1))
    if card and rec.get("_engine") and rec.get("model_total"):
        t2 = rec["model_total"]
        ins.factors.append(Factor(f"Marv model total {t2:.1f}", d if t2 > line else -d))
    tags = ou_tags(state_dir, sport, rec.get("game_id") or (card or {}).get("game_id"), rec.get("notes", []))
    fade = (card or {}).get("fade") or ""
    if not fade and tags:
        fade = tags[0][1].upper()
    if fade:
        agree = (fade == "OVER") == over
        extra = [t for t, _ in tags if "+" in t]
        ins.factors.append(Factor(f"O/U trend fade → {fade}" + (f" ({', '.join(extra)})" if extra else ""),
                                  1 if agree else -1))
        if agree and fade == "UNDER":
            ins.signal = "OVER-FADE"
    mv_ = moves(state_dir, sport, rec, card)
    if "total" in mv_ and abs(mv_["total"][1] - mv_["total"][0]) >= 0.5:
        to, tn = mv_["total"]
        ins.factors.append(Factor(f"total moved {to:g}→{tn:g}", d if tn > to else -d))
    for g in gaps(state_dir, sport, home, away):
        if g.get("market") != "total":
            continue
        ok = g.get("side", "").lower().startswith("o") == over
        ins.factors.append(Factor(f"Bovada {g['line']:g} vs Pinnacle {g['pinnacle_line']:g} favours {g.get('side')}",
                                  1 if ok else -1))
        if ok and abs(float(g["line"]) - float(line)) < 0.01 and ins.signal is None:
            ins.signal = "GAP"
    qb = qb_issues(rec.get("notes", []), home, away)
    if any(w >= 2 for lbl, w in qb["home"] + qb["away"]):
        ins.factors.append(Factor("QB out → lean Under", -d))
    r = hybrid_read(state_dir, sport, rec, total=float(line))
    if r:  # restricted Monte Carlo (pace + scoring variance only): median vs the line; inside +/-3 is info only
        mc = r["mc"]
        gap = mc["median"] - float(line)
        ins.factors.append(Factor(f"hybrid MC total {mc['median']:.0f} (p25 {mc['p25']:.0f}–p75 {mc['p75']:.0f})",
                                  (d if gap > 0 else -d) if abs(gap) >= 3 else 0))
    return ins


def best_total_line(state_dir: Path, sport: str, rec: dict, card: dict | None) -> float | None:
    ln = lines(state_dir, sport, rec.get("game_id") or (card or {}).get("game_id"), rec["home"], rec["away"])
    if ln and ln.get("total_last") is not None:
        return float(ln["total_last"])
    if card and card.get("total_line"):
        return float(card["total_line"])
    return None
