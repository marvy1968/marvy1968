import os
import tempfile
import unittest
from unittest import mock

import requests

from marv import telegram


def resp(code=200, desc=""):
    r = mock.Mock(status_code=code)
    def rfs():
        if code >= 400:
            raise requests.HTTPError(f"{code} for url: https://api.telegram.org/botSECRET/sendMessage")
    r.raise_for_status = rfs
    r.json = lambda: {"ok": code < 400, "description": desc, "result": {"username": "Marvy1_bot"}}
    return r


class BlockedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"STATE_DIR": self.tmp.name, "MIRROR_BOT_TOKEN": "9:MIRROR",
                                                "MIRROR_CHAT_ID": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_blocked_once_note_then_quiet_then_recovers(self):
        calls = []
        blocked = {"on": True}
        def post(url, json, timeout):
            calls.append(url.split("/bot")[1].split("/")[0])
            if "1:MARV" in url and blocked["on"]:
                return resp(403, "Forbidden: bot was blocked by the user")
            return resp()
        get = mock.Mock(return_value=resp())
        with mock.patch.object(telegram.requests, "post", post), mock.patch.object(telegram.requests, "get", get):
            telegram.send_message("1:MARV", "123", "a")   # 403: no raise, mirror note + mirror copy
            self.assertTrue(telegram.blocked_since("1:MARV", "123"))
            self.assertEqual(calls, ["1:MARV", "9:MIRROR", "9:MIRROR"])
            calls.clear()
            for _ in range(5):
                telegram.send_message("1:MARV", "123", "b")  # within the retry window: Marv chat not hit, no new note
            self.assertEqual(calls, ["9:MIRROR"] * 5)
            # after the window, the bot is unblocked: delivered and the flag clears
            st = telegram._block_state()
            for v in st.values():
                v["tried"] = 0
            telegram._block_save(st)
            blocked["on"] = False
            calls.clear()
            telegram.send_message("1:MARV", "123", "c", mirror=False)
            self.assertEqual(calls, ["1:MARV"])
            self.assertIsNone(telegram.blocked_since("1:MARV", "123"))

    def test_other_errors_raise_without_token(self):
        with mock.patch.object(telegram.requests, "post", lambda url, json, timeout: resp(400, "Bad Request: x")):
            with self.assertRaises(telegram.TelegramError) as cm:
                telegram.send_message("1:MARV", "123", "a", mirror=False)
        self.assertNotIn("SECRET", str(cm.exception))
        self.assertNotIn("MARV", str(cm.exception))


    def test_marv_telegram_off_mirror_only(self):
        calls = []
        def post(url, json, timeout):
            calls.append(url.split("/bot")[1].split("/")[0])
            return resp()
        with mock.patch.dict(os.environ, {"MARV_TELEGRAM": "off", "MIRROR_BOT_TOKEN": "9:MIRROR"}), \
             mock.patch.object(telegram.requests, "post", post):
            telegram.send_message("1:MARV", "123", "a")
            telegram.send_message("1:MARV", "123", "b", mirror=False)
        self.assertEqual(calls, ["9:MIRROR"])  # Marv bot never hit; mirror=False is quiet

if __name__ == "__main__":
    unittest.main()
