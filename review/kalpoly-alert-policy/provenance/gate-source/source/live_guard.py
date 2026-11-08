"""Live-alert guard (Marvin, 2026-10-01, after the North Texas -600 alert at 31-24 with Tulsa in scoring range).

Rules (config strategies.live_guard):
1. Heavy favourites: no live moneyline alert at a price shorter than `heavy_fav_price` (-300) unless the team
   was a huge pregame favourite (ML <= `huge_pregame_ml`, -2000) AND leads by more than one possession.
2. Volatile one-possession games (NFL/NCAAF lead <= `one_possession_pts`, 8): no live alert (any market)
   when the trailing team has the ball in scoring range (ESPN red zone, or within `scoring_range_yds` of the
   goal). If ESPN gives no possession data, the game counts as volatile once <= `unknown_volatile_frac_left`
   of the game is left (4th quarter by default).
3. Target band: live ML alerts only at prices from `ml_price_min` (-200) to `ml_price_max` (+150), i.e. modest
   favourites or close/tied games at plus money / near even (the LV Aces kind), never big favourites.
Blocked picks go to the paper watchlist (tracked, never sent). The price checked is Bovada's when
only_my_book is on (what Marvin would actually bet), else the alert price.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from .espn import Game, time_left_frac

FOOTBALL = ("nfl", "ncaaf")
DEFAULTS = {"enabled": True, "heavy_fav_price": -300.0, "huge_pregame_ml": -2000.0,
            "one_possession_pts": {"nfl": 8, "ncaaf": 8, "nba": 3, "wnba": 3, "ncaab": 3},
            "scoring_range_yds": 40, "unknown_volatile_frac_left": 0.25,
            "ml_price_min": -200.0, "ml_price_max": 150.0, "enforce_target_band": True}


def _cfg(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    c = dict(DEFAULTS)
    c.update(((cfg or {}).get("strategies") or {}).get("live_guard") or {})
    return c


def _shorter(price: float, than: float) -> bool:
    """True if American `price` is a shorter (more favourite) price than `than` (both may be +/-)."""
    def dec(o):
        return 1 + (o / 100 if o > 0 else 100 / -o)
    return dec(price) < dec(than) - 1e-12


def one_possession(league: str, c: Dict[str, Any]) -> Optional[float]:
    op = c.get("one_possession_pts")
    if isinstance(op, dict):
        return op.get(league)
    return op


def yards_to_goal(g: Game, poss_side: str) -> Optional[float]:
    """Distance to the end zone for the team with the ball, from ESPN possessionText ('TLSA 25')."""
    sit = g.situation or {}
    txt = str(sit.get("possessionText") or "")
    m = re.match(r"\s*([A-Za-z&.]+)\s+(\d{1,2})\s*$", txt)
    if not m:
        return None
    side_abbr, yd = m.group(1).upper(), float(m.group(2))
    if yd == 50:
        return 50.0
    own = g.team(poss_side).abbr.upper()
    return 100.0 - yd if side_abbr == own else yd


def possession_side(g: Game) -> Optional[str]:
    pid = str((g.situation or {}).get("possession") or "")
    if not pid:
        return None
    if pid == str(g.home.id):
        return "home"
    if pid == str(g.away.id):
        return "away"
    return None


def volatile(g: Game, league_cfg: Dict[str, Any], c: Dict[str, Any]) -> Tuple[bool, str]:
    """Rule 2: one-possession football game with the trailing team threatening (or no data, late)."""
    if g.league not in FOOTBALL:
        return False, ""
    op = one_possession(g.league, c)
    lead = abs(g.home.score - g.away.score)
    if op is None or lead > op:
        return False, ""
    trailing = None if lead == 0 else ("home" if g.home.score < g.away.score else "away")
    poss = possession_side(g)
    sit = g.situation or {}
    if poss is not None:
        ytg = yards_to_goal(g, poss)
        threat = bool(sit.get("isRedZone")) or (ytg is not None and ytg <= float(c["scoring_range_yds"]))
        if threat and (trailing is None or poss == trailing):
            who = g.team(poss).name
            where = sit.get("possessionText") or ("red zone" if sit.get("isRedZone") else "scoring range")
            return True, (f"one-possession game ({g.away.abbr} {g.away.score}-{g.home.score} {g.home.abbr}) and "
                          f"{who} has the ball at {where}: too volatile")
        return False, ""
    frac = time_left_frac(g, int(league_cfg.get("periods") or 4), float(league_cfg.get("period_minutes") or 15))
    if frac <= float(c["unknown_volatile_frac_left"]):
        return True, "one-possession game, second half, no ESPN possession data: treated as volatile"
    return False, ""


def check(g: Game, side: Optional[str], market: str, price: Optional[float], league_cfg: Dict[str, Any],
          cfg: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """(allowed, reason) for a live alert on `side` in `market` at American `price`."""
    c = _cfg(cfg)
    if not c.get("enabled", True):
        return True, ""
    vol, why = volatile(g, league_cfg, c)
    if vol:
        return False, why
    if market != "h2h" or price is None or side not in ("home", "away"):
        return True, ""
    margin = (g.home.score - g.away.score) * (1 if side == "home" else -1)
    op = one_possession(g.league, c)
    pm = g.pregame.ml(side)
    huge = pm is not None and pm <= float(c["huge_pregame_ml"]) and op is not None and margin > op
    if _shorter(price, float(c["heavy_fav_price"])):
        if huge:
            return True, f"huge pregame favourite ({pm:+.0f}) leading by more than one possession"
        return False, (f"price {price:+.0f} is a heavy favourite (shorter than {c['heavy_fav_price']:+.0f}); only allowed for a "
                       f"huge pregame favourite (<= {c['huge_pregame_ml']:+.0f}) leading by more than one possession")
    if c.get("enforce_target_band", True) and not huge:
        lo, hi = float(c["ml_price_min"]), float(c["ml_price_max"])
        if _shorter(price, lo) or _shorter(hi, price):
            return False, f"price {price:+.0f} outside the live ML target band {lo:+.0f} to {hi:+.0f}"
    return True, ""


# ---------------------------------------------------------------- stale-price guard (round 2, 2026-10-02)
def _iso_ts(s: Any) -> Optional[float]:
    if not s:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def book_last_update(ev: Optional[Dict[str, Any]], book: str = "pinnacle", market: Optional[str] = None) -> Optional[float]:
    """Epoch seconds of `book`'s last_update in an Odds API event (market-level if given, else bookmaker-level)."""
    for b in (ev or {}).get("bookmakers") or []:
        if (b.get("key") or "").lower() != book:
            continue
        if market:
            for m in b.get("markets") or []:
                if m.get("key") == market and m.get("last_update"):
                    return _iso_ts(m["last_update"])
        return _iso_ts(b.get("last_update"))
    return None


def situation_brief(g: Optional[Game]) -> Dict[str, Any]:
    """Compact ESPN possession / field position for the live-alert log."""
    s = (getattr(g, "situation", None) or {}) if g is not None else {}
    if not s:
        return {}
    keep = ("possession", "possessionText", "downDistanceText", "shortDownDistanceText", "yardLine", "isRedZone",
            "down", "distance")
    out = {k: s.get(k) for k in keep if s.get(k) is not None}
    try:
        ps = possession_side(g)
        if ps:
            out["possession_side"] = ps
    except Exception:  # noqa: BLE001
        pass
    lp = s.get("lastPlay") or {}
    if isinstance(lp, dict) and lp.get("text"):
        out["last_play"] = str(lp["text"])[:160]
    return out


def stale_check(g: Optional[Game], ev: Optional[Dict[str, Any]], odds_ts: Optional[float], cfg: Optional[Dict[str, Any]],
                now: Optional[float] = None, best_price: Optional[float] = None, my_price: Optional[float] = None,
                market_ev: Optional[float] = None, market: Optional[str] = None) -> Tuple[bool, str]:
    """HARD stale-price block for live alerts. Returns (ok, why). Rules:
    1. price age (now - odds fetch time) <= live_odds.max_price_age_s (30)
    2. value_finder.max_best_vs_mybook_gap (0.10): best-book decimal payout more than 10% above Bovada's = stale
    3. value_finder.in_game_ev_max (0.15): a live edge above 15% is almost always a stale quote
    4. the Pinnacle quote must be newer than the last ESPN score change (live_odds.require_pinnacle_after_score)."""
    import time as _t
    now = _t.time() if now is None else now
    lo = (cfg or {}).get("live_odds") or {}
    vfc = ((cfg or {}).get("strategies") or {}).get("value_finder") or {}
    if lo.get("hard_price_age", True):
        lim = float(lo.get("max_price_age_s", 30))
        if odds_ts is None:
            return False, "stale: price age unknown"
        age = now - float(odds_ts)
        if age > lim:
            return False, f"stale: prices {age:.0f}s old (> {lim:.0f}s)"
    gap = vfc.get("max_best_vs_mybook_gap")
    if gap is not None and best_price is not None and my_price is not None:
        def dec(o):
            return 1 + (o / 100 if o > 0 else 100 / -o)
        g_ = dec(float(best_price)) / dec(float(my_price)) - 1
        if g_ > float(gap) + 1e-12:
            return False, f"stale: best book pays {g_:.0%} more than Bovada (> {float(gap):.0%})"
    emax = vfc.get("in_game_ev_max")
    if emax is not None and market_ev is not None and market_ev > float(emax) + 1e-12:
        return False, f"stale: live edge {market_ev:+.0%} above the {float(emax):.0%} cap"
    if lo.get("require_pinnacle_after_score", True) and g is not None:
        sc = getattr(g, "score_changed_ts", None)
        pin = book_last_update(ev, "pinnacle", market) if ev is not None else None
        if pin is None:
            return False, "stale: no Pinnacle timestamp"
        if sc is not None and pin <= float(sc):
            return False, "stale: Pinnacle quote is older than the last score change"
    return True, ""
