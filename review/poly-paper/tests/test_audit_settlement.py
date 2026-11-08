"""Audit immutability, fees, duplicates, transactions, and settlement identity."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from poly_paper.fee import contracts_from_dollars, cost, fee
from poly_paper.settlement import settle
from poly_paper.storage import LedgerRefusal, connect
from poly_paper.writers import insert

from helpers import FIXTURES, exact_request, load_json, samford_entry


def _rows(path, sql):
    con = sqlite3.connect(path)
    try:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql)]
    finally:
        con.close()


def test_incident_fee_math_is_exact_and_does_not_insert():
    assert contracts_from_dollars(50, 0.02) == 2340
    assert fee(2340, 0.02) == 3.1875
    assert cost(2340, 0.02, 3.1875) == 49.9875
    observed = load_json(FIXTURES / "observed-case619-minimal.json")
    assert observed["contracts"] == 2340
    assert observed["price"] == 0.02
    assert observed["fee"] == 3.1875
    assert observed["cost"] == 49.9875
    assert observed["pnl"] == -49.9875


def test_accepted_fill_persists_exact_fee_and_identity(db_path, policy_dir, switch_dir):
    body = exact_request(price=0.02, contracts=2340, fee=3.1875, cost=49.9875)
    decision = insert("runner", db_path, body, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert decision["accepted"], decision
    fill = _rows(db_path, "select * from fills")[0]
    audit = _rows(db_path, "select * from fill_audit")[0]
    assert fill["contracts"] == 2340
    assert fill["fee"] == 3.1875
    assert fill["cost"] == 49.9875
    assert fill["pick"] == "Samford Bulldogs ML"
    assert fill["slug"] == "synthetic-samford-unlv-winner"
    assert fill["event_slug"] == "synthetic-cbb-samford-unlv-2030-11-07"
    assert "Samford" in fill["participants_json"]
    assert "UNLV" in fill["participants_json"]
    assert fill["token_id"] == "synthetic-token-samford"
    assert fill["input_sha256"]
    assert fill["snapshot_sha256"]
    assert fill["policy_sha256"]
    assert fill["source_version"] == "review-poly-paper-20261108"
    assert len(fill["source_tree_sha256"]) == 64
    assert audit["fill_id"] == fill["id"]
    assert audit["action"] == "accept"
    assert audit["input_sha256"] == fill["input_sha256"]
    assert "Boyce" not in fill["participants_json"]


def test_rejected_incident_writes_audit_and_no_fill(db_path, policy_dir, switch_dir):
    payload = load_json(FIXTURES / "synthetic-provider-policy-cases.json")
    body = exact_request(
        snapshot={"events": payload["league_event_response"]["events"]},
        claimed_slug="synthetic-boyce-liberty-winner",
        price=0.02,
        contracts=2340,
        fee=3.1875,
        cost=49.9875,
    )
    decision = insert("pm_fill", db_path, dict(body, lane_switch="ME_TRACK"),
                      policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert decision["action"] == "track_reject"
    assert _rows(db_path, "select * from fills") == []
    audit = _rows(db_path, "select * from fill_audit")[0]
    assert audit["fill_id"] is None
    assert "cross_game" in audit["reasons_json"]
    assert audit["token_id"] is None


def test_fee_mismatch_and_non_integer_contracts(db_path, policy_dir, switch_dir):
    bad_fee = exact_request(fee=3.1876, contracts=2340, price=0.02, cost=49.9875)
    decision = insert("runner", db_path, bad_fee, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert decision["reasons"] == ["fee_or_contract_mismatch"]
    fractional = exact_request(contracts=2340.0, price=0.02)
    decision = insert("runner", db_path, fractional, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert "fee_or_contract_mismatch" in decision["reasons"]
    flagged = exact_request(contracts=True, price=0.02)
    decision = insert("runner", db_path, flagged, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert not decision["accepted"]
    assert _rows(db_path, "select count(*) as n from fills")[0]["n"] == 0


def test_duplicate_and_concurrency(db_path, policy_dir, switch_dir):
    body = exact_request()
    first = insert("runner", db_path, body, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    second = insert("runner", db_path, body, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert first["accepted"] and not second["accepted"]
    assert second["reasons"] == ["duplicate_key"]
    assert len(_rows(db_path, "select * from fills")) == 1

    def attempt(i):
        entry = samford_entry(key="ncaab:concurrent")
        return insert("runner", db_path, exact_request(entry=entry), policy_dir=policy_dir,
                      switch_dir=switch_dir, env={})

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sum(item["accepted"] for item in results) == 1
    assert sum("duplicate_key" in item["reasons"] for item in results) == 3
    assert len(_rows(db_path, "select * from fills where key='ncaab:concurrent'")) == 1


def test_transaction_failure_rolls_back(db_path, policy_dir, switch_dir):
    body = exact_request(failpoint="after_fill_insert")
    decision = insert("runner", db_path, body, policy_dir=policy_dir, switch_dir=switch_dir, env={})
    assert decision["accepted"] is False
    assert decision["reasons"] == ["transaction_failed"]
    assert _rows(db_path, "select * from fills") == []
    audits = _rows(db_path, "select * from fill_audit")
    assert len(audits) == 1
    assert audits[0]["fill_id"] is None
    assert audits[0]["action"] == "fail_closed"


def test_direct_sql_and_identity_updates_are_blocked(db_path, policy_dir, switch_dir):
    insert("runner", db_path, exact_request(), policy_dir=policy_dir, switch_dir=switch_dir, env={})
    con = connect(db_path)
    try:
        before = dict(con.execute("select pick, slug, event_slug, pnl from fills").fetchone())
        for sql in (
            "insert into fills(date, key, pick) values('2030-01-01', 'x', 'Boyce')",
            "/* hide */ insert into fills(date, key) values('2030-01-01', 'y')",
            "insert into main.fills(date, key) values('2030-01-01', 'z')",
            "update fills set pick='Boyce Bulldogs'",
            "delete from fill_audit",
            "drop table fills",
        ):
            try:
                con.execute(sql)
            except PermissionError:
                pass
            else:
                raise AssertionError(sql)
        try:
            con.executemany("insert into fills(date, key) values(?, ?)", [("2030-01-01", "e")])
            raise AssertionError("executemany")
        except PermissionError:
            pass
        try:
            con.executescript("insert into fills(date, key) values('2030-01-01', 's');")
            raise AssertionError("script")
        except PermissionError:
            pass
        after = dict(con.execute("select pick, slug, event_slug, pnl from fills").fetchone())
    finally:
        con.close()
    assert after == before
    blocked = _rows(db_path, "select action, reasons_json from fill_audit where action='fail_closed'")
    assert blocked
    assert any("direct_insert_blocked" in row["reasons_json"] for row in blocked)


def test_settlement_cannot_relabel_or_regrade(db_path, policy_dir, switch_dir):
    decision = insert("runner", db_path, exact_request(price=0.02, contracts=2340, fee=3.1875, cost=49.9875),
                      policy_dir=policy_dir, switch_dir=switch_dir, env={})
    fill_id = decision["fill_id"]
    original = _rows(db_path, "select * from fills")[0]
    boyce = settle(db_path, fill_id, {
        "event_slug": "synthetic-cbb-boyce-liberty-2030-11-07",
        "event_id": "synthetic-boyce-liberty-event",
        "token_id": "synthetic-token-boyce",
        "participants": [{"name": "Boyce", "alias": "Bulldogs"}, {"name": "Liberty"}],
        "long_settlement_px": 1,
        "source": "synthetic",
    })
    assert not boyce["accepted"]
    assert "settlement_event_disagrees" in boyce["reasons"]
    same_slug = settle(db_path, fill_id, {
        "event_slug": original["event_slug"],
        "event_id": original["event_id"],
        "token_id": original["token_id"],
        "participants": [{"name": "Boyce"}, {"name": "Liberty"}],
        "long_settlement_px": 1,
        "source": "synthetic",
    })
    assert "settlement_participants_disagree" in same_slug["reasons"]
    unchanged = _rows(db_path, "select * from fills")[0]
    assert unchanged["status"] == "open"
    assert unchanged["pnl"] is None
    assert unchanged["pick"] == original["pick"]
    assert unchanged["participants_json"] == original["participants_json"]
    ok = settle(db_path, fill_id, {
        "event_slug": original["event_slug"],
        "event_id": original["event_id"],
        "token_id": original["token_id"],
        "participants": [{"name": "Samford Bulldogs"}, {"name": "UNLV Rebels"}],
        "long_settlement_px": 0,
        "source": "synthetic",
    })
    assert ok["accepted"], ok
    assert ok["result"] == "loss"
    assert ok["pnl"] == -49.9875
    settled = _rows(db_path, "select * from fills")[0]
    assert settled["pick"] == original["pick"]
    assert settled["slug"] == original["slug"]
    assert settled["event_slug"] == original["event_slug"]
    assert settled["token_id"] == original["token_id"]
    assert settled["participants_json"] == original["participants_json"]
    again = settle(db_path, fill_id, {
        "event_slug": original["event_slug"],
        "event_id": original["event_id"],
        "token_id": original["token_id"],
        "participants": [{"name": "Samford Bulldogs"}, {"name": "UNLV Rebels"}],
        "long_settlement_px": 1,
        "source": "synthetic",
    })
    assert again["reasons"] == ["already_settled"]
    assert _rows(db_path, "select pnl, result from fills")[0]["pnl"] == -49.9875


def test_foreign_ledger_is_not_migrated_or_rewritten(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.execute("create table fills(id integer primary key, pick text, pnl real, result text)")
    raw.execute(
        "insert into fills(id, pick, pnl, result) values(619, 'Samford Bulldogs ML', -49.9875, 'loss')"
    )
    raw.commit()
    raw.close()
    try:
        connect(path)
        raise AssertionError("foreign ledger opened")
    except LedgerRefusal:
        pass
    raw = sqlite3.connect(path)
    row = raw.execute("select id, pick, pnl, result from fills").fetchone()
    names = {item[0] for item in raw.execute("select name from sqlite_master")}
    raw.close()
    assert row == (619, "Samford Bulldogs ML", -49.9875, "loss")
    assert "fill_audit" not in names
    assert "review_meta" not in names
