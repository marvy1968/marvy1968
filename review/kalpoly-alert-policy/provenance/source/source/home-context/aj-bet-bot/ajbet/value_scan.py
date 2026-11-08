"""Strategy C orchestration: scope (ESPN, free) -> scan windows -> featured + props (credits)
-> market/model value findings -> dedupe -> paper bets -> alerts (dry-run unless enabled)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from . import nba_props_tg as _nbap
from . import edge_open as _eo
from .alerts import Notifier
from .db import DB, now
from .espn import ESPNClient, Game, parse_scoreboard
from .markets import match_event, name_sim
from .oddsapi import BudgetExceeded, OddsAPI
from .oddsmath import american_to_decimal, fmt_american
from .rankings import RANKINGS_PATH, ranked_ids, top_n
from .ratings import SRS, results_from_scoreboard
from . import valuefinder as vf
from . import policy

log = logging.getLogger(__name__)
SPORT_NAMES = {"nfl": "NFL", "ncaaf": "NCAAF", "wnba": "WNBA", "nba": "NBA"}
MARKET_NAMES = {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}

FetchFn = Callable[[str, Optional[Dict[str, Any]]], Optional[Dict[str, Any]]]


def _ts(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None



def _tt_over(f) -> bool:
    """Oct 18 2026: full-game team-total Over on NFL/NCAAF (pregame ⭐⭐ bar, policy.TT_OVER_PREGAME_MIN_STARS)."""
    return (getattr(f, "market", "") == "team_totals" and getattr(f, "selection", "") == "Over"
            and str(getattr(f, "sport", "")).lower() in policy.TT_OVER_SPORTS)

class ValueScanner:
    def __init__(self, cfg: Dict[str, Any], db: DB, odds: OddsAPI, notifier: Notifier,
                 fetch: Optional[FetchFn] = None, clock: Callable[[], datetime] = now, printer=print,
                 store=None):
        self.cfg, self.db, self.odds, self.notifier, self.print = cfg, db, odds, notifier, printer
        self.vc = cfg["strategies"]["value_finder"]
        from . import mybook
        mybook.configure(cfg)
        from .registry import build_registry
        self.registry = build_registry(cfg)
        enabled = self.registry.enabled_sports("value_finder")
        want = self.vc.get("sports")
        self.sports = {k: s.as_league() for k, s in enabled.items() if s.kind == "us" and (not want or k in want)}
        # Props-only sports (value_finder.props_only_sports, e.g. NBA): player props per event ONLY - no featured
        # windows, no team props, no in-game value (live behaviour unchanged). Each sport bills its own allocation
        # odds_api.allocations.props_<sport> (missing allocation => the sport is skipped: fail closed).
        # Oct 3 2:22 PM (Marvin: props on for every sport): MLB/NHL are registered `enabled: false, props_only: true`
        # (no live scanning, no live odds pulls, props only); the token "soccer" = every soccer league (one shared
        # allocation props_soccer and markets value_finder.props_markets.soccer).
        self.props_only: set = set()
        want_po = list(self.vc.get("props_only_sports") or [])
        if "soccer" in want_po:
            want_po += [k for k, s in self.registry.sports.items() if s.kind == "soccer" and s.enabled]
        for k in want_po:
            spec = self.registry.sports.get(k)
            if spec is None or spec.kind not in ("us", "soccer") or not spec.odds_key:
                continue
            if not (spec.enabled or (spec.extra or {}).get("props_only")):
                continue
            self.props_only.add(k)
            self.sports.setdefault(k, spec.as_league())
        self.tz = ZoneInfo(cfg.get("timezone", "America/Chicago"))
        espn = ESPNClient()
        self.fetch = fetch or (lambda path, params: espn.s.get(
            f"https://site.api.espn.com/apis/site/v2/sports/{path}", params=params or {}, timeout=20).json())
        self.clock = clock
        self.aliases = cfg.get("team_aliases") or {}
        self.top30: Dict[str, int] = {}
        self._srs: Dict[str, SRS] = {}
        self._top_list: Optional[List[Dict[str, Any]]] = None
        self._top_ts = self.clock()
        # Ratings / player models (ported MarchEdgePro adapters). `store` = features.FeatureStore.
        self.store = store
        self.team_model = None
        if store is not None:
            from .features import TeamModel
            self.team_model = TeamModel(store, cfg)
        self.pc = cfg.get("models", {}).get("players", {})
        self._pidx: Dict[str, Any] = {}

    # ------------------------------------------------------------------ scope (free)
    def load_top30(self) -> List[Dict[str, Any]]:
        if self._top_list is not None and self.clock() - self._top_ts < timedelta(hours=6):
            return self._top_list
        data = self.fetch(RANKINGS_PATH, None) or {}
        top = top_n(data, self.vc.get("ncaaf_top_n", 30), union_coaches=self.vc.get("ncaaf_union_coaches", False))
        self.top30 = ranked_ids(top)
        self._top_list, self._top_ts = top, self.clock()
        return top

    def props_in_scope(self, sport: str, g: Game) -> bool:
        """Per-event props / team props stay top-30 for NCAAF (vc.ncaaf_props_scope, default top_n) even though
        featured lines, live alerts and trends cover every FBS game: props cost credits per event."""
        if sport != "ncaaf" or self.vc.get("ncaaf_props_scope", "top_n") == "all":
            return True
        if not self.top30:
            try:
                self.load_top30()
            except Exception:  # noqa: BLE001
                return True
        return not self.top30 or g.home.id in self.top30 or g.away.id in self.top30

    def scope_games(self, us_only: bool = False) -> Dict[str, List[Game]]:
        out: Dict[str, List[Game]] = {}
        horizon = self.clock() + timedelta(hours=self.vc.get("lookahead_hours", 36))
        for key, sc in self.sports.items():
            if us_only and sc.get("kind", "us") != "us":
                continue
            try:
                data = self.fetch(f"{sc['espn']}/scoreboard", sc.get("params")) or {}
            except Exception as e:
                log.warning("ESPN %s failed: %s", key, e)
                data = {}
            games = []
            for g in parse_scoreboard(data, key, sc.get("kind") or "us"):
                if g.state == "post":
                    continue
                st = _ts(g.start)
                if g.state == "pre" and st and st > horizon:
                    continue
                if sc.get("scope", "all") != "all":
                    if sc.get("scope") == "ncaaf_top_n" and not self.top30:
                        self.load_top30()
                    if not self.registry.in_scope(key, g, {"top30": self.top30}):
                        continue
                games.append(g)
            out[key] = games   # zero games (e.g. WNBA off-day) is fine
        return out

    # ------------------------------------------------------------------ model (free)
    def srs(self, sport: str) -> Optional[SRS]:
        day = self.clock().astimezone(self.tz).date().isoformat()
        if (sport, day) in self._srs:
            return self._srs[(sport, day)]
        sc = self.sports[sport]
        mc = self.vc.get("model", {})
        k = f"results:{sport}:{day}"
        _, cached = self.db.kv_get(k)
        if cached is None:
            cached = []
            try:
                year = self.clock().year
                if sport == "wnba":
                    pages = [self.fetch(f"{sc['espn']}/scoreboard", {"dates": year, "limit": 1000})]
                else:
                    cur = self.fetch(f"{sc['espn']}/scoreboard", sc.get("params")) or {}
                    wk = int((cur.get("week") or {}).get("number") or 1)
                    pages = [self.fetch(f"{sc['espn']}/scoreboard",
                                        dict(sc.get("params") or {}, dates=year, seasontype=2, week=w))
                             for w in range(1, wk + 1)]
                for p in pages:
                    for r in results_from_scoreboard(p or {}):
                        cached.append(r.__dict__)
                self.db.kv_set(k, cached)
            except Exception as e:
                log.warning("ratings fetch failed for %s: %s", sport, e)
        from .ratings import Result
        res = [Result(**r) for r in cached]
        if not res:
            self._srs[(sport, day)] = None
            return None
        self._srs[(sport, day)] = SRS(res, hfa=mc.get("hfa", {}).get(sport, 2.0), ridge=mc.get("ridge", 2.0),
                               margin_cap=mc.get("margin_cap", {}).get(sport))
        return self._srs[(sport, day)]

    # ------------------------------------------------------------------ windows
    def _todays_kicks(self, games: List[Game]) -> List[datetime]:
        local = self.clock().astimezone(self.tz)
        ts = [_ts(g.start) for g in games if g.state == "pre" and _ts(g.start)]
        return sorted(t for t in ts if t.astimezone(self.tz).date() == local.date())

    def due_windows(self, sport: str, games: List[Game]) -> List[str]:
        """Featured-market windows due now for `sport` (any due window => ONE featured call)."""
        local = self.clock().astimezone(self.tz)
        day = local.date().isoformat()
        kicks = self._todays_kicks(games)
        if not kicks:
            return []
        first, last = kicks[0], kicks[-1]
        try:   # 2026-10-24 (Marvin): Saturday = CFB priority -> earlier morning + half-hourly CFB pregame lines
            from . import sat_priority as _sp
            sp = _sp.pregame(sport, self.cfg)
        except Exception:  # noqa: BLE001
            sp = {}
        hh, mm = map(int, (sp.get("morning_time") or self.vc.get("morning_time", "10:00")).split(":"))
        morning_at = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        due = []
        for w in self.vc.get("windows", ["prekick"]):
            if w == "morning":
                if morning_at <= local < last and not self.db.window_done(day, sport, "morning"):
                    due.append("morning")
            elif w == "hourly":
                name = f"h{local.hour:02d}" + (("a" if local.minute < 30 else "b") if sp.get("half_hourly") else "")
                if morning_at <= local < last and not self.db.window_done(day, sport, name):
                    due.append(name)
            elif w == "prekick":
                if first - timedelta(minutes=self.vc.get("prekick_minutes", 90)) <= local < first \
                        and not self.db.window_done(day, sport, "prekick"):
                    due.append("prekick")
        return due

    def due_prop_snapshots(self, games: List[Game], force: bool = False) -> List[Tuple[Game, str]]:
        """(game, snapshot) pairs due now: 'morning' (from morning_time, until the game's prekick
        window) and 'prekick' (within props_prekick_minutes of kickoff). Today's games only."""
        local = self.clock().astimezone(self.tz)
        day = local.date().isoformat()
        hh, mm = map(int, self.vc.get("morning_time", "10:00").split(":"))
        morning_at = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        pk = timedelta(minutes=self.vc.get("props_prekick_minutes", 75))
        snaps = self.vc.get("props_snapshots", ["morning", "prekick"])
        done = set(self.db.props_fetched(day))
        out = []
        for g in games:
            st = _ts(g.start)
            if g.state != "pre" or st is None:
                continue
            if force:
                if st - self.clock() <= timedelta(hours=self.vc.get("props_lookahead_hours", 14)):
                    out.append((g, "manual"))
                continue
            if st.astimezone(self.tz).date() != local.date():
                continue
            in_pk = st - pk <= local < st
            fams = ["px:"] if g.league in getattr(self, "props_only", ()) else \
                [""] + (["team:"] if self.team_props_on(g.league) else [])
            pending = lambda snap: any(f"{g.event_id}:{t}{snap}" not in done for t in fams)
            if "prekick" in snaps and in_pk and pending("prekick"):
                out.append((g, "prekick"))
            elif "morning" in snaps and not in_pk and local >= morning_at and local < st - pk \
                    and pending("morning"):
                out.append((g, "morning"))
        return out

    # ------------------------------------------------------------------ main
    def run(self, force: bool = False, sports: Optional[List[str]] = None, props_events: Optional[int] = None,
            props_markets: Optional[Dict[str, List[str]]] = None, featured: bool = True,
            team_props_markets: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"scope": {}, "calls": [], "findings": [], "alerts": [], "skipped": [],
                                   "clv_updates": 0, "top30": []}
        if any(sc.get("scope") == "ncaaf_top_n" for sc in self.sports.values()):
            summary["top30"] = self.load_top30()
        games = self.scope_games()
        day = self.clock().astimezone(self.tz).date().isoformat()
        prop_jobs: List[Tuple[Tuple, str, Game, Dict[str, Any], str]] = []
        team_jobs: List[Tuple[Tuple, str, Game, Dict[str, Any], str]] = []
        px_jobs: List[Tuple[Tuple, str, Game, Dict[str, Any], str]] = []      # props-only sports (NBA)
        prio = self.vc.get("props_priority", ["nfl", "wnba", "ncaaf"])
        for sport, gs in games.items():
            if sports and sport not in sports:
                continue
            summary["scope"][sport] = [f"{g.away.name} @ {g.home.name} ({self.fmt_time(g.start)}, {g.state})"
                                       for g in gs]
            if not gs:
                continue
            po = sport in self.props_only
            due = (["manual"] if force else self.due_windows(sport, gs)) if featured and not po else []
            snaps = self.due_prop_snapshots(gs, force) if (props_events is None or props_events > 0) else []
            if po:
                snaps = [(g, sn) for g, sn in snaps if self.props_only_game_ok(sport, g)]
            if not due and not snaps:
                continue
            sc = self.sports[sport]
            events: List[Dict[str, Any]] = []
            if due:
                try:
                    n0 = self.odds.calls
                    events = self.odds.live_odds(sc["odds_key"], strategy="value") or []
                    if self.odds.calls > n0:
                        summary["calls"].append(f"{sc['odds_key']} featured ({','.join(due)})")
                        self.db.kv_set(f"featured:{sport}:{day}:{','.join(due)}", events)
                        self.db.kv_set(f"lastfeatured:{sport}", events)      # Telegram /game cache
                except BudgetExceeded as e:
                    summary["skipped"].append(f"{sport} featured: {e}")
                for w in due:
                    if w != "manual":
                        self.db.mark_window(day, sport, w)
            if not events and snaps:
                try:
                    events = self.odds.events(sc["odds_key"]) or []   # free: event ids for props
                except BudgetExceeded:
                    events = []
                if po and events:
                    self.db.kv_set(f"lastevents:{sport}", events)          # Telegram /props event lookup
            ev_for = {}
            for g in gs:
                ev = match_event(g.home.name, g.away.name, g.start, events, self.aliases)
                if ev:
                    ev_for[g.event_id] = ev
            if events and events[0].get("bookmakers") is not None:
                summary["clv_updates"] += self.update_clv(events)
                try:                           # line history: pregame movement from morning/hourly/prekick snapshots
                    from . import line_history as _lh
                    for g in gs:
                        if g.state == "pre" and g.event_id in ev_for:
                            _lh.record_oddsapi(self.db, g, ev_for[g.event_id], self.aliases, "pre")
                except Exception:  # noqa: BLE001
                    log.debug("line history (featured) failed", exc_info=True)
                try:
                    from .total_swing import record_pregame_totals
                    record_pregame_totals(self.db, events)     # baseline for total-swing alerts (0 credits)
                except Exception:  # noqa: BLE001
                    log.debug("pregame totals not recorded", exc_info=True)
                for g in gs:
                    if g.state == "pre" and g.event_id in ev_for:
                        summary["findings"] += self.evaluate_featured(sport, g, ev_for[g.event_id])
            for g, snap in snaps:
                if g.event_id not in ev_for or not self.props_in_scope(sport, g):
                    continue
                st = _ts(g.start) or self.clock()
                rank = min(self.top30.get(g.home.id, 99), self.top30.get(g.away.id, 99)) if sport == "ncaaf" else 0
                key = (0 if snap == "prekick" else 1, prio.index(sport) if sport in prio else 9, rank, st)
                (px_jobs if po else prop_jobs).append((key, sport, g, ev_for[g.event_id], snap))
                if self.team_props_on(sport):
                    team_jobs.append((key, sport, g, ev_for[g.event_id], snap))
        # props then team props: closing (prekick) snapshots first, then priority; each family has
        # its own daily event cap and its own credit allocation (strategy label value / team_props)
        families = [("props", prop_jobs, self.vc.get("props_events_per_day", 80), "value", "")]
        if self.vc.get("team_props", True) and team_props_markets != {}:
            families.append(("team props", team_jobs, self.vc.get("team_props_events_per_day", 60), "team_props", "team:"))
        if px_jobs:
            families.append(("props-only", px_jobs, self.vc.get("props_only_events_per_day", 20), None, "px:"))
        done_keys = set(self.db.props_fetched(day))
        for fam, jobs, cap0, strat, tag in families:
            cap = cap0 if props_events is None else props_events
            used = len([x for x in done_keys if _fam_tag(x) == tag])
            left = max(0, cap - used) if props_events is None else cap
            for _, sport, g, ev, snap in sorted(jobs, key=lambda x: x[0]):
                if snap != "manual" and f"{g.event_id}:{tag}{snap}" in done_keys:
                    continue
                if left <= 0:
                    summary["skipped"].append(f"{fam} cap reached ({cap}/day)")
                    break
                if tag == "px:":
                    mk0 = (props_markets or {}).get(sport) or self.px_markets(sport)
                    pcap = self.px_cap(sport)
                    if pcap is not None and snap != "manual" and props_events is None and \
                            self.px_used(sport, day) >= pcap:
                        summary["skipped"].append(f"{fam} {self.px_group(sport)} cap reached ({pcap}/day)")
                        continue
                    cl = self._odds_client()
                    units = cl.region_units() if cl is not None else 1
                    ok, why = self.props_only_budget_ok(sport, len(mk0) * units, day, ev, snap)
                    if not ok:
                        summary["skipped"].append(f"{fam} {sport} {g.name}: {why}")
                        log.debug("props-only %s %s skipped: %s", sport, g.name, why)
                        if why.startswith("no Bovada") and snap != "manual":
                            self.db.mark_props(day, f"{g.event_id}:{tag}{snap}")
                        continue
                if tag == "team:":
                    mkts = (team_props_markets or {}).get(sport) or self.team_markets(sport)
                elif tag == "px:":
                    mkts = (props_markets or {}).get(sport) or self.px_markets(sport)
                else:
                    mkts = (props_markets or {}).get(sport) or self.vc.get("props_markets", {}).get(sport) or []
                if not mkts:
                    continue
                n0 = self.odds.calls
                try:
                    pev = self.odds.event_odds(self.sports[sport]["odds_key"], ev["id"], ",".join(mkts),
                                               strategy=strat or f"props_{self.px_group(sport)}")
                except BudgetExceeded as e:
                    summary["skipped"].append(f"{fam} {g.name}: {e}")
                    if tag == "px:":
                        continue          # one props-only sport hitting its cap must not stop the others
                    break
                if self.odds.calls > n0:
                    summary["calls"].append(f"{fam} {g.away.abbr}@{g.home.abbr} ({snap}) {len(mkts)} mkts")
                if snap != "manual":
                    self.db.mark_props(day, f"{g.event_id}:{tag}{snap}")
                    if tag == "px:" and self.odds.calls > n0:
                        self.px_count(sport, day)
                left -= 1
                if pev and tag == "px:":
                    has_mine = _has_my_book(pev)
                    self.db.kv_set(f"pxbook:{ev['id']}:{day}", {"my_book": has_mine})
                    self.px_nobook_track(sport, has_mine)
                    if not has_mine:          # Bovada must be in the response; otherwise nothing to price/track
                        summary["skipped"].append(f"{fam} {g.away.abbr}@{g.home.abbr} ({snap}): no Bovada props")
                        continue
                if pev:
                    self.db.kv_set(f"{'teamprops' if tag == 'team:' else 'props'}:{ev['id']}:{day}:{snap}", pev)
                    self.db.kv_set(f"lastprops:{sport}:{ev['id']}:{'team' if tag == 'team:' else 'player'}", pev)
                    summary["clv_updates"] += self.update_prop_clv(pev)
                    summary["findings"] += self.evaluate_props(sport, g, pev, mkts)
        # Oct 11 2026 (Marvin, explicit: 'in-game props ON for sure'): poll player props DURING live games
        # (value_finder.live_props, own allocation odds_api.allocations.live_props). Scheduled scans only.
        if not force and props_events is None and featured:
            try:
                self.run_live_props(games, summary, sports)
            except Exception:  # noqa: BLE001
                log.exception("in-game props failed")
        flagged = [f for f in summary["findings"] if f.market_flag or f.model_flag]
        flagged.sort(key=lambda f: -max(f.ev, f.model_ev or -1))
        for f, g in [(f, f.extra.get("_game")) for f in flagged[: self.vc.get("max_alerts_per_scan", 8)]]:
            a = self.emit(f, g)
            if a:
                summary["alerts"].append(a)
        summary["watchlist"] = []
        for f in self.watch_picks(summary["findings"]):
            w = self.emit_watch(f, f.extra.get("_game"))
            if w:
                summary["watchlist"].append(w)
        summary["n_findings"] = len(summary["findings"])
        summary["n_flagged"] = len(flagged)
        summary["flagged"] = flagged
        lu = self.db.last_usage(self.odds.key_label)
        summary["credits_remaining"] = None if lu is None else lu["remaining"]
        summary["key"] = self.odds.key_label
        return summary

    def team_props_on(self, sport: str) -> bool:
        if sport in getattr(self, "props_only", ()):
            return False
        return bool(self.vc.get("team_props", True)) and bool(self.team_markets(sport))

    # ------------------------------------------------------------------ in-game props (Oct 11 2026)
    LIVE_PROPS = {"enabled": False, "sports": [], "interval_s": 900, "interval_overrides": {}, "max_polls_per_game": 10,
                  "min_minutes_after_start": 10, "max_games_per_day": {}, "nobook_after": 2, "markets": {},
                  "offhours_skip": True, "reserve": 5000}

    def lp_cfg(self) -> Dict[str, Any]:
        c = {**self.LIVE_PROPS, **(self.vc.get("live_props") or {})}
        # Oct 11 2026 (Marvin): Sat/Sun (CT) are the big days -> live_props.weekend overrides (interval_s,
        # interval_overrides, max_polls_per_game, max_games_per_day, ...) replace the weekday values on those days.
        wk = c.get("weekend") or {}
        if wk and self.clock().astimezone(self.tz).weekday() in (5, 6):
            c = {**c, **wk}
        try:   # 2026-10-24 (Marvin): Saturday = CFB priority -> sat_cfb_priority.live_props on top (dicts merged)
            from . import sat_priority as _sp
            so = _sp.live_props_overrides(self.cfg)
            if so:
                c = {**c, **{k: ({**(c.get(k) or {}), **v} if isinstance(v, dict) and isinstance(c.get(k), dict) else v)
                             for k, v in so.items()}}
        except Exception:  # noqa: BLE001
            pass
        return c

    def lp_interval(self, sport: str) -> float:
        c = self.lp_cfg()
        ov = c.get("interval_overrides") or {}
        grp = self.px_group(sport)
        return float(ov.get(sport, ov.get(grp, c.get("interval_s", 900))))

    def lp_markets(self, sport: str) -> List[str]:
        mk = self.lp_cfg().get("markets") or {}
        return list(mk.get(sport) or mk.get(self.px_group(sport)) or [])

    def lp_offhours(self, sport: str) -> bool:
        """live_odds.offhours window (CT) - in-game props pause there unless the sport is exempt (NFL/NCAAF)."""
        oh = (self.cfg.get("live_odds") or {}).get("offhours") or {}
        ex = oh.get("exempt") or []
        if not self.lp_cfg().get("offhours_skip", True) or not oh.get("enabled", False) or sport in ex \
                or self.px_group(sport) in ex:
            return False
        t = self.clock().astimezone(self.tz)
        m = t.hour * 60 + t.minute
        sh, sm = (int(x) for x in str(oh.get("start", "00:00")).split(":"))
        eh, em = (int(x) for x in str(oh.get("end", "10:00")).split(":"))
        a, b = sh * 60 + sm, eh * 60 + em
        return (a <= m < b) if a <= b else (m >= a or m < b)

    def lp_track_score(self, g: Game) -> Optional[float]:
        """Epoch of the last score change WE observed for a live game (None until one is seen; a restart does not
        count as a change). Bovada prop quotes not updated since then are treated as stale (paper only)."""
        sc = getattr(self, "_lp_scores", None)
        if sc is None:
            sc = self._lp_scores = {}
        cur = (g.away.score, g.home.score)
        prev = sc.get(g.event_id)
        if prev is None:
            sc[g.event_id] = (cur, None)
        elif prev[0] != cur:
            sc[g.event_id] = (cur, self.clock().timestamp())
        return sc[g.event_id][1]

    def lp_budget_ok(self, cost: float) -> Tuple[bool, str]:
        """Hard gates BEFORE an in-game props call: allocation live_props must exist (fail closed), its daily/monthly
        allocation (+ global daily cap) via the guard, key reserve, and the all-strategy month ceiling."""
        from .db import month_utc
        oc = self.cfg.get("odds_api") or {}
        if not ((oc.get("allocations") or {}).get("live_props") or {}).get("daily"):
            return False, "no odds_api.allocations.live_props (fail closed)"
        cl = self._odds_client()
        if cl is None:
            return False, "no Odds API client (fail closed)"
        label = cl.key_label
        ok, why = cl.guard.check(cost, "live_props", label)
        if not ok:
            return False, why
        rem = cl.guard.remaining(label)
        if rem is not None:
            floor, _ = cl.guard._limits(label)
            need = floor + float(self.lp_cfg().get("reserve", 5000))
            if rem - cost < need:
                return False, f"reserve: remaining {rem:.0f} - {cost:.0f} < floor+reserve {need:.0f}"
        ceil = (self.vc.get("props_only_guard") or {}).get("month_ceiling")
        if ceil is not None:
            labels = [k.get("label", k.get("env")) for k in (oc.get("keys") or [])] or [label]
            used = sum(self.db.credits_used_in_month(month_utc(), key_label=lb) for lb in labels if lb)
            if used + cost > float(ceil):
                return False, f"month ceiling: used {used:.0f} + {cost:.0f} > {float(ceil):.0f}"
        return True, "ok"

    def run_live_props(self, games: Dict[str, List[Game]], summary: Dict[str, Any],
                       sports: Optional[List[str]] = None) -> int:
        """In-game player props: every live (ESPN state 'in') game of a live_props sport is re-priced every
        lp_interval(sport) seconds (one per-event call = len(markets) credits), at most max_polls_per_game polls and
        max_games_per_day games per sport; a game whose responses carry no Bovada props nobook_after times in a row
        stops polling. Findings are tagged in_game -> policy LIVE_PROP_MIN_STARS (⭐⭐) bar, live dedupe/re-alert
        rules, live edge cap. Returns the number of paid calls."""
        c = self.lp_cfg()
        if not c.get("enabled", False):
            return 0
        want = set(c.get("sports") or [])
        if "soccer" in want:
            want |= {k for k in self.sports if self.px_group(k) == "soccer"}
        now_ = self.clock()
        t = now_.timestamp()
        day = now_.astimezone(self.tz).date().isoformat()
        prio = list(c.get("priority") or self.vc.get("props_priority") or [])
        order = sorted(games.items(), key=lambda kv: prio.index(kv[0]) if kv[0] in prio else 99)
        calls = 0
        for sport, gs in order:
            if sports and sport not in sports:
                continue
            if sport not in want or sport not in self.sports:
                continue
            live = [g for g in gs if g.state == "in"]
            for g in live:
                self.lp_track_score(g)
            live = [g for g in live if self.props_in_scope(sport, g) and self.props_only_game_ok(sport, g)]
            if not live or self.lp_offhours(sport):
                continue
            mkts = self.lp_markets(sport)
            if not mkts:
                continue
            every = self.lp_interval(sport)
            min_after = timedelta(minutes=float(c.get("min_minutes_after_start", 10)))
            due = []
            for g in live:
                _, st = self.db.kv_get(f"lpx:{g.event_id}:{day}")
                st = st or {}
                if st.get("off") or int(st.get("n", 0)) >= int(c.get("max_polls_per_game", 10)):
                    continue
                if t - float(st.get("t", 0)) < every:
                    continue
                start = _ts(g.start)
                if start is not None and now_ - start < min_after:
                    continue
                rank = min(self.top30.get(g.home.id, 99), self.top30.get(g.away.id, 99)) if sport == "ncaaf" else 0
                due.append(((0 if st.get("n") else 1, rank, float(st.get("t", 0))), g, st))
            if not due:
                continue
            _, seen = self.db.kv_get(f"lpxgames:{sport}:{day}")
            seen = list(seen or [])
            gcap = (c.get("max_games_per_day") or {}).get(sport, (c.get("max_games_per_day") or {}).get(self.px_group(sport)))
            try:
                events = self.odds.events(self.sports[sport]["odds_key"]) or []    # free: ids incl. in-play events
            except BudgetExceeded:
                events = []
            cl = self._odds_client()
            units = cl.region_units() if cl is not None else 1
            stop = False
            for _, g, st in sorted(due, key=lambda x: x[0]):
                if g.event_id not in seen and gcap is not None and len(seen) >= int(gcap):
                    log.debug("in-game props %s %s skipped: %s games/day cap", sport, g.name, gcap)
                    continue
                ev = match_event(g.home.name, g.away.name, g.start, events, self.aliases)
                if not ev:
                    continue
                ok, why = self.lp_budget_ok(len(mkts) * units)
                if not ok:
                    summary["skipped"].append(f"in-game props {g.name}: {why}")
                    stop = True
                    break
                n0 = self.odds.calls
                try:
                    pev = self.odds.event_odds(self.sports[sport]["odds_key"], ev["id"], ",".join(mkts),
                                               strategy="live_props")
                except BudgetExceeded as e:
                    summary["skipped"].append(f"in-game props {g.name}: {e}")
                    stop = True
                    break
                paid = self.odds.calls > n0
                calls += int(paid)
                if g.event_id not in seen:
                    seen.append(g.event_id)
                    self.db.kv_set(f"lpxgames:{sport}:{day}", seen)
                st = dict(st, n=int(st.get("n", 0)) + 1, t=t)
                if paid:
                    summary["calls"].append(f"in-game props {g.away.abbr}@{g.home.abbr} #{st['n']} {len(mkts)} mkts")
                if not pev or not _has_my_book(pev):
                    st["miss"] = int(st.get("miss", 0)) + 1
                    if st["miss"] >= int(c.get("nobook_after", 2)):
                        st["off"] = True
                        summary["skipped"].append(f"in-game props {g.away.abbr}@{g.home.abbr}: no Bovada live props, stopped")
                    self.db.kv_set(f"lpx:{g.event_id}:{day}", st)
                    continue
                st["miss"] = 0
                self.db.kv_set(f"lpx:{g.event_id}:{day}", st)
                self.db.kv_set(f"lastprops:{sport}:{ev['id']}:player", pev)
                fs = self.evaluate_live_props(sport, g, pev, mkts, t)
                summary["findings"] += fs
                summary.setdefault("live_props", []).append(f"{g.away.abbr}@{g.home.abbr}: {len([f for f in fs if f.market_flag])} flagged")
            if stop:
                break
        return calls

    def evaluate_live_props(self, sport: str, g: Game, pev: Dict[str, Any], mkts: List[str],
                            odds_ts: Optional[float] = None) -> List[vf.Finding]:
        """In-game prop findings (consensus of the other live books, same math as pregame). No pregame player
        model / injury logic. Bovada's quote counts as stale (-> 0 stars, paper only) when it was not updated since
        the last score change we saw, or lags the freshest book on that market by > live_props.max_lag_s."""
        from . import live_guard, live_quality as lq, mybook
        cfg = dict(self.vc)
        ev_min = self.lp_cfg().get("ev_min")
        if ev_min is not None:
            cfg["prop_ev_min"] = cfg["team_prop_ev_min"] = float(ev_min)
        fs = vf.cal_on(vf.prop_findings)(pev, sport, cfg, mkts, registry=self.registry)
        if sport == "ncaaf" and "player_anytime_td" in mkts:   # 2026-10-24 (Marvin): CFB live TD 2-book
            from . import cfb_live_td
            if cfb_live_td.enabled():
                fs = [f for f in fs if f.market != cfb_live_td.MARKET] + cfb_live_td.findings(pev, cfg, self.lp_cfg())
        # 2026-10-30 (Marvin approved): CFB/NFL in-game prop win prob = prop pacer (calibrated); per-row fallback to the
        # current model on any pacer error. Off: PACER_CFB_OFF / PACER_NFL_OFF (env or file in /opt/marv-feed/reports/prop_pacer)
        try:
            from . import pacer_hook as _ph
            _ph.apply(sport, g, fs, float(cfg.get("prop_ev_min", 0.05)))
        except Exception:  # noqa: BLE001
            log.exception("pacer hook failed (current model kept)")
        fs = vf.best_per_selection(fs)
        sc_ts = (getattr(self, "_lp_scores", {}) or {}).get(g.event_id, (None, None))[1]
        max_lag = float(self.lp_cfg().get("max_lag_s", 180))
        upd: Dict[str, Dict[str, Optional[float]]] = {}
        for bk in pev.get("bookmakers") or []:
            for m in bk.get("markets") or []:
                upd.setdefault(m.get("key"), {})[bk.get("key")] = lq._ts(m.get("last_update")) or lq._ts(bk.get("last_update"))
        sit = live_guard.situation_brief(g)
        for f in fs:
            f.extra.update(_game=g, in_game=True, situation=sit, _odds_ts=odds_ts, _ev=pev, price_age_s=0.0)
            mu = upd.get(f.market) or {}
            mine = next((v for k, v in mu.items() if mybook.is_mine(k)), None)
            fresh = max([v for v in mu.values() if v is not None], default=None)
            why = None
            if mine is not None and sc_ts is not None and mine <= sc_ts:
                why = "Bovada prop quote older than the last score change"
            elif mine is not None and fresh is not None and fresh - mine > max_lag:
                why = f"Bovada prop quote {fresh - mine:.0f}s behind the freshest book"
            if why:
                f.extra["my_stale"] = True
                f.extra["live_reason"] = "stale: " + why
        self.apply_prop_rules(sport, fs)
        self.apply_my_book(fs)
        for f in fs:
            if f.extra.get("my_stale") and (f.market_flag or f.model_flag):
                f.extra["alert_blocked"] = f.extra.get("live_reason")
        return self.team_prop_quality(sport, fs)

    # ------------------------------------------------------------------ props-only sports (NBA)
    def props_only_game_ok(self, sport: str, g: Game) -> bool:
        """Season gate: preseason (ESPN season type 1) is skipped when props_skip_preseason (default on).
        US sports only (ESPN soccer leagues use season type 1 for the regular season)."""
        if (self.sports.get(sport) or {}).get("kind", "us") != "us":
            return True
        if self.vc.get("props_skip_preseason", True) and g.season_type == 1:
            return False
        return True

    def px_group(self, sport: str) -> str:
        """Allocation / market / cap group of a props-only sport: 'soccer' for every soccer league, else the sport."""
        return "soccer" if (self.sports.get(sport) or {}).get("kind") == "soccer" else sport

    def px_markets(self, sport: str) -> List[str]:
        pm = self.vc.get("props_markets") or {}
        return list(pm.get(sport) or pm.get(self.px_group(sport)) or [])

    def px_cap(self, sport: str) -> Optional[int]:
        """Per-group daily snapshot cap (value_finder.props_only_caps), None = only the family cap applies."""
        caps = self.vc.get("props_only_caps") or {}
        v = caps.get(self.px_group(sport))
        return None if v is None else int(v)

    def px_used(self, sport: str, day: str) -> int:
        _, n = self.db.kv_get(f"pxused:{self.px_group(sport)}:{day}")
        return int(n or 0)

    def px_count(self, sport: str, day: str) -> None:
        self.db.kv_set(f"pxused:{self.px_group(sport)}:{day}", self.px_used(sport, day) + 1)

    def px_nobook_track(self, sport: str, has_mine: bool) -> None:
        """Auto-pause (value_finder.props_only_nobook_pause.<group>: {after, days}): after `after` paid responses
        in a row without Bovada props, the group is paused for `days` (Bovada had none for soccer on Oct 3)."""
        grp = self.px_group(sport)
        rule = (self.vc.get("props_only_nobook_pause") or {}).get(grp)
        if not rule:
            return
        k = f"pxnobook:{grp}"
        if has_mine:
            self.db.kv_set(k, 0)
            return
        _, n = self.db.kv_get(k)
        n = int(n or 0) + 1
        self.db.kv_set(k, n)
        if n >= int(rule.get("after", 3)):
            until = (self.clock() + timedelta(days=float(rule.get("days", 7)))).isoformat()
            self.db.kv_set(f"pxpause:{grp}", until)
            self.db.kv_set(k, 0)
            log.info("props-only %s paused until %s: %d responses in a row without Bovada props", grp, until, n)

    def px_paused(self, sport: str) -> Optional[str]:
        _, until = self.db.kv_get(f"pxpause:{self.px_group(sport)}")
        t = _ts(until) if isinstance(until, str) else None
        return until if t is not None and self.clock() < t else None

    def _odds_client(self):
        """The Odds API client behind self.odds (OddsAPI directly, or the hub's primary provider)."""
        if getattr(self.odds, "guard", None) is not None:
            return self.odds
        c = getattr(getattr(self.odds, "primary", None), "client", None)
        return c if getattr(c, "guard", None) is not None else None

    def props_only_budget_ok(self, sport: str, cost: float, day: str, ev: Dict[str, Any], snap: str) -> Tuple[bool, str]:
        """Extra hard gates for props-only sports, checked BEFORE the call (the OddsAPI guard then applies the
        props_<sport> daily/monthly allocation, the global daily cap and the key floor):
          * the allocation odds_api.allocations.props_<sport> must exist (fail closed)
          * key reserve: last x-requests-remaining - cost >= key floor + props_only_guard.reserve
          * month ceiling: credits used this UTC month (all strategies) + cost <= props_only_guard.month_ceiling
          * Bovada: if an earlier snapshot of this event today had no Bovada props, skip the next one."""
        from .db import month_utc
        oc = self.cfg.get("odds_api") or {}
        grp = self.px_group(sport)
        if not ((oc.get("allocations") or {}).get(f"props_{grp}") or {}).get("daily"):
            return False, f"no odds_api.allocations.props_{grp} (fail closed)"
        if snap != "manual":
            pu = self.px_paused(sport)
            if pu:
                return False, f"no Bovada props lately: {grp} paused until {pu[:16]}"
        g = self.vc.get("props_only_guard") or {}
        cl = self._odds_client()
        if cl is None:
            return False, "no Odds API client (fail closed)"
        label = cl.key_label
        ok, why = cl.guard.check(cost, f"props_{grp}", label)   # quiet pre-check (no SKIPPED rows)
        if not ok:
            return False, why
        rem = cl.guard.remaining(label)
        if rem is not None:
            floor, _ = cl.guard._limits(label)
            need = floor + float(g.get("reserve", 2000))
            if rem - cost < need:
                return False, f"reserve: remaining {rem:.0f} - {cost:.0f} < floor+reserve {need:.0f}"
        ceil = g.get("month_ceiling")
        if ceil is not None:
            labels = [k.get("label", k.get("env")) for k in (oc.get("keys") or [])] or [label]
            used = sum(self.db.credits_used_in_month(month_utc(), key_label=lb) for lb in labels if lb)
            if used + cost > float(ceil):
                return False, f"month ceiling: used {used:.0f} + {cost:.0f} > {float(ceil):.0f}"
        if snap != "manual":
            _, seen = self.db.kv_get(f"pxbook:{ev.get('id')}:{day}")
            if seen is not None and not seen.get("my_book"):
                return False, "no Bovada props at the earlier snapshot"
        return True, "ok"

    def team_markets(self, sport: str) -> List[str]:
        """Team-prop market keys for a sport (registry sport spec, else value_finder.team_props_markets)."""
        spec = self.registry.sports.get(sport)
        mk = (spec.team_markets if spec and spec.team_markets else None) or \
            (self.vc.get("team_props_markets") or {}).get(sport) or []
        return [m for m in mk if self.registry.market(m, sport) is not None]

    # ------------------------------------------------------------------ evaluation
    def _min_games(self, sport: str) -> int:
        mg = self.vc.get("model_min_games", 3)
        return mg.get(sport, 3) if isinstance(mg, dict) else mg

    def model_for(self, sport: str, g: Game) -> Tuple[Optional[float], bool, Dict[str, Any]]:
        """Pregame margin (home) from the ratings model; never from market data."""
        if self.team_model is not None:
            r = self.team_model.margin(sport, g.home.name, g.away.name, g.neutral, g.home.id, g.away.id)
            if r is None:
                return None, False, {}
            mg = self._min_games(sport)
            ok = min(r["games"]) >= mg
            info = dict(r, min_games=mg, kind="ratings")
            if g.season_type == 3:
                info["low_confidence"] = "playoffs"
            return r["margin"], ok, info
        if self.vc.get("legacy_srs", False):
            srs = self.srs(sport)
            if srs is None:
                return None, False, {}
            m = srs.margin(g.home.id, g.away.id, g.neutral)
            ng = (srs.n_games(g.home.id), srs.n_games(g.away.id))
            mg = self._min_games(sport)
            return m, min(ng) >= mg, {"games": ng, "min_games": mg, "kind": "legacy_srs",
                                      "source": "espn results (legacy SRS)"}
        return None, False, {}

    def evaluate_featured(self, sport: str, g: Game, ev: Dict[str, Any], in_game: bool = False) -> List[vf.Finding]:
        sigma = float(self.sports[sport]["sigma"])
        m, ok, info = (None, False, {}) if in_game else self.model_for(sport, g)
        ick, irep = getattr(self, "injuries", None), None
        if ick is not None and not in_game:
            try:
                irep = ick.report(sport, g)
                m2, d = ick.model_margin(sport, g, m, irep)
                if d:
                    info = dict(info, injury_adj=round(d, 2), margin_raw=m)
                    m = m2
            except Exception:  # noqa: BLE001
                log.debug("injury report failed", exc_info=True)
                irep = None
        if info.get("sigma"):
            sigma = float(info["sigma"])
        cfg = dict(self.vc)
        if in_game:
            cfg["market_ev_min"] = self.vc.get("in_game_ev_min", 0.04)
        stale = {}
        if in_game:          # Marvin 2026-10-03: lagging / pre-score / outlier live books out of the consensus
            from . import live_quality as lq
            stale = lq.stale_books(ev, g)
            if stale:
                log.info("in-game %s: stale books skipped: %s", g.name, stale)
        fs = vf.cal_on(vf.featured_findings)(ev, sport, cfg, m, sigma, ok, registry=self.registry, exclude_books=stale)
        for f in fs:
            if stale:
                f.extra["stale_books"] = stale
        ctx = None
        if not in_game and getattr(self, "context", None) is not None and fs:
            try:
                ctx = self.context.factor_line(g, getattr(self, "trend_store", None))
                ws = self.context.weather_signal(
                    g, skip=tuple(((self.cfg.get("strategies") or {}).get("context") or {}).get("disabled_signals") or ()))
            except Exception:  # noqa: BLE001
                ctx, ws = None, None
        for f in fs:
            f.extra.update(_game=g, model_info=info, in_game=in_game)
            if ctx:
                f.extra["context"] = ctx
            if ctx is not None and ws and f.market == "totals":
                e = ws["effect"]
                f.extra["context_warn"] = (f"{ws['why']}: NFL outdoor games like this went UNDER "
                                           f"{(e['disc'][0] + e['test'][0])}-{(e['disc'][1] + e['test'][1]) - (e['disc'][0] + e['test'][0])} "
                                           f"vs the closing total (out-of-sample confirmed)"
                                           + ("; this OVER goes against it" if f.selection == "Over" else ""))
            if f.model_flag and info.get("low_confidence"):
                f.extra["low_confidence"] = info["low_confidence"]
        if not in_game and getattr(self, "edge_adjuster", None) is not None and fs:
            self.apply_context_edges(g, fs)
        if irep is not None:
            self.apply_injuries(sport, g, fs, irep, info)
        self.apply_my_book(fs)
        return fs

    def apply_context_edges(self, g: Game, fs: List[vf.Finding]) -> int:
        """Holdout-validated context factors (ajbet.context.EdgeAdjuster, FACTORS.md) shift fair_p before
        injuries / my-book checks, so 'Good buy up to', EV and the alert flag all use the adjusted probability."""
        def thr(f: vf.Finding) -> float:
            h = self.registry.market(f.market, f.sport)
            return float(h.ev_min) if h is not None and h.ev_min is not None else float(self.vc.get("market_ev_min", 0.03))
        try:
            return self.edge_adjuster.apply(g, fs, thr)
        except Exception:  # noqa: BLE001
            log.debug("context edge adjust failed", exc_info=True)
            return 0

    def apply_injuries(self, sport: str, g: Game, fs: List[vf.Finding], rep: Dict[str, Any], info: Dict[str, Any]) -> int:
        """🩺 line on every finding; market-based side picks backing a team whose QB1 is out lose the validated
        backup-QB fade (p -= ~4.5 pts); if the edge disappears the pick is downgraded to the watchlist."""
        from .markets import name_sim
        from .oddsmath import american_to_decimal as dec
        ick = self.injuries
        line = ick.line(sport, g, rep)
        n = 0
        for f in fs:
            if line:
                f.extra["injury"] = line + (f" · model margin adjusted {info['injury_adj']:+.1f} pts"
                                            if info.get("injury_adj") and f.model_flag else "")
            if f.market not in ("spreads", "h2h") or not (f.market_flag or f.model_flag):
                continue
            side = "home" if name_sim(f.selection, g.home.name, self.aliases) >= name_sim(f.selection, g.away.name,
                                                                                          self.aliases) else "away"
            pen = ick.side_penalty(sport, g, side, rep)
            if not pen or not f.market_flag:
                continue
            p = (f.fair_p or 0) - pen
            price = f.extra.get("my_price") or f.price
            if price is None or p * dec(float(price)) - 1 < self.ev_min_for(f):
                f.market_flag = f.model_flag = False
                f.extra["alert_blocked"] = f"injury: {g.team(side).abbr} QB1 out, edge gone after adjustment"
                f.extra["watchlist"] = list(f.extra.get("watchlist") or []) + ["injury_downgrade"]
                n += 1
        return n

    def apply_prop_injuries(self, sport: str, g: Game, fs: List[vf.Finding]) -> int:
        ick = getattr(self, "injuries", None)
        if ick is None:
            return 0
        try:
            rep = ick.report(sport, g)
        except Exception:  # noqa: BLE001
            return 0
        n = 0
        for f in fs:
            if not f.player:
                continue
            act, why = ick.prop_check(sport, g, f.player, f.market, rep)
            if act:
                f.extra["injury"] = f"🩺 Injuries: {why}"
            if act == "suppress" and (f.market_flag or f.model_flag):
                f.market_flag = f.model_flag = False
                f.extra["alert_blocked"] = f"injury: {why}"
                f.extra["watchlist"] = list(f.extra.get("watchlist") or []) + ["injury_suppressed"]
                n += 1
        return n

    def apply_my_book(self, fs: List[vf.Finding]) -> int:
        """alerts.only_my_book: an edge alerts only if MY book's own price clears the threshold;
        other-book edges become paper-only watchlist picks ('not_my_book'). Returns # demoted."""
        from . import mybook
        if not mybook.only():
            return 0
        n = 0
        for f in fs:
            if not (f.market_flag or f.model_flag):
                continue
            win_p = f.model_p if (f.model_flag and not f.market_flag and f.model_p) else f.fair_p
            thr = self.ev_min_for(f)
            if mybook.is_mine(f.book) or mybook.clears(win_p, f.extra.get("my_price"), thr):
                continue
            f.market_flag = f.model_flag = False
            f.extra["watchlist"] = list(f.extra.get("watchlist") or []) + ["not_my_book"]
            n += 1
        return n

    # ------------------------------------------------------------------ player model
    def player_index(self, sport: str):
        if self.store is None:
            return None
        snap = self.store.players(sport)
        if not snap:
            return None
        key = (sport, snap.get("as_of_utc"))
        if key not in self._pidx:
            from .players.project import PlayerIndex
            self._pidx = {k: v for k, v in self._pidx.items() if k[0] != sport}
            self._pidx[key] = PlayerIndex(snap)
        return self._pidx[key]

    def apply_player_model(self, sport: str, g: Game, fs: List[vf.Finding]) -> None:
        """Independent projection (last-20 logs + opponent defence) -> model_p / model EV."""
        idx = self.player_index(sport)
        if idx is None:
            return
        from .players.project import project
        from .players import nfl as pnfl
        elig = pnfl.eligible if sport == "nfl" else None
        pcfg = dict(self.pc, prior_sd=self.vc.get("prop_sd", {}))
        thr = self.vc.get("prop_model_ev_min", 0.07)
        cap = self.vc.get("prop_model_max_disagreement", self.vc.get("model_max_disagreement", 0.15))
        cache: Dict[Tuple[str, str], Any] = {}
        for f in fs:
            if not f.player:
                continue
            k = (f.player, f.market)
            if k not in cache:
                pl = idx.find(f.player, [g.home.name, g.away.name])
                if pl is None:
                    cache[k] = (None, "player not found / ambiguous")
                else:
                    opp = idx.opponent_of(pl, g.home.name, g.away.name)
                    cache[k] = project(pl, f.market, idx.defense_for(opp), pcfg, elig)
            proj, why = cache[k]
            if proj is None:
                f.extra["model_note"] = why
                continue
            try:
                p_over, p_under = proj.prob_at(f.point)
            except ValueError:
                continue
            over = f.selection.lower() in ("over", "yes")
            f.model_p, pl_ = (p_over, p_under) if over else (p_under, p_over)
            f.model_ev = vf.ev_pct(f.model_p, f.price, pl_)
            gap = abs(f.model_p - f.fair_p)
            alerts_on = bool(self.vc.get("prop_model_alerts", False))
            f.model_flag = alerts_on and f.model_ev >= thr and gap <= cap
            if not alerts_on:
                f.extra["model_note"] = "info only: player-model alerts off (backtest: overconfident)"
            elif f.model_ev >= thr and gap > cap:
                f.extra["model_note"] = f"model vs market gap {gap * 100:.0f} pp > cap (likely model/news gap)"
            f.extra["model_info"] = {"kind": "player", "mean": round(proj.mean, 2),
                                     "sd": None if proj.sd is None else round(proj.sd, 2), "dist": proj.dist,
                                     "n": proj.n, "opp_factor": round(proj.opp_factor, 3),
                                     "last5": proj.details.get("last5"), "team": proj.team}
            if f.model_flag and g.season_type == 3:
                f.extra["low_confidence"] = "playoffs"

    def evaluate_props(self, sport: str, g: Game, pev: Dict[str, Any], mkts: List[str]) -> List[vf.Finding]:
        fs = vf.best_per_selection(vf.cal_on(vf.prop_findings)(pev, sport, self.vc, mkts, registry=self.registry))
        for f in fs:
            f.extra["_game"] = g
        self.apply_player_model(sport, g, fs)
        self.apply_prop_rules(sport, fs)
        self.apply_prop_injuries(sport, g, fs)
        self.apply_my_book(fs)
        return self.team_prop_quality(sport, fs)

    def _is_team_prop(self, f: vf.Finding) -> bool:
        try:
            h = self.registry.market(f.market, f.sport)
            if h is not None:
                return h.family == "team_props"
        except Exception:  # noqa: BLE001
            pass
        return bool(f.player) and not str(f.market).startswith(("player_", "batter_", "pitcher_"))

    def team_prop_quality(self, sport: str, fs: List[vf.Finding]) -> List[vf.Finding]:
        """2026-10-16 (Marvin): junk team-prop lines. Drop any price worse than -1000 or above +1000 (book or
        Bovada price); stars/alerts only with >= 3 books and the line within 15% of the consensus median line."""
        out = []
        for f in fs:
            if not self._is_team_prop(f):
                out.append(f)
                continue
            pxs = [x for x in (f.price, (f.extra or {}).get("my_price")) if x is not None]
            if any(float(x) < -1000 or float(x) > 1000 for x in pxs):
                log.info("team prop junk: skip %s %s %s %s @ %s (price outside -1000/+1000)",
                         f.player, f.market, f.selection, f.point, pxs)
                continue
            ml = (f.extra or {}).get("median_line")
            dev = None if (ml is None or f.point is None) else abs(f.point - ml) / max(abs(ml), 1.0)
            if (f.n_books or 0) < 3 or dev is None or dev > 0.15:
                f.extra["no_stars"] = f"team prop: {f.n_books} books, line dev {dev if dev is None else round(dev, 3)}"
                f.market_flag = f.model_flag = False
            out.append(f)
        return out

    def bovada_stars(self, f: vf.Finding, win_p: Optional[float] = None) -> int:
        """Card rating (⭐ count) at BOVADA's own price for this exact pick (Marvin Oct 3 2:15 PM: props send at
        ⭐⭐+). 0 when Bovada does not quote it, its quote is stale, or the edge is above the stale cap."""
        from . import friendly as frd, live_quality as lq, mybook
        mp = (f.extra or {}).get("my_price")
        if mp is None and mybook.is_mine(f.book):
            mp = f.price
        if mp is None or (f.extra or {}).get("my_stale") or (f.extra or {}).get("no_stars"):
            return 0
        if win_p is None:
            win_p = f.model_p if (f.model_flag and not f.market_flag and f.model_p) else f.fair_p
        if win_p is None:
            return 0
        live = bool((f.extra or {}).get("in_game"))
        ev = frd.ev_at(win_p, mp)
        if ev > lq.edge_cap(live, self.cfg) + 1e-12:
            return 0                                # 'likely stale' edge: never sent
        return len(frd.stars(ev))                  # Oct 11 2:46 PM: live props rated uncapped (⭐⭐ bar live)

    # props backtest rules (docs/backtest_results.md section 6, "Most profitable props")
    def apply_prop_rules(self, sport: str, fs: List[vf.Finding]) -> None:
        """Alert gates + paper-only watchlist tags. Never adds alerts, only removes them.

        * markets in prop_alert_exclude never alert (pass TDs / rush attempts lost 23-25% at EV>=5%)
        * team-total Unders never alert (-21% NFL, negative both seasons)
        * a line worse than the consensus line for the chosen side never alerts (lost 8-9%)
        * watchlist (paper bets, no alerts): NFL team-total Overs, WNBA low-volume Unders,
          WNBA props with EV >= 3%. One paper bet per prop side, at the consensus line."""
        vc = self.vc
        excl = set(vc.get("prop_alert_exclude") or [])
        wl = vc.get("watchlist") or {}
        no_alert_sports = set(wl.get("paper_only_sports") or [])
        no_alert_tt = set(wl.get("team_total_over_paper_only") or [])
        # low-volume = consensus line in the lower third of that market's lines in this event
        lines: Dict[str, List[float]] = {}
        for f in fs:
            ml = f.extra.get("median_line")
            if f.player and ml is not None and f.market.startswith(("player_", "batter_", "pitcher_")):
                lines.setdefault(f.market, []).append(ml)
        cut = {}
        for mk, ls in lines.items():
            u = sorted(set(ls))
            if len(u) >= 3:
                cut[mk] = u[max(0, (len(u) - 1) // 3)]
        for f in fs:
            is_tt = "team_totals" in f.market
            rel = vf.line_vs_consensus(f)
            worse = rel is not None and rel < -1e-9
            reasons = []
            if f.market in excl:
                reasons.append("market excluded from alerts (backtest)")
            if is_tt and f.selection == "Under" and not vc.get("team_total_under_alerts", False):
                reasons.append("team-total unders never alert (backtest -21%)")
            if worse and vc.get("prop_require_line_not_worse", True):
                reasons.append("line worse than consensus")
            watch = []
            if wl.get("enabled", True) and not worse and not (is_tt and f.selection == "Under") \
                    and f.market not in excl:
                if is_tt and f.selection == "Over" and sport in no_alert_tt and f.market == "team_totals":
                    watch.append(f"{sport}_team_total_over")
                if sport in (wl.get("low_volume_under_sports") or []) and f.selection == "Under" and \
                        f.market in cut and f.extra.get("median_line") is not None and \
                        f.extra["median_line"] <= cut[f.market]:
                    watch.append(f"{sport}_low_volume_under")
                ev_min = (wl.get("ev_min_by_sport") or {}).get(sport)
                if ev_min is not None and f.ev >= ev_min:
                    watch.append(f"{sport}_ev{int(round(ev_min * 100))}")
            tt_promoted = is_tt and f.selection == "Over" and f.market == "team_totals" and \
                sport == "nfl" and policy.status("team_total_over") == "kept"
            # Marvin Oct 3 2:15 PM: a ⭐⭐+ prop at Bovada's price is no longer held back by the paper-only sport /
            # NFL team-total-Over lists (policy.decide sends it); weaker ones keep the old paper-only path
            strong = (f.market_flag or f.model_flag) and not reasons and \
                self.bovada_stars(f) >= policy.prop_min_stars(bool((f.extra or {}).get("in_game")), tt_over=_tt_over(f))
            if strong:
                pass
            elif (is_tt and f.selection == "Over" and sport in no_alert_tt and not tt_promoted) or (sport in no_alert_sports and not _nbap.is_nba_player_prop(f)):
                if f.market_flag or f.model_flag:
                    reasons.append("paper-only watchlist (no alerts yet)")
                    if sport in no_alert_sports and not _nbap.is_nba_player_prop(f) and not worse and f.market not in excl and wl.get("enabled", True):
                        watch.append(f"{sport}_paper_only")     # still paper-tracked (never sent)
            if reasons and (f.market_flag or f.model_flag):
                f.market_flag = f.model_flag = False
                f.extra["alert_blocked"] = "; ".join(reasons)
            if watch:
                f.extra["watchlist"] = watch

    def watch_picks(self, fs: List[vf.Finding]) -> List[vf.Finding]:
        """One watchlist bet per (event, market, player/team, side): at the consensus line if
        quoted, best EV, then FanDuel > DraftKings > ESPN BET."""
        best: Dict[Tuple, vf.Finding] = {}
        for f in fs:
            if not f.extra.get("watchlist") or f.market_flag or f.model_flag:
                continue
            k = (f.event_id, f.market, f.player, f.selection)
            rel = vf.line_vs_consensus(f) or 0.0
            score = (abs(rel) < 1e-9, f.ev, -vf._book_rank(f.book))
            cur = best.get(k)
            if cur is None or score > cur[0]:
                best[k] = (score, f)
        return [f for _, f in best.values()]

    def evaluate_live_events(self, sport: str, events: List[Dict[str, Any]], games: List[Game],
                             odds_ts: Optional[float] = None) -> List[str]:
        """In-game hook: reuse Strategy A's live featured response (0 extra credits).
        Round 2: every flagged live pick must pass live_guard.stale_check (HARD): price age <= 30 s,
        best book <= 10% over Bovada, edge <= 15%, Pinnacle quote newer than the last score change."""
        from . import live_guard
        if not self.vc.get("in_game", True) or sport not in self.sports or sport in getattr(self, "props_only", ()):
            return []
        out = []
        for g in games:
            if g.state != "in":
                continue
            if sport == "ncaaf" and self.sports.get(sport, {}).get("scope", "all") != "all" and self.top30 \
                    and g.home.id not in self.top30 and g.away.id not in self.top30:
                continue
            ev = match_event(g.home.name, g.away.name, g.start, events, self.aliases)
            if not ev:
                continue
            fs = self.evaluate_featured(sport, g, ev, in_game=True)
            for f in fs:
                f.extra["_odds_ts"], f.extra["_ev"] = odds_ts, ev
                f.extra["price_age_s"] = None if odds_ts is None else round(self.clock().timestamp() - float(odds_ts), 1)
                f.extra["situation"] = live_guard.situation_brief(g)
                if f.market_flag and f.method.startswith("sharp"):
                    ok, why = live_guard.stale_check(g, ev, odds_ts, self.cfg, now=self.clock().timestamp(), best_price=f.price,
                                                     my_price=(f.extra or {}).get("my_price"), market_ev=f.ev,
                                                     market=f.market)
                    if ok and f.extra.get("my_stale"):    # Marvin 2026-10-03: Bovada's own live quote is stale
                        from . import mybook as _mb
                        ok, why = False, f"stale: {_mb.key()} {(f.extra.get('stale_books') or {}).get(_mb.key(), 'lagging')}"
                    if not ok:                  # stale quote -> paper watchlist (if edge >= 0 at Bovada), not sent
                        f.market_flag = False
                        f.extra["alert_blocked"] = why
                        f.extra["watchlist"] = list(f.extra.get("watchlist") or []) + ["stale_price"]
                        self.emit_watch(f, g)
                        log.info("in-game value %s %s stale block: %s", f.selection, f.market, why)
                        continue
                    ok, why = self.live_side_ok(sport, g, f)
                    if not ok:                  # breaks the pregame strategy -> paper watchlist, not sent
                        f.market_flag = False
                        f.extra["alert_blocked"] = why
                        f.extra["watchlist"] = list(f.extra.get("watchlist") or []) + [f.extra.pop("_blocked_by", "live_not_pregame_strategy")]
                        self.emit_watch(f, g)
                        log.info("in-game value %s %s blocked: %s", f.selection, f.market, why)
                        continue
                    f.extra["live_reason"] = why
                    a = self.emit(f, g)
                    if a:
                        out.append(a)
            for f in self.watch_picks([f for f in fs if "not_my_book" in (f.extra.get("watchlist") or [])
                                       and f.method.startswith("sharp")]):
                self.emit_watch(f, g)       # other-book live edge: paper-only, not sent
        return out

    def live_side_ok(self, sport: str, g: Game, f: vf.Finding):
        """In-game side bets (ML/spread) must follow the pregame strategy (ajbet/overreaction.py).
        Totals are unaffected."""
        from . import overreaction as ovr
        from . import live_guard, mybook
        side = ovr.side_for(g, f.selection, lambda a, b: name_sim(a, b, self.aliases))
        mp = (f.extra or {}).get("my_price")
        price = mp if (mybook.only() and mp is not None) else f.price
        ok, why = live_guard.check(g, side, f.market, price, self.sports.get(sport) or {}, self.cfg)
        if not ok:                       # heavy favourite / outside target band / volatile one-possession game
            f.extra["_blocked_by"] = "live_guard"
            return False, why
        if f.market not in ("h2h", "spreads") or not self.vc.get("live_follow_pregame", True):
            return True, ""
        fc = (self.cfg.get("strategies") or {}).get("favorite_comeback") or {}
        c = {k: fc[k] for k in ("strong_fav_ml", "strong_fav_spread") if fc.get(k) is not None}
        return ovr.check(g, side, self.sports.get(sport) or {}, c)

    # ------------------------------------------------------------------ CLV
    def update_clv(self, events: List[Dict[str, Any]]) -> int:
        """Re-price open pregame value bets with the latest pre-kick fair price (closing proxy)."""
        by_id = {e.get("id"): e for e in events}
        n = 0
        for bet in self.db.open_bets():
            bet = dict(bet)
            if bet.get("strategy") != "value" or bet.get("odds_event_id") not in by_id:
                continue
            if (bet.get("market") or "").startswith(("player_", "batter_", "pitcher_")):
                continue
            ev = by_id[bet["odds_event_id"]]
            st = _ts(ev.get("commence_time", ""))
            if st and st <= self.clock():
                continue
            for f in vf.featured_findings(ev, bet.get("sport") or "", self.vc):
                if f.market == bet["market"] and f.selection == bet["selection"] and \
                        (f.point is None or bet["line"] is None or abs(f.point - bet["line"]) < 1e-9):
                    self.db.update_clv(bet["id"], f.fair_p, vf.ev_pct(f.fair_p, bet["price"]))
                    n += 1
                    break
        return n

    def update_prop_clv(self, pev: Dict[str, Any]) -> int:
        """Re-price open prop bets on this event with the latest pre-kick consensus (same line)."""
        st = _ts(pev.get("commence_time", ""))
        if st and st <= self.clock():
            return 0
        n = 0
        fair = None
        for bet in self.db.open_bets():
            bet = dict(bet)
            if bet.get("strategy") != "value" or bet.get("odds_event_id") != pev.get("id") \
                    or not (bet.get("market") or "").startswith(("player_", "batter_", "pitcher_")):
                continue
            if fair is None:
                fair = vf.prop_findings(pev, bet.get("sport") or "", self.vc)
            for f in fair:
                if f.market == bet["market"] and f.player == bet["player"] and f.selection == bet["selection"] \
                        and (f.point == bet["line"] or (f.point is not None and bet["line"] is not None
                                                        and abs(f.point - bet["line"]) < 1e-9)):
                    self.db.update_clv(bet["id"], f.fair_p, vf.ev_pct(f.fair_p, bet["price"], f.p_lose))
                    n += 1
                    break
        return n

    # ------------------------------------------------------------------ output
    def fmt_time(self, iso: str) -> str:
        t = _ts(iso)
        return t.astimezone(self.tz).strftime("%a %b %d %I:%M %p CT") if t else iso

    def bet_price(self, f: vf.Finding) -> Tuple[float, str]:
        """TUNE: the price Marvin can actually take. With only_my_book, paper-track (and size) at MY book's
        price, not the best book's, so the ledger/report card is not flattered by prices he can't get."""
        from . import mybook
        mp = (f.extra or {}).get("my_price")
        if mybook.only() and mp is not None and not mybook.is_mine(f.book):
            return float(mp), mybook.key()
        return float(f.price), f.book

    def stakes(self, f: vf.Finding) -> Tuple[float, float]:
        p = f.model_p if (f.model_flag and not f.market_flag and f.model_p) else f.fair_p
        from . import live_wincap as _LW   # 2026-10-26 live stake uses the 65%-capped win chance
        p = _LW.cap(f, p)
        price, _ = self.bet_price(f)
        k = vf.kelly_stake(p, price, self.vc.get("bankroll", 2000), self.vc.get("kelly_fraction", 0.25),
                           self.vc.get("kelly_cap_pct", 0.02))
        return float(self.vc.get("flat_stake", 100)), k

    def ev_min_for(self, f: vf.Finding) -> float:
        """Alert threshold that applies to this finding (used for 'Good buy up to')."""
        h = self.registry.market(f.market, f.sport)
        if h is not None and h.ev_min is not None:
            return float(h.ev_min)
        if f.model_flag and not f.market_flag:
            return float(self.vc.get("model_ev_min", 0.05))
        if "team_totals" in f.market:
            return float(self.vc.get("team_prop_ev_min", self.vc.get("prop_ev_min", 0.05)))
        if f.player:
            return float(self.vc.get("prop_ev_min", 0.05))
        if f.extra.get("in_game"):
            return float(self.vc.get("in_game_ev_min", 0.04))
        return float(self.vc.get("market_ev_min", 0.03))

    def format(self, f: vf.Finding, g: Optional[Game], header: str = "", edge_open=None) -> str:
        """Friendly Telegram HTML card (phone-first format, ajbet/tgfmt.py)."""
        from .friendly import finding_card
        flat, kelly = self.stakes(f)
        stake = kelly if self.vc.get("paper_stake", "flat") == "kelly" else flat
        pa = self.paper_assess(f, g)
        return finding_card(f, g, self.ev_min_for(f), stake, float(self.vc.get("unit", 100)), header,
                            now=self.clock(), edge_open=edge_open, paper=pa)

    def paper_assess(self, f: vf.Finding, g: Optional[Game]):
        """Marvin 2026-10-03: graded paper results (sent + paper-only + watchlist) feed this card's confidence and
        win-chance calibration (ajbet/paper_conf.py). Stored in f.extra['paper_conf'] -> the alert's details."""
        from . import paper_conf as pc
        model = bool(f.model_flag and not f.market_flag and f.model_p)
        p = f.model_p if model else f.fair_p
        sides = not (f.player or "totals" in (f.market or "")) and f.selection not in ("Over", "Under")
        base = "low" if (f.extra.get("in_game") or f.player or "team_totals" in (f.market or "")
                         or f.extra.get("low_confidence")) else confidence(f).split(" ", 1)[0]
        a = pc.assess(self.db, policy.value_key(f), f.sport, p, teams=[f.selection] if sides else (), base=base)
        from . import live_wincap as _LW   # 2026-10-26 live win chance cap 65% (off: LIVE_WINCAP=off | data/LIVE_WINCAP_OFF)
        if a is not None and a.p_cal is not None and _LW.applies(f) and a.p_cal > _LW.CAP:
            a.p_cal = _LW.CAP
        if a is not None:
            f.extra["paper_conf"] = a.record()
        return a

    def format_plain(self, f: vf.Finding, g: Optional[Game]) -> str:
        prefix = self.notifier.prefix
        flat, kelly = self.stakes(f)
        sport = SPORT_NAMES.get(f.sport, f.sport.upper())
        when = "IN-GAME" if f.extra.get("in_game") else self.fmt_time(f.commence)
        if f.player:
            what = f"{f.player} {f.market.replace('player_', '').replace('_', ' ')} {f.selection}" + \
                   (f" {f.point:g}" if f.point is not None else "")
        else:
            mk = MARKET_NAMES.get(f.market, f.market)
            pt = "" if f.point is None else (f" {f.point:+g}" if f.market == "spreads" else f" {f.point:g}")
            what = f"{mk}: {f.selection}{pt}"
        lc = f", low confidence ({f.extra['low_confidence']})" if f.extra.get("low_confidence") else ""
        lines = [f"{prefix} VALUE ({f.tag}{lc}) {sport}: {f.away} @ {f.home}",
                 f"When: {when}" + (f" | {g.away.abbr} {g.away.score}-{g.home.score} {g.home.abbr} {g.detail}"
                                    if g is not None and g.state == "in" else ""),
                 f"Bet: {what} {fmt_american(f.price)} @ {f.book}",
                 f"Fair (no-vig, {f.method}): {fmt_american(f.fair_price)} ({f.fair_p * 100:.1f}%) "
                 f"| market EV {f.ev * 100:+.1f}%"]
        if f.player and f.extra.get("line_diff"):
            lines.append(f"Line vs consensus median: {f.extra['line_diff']:+g}")
        if f.model_p is not None:
            mi = f.extra.get("model_info", {})
            why = "" if f.model_flag else f" (not flagged: {f.extra.get('model_note') or 'below threshold'})"
            if mi.get("kind") == "player":
                desc = (f"player model: last-{mi.get('n')} weighted mean {mi.get('mean')}"
                        + (f", sd {mi.get('sd')}" if mi.get("sd") else f" ({mi.get('dist')})")
                        + f", opp defence x{mi.get('opp_factor')}")
            elif mi.get("kind") == "ratings":
                desc = (f"ratings: {mi.get('source')}, as of {str(mi.get('as_of', ''))[:10]}, home margin "
                        f"{f.extra.get('model_margin_home', 0):+.1f}, games {mi.get('games')}")
            else:
                desc = f"{mi.get('source', 'SRS ratings')}, games {mi.get('games')}"
            lines.append(f"Model ({desc}): {f.model_p * 100:.1f}% -> model EV {f.model_ev * 100:+.1f}%{why}")
        elif f.player:
            lines.append(f"Model: none (consensus-based prop pricing; no independent projection: "
                         f"{f.extra.get('model_note') or 'player model unavailable'})")
        if f.extra.get("low_confidence"):
            lines.append(f"Confidence: LOW ({f.extra['low_confidence']})")
        lines.append("Confidence: " + confidence(f))
        lines.append(f"Stake (paper): flat ${flat:.0f} | 0.25 Kelly ${kelly:.2f} "
                     f"(bankroll ${self.vc.get('bankroll', 2000):,.0f}, cap {self.vc.get('kelly_cap_pct', 0.02):.0%})")
        if f.market_flag:
            lines.append("Note: market-relative value (line shopping vs no-vig consensus), not a model edge.")
        return "\n".join(lines)

    def emit_shadow(self, d: Dict[str, Any]) -> Optional[str]:
        """2026-10-30 (Marvin: "Place it under aj alert"): MLB/soccer Marv-priced in-game shadow pick -> normal
        AJ live alert path (same in-game gate, dedupe, thumbs-down, blowout, stale checks). Shadow table still logs."""
        g, ev, q = d["game"], d["event"], d["quote"]
        team = None if q.get("team") is None else (g.home.name if q["team"] == "home" else g.away.name)
        sport = "mlb" if d.get("src") == "mlb" else str(q.get("league") or "soccer")
        p = float(q.get("p_win") or 0.0)
        f = vf.Finding(sport=sport, event_id=str(ev.get("id") or g.event_id), home=ev.get("home_team") or g.home.name,
                       away=ev.get("away_team") or g.away.name, commence=ev.get("commence_time") or g.start,
                       market=q["market"], selection=q["side"], point=q.get("line"), book="bovada",
                       price=float(q["price"]), fair_p=p, ev=float(d["ev"]), method=f"marv_{d.get('src')}_live",
                       n_books=1, player=team, p_lose=max(0.0, 1.0 - p - float(q.get("p_push") or 0.0)),
                       extra={"in_game": True, "shadow_src": d.get("src"), "book_ts": q.get("book_ts")})
        f.market_flag = True
        return self.emit(f, g)

    def prop_once_block(self, f: vf.Finding, g: Optional[Game]) -> bool:
        """2026-11-01 (Marvin): ONE alert/paper bet per game + player + market + side, any market, sent or
        watchlist, regardless of price/line moves or live re-alert counters. Off: data/PROP_ONCE_OFF"""
        import os as _os
        if not f.player or _os.path.exists("data/PROP_ONCE_OFF"):
            return False
        espn_id = str(g.event_id if g is not None else f.event_id)
        try:
            r = self.db.conn.execute(
                "SELECT 1 FROM alerts WHERE event_id=? AND market=? AND lower(selection)=lower(?) AND dedupe_key LIKE ? LIMIT 1",
                (espn_id, f.market, str(f.selection), f"%:{f.market}:{f.player}:%")).fetchone()
        except Exception:  # noqa: BLE001
            log.debug("prop once lookup failed", exc_info=True)
            return False
        if r is not None:
            log.info("prop once: block repeat %s %s %s game %s", f.player, f.market, f.selection, espn_id)
            return True
        return False

    def emit(self, f: vf.Finding, g: Optional[Game]) -> Optional[str]:
        if self.prop_once_block(f, g):  # 2026-11-01: one per game+player+market+side
            return None
        if self.td_prop_block(f, g):   # 2026-10-31 (Marvin): one TD alert/paper bet per player/game/market; no D/ST
            return None
        if self.td_paper_only(f):      # 2026-11-01 (Marvin): TD scorer props are PAPER ONLY (no Telegram). On: data/TD_ALERTS_ON
            f.market_flag = f.model_flag = False
            f.extra["watchlist"] = list(f.extra.get("watchlist") or []) + ["td_paper_only"]
            f.extra["paper_reason"] = "TD scorer props paper-only (no alerts)"
            log.info("td rule: paper-only %s %s %s", f.player, f.market, f.event_id)
            self.emit_watch(f, g)
            return None
        pk = policy.value_key(f)
        stars = None
        if pk in policy.PROP_STARS:     # Oct 3 2:15 PM: props send at ⭐⭐+ (rating at Bovada, paper-calibrated)
            pa0 = self.paper_assess(f, g)
            wp = pa0.p_cal if (pa0 is not None and pa0.shown and pa0.p_cal is not None) else None
            stars = self.bovada_stars(f, wp)
            f.extra["bovada_stars"] = stars
        d = policy.decide(pk, live=bool(f.extra.get("in_game")), stars=stars, tt_over=_tt_over(f))   # Marvin 2026-10-03: live bets only
        if f.extra.get("in_game") and (pk in policy.PROP_STARS or f.extra.get("shadow_src")):
            # 2026-10-20 (Marvin): in-game props, every sport (NBA still off below): no star tier, a 70% win-chance bar.
            # Marv player p is overconfident (alt70: raw 70% hit 59.5%, raw 80% hit 72.7%), so: paper-calibrated p >= 70%
            # when shown, else raw >= 80% (player) / >= 70% (team props). Positive-Bovada-edge + no-duplicate gates still apply.
            pc = wp if wp is not None else None
            if f.extra.get("prob_src") == "pacer" and f.extra.get("pacer_p") is not None:   # 2026-10-30 pacer live
                pc = float(f.extra["pacer_p"])
            team = pk == "team_total_over" or not (f.extra.get("player") or getattr(f, "player", None))
            if pc is not None:
                p70, bar, basis = pc, 0.70, "calibrated"
            else:
                p70, bar, basis = f.fair_p, (0.70 if team else 0.80), "raw"
            # 2026-10-30 (Marvin: "all available sports in game prop with a min calculated 62% wr or edge"):
            # send when win chance (calibrated if shown, else raw) >= 62% OR Bovada edge >= in_game_ev_min (4%).
            # Negative-edge alerts still blocked below (62% path needs edge > 0; edge path needs the card bar).
            bar = 0.62
            ok_p = p70 is not None and float(p70) >= bar
            ok_e = False
            try:
                from .friendly import ev_at as _ev_at
                from . import mybook as _mb
                _px = f.extra.get("my_price") if f.extra.get("my_price") is not None else (f.price if _mb.is_mine(f.book) else None)
                _wp = p70 if p70 is not None else f.fair_p
                ok_e = _px is not None and _wp is not None and _ev_at(float(_wp), float(_px)) >= float(self.vc.get("in_game_ev_min", 0.04))
            except Exception:  # noqa: BLE001
                ok_e = False
            ok70 = ok_p or ok_e
            f.extra["p70"] = {"p": p70, "bar": bar, "basis": basis, "via": "p62" if ok_p else ("edge" if ok_e else None)}
            d = "send" if ok70 else "watch"
        if d == "skip":
            return None
        # Oct 16 2026 (Marvin): NBA stays off Telegram always (paper_only_sports / telegram_exclude_sports)
        no_tg = set(((self.vc.get("watchlist") or {}).get("paper_only_sports")) or []) | set(self.vc.get("telegram_exclude_sports") or [])
        if f.sport in no_tg and not _nbap.is_nba_player_prop(f) and not (f.extra.get("in_game") and pk in policy.PROP_STARS):   # 2026-10-30 (Marvin: all in-game props on TG)   # 2026-10-24: NBA player props ON (nba_props_tg)
            f.market_flag = f.model_flag = False
            f.extra["watchlist"] = list(f.extra.get("watchlist") or []) + [f"{f.sport}_paper_only"]
            self.emit_watch(f, g)
            return None
        if d == "send" and (pk in policy.PROP_STARS or f.extra.get("shadow_src")):
            pass                          # star-gated prop: sends in scanned sports (NBA blocked above)
        elif f.sport in getattr(self, "props_only", ()):
            # other props-only sports below star gate: paper ledger only
            f.market_flag = f.model_flag = False
            f.extra["watchlist"] = list(f.extra.get("watchlist") or []) + [f"{f.sport}_paper_only"]
            self.emit_watch(f, g)
            return None
        if d == "watch":                  # lost out-of-sample -> paper-only watchlist, not sent
            f.extra["watchlist"] = list(f.extra.get("watchlist") or []) + [f"policy_{pk}"]
            self.emit_watch(f, g)
            return None
        if f.extra.get("in_game"):
            # 2026-10-20 (Marvin/AJ): a live value alert must show a POSITIVE edge at Bovada's actual price, using the
            # same win chance the card prints (paper-calibrated when shown). Bug: Commanders ML +1400 sent on the raw
            # consensus EV (+11%) while the card said 'Edge at Bovada -48% ... ❌ too pricey'. Paper only, still graded.
            try:
                from .friendly import ev_at
                from . import mybook
                pa_g = self.paper_assess(f, g)
                wp_g = pa_g.p_cal if (pa_g is not None and getattr(pa_g, "shown", False) and pa_g.p_cal is not None) else f.fair_p
                if f.extra.get("prob_src") == "pacer" and f.extra.get("pacer_p") is not None:   # 2026-10-30 pacer live
                    wp_g = float(f.extra["pacer_p"])
                from . import live_wincap as _LW   # 2026-10-26 cap 65% before the live EV gate
                wp_g = _LW.cap(f, wp_g)
                px_g = f.extra.get("my_price") if f.extra.get("my_price") is not None else (f.price if mybook.is_mine(f.book) else None)
                # 2026-10-26 (Marvin audit, Packers +225 sent at edge +1% vs bar +4%): the live Bovada edge must clear the
                # card's bar (ev_min_for), not just be > 0; below-bar = paper only. Off: LIVE_VALUE_BAR_GATE=off | data/LIVE_VALUE_BAR_GATE_OFF
                import os as _os
                _bar = 0.0 if ((f.extra.get("p70") or {}).get("via") == "p62") else self.ev_min_for(f) if (_os.environ.get("LIVE_VALUE_BAR_GATE", "").lower() not in ("off", "0", "false")
                                              and not _os.path.exists("data/LIVE_VALUE_BAR_GATE_OFF")) else 0.0
                if wp_g is not None and px_g is not None and ev_at(wp_g, float(px_g)) < _bar + (1e-12 if _bar == 0 else 0):
                    f.extra["watchlist"] = list(f.extra.get("watchlist") or []) + ["live_no_edge_at_bovada"]
                    f.extra["paper_reason"] = f"edge at Bovada {ev_at(wp_g, float(px_g)) * 100:+.0f}% < bar {_bar * 100:+.0f}% (win {wp_g:.0%})"
                    self.emit_watch(f, g)
                    return None
            except Exception:  # noqa: BLE001
                log.debug("live bovada-edge gate failed", exc_info=True)
        eo = self.edge_vs_open(f, g)
        text = self.format(f, g, edge_open=eo)      # live_reason stays in details_json (not in the alert text)
        if pk in policy.PROP_STARS and "📒" not in text:   # Oct 3 2:15 PM: prop alerts always carry the 📒 line
            pa1 = self.paper_assess(f, g)
            pl = pa1.line(force=True) if pa1 is not None else ""
            if pl:
                text += "\n" + pl
        p7 = f.extra.get("p70") if f.extra.get("in_game") else None
        if p7 and p7.get("p") is not None:
            text = "🛡️ <b>70%</b> · win " + f"{float(p7['p']):.0%}" + (" (calibrated)" if p7["basis"] == "calibrated" else " (raw)") + "\n" + text
        pn = policy.note(pk)
        if pn:
            text += "\n" + pn
        if f.extra.get("in_game") and f.player:   # 2026-10-30 7:25 PM (Marvin): running graded record per bucket (1 line)
            try:
                _pz = f.extra.get("prob_src") == "pacer"   # 2026-11-01: '🧠 Pacer pick' label on the same line
                text += "\n" + ("🧠 Pacer pick · " if _pz else "") + self.prop_record_line(f.sport, _pz)
            except Exception:  # noqa: BLE001
                log.debug("prop record line failed", exc_info=True)
        espn_id = g.event_id if g is not None else f.event_id
        key = f"{espn_id}:{f.dedupe_key}"
        live_state = None
        if f.extra.get("in_game"):                 # review fix #2: live has its own keys (+ material re-alerts)
            lk = self.live_realert_key(key, f, g)
            if lk is None:
                return None
            key, live_state = lk
            if g is not None and not f.player:
                # Marvin 2026-10-03: ESPN gamecast. Opponent (or anyone, for an Under) driving in scoring range ->
                # hold one drive, then re-evaluated; ESPN win probability shown as a cross-check
                from . import gamecast
                sd = self.side_of(f, g)
                act, hnote = gamecast.hold_check(self.db, g, f"value:{f.dedupe_key}",
                                                 side=sd if sd in ("home", "away") else None,
                                                 total_side=sd if sd in ("over", "under") else None, strategy="live_value")
                if act == "hold":
                    return None
                sit = gamecast.situation(g)
                if sit is not None:
                    f.extra["gamecast"] = sit.brief()
                xl = gamecast.cross_line(f.fair_p, g, sd) if f.market == "h2h" else ""
                if xl:
                    text += "\n" + xl
                if hnote:
                    text += "\n" + hnote
                    f.extra["hold_note"] = hnote
        if f.player and self.prop_side_block(espn_id, f):  # 2026-10-13 same-player/stat/game rule
            return None
        aid = self.db.insert_alert(dict(
            dedupe_key=key, event_id=espn_id, league=f.sport, strategy="value", market=f.market,
            selection=f.selection, line=f.point, price=f.price, book=f.book, fair_p=f.fair_p,
            market_p=None, edge=f.ev, value=int(f.market_flag) + 2 * int(f.model_flag),
            trigger=f.tag, message=text,
            details_json=json.dumps(dict({k: v for k, v in f.extra.items() if not k.startswith("_")},
                                         matchup=f"{f.away} @ {f.home}", ev_min=self.ev_min_for(f)), default=str)))
        if aid is None:
            return None
        if live_state is not None:
            self.db.kv_set(live_state[0], live_state[1])
        flat, kelly = self.stakes(f)
        stake = kelly if self.vc.get("paper_stake", "flat") == "kelly" else flat
        if stake > 0:
            side = self.side_of(f, g)
            bp, bb = self.bet_price(f)
            self.db.insert_paper_bet(dict(
                alert_id=aid, event_id=espn_id, league=f.sport, strategy="value", market=f.market,
                selection=f.selection, side=side, line=f.point, price=bp, book=bb,
                stake=stake, to_win=round(stake * (american_to_decimal(bp) - 1), 2), player=f.player,
                ev=vf.ev_pct(f.fair_p, bp), fair_p=f.fair_p, model_p=f.model_p, kelly_stake=kelly, odds_event_id=f.event_id,
                commence=f.commence, tag=f.tag, sport=f.sport,
                edge_open=eo["edge"] if eo else None, open_ref=_eo.ref(eo)))
        ll = getattr(self, "live_log", None) if f.extra.get("in_game") else None
        if ll is not None:
            ll.record(aid, g, "value", f.market, f.selection, f.point, False, f.extra.get("_odds_ts"),
                      f.extra.get("_ev"), f.event_id)
        if self.notifier.send(text, alert_id=aid):
            self.db.mark_sent(aid)
            if ll is not None:
                ll.mark_sent(aid)
        return text

    def prop_record_line(self, sport: str, pacer: bool) -> str:
        """2026-10-30 (Marvin): graded record of in-game player-prop alerts for this sport + model (pacer vs old).
        Uses the existing grader's paper_bets (won/lost; voids excluded); flat-stake ROI at the alerted Bovada price."""
        lab = f"{'Pacer' if pacer else 'Old-model'} {(sport or '?').upper()} props"
        cond = "= 'pacer'" if pacer else "IS NOT 'pacer'"
        rows = self.db.conn.execute(
            "SELECT p.status, p.pnl, p.stake FROM paper_bets p JOIN alerts a ON a.id = p.alert_id "
            "WHERE p.player IS NOT NULL AND p.league = ? AND p.status IN ('won','lost') AND p.stake > 0 "
            "AND json_extract(a.details_json, '$.in_game') = 1 "
            f"AND json_extract(a.details_json, '$.prob_src') {cond}", (sport,)).fetchall()
        if not rows:
            return f"📊 {lab}: new, 0 graded"
        w = sum(1 for r in rows if r[0] == "won"); n = len(rows)
        roi = sum(float(r[1]) / float(r[2]) for r in rows) / n
        return f"📊 {lab}: {w}-{n - w} ({w / n:.0%}) · ROI {roi * 100:+.1f}%"

    LIVE_REALERT = {"cooldown_s": 600, "min_prob_move": 0.04, "max_per_game": 3}

    def live_realert_key(self, base: str, f: vf.Finding, g: Optional[Game]) -> Optional[Tuple[str, Tuple[str, Dict]]]:
        """Live value dedupe (review fix #2, Marvin 2026-10-02). Pregame and live alerts use different keys, so a
        pregame alert never blocks a live one. The same live pick may alert again only when it materially changed
        (Bovada/bet price moved >= min_prob_move in implied probability, or the game situation changed: score in
        football/soccer, period in basketball) AND cooldown_s passed since the last live alert; at most
        max_per_game live alerts per pick per game. Returns (dedupe key, (kv key, new state)) or None (suppressed)."""
        rc = {**self.LIVE_REALERT, **(self.vc.get("live_realert") or {})}
        sk = f"livevalue:{base}"
        _, st = self.db.kv_get(sk)
        bp, _ = self.bet_price(f)
        p = 1.0 / american_to_decimal(bp)
        if g is None:
            situ = ""
        elif (g.league or f.sport) in ("wnba", "nba", "ncaab", "ncaaw"):
            situ = f"P{g.period}"
        else:
            situ = f"{g.away.score}-{g.home.score}"
        t = self.clock().timestamp()
        if st:
            n = int(st.get("n", 1))
            if n >= int(rc["max_per_game"]):
                return None
            if t - float(st.get("t", 0)) < float(rc["cooldown_s"]):
                return None
            moved = abs(p - float(st.get("p", p))) >= float(rc["min_prob_move"]) - 1e-9
            if not (moved or situ != st.get("situ")):
                return None
            n += 1
        else:
            n = 1
        return f"{base}:live:{n}", (sk, {"n": n, "t": t, "p": round(p, 4), "situ": situ, "price": bp})

    def edge_vs_open(self, f: vf.Finding, g: Optional[Game]) -> Optional[Dict[str, Any]]:
        """Edge vs the opening line at the price Marvin can take (Bovada when it has it). Info only."""
        if f.player or g is None:
            return None
        try:
            bp, bb = self.bet_price(f)
            eo = _eo.compute(self.db, g, f.market, self.side_of(f, g) or "", f.point, bp, bb)
            if eo:
                f.extra["edge_open"] = eo["edge"]
                f.extra["open_ref"] = _eo.ref(eo)
            return eo
        except Exception:  # noqa: BLE001 - info line must never block an alert
            log.debug("edge vs open failed", exc_info=True)
            return None

    def side_of(self, f: vf.Finding, g: Optional[Game]) -> Optional[str]:
        """Paper-bet side the grader understands: over/under/yes/no for props & totals, home/away for sides."""
        if f.player or "totals" in (f.market or ""):
            return f.selection.lower()
        if g is not None:
            return "home" if name_sim(f.selection, g.home.name, self.aliases) >= \
                name_sim(f.selection, g.away.name, self.aliases) else "away"
        if f.home and f.away:
            return "home" if name_sim(f.selection, f.home, self.aliases) >= name_sim(f.selection, f.away, self.aliases) \
                else "away"
        return f.selection.lower()

    def prop_side_block(self, espn_id, f: vf.Finding) -> bool:
        """2026-10-13 (Marvin): one player/stat/game -> one side only. Once a side was alerted or watchlisted, the
        opposite side is blocked for that game; same-side repeats are capped at 1 unless the line improves by
        >= 2 (yards) / 1 (catches & other counts). Remove this method's call sites to roll back."""
        try:
            sel = str(f.selection or "").lower()
            if sel not in ("over", "under") or f.point is None:
                return False
            rows = self.db.conn.execute(
                "SELECT selection, line FROM alerts WHERE event_id=? AND market=? AND dedupe_key LIKE ?",
                (str(espn_id), f.market, f"%:{f.market}:{f.player}:%")).fetchall()
            if not rows:
                return False
            opp = [r for r in rows if str(r[0]).lower() in ("over", "under") and str(r[0]).lower() != sel]
            if opp:
                log.info("prop rule: block %s %s %s %s (opposite side already alerted for this game)",
                         f.player, f.market, f.selection, f.point)
                return True
            same = [float(r[1]) for r in rows if str(r[0]).lower() == sel and r[1] is not None]
            if not same:
                return False
            thr = 2.0 if "yds" in (f.market or "") or "yard" in (f.market or "") else 1.0
            p = float(f.point)
            better = p <= min(same) - thr if sel == "over" else p >= max(same) + thr
            if not better:
                log.info("prop rule: block repeat %s %s %s %s (prior %s, needs %.0f better)",
                         f.player, f.market, f.selection, p, same, thr)
            return not better
        except Exception:  # noqa: BLE001 - never break the scan
            log.debug("prop_side_block failed", exc_info=True)
            return False

    TD_MARKETS = ("player_anytime_td", "player_1st_td", "player_last_td")
    TD_ALERTS_ON_FLAG = "data/TD_ALERTS_ON"

    def td_paper_only(self, f: vf.Finding) -> bool:
        """2026-11-01 (Marvin): anytime/first/last TD player props never go to Telegram (any chat); they are logged +
        graded as paper watchlist bets instead. Re-enable sends: touch data/TD_ALERTS_ON"""
        import os as _os
        return (str(getattr(f, "market", "") or "") in self.TD_MARKETS
                and not _os.path.exists(self.TD_ALERTS_ON_FLAG))

    @staticmethod
    def is_dst(name) -> bool:
        n = str(name or "").lower()
        return "d/st" in n or n.endswith(" dst") or n.endswith(" defense") or "defense/special" in n

    def td_prop_block(self, f: vf.Finding, g: Optional[Game]) -> bool:
        """2026-10-31 (Marvin): TD scorer props -> (1) team D/ST props dropped (never in the box score, always VOID);
        (2) ONE alert + one paper bet per player per game per market, across books, price moves, live re-alerts,
        watchlist vs sent, and re-runs. A better price later does NOT re-alert. Off: data/TD_DEDUPE_OFF"""
        mk = str(f.market or "")
        if mk not in self.TD_MARKETS and not mk.endswith("_td"):
            return False
        who = f.player or getattr(f, "selection", None)
        if self.is_dst(f.player) or self.is_dst(f.selection):
            log.info("td rule: drop D/ST prop %s %s", who, mk)
            return True
        import os as _os
        if _os.path.exists("data/TD_DEDUPE_OFF") or not f.player:
            return False
        espn_id = str(g.event_id if g is not None else f.event_id)
        try:
            r = self.db.conn.execute(
                "SELECT 1 FROM alerts WHERE event_id=? AND market=? AND dedupe_key LIKE ? LIMIT 1",
                (espn_id, mk, f"%:{mk}:{f.player}:%")).fetchone()
            if r is None:
                r = self.db.conn.execute(
                    "SELECT 1 FROM paper_bets WHERE event_id=? AND market=? AND player=? LIMIT 1",
                    (espn_id, mk, f.player)).fetchone()
        except Exception:  # noqa: BLE001
            log.debug("td dedupe lookup failed", exc_info=True)
            return False
        if r is not None:
            log.info("td rule: block repeat %s %s game %s (already alerted/paper-bet)", f.player, mk, espn_id)
            return True
        return False

    def emit_watch(self, f: vf.Finding, g: Optional[Game]) -> Optional[str]:
        """Paper-only watchlist bet: logged + paper-tracked, never sent to Telegram.
        Round 2: recorded at the price Marvin can take (Bovada under only_my_book) and only if the edge there
        is >= 0; side is home/away (the grader's language), not the team name."""
        if self.td_prop_block(f, g):   # 2026-10-31 (Marvin): one TD alert/paper bet per player/game/market; no D/ST
            return None
        if self.prop_once_block(f, g):  # 2026-11-01: one per game+player+market+side
            return None
        from . import mybook
        bp, bb = self.bet_price(f)
        if mybook.only() and not mybook.is_mine(bb) and (f.extra or {}).get("my_price") is None:
            log.info("watchlist %s %s skipped: Bovada does not offer it", f.selection, f.market)
            return None
        p = f.model_p if (f.model_flag and not f.market_flag and f.model_p) else f.fair_p
        # 2026-10-26 (Marvin, calib_test shrink25): unsent paper watchlist (non-prop) win chance shrunk 25% toward the
        # price's implied chance, p' = p - 0.25*(p - implied). Paper only. Off: WATCH_SHRINK=off | data/WATCH_SHRINK_OFF
        import os as _os
        if (not f.player and p is not None and bp is not None and _os.environ.get("WATCH_SHRINK", "on").lower() not in ("off", "0", "false", "no")
                and not _os.path.exists("data/WATCH_SHRINK_OFF")):
            p = float(p) - 0.25 * (float(p) - 1.0 / american_to_decimal(float(bp)))
        edge = vf.ev_pct(p, bp, f.p_lose) if f.player else vf.ev_pct(p, bp)
        if edge is None or edge < 0:
            log.info("watchlist %s %s skipped: edge %s < 0 at %s", f.selection, f.market,
                     None if edge is None else f"{edge:+.3f}", bb)
            return None
        espn_id = g.event_id if g is not None else f.event_id
        rules = ",".join(f.extra.get("watchlist") or [])
        key = f"{espn_id}:watch:{f.market}:{f.player or ''}:{f.selection}" + (":live" if f.extra.get("in_game") else "")
        eo = self.edge_vs_open(f, g)
        text = self.format(f, g, header=f"📋 <i>Watchlist (not sent): {rules}</i>", edge_open=eo)
        if f.player and self.prop_side_block(espn_id, f):  # 2026-10-13 same-player/stat/game rule
            return None
        aid = self.db.insert_alert(dict(
            dedupe_key=key, event_id=espn_id, league=f.sport, strategy="value", market=f.market,
            selection=f.selection, line=f.point, price=f.price, book=f.book, fair_p=f.fair_p,
            market_p=None, edge=f.ev, value=0, trigger="watchlist", message=text,
            details_json=json.dumps(dict({k: v for k, v in f.extra.items() if not k.startswith("_")},
                                         matchup=f"{f.away} @ {f.home}", ev_min=self.ev_min_for(f)), default=str)))
        if aid is None:
            return None
        stake = float(self.vc.get("flat_stake", 100))
        self.db.insert_paper_bet(dict(
            alert_id=aid, event_id=espn_id, league=f.sport, strategy="value", market=f.market,
            selection=f.selection, side=self.side_of(f, g), line=f.point, price=bp, book=bb,
            stake=stake, to_win=round(stake * (american_to_decimal(bp) - 1), 2), player=f.player,
            ev=edge, fair_p=f.fair_p, model_p=f.model_p, kelly_stake=None, odds_event_id=f.event_id,
            commence=f.commence, tag=f"watchlist:{rules}", sport=f.sport,
            edge_open=eo["edge"] if eo else None, open_ref=_eo.ref(eo)))
        ll = getattr(self, "live_log", None) if f.extra.get("in_game") else None
        if ll is not None:
            ll.record(aid, g, "value", f.market, f.selection, f.point, False, f.extra.get("_odds_ts"),
                      f.extra.get("_ev"), f.event_id)
        return text


def _fam_tag(key: str) -> str:
    """value_props key '<event>:<tag><snap>' -> family tag ('' player props, 'team:', 'px:' props-only)."""
    if ":team:" in key:
        return "team:"
    if ":px:" in key:
        return "px:"
    return ""


def _has_my_book(pev: Dict[str, Any]) -> bool:
    from . import mybook
    return any(mybook.is_mine(b.get("key") or b.get("title") or "") and (b.get("markets") or [])
               for b in (pev or {}).get("bookmakers") or [])


def confidence(f: vf.Finding) -> str:
    """Plain-language confidence label (honest about the backtests)."""
    why = [f"{f.n_books} books"]
    if f.player or "team_totals" in f.market:
        lvl = "low"
        why.append("props backtest: no EV bucket beat the vig")
    elif str(f.method).startswith("sharp"):
        lvl = "medium"
        why.append("vs sharp (Pinnacle) no-vig price")
    else:
        lvl = "low" if f.n_books < 5 else "medium"
        why.append("consensus no-vig")
    if f.model_p is not None:
        agree = f.model_p >= f.fair_p
        why.append(f"model {'agrees' if agree else 'disagrees'} (info only)")
    if f.extra.get("low_confidence"):
        lvl, _ = "low", why.append("playoffs")
    return f"{lvl} ({'; '.join(why)})"


def format_value_summary(s: Dict[str, Any]) -> str:
    out = ["AJ Bet Bot value scan (Strategy C):"]
    if s.get("top30"):
        out.append("  NCAAF top-30: " + ", ".join(f"{t['rank']}.{t['abbr']}" for t in s["top30"]))
    for sp, gs in s["scope"].items():
        out.append(f"  {sp}: {len(gs)} in-scope games")
        out += [f"    - {x}" for x in gs[:20]]
        if len(gs) > 20:
            out.append(f"    ... +{len(gs) - 20} more")
    out.append(f"  Odds API calls: {len(s['calls'])} -> " + "; ".join(s["calls"]))
    out.append(f"  priced selections: {s.get('n_findings', 0)}; flagged: {s.get('n_flagged', 0)}; "
               f"new alerts: {len(s['alerts'])}")
    if s.get("watchlist"):
        out.append(f"  watchlist paper bets (no alerts): {len(s['watchlist'])}")
    out += [f"  skipped: {x}" for x in s["skipped"]]
    out.append(f"  CLV updates: {s.get('clv_updates', 0)}")
    out.append(f"  credits remaining ({s.get('key') or 'no key'}): "
               f"{s['credits_remaining'] if s['credits_remaining'] is not None else 'unknown'}")
    return "\n".join(out)
