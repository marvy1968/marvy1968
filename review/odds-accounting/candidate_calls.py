"""Candidate observe-only calls. Nothing in the running tree imports this module.

The functions repeat the Odds API request shape from a named source file and
record a job tag. They do not replace those files. ODDS_ACCOUNTING_LEDGER is
not set here, so a call records a row only when a test or an operator has
already pointed the ledger at a scratch database.

Repo files and the VM copies of live_monitor, main, and live_prop_engine stay
on their current imports. VM alt70 is not in the running repository.
"""

from __future__ import annotations

# VM export 2026-11-08: marv e7d1f50c, feed a5d7bdbd, muse c3cb88de.
# These names are the candidate job tags, not labels the running processes emit.

VM_LIVE = "vm-marv-live"
VM_MAIN_PROBE = "vm-marv-main-probe"
VM_LIVE_PROP_ENGINE = "vm-live-prop-engine"
VM_ALT70_NFL = "vm-alt70-nfl"
VM_ALT70_NCAAF = "vm-alt70-ncaaf"


def _note(source_job, *, response=None, transport_error=None, **fields):
    try:
        from marv.data.odds_observe import note_hops
        note_hops(source_job=source_job, response=response, transport_error=transport_error, **fields)
    except Exception:
        return


def requests_call(get, url, params, *, timeout, source_job, endpoint_template, sport=None,
                  markets=None, regions=None, bookmakers=None, estimated_cost=None,
                  estimate_provenance="not_supplied"):
    """One library call. timeout None means the requests call omits timeout, as the source does."""
    kwargs = {"params": params}
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        response = get(url, **kwargs)
    except Exception as exc:
        _note(source_job, transport_error=exc, endpoint_template=endpoint_template, sport=sport,
              markets=markets, regions=regions, bookmakers=bookmakers, estimated_cost=estimated_cost,
              estimate_provenance=estimate_provenance)
        raise
    _note(source_job, response=response, endpoint_template=endpoint_template, sport=sport,
          markets=markets, regions=regions, bookmakers=bookmakers, estimated_cost=estimated_cost,
          estimate_provenance=estimate_provenance)
    return response


def featured_odds(get, *, sport, api_key, markets, regions="us", bookmakers=None, date_format=None,
                  timeout=None, source_job, extra_params=None):
    params = {"apiKey": api_key, "regions": regions, "markets": markets, "oddsFormat": "american"}
    if bookmakers is not None:
        params["bookmakers"] = bookmakers
    if date_format is not None:
        params["dateFormat"] = date_format
    if extra_params:
        params.update(extra_params)
    return requests_call(
        get, f"https://api.the-odds-api.com/v4/sports/{sport}/odds", params, timeout=timeout,
        source_job=source_job, endpoint_template="/v4/sports/{sport}/odds", sport=sport,
        markets=markets.split(","), regions=regions.split(","),
        bookmakers=None if bookmakers is None else bookmakers.split(","),
        estimate_provenance="not_supplied")


def events_list(get, *, sport, api_key, timeout=None, source_job):
    return requests_call(
        get, f"https://api.the-odds-api.com/v4/sports/{sport}/events", {"apiKey": api_key},
        timeout=timeout, source_job=source_job, endpoint_template="/v4/sports/{sport}/events",
        sport=sport, estimated_cost=0, estimate_provenance="documented_free_events")


def event_odds(get, *, sport, event_id, api_key, markets, regions="us", timeout=None, source_job):
    return requests_call(
        get, f"https://api.the-odds-api.com/v4/sports/{sport}/events/{event_id}/odds",
        {"apiKey": api_key, "regions": regions, "markets": markets, "oddsFormat": "american"},
        timeout=timeout, source_job=source_job,
        endpoint_template="/v4/sports/{sport}/events/{event}/odds", sport=sport,
        markets=markets.split(","), regions=regions.split(","), estimate_provenance="not_supplied")


def historical_featured(get, *, sport, api_key, date, markets, regions="us", bookmakers=None,
                        timeout=None, source_job):
    params = {"apiKey": api_key, "regions": regions, "markets": markets, "oddsFormat": "american", "date": date}
    if bookmakers is not None:
        params["bookmakers"] = bookmakers
    return requests_call(
        get, f"https://api.the-odds-api.com/v4/historical/sports/{sport}/odds", params, timeout=timeout,
        source_job=source_job, endpoint_template="/v4/historical/sports/{sport}/odds", sport=sport,
        markets=markets.split(","), regions=regions.split(","),
        bookmakers=None if bookmakers is None else bookmakers.split(","),
        estimate_provenance="not_supplied")


# Exact request shapes. The running module is not changed and does not call these.
REPO_DIRECT = (
    {"id": "repo-forex-app", "file": "forex_bots/masterbot_oanda/app.py", "kind": "featured",
     "sport": "basketball_ncaab", "markets": "totals,h2h,spreads", "bookmakers_joined": True,
     "date_format": "iso", "timeout": 20, "needle": "dateFormat"},
    {"id": "repo-nfl-team-props-events", "file": "forex_bots/masterbot_oanda/nfl_team_props.py",
     "kind": "events", "sport": "americanfootball_nfl", "timeout": None, "needle": "americanfootball_nfl"},
    {"id": "repo-nfl-team-props-odds", "file": "forex_bots/masterbot_oanda/nfl_team_props.py",
     "kind": "event", "sport": "americanfootball_nfl",
     "markets": "team_totals,player_passing_yards,player_rushing_yards,player_receiving_yards,player_touchdowns",
     "timeout": None, "needle": "player_touchdowns"},
    {"id": "repo-nba-team-props-events", "file": "forex_bots/masterbot_oanda/nba_team_props.py",
     "kind": "events", "sport": "basketball_nba", "timeout": None, "needle": "basketball_nba"},
    {"id": "repo-nba-team-props-odds", "file": "forex_bots/masterbot_oanda/nba_team_props.py",
     "kind": "event", "sport": "basketball_nba",
     "markets": "team_totals,player_points,player_rebounds,player_assists,player_points_rebounds_assists,player_threes",
     "timeout": None, "needle": "player_threes"},
    {"id": "repo-wnba-team-props-events", "file": "forex_bots/masterbot_oanda/wnba_team_props.py",
     "kind": "events", "sport": "basketball_wnba", "timeout": None, "needle": "basketball_wnba"},
    {"id": "repo-wnba-team-props-odds", "file": "forex_bots/masterbot_oanda/wnba_team_props.py",
     "kind": "event", "sport": "basketball_wnba",
     "markets": "team_totals,player_points,player_rebounds,player_assists,player_points_rebounds_assists,player_threes",
     "timeout": None, "needle": "player_threes"},
    {"id": "repo-ncaaf-team-props-events", "file": "forex_bots/masterbot_oanda/ncaaf_team_props.py",
     "kind": "events", "sport": "americanfootball_ncaaf", "timeout": None, "needle": "americanfootball_ncaaf"},
    {"id": "repo-ncaaf-team-props-odds", "file": "forex_bots/masterbot_oanda/ncaaf_team_props.py",
     "kind": "event", "sport": "americanfootball_ncaaf",
     "markets": "team_totals,player_passing_yards,player_rushing_yards,player_receiving_yards,player_touchdowns",
     "timeout": None, "needle": "player_touchdowns"},
    {"id": "repo-mlb-team-props-events", "file": "forex_bots/masterbot_oanda/mlb_team_props.py",
     "kind": "events", "sport": "baseball_mlb", "timeout": None, "needle": "baseball_mlb"},
    {"id": "repo-mlb-team-props-odds", "file": "forex_bots/masterbot_oanda/mlb_team_props.py",
     "kind": "event", "sport": "baseball_mlb",
     "markets": "team_totals,player_hits,player_runs,player_rbi,player_strikeouts,player_home_runs",
     "timeout": None, "needle": "player_home_runs"},
    {"id": "repo-clean-master-events", "file": "forex_bots/masterbot_oanda/clean_master.py",
     "kind": "events", "sport": "basketball_nba", "timeout": None, "needle": "basketball_nba"},
    {"id": "repo-all-engines-events", "file": "forex_bots/masterbot_oanda/all_engines_live.py",
     "kind": "events", "sport": "basketball_nba", "timeout": None, "needle": "/events"},
    {"id": "repo-all-engines-odds", "file": "forex_bots/masterbot_oanda/all_engines_live.py",
     "kind": "event", "sport": "basketball_nba", "markets": "team_totals", "timeout": None,
     "needle": "team_totals"},
    {"id": "repo-basketball-engine-events", "file": "forex_bots/masterbot_oanda/basketball_team_props_engine.py",
     "kind": "events", "sport": "basketball_nba", "timeout": None, "needle": "/events"},
    {"id": "repo-basketball-engine-odds", "file": "forex_bots/masterbot_oanda/basketball_team_props_engine.py",
     "kind": "event", "sport": "basketball_nba",
     "markets": "team_totals,team_totals_h1,team_totals_h2,team_totals_q1,team_totals_q2,team_totals_q3,team_totals_q4",
     "timeout": None, "needle": "team_totals_q4"},
    {"id": "repo-basketball-master-events", "file": "forex_bots/masterbot_oanda/basketball_team_props_master.py",
     "kind": "events", "sport": "basketball_nba", "timeout": None, "needle": "/events"},
    {"id": "repo-basketball-master-odds", "file": "forex_bots/masterbot_oanda/basketball_team_props_master.py",
     "kind": "event", "sport": "basketball_nba", "markets": "team_totals", "timeout": None,
     "needle": "oddsFormat"},
    {"id": "repo-bovada-hist", "file": "tools/fetch_bovada_nfl.py", "kind": "historical",
     "sport": "americanfootball_nfl", "markets": "h2h,spreads,totals", "bookmakers": "bovada",
     "timeout": 40, "needle": "timeout=40"},
)


def run_repo_direct(spec, get, api_key, *, event_id="event-1", bookmakers=None, date="2026-01-01T16:00:00Z"):
    kind = spec["kind"]
    job = spec["id"]
    if kind == "featured":
        books = ",".join(bookmakers or []) if spec.get("bookmakers_joined") else spec.get("bookmakers")
        return featured_odds(
            get, sport=spec["sport"], api_key=api_key, markets=spec["markets"], bookmakers=books,
            date_format=spec.get("date_format"), timeout=spec["timeout"], source_job=job)
    if kind == "events":
        return events_list(get, sport=spec["sport"], api_key=api_key, timeout=spec["timeout"], source_job=job)
    if kind == "event":
        return event_odds(
            get, sport=spec["sport"], event_id=event_id, api_key=api_key, markets=spec["markets"],
            timeout=spec["timeout"], source_job=job)
    if kind == "historical":
        return historical_featured(
            get, sport=spec["sport"], api_key=api_key, date=date, markets=spec["markets"],
            bookmakers=spec.get("bookmakers"), timeout=spec["timeout"], source_job=job)
    raise KeyError(spec["kind"])


async def aiohttp_get(session, url, params, *, source_job, endpoint_template, sport=None, markets=None,
                      regions=None, bookmakers=None):
    """Same session.get(url, params=params) shape. The response is used inside the context."""
    async with session.get(url, params=params) as response:
        _note(source_job, response=response, endpoint_template=endpoint_template, sport=sport,
              markets=markets, regions=regions, bookmakers=bookmakers, estimate_provenance="not_supplied")
        return response


# VM callers. Each function is the odds call only. The surrounding file is not replaced.
def vm_live_monitor_fetch(fetch, api_key, sport_keys):
    """VM and repo live_monitor._live_odds loop, with the candidate job tag."""
    events, errors = [], []
    for key in sport_keys:
        try:
            events += fetch(api_key, key, source_job=VM_LIVE)
        except Exception as exc:
            errors.append((key, exc))
    return events, errors


def vm_main_probe_events(events, api_key):
    return events(api_key, "americanfootball_nfl", source_job=VM_MAIN_PROBE)


def vm_main_probe_props(event_props, api_key, event_id, markets, book):
    return event_props(api_key, "americanfootball_nfl", event_id, markets, book, source_job=VM_MAIN_PROBE)


def vm_live_prop_engine_events(events, api_key, odds_sport):
    return events(api_key, odds_sport, source_job=VM_LIVE_PROP_ENGINE)


def vm_live_prop_engine_props(event_props, api_key, odds_sport, event_id, markets):
    return event_props(api_key, odds_sport, event_id, markets, "bovada", source_job=VM_LIVE_PROP_ENGINE)


def vm_alt70_events(events, api_key, sport):
    job = VM_ALT70_NFL if sport == "americanfootball_nfl" else VM_ALT70_NCAAF
    return events(api_key, sport, source_job=job)


def vm_alt70_props(event_props, api_key, sport, event_id, markets, budget):
    job = VM_ALT70_NFL if sport == "americanfootball_nfl" else VM_ALT70_NCAAF
    return event_props(api_key, sport, event_id, markets, "bovada", budget, source_job=job)
