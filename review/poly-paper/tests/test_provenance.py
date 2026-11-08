"""Provenance locks: baseline bytes, screened config, and no network in the package."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_baseline_hashes_match_the_screened_export():
    lines = (ROOT / "provenance" / "SHA256SUMS").read_text().splitlines()
    assert lines
    for line in lines:
        digest, rel = line.split("  ", 1)
        found = hashlib.sha256((ROOT / "provenance" / "baseline" / rel).read_bytes()).hexdigest()
        assert found == digest, rel


def test_screened_config_snapshot_is_paper_only():
    import json
    cfg = json.loads((ROOT / "schemas" / "config.nonsecret-snapshot.json").read_text())
    gate = json.loads((ROOT / "schemas" / "type_gate.nonsecret-snapshot.json").read_text())
    assert cfg["nonsecret_snapshot"]["PAPER_ONLY"] is True
    assert cfg["nonsecret_snapshot"]["PREPROD_ENABLED"] is False
    assert gate["nonsecret_snapshot"]["trade"] == ["ml:cfb"]
    assert "prop:nfl:pre" in gate["nonsecret_snapshot"]["force_track"]
    reviewed = json.loads((ROOT / "poly_paper" / "effective_policy.json").read_text())
    assert reviewed["paper_only"] is True
    assert reviewed["preprod_enabled"] is False
    assert reviewed["live_orders"] is False
    assert reviewed["trade_all_default"] == "on"
    assert reviewed["ncaab_paper_eligible"] is True
    assert reviewed["ncaab_track_only_ban"] is False
    assert reviewed["nov17_auto_switch"] is False
    assert reviewed["nba_ml_hold"] is True


def test_callers_do_not_insert_and_the_two_writers_do():
    base = ROOT / "provenance" / "baseline"
    writers = {
        base / "polymarket-paper" / "runner.py": True,
        base / "polymarket-paper" / "pm_fill.py": True,
    }
    callers = [
        base / "polymarket-paper" / "pm_nba_live.py",
        base / "polymarket-paper" / "pm_me_track.py",
        base / "marv-feed" / "live_props" / "live_props.py",
    ]
    for path, inserts in writers.items():
        text = path.read_text().lower()
        assert ("insert or ignore into fills" in text) is inserts
    for path in callers:
        text = path.read_text().lower()
        assert "insert into fills" not in text
        assert "pm_fill" in text


def test_runnable_package_does_not_open_the_network_or_opt_paths():
    for path in (ROOT / "poly_paper").glob("*.py"):
        text = path.read_text()
        assert "urllib" not in text
        assert "subprocess" not in text
        assert "sys.path.insert" not in text
        assert "socket." not in text
    mapper = (ROOT / "poly_paper" / "mapper.py").read_text()
    assert "sys.path.insert" not in mapper
