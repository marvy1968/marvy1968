import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alpacabot import paper


class TradeTelegramTests(unittest.TestCase):
    def test_goes_only_to_the_trade_chat(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text('TELEGRAM_BOT_TOKEN=betting\nMIRROR_CHAT_ID=edge\nTRADE_TG_TOKEN="tok123" # master trade\nTRADE_TG_CHAT_ID=-100777\n')
            with mock.patch.dict(os.environ, {"MARV_ENV": str(env)}), mock.patch.object(paper.requests, "post") as post:
                post.return_value.status_code = 200
                self.assertTrue(paper.send_telegram("hello"))
            url, kw = post.call_args.args[0], post.call_args.kwargs
            self.assertEqual(url, "https://api.telegram.org/bottok123/sendMessage")
            self.assertEqual(kw["json"]["chat_id"], "-100777")
            self.assertTrue(kw["json"]["text"].startswith("ALPACA PAPER\nhello"))

    def test_no_trade_chat_means_nothing_sent_and_no_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("TELEGRAM_BOT_TOKEN=betting\nTELEGRAM_CHAT_ID=1\nMIRROR_BOT_TOKEN=m\nMIRROR_CHAT_ID=edge\n")
            clean = {k: v for k, v in os.environ.items() if not k.startswith("TRADE_TG")}
            with mock.patch.dict(os.environ, {**clean, "MARV_ENV": str(env)}, clear=True), mock.patch.object(paper.requests, "post") as post:
                self.assertFalse(paper.send_telegram("hello"))
            post.assert_not_called()

    def test_refusal_returns_false_without_leaking_the_token(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("TRADE_TG_TOKEN=secret999\nTRADE_TG_CHAT_ID=5\n")
            with mock.patch.dict(os.environ, {"MARV_ENV": str(env)}), mock.patch.object(paper.requests, "post") as post, \
                    self.assertLogs("alpacabot", level="WARNING") as logs:
                post.return_value.status_code = 403
                post.return_value.text = "Forbidden secret999"
                self.assertFalse(paper.send_telegram("x"))
            self.assertNotIn("secret999", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
