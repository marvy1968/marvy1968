import os
import unittest
from unittest import mock

from marv import telegram


class MirrorTests(unittest.TestCase):
    def run_send(self, env, **kw):
        calls = []
        def fake_post(url, json, timeout):
            calls.append((url.split("/bot")[1].split("/")[0], json["chat_id"], json["text"]))
            return mock.Mock(raise_for_status=lambda: None)
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(telegram.requests, "post", fake_post):
            telegram.send_message("MARVTOKEN", "123", "NEW EDGE\nUnder 48.5", **kw)
        return calls

    def test_label_and_mirror(self):
        calls = self.run_send({"MIRROR_BOT_TOKEN": "MARCHTOKEN", "MIRROR_CHAT_ID": ""})
        self.assertEqual([c[:2] for c in calls], [("MARVTOKEN", "123"), ("MARCHTOKEN", "123")])  # same user id
        self.assertTrue(all(c[2].startswith("👽 <b>MARV</b>\nNEW EDGE") for c in calls))

    def test_explicit_chat_no_mirror_and_failures(self):
        calls = self.run_send({"MIRROR_BOT_TOKEN": "MARCHTOKEN", "MIRROR_CHAT_ID": "-999"})
        self.assertEqual(calls[1][:2], ("MARCHTOKEN", "-999"))
        self.assertEqual(len(self.run_send({"MIRROR_BOT_TOKEN": "MARCHTOKEN"}, mirror=False)), 1)  # command replies etc.
        self.assertEqual(len(self.run_send({"MIRROR_BOT_TOKEN": ""})), 1)  # not configured: Marv chat only

        def flaky(url, json, timeout):
            if "MARCHTOKEN" in url:
                raise RuntimeError("blocked")
            return mock.Mock(raise_for_status=lambda: None)
        with mock.patch.dict(os.environ, {"MIRROR_BOT_TOKEN": "MARCHTOKEN"}), mock.patch.object(telegram.requests, "post", flaky):
            telegram.send_message("MARVTOKEN", "123", "x")  # a broken mirror never blocks the Marv message


if __name__ == "__main__":
    unittest.main()


class TokenCleanupTests(unittest.TestCase):
    def test_copy_paste_slips_are_forgiven(self):
        good = "1234567890:AAEexampleexampleexampleexample12345"
        for raw in (good, f" {good}\r", f'"{good}"', f"MIRROR_BOT_TOKEN={good}", f"{good} "):
            with mock.patch.dict(os.environ, {"MIRROR_BOT_TOKEN": raw}):
                self.assertEqual(telegram.mirror_token(), good)
        self.assertIn("10 digits before the colon", telegram.token_shape(good))
        self.assertEqual(telegram.token_shape(""), "empty")
        self.assertIn("no digits", telegram.token_shape("hello"))
