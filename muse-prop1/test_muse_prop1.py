"""Muse Prop1 — integration tests with mocked PropLine / ESPN payloads.
No network, no keys, no Telegram, no bets. Run: python3 test_muse_prop1.py
"""
import json, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from propline import extract_propline_lines, find_line, configure_bookmakers
from live_feed import parse_scoreboard, parse_summary
from priors import freeze_first_line, frozen_prior, is_pregame_frozen
from quota import QuotaTracker, QuotaExceeded
from runner import evaluate_line, run_once, load_config, load_top30, load_top30_with_warnings, freeze_priors

def load_fixture(name):
    return json.loads((ROOT / "fixtures" / name).read_text())

def _prefreeze_nfl(state):
    """Freeze the fixture's NFL prior as a TRUE pregame freeze (frozen
    before the game's commence_time), so evaluate_line's pregame gate
    accepts it. The fixture game itself is in the past; the test freezes
    with a future commence_time to exercise the real pregame path."""
    freeze_first_line(state, "nfl", "401671234", "Josh Allen",
                      "player_pass_yds", 245.5,
                      commence_time=time.time() + 3600)

def _prefreeze_basketball(state):
    freeze_first_line(state, "ncaab", "99", "Test Player",
                      "player_points", 5.5,
                      commence_time=time.time() + 3600)

def test_extract_propline_lines():
    lines = extract_propline_lines({"payload": load_fixture("propline_nfl.json"), "as_of": 1000}, "nfl")
    assert len(lines) == 2, lines
    allen = [l for l in lines if l["market"] == "player_pass_yds"][0]
    assert allen["player"] == "Josh Allen" and allen["line"] == 245.5
    assert allen["over_price"] == -110 and allen["under_price"] == -110
    # The price source is recorded on the row (auditability).
    assert allen["bookmakers"] == ["kalshi"], allen
    # exact lookup
    assert find_line(lines, "401671234", "Josh Allen", "player_pass_yds", 245.5) is not None
    assert find_line(lines, "401671234", "Josh Allen", "player_pass_yds", 249.5) is None
    # suspended / alternate rows are ignored
    payload = {"payload": {"events": [{"id": "1", "bookmakers": [{"key": "kalshi", "markets": [
        {"key": "player_pass_yds", "suspended_at": "2026-01-01", "outcomes": [
            {"name": "Over", "description": "X", "price": -110, "point": 200.5},
            {"name": "Under", "description": "X", "price": -110, "point": 200.5}]},
        {"key": "player_pass_yds", "line_type": "alternate", "outcomes": [
            {"name": "Over", "description": "Y", "price": -110, "point": 300.5},
            {"name": "Under", "description": "Y", "price": -110, "point": 300.5}]}]}]}]}}
    assert extract_propline_lines(payload, "nfl") == []

def test_propline_median_price_and_bovada_excluded():
    """Price rule (Bovada removal): the row price is the MEDIAN across the
    allowed bookmakers, and "bovada" is never allowed — a payload with only
    Bovada yields no lines at all."""
    configure_bookmakers(None)
    payload = {"payload": {"events": [{"id": "1", "bookmakers": [
        {"key": "kalshi", "markets": [{"key": "player_pass_yds", "outcomes": [
            {"name": "Over", "description": "X", "price": -110, "point": 200.5},
            {"name": "Under", "description": "X", "price": -110, "point": 200.5}]}]},
        {"key": "draftkings", "markets": [{"key": "player_pass_yds", "outcomes": [
            {"name": "Over", "description": "X", "price": -120, "point": 200.5},
            {"name": "Under", "description": "X", "price": -120, "point": 200.5}]}]},
        {"key": "bovada", "markets": [{"key": "player_pass_yds", "outcomes": [
            {"name": "Over", "description": "X", "price": -100, "point": 200.5},
            {"name": "Under", "description": "X", "price": -100, "point": 200.5}]}]},
    ]}]}}
    lines = extract_propline_lines(payload, "nfl")
    assert len(lines) == 1, lines
    # Median of -110 and -120 is -115; Bovada's -100 is excluded.
    assert lines[0]["over_price"] == -115, lines[0]
    assert lines[0]["under_price"] == -115, lines[0]
    assert lines[0]["bookmakers"] == ["draftkings", "kalshi"], lines[0]
    # Bovada-only payload -> no lines.
    bovada_only = {"payload": {"events": [{"id": "1", "bookmakers": [
        {"key": "bovada", "markets": [{"key": "player_pass_yds", "outcomes": [
            {"name": "Over", "description": "X", "price": -110, "point": 200.5},
            {"name": "Under", "description": "X", "price": -110, "point": 200.5}]}]}]}]}}
    assert extract_propline_lines(bovada_only, "nfl") == []
    # The allowlist can never re-admit a banned book.
    configure_bookmakers(["kalshi", "bovada"])
    assert extract_propline_lines(payload, "nfl")[0]["bookmakers"] == ["kalshi"]
    configure_bookmakers(None)

def test_parse_espn():
    games = parse_scoreboard({"payload": load_fixture("espn_nfl_scoreboard.json"), "as_of": 1000}, "nfl")
    assert len(games) == 1 and games[0]["elapsed_min"] == 25.0
    game = parse_summary({"payload": load_fixture("espn_nfl_summary.json"), "as_of": 1000}, "nfl")
    assert game["players"]["Josh Allen"]["pass_yds"] == 142.0
    assert game["players"]["Josh Allen"]["rush_yds"] == 18.0

def test_evaluate_line_fires_and_dedupes():
    cfg = load_config()
    lines = extract_propline_lines({"payload": load_fixture("propline_nfl.json"), "as_of": 1000}, "nfl")
    game = parse_summary({"payload": load_fixture("espn_nfl_summary.json"), "as_of": 1000}, "nfl")
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        _prefreeze_nfl(state)
        pick = evaluate_line("nfl", lines[0], game, cfg, {}, state)
        assert pick is not None and pick["side"] == "over" and pick["model_p"] > 0.6, pick
        # The pick records the line anchor (Grok flaw 1): raw projection
        # above the line, anchored projection between raw and line.
        assert pick["projected_raw"] > pick["line"] > 0, pick
        assert pick["line"] < pick["projected_final"] < pick["projected_raw"], pick
        assert pick["line_anchor_weight"] == 0.5, pick
        assert pick["odds_source"] == "propline", pick
        # near-projection line: model ~50%, no edge either side -> no pick
        cold = dict(lines[0]); cold["line"] = 306.5
        assert evaluate_line("nfl", cold, game, cfg, {}, state) is None
        # far line the other way: Under 400.5 SHOULD fire (model ~100% under)
        far = dict(lines[0]); far["line"] = 400.5
        under = evaluate_line("nfl", far, game, cfg, {}, state)
        assert under is not None and under["side"] == "under", under

def test_pregame_prior_gate():
    """Grok flaw 2: a prior frozen AFTER the game started is rejected
    (skip "prior_not_pregame"); a prior frozen BEFORE the start is
    accepted; a legacy entry (no frozen_at) is rejected; a priors.yaml
    override bypasses the gate."""
    cfg = load_config()
    lines = extract_propline_lines({"payload": load_fixture("propline_nfl.json"), "as_of": 1000}, "nfl")
    game = parse_summary({"payload": load_fixture("espn_nfl_summary.json"), "as_of": 1000}, "nfl")
    # (a) Frozen after the start -> rejected.
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        freeze_first_line(state, "nfl", "401671234", "Josh Allen",
                          "player_pass_yds", 245.5,
                          commence_time=time.time() - 3600)
        assert evaluate_line("nfl", lines[0], game, cfg, {}, state) is None
        assert "prior_not_pregame" in (state / "skips.jsonl").read_text()
    # (b) Frozen before the start -> accepted.
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        _prefreeze_nfl(state)
        assert evaluate_line("nfl", lines[0], game, cfg, {}, state) is not None
    # (c) Legacy entry (plain number, no frozen_at) -> rejected.
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        (state / "first_lines.json").write_text(json.dumps(
            {"nfl|401671234|Josh Allen|player_pass_yds": 245.5}))
        assert frozen_prior(state, "nfl", "401671234", "Josh Allen",
                            "player_pass_yds") is not None
        assert not is_pregame_frozen(
            frozen_prior(state, "nfl", "401671234", "Josh Allen", "player_pass_yds"))
        assert evaluate_line("nfl", lines[0], game, cfg, {}, state) is None
    # (d) priors.yaml override bypasses the gate (the override mean is a
    # real pregame prior by construction).
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        freeze_first_line(state, "nfl", "401671234", "Josh Allen",
                          "player_pass_yds", 245.5,
                          commence_time=time.time() - 3600)
        override = {"Josh Allen": {"player_pass_yds": {"mean": 245.0, "sd": 55.0, "per_play": 3.8}}}
        assert evaluate_line("nfl", lines[0], game, cfg, override, state) is not None

def test_freeze_priors_only_pregame():
    """Grok flaw 2: --freeze-priors freezes only games whose commence_time
    is still in the future; already-started games are skipped, and an
    existing frozen line is never overwritten."""
    import runner as _runner
    orig_fetch = _runner.fetch_propline_props
    now = time.time()
    payload = {"sport": "nfl", "as_of": now, "payload": {"events": [
        {"id": "100", "home_team": "A", "away_team": "B",
         "commence_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 7200)),
         "bookmakers": [{"key": "kalshi", "markets": [{"key": "player_pass_yds", "outcomes": [
             {"name": "Over", "description": "Future QB", "price": -110, "point": 250.5},
             {"name": "Under", "description": "Future QB", "price": -110, "point": 250.5}]}]}]},
        {"id": "101", "home_team": "C", "away_team": "D",
         "commence_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 7200)),
         "bookmakers": [{"key": "kalshi", "markets": [{"key": "player_pass_yds", "outcomes": [
             {"name": "Over", "description": "Started QB", "price": -110, "point": 240.5},
             {"name": "Under", "description": "Started QB", "price": -110, "point": 240.5}]}]}]},
    ]}}
    try:
        _runner.fetch_propline_props = lambda sport, api_key=None: payload
        cfg = load_config()
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            counts = freeze_priors(cfg, ["nfl"], state)
            assert counts["frozen"] == 1, counts
            assert counts["started"] == 1, counts
            entry = frozen_prior(state, "nfl", "100", "Future QB", "player_pass_yds")
            assert entry is not None and entry["line"] == 250.5
            assert is_pregame_frozen(entry)
            assert frozen_prior(state, "nfl", "101", "Started QB", "player_pass_yds") is None
            # Second run: the frozen line is already there, not re-frozen.
            counts2 = freeze_priors(cfg, ["nfl"], state)
            assert counts2["frozen"] == 0 and counts2["already"] == 1, counts2
    finally:
        _runner.fetch_propline_props = orig_fetch

def test_no_live_games_skips_propline():
    """FREE-TIER GATE: when ESPN reports no live games for a sport, run_once
    must not call PropLine at all for that sport (no wasted bulk calls)."""
    import runner as _runner
    orig_games = _runner.fetch_live_games
    orig_odds = _runner.fetch_propline_props
    odds_calls = []
    try:
        def no_games(sport, timeout=10):
            return {"sport": sport, "as_of": 1000, "payload": {"events": []}}
        def count_odds(sport, api_key=None):
            odds_calls.append(sport)
            return {"sport": sport, "as_of": 1000, "payload": {"events": []}}
        _runner.fetch_live_games = no_games
        _runner.fetch_propline_props = count_odds
        cfg = load_config()
        with tempfile.TemporaryDirectory() as td:
            picks = run_once(cfg, ["nfl", "ncaaf", "ncaab", "ncaaw"],
                             "log_only", dry_run=False, state_dir=Path(td))
        assert picks == [], picks
        assert odds_calls == [], f"PropLine called with no live games: {odds_calls}"
    finally:
        _runner.fetch_live_games = orig_games
        _runner.fetch_propline_props = orig_odds

def test_free_tier_pace_config():
    """FREE-TIER PACE (Grok flaw 3): 300s per sport, log-only, 4 sports,
    and the quota budgets are present in config."""
    from runner import _poll_interval_seconds
    cfg = load_config()
    assert cfg["poll_seconds"] == 300, cfg["poll_seconds"]
    assert cfg["basketball_poll_seconds"] == 300, cfg["basketball_poll_seconds"]
    assert _poll_interval_seconds("nfl", cfg) == 300
    assert _poll_interval_seconds("ncaaf", cfg) == 300
    assert _poll_interval_seconds("ncaab", cfg) == 300
    assert _poll_interval_seconds("ncaaw", cfg) == 300
    assert cfg.get("mode") == "log_only"
    assert set(cfg["sports"]) == {"nfl", "ncaaf_top30", "ncaab", "ncaaw"}
    assert cfg["propline_daily_budget"] == 500, cfg
    assert cfg["propline_monthly_budget"] == 1000, cfg
    assert cfg["odds_source"] == "propline", cfg
    assert cfg.get("use_live_pace") is False, cfg
    assert cfg.get("line_anchor_weight") == 0.5, cfg
    assert cfg.get("require_pregame_prior") is True, cfg

def test_quota_tracker():
    """Grok flaw 3: the quota tracker refuses (fail-closed) once a budget
    is hit, and rolls the day/month over on the calendar."""
    from datetime import datetime, timezone
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        q = QuotaTracker(state, daily_budget=5, monthly_budget=100)
        d1 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        for _ in range(5):
            q.reserve(1, now=d1)
        try:
            q.reserve(1, now=d1)
        except QuotaExceeded:
            pass
        else:
            raise AssertionError("6th call on a 5/day budget should raise")
        # Next day the daily count resets; the monthly count carries.
        d2 = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
        u = q.reserve(1, now=d2)
        assert u["day_count"] == 1 and u["month_count"] == 6, u
        # Monthly budget binds independently of the daily one (fresh state
        # dir — one tracker owns one usage file in production).
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        q2 = QuotaTracker(state, daily_budget=100, monthly_budget=3)
        d3 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        for _ in range(3):
            q2.reserve(1, now=d3)
        try:
            q2.reserve(1, now=d3)
        except QuotaExceeded:
            pass
        else:
            raise AssertionError("4th call on a 3/month budget should raise")

def test_top30_espn_mapping_complete():
    """Grok flaw 4: every Top-30 AP name maps to exactly one ESPN
    displayName, and every mapped name exists in the ESPN teams fixture
    (verified live 2026-11-10). The runner matches ESPN names, not AP
    spellings — so the AP spelling itself must NOT be in the set."""
    import yaml
    top30 = load_top30()
    assert len(top30) == 30, top30
    assert "Georgia Bulldogs" in top30 and "Georgia" not in top30
    assert "Arizona Wildcats" in top30 and "Arizona State Sun Devils" not in top30
    assert "Texas Longhorns" in top30 and "Texas Tech Red Raiders" in top30
    assert "Miami Hurricanes" in top30 and "Miami (OH) RedHawks" not in top30
    assert "Iowa Hawkeyes" in top30 and "Iowa State Cyclones" not in top30
    assert "Oregon Ducks" in top30 and "Oregon State Beavers" not in top30
    assert "Ole Miss Rebels" in top30
    _, missing = load_top30_with_warnings()
    assert missing == [], missing
    # Every mapped name is a real ESPN displayName (fixture = live ESPN
    # teams endpoint, 2026-11-10).
    espn_names = set(load_fixture("espn_ncaaf_teams.json").keys())
    data = yaml.safe_load((ROOT / "top30_ncaaf.yaml").read_text())
    mapping = data["espn_display_names"]
    assert set(mapping.keys()) == set(data["teams"])
    for ap, espn in mapping.items():
        assert espn in espn_names, f"{ap} -> {espn!r} is not an ESPN displayName"

def test_ncaaf_top30_label_maps_to_ncaaf():
    """Config lists 'ncaaf_top30'; the engine key is 'ncaaf'. A default run
    must poll NCAAF under the right key, not silently skip it."""
    import runner as _runner
    orig_games = _runner.fetch_live_games
    orig_odds = _runner.fetch_propline_props
    orig_summary = _runner.fetch_game_summary
    odds_calls = []
    try:
        def one_live_game(sport, timeout=10):
            comp = {"status": {"type": {"state": "in"}, "period": 2,
                               "displayClock": "10:00"},
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": "A"},
                         "score": "14"},
                        {"homeAway": "away", "team": {"displayName": "B"},
                         "score": "7"}]}
            event = {"id": "1", "competitions": [comp]}
            return {"sport": sport, "as_of": 1000,
                    "payload": {"events": [event]}}
        def count_odds(sport, api_key=None):
            odds_calls.append(sport)
            return {"sport": sport, "as_of": 1000, "payload": {"events": []}}
        def empty_summary(sport, event_id, timeout=10):
            return {"sport": sport, "as_of": 1000, "payload": {"header": {
                "id": "1", "competitions": [{"status": {"type": {"state": "in"},
                    "period": 2, "displayClock": "10:00"},
                    "competitors": [{"homeAway": "home", "score": "14"},
                                    {"homeAway": "away", "score": "7"}]}]},
                "boxscore": {"players": [], "teams": []}}}
        _runner.fetch_live_games = one_live_game
        _runner.fetch_propline_props = count_odds
        _runner.fetch_game_summary = empty_summary
        cfg = load_config()
        with tempfile.TemporaryDirectory() as td:
            picks = run_once(cfg, ["ncaaf_top30"], "log_only",
                             dry_run=False, state_dir=Path(td))
        assert odds_calls == ["ncaaf"], odds_calls
        assert picks == []
    finally:
        _runner.fetch_live_games = orig_games
        _runner.fetch_propline_props = orig_odds
        _runner.fetch_game_summary = orig_summary

def test_dry_run_once():
    cfg = load_config()
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        picks = run_once(cfg, ["nfl"], "log_only", dry_run=True, state_dir=state)
        assert len(picks) == 2, picks
        picks2 = run_once(cfg, ["nfl"], "log_only", dry_run=True, state_dir=state)
        assert picks2 == [], "dedupe must block a second pick for the same line"
        assert (state / "picks.jsonl").exists()

def _basketball_summary_payload():
    """Synthetic NCAAB summary: home 40 - away 38 at 20:00 of half 1.
    Home boxscore: FGA 40-86 -> 86 att, OREB 12, TOV 10, FTA 12-18 -> 18 att
    => poss = 86 - 12 + 10 + 0.44*18 = 91.92. Away: FGA 37-80 -> 80 att,
    OREB 9, TOV 13, FTA 20-24 -> 24 att => poss = 80 - 9 + 13 + 10.56 = 94.56.
    Game possessions = (91.92 + 94.56) / 2 = 93.24."""
    return {"payload": {
        "header": {"id": "99", "competitions": [{
            "status": {"type": {"state": "in"}, "period": 1, "displayClock": "20:00"},
            "competitors": [
                {"homeAway": "home", "team": {"id": "1", "displayName": "Home"},
                 "score": "40"},
                {"homeAway": "away", "team": {"id": "2", "displayName": "Away"},
                 "score": "38"}]}]},
        "boxscore": {
            "teams": [
                {"team": {"id": "1"}, "statistics": [
                    {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "40-86"},
                    {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "12-18"},
                    {"name": "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "displayValue": "8-20"},
                    {"name": "totalRebounds", "displayValue": "35"},
                    {"name": "offensiveRebounds", "displayValue": "12"},
                    {"name": "assists", "displayValue": "15"},
                    {"name": "turnovers", "displayValue": "10"}]},
                {"team": {"id": "2"}, "statistics": [
                    {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "37-80"},
                    {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "20-24"},
                    {"name": "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "displayValue": "6-18"},
                    {"name": "totalRebounds", "displayValue": "30"},
                    {"name": "offensiveRebounds", "displayValue": "9"},
                    {"name": "assists", "displayValue": "12"},
                    {"name": "turnovers", "displayValue": "13"}]}],
            "players": []}},
        "as_of": 1000}

def test_parse_summary_basketball_possessions():
    game = parse_summary(_basketball_summary_payload(), "ncaab")
    assert game["home_team_id"] == "1" and game["away_team_id"] == "2"
    assert game["team_totals"]["1"]["fga"] == 86.0
    assert game["team_totals"]["1"]["fta"] == 18.0
    assert game["team_totals"]["1"]["oreb"] == 12.0
    assert game["team_totals"]["1"]["points"] == 40.0
    assert game["team_totals"]["2"]["threes"] == 6.0
    poss = game["possessions"]
    assert poss is not None
    assert abs(poss["home"] - 91.92) < 1e-9, poss
    assert abs(poss["away"] - 94.56) < 1e-9, poss
    assert abs(poss["game"] - 93.24) < 1e-9, poss

def _basketball_game(poss_game):
    """Synthetic live NCAAB game state for evaluate_line: 20 min elapsed,
    Test Player has 14 pts in 18 min played, 1 foul."""
    return {
        "game_id": "99", "sport": "ncaab", "elapsed_min": 20.0,
        "remaining_min": 20.0, "home_score": 40, "away_score": 38,
        "home_team_id": "1", "away_team_id": "2",
        "team_totals": {}, "possessions": {"home": poss_game, "away": poss_game,
                                           "game": poss_game},
        "players": {"Test Player": {
            "name": "Test Player", "points": 14.0, "minutes": 18.0, "fouls": 1,
            "rebounds": 5.0, "assists": 3.0, "threes": 2.0,
            "pr": 19.0, "pa": 17.0, "ra": 8.0, "pra": 22.0}},
    }

def _basketball_line(line=5.5):
    return {"sport": "ncaab", "game_id": "99", "home_team": "Home",
            "away_team": "Away", "player": "Test Player", "player_id": None,
            "market": "player_points", "line": line,
            "over_price": -110, "under_price": -110, "as_of": 1000}

def test_evaluate_line_basketball_rate_only_default():
    """Rate-only rework wired into the live path (Grok flaw 5): with the
    default config (use_live_pace=false) the live game pace does NOT move
    the projection — a fast game (45 poss) and a slow game (35 poss) at the
    same elapsed project identically. With use_live_pace=True the old
    behaviour returns (fast > slow), proving the flag does something.
    Prior mean = first-seen line (5.5), so the over fires in both cases."""
    cfg = load_config()
    assert cfg.get("use_live_pace") is False, cfg
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        _prefreeze_basketball(state)
        fast = evaluate_line("ncaab", _basketball_line(), _basketball_game(45.0),
                             cfg, {}, state)
        assert fast is not None and fast["side"] == "over", fast
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        _prefreeze_basketball(state)
        slow = evaluate_line("ncaab", _basketball_line(), _basketball_game(35.0),
                             cfg, {}, state)
        assert slow is not None and slow["side"] == "over", slow
    assert abs(fast["projected_final"] - slow["projected_final"]) < 1e-9, (fast, slow)
    # Live pace ON (explicit override): fast must project higher than slow.
    cfg_live = dict(cfg); cfg_live["use_live_pace"] = True
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        _prefreeze_basketball(state)
        fast_on = evaluate_line("ncaab", _basketball_line(), _basketball_game(45.0),
                                cfg_live, {}, state)
        assert fast_on is not None, fast_on
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        _prefreeze_basketball(state)
        slow_on = evaluate_line("ncaab", _basketball_line(), _basketball_game(35.0),
                                cfg_live, {}, state)
        assert slow_on is not None, slow_on
    assert fast_on["projected_final"] > slow_on["projected_final"], (fast_on, slow_on)

def test_evaluate_line_basketball_no_possessions():
    """A basketball summary with no parseable team totals: with live pace ON
    the line is skipped (fail-closed, "no_team_possessions") because the
    pace ratio needs real possessions. With the rate-only default (pace
    ratio 1.0) possessions are not needed, so the line is evaluated, not
    skipped for that reason."""
    cfg = load_config()
    game = _basketball_game(35.0)
    game["possessions"] = None
    # Live pace ON -> fail-closed skip.
    cfg_live = dict(cfg); cfg_live["use_live_pace"] = True
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        _prefreeze_basketball(state)
        assert evaluate_line("ncaab", _basketball_line(), game, cfg_live, {}, state) is None
        skips = (state / "skips.jsonl").read_text()
        assert "no_team_possessions" in skips, skips
    # Rate-only default -> evaluated (fires over here), no possessions skip.
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        _prefreeze_basketball(state)
        pick = evaluate_line("ncaab", _basketball_line(), game, cfg, {}, state)
        assert pick is not None and pick["side"] == "over", pick
        skips = (state / "skips.jsonl").read_text() if (state / "skips.jsonl").exists() else ""
        assert "no_team_possessions" not in skips, skips

def test_nba_clock_sub_minute_format():
    """NBA clocks go 'SS.S' (seconds with tenths) under a minute — the
    colon-only parser returned elapsed 0.0 for those, which broke minutes
    reconstruction. Q3, 4.4s left = 24 + (12 - 4.4/60) = 35.93 elapsed."""
    from live_feed import _clock_to_elapsed
    elapsed, remaining = _clock_to_elapsed("nba", 3, "4.4")
    assert abs(elapsed - 35.9267) < 0.01, elapsed
    assert abs(remaining - (48.0 - 35.9267)) < 0.01, remaining
    # colon format still works, and period starts are exact
    elapsed, _ = _clock_to_elapsed("nba", 2, "12:00")
    assert elapsed == 12.0, elapsed
    elapsed, _ = _clock_to_elapsed("nba", 1, "5:22")
    assert abs(elapsed - 6.6333) < 0.01, elapsed

def test_nba_parse_summary_basketball_branch():
    """parse_summary returns team totals + possessions for the NBA too
    (additive — college branches untouched)."""
    def team_stats(tid, fga, fta, reb, oreb, ast, tov):
        return {"team": {"id": tid}, "statistics": [
            {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": fga},
            {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": fta},
            {"name": "totalRebounds", "displayValue": str(reb)},
            {"name": "offensiveRebounds", "displayValue": str(oreb)},
            {"name": "assists", "displayValue": str(ast)},
            {"name": "turnovers", "displayValue": str(tov)}]}
    payload = {"payload": {
        "header": {"id": "1", "competitions": [{
            "status": {"period": 2, "displayClock": "6:00", "type": {"state": "in"}},
            "competitors": [
                {"team": {"id": "1"}, "homeAway": "home", "score": 50},
                {"team": {"id": "2"}, "homeAway": "away", "score": 48}]}]},
        "boxscore": {"teams": [
            team_stats("1", "20-40", "8-10", 20, 5, 12, 7),
            team_stats("2", "19-42", "6-8", 22, 6, 11, 8)],
            "players": []}}, "as_of": 1000}
    game = parse_summary(payload, "nba")
    assert game["elapsed_min"] == 18.0, game["elapsed_min"]
    assert game["team_totals"]["1"]["fga"] == 40.0
    assert game["possessions"] is not None
    assert game["possessions"]["game"] > 0

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"PASS {name}")
    print("all Muse Prop1 integration tests passed")
