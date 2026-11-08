"""Adversarial cases run after the main suite. These try to buy the wrong game."""
import sqlite3

from poly_paper.storage import connect
from poly_paper.writers import insert

from helpers import exact_request, samford_entry, samford_event, team


def test_extra_school_token_and_closed_event_do_not_fall_through(db_path, policy_dir, switch_dir):
    north = samford_event(away_team=team("synthetic-north", "North Samford Bulldogs", "Bulldogs"))
    decision = insert(
        "runner", db_path,
        exact_request(snapshot={"events": [north]}, claimed_slug="synthetic-samford-unlv-winner"),
        policy_dir=policy_dir, switch_dir=switch_dir, env={},
    )
    assert not decision["accepted"]
    assert "no_unique_event" in decision["reasons"]
    closed = samford_event()
    closed["closed"] = True
    boyce = samford_event(away_team=team("synthetic-boyce", "Boyce", "Bulldogs"),
                          home_team=team("synthetic-liberty", "Liberty", "Flames"))
    boyce["id"] = "synthetic-boyce-liberty-event"
    boyce["slug"] = "synthetic-cbb-boyce-liberty-2030-11-07"
    boyce["markets"][0]["slug"] = "synthetic-boyce-liberty-winner"
    decision = insert(
        "runner", db_path,
        exact_request(
            snapshot={"events": [closed, boyce]},
            claimed_slug="synthetic-boyce-liberty-winner",
            claimed_side="long",
        ),
        policy_dir=policy_dir, switch_dir=switch_dir, env={},
    )
    assert not decision["accepted"]
    assert "event_closed" in decision["reasons"] or "no_unique_event" in decision["reasons"]
    assert "cross_game" in decision["reasons"]


def test_extra_words_in_the_pick_do_not_match(db_path, policy_dir, switch_dir):
    entry = samford_entry(pick="Samford Bulldogs ML'); drop table fills;--", key="ncaab:inject")
    decision = insert("runner", db_path, exact_request(entry=entry), policy_dir=policy_dir,
                      switch_dir=switch_dir, env={})
    assert not decision["accepted"]
    assert "outcome_not_found" in decision["reasons"]


def test_private_connection_insert_still_denied_and_quotes_are_stored(db_path, policy_dir, switch_dir):
    entry = samford_entry(pick="Samford' Bulldogs ML", key="ncaab:quote")
    decision = insert("runner", db_path, exact_request(entry=entry), policy_dir=policy_dir,
                      switch_dir=switch_dir, env={})
    assert decision["accepted"], decision
    con = connect(db_path)
    try:
        try:
            con._raw.execute("insert into fills(date, key, pick) values('2030-01-01', 'bypass', 'Boyce')")
            raise AssertionError("private insert was authorized")
        except sqlite3.DatabaseError as exc:
            assert "not authorized" in str(exc).lower()
        stored = con.execute("select pick from fills").fetchone()[0]
    finally:
        con.close()
    assert stored == "Samford' Bulldogs ML"
    assert len(__import__("sqlite3").connect(db_path).execute("select * from fills").fetchall()) == 1


def test_type_tag_and_writer_cannot_relabel_the_lane(db_path, policy_dir, switch_dir):
    lied = exact_request(type_tag="ml:cfb")
    decision = insert("runner", db_path, lied, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert "request_malformed" in decision["reasons"] or "type_tag_disagrees" in decision["reasons"]
    conflict = exact_request(writer="pm_fill")
    decision = insert("runner", db_path, conflict, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert not decision["accepted"]
    assert "request_malformed" in decision["reasons"]


def test_open_winner_market_is_used_when_a_duplicate_is_closed(db_path, policy_dir, switch_dir):
    event = samford_event()
    closed_market = dict(event["markets"][0])
    closed_market["closed"] = True
    closed_market["slug"] = "synthetic-samford-unlv-winner-old"
    event["markets"] = [closed_market, event["markets"][0]]
    decision = insert("runner", db_path, exact_request(event=event), policy_dir=policy_dir,
                      switch_dir=switch_dir, env={})
    assert decision["accepted"], decision


def test_integer_zero_closed_flag_is_not_treated_as_open(db_path, policy_dir, switch_dir):
    event = samford_event()
    event["closed"] = 0
    decision = insert("runner", db_path, exact_request(event=event), policy_dir=policy_dir,
                      switch_dir=switch_dir, env={})
    assert not decision["accepted"]
    assert "provider_identity_malformed" in decision["reasons"] or "no_unique_event" in decision["reasons"]
