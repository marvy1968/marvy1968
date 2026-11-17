"""Strategy C math: no-vig consensus fair prices, EV, Kelly, prop pairing and outliers.

Honesty rules:
* 'market' value = a book's price beats the no-vig fair price built from OTHER books (sharp books
  first). It is line shopping, not a prediction.
* 'model' value = an independent model (SRS ratings) disagrees with the price. Reported separately.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import norm_cdf, norm_ppf, poisson_cdf, poisson_lambda_for, poisson_pmf
from . import mybook
from .oddsmath import american_to_decimal, american_to_prob, devig_american, prob_to_american


# ------------------------------------------------------------------ calibration (2026-10-23, see calib.py)
import threading as _th
_CAL = _th.local()


def cal_on(fn):
    """Wrap a findings call so its fair_p is recalibrated + capped 65% (value_scan alert paths only; CLV untouched)."""
    def w(*a, **k):
        prev = getattr(_CAL, "on", False); _CAL.on = True
        try:
            return fn(*a, **k)
        finally:
            _CAL.on = prev
    return w


def _cal_hook(f, kind):
    if getattr(_CAL, "on", False):
        try:
            from . import calib; calib.adjust_finding(f, kind)
        except Exception:
            pass


def _cal_model_hook(f):
    if getattr(_CAL, "on", False):
        try:
            from . import calib; calib.cap_model(f)
        except Exception:
            pass


# ------------------------------------------------------------------ basics

def ev_pct(p_win: float, price: float, p_lose: Optional[float] = None) -> float:
    """Expected return per $1 staked. p_lose defaults to 1 - p_win (no push)."""
    if p_lose is None:
        p_lose = 1.0 - p_win
    return p_win * (american_to_decimal(price) - 1.0) - p_lose


def kelly_fraction(p: float, price: float) -> float:
    b = american_to_decimal(price) - 1.0
    return max(0.0, (b * p - (1.0 - p)) / b)


def kelly_stake(p: float, price: float, bankroll: float, fraction: float = 0.25,
                cap_pct: float = 0.02, max_stake: Optional[float] = None) -> float:
    s = bankroll * fraction * kelly_fraction(p, price)
    s = min(s, bankroll * cap_pct)
    if max_stake is not None:
        s = min(s, max_stake)
    return round(s, 2)


def fair_american(p: float) -> float:
    return prob_to_american(min(max(p, 1e-6), 1 - 1e-6))


@dataclass
class Finding:
    sport: str
    event_id: str
    home: str
    away: str
    commence: str
    market: str
    selection: str            # outcome name (team / Over / Under / Yes / No)
    point: Optional[float]
    book: str
    price: float
    fair_p: float
    ev: float
    method: str
    n_books: int
    player: Optional[str] = None
    model_p: Optional[float] = None
    model_ev: Optional[float] = None
    market_flag: bool = False
    model_flag: bool = False
    p_lose: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def tag(self) -> str:
        if self.market_flag and self.model_flag:
            return "market+model"
        return "model" if self.model_flag else "market"

    @property
    def dedupe_key(self) -> str:
        pt = "" if self.point is None else f"{self.point:g}"
        who = self.player or ""
        return f"{self.event_id}:value:{self.market}:{who}:{self.selection}:{pt}"

    @property
    def fair_price(self) -> float:
        return fair_american(self.fair_p)


# ------------------------------------------------------------------ featured markets

def _default_registry():
    from .registry import Registry, BUILTIN_MARKETS, MarketHandler
    global _REG
    try:
        return _REG
    except NameError:
        r = Registry()
        for m in BUILTIN_MARKETS:
            r.register_market(MarketHandler(**m))
        _REG = r
        return r


def _pairs(event: Dict[str, Any], market: str, pairing: Optional[str] = None) -> Dict[Tuple, Dict[str, Dict[str, float]]]:
    """{line_key: {book: {outcome_name: price}}} for two-way (or 3-way h2h) markets.

    line_key: () for h2h, (home_point,) for spreads, (total,) for totals."""
    home = event.get("home_team")
    out: Dict[Tuple, Dict[str, Dict[str, float]]] = defaultdict(dict)
    for bk in event.get("bookmakers", []) or []:
        book = bk.get("key") or bk.get("title")
        for m in bk.get("markets", []) or []:
            if m.get("key") != market:
                continue
            outs = m.get("outcomes", []) or []
            kind = pairing or {"h2h": "h2h", "spreads": "spread", "totals": "total"}.get(market, "total")
            if kind == "h2h":
                out[()][book] = {o["name"]: float(o["price"]) for o in outs}
            elif kind == "spread" and market.startswith("alternate"):
                for o in outs:   # alternate lines: pair home point p with away point -p
                    if o.get("name") == home and o.get("point") is not None:
                        aw = next((x for x in outs if x.get("name") != home and x.get("point") == -o["point"]), None)
                        if aw:
                            out[(float(o["point"]),)][book] = {home: float(o["price"]), aw["name"]: float(aw["price"])}
            elif kind == "total" and market.startswith("alternate"):
                by_pt: Dict[float, Dict[str, float]] = defaultdict(dict)
                for o in outs:
                    if o.get("point") is not None:
                        by_pt[float(o["point"])][o["name"]] = float(o["price"])
                for pt, d in by_pt.items():
                    if len(d) == 2:
                        out[(pt,)][book] = d
            elif kind == "spread":
                hp = next((o.get("point") for o in outs if o.get("name") == home), None)
                if hp is not None:
                    out[(float(hp),)][book] = {o["name"]: float(o["price"]) for o in outs}
            elif kind == "total":
                pts = {o.get("point") for o in outs}
                if len(pts) == 1 and None not in pts:
                    out[(float(pts.pop()),)][book] = {o["name"]: float(o["price"]) for o in outs}
    return out


def _devig_book(prices: Dict[str, float], method: str = "power") -> Optional[Dict[str, float]]:
    if len(prices) < 2:
        return None
    names = list(prices)
    try:
        return dict(zip(names, devig_american([prices[n] for n in names], method)))
    except ValueError:
        return None


def consensus_fair(by_book: Dict[str, Dict[str, float]], exclude: Optional[str],
                   sharp_books: Iterable[str], min_books: int,
                   method: str = "power") -> Optional[Tuple[Dict[str, float], str, int]]:
    """No-vig fair probabilities for one line. Sharp books (e.g. Pinnacle) win if present;
    otherwise the mean of the other books' de-vigged probabilities (leave-one-out)."""
    sharp = [b for b in sharp_books if b in by_book and b != exclude]
    pool = sharp if sharp else [b for b in by_book if b != exclude]
    dv = [d for d in (_devig_book(by_book[b], method) for b in pool) if d]
    names = set.intersection(*(set(d) for d in dv)) if dv else set()
    if not dv or not names:
        return None
    if not sharp and len(dv) < max(1, min_books - 1):
        return None
    fair = {n: statistics.mean(d[n] for d in dv) for n in names}
    s = sum(fair.values())
    fair = {n: v / s for n, v in fair.items()}
    method = f"sharp:{'+'.join(sharp)}" if sharp else f"consensus({len(dv)} books)"
    return fair, method, len(dv)


def featured_findings(event: Dict[str, Any], sport: str, cfg: Dict[str, Any],
                      model_margin_home: Optional[float] = None, sigma: Optional[float] = None,
                      model_ok: bool = False, registry=None, exclude_books: Iterable[str] = ()) -> List[Finding]:
    """Best price per (market, line, selection) with market EV and (optional) model EV.

    exclude_books (Marvin 2026-10-03, ajbet/live_quality.stale_books): stale/lagging live books are left out of the
    consensus and never become the best price; my book's own price is still attached (extra['my_stale'] = True).

    Markets come from the registry: every market key present in the event whose handler pairing
    is h2h / spread / total (featured, period, alternates, and any config-added game line)."""
    sharp = cfg.get("sharp_books", ["pinnacle"])
    min_books = cfg.get("min_books", 3)
    reg = registry or _default_registry()
    out: List[Finding] = []
    home, away = event.get("home_team"), event.get("away_team")
    present: List[str] = []
    for bk in event.get("bookmakers", []) or []:
        for m in bk.get("markets", []) or []:
            if m.get("key") not in present:
                present.append(m.get("key"))
    for market in present:
        h = reg.market(market, sport)
        if h is None or h.pairing not in ("h2h", "spread", "total"):
            continue
        thr = h.ev_min if h.ev_min is not None else cfg.get("market_ev_min", 0.03)
        excl = set(exclude_books or ())
        for key, by_book_all in _pairs(event, market, h.pairing).items():
            best: Dict[str, Finding] = {}
            mine: Dict[str, float] = {}
            my_stale = False
            by_book = {b: p for b, p in by_book_all.items() if b not in excl}
            for book, prices in by_book_all.items():
                if book in excl and mybook.is_mine(book):
                    mine.update(prices)
                    my_stale = True
            for book, prices in by_book.items():
                cf = consensus_fair(by_book, book, sharp, min_books, h.devig or cfg.get("devig_method", "power"))
                if not cf:
                    continue
                fair, method, n = cf
                for name, price in prices.items():
                    if name not in fair:
                        continue
                    point = None
                    if h.pairing == "spread":
                        point = key[0] if name == home else -key[0]
                    elif h.pairing == "total":
                        point = key[0]
                    f = Finding(sport, event.get("id"), home, away, event.get("commence_time", ""), market,
                                name, point, book, price, fair[name], ev_pct(fair[name], price), method, n + 1)
                    if mybook.is_mine(book):
                        mine[name] = price
                    cur = best.get(name)
                    if cur is None or mybook.better_or_tie(price, book, cur.price, cur.book):
                        best[name] = f
            for f in best.values():
                f.extra["my_book"] = mybook.key()
                f.extra["my_price"] = mine.get(f.selection)
                if my_stale:
                    f.extra["my_stale"] = True
                _cal_hook(f, "game")
                f.market_flag = f.ev >= thr
                f.extra["family"] = h.family
                if model_margin_home is not None and sigma and market in ("h2h", "spreads"):
                    m = model_margin_home if f.selection == home else -model_margin_home
                    f.model_p = norm_cdf(m / sigma) if market == "h2h" else norm_cdf((m + f.point) / sigma)
                    f.model_ev = ev_pct(f.model_p, f.price)
                    _cal_model_hook(f)
                    f.extra["model_margin_home"] = model_margin_home
                    gap = abs(f.model_p - f.fair_p)
                    big = gap > cfg.get("model_max_disagreement", 0.15)
                    alerts_on = bool(cfg.get("model_alerts", False))
                    f.model_flag = alerts_on and model_ok and not big and f.model_ev >= cfg.get("model_ev_min", 0.05)
                    if not alerts_on:
                        f.extra["model_note"] = "info only: team-model alerts off (backtest: no edge)"
                    elif not model_ok:
                        f.extra["model_note"] = "too few results per team"
                    elif big:
                        f.extra["model_note"] = f"model vs market gap {gap * 100:.0f} pp > cap (likely model error)"
                out.append(f)
    return out


# ------------------------------------------------------------------ props

POISSON_MARKETS = {"player_receptions", "player_threes", "player_assists", "player_rebounds",
                   "player_pass_tds", "player_rush_attempts", "player_pass_completions",
                   "player_pass_interceptions", "player_rush_tds", "player_reception_tds",
                   "player_blocks", "player_steals", "player_turnovers"}
YES_NO_MARKETS = {"player_anytime_td", "player_1st_td", "player_last_td", "player_double_double",
                  "player_triple_double", "player_first_basket"}


@dataclass
class PropQuote:
    book: str
    market: str
    player: str
    point: Optional[float]
    over: Optional[float]      # price for Over (or Yes)
    under: Optional[float]     # price for Under (or No)

    @property
    def p_over(self) -> Optional[float]:
        if self.over is None or self.under is None:
            return None
        return devig_american([self.over, self.under])[0]


def pair_props(event: Dict[str, Any], markets: Optional[Iterable[str]] = None) -> Dict[Tuple[str, str], List[PropQuote]]:
    """Group per-event prop outcomes into Over/Under (Yes/No) pairs per book.

    Returns {(market, player): [PropQuote per book/line]}. Unpaired sides keep None."""
    want = set(markets) if markets else None
    acc: Dict[Tuple[str, str, str, Optional[float]], PropQuote] = {}
    for bk in event.get("bookmakers", []) or []:
        book = bk.get("key") or bk.get("title")
        for m in bk.get("markets", []) or []:
            mk = m.get("key", "")
            if want and mk not in want:
                continue
            for o in m.get("outcomes", []) or []:
                player = o.get("description") or o.get("participant")
                if not player:
                    continue
                pt = o.get("point")
                pt = float(pt) if pt is not None else None
                k = (book, mk, player, pt)
                q = acc.setdefault(k, PropQuote(book, mk, player, pt, None, None))
                side = (o.get("name") or "").lower()
                if side in ("over", "yes"):
                    q.over = float(o["price"])
                elif side in ("under", "no"):
                    q.under = float(o["price"])
    out: Dict[Tuple[str, str], List[PropQuote]] = defaultdict(list)
    for q in acc.values():
        out[(q.market, q.player)].append(q)
    return out


def _probs_at(line: float, loc: float, market: str, sd: float) -> Tuple[float, float]:
    """(p_over, p_under) at `line` for a distribution with location `loc` (mean or lambda)."""
    if market in POISSON_MARKETS:
        k = math.floor(line)
        if float(line).is_integer():
            push = poisson_pmf(int(line), loc)
            p_under = poisson_cdf(int(line) - 1, loc)
            return 1 - p_under - push, p_under
        p_under = poisson_cdf(k, loc)
        return 1 - p_under, p_under
    p_over = 1 - norm_cdf((line - loc) / sd)
    return p_over, 1 - p_over


def _loc_from(q: PropQuote, market: str, sd: float) -> Optional[float]:
    p = q.p_over
    if p is None or q.point is None or not 0.01 < p < 0.99:
        return None
    if market in POISSON_MARKETS:
        return poisson_lambda_for(q.point, p)
    return q.point + sd * norm_ppf(p)


def prop_findings(event: Dict[str, Any], sport: str, cfg: Dict[str, Any],
                  markets: Optional[Iterable[str]] = None, registry=None) -> List[Finding]:
    """Leave-one-out consensus per (market, player); each book's Over/Under priced vs that fair.

    Over/Under markets: every book's de-vigged pair implies a distribution location (normal mean
    with a per-market SD, or a Poisson rate for counts); the consensus location is the median of
    the OTHER books, which also prices books hanging a different line (line outliers).
    Yes/No markets: mean de-vigged P(yes) of other books; one-sided books are skipped."""
    sds = dict(cfg.get("prop_sd", {}))
    sds.update((cfg.get("prop_sd_by_sport") or {}).get(sport) or {})
    min_books = cfg.get("prop_min_books", 3)
    reg = registry or _default_registry()
    out: List[Finding] = []
    for (market, player), quotes in pair_props(event, markets).items():
        books = {q.book for q in quotes}
        if len(books) < min_books:
            continue
        h = reg.market(market, sport)
        if h is None or h.pairing not in ("participant_ou", "participant_yes_no"):
            continue
        fam_thr = cfg.get("team_prop_ev_min", cfg.get("prop_ev_min", 0.05)) if h.family == "team_props" \
            else cfg.get("prop_ev_min", 0.05)
        thr = h.ev_min if h.ev_min is not None else fam_thr
        sd = float(h.sd if h.sd is not None else sds.get(market, sds.get(_sd_fallback_key(market), 10.0)))
        if market in YES_NO_MARKETS or h.pairing == "participant_yes_no":
            paired = [q for q in quotes if q.p_over is not None]
            for q in quotes:
                others = [x.p_over for x in paired if x.book != q.book]
                if len(others) < min_books - 1:
                    continue
                p_yes = statistics.mean(others)
                for sel, price, p in (("Yes", q.over, p_yes), ("No", q.under, 1 - p_yes)):
                    if price is None:
                        continue
                    f = Finding(sport, event.get("id"), event.get("home_team"), event.get("away_team"),
                                event.get("commence_time", ""), market, sel, None, q.book, price, p,
                                ev_pct(p, price), f"consensus({len(others)} books, no-vig)", len(others) + 1,
                                player=player)
                    _cal_hook(f, "prop")
                    f.market_flag = f.ev >= thr
                    out.append(f)
            continue
        if h.dist == "poisson":
            POISSON_MARKETS.add(market)
        locs = [(q.book, _loc_from(q, market, sd)) for q in quotes]
        all_lines = [q.point for q in quotes if q.point is not None]
        med_line = statistics.median(all_lines) if all_lines else None
        # alternate ladders (alternate_team_totals*, ...) are priced line-by-line vs the consensus
        # location, so the main-line outlier filter does not apply to them
        max_dev = None if market.startswith("alternate_") else cfg.get("prop_max_line_dev", 0.15)
        for q in quotes:
            if q.point is None:
                continue
            if max_dev is not None and med_line is not None and \
                    abs(q.point - med_line) / max(abs(med_line), 1.0) > max_dev:
                continue   # really an alternate line, not a mispriced main line
            others = [v for b, v in locs if v is not None and b != q.book]
            n_other = len({b for b, v in locs if v is not None and b != q.book})
            if n_other < min_books - 1:
                continue
            loc = statistics.median(others)
            p_over, p_under = _probs_at(q.point, loc, market, sd)
            for sel, price, pw, pl in (("Over", q.over, p_over, p_under), ("Under", q.under, p_under, p_over)):
                if price is None:
                    continue
                f = Finding(sport, event.get("id"), event.get("home_team"), event.get("away_team"),
                            event.get("commence_time", ""), market, sel, q.point, q.book, price, pw,
                            ev_pct(pw, price, pl), f"consensus({n_other} books, "
                            f"{'poisson' if market in POISSON_MARKETS else f'normal sd={sd:g}'})",
                            n_other + 1, player=player, p_lose=pl,
                            extra={"consensus_loc": loc, "median_line": med_line,
                                   "line_diff": None if med_line is None else q.point - med_line})
                _cal_hook(f, "prop")
                f.market_flag = f.ev >= thr
                out.append(f)
    return out


def _sd_fallback_key(market: str) -> str:
    """team_totals_q1 -> team_totals_q1 / alternate_team_totals_h1 -> team_totals_h1."""
    return market.replace("alternate_", "")


# Tie-break when prices/EV are equal: lowest-hold books first (props backtest: FanDuel -5.8%,
# DraftKings / ESPN BET -6.0% hold at the consensus line).
# Marvin's own book (alerts.my_book, default Bovada) always wins ties first.
BOOK_PREFERENCE = ("fanduel", "draftkings", "espnbet")


def _book_rank(book: str, pref=BOOK_PREFERENCE) -> int:
    if mybook.is_mine(book):
        return -1
    b = (book or "").lower()
    return pref.index(b) if b in pref else len(pref)


def best_per_selection(findings: List[Finding], pref=BOOK_PREFERENCE) -> List[Finding]:
    """Keep the single best (highest EV) book per dedupe key; equal EV -> my book (Bovada), FanDuel,
    DraftKings, ESPN BET. My book's own price for the same pick is attached as extra['my_price']."""
    best: Dict[str, Finding] = {}
    mine: Dict[str, float] = {}
    for f in findings:
        k = f.dedupe_key
        if mybook.is_mine(f.book):
            mine[k] = f.price
        cur = best.get(k)
        if cur is None or f.ev > cur.ev + 1e-12 or \
                (abs(f.ev - cur.ev) <= 1e-12 and _book_rank(f.book, pref) < _book_rank(cur.book, pref)):
            best[k] = f
    for k, f in best.items():
        f.extra["my_book"] = mybook.key()
        if k in mine:
            f.extra["my_price"] = mine[k]
        elif "my_price" not in f.extra:
            f.extra["my_price"] = None
    return list(best.values())


def line_vs_consensus(f: Finding) -> Optional[float]:
    """+ = the line is better than the consensus median line for the chosen side, - = worse.
    Over: a lower line is better; Under: a higher line is better. None if unknown."""
    d = (f.extra or {}).get("line_diff")
    if d is None or f.selection not in ("Over", "Under"):
        return None
    return -d if f.selection == "Over" else d
