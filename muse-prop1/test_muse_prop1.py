"""Muse Prop1 — integration tests with mocked PropLine / ESPN payloads.
No network, no keys, no Telegram, no bets. Run: python3 test_muse_prop1.py
"""
import json, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from bovada import extract_bovada_lines, find_line
from live_feed import parse_scoreboard, parse_summary

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

def test_scoreboard_keeps_scheduled_start_and_pregame():
    payload = {"payload": load_fixture("espn_nfl_scoreboard.json"), "as_of": 1000}
    payload["payload"]["events"][0]["date"] = "2026-11-06T18:00:00Z"
    live = parse_scoreboard(payload, "nfl")
    assert live[0]["scheduled_start"] == "2026-11-06T18:00:00Z"
    payload["payload"]["events"][0]["competitions"][0]["status"]["type"]["state"] = "pre"
    assert parse_scoreboard(payload, "nfl") == []
    pre = parse_scoreboard(payload, "nfl", include_pregame=True)
    assert len(pre) == 1 and pre[0]["state"] == "pre" and pre[0]["elapsed_min"] == 0.0

def test_summary_uses_observed_plays_only():
    game = parse_summary({"payload": load_fixture("espn_nfl_summary.json"), "as_of": 1000}, "nfl")
    assert game["team_plays"] is None
    richer = json.loads(json.dumps(load_fixture("espn_nfl_summary.json")))
    richer["boxscore"]["teams"] = [{
        "team": {"displayName": "Buffalo Bills"},
        "statistics": [{"name": "totalOffensivePlays", "displayValue": "32"}],
    }]
    parsed = parse_summary({"payload": richer, "as_of": 1000}, "nfl")
    assert parsed["team_plays_by_team"]["Buffalo Bills"] == 32.0
    assert parsed["players"]["Josh Allen"]["team"] == "Buffalo Bills"

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"PASS {name}")
    print("all Muse Prop1 integration tests passed")
