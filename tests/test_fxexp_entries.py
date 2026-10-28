import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import fxexp_entries as fe  # noqa: E402


class FxexpEntriesTests(unittest.TestCase):
    def test_stop_flips_only_that_key_keeps_the_mode_and_makes_a_backup(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            cfg = {"systems": ["a", "b"], "risk_pct": 0.25, "no_new_entries": False, "system_pairs": {"a": ["EUR_USD"]}}
            Path(p).write_text(json.dumps(cfg))
            os.chmod(p, 0o640)
            self.assertEqual(fe.main(["stop"], p), 0)
            new = json.loads(Path(p).read_text())
            self.assertTrue(new["no_new_entries"])
            self.assertEqual({k: v for k, v in new.items() if k != "no_new_entries"}, {k: v for k, v in cfg.items() if k != "no_new_entries"})
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o640)
            self.assertEqual(len([f for f in os.listdir(d) if f.startswith("config.json.bak-")]), 1)
            self.assertEqual(fe.main(["resume"], p), 0)
            self.assertFalse(json.loads(Path(p).read_text())["no_new_entries"])

    def test_status_changes_nothing_and_bad_args_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            Path(p).write_text(json.dumps({"systems": [], "no_new_entries": False}))
            before = Path(p).read_text()
            self.assertEqual(fe.main(["status"], p), 0)
            self.assertEqual(Path(p).read_text(), before)
            self.assertEqual(fe.main(["bogus"], p), 2)
            self.assertEqual(os.listdir(d), ["config.json"])

    def test_vm_link_accepts_only_the_three_words(self):
        import vm_link
        argv, err = vm_link.parse("fx-experts-entries stop")
        self.assertEqual(err, "")
        self.assertEqual(argv[0], "python3")
        self.assertTrue(argv[1].endswith("deploy/fxexp_entries.py"))
        self.assertEqual(argv[2], "stop")
        self.assertIsNone(vm_link.parse("fx-experts-entries delete")[0])
        self.assertIsNone(vm_link.parse("fx-experts-entries")[0])

if __name__ == "__main__":
    unittest.main()
