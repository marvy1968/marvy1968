"""Snapshot replay: isolated state, no network, no Telegram, same pricer."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from marv import live_prop_engine as E
from marv import prop_snapshot as S
from marv.config import Settings
from marv.main import main

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "prop_snapshot_v1.sample.json"


def _forbid_network():
    def boom(*_a, **_k):
        raise AssertionError("network used during snapshot replay")
    return [
        mock.patch("requests.sessions.Session.request", boom),
        mock.patch("requests.get", boom),
        mock.patch("requests.post", boom),
        mock.patch("urllib.request.urlopen", boom),
        mock.patch("socket.create_connection", boom),
        mock.patch("marv.live_prop_engine.Runner._budget_ok", boom),
        mock.patch("marv.live_prop_engine.Runner._event", boom),
        mock.patch("marv.live_prop_engine.Runner._odds", boom),
        mock.patch("marv.data.espn.ESPNClient.scoreboard", boom),
        mock.patch("marv.telegram.send_message", boom),
    ]


class SnapshotReplayTests(unittest.TestCase):
    def setUp(self):
        self._patches = _forbid_network()
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in self._patches:
            patch.stop()

    def test_settings_state_dir_is_configurable(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / "empty.env"
            env.write_text("")
            with mock.patch.dict(os.environ, {"STATE_DIR": str(Path(d) / "state"), "MARV_ENV": str(env)}):
                got = Path(Settings.from_env().state_dir).resolve()
            self.assertEqual(got, (Path(d) / "state").resolve())

    def test_shared_install_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as d:
            shared = Path(d) / "opt-marv-bot"
            shared.mkdir()
            (shared / "sentinel").write_text("keep")
            doc = json.loads(FIXTURE.read_text())
            with mock.patch.object(S, "SHARED_INSTALL", shared), \
                    mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off"}):
                for bad in (shared, shared / "state", shared.parent):
                    with self.assertRaises(S.ReplayError):
                        S.replay_file(FIXTURE, bad)
            self.assertEqual((shared / "sentinel").read_text(), "keep")
            self.assertFalse((shared / "live_props").exists())
            self.assertEqual(S.SHARED_INSTALL, Path("/opt/marv-bot"))
            self.assertIsInstance(doc, dict)

    def test_replay_writes_only_shadow_and_matches_scan_game(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "replay-state"
            other = Path(d) / "other"
            other.mkdir()
            (other / "sentinel").write_text("untouched")
            ignored = Path(d) / "from-env"
            ignored.mkdir()
            (state / "live_props").mkdir(parents=True)
            (state / "live_props" / "credits.json").write_text('{"keep": true}')
            (state / "picks.json").write_text("[]")
            env = {
                "LIVE_PROPS_SEND": "off",
                "LIVE_PROPS_ENGINE": "on",
                "LIVE_PROPS_LOG": "on",
                "ODDS_API_KEY": "not-used",
                "STATE_DIR": str(ignored),
            }
            with mock.patch.dict(os.environ, env):
                code = main(["live-props", "replay", "--snapshot", str(FIXTURE), "--state-dir", str(state)])
            self.assertEqual(code, 0)
            self.assertEqual(list(ignored.rglob("*")), [])
            self.assertEqual((other / "sentinel").read_text(), "untouched")
            self.assertEqual((state / "live_props" / "credits.json").read_text(), '{"keep": true}')
            self.assertEqual((state / "picks.json").read_text(), "[]")
            written = sorted(p.relative_to(state).as_posix() for p in state.rglob("*") if p.is_file())
            self.assertEqual(written, ["live_props/credits.json", "live_props/shadow.jsonl", "picks.json"])
            first = (state / "live_props" / "shadow.jsonl").read_text()
            with mock.patch.dict(os.environ, env):
                self.assertEqual(main(["live-props", "replay", "--snapshot", str(FIXTURE),
                                        "--state-dir", str(state)]), 0)
            self.assertEqual((state / "live_props" / "shadow.jsonl").read_text(), first)
            rows = [json.loads(line) for line in first.splitlines()]
            self.assertEqual(len(rows), 2)
            priced = next(r for r in rows if not r["abstained"])
            missed = next(r for r in rows if r["abstained"])
            self.assertEqual(priced["schema"], S.SCHEMA_OUT)
            self.assertEqual(priced["model_version"], "claude-prop1")
            self.assertEqual(priced["anchor_kind"], "model_projection")
            self.assertFalse(priced["quoted_before_start"])
            self.assertEqual(priced["cohort_key"], "nfl|new york giants|dallas cowboys|2026-11-08T18:00:00Z")
            self.assertEqual(priced["aliases"]["espn_event_id"], "401772969")
            self.assertTrue(priced["input_hash"].startswith("sha256:"))
            self.assertEqual(missed["abstain_reason"], "player_not_in_box")
            self.assertEqual(missed["input_hash"], priced["input_hash"])
            self.assertNotEqual(priced["anchor_kind"], "pregame")
            doc = json.loads(FIXTURE.read_text())
            obs = doc["observations"][0]
            payload = S.hash_payload(obs, "observations[0]")
            direct, _recs = E.scan_game(
                "nfl", {"boxscore": obs["boxscore"]}, obs["quoted_odds"],
                {("ceedee lamb", "player_reception_yds"): 70.0}, 30.0,
                "Dallas Cowboys", "New York Giants", 14, 10,
                now=datetime(2026, 11, 8, 19, 0, tzinfo=timezone.utc))
            self.assertEqual(len(direct), 1)
            self.assertEqual(priced["edge"], round(direct[0]["edge"], 4))
            self.assertEqual(priced["p"], round(direct[0]["p"], 4))
            self.assertEqual(priced["proj"], round(direct[0]["proj"], 4))
            self.assertEqual(priced["input_hash"], S.input_hash(payload))

    def test_explicit_off_required_and_inferred_is_not_called_pregame(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state"
            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "on"}):
                self.assertEqual(main(["live-props", "replay", "--snapshot", str(FIXTURE),
                                        "--state-dir", str(state)]), 2)
            os.environ.pop("LIVE_PROPS_SEND", None)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LIVE_PROPS_SEND", None)
                self.assertEqual(main(["live-props", "replay", "--snapshot", str(FIXTURE),
                                        "--state-dir", str(state)]), 2)
            self.assertFalse(state.exists())
            doc = json.loads(FIXTURE.read_text())
            doc["observations"][0]["anchors"] = [{
                "player": "CeeDee Lamb", "market": "player_reception_yds", "value": 70.0, "kind": "inferred",
            }]
            doc["observations"][0]["anchors"].append({
                "player": "CeeDee Lamb", "market": "player_reception_yds", "value": 90.0, "kind": "quoted_pregame",
            })
            snap = Path(d) / "snap.json"
            snap.write_text(json.dumps(doc))
            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off"}):
                n_obs, n_priced, n_abs, dest = S.replay_file(snap, state)
            self.assertEqual((n_obs, n_priced, n_abs), (1, 1, 1))
            priced = next(json.loads(line) for line in dest.read_text().splitlines() if '"abstained":false' in line)
            # quoted_pregame outranks inferred, so the number used is the real quote.
            self.assertEqual(priced["anchor_kind"], "quoted_pregame")
            self.assertTrue(priced["quoted_before_start"])
            self.assertEqual(priced["anchor"], 90.0)
            doc["observations"][0]["anchors"] = [{
                "player": "CeeDee Lamb", "market": "player_reception_yds", "value": 70.0, "kind": "inferred",
            }]
            snap.write_text(json.dumps(doc))
            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off"}):
                _n, _p, _a, dest = S.replay_file(snap, state)
            priced = next(json.loads(line) for line in dest.read_text().splitlines() if '"abstained":false' in line)
            self.assertEqual(priced["anchor_kind"], "inferred")
            self.assertFalse(priced["quoted_before_start"])
            self.assertNotIn("pregame", json.dumps(priced))
            doc["observations"][0]["anchors"][0]["kind"] = "pregame"
            snap.write_text(json.dumps(doc))
            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off"}):
                with self.assertRaises(S.ReplayError):
                    S.replay_file(snap, state)

    def test_stale_quote_abstains_without_a_partial_file_on_bad_input(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state"
            doc = json.loads(FIXTURE.read_text())
            market = doc["observations"][0]["quoted_odds"]["bookmakers"][0]["markets"][0]
            market["last_update"] = "2026-11-08T18:00:00Z"
            doc["observations"][0]["quoted_odds"]["bookmakers"][0]["markets"] = [market]
            snap = Path(d) / "stale.json"
            snap.write_text(json.dumps(doc))
            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off"}):
                _n, n_priced, n_abs, dest = S.replay_file(snap, state)
            self.assertEqual((n_priced, n_abs), (0, 1))
            row = json.loads(dest.read_text())
            self.assertEqual(row["abstain_reason"], "stale_quote")
            doc["observations"][0].pop("sport")
            snap.write_text(json.dumps(doc))
            before = dest.read_text()
            with mock.patch.dict(os.environ, {"LIVE_PROPS_SEND": "off"}):
                with self.assertRaises(S.ReplayError):
                    S.replay_file(snap, state)
            self.assertEqual(dest.read_text(), before)


if __name__ == "__main__":
    unittest.main()
