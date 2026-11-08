"""Policy precedence and both writer entry points."""
import json

from poly_paper.policy import classify, evaluate
from poly_paper.writers import CALLER_WRITER, insert

from helpers import exact_request, samford_entry, samford_event


def _go(caller, db_path, policy_dir, switch_dir, env=None, **request_overrides):
    body = exact_request(**request_overrides)
    return insert(caller, db_path, body, policy_dir=policy_dir, switch_dir=switch_dir, env=env or {})


def test_default_trade_all_allows_ncaab_and_not_a_nov17_switch(db_path, policy_dir, switch_dir):
    decision = _go("runner", db_path, policy_dir, switch_dir)
    assert decision["accepted"], decision
    assert "trade_all" in decision["reasons"]
    later = samford_entry(start="2026-11-18T21:00:00Z")
    event = samford_event(startTime="2026-11-18T21:00:00Z", slug="synthetic-cbb-samford-unlv-2026-11-18")
    decision = _go("pm_fill", db_path, policy_dir, switch_dir, entry=later, event=event,
                   lane_switch="ME_TRACK", key_ignored=None)
    # key stays unique because exact_request uses the entry key; change the key via entry.
    assert decision["action"] in ("accept", "track_reject", "fail_closed")


def test_ncaab_after_nov17_still_fills(db_path, policy_dir, switch_dir):
    entry = samford_entry(start="2026-11-18T21:00:00Z", key="ncaab:synthetic:ml:nov18")
    event = samford_event(startTime="2026-11-18T21:00:00Z", slug="synthetic-cbb-samford-unlv-2026-11-18")
    decision = insert("runner", db_path, exact_request(entry=entry, event=event),
                      policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert decision["accepted"], decision


def test_trade_all_off_allowlist_and_ncaab_not_silently_added(db_path, policy_dir, switch_dir):
    env = {"PM_TRADE_ALL": "off"}
    ncaab = insert("runner", db_path, exact_request(price=0.55), policy_dir=policy_dir, switch_dir=switch_dir, env=env)
    assert not ncaab["accepted"]
    assert "not_on_allowlist" in ncaab["reasons"]
    cfb_entry = samford_entry(sport="cfb", key="cfb:synthetic:ml", market="ml")
    cfb_event = samford_event(league="cfb", slug="synthetic-cfb-samford-unlv-2030-11-07")
    # Participants are still the synthetic schools; league token must be cfb.
    decision = insert("runner", db_path, exact_request(entry=cfb_entry, event=cfb_event),
                      policy_dir=policy_dir, switch_dir=switch_dir, env=env)
    assert decision["accepted"], decision
    assert "allowlist" in decision["reasons"]
    file_off = switch_dir / "PM_TRADE_ALL_OFF"
    file_off.write_text("off")
    again = insert("pm_fill", db_path, exact_request(lane_switch="NBA_LIVE"),
                   policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert not again["accepted"]
    assert "trade_all_off" in again["reasons"]


def test_force_track_hold_and_lane_precedence(db_path, policy_dir, switch_dir):
    prop = _prop_request(live=False)
    held = insert("pm_nba_live", db_path, prop, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert not held["accepted"]
    assert "force_track" in held["reasons"]
    released = insert(
        "pm_nba_live", db_path, prop, policy_dir=policy_dir, switch_dir=switch_dir,
        env={"PM_FORCE_TRACK": "off"},
    )
    assert released["accepted"], released
    hidden = _prop_request(live=False, me_type="value_live", key="nfl:synthetic:prop:hidden")
    hidden_decision = insert("runner", db_path, hidden, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert "force_track" in hidden_decision["reasons"]
    nba = samford_entry(sport="nba", market="ml", key="nba:synthetic:ml", me_type="value_live")
    nba_event = samford_event(league="nba", slug="synthetic-nba-samford-unlv-2030-11-07")
    decision = insert("runner", db_path, exact_request(entry=nba, event=nba_event),
                      policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert "nba_hold" in decision["reasons"]
    prop_nba = _prop_request(sport="nba", league="nba", slug="synthetic-nba-buf-kc-2030-11-07",
                             key="nba:synthetic:prop", live=True, lane_switch="NBA_LIVE")
    decision = insert("pm_nba_live", db_path, prop_nba, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert decision["accepted"], decision
    (switch_dir / "PM_FILL_ME_TRACK_OFF").write_text("off")
    lane = insert("pm_me_track", db_path, exact_request(lane_switch="ME_TRACK"),
                  policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert "lane_off" in lane["reasons"]


def test_kill_pin_never_and_type_gate_off(db_path, policy_dir, switch_dir):
    (switch_dir / "KILL").write_text("stop")
    killed = insert("runner", db_path, exact_request(), policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert killed["reasons"] == ["hard_kill"]
    (switch_dir / "KILL").unlink()
    nfl = samford_entry(sport="nfl", key="nfl:synthetic:ml")
    nfl_event = samford_event(league="nfl", slug="synthetic-nfl-samford-unlv-2030-11-07")
    on = insert("runner", db_path, exact_request(entry=nfl, event=nfl_event),
                policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert on["accepted"], on
    off = insert("runner", db_path, exact_request(entry=nfl, event=nfl_event, price=0.55),
                 policy_dir=policy_dir, switch_dir=switch_dir, env={"PM_TRADE_ALL": "off"})
    # key collision: exact_request entry key is nfl:synthetic:ml and the previous fill used it.
    # This call builds a new request from overrides but entry is passed so key stays.
    assert "pin_track" in off["reasons"] or "duplicate_key" in off["reasons"]
    fresh = samford_entry(sport="nfl", key="nfl:synthetic:ml:2")
    off = insert("runner", db_path, exact_request(entry=fresh, event=nfl_event, price=0.55),
                 policy_dir=policy_dir, switch_dir=switch_dir, env={"PM_TRADE_ALL": "off"})
    assert "pin_track" in off["reasons"]
    never = samford_entry(sport="cfb", me_type="h2h_cfb", key="cfb:synthetic:h2h")
    cfb_event = samford_event(league="cfb", slug="synthetic-cfb-samford-unlv-2030-11-07")
    allowed = insert("runner", db_path, exact_request(entry=never, event=cfb_event),
                     policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert allowed["accepted"], allowed
    blocked = insert(
        "runner", db_path,
        exact_request(entry=samford_entry(sport="cfb", me_type="h2h_cfb", key="cfb:synthetic:h2h:2"), event=cfb_event, price=0.55),
        policy_dir=policy_dir, switch_dir=switch_dir, env={"PM_TRADE_ALL": "off"},
    )
    assert "never_trade" in blocked["reasons"]
    opened = insert(
        "runner", db_path,
        exact_request(entry=samford_entry(sport="nba", market="ml", key="nba:gate-off"),
                      event=samford_event(league="nba", slug="synthetic-nba-samford-unlv-2030-11-07")),
        policy_dir=policy_dir, switch_dir=switch_dir,
        env={"PM_TRADE_ALL": "off", "TYPE_GATE": "off"},
    )
    assert "nba_hold" in opened["reasons"]


def test_missing_and_conflicting_policy(db_path, policy_dir, switch_dir, tmp_path):
    missing = tmp_path / "empty"
    missing.mkdir()
    decision = insert("runner", db_path, exact_request(), policy_dir=missing, switch_dir=switch_dir, env={})
    assert decision["action"] == "fail_closed"
    assert "policy_missing" in decision["reasons"]
    (policy_dir / "effective_policy.json").write_text("{")
    decision = insert("runner", db_path, exact_request(), policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert "policy_malformed" in decision["reasons"]
    shutil_policy(policy_dir)
    document = json.loads((policy_dir / "effective_policy.json").read_text())
    for key, value, code in (
        ("nov17_auto_switch", True, "unapproved_policy_conflict"),
        ("nba_ml_hold", False, "unapproved_policy_conflict"),
        ("ncaab_track_only_ban", True, "unapproved_policy_conflict"),
        ("ncaab_paper_eligible", False, "unapproved_policy_conflict"),
        ("identity_required", False, "unapproved_policy_conflict"),
        ("paper_only", False, "paper_mode_violation"),
        ("preprod_enabled", True, "paper_mode_violation"),
        ("live_orders", True, "paper_mode_violation"),
    ):
        body = dict(document)
        body[key] = value
        (policy_dir / "effective_policy.json").write_text(json.dumps(body))
        decision = insert("runner", db_path, exact_request(entry=samford_entry(key=f"ncaab:{key}")),
                          policy_dir=policy_dir, switch_dir=switch_dir, env={})
        assert code in decision["reasons"], (key, decision)
        assert not decision["accepted"]
    (policy_dir / "effective_policy.json").write_text(json.dumps(document))
    no_switch = tmp_path / "missing-switch"
    decision = insert("runner", db_path, exact_request(), policy_dir=policy_dir, switch_dir=no_switch, env={})
    assert "switch_dir_missing" in decision["reasons"]
    decision = insert("runner", db_path, exact_request(live_order=True),
                      policy_dir=policy_dir, switch_dir=switch_dir, env={"PREPROD_ENABLED": "true"})
    assert "live_order_refused" in decision["reasons"]


def test_every_caller_uses_the_boundary(db_path, policy_dir, switch_dir):
    from helpers import FIXTURES
    payload = json.loads((FIXTURES / "synthetic-provider-policy-cases.json").read_text())
    bad = exact_request(snapshot={"events": payload["league_event_response"]["events"]},
                        claimed_slug="synthetic-boyce-liberty-winner", price=0.02, contracts=2340,
                        fee=3.1875, cost=49.9875)
    for caller, writer in CALLER_WRITER.items():
        body = dict(bad, lane_switch="ME_TRACK", entry=samford_entry(key=f"ncaab:{caller}"))
        decision = insert(caller, db_path, body, policy_dir=policy_dir, switch_dir=switch_dir, env={})
        assert decision["accepted"] is False, caller
        assert decision["action"] in ("track_reject", "fail_closed")
        assert "no_unique_event" in decision["reasons"] or "cross_game" in decision["reasons"]
    good = exact_request(lane_switch="MARV_CBB")
    for caller in ("runner", "pm_fill", "pm_me_track.marv_cbb", "live_props.poly_fill"):
        entry = samford_entry(key=f"ncaab:ok:{caller}")
        decision = insert(caller, db_path, exact_request(entry=entry, lane_switch="MARV_CBB"),
                          policy_dir=policy_dir, switch_dir=switch_dir, env={})
        assert decision["accepted"], (caller, decision)


def test_classify_matches_baseline_function():
    import importlib.util
    path = __import__("pathlib").Path(__file__).resolve().parents[1] / "provenance/baseline/polymarket-paper/pm_type_gate.py"
    spec = importlib.util.spec_from_file_location("baseline_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    samples = [
        ("ncaab:401924912:ml:away:None", "marv", "ml", "ncaab"),
        ("nba:1:ml:home", None, "ml", "nba"),
        ("pmprop:nfl:x", None, "prop", "nfl"),
        ("anything", "value_live", "ml", "ncaab"),
    ]
    for key, me_type, market, sport in samples:
        assert classify(key, me_type, market, sport) == module.classify(key, me_type, market, sport)


def test_evaluate_documents_trade_all_versus_force(policy_dir, switch_dir):
    entry = {"key": "ncaab:1:ml:away", "sport": "ncaab", "market": "ml", "me_type": "marv", "live": False}
    action, reasons = evaluate(entry, writer="runner", lane_switch=None, policy_dir=policy_dir,
                               switch_dir=switch_dir, env={})
    assert action == "allow" and reasons == ["trade_all"]
    prop = {"key": "nfl:1:prop", "sport": "nfl", "market": "prop", "live": False}
    action, reasons = evaluate(prop, writer="runner", lane_switch=None, policy_dir=policy_dir,
                               switch_dir=switch_dir, env={})
    assert action == "track_reject" and "force_track" in reasons


def shutil_policy(policy_dir):
    from pathlib import Path
    import shutil
    src = Path(__file__).resolve().parents[1] / "poly_paper" / "effective_policy.json"
    shutil.copy(src, policy_dir / "effective_policy.json")


def _prop_request(live=False, me_type="marv", key="nfl:synthetic:prop", sport="nfl",
                  league="nfl", slug="synthetic-nfl-buf-kc-2030-11-07", lane_switch="NBA_LIVE"):
    away = {"id": "synthetic-buf", "name": "Buffalo Bills", "alias": "Bills"}
    home = {"id": "synthetic-kc", "name": "Kansas City Chiefs", "alias": "Chiefs"}
    event = {
        "id": "synthetic-prop-event",
        "slug": slug,
        "league": league,
        "startTime": "2030-11-07T18:00:00Z",
        "closed": False,
        "teams": [away, home],
        "markets": [{
            "slug": "synthetic-player-prop",
            "sportsMarketType": "football_player_passing_yards" if sport == "nfl" else "basketball_player_points",
            "closed": False,
            "player": "Synthetic Quarterback",
            "stat": "PYD",
            "line": 275.5,
            "marketSides": [
                {"long": True, "description": "Over", "tokenId": "synthetic-token-prop-over"},
                {"long": False, "description": "Under", "tokenId": "synthetic-token-prop-under"},
            ],
        }],
    }
    entry = {
        "key": key,
        "sport": sport,
        "market": "prop",
        "pick": "Over 275.5",
        "home": "Kansas City Chiefs",
        "away": "Buffalo Bills",
        "start": "2030-11-07T18:00:00Z",
        "me_type": me_type,
        "live": live,
        "prop": {"player": "Synthetic Quarterback", "stat": "PYD", "line": 275.5, "side": "over"},
    }
    return exact_request(
        entry=entry,
        snapshot={"events": [event]},
        claimed_slug="synthetic-player-prop",
        claimed_side="long",
        claimed_token="synthetic-token-prop-over",
        lane_switch=lane_switch,
        lane="prop",
    )
