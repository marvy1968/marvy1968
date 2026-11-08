"""Runtime parity: the exported writers keep stake, cap, dedupe, and card rules.

The identity gate is the only new reject. writers.insert is not used here.
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from importlib.machinery import SourceFileLoader
import importlib.util

from poly_paper.fee import contracts_from_dollars, cost, fee
from poly_paper.integrated import run_live_props_fill, run_marv_cbb, run_me_track, run_pm_fill, run_runner, settle_linked, writer_session
from poly_paper.storage import LedgerRefusal, connect, read_legacy_fills

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
START = "2030-11-07T21:00:00Z"


def _event(sport, league, away, home, slug, market_slug, start=START, token="synthetic-token-away"):
    away_team = {"id": f"id-{away}", "name": away}
    home_team = {"id": f"id-{home}", "name": home}
    long_side = {"long": True, "description": away, "team": away_team}
    short_side = {"long": False, "description": home, "team": home_team}
    if token:
        long_side["tokenId"] = token
        short_side["tokenId"] = token + "-other"
    return {
        "id": "event-" + slug,
        "slug": slug,
        "league": league,
        "startTime": start,
        "closed": False,
        "teams": [away_team, home_team],
        "markets": [{
            "slug": market_slug,
            "sportsMarketType": f"{sport}_full_game_winner",
            "closed": False,
            "question": f"{away} at {home}",
            "marketSides": [long_side, short_side],
        }],
    }


def _entry(sport, away, home, key, pick=None, start=START, p=0.55, **extra):
    body = {
        "key": key,
        "sport": sport,
        "market": "ml",
        "pick": pick or f"{away} ML",
        "home": home,
        "away": away,
        "start": start,
        "p": p,
        "me_type": "marv",
    }
    body.update(extra)
    return body


def _bbo(ask, bid=None):
    if bid is None:
        bid = round(ask - 0.02, 2)
    return {"state": "MARKET_STATE_OPEN", "bestAsk": {"value": str(ask)}, "bestBid": {"value": str(bid)}}


def _sig(rows):
    return [(r["key"], r["slug"], r["side"], r["contracts"], r["price"], r["fee"], r["cost"], r.get("lane")) for r in rows]


def _both(tmp_factory, entries, events, bbo=None, config=None, env=None):
    out = {}
    for gate, name in ((False, "writer"), (True, "gated")):
        tmp = tmp_factory.mktemp(name)
        with writer_session(tmp, config=config, env=env, identity_gate=gate) as session:
            run_runner(session, entries, events, bbo)
            out[name] = session.fills()
    return out


def test_valid_identity_matches_the_ungated_writer(tmp_path_factory):
    away, home = "Samford Bulldogs", "UNLV Rebels"
    event = _event("ncaab", "cbb", away, home, "aec-cbb-samford-unlv-2030-11-07", "aec-samford-unlv-winner")
    entry = _entry("ncaab", away, home, "ncaab:synthetic:ml:away")
    out = _both(tmp_path_factory, [entry], [event], _bbo(0.40))
    assert _sig(out["writer"]) == _sig(out["gated"])
    assert len(out["gated"]) == 1
    row = out["gated"][0]
    assert row["contracts"] == contracts_from_dollars(50, 0.40)
    assert row["fee"] == fee(row["contracts"], 0.40)
    assert row["cost"] == cost(row["contracts"], 0.40, row["fee"])
    assert row["slug"] == "aec-samford-unlv-winner"


def test_daily_cap_slug_dedupe_and_max_stake_are_the_writers(tmp_path):
    def game(tag, away, home):
        return (
            _entry("ncaab", away, home, f"ncaab:{tag}:ml", p=0.55),
            _event("ncaab", "cbb", away, home, f"aec-cbb-{tag}-2030-11-07", f"winner-{tag}"),
        )
    first = game("sam", "Samford Bulldogs", "UNLV Rebels")
    second = game("duke", "Duke Blue Devils", "North Carolina Tar Heels")
    entries = [first[0], second[0]]
    events = [first[1], second[1]]
    with writer_session(tmp_path / "cap", config={"max_daily_cost_usd": 50}, identity_gate=True) as gated:
        run_runner(gated, entries, events, _bbo(0.40))
        gated_rows = gated.fills()
    with writer_session(tmp_path / "cap-open", config={"max_daily_cost_usd": 50}, identity_gate=False) as open_writer:
        run_runner(open_writer, entries, events, _bbo(0.40))
        open_rows = open_writer.fills()
    assert _sig(gated_rows) == _sig(open_rows)
    assert len(gated_rows) == 1
    assert gated_rows[0]["contracts"] == contracts_from_dollars(50, 0.40)

    with writer_session(tmp_path / "dedupe", identity_gate=True) as session:
        entry, event = first
        run_runner(session, [entry], [event], _bbo(0.40))
        run_runner(session, [entry], [event], _bbo(0.40))
        assert len(session.fills()) == 1


def test_trade_all_off_keeps_nfl_dog_and_cfb_paths(tmp_path):
    nfl = _entry("nfl", "Buffalo Bills", "Kansas City Chiefs", "nfl:g:ml", p=0.55)
    nfl_event = _event("nfl", "nfl", "Buffalo Bills", "Kansas City Chiefs", "aec-nfl-buf-kc-2030-11-07", "winner-nfl")
    dog = _entry("cfb", "Samford Bulldogs", "UNLV Rebels", "cfb:dog:ml", p=0.55)
    dog_event = _event("cfb", "cfb", "Samford Bulldogs", "UNLV Rebels", "aec-cfb-sam-unlv-2030-11-07", "winner-cfb-dog")
    fav = _entry("cfb", "Duke Blue Devils", "North Carolina Tar Heels", "cfb:fav:ml", p=0.80)
    fav_event = _event("cfb", "cfb", "Duke Blue Devils", "North Carolina Tar Heels", "aec-cfb-duke-unc-2030-11-07", "winner-cfb-fav")
    card = _entry("mlb", "New York Yankees", "Boston Red Sox", "mlb:dog:ml", p=0.55)
    card_event = _event("mlb", "mlb", "New York Yankees", "Boston Red Sox", "aec-mlb-nyy-bos-2030-11-07", "winner-mlb")

    def run(folder, ask_for):
        with writer_session(tmp_path / folder, env={"PM_TRADE_ALL": "off"}, identity_gate=True) as session:
            # One market per call so the ask matches that case.
            return session

    with writer_session(tmp_path / "nfl", env={"PM_TRADE_ALL": "off"}, identity_gate=True) as session:
        run_runner(session, [nfl], [nfl_event], _bbo(0.40))
        rows = session.fills()
    assert len(rows) == 1 and rows[0]["lane"] == "nfl_dog"
    with writer_session(tmp_path / "nfl-open", env={"PM_TRADE_ALL": "off"}, identity_gate=False) as session:
        run_runner(session, [nfl], [nfl_event], _bbo(0.40))
        assert _sig(session.fills()) == _sig(rows)

    with writer_session(tmp_path / "dog", env={"PM_TRADE_ALL": "off"}, identity_gate=True) as session:
        run_runner(session, [dog], [dog_event], _bbo(0.40))
        assert session.fills()[0]["lane"] == "cfb_dog"
    with writer_session(tmp_path / "fav-off", env={"PM_TRADE_ALL": "off"}, identity_gate=True) as session:
        run_runner(session, [fav], [fav_event], _bbo(0.52, 0.50))
        assert session.fills() == []
    with writer_session(tmp_path / "fav-on", identity_gate=True) as session:
        run_runner(session, [fav], [fav_event], _bbo(0.52, 0.50))
        assert len(session.fills()) == 1
        assert session.fills()[0]["lane"] != "cfb_dog"
    with writer_session(tmp_path / "card", env={"PM_TRADE_ALL": "off"}, identity_gate=True) as session:
        run_runner(session, [card], [card_event], _bbo(0.40))
        assert session.fills() == []
    with writer_session(tmp_path / "card-open", env={"PM_TRADE_ALL": "off"}, identity_gate=False) as session:
        run_runner(session, [card], [card_event], _bbo(0.40))
        assert session.fills() == []


def test_doubleheader_and_bad_mapper_are_identity_rejects(tmp_path):
    away, home = "Samford Bulldogs", "UNLV Rebels"
    early = _event("ncaab", "cbb", away, home, "aec-cbb-sam-unlv-2030-11-07-early", "winner-early",
                   start="2030-11-07T18:00:00Z")
    late = _event("ncaab", "cbb", away, home, "aec-cbb-sam-unlv-2030-11-07-late", "winner-late",
                  start="2030-11-07T23:00:00Z")
    entry = _entry("ncaab", away, home, "ncaab:dh:ml", start="2030-11-07T23:00:00Z")
    with writer_session(tmp_path / "open", identity_gate=False) as session:
        run_runner(session, [entry], [early, late], _bbo(0.40))
        assert session.fills()[0]["slug"] == "winner-early"
    with writer_session(tmp_path / "gated", identity_gate=True) as session:
        run_runner(session, [entry], [early, late], _bbo(0.40))
        assert session.fills() == []
        reasons = session.audits()[-1]["reasons_json"]
        assert "slug_disagrees" in reasons or "start_time_mismatch" in reasons

    boyce = _event("ncaab", "cbb", "Boyce", "Liberty", "synthetic-cbb-boyce-liberty-2030-11-07",
                   "synthetic-boyce-liberty-winner")
    boyce["teams"][0]["alias"] = "Bulldogs"
    boyce["markets"][0]["marketSides"][0]["description"] = "Bulldogs"
    boyce["markets"][0]["marketSides"][0]["team"]["alias"] = "Bulldogs"
    backup_path = ROOT / "provenance/baseline/polymarket-paper/mapper.py.baseline-20261107"
    loader = SourceFileLoader("backup_mapper_for_writer", str(backup_path))
    spec = importlib.util.spec_from_loader("backup_mapper_for_writer", loader)
    backup = importlib.util.module_from_spec(spec)
    loader.exec_module(backup)
    with writer_session(tmp_path / "bug-open", identity_gate=False) as session:
        session.map_holder["fn"] = backup.map_entry
        run_runner(session, [entry], [boyce], _bbo(0.40))
        assert session.fills()[0]["slug"] == "synthetic-boyce-liberty-winner"
    with writer_session(tmp_path / "bug-gated", identity_gate=True) as session:
        session.map_holder["fn"] = backup.map_entry
        run_runner(session, [entry], [boyce], _bbo(0.40))
        assert session.fills() == []
        assert "no_unique_event" in session.audits()[-1]["reasons_json"]


def test_callers_and_settlement_and_legacy_read(tmp_path):
    away, home = "Samford Bulldogs", "UNLV Rebels"
    event = _event("ncaab", "cbb", away, home, "aec-cbb-samford-unlv-2030-11-07", "aec-samford-unlv-winner")
    entry = _entry("ncaab", away, home, "ncaab:me:1", start=START, p=0.55, me_alert_id=1)
    with writer_session(tmp_path / "me", identity_gate=True) as session:
        run_me_track(session, [entry], [event], _bbo(0.40))
        rows = session.fills()
        assert len(rows) == 1
        assert rows[0]["fee"] == fee(rows[0]["contracts"], rows[0]["price"])
        audit = session.audits()[-1]
        assert audit["fill_id"] == rows[0]["id"]
        assert audit["token_verified"] == 1
        bad = settle_linked(session, rows[0]["id"], {
            "market_slug": "synthetic-boyce-liberty-winner",
            "participants": [{"name": "Boyce"}, {"name": "Liberty"}],
            "token_id": "synthetic-token-away",
            "long_settlement_px": 1,
        })
        assert bad["accepted"] is False
        assert session.fills()[0]["status"] == "open"
        good = settle_linked(session, rows[0]["id"], {
            "market_slug": rows[0]["slug"],
            "participants": [{"name": away}, {"name": home}],
            "token_id": "synthetic-token-away",
            "long_settlement_px": 0,
        })
        assert good["accepted"] and good["result"] == "loss"
        assert good["pnl"] == -rows[0]["cost"]
        assert session.fills()[0]["pick"] == f"{away} ML"
        again = settle_linked(session, rows[0]["id"], {
            "market_slug": rows[0]["slug"],
            "participants": [{"name": away}, {"name": home}],
            "token_id": "synthetic-token-away",
            "long_settlement_px": 1,
        })
        assert again["reasons"] == ["already_settled"]

    bare = _event("ncaab", "cbb", away, home, "aec-cbb-samford-unlv-2030-11-07", "aec-samford-unlv-winner", token=None)
    with writer_session(tmp_path / "notoken", identity_gate=True) as session:
        run_runner(session, [_entry("ncaab", away, home, "ncaab:notoken")], [bare], _bbo(0.40))
        audit = session.audits()[-1]
        assert audit["token_verified"] == 0
        assert json.loads(audit["outcome_json"])["token_verified"] is False
        refused = settle_linked(session, session.fills()[0]["id"], {
            "market_slug": session.fills()[0]["slug"],
            "participants": [{"name": away}, {"name": home}],
            "token_id": "synthetic-invented",
            "long_settlement_px": 0,
        })
        assert "settlement_token_not_verified_at_fill" in refused["reasons"]

    board_entry = _entry("ncaab", away, home, "board-1")
    with writer_session(tmp_path / "cbb", identity_gate=True) as session:
        run_marv_cbb(session, [board_entry], [event], _bbo(0.40))
        assert len(session.fills()) == 1

    with writer_session(tmp_path / "props", identity_gate=True) as session:
        stats = run_live_props_fill(
            session,
            {"slug": "synthetic-prop", "side": "long", "pick": "Synthetic Player 20+ PTS", "price": 0.40,
             "key": "nfl:live:1", "sport": "nfl", "market": "prop"},
            {"id": "g1"},
            {"id": "p1", "name": "Synthetic Player"},
            0.70,
        )
        assert session.fills() == []
        assert "identity_context_missing" in session.audits()[-1]["reasons_json"]
        assert stats

    legacy = tmp_path / "legacy.db"
    raw = sqlite3.connect(legacy)
    raw.execute("create table fills(id integer primary key, pick text, pnl real, result text, slug text)")
    raw.execute("insert into fills(id, pick, pnl, result, slug) values(619, 'Samford Bulldogs ML', -49.9875, 'loss', 'aec-cbb-bc-librty-2026-11-07')")
    raw.commit()
    raw.close()
    before = legacy.read_bytes()
    rows = read_legacy_fills(legacy)
    assert rows[0]["id"] == 619 and rows[0]["pnl"] == -49.9875
    assert legacy.read_bytes() == before
    try:
        connect(legacy)
        raise AssertionError("legacy ledger opened for the review schema")
    except LedgerRefusal:
        pass
    assert legacy.read_bytes() == before


def test_nba_live_reaches_pm_fill_and_does_not_invent_identity(tmp_path):
    today = datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")
    slug = f"aec-nba-bos-nyk-{today}"
    event = {
        "id": "synthetic-nba-event",
        "slug": slug,
        "league": "nba",
        "startTime": "2030-11-07T21:00:00Z",
        "closed": False,
        "teams": [{"id": "bos", "name": "Boston Celtics"}, {"id": "nyk", "name": "New York Knicks"}],
        "markets": [{
            "slug": "synthetic-nba-player-points",
            "sportsMarketType": "basketball_player_points",
            "closed": False,
            "question": "Will Synthetic Player record at least 8",
            "bestBidQuote": 0.43,
            "bestAskQuote": 0.45,
        }],
    }
    now = datetime.now(timezone.utc).isoformat()
    bbo = {
        "state": "MARKET_STATE_OPEN",
        "bestBid": {"value": "0.43"},
        "bestAsk": {"value": "0.45"},
        "askShares": 500,
        "bidShares": 500,
        "lastPriceSample": {"ts": now},
    }
    with writer_session(tmp_path / "nba", identity_gate=True) as session:
        from poly_paper.integrated import install_client, _load
        install_client(session, [event], bbo)
        nba = _load(session.workspace / "pm_nba_live.py", "pm_nba_live")
        nba.boxes = lambda: [{
            "id": "synthetic-game",
            "name": "BOS@NYK",
            "period": 1,
            "clock": "10:00",
            "margin": 2,
            "teams": {"bos", "nyk"},
            "half": "1st",
            "players": {"synthetic player": {
                "id": "p1", "name": "Synthetic Player", "team": "BOS", "starter": True, "active": True,
                "cur": {"MIN": 2, "PTS": 2, "REB": 1, "AST": 0, "STL": 0, "BLK": 0, "3PM": 0, "PF": 0, "PRA": 3},
            }},
        }]
        nba.rates = lambda aid, cache: {"d": today, "min": 34, "PTS": 0.9, "REB": 0.3, "AST": 0.2, "STL": 0.05, "BLK": 0.05, "3PM": 0.1, "PRA": 1.4}
        nba.main()
        assert session.fills() == []
        assert session.audits(), "pm_nba_live did not reach the fill insert"
        assert "identity_context_missing" in session.audits()[-1]["reasons_json"]


def test_missing_policy_denies_every_supported_writer(tmp_path):
    away, home = "Samford Bulldogs", "UNLV Rebels"
    event = _event("ncaab", "cbb", away, home, "aec-cbb-samford-unlv-2030-11-07", "aec-samford-unlv-winner")
    entry = _entry("ncaab", away, home, "ncaab:policy:ml")
    for name in ("runner", "me", "cbb"):
        with writer_session(tmp_path / name, identity_gate=True) as session:
            (session.workspace / "type_gate.json").write_text("{")
            if name == "runner":
                run_runner(session, [entry], [event], _bbo(0.40))
            elif name == "me":
                run_me_track(session, [dict(entry, me_alert_id=7, key="ncaab:me-policy")], [event], _bbo(0.40))
            else:
                run_marv_cbb(session, [dict(entry, key="board-policy")], [event], _bbo(0.40))
            assert session.fills() == []
            assert "policy_malformed" in session.audits()[-1]["reasons_json"]
    with writer_session(tmp_path / "pm", identity_gate=True) as session:
        (session.workspace / "effective_policy.json").write_text("")
        ok, why = run_pm_fill(
            session, "ME_TRACK",
            {"slug": "aec-samford-unlv-winner", "side": "long", "pick": f"{away} ML", "price": 0.40,
             "fair": 0.55, "key": "ncaab:pm-policy", "sport": "ncaab", "market": "ml"},
            "ml:ncaab", entry, [event], _bbo(0.40),
        )
        assert ok is False and why.startswith("policy:")
        assert session.fills() == []


def test_identity_reject_is_not_called_a_duplicate_and_does_not_reserve_stake(tmp_path):
    away, home = "Samford Bulldogs", "UNLV Rebels"
    good = _event("ncaab", "cbb", away, home, "aec-cbb-samford-unlv-2030-11-07", "aec-samford-unlv-winner")
    boyce = _event("ncaab", "cbb", "Boyce", "Liberty", "synthetic-cbb-boyce-liberty-2030-11-07", "synthetic-boyce-liberty-winner")
    boyce["teams"][0]["alias"] = "Bulldogs"
    boyce["markets"][0]["marketSides"][0]["description"] = "Bulldogs"
    boyce["markets"][0]["marketSides"][0]["team"]["alias"] = "Bulldogs"
    loader = SourceFileLoader("backup_mapper_reason", str(ROOT / "provenance/baseline/polymarket-paper/mapper.py.baseline-20261107"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    backup = importlib.util.module_from_spec(spec)
    loader.exec_module(backup)
    valid = _entry("ncaab", away, home, "ncaab:valid:ml")
    bad = _entry("ncaab", away, home, "ncaab:bad:ml")
    with writer_session(tmp_path / "reason", identity_gate=True) as session:
        session.map_holder["fn"] = backup.map_entry
        ok, why = run_pm_fill(
            session, "ME_TRACK",
            {"slug": "synthetic-boyce-liberty-winner", "side": "long", "pick": f"{away} ML", "price": 0.40,
             "fair": 0.55, "key": "me:99", "sport": "ncaab", "market": "ml"},
            "ml:ncaab", dict(bad, me_alert_id=99), [boyce], _bbo(0.40),
        )
        assert why != "dup key" and why.startswith("identity:")
        assert ok is False and session.fills() == []
    with writer_session(tmp_path / "next", identity_gate=True) as session:
        run_runner(session, [valid], [good], _bbo(0.40))
        rows = session.fills()
        assert len(rows) == 1 and rows[0]["slug"] == "aec-samford-unlv-winner"
        assert rows[0]["cost"] == cost(rows[0]["contracts"], rows[0]["price"], rows[0]["fee"])
        spent = rows[0]["cost"]
        assert spent < 50
    with writer_session(tmp_path / "dedupe", identity_gate=True) as session:
        run_pm_fill(
            session, "ME_TRACK",
            {"slug": "aec-samford-unlv-winner", "side": "long", "pick": f"{away} ML", "price": 0.40,
             "fair": 0.55, "key": "ncaab:once", "sport": "ncaab", "market": "ml"},
            "ml:ncaab", valid, [good], _bbo(0.40),
        )
        ok, why = run_pm_fill(
            session, "ME_TRACK",
            {"slug": "aec-samford-unlv-winner", "side": "long", "pick": f"{away} ML", "price": 0.40,
             "fair": 0.55, "key": "ncaab:twice", "sport": "ncaab", "market": "ml"},
            "ml:ncaab", dict(valid, key="ncaab:twice"), [good], _bbo(0.40),
        )
        assert why == "already hold this market"
        assert len(session.fills()) == 1


def test_mock_calibration_changes_stake_without_claiming_real_calib(tmp_path):
    away, home = "Samford Bulldogs", "UNLV Rebels"
    event = _event("ncaab", "cbb", away, home, "aec-cbb-samford-unlv-2030-11-07", "aec-samford-unlv-winner")
    entry = _entry("ncaab", away, home, "ncaab:mock-calib")

    def fake_size(venue, lane, fair, price, me_type=None, cap_usd=None, dry=False, extra=None):
        assert cap_usd == 50
        return 12.5, {"tier": "mock", "mock": True}

    with writer_session(tmp_path / "mock", identity_gate=True, calib_mock=fake_size) as session:
        run_runner(session, [entry], [event], _bbo(0.40))
        row = session.fills()[0]
    assert row["contracts"] == contracts_from_dollars(12.5, 0.40)
    assert row["contracts"] != contracts_from_dollars(50, 0.40)


def test_reject_audit_is_append_only_and_a_failed_insert_rolls_back(tmp_path):
    away, home = "Samford Bulldogs", "UNLV Rebels"
    event = _event("ncaab", "cbb", away, home, "aec-cbb-samford-unlv-2030-11-07", "aec-samford-unlv-winner")
    entry = _entry("ncaab", away, home, "ncaab:rollback")
    with writer_session(tmp_path / "boom", identity_gate=True) as session:
        session.gate.failpoint = "after_fill"
        try:
            run_runner(session, [entry], [event], _bbo(0.40))
        except RuntimeError as exc:
            assert "injected" in str(exc)
        assert session.fills() == []
        assert session.audits() == []
    with writer_session(tmp_path / "deny", identity_gate=True) as session:
        (session.workspace / "type_gate.json").write_text("{")
        run_runner(session, [entry], [event], _bbo(0.40))
        run_runner(session, [dict(entry, key="ncaab:rollback-2")], [event], _bbo(0.40))
        audits = session.audits()
        assert len(audits) >= 2
        assert all(row["action"] == "fail_closed" for row in audits)
        assert all(row["effective_policy_json"] in (None, "") or "policy_malformed" in row["reasons_json"] for row in audits)
        assert session.fills() == []
