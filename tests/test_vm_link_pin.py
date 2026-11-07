"""marv-vmlink `update` must not run install.sh while a deploy pin is set.

Other allowlisted jobs stay available. The pin value is not copied into the refusal text.
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "vm_link_under_test", Path(__file__).resolve().parents[1] / "deploy" / "vm_link.py")
vm_link = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vm_link)


class DeployPinTests(unittest.TestCase):
    def test_update_runs_only_when_unpinned(self):
        missing = str(Path(tempfile.gettempdir()) / "marv-deploy-pin-missing-d7c4")
        with mock.patch.dict(os.environ, {"MARV_DEPLOY_PIN": "", "MARV_DEPLOY_PIN_FILE": missing}):
            argv, err = vm_link.parse("update")
        self.assertEqual(err, "")
        self.assertEqual(argv[0], "bash")
        self.assertTrue(str(argv[1]).endswith("deploy/install.sh"))

    def test_update_refused_when_env_or_file_pin_set_other_jobs_still_parse(self):
        with tempfile.TemporaryDirectory() as d:
            pin = Path(d) / "pin"
            pin.write_text("super-secret-value\n")
            missing = str(Path(d) / "no-such-pin")
            with mock.patch.dict(os.environ, {"MARV_DEPLOY_PIN": "abc123", "MARV_DEPLOY_PIN_FILE": missing}):
                argv, err = vm_link.parse("update")
                status, status_err = vm_link.parse("status")
            self.assertIsNone(argv)
            self.assertIn("refused", err)
            self.assertNotIn("abc123", err)
            self.assertEqual(status_err, "")
            self.assertIsNotNone(status)
            with mock.patch.dict(os.environ, {"MARV_DEPLOY_PIN": "", "MARV_DEPLOY_PIN_FILE": str(pin)}):
                argv, err = vm_link.parse("update")
            self.assertIsNone(argv)
            self.assertIn("refused", err)
            self.assertNotIn("super-secret-value", err)


if __name__ == "__main__":
    unittest.main()
