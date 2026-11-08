"""ESPN gamecast / play-by-play situation for live alerts (Marvin 2026-10-03 1:34 PM CT).

Marvin's example: a LINE SWING on Mississippi State +17.5 at ALA 41-17 MSST, 7:07 3rd, "but bama was about to
score". Score and clock alone miss that, so live alerts now also use ESPN's situation:

* Football (NFL/NCAAF): possession, down & distance, field position (yards to the goal), red zone, timeouts,
  last play, current drive, ESPN win probability. Source: the scoreboard competition `situation` (refreshed every
  scan) refined by the event /summary (`drives.current` last play + `winprobability`), fetched only when an alert
  is being built and cached `summary_ttl_s` (30 s) per game.
* Basketball: possession (if given) and the current scoring run from the /summary play-by-play.
* Soccer: red cards from the scoreboard competition `details`.

Use in alerts:
1. Model: the team with the ball is credited the expected points of its drive at that field position
   (`expected_points`, a standard NFL-style EP curve, scaled by down); the Stern margin input moves by +/-EP.
2. Cards: a short situation line under the score ('📍 ALA ball, 1st & 10 at MSST 12 (red zone)').
3. Hold: when the OPPONENT of the alerted side (or anyone, for an Under) has the ball in scoring range
   (red zone or <= `scoring_range_yds`), the alert is held until that drive ends (possession or score changes),
   then re-evaluated. Each alert is held at most ONE drive; if the opponent threatens again later it sends
   marked '⚠️ opponent in scoring position'. Holds never silence an alert type. Holds/releases are logged
   (log + kv 'gamecast:holds').
4. ESPN win probability is shown next to the model's win chance as a cross-check (and flagged when far apart).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

FOOTBALL = ("nfl", "ncaaf")
BASKETBALL = ("nba", "wnba", "ncaab", "wncaab")
DEFAULTS = {"enabled": True, "summary_ttl_s": 30, "scoring_range_yds": 35, "hold": True,
            "max_hold_s": 900, "ep_adjust": True, "espn_wp_flag_pts": 0.20, "run_min_pts": 8}
_CFG: Dict[str, Any] = dict(DEFAULTS)
_SUMMARY_FN: Optional[Callable[[str], Dict[str, Any]]] = None   # (league, event_id) -> summary json
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_clock: Callable[[], float] = time.time

# expected points of a drive by yards to the opponent's goal (1st & 10; NFL EP models, NCAAF is similar)
EP_TABLE = [(1, 6.0), (3, 5.5), (5, 5.1), (10, 4.6), (15, 4.2), (20, 3.8), (25, 3.4), (30, 3.0), (35, 2.6),
            (40, 2.3), (50, 1.7), (60, 1.1), (70, 0.6), (80, 0.2), (90, -0.2), (99, -0.6)]
DOWN_MULT = {1: 1.0, 2: 0.93, 3: 0.8, 4: 0.6}


def configure(cfg: Optional[Dict[str, Any]] = None, summary_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
              clock: Optional[Callable[[], float]] = None) -> None:
    """Scanner start-up: config (strategies.gamecast) and the ESPN /summary fetcher (league, event_id)."""
    global _SUMMARY_FN, _clock
    _CFG.clear()
    _CFG.update(DEFAULTS)
    _CFG.update(((cfg or {}).get("strategies") or {}).get("gamecast") or {})
    _SUMMARY_FN = summary_fn
    if clock is not None:
        _clock = clock
    _CACHE.clear()


def cfg() -> Dict[str, Any]:
    return _CFG


def expected_points(ytg: Optional[float], down: Optional[int] = None, distance: Optional[float] = None) -> Optional[float]:
    """Expected points of the drive for the team with the ball, `ytg` yards from the goal."""
    if ytg is None:
        return None
    y = min(max(float(ytg), 1.0), 99.0)
    ep = EP_TABLE[-1][1]
    for (y0, e0), (y1, e1) in zip(EP_TABLE, EP_TABLE[1:]):
        if y0 <= y <= y1:
            ep = e0 + (e1 - e0) * (y - y0) / (y1 - y0)
            break
    if down in DOWN_MULT and ep > 0:
        m = DOWN_MULT[down]
        if down >= 3 and distance is not None and distance <= 2:
            m = min(1.0, m + 0.12)          # short yardage converts most of the time
        ep *= m
    return round(ep, 2)


@dataclass
class Situation:
    league: str = ""
    poss_side: Optional[str] = None       # home / away
    poss_abbr: str = ""
    down: Optional[int] = None
    distance: Optional[float] = None
    ytg: Optional[float] = None           # yards to the opponent's goal for the team with the ball
    spot: str = ""                         # 'MSST 12'
    red_zone: bool = False
    home_timeouts: Optional[int] = None
    away_timeouts: Optional[int] = None
    last_play: str = ""
    drive: str = ""                        # '6 plays, 63 yards, 2:41'
    drive_start: str = ""
    espn_home_wp: Optional[float] = None
    run: Optional[Tuple[str, int, int]] = None      # basketball (side, pts, opp pts) current run
    red_cards: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "scoreboard"

    @property
    def known(self) -> bool:
        return self.poss_side is not None or bool(self.red_cards) or self.run is not None

    def ep(self) -> Optional[float]:
        return expected_points(self.ytg, self.down, self.distance) if self.league in FOOTBALL else None

    def threat(self, side: str) -> bool:
        """`side` has the ball in scoring range (red zone or <= scoring_range_yds)."""
        if self.league not in FOOTBALL or self.poss_side != side:
            return False
        return self.red_zone or (self.ytg is not None and self.ytg <= float(_CFG["scoring_range_yds"]))

    def any_threat(self) -> Optional[str]:
        for s in ("home", "away"):
            if self.threat(s):
                return s
        return None

    def drive_sig(self, g) -> List[Any]:
        return [self.poss_side, self.drive_start, g.away.score, g.home.score]

    def brief(self) -> Dict[str, Any]:
        return {k: v for k, v in dict(poss=self.poss_side, down=self.down, dist=self.distance, ytg=self.ytg,
                                      spot=self.spot, rz=self.red_zone or None, ep=self.ep(), wp_home=self.espn_home_wp,
                                      drive=self.drive or None, src=self.source,
                                      last_play=self.last_play[:140] or None).items() if v not in (None, "")}


def _ord(n: Optional[int]) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(int(n or 0), "")


def _side_of_team_id(g, tid: Any) -> Optional[str]:
    tid = str(tid or "")
    if not tid:
        return None
    if tid == str(g.home.id):
        return "home"
    if tid == str(g.away.id):
        return "away"
    return None


def _ytg_from_text(g, poss_side: Optional[str], txt: str) -> Optional[float]:
    m = re.match(r"\s*([A-Za-z&.]+)\s+(\d{1,2})\s*$", str(txt or ""))
    if not m or poss_side is None:
        return None
    abbr, yd = m.group(1).upper(), float(m.group(2))
    if yd == 50:
        return 50.0
    return 100.0 - yd if abbr == g.team(poss_side).abbr.upper() else yd


def _spot(g, side: str, ytg: Optional[float]) -> str:
    """'SYR 31' from yards to the goal when ESPN gives no possessionText."""
    if ytg is None or not 0 < ytg < 100:
        return ""
    if ytg == 50:
        return "50"
    opp = "away" if side == "home" else "home"
    return f"{g.team(opp).abbr} {ytg:g}" if ytg < 50 else f"{g.team(side).abbr} {100 - ytg:g}"


def _prob(p: Any) -> Optional[float]:
    try:
        v = float(p)
    except (TypeError, ValueError):
        return None
    return v if 0.0 <= v <= 1.0 else None


def from_game(g) -> Situation:
    """Situation from the scoreboard competition (`g.situation`, `g.details`)."""
    s = getattr(g, "situation", None) or {}
    sit = Situation(league=getattr(g, "league", ""))
    sit.poss_side = _side_of_team_id(g, s.get("possession"))
    if sit.poss_side:
        sit.poss_abbr = g.team(sit.poss_side).abbr
    try:
        sit.down = int(s["down"]) if s.get("down") not in (None, "", -1) and int(s["down"]) > 0 else None
    except (TypeError, ValueError):
        sit.down = None
    try:
        sit.distance = float(s["distance"]) if (sit.down is not None and s.get("distance") is not None) else None
    except (TypeError, ValueError):
        sit.distance = None
    sit.spot = str(s.get("possessionText") or "")
    sit.ytg = _ytg_from_text(g, sit.poss_side, sit.spot)
    sit.red_zone = bool(s.get("isRedZone"))
    for k, a in (("homeTimeouts", "home_timeouts"), ("awayTimeouts", "away_timeouts")):
        if s.get(k) is not None:
            try:
                setattr(sit, a, int(s[k]))
            except (TypeError, ValueError):
                pass
    lp = s.get("lastPlay") or {}
    if isinstance(lp, dict):
        sit.last_play = str(lp.get("text") or "")
        pr = lp.get("probability") or {}
        sit.espn_home_wp = _prob(pr.get("homeWinPercentage"))
        dr = lp.get("drive") or {}
        sit.drive = str(dr.get("description") or "")
        sit.drive_start = str((dr.get("start") or {}).get("text") or "")
    sit.red_cards = list(getattr(g, "red_cards", None) or [])
    return sit


def refine(g, sit: Situation, summary: Dict[str, Any]) -> Situation:
    """Fresher data from /summary: drives.current last play (football), plays run (basketball), win probability."""
    if not summary:
        return sit
    try:
        cur = ((summary.get("drives") or {}).get("current")) or {}
        plays = cur.get("plays") or []
        if g.league in FOOTBALL and cur:
            end = (plays[-1].get("end") if plays else None) or {}
            side = _side_of_team_id(g, (end.get("team") or {}).get("id") or (cur.get("team") or {}).get("id"))
            last = plays[-1] if plays else {}
            over = bool(cur.get("end") or cur.get("result") or last.get("scoringPlay")
                        or (end.get("yardsToEndzone") is not None and not 0 < float(end["yardsToEndzone"]) < 100)
                        or str((last.get("type") or {}).get("abbreviation") or "").upper() in ("TD", "FG", "PUNT", "XP"))
            if over:                       # drive finished (score / punt / turnover): between possessions
                if not sit.poss_side or sit.poss_side == side:
                    sit.poss_side, sit.poss_abbr, sit.down, sit.distance, sit.ytg = None, "", None, None, None
                    sit.spot, sit.red_zone = "", False
                side = None
            if side and end:
                sit.poss_side, sit.poss_abbr = side, g.team(side).abbr
                d = end.get("down")
                sit.down = int(d) if d not in (None, "") and int(d) > 0 else None
                sit.distance = (float(end["distance"]) if (sit.down is not None and end.get("distance") is not None)
                                else None)
                if end.get("yardsToEndzone") is not None:
                    sit.ytg = float(end["yardsToEndzone"])
                sit.spot = str(end.get("possessionText") or "") or _spot(g, side, sit.ytg) or sit.spot
                sit.red_zone = sit.ytg is not None and sit.ytg <= 20
                sit.last_play = str(plays[-1].get("text") or sit.last_play)
            sit.drive = str(cur.get("description") or sit.drive)
            sit.drive_start = str((cur.get("start") or {}).get("text") or sit.drive_start)
            sit.source = "summary"
        if g.league in BASKETBALL:
            sit.run = scoring_run(g, summary.get("plays") or [])
            sit.source = "summary"
        wp = summary.get("winprobability") or []
        if wp:
            v = _prob((wp[-1] or {}).get("homeWinPercentage"))
            if v is not None:
                sit.espn_home_wp = v
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as e:
        log.debug("gamecast refine %s failed: %s", getattr(g, "event_id", "?"), e)
    return sit


def scoring_run(g, plays: List[Dict[str, Any]]) -> Optional[Tuple[str, int, int]]:
    """Current basketball run: (side, its points, 0) = points by the last team to score since the other team's
    last score."""
    pts = {"home": 0, "away": 0}
    lead_side = None
    for p in reversed(plays):
        if not p.get("scoringPlay"):
            continue
        side = _side_of_team_id(g, (p.get("team") or {}).get("id"))
        if side is None:
            continue
        v = int(p.get("scoreValue") or 0)
        if lead_side is None:
            lead_side = side
        if side != lead_side:
            break
        pts[side] += v
    if lead_side is None:
        return None
    return lead_side, pts[lead_side], 0


def _summary(g) -> Dict[str, Any]:
    if _SUMMARY_FN is None:
        return {}
    now = _clock()
    hit = _CACHE.get(g.event_id)
    if hit and now - hit[0] < float(_CFG["summary_ttl_s"]):
        return hit[1]
    try:
        data = _SUMMARY_FN(g.league, g.event_id) or {}
    except Exception as e:  # noqa: BLE001 - ESPN hiccup: fall back to the scoreboard situation
        log.info("gamecast summary %s failed: %s", g.event_id, e)
        data = hit[1] if hit else {}
    _CACHE[g.event_id] = (now, data)
    if len(_CACHE) > 300:
        for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:100]:
            _CACHE.pop(k, None)
    return data


def situation(g, fetch: bool = True) -> Optional[Situation]:
    """Live situation for an in-progress game (None when not live / disabled)."""
    if g is None or getattr(g, "state", "") != "in" or not _CFG.get("enabled", True):
        return None
    sit = from_game(g)
    if fetch and (g.league in FOOTBALL or g.league in BASKETBALL):
        sit = refine(g, sit, _summary(g))
    return sit


def margin_adj(g, side: str, sit: Optional[Situation] = None) -> float:
    """Expected-margin shift for `side` from the current drive: +EP when `side` has the ball, -EP when the other
    team has it (football only)."""
    if not _CFG.get("ep_adjust", True) or g is None or g.league not in FOOTBALL or side not in ("home", "away"):
        return 0.0
    sit = sit if sit is not None else situation(g)
    if sit is None or sit.poss_side is None:
        return 0.0
    ep = sit.ep()
    if ep is None:
        return 0.0
    return ep if sit.poss_side == side else -ep


def espn_wp(g, side: str, sit: Optional[Situation] = None) -> Optional[float]:
    sit = sit if sit is not None else situation(g)
    if sit is None or sit.espn_home_wp is None or side not in ("home", "away"):
        return None
    return sit.espn_home_wp if side == "home" else 1.0 - sit.espn_home_wp


def wp_cross(model_p: Optional[float], g, side: str, sit: Optional[Situation] = None) -> str:
    """' (ESPN 2%)' next to the model win chance; '⚠️' added when the two differ by > espn_wp_flag_pts."""
    e = espn_wp(g, side, sit)
    if e is None:
        return ""
    flag = model_p is not None and abs(model_p - e) > float(_CFG["espn_wp_flag_pts"])
    return f" (ESPN {e * 100:.0f}%{' ⚠️' if flag else ''})"


def cross_line(model_p: Optional[float], g, side: Optional[str]) -> str:
    """'ESPN win 12% for MSST (model 30% ⚠️)': ESPN's own win probability as a cross-check on a live card."""
    if side not in ("home", "away"):
        return ""
    e = espn_wp(g, side)
    if e is None:
        return ""
    s = f"ESPN win {e * 100:.0f}% for {g.team(side).abbr}"
    if model_p is not None:
        flag = abs(model_p - e) > float(_CFG["espn_wp_flag_pts"])
        s += f" (model {model_p * 100:.0f}%{' ⚠️' if flag else ''})"
    return s


def card_line(g, sit: Optional[Situation] = None) -> str:
    """'📍 ALA ball, 1st & 10 at MSST 12 (red zone)' (+ timeouts late, basketball run, soccer red cards)."""
    from . import friendly as fr
    if g is None or getattr(g, "state", "") != "in":
        return ""
    sit = sit if sit is not None else situation(g)
    if sit is None:
        return ""
    bits: List[str] = []
    if g.league in FOOTBALL and sit.poss_side:
        s = f"{sit.poss_abbr or g.team(sit.poss_side).abbr} ball"
        if sit.down and sit.distance is not None:
            dist = "goal" if sit.ytg is not None and sit.distance >= sit.ytg else f"{sit.distance:g}"
            s += f", {_ord(sit.down)} & {dist}"
        if sit.spot:
            s += f" at {sit.spot}"
        if sit.red_zone:
            s += " (red zone)"
        elif sit.threat(sit.poss_side):
            s += " (scoring range)"
        ep = sit.ep()
        if ep is not None and abs(ep) >= 0.5:
            s += f" · drive ≈{ep:+.1f} pts"
        bits.append(s)
    elif g.league in BASKETBALL and sit.run and sit.run[1] >= int(_CFG["run_min_pts"]):
        bits.append(f"{g.team(sit.run[0]).abbr} on a {sit.run[1]}-{sit.run[2]} run")
    if sit.red_cards:
        rc = []
        for r in sit.red_cards[:3]:
            side = _side_of_team_id(g, r.get("team_id"))
            who = g.team(side).abbr if side else "?"
            rc.append(f"{who} {r.get('minute') or ''}".strip())
        bits.append("red card " + ", ".join(rc))
    if not bits:
        return ""
    return "📍 " + fr.esc(" · ".join(bits))


# ------------------------------------------------------------------ hold one drive
def _log_hold(db, row: Dict[str, Any]) -> None:
    try:
        _, rows = db.kv_get("gamecast:holds")
        rows = list(rows or [])
        rows.append(row)
        db.kv_set("gamecast:holds", rows[-300:])
    except Exception:  # noqa: BLE001
        log.debug("hold log failed", exc_info=True)


def threat_for(g, sit: Optional[Situation], side: Optional[str] = None, total_side: Optional[str] = None) -> Optional[str]:
    """The side threatening the alert: the opponent of `side` in scoring range, or anyone for an Under."""
    if sit is None or g.league not in FOOTBALL:
        return None
    if side in ("home", "away"):
        opp = "away" if side == "home" else "home"
        return opp if sit.threat(opp) else None
    if (total_side or "").lower() == "under":
        return sit.any_threat()
    return None


def hold_check(db, g, alert_key: str, side: Optional[str] = None, total_side: Optional[str] = None,
               strategy: str = "", sit: Optional[Situation] = None) -> Tuple[str, str]:
    """('send', note) or ('hold', why) for a live alert about to go out. The alert is held while the drive that
    threatens it lasts (at most one drive per alert, capped at max_hold_s); afterwards it is re-evaluated by its
    own scanner. A second threat later sends with the warning note."""
    if g is None or getattr(g, "state", "") != "in" or g.league not in FOOTBALL or not _CFG.get("enabled", True):
        return "send", ""
    sit = sit if sit is not None else situation(g)
    k = f"gchold:{g.event_id}:{alert_key}"
    _, rec = db.kv_get(k)
    thr = threat_for(g, sit, side, total_side)
    now = _clock()
    if thr is None:
        if rec and rec.get("state") == "held":
            rec.update(state="released", released_ts=now)
            db.kv_set(k, rec)
            log.info("gamecast HOLD released %s %s: drive over (%s)", strategy, alert_key, (sit.brief() if sit else {}))
            _log_hold(db, dict(ts=now, action="release", strategy=strategy, key=alert_key, event_id=g.event_id,
                               score=f"{g.away.abbr} {g.away.score}-{g.home.score} {g.home.abbr}", detail=g.detail))
        return "send", ""
    who = g.team(thr).abbr
    note = "⚠️ opponent in scoring position" if side in ("home", "away") else f"⚠️ {who} in scoring position"
    if not _CFG.get("hold", True):
        return "send", note
    sig = sit.drive_sig(g)
    if rec is None:
        rec = dict(state="held", sig=sig, ts=now, strategy=strategy, threat=thr)
        db.kv_set(k, rec)
        why = f"{who} ball at {sit.spot or '?'}{' (red zone)' if sit.red_zone else ''}"
        log.info("gamecast HOLD %s %s %s: %s, held until the drive ends", strategy, g.name, alert_key, why)
        _log_hold(db, dict(ts=now, action="hold", strategy=strategy, key=alert_key, event_id=g.event_id, why=why,
                           score=f"{g.away.abbr} {g.away.score}-{g.home.score} {g.home.abbr}", detail=g.detail,
                           situation=sit.brief()))
        return "hold", why
    if rec.get("state") == "held" and rec.get("sig") == sig and now - float(rec["ts"] if rec.get("ts") is not None else now) < float(_CFG["max_hold_s"]):
        return "hold", "still on the same drive"
    if rec.get("state") == "held":           # new threatening drive or time cap: one hold used up -> send marked
        rec.update(state="released", released_ts=now)
        db.kv_set(k, rec)
        log.info("gamecast HOLD released %s %s: %s (sent marked)", strategy, alert_key,
                 "time cap" if rec.get("sig") == sig else "new drive")
        _log_hold(db, dict(ts=now, action="release_marked", strategy=strategy, key=alert_key, event_id=g.event_id))
    return "send", note


def holds_recent(db, n: int = 20) -> List[Dict[str, Any]]:
    _, rows = db.kv_get("gamecast:holds")
    return list(rows or [])[-n:]
