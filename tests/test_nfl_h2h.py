"""NFL H2H (Oct 9 2026): game-level pick, close games never print a %, NFL has no proven % (walk-forward)."""
import json
from types import SimpleNamespace

from marv import board, bridge, proven, querybot


def _state(tmp_path, margin):
    (tmp_path / "marv_predict").mkdir()
    from datetime import datetime, timezone
    card = {"card_day": datetime.now(timezone.utc).date().isoformat(), "games": [
        {"game_id": "2026_05_CIN_MIA", "home": "Miami Dolphins", "away": "Cincinnati Bengals", "margin": margin,
         "rating_total": 46.3, "cat_home": 1, "cat_away": 1, "stars_home": 0, "stars_away": 4, "fade": "",
         "pick": "Cincinnati Bengals", "method": "star H2H tie-break", "close": abs(margin) < 3, "total_line": 43.0}]}
    (tmp_path / "marv_predict" / "h2h_nfl.json").write_text(json.dumps(card))
    preds = {"nfl:2026_05_CIN_MIA": {"sport": "nfl", "game_id": "2026_05_CIN_MIA", "start": "2026-10-11T17:00:00+00:00",
                                     "home": "Miami Dolphins", "away": "Cincinnati Bengals", "home_exp": 21.3,
                                     "away_exp": 25.2, "model_total": 46.7, "model_margin": -3.9, "home_win": 0.39,
                                     "qualified": [], "notes": []}}
    (tmp_path / "predictions.json").write_text(json.dumps(preds))
    return tmp_path


def test_close_game_pick_without_pct(tmp_path):
    st = _state(tmp_path, -1.2)
    o = bridge.game_h2h(st, "nfl", "Bengals")
    assert o.found and o.pick == "Cincinnati Bengals" and bridge.CLOSE_TEXT in o.line() and "%" not in o.line().replace("no %", "")
    a = bridge.overlay(st, "nfl", "h2h", "Cincinnati Bengals")
    assert "close game: H2H pick Cincinnati Bengals" in a.line() and a.prob == bridge.CLOSE_TEXT


def test_nfl_not_close_is_unproven(tmp_path):
    st = _state(tmp_path, -6.0)
    assert proven.UNPROVEN in bridge.game_h2h(st, "nfl", "Dolphins").line()
    assert proven.evidence("nfl", "ml", "H2H-SWEEP") is None


def test_queries_show_marv_h2h_and_no_raw_pct(tmp_path, monkeypatch):
    monkeypatch.delenv("PROVEN_GATE_QUERY_SPORTS", raising=False)
    st = _state(tmp_path, -1.2)
    s = SimpleNamespace(state_dir=str(st))
    g = querybot.answer(s, "/game Bengals")
    assert "🧠 Marv H2H: Cincinnati Bengals" in g and "61%" not in g
    assert "Marv H2H" in querybot.answer(s, "/h2h nfl Bengals")
    c = querybot.answer(s, "/check nfl Bengals ml Bengals +120")
    assert "Marv H2H" in c and proven.UNPROVEN in c
    txt = board.text([{"sport": "nfl", "game": "2026_05_CIN_MIA", "pick": "Bengals ML", "price": 120, "book": "Bovada",
                       "status": "lean", "p_marv": .6, "p_market": .45, "edge": .1, "stake": .01}],
                     h2h=lambda e: querybot._board_h2h(st, e))
    assert "Marv H2H" in txt and "60%" not in txt
