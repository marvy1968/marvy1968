"""Muse Prop1 — integration tests with mocked PropLine / ESPN payloads.
No network, no keys, no Telegram, no bets. Run: python3 test_muse_prop1.py
"""
import json, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from bovada import extract_bovada_lines, find_line
from live_feed import parse_scoreboard, parse_summary
from runner import evaluate_line, run_once, load_config

def load_fixture(name):
    return json.loads((ROOT / "fixtures" / name).read_text())

def test_extract_bovada_lines():
    lines = extract_bovada_lines({"payload": load_fixture("propline_nfl.json"), "as_of": 1000}, "nfl")
    assert len(lines) == 2, lines
    allen = [l for l in lines if l["market"] == "player_pass_yds"][0]
    assert allen["player"] == "Josh Allen" and allen["line"] == 245.5
    assert allen["over_price"] == -110 and allen["under_price"] == -110
    # exact lookup
    assert find_line(lines, "401671234", "Josh Allen", "player_pass_yds", 245.5) is not None
    assert find_line(lines, "401671234", "Josh Allen", "player_pass_yds", 249.5) is None
    # suspended / alternate rows are ignored
    payload = {"payload": {"events": [{"id": "1", "bookmakers": [{"key": "bovada", "markets": [
        {"key": "player_pass_yds", "suspended_at": "2026-01-01", "outcomes": [
            {"name": "Over", "description": "X", "price": -110, "point": 200.5},
            {"name": "Under", "description": "X", "price": -110, "point": 200.5}]},
        {"key": "player_pass_yds", "line_type": "alternate", "outcomes": [
            {"name": "Over", "description": "Y", "price": -110, "point": 300.5},
            {"name": "Under", "description": "Y", "price": -110, "point": 300.5}]}]}]}]}}
    assert extract_bovada_lines(payload, "nfl") == []

def test_parse_espn():
    games = parse_scoreboard({"payload": load_fixture("espn_nfl_scoreboard.json"), "as_of": 1000}, "nfl")
    assert len(games) == 1 and games[0]["elapsed_min"] == 25.0
    game = parse_summary({"payload": load_fixture("espn_nfl_summary.json"), "as_of": 1000}, "nfl")
    assert game["players"]["Josh Allen"]["pass_yds"] == 142.0
    assert game["players"]["Josh Allen"]["rush_yds"] == 18.0

def test_evaluate_line_fires_and_dedupes():
    cfg = load_config()
    lines = extract_bovada_lines({"payload": load_fixture("propline_nfl.json"), "as_of": 1000}, "nfl")
    game = parse_summary({"payload": load_fixture("espn_nfl_summary.json"), "as_of": 1000}, "nfl")
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        pick = evaluate_line("nfl", lines[0], game, cfg, {}, state)
        assert pick is not None and pick["side"] == "over" and pick["model_p"] > 0.6, pick
        # near-projection line: model ~50%, no edge either side -> no pick
        cold = dict(lines[0]); cold["line"] = 306.5
        assert evaluate_line("nfl", cold, game, cfg, {}, state) is None
        # far line the other way: Under 400.5 SHOULD fire (model ~100% under)
        far = dict(lines[0]); far["line"] = 400.5
        under = evaluate_line("nfl", far, game, cfg, {}, state)
        assert under is not None and under["side"] == "under", under

def test_no_live_games_skips_propline():
    """FREE-TIER GATE: when ESPN reports no live games for a sport, run_once
    must not call PropLine at all for that sport (no wasted bulk calls)."""
    import runner as _runner
    orig_games = _runner.fetch_live_games
    orig_odds = _runner.fetch_bovada_props
    odds_calls = []
    try:
        def no_games(sport, timeout=10):
            return {"sport": sport, "as_of": 1000, "payload": {"events": []}}
        def count_odds(sport, api_key=None):
            odds_calls.append(sport)
            return {"sport": sport, "as_of": 1000, "payload": {"events": []}}
        _runner.fetch_live_games = no_games
        _runner.fetch_bovada_props = count_odds
        cfg = load_config()
        with tempfile.TemporaryDirectory() as td:
            picks = run_once(cfg, ["nfl", "ncaaf", "ncaab", "ncaaw"],
                             "log_only", dry_run=False, state_dir=Path(td))
        assert picks == [], picks
        assert odds_calls == [], f"PropLine called with no live games: {odds_calls}"
    finally:
        _runner.fetch_live_games = orig_games
        _runner.fetch_bovada_props = orig_odds

def test_free_tier_pace_config():
    """FREE-TIER PACE: football 150s, basketball 180s, log-only, 4 sports."""
    from runner import _poll_interval_seconds
    cfg = load_config()
    assert cfg["poll_seconds"] == 150, cfg["poll_seconds"]
    assert cfg["basketball_poll_seconds"] == 180, cfg["basketball_poll_seconds"]
    assert _poll_interval_seconds("nfl", cfg) == 150
    assert _poll_interval_seconds("ncaaf", cfg) == 150
    assert _poll_interval_seconds("ncaab", cfg) == 180
    assert _poll_interval_seconds("ncaaw", cfg) == 180
    assert cfg.get("mode") == "log_only"
    assert set(cfg["sports"]) == {"nfl", "ncaaf_top30", "ncaab", "ncaaw"}

def test_ncaaf_top30_label_maps_to_ncaaf():
    """Config lists 'ncaaf_top30'; the engine key is 'ncaaf'. A default run
    must poll NCAAF under the right key, not silently skip it."""
    import runner as _runner
    orig_games = _runner.fetch_live_games
    orig_odds = _runner.fetch_bovada_props
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
        _runner.fetch_bovada_props = count_odds
        _runner.fetch_game_summary = empty_summary
        cfg = load_config()
        with tempfile.TemporaryDirectory() as td:
            picks = run_once(cfg, ["ncaaf_top30"], "log_only",
                             dry_run=False, state_dir=Path(td))
        assert odds_calls == ["ncaaf"], odds_calls
        assert picks == []
    finally:
        _runner.fetch_live_games = orig_games
        _runner.fetch_bovada_props = orig_odds
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

def test_evaluate_line_basketball_uses_real_possessions():
    """Real live pace must move the projection: 45 game possessions at 20 min
    elapsed is a fast game (2.25 poss/min, ratio capped 1.15) vs 35 (season
    pace, ratio 1.0). Same player, same line — fast-pace projection higher.
    Prior mean = first-seen line (5.5), so the over fires in both cases."""
    cfg = load_config()
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        fast = evaluate_line("ncaab", _basketball_line(), _basketball_game(45.0),
                             cfg, {}, state)
        assert fast is not None and fast["side"] == "over", fast
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        slow = evaluate_line("ncaab", _basketball_line(), _basketball_game(35.0),
                             cfg, {}, state)
        assert slow is not None and slow["side"] == "over", slow
    assert fast["projected_final"] > slow["projected_final"], (fast, slow)

def test_evaluate_line_basketball_no_possessions_fails_closed():
    """A basketball summary with no parseable team totals must not fall back
    to the elapsed*season-pace estimate — the line is skipped, reason logged."""
    cfg = load_config()
    game = _basketball_game(35.0)
    game["possessions"] = None
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        assert evaluate_line("ncaab", _basketball_line(), game, cfg, {}, state) is None
        skips = (state / "skips.jsonl").read_text()
        assert "no_team_possessions" in skips, skips

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
