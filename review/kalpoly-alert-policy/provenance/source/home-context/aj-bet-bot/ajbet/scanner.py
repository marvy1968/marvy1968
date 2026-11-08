"""One scan: ESPN (free) for every league -> candidates -> Odds API only for candidate sports."""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import asdict
from typing import Tuple, Any, Callable, Dict, List, Optional

from .alerts import Notifier, format_alert
from .config import league_table
from .db import DB
from .espn import ESPNClient, Game, Lines, game_from_summary, parse_scoreboard
from .markets import match_event
from .oddsapi import BudgetExceeded, OddsAPI
from .oddsmath import american_to_prob
from . import mybook as _mybook
from . import line_history as _lh
from . import opening as _opening
from .paper import record_paper_bet, settle_with_game
from . import live_state
from . import strategies as st

log = logging.getLogger(__name__)

ScoreboardFn = Callable[[Dict[str, Any]], Dict[str, Any]]
SummaryFn = Callable[[Dict[str, Any], str], Dict[str, Any]]


def price_age_line(odds_ts, cfg, now=None):
    """'⏱ Live prices 42s old (as of 7:15:03 PM CT)' + a STALE warning past live_odds.max_price_age_s (30).

    Round 2: the age is ALSO a hard block for sending live alerts (live_guard.stale_check); this line documents it."""
    if odds_ts is None:
        return ""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = time.time() if now is None else now
    age = max(0.0, now - float(odds_ts))
    lim = float((cfg.get("live_odds") or {}).get("max_price_age_s", 30))
    t = datetime.fromtimestamp(float(odds_ts), ZoneInfo(cfg.get("timezone", "America/Chicago")))
    hm = t.strftime("%I:%M:%S %p").lstrip("0")
    if age <= 30:                    # Marvin 2026-10-02: only show the age when it is older than 30 s
        return ""
    line = f"⏱ Prices {age:.0f}s old ({hm} CT)"
    if age > lim:
        line += " ⚠️ recheck Bovada before betting"
    return line


class Scanner:
    def __init__(self, cfg: Dict[str, Any], db: DB, odds: OddsAPI, notifier: Notifier,
                 scoreboard_fn: Optional[ScoreboardFn] = None, summary_fn: Optional[SummaryFn] = None,
                 printer=print):
        self.cfg, self.db, self.odds, self.notifier, self.print = cfg, db, odds, notifier, printer
        from . import sat_priority
        sat_priority.configure(cfg)
        self.leagues = league_table(cfg)
        espn = ESPNClient()
        self.scoreboard_fn = scoreboard_fn or (lambda lg: espn.scoreboard(lg["espn"], lg.get("params")))
        self.summary_fn = summary_fn or (lambda lg, eid: espn.summary(lg["espn"], eid))
        self.aliases = cfg.get("team_aliases") or {}
        self.sa = cfg["strategies"]["favorite_comeback"]
        self.sb = cfg["strategies"]["soccer_grind"]
        self.paper = cfg.get("paper", {})
        self.value_scanner = None   # optional Strategy C in-game hook (set by CLI)
        self.team_model = None      # optional features.TeamModel (aj_edge v2 ratings prior)
        self._backoff: Dict[str, Tuple[int, float]] = {}   # league -> (failures, retry_at)
        _mybook.configure(cfg)
        from . import policy as _policy
        _policy.configure(cfg)
        log.info("alert policy (scorecard): %s", _policy.summary())
        self._live_events: Dict[str, Tuple[float, list]] = {}
        self._no_offer: Dict[str, float] = {}     # soccer event -> ts when no under price was offered   # odds_key -> (ts, events) for 0-credit reuse
        from .total_swing import TotalSwingScanner
        self.total_swing = TotalSwingScanner(cfg, db, league_table(cfg, include_disabled=True), notifier,
                                             odds_cache_fn=self._live_events.get)
        from .trends import TrendStore, TrendScanner
        from .config import resolve_path as _rp
        self.trend_store = TrendStore(cfg, _rp(cfg, (cfg.get("strategies", {}).get("team_trends") or {})
                                                .get("data_dir", "data/trends")))
        self.trends = TrendScanner(cfg, db, self.trend_store, notifier, odds_cache_fn=self._live_events.get,
                                   leagues=league_table(cfg, include_disabled=True))
        from .line_swing import LineSwingScanner
        self.line_swing = LineSwingScanner(cfg, db, league_table(cfg, include_disabled=True), notifier,
                                           summary_fn=self.summary_fn, rating_fn=self.rating_for,
                                           top30_fn=self._top30, odds_cache_fn=self._live_events.get)
        from .tracking import LiveLog, Closer
        self.live_log = LiveLog(db, cfg, league_table(cfg, include_disabled=True), self._live_events.get,
                                aliases=self.aliases)
        self.closer = Closer(db, cfg, league_table(cfg, include_disabled=True), odds, aliases=self.aliases)
        self.total_swing.live_log = self.live_log
        self._scores: Dict[str, Tuple[tuple, float]] = {}   # event -> (score, ts of last observed change)
        from . import gamecast              # ESPN gamecast situation (drives / win prob), cached per game
        _all = league_table(cfg, include_disabled=True)
        gamecast.configure(cfg, lambda lk, eid: self.summary_fn(_all[lk], eid))

    def track_scores(self, games: Dict[str, List[Game]], now: Optional[float] = None) -> None:
        """Stale guard input: epoch of the last observed score change per live game (first sighting counts
        as a change, so a quote must be newer than the moment we first saw the game live)."""
        now = time.time() if now is None else now
        for gs in games.values():
            for g in gs:
                if g.state != "in":
                    continue
                sc = (g.away.score, g.home.score)
                prev = self._scores.get(g.event_id)
                if prev is None or prev[0] != sc:
                    self._scores[g.event_id] = (sc, now)
                g.score_changed_ts = self._scores[g.event_id][1]

    def _top30(self):
        vs = self.value_scanner
        if vs is None:
            return {}
        if not vs.top30:
            try:
                vs.load_top30()
            except Exception:  # noqa: BLE001
                return {}
        return vs.top30

    def rating_for(self, g: Game) -> Optional[Dict[str, Any]]:
        """Ratings pregame margin for aj_edge v2 (None => market prior). NCAAF: every FBS game unless the
        ncaaf sport scope is ncaaf_top_n (then top-30 games only)."""
        if self.team_model is None or g.league not in ("nfl", "ncaaf", "wnba"):
            return None
        if g.league == "ncaaf" and (self.cfg.get("sports", {}).get("ncaaf") or {}).get("scope", "all") != "all":
            vs = self.value_scanner
            top = vs.top30 if vs is not None else {}
            if vs is not None and not top:
                try:
                    vs.load_top30()
                    top = vs.top30
                except Exception as e:  # noqa: BLE001
                    log.warning("top-30 load failed: %s", e)
            if g.home.id not in top and g.away.id not in top:
                return None
        try:
            r = self.team_model.margin(g.league, g.home.name, g.away.name, g.neutral, g.home.id, g.away.id)
        except Exception as e:  # noqa: BLE001
            log.warning("rating lookup failed %s: %s", g.name, e)
            return None
        if not r:
            return None
        return {"margin_home": r["margin"], "source": r["source"], "as_of": r["as_of"], "games": r["games"],
                "srs": r["srs"], "calibrated": r["calibrated"]}

    # ------------------------------------------------------------ ESPN
    def _pregame(self, g: Game) -> None:
        """Persist pregame lines while 'pre'; once live, prefer the stored snapshot."""
        if g.state == "pre" and (g.pregame.ml_home is not None or g.pregame.total is not None):
            self.db.save_pregame(g.event_id, g.league, asdict(g.pregame))
        elif g.state == "in":
            snap = self.db.get_pregame(g.event_id)
            if snap:
                stored = Lines(**snap)
                for k, v in asdict(stored).items():
                    if v is not None:
                        setattr(g.pregame, k, v)
            elif g.pregame.ml_home is None and g.pregame.spread_home is None and g.kind == "us":
                try:  # free fallback: summary pickcenter
                    sg = game_from_summary(self.summary_fn(self.leagues[g.league], g.event_id), g.league, g.kind)
                    if sg:
                        g.pregame = sg.pregame
                        self.db.save_pregame(g.event_id, g.league, asdict(g.pregame))
                except Exception as e:
                    log.warning("summary fallback failed %s: %s", g.event_id, e)

    # ------------------------------------------------------------ live odds (The Odds API)
    def _live_cfg(self) -> Dict[str, Any]:
        return {"enabled": True, "interval_s": 180, "sports": "all",
                **(self.cfg.get("live_odds") or {})}

    def _sport_cap_ok(self, key: str, odds_key: str, c: dict) -> bool:
        """Per-sport daily live credit cap (Odds API credits logged today for this sport key). Fails open."""
        try:
            cap = (c.get("sport_daily_cap") or {}).get(key)
            if not cap:
                return True
            from .db import today_local
            row = self.db.conn.execute(f"SELECT COALESCE(SUM(cost),0) FROM api_usage WHERE {self.db._DAY}=? AND sport=?",
                                       (today_local(), odds_key)).fetchone()
            return float(row[0] or 0) < float(cap)
        except Exception:  # noqa: BLE001
            return True

    def _soccer_budget_ok(self, share: float) -> bool:
        """Soccer live pulls only while today's live credits are below `share` of the daily allocation."""
        try:
            from .db import today_local
            a = ((self.cfg.get("odds_api") or {}).get("allocations") or {}).get("live") or {}
            daily = float(a.get("daily") or 0)
            return not daily or self.db.credits_used_on(today_local(), "live") < share * daily
        except Exception:  # noqa: BLE001
            return True

    def _priority_paused(self, key: str) -> bool:
        """2026-10-24 (Marvin: Saturday = CFB priority): non-priority leagues pause live pulls once this bot's
        credits today reach others_max_share_of_day_cap x the day's cap (ajbet/sat_priority.py). Fails open."""
        try:
            from .db import is_weekend_local, today_local
            from . import sat_priority as _sp
            oc = self.cfg.get("odds_api") or {}
            t = today_local()
            cap = oc.get("daily_credit_cap")
            if cap is not None and oc.get("daily_credit_cap_weekend") is not None and is_weekend_local(t):
                cap = oc["daily_credit_cap_weekend"]
            cap = _sp.day_cap(t, cap, self.cfg)
            return _sp.others_paused(key, self.db.credits_used_on(t), cap, self.cfg)
        except Exception:  # noqa: BLE001
            return False

    def _live_interval(self) -> float:
        return float(self._live_cfg().get("interval_s", 180))

    def _offhours_now(self, now_: Optional[float] = None) -> bool:
        """Oct 11 2026 (Marvin approved): True inside live_odds.offhours [start, end) in CT (wraps midnight)."""
        oh = self._live_cfg().get("offhours") or {}
        if not oh.get("enabled", False):
            return False
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            t = datetime.fromtimestamp(now_ if now_ is not None else time.time(),
                                       ZoneInfo(self.cfg.get("timezone", "America/Chicago")))
            m = t.hour * 60 + t.minute
            sh, sm = (int(x) for x in str(oh.get("start", "00:00")).split(":"))
            eh, em = (int(x) for x in str(oh.get("end", "10:00")).split(":"))
            a, b = sh * 60 + sm, eh * 60 + em
            return (a <= m < b) if a <= b else (m >= a or m < b)
        except Exception:  # noqa: BLE001
            return False

    def _live_interval_for(self, key: str, now_: Optional[float] = None) -> float:
        """Live-odds refresh interval for league `key`: interval_overrides (per league, 'soccer' for every soccer
        league), then the off-hours floor (Oct 11 2026) unless the league is exempt."""
        c = self._live_cfg()
        lg = self.leagues.get(key) or {}
        ov = c.get("interval_overrides") or {}
        base = float(c.get("interval_s", 180))
        every = float(ov.get(key, ov.get("soccer", base) if lg.get("kind") == "soccer" else base))
        try:   # 2026-10-24 (Marvin): Saturday = CFB priority / credit plan day trims (ajbet/sat_priority.py)
            from . import sat_priority as _sp
            every = _sp.live_interval(key, lg.get("kind"), every, self.cfg)
        except Exception:  # noqa: BLE001
            pass
        oh = c.get("offhours") or {}
        ex = oh.get("exempt") or []
        if key not in ex and not ("soccer" in ex and lg.get("kind") == "soccer") and self._offhours_now(now_):
            every = max(every, float(oh.get("interval_s", every)))
        return every

    def refresh_live_odds(self, games: Dict[str, List[Game]], summary: Dict[str, Any]) -> int:
        """While games in a sport are live, pull that sport's live featured odds (one call covers every
        live event, ~3 credits) every `live_odds.interval_s`. Feeds Strategy A, D (line swing) and
        live value. The live budget gate (odds_api.allocations.live) still applies."""
        c = self._live_cfg()
        if not c.get("enabled", True) or not getattr(self.odds, "has_key", True):
            return 0
        n = 0
        now_ = time.time()
        soccer_cap = float(c.get("soccer_max_share_of_live_daily", 0.6))
        try:   # 2026-10-24: smaller soccer share on Saturdays (CFB priority) / plan days
            from . import sat_priority as _sp
            soccer_cap = _sp.soccer_share(soccer_cap, self.cfg)
        except Exception:  # noqa: BLE001
            pass
        for key, gs in games.items():
            lg = self.leagues.get(key) or {}
            sp = c.get("sports", "all")
            if sp != "all" and key not in sp:
                continue
            if not any(g.state == "in" for g in gs):
                continue
            if lg.get("kind") == "soccer" and not self._soccer_budget_ok(soccer_cap):
                continue                      # keep live credits for football/basketball first
            ok = lg.get("odds_key")
            hit = self._live_events.get(ok)
            every = self._live_interval_for(key, now_)
            if hit is not None and now_ - hit[0] < every:
                continue
            if not self._sport_cap_ok(key, ok, c):   # 2026-10-25: per-sport daily credit cap (live_odds.sport_daily_cap)
                summary.setdefault("skipped", []).append(f"{ok} live odds: per-sport daily cap reached")
                continue
            if self._priority_paused(key):    # 2026-10-24: keep the rest of the day's credits for the priority sport
                summary.setdefault("skipped", []).append(f"{ok} live odds: paused for priority sport (sat_cfb_priority/credit_plan)")
                continue
            try:
                events = self.odds.live_odds(ok, strategy="live") or []
            except BudgetExceeded as e:
                summary.setdefault("skipped", []).append(f"{ok} live odds: {e}")
                continue
            self._live_events[ok] = (time.time(), events)
            n += 1
            try:                              # line history: Odds API consensus for every game in this pull
                for g in gs:
                    ev = match_event(g.home.name, g.away.name, g.start, events, self.cfg.get("team_aliases") or {})
                    if ev:
                        _lh.record_oddsapi(self.db, g, ev, self.cfg.get("team_aliases") or {})
            except Exception:  # noqa: BLE001
                log.debug("line history (live pull) failed", exc_info=True)
            sls = getattr(self, "soccer_live_shadow", None)
            if sls is not None and lg.get("kind") == "soccer":
                try:                          # Oct 16 2026: Marv-priced in-game soccer props, SHADOW only (same pull, 0 credits)
                    sls.on_live_events(key, events, gs)
                except Exception:  # noqa: BLE001
                    log.debug("soccer live props shadow failed", exc_info=True)
            mls = getattr(self, "mlb_live_shadow", None)
            if mls is not None and key == "mlb":
                try:                          # Oct 16 2026: Marv-priced in-game MLB props, SHADOW only (same pull, 0 extra credits)
                    mls.on_live_events(key, events, gs)
                except Exception:  # noqa: BLE001
                    log.debug("mlb live props shadow failed", exc_info=True)
            try:
                self.total_swing.record_pregame(events)       # upcoming games in the same response
            except Exception:  # noqa: BLE001
                log.debug("pregame totals not recorded", exc_info=True)
            summary.setdefault("live_priced", 0)
            summary["live_priced"] += sum(len(b.get("markets") or []) for e in events for b in e.get("bookmakers") or [])
            if self.value_scanner is not None and events:
                try:
                    summary["alerts"] += self.value_scanner.evaluate_live_events(key, events, gs,
                                                                                 odds_ts=self._live_events[ok][0])
                except Exception:  # noqa: BLE001
                    log.exception("live value failed %s", key)
        return n

    def fetch_games(self) -> Dict[str, List[Game]]:
        out: Dict[str, List[Game]] = {}
        now = time.time()
        for key, lg in self.leagues.items():
            fails, retry_at = self._backoff.get(key, (0, 0.0))
            if now < retry_at:
                continue                      # exponential backoff after ESPN failures
            try:
                data = self.scoreboard_fn(lg)
                self._backoff.pop(key, None)
            except Exception as e:
                fails += 1
                wait = min(30 * 2 ** (fails - 1), 600)
                self._backoff[key] = (fails, now + wait)
                log.warning("ESPN %s failed (%d in a row, retry in %ds): %s", key, fails, wait, e)
                continue
            if not data:
                continue
            games = parse_scoreboard(data, key, lg["kind"])
            for g in games:
                self._pregame(g)
                try:                          # opening-line baseline for swing alerts (ajbet/opening.py)
                    _opening.resolve(self.db, g)
                except Exception:  # noqa: BLE001
                    log.debug("opening line not resolved %s", g.event_id, exc_info=True)
                _lh.record_game(self.db, g)   # full line history: opening + every change (pre & live), every scan
            out[key] = games
        return out

    # ------------------------------------------------------------ settlement
    def settle(self, games: Dict[str, List[Game]]) -> List[str]:
        # all leagues incl. disabled ones: MLB/NHL are props-only (not live-scanned) but their paper props settle here
        return settle_open(self.db, {g.event_id: g for gs in games.values() for g in gs},
                           league_table(self.cfg, include_disabled=True), self.summary_fn)

    # ------------------------------------------------------------ main scan
    def scan_once(self) -> Dict[str, Any]:
        games = self.fetch_games()
        self.track_scores(games)
        if self.value_scanner is not None and getattr(self.value_scanner, "live_log", None) is None:
            self.value_scanner.live_log = self.live_log
        summary: Dict[str, Any] = {"leagues": {}, "candidates": [], "alerts": [], "skipped": [],
                                   "odds_calls": 0, "settled": []}
        summary["settled"] = self.settle(games)
        if getattr(self, "soccer_live_shadow", None) is not None:
            try:                               # grade Marv-priced soccer live prop shadows from ESPN finals (free)
                summary["soccer_live_shadow_graded"] = self.soccer_live_shadow.grade(games)
            except Exception:  # noqa: BLE001
                log.debug("soccer live props shadow grading failed", exc_info=True)
        if getattr(self, "mlb_live_shadow", None) is not None:
            try:                               # grade Marv-priced MLB live prop shadows from ESPN finals (free)
                summary["mlb_live_shadow_graded"] = self.mlb_live_shadow.grade(games)
            except Exception:  # noqa: BLE001
                log.debug("mlb live props shadow grading failed", exc_info=True)
        if getattr(self, "wx_ledger", None) is not None:
            try:                               # forecast weather for outdoor NFL games (validation ledger, free)
                summary["wx_ledger"] = self.wx_ledger.record(games)
            except Exception:  # noqa: BLE001
                log.exception("weather ledger failed")
        calls_before = self.odds.calls
        try:
            self.refresh_live_odds(games, summary)
        except Exception:  # noqa: BLE001
            log.exception("live odds refresh failed")
        try:                                   # ~5-min Bovada markout for live alerts (cache only, 0 credits)
            summary["markouts"] = self.live_log.process()
        except Exception:  # noqa: BLE001
            log.exception("live markout failed")
        try:                                   # Pinnacle close snapshot 5-10 min before kickoff (CLV)
            summary["close"] = self.closer.run(games)
        except Exception:  # noqa: BLE001
            log.exception("close snapshot failed")
        try:                                   # Strategy D: ESPN-only line swings (0 credits)
            ls = self.line_swing.scan(games)
            summary["swings"] = ls
        except Exception:  # noqa: BLE001
            log.exception("line swing scan failed")
            summary["swings"] = {}
        try:                                   # game-total swings (live total vs pregame, Odds API cache)
            summary["tswings"] = self.total_swing.scan(games)
        except Exception:  # noqa: BLE001
            log.exception("total swing scan failed")
            summary["tswings"] = {}
        try:                                   # Marvin 2026-10-24 7:50 PM CT: CFB matchup-Under pregame alerts (0 credits)
            if getattr(self, "cfb_mu", None) is None:   # off: CFB_MATCHUP_UNDER=off | data/CFB_MATCHUP_UNDER_OFF
                from .cfb_matchup_under import CfbMatchupUnder
                self.cfb_mu = CfbMatchupUnder(self.cfg, self.db, self.notifier, odds_cache_fn=self._live_events.get,
                                              leagues=self.trends.leagues)
            summary["cfb_matchup_under"] = self.cfb_mu.scan(games)
        except Exception:  # noqa: BLE001
            log.exception("cfb matchup-under scan failed")
        try:                                   # team trends: pregame (~2h out) + live slow-start comebacks
            summary["trends"] = self.trends.scan(games)
        except Exception:  # noqa: BLE001
            log.exception("team trend scan failed")
            summary["trends"] = {}
        if getattr(self, "injury_watch", None) is not None:
            try:                               # key-player status changes after an alert was sent
                summary["injury_updates"] = self.injury_watch.check(games)
            except Exception:  # noqa: BLE001
                log.exception("injury watch failed")

        cands: Dict[str, List[tuple]] = defaultdict(list)   # odds_key -> [(game, kind, side)]
        for key, gs in games.items():
            lg = self.leagues[key]
            live = [g for g in gs if g.state == "in"]
            summary["leagues"][key] = {"games": len(gs), "live": len(live)}
            for g in live:
                if lg["kind"] == "us" and self.sa.get("enabled", True):
                    side = st.is_candidate_fav(g, self.sa, lg)
                    if side and not self.db.has_alert(f"{g.event_id}:fav_comeback:h2h"):
                        cands[lg["odds_key"]].append((g, "fav", side))
                elif lg["kind"] == "soccer" and lg.get("odds_key") and st.is_candidate_soccer(g, self.sb) and \
                        time.time() - self._no_offer.get(g.event_id, 0) > float(self.sb.get("no_offer_retry_s", 600)):
                    if not self.db.has_alert(f"{g.event_id}:soccer_grind:alternate_totals"):
                        cands[lg["odds_key"]].append((g, "soccer", None))
        for ok, lst in cands.items():
            for g, kind, side in lst:
                summary["candidates"].append(f"{g.league}: {g.score_str()} [{g.detail}] ({kind})")

        for odds_key, lst in cands.items():
            hit = self._live_events.get(odds_key)
            # Oct 11 2026: freshness follows the league's own refresh interval (soccer / off-hours slower), so the
            # candidate path does not re-buy what the refresher deliberately skipped
            fresh = hit is not None and time.time() - hit[0] <= self._live_interval_for(lst[0][0].league) + 5
            odds_ts = hit[0] if fresh else None
            if fresh:
                events = hit[1]               # already fetched by the live refresher (no extra credits)
            else:
                if not self.odds.cooldown_ok(odds_key):
                    summary["skipped"].append(f"{odds_key}: cooldown")
                    continue
                try:
                    events = self.odds.live_odds(odds_key, strategy="live") or []
                    odds_ts = time.time()
                    if events:
                        self._live_events[odds_key] = (odds_ts, events)
                except BudgetExceeded as e:
                    summary["skipped"].append(f"{odds_key}: {e}")
                    continue
                lg_key = lst[0][0].league
                if self.value_scanner is not None and events:
                    summary["alerts"] += self.value_scanner.evaluate_live_events(lg_key, events,
                                                                                 games.get(lg_key, []),
                                                                                 odds_ts=odds_ts)
            for g, kind, side in lst:
                ev, how = live_state.match(g, events, self.aliases)
                if kind == "fav":
                    if ev is None:
                        summary["skipped"].append(f"{g.name}: no Odds API match")
                        continue
                    lg = self.leagues[g.league]
                    sig = st.evaluate_fav(g, side, ev, self.sa, lg, self.paper.get("favorite_to_win", 100),
                                          self.aliases, rating=self.rating_for(g),
                                          ratings_enabled=self.team_model is not None)
                    if sig:
                        ls = live_state.LiveState.from_game(g, lg["periods"], lg["period_minutes"], ev, how)
                        sig.details["live_state"] = ls.__dict__
                else:
                    live_total = st.soccer_trigger(g, ev, self.sb)
                    if live_total is None:
                        continue
                    alt = None
                    if ev is not None and ((self.leagues.get(g.league) or {}).get("kind") != "soccer"
                                           or self._soccer_budget_ok(float(self._live_cfg().get(
                                               "soccer_max_share_of_live_daily", 0.6)))):
                        try:
                            alt = self.odds.event_odds(odds_key, ev["id"], "alternate_totals")
                        except BudgetExceeded as e:
                            summary["skipped"].append(f"{g.name} alt totals: {e}")
                    sig = st.evaluate_soccer(g, live_total, alt, self.sb, self.paper.get("soccer_to_win", 20),
                                             main_event=ev)
                    if sig is not None and sig.price is None:
                        self._no_offer[g.event_id] = time.time()
                        summary["skipped"].append(f"{g.name}: no Under >= {sig.line:g} offered (not alerted)")
                        log.info("soccer_grind %s: no under offered at >= %s; no alert, retry in %ss",
                                 g.name, sig.line, self.sb.get("no_offer_retry_s", 600))
                        sig = None
                if sig:
                    sig.details["odds_ts"] = odds_ts
                    sig.details["_ev"] = ev
                    a = self.emit(sig)
                    if a:
                        summary["alerts"].append(a)
        summary["odds_calls"] = self.odds.calls - calls_before
        lu = self.db.last_usage()
        summary["credits_remaining"] = None if lu is None else lu["remaining"]
        return summary

    def emit(self, sig: st.Signal) -> Optional[str]:
        from .friendly import signal_card
        ev = (sig.details or {}).pop("_ev", None)
        ev_min = float(self.cfg.get("strategies", {}).get("value_finder", {}).get("prop_ev_min", 0.05))
        from . import paper_conf as pc     # Marvin 2026-10-03: graded paper results feed confidence/calibration
        pa = pc.assess(self.db, sig.strategy, sig.game.league, sig.fair_p,
                       teams=[sig.selection] if sig.strategy == "fav_comeback" else ())
        if pa is not None and sig.details is not None:
            sig.details["paper_conf"] = pa.record()
        text = signal_card(sig, stake=sig.stake, unit=float(self.cfg.get("strategies", {}).get(
            "value_finder", {}).get("unit", 100)), ev_min=ev_min, paper=pa)
        if sig.price is None:
            log.info("%s %s: no price, not alerted", sig.strategy, sig.game.name)
            return None
        from . import policy
        # Marvin 2026-10-03: only live bet alerts send; a pregame instance of any type is paper only
        pdec = policy.decide(sig.strategy, live=sig.game.state == "in")
        if pdec == "skip":
            log.info("%s %s: alert type off (scorecard)", sig.strategy, sig.game.name)
            return None
        pn = policy.note(sig.strategy)
        if pn:
            text += "\n" + pn
        age_line = price_age_line((sig.details or {}).get("odds_ts"), self.cfg)
        if age_line:
            text += "\n" + age_line
        sig.details.setdefault("matchup", f"{sig.game.away.name} @ {sig.game.home.name}")
        from . import mybook
        live = sig.game.state == "in"
        mp = (sig.details or {}).get("my_price")
        if mybook.is_mine(sig.book) and mp is None:
            mp = sig.price
        demote = False
        wl = "not_my_book"
        if mybook.only() and sig.value and sig.price is not None:
            edge_mine = None if mp is None else sig.fair_p - american_to_prob(mp)
            sec = self.sa if sig.strategy == "fav_comeback" else self.sb
            thr = float(sec.get("value_edge", 0.0) or 0.0)
            if not mybook.is_mine(sig.book) and (edge_mine is None or edge_mine < thr):
                demote = True           # edge only at another book -> paper-only watchlist, not sent
        if sig.strategy == "soccer_grind" and mybook.only() and (not (sig.details or {}).get("offered") or mp is None):
            demote, wl = True, "not_at_bovada"   # round 2: soccer_grind only when Bovada actually offers the line
            sig.details["alert_blocked"] = "Bovada does not offer this line"
        # Oct 16 2026 (Marvin): new soccer leagues paper/shadow first — never Telegram until proven
        if (sig.game.league or "") in set(self.cfg.get("soccer_paper_leagues") or []):
            demote, wl = True, "soccer_paper_league"
            sig.details["alert_blocked"] = "soccer paper league (no Telegram until proven)"
        if pdec == "watch" and not demote:
            demote, wl = True, f"policy_{sig.strategy}"
        if live:
            from . import live_guard        # round 2: HARD stale-price block (age, best-vs-Bovada gap, Pinnacle vs score)
            sig.details["price_age_s"] = (None if sig.details.get("odds_ts") is None
                                          else round(time.time() - float(sig.details["odds_ts"]), 1))
            sig.details["situation"] = live_guard.situation_brief(sig.game)
            ok, why = live_guard.stale_check(sig.game, ev, sig.details.get("odds_ts"), self.cfg,
                                             best_price=sig.price, my_price=mp, market=sig.market)
            if not ok:
                sig.details["stale_block"] = why
                if not demote:
                    demote, wl = True, "stale_price"
                    sig.details["alert_blocked"] = why
                log.info("%s %s stale-price block: %s", sig.strategy, sig.game.name, why)
        if not demote and live:
            from . import live_guard        # heavy-favourite / target-band / volatile one-possession guard
            gp = mp if (mybook.only() and mp is not None) else sig.price
            ok, why = live_guard.check(sig.game, sig.side, sig.market, gp, self.leagues.get(sig.game.league) or {}, self.cfg)
            if not ok:
                demote, wl = True, "live_guard"
                sig.details["alert_blocked"] = why
                log.info("%s %s blocked by live guard: %s", sig.strategy, sig.game.name, why)
        if demote:
            aid = self.db.insert_alert(dict(
                dedupe_key=sig.dedupe_key, event_id=sig.game.event_id, league=sig.game.league,
                strategy=sig.strategy, market=sig.market, selection=sig.selection, line=sig.line,
                price=sig.price, book=sig.book, fair_p=sig.fair_p, market_p=sig.market_p, edge=sig.edge,
                value=0, trigger="watchlist", message=text,
                details_json=__import__("json").dumps(dict(sig.details, watchlist=[wl]), default=str)))
            # paper at the price Marvin could take (Bovada under only_my_book); round 2: only edge >= 0
            bp, bb = (mp, mybook.key()) if (mybook.only() and mp is not None) else (sig.price, sig.book)
            edge_bp = None if (bp is None or sig.fair_p is None) else sig.fair_p - american_to_prob(bp)
            if aid is not None and sig.stake is not None and edge_bp is not None and edge_bp >= 0:
                from .oddsmath import american_to_decimal as _dec
                stake = round(sig.stake, 2)
                self.db.insert_paper_bet(dict(
                    alert_id=aid, event_id=sig.game.event_id, league=sig.game.league, strategy=sig.strategy,
                    market=sig.market, selection=sig.selection, side=sig.side, line=sig.line, price=bp,
                    book=bb, stake=stake, to_win=round(stake * (_dec(bp) - 1), 2), tag=f"watchlist:{wl}",
                    sport=sig.game.league, commence=sig.game.start))
            if live and aid is not None:
                self.live_log.record(aid, sig.game, sig.strategy, sig.market, sig.selection, sig.line, False,
                                     sig.details.get("odds_ts"), ev)
            return None
        if live:
            # Marvin 2026-10-03: ESPN gamecast. Opponent driving in scoring range -> hold one drive; ESPN win prob
            from . import gamecast
            tside = sig.selection if sig.market == "totals" else None
            act, hnote = gamecast.hold_check(self.db, sig.game, f"{sig.strategy}:{sig.dedupe_key}",
                                             side=sig.side if sig.market != "totals" else None,
                                             total_side=(tside or "").split()[0] if tside else None,
                                             strategy=sig.strategy)
            if act == "hold":
                return None
            sit = gamecast.situation(sig.game)
            if sit is not None:
                sig.details["gamecast"] = sit.brief()
            xl = gamecast.cross_line(sig.fair_p, sig.game, sig.side) if sig.market != "totals" else ""
            if xl:
                text += "\n" + xl
            if hnote:
                text += "\n" + hnote
                sig.details["hold_note"] = hnote
        aid = self.db.insert_alert(dict(
            dedupe_key=sig.dedupe_key, event_id=sig.game.event_id, league=sig.game.league,
            strategy=sig.strategy, market=sig.market, selection=sig.selection, line=sig.line,
            price=sig.price, book=sig.book, fair_p=sig.fair_p, market_p=sig.market_p, edge=sig.edge,
            value=int(sig.value), trigger="; ".join(sig.triggers), message=text,
            details_json=__import__("json").dumps(sig.details, default=str)))
        if aid is None:
            return None  # duplicate
        record_paper_bet(self.db, sig, aid)
        if live:
            self.live_log.record(aid, sig.game, sig.strategy, sig.market, sig.selection, sig.line, False,
                                 sig.details.get("odds_ts"), ev)
        if self.notifier.send(text, alert_id=aid):
            self.db.mark_sent(aid)
            if live:
                self.live_log.mark_sent(aid)
        return text


def _maybe_final(commence: Optional[str], hours: float = 2.5) -> bool:
    """True when a game that started at `commence` (ISO) could be final by now (or the time is unknown)."""
    if not commence:
        return True
    try:
        from datetime import datetime, timedelta, timezone
        st = datetime.fromisoformat(str(commence).replace("Z", "+00:00"))
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= st + timedelta(hours=hours)
    except ValueError:
        return True


def settle_open(db: DB, idx: Dict[str, Game], leagues: Dict[str, Dict[str, Any]],
                summary_fn: SummaryFn) -> List[str]:
    """Settle open paper bets from ESPN finals; props use the /summary box score (free)."""
    msgs = []
    summaries: Dict[str, Any] = {}
    for bet in db.open_bets():
        bet = dict(bet)
        g = idx.get(bet["event_id"])
        lg = leagues.get(bet["league"])
        from .paper import needs_summary
        is_prop = needs_summary(bet["market"] or "")
        if g is not None and g.state != "post":
            continue
        if g is None and not _maybe_final(bet.get("commence")):
            continue          # not on today's live scoreboard (props-only league) and not finished yet: no fetch
        if (g is None or is_prop) and lg:
            if bet["event_id"] not in summaries:
                try:
                    summaries[bet["event_id"]] = summary_fn(lg, bet["event_id"]) or {}
                except Exception as e:
                    log.warning("settle lookup failed %s: %s", bet["event_id"], e)
                    summaries[bet["event_id"]] = {}
            if g is None:
                g = game_from_summary(summaries[bet["event_id"]], bet["league"], lg["kind"])
        if g is None:
            continue
        res = settle_with_game(db, bet, g, summaries.get(bet["event_id"]))
        if res:
            what = f"{bet.get('player') + ' ' if bet.get('player') else ''}{bet['selection']}"
            msgs.append(f"settled #{bet['id']} {what} ({bet['strategy']}): {res.upper()}")
    return msgs


def format_summary(s: Dict[str, Any]) -> str:
    lines = ["AJ Bet Bot scan:"]
    lines.append("  ESPN: " + ", ".join(f"{k} {v['live']}/{v['games']} live" for k, v in s["leagues"].items()))
    lines.append(f"  candidates: {len(s['candidates'])}")
    lines += [f"    - {c}" for c in s["candidates"]]
    lines.append(f"  alerts: {len(s['alerts'])}; Odds API calls: {s['odds_calls']}; "
                 f"credits remaining: {s['credits_remaining'] if s['credits_remaining'] is not None else 'unknown (no call)'}")
    lines += [f"  skipped: {x}" for x in s["skipped"]]
    lines += [f"  {x}" for x in s["settled"]]
    return "\n".join(lines)
