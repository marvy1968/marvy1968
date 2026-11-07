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

def test_dry_run_once():
    cfg = load_config()
    with tempfile.TemporaryDirectory() as td:
        state = Path(td)
        picks = run_once(cfg, ["nfl"], "log_only", dry_run=True, state_dir=state)
        assert len(picks) == 2, picks
        picks2 = run_once(cfg, ["nfl"], "log_only", dry_run=True, state_dir=state)
        assert picks2 == [], "dedupe must block a second pick for the same line"
        assert (state / "picks.jsonl").exists()

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"PASS {name}")
    print("all Muse Prop1 integration tests passed")
