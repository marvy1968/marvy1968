"""Telegram bot: command parsing, auth filter, cache-first replies, allowance, pushes."""
from datetime import datetime, timezone

import pytest

from ajbet.alerts import Notifier
from ajbet.db import DB
from ajbet.espn import Game, Team
from ajbet.oddsapi import OddsAPI
from ajbet.telegram_bot import (TelegramBot, authorized, chunks, parse_command, prop_markets_in,
                                strip_prop_words)
from ajbet.value_scan import ValueScanner

CHAT = "424242"


@pytest.mark.parametrize("text,cmd,arg", [
    ("/today", "today", ""),
    ("/upcoming wnba", "upcoming", "wnba"),
    ("/game Bears", "game", "Bears"),
    ("/props caitlin clark", "props", "caitlin clark"),
    ("/props@March_betbot bears", "props", "bears"),
    ("/edges", "edges", ""), ("/recs", "recs", ""), ("/report", "report", ""), ("/budget", "budget", ""),
    ("/help", "help", ""), ("/start", "help", ""),
    ("/todya", "today", ""),                       # typo -> closest command
    ("/foo", "search", "foo"),          # unknown /word -> free-text lookup
    ("bears game", "game", "bears"),
    ("caitlin clark points", "props", "caitlin clark points"),
    ("texas team total", "props", "texas team total"),
    ("any edges?", "edges", ""),
    ("best bets", "recs", ""),
    ("how much budget left", "budget", ""),
    ("nfl schedule", "upcoming", "nfl"),
    ("today", "today", ""),
    ("A'ja Wilson", "search", "A'ja Wilson"),
])
def test_parse_command(text, cmd, arg):
    assert parse_command(text, "March_betbot") == (cmd, arg)


def test_command_for_other_bot_ignored():
    assert parse_command("/today@SomeOtherBot", "March_betbot") == ("ignore", "")


def test_prop_words():
    assert prop_markets_in("caitlin clark points") == ["player_points"]
    assert prop_markets_in("wilson pra") == ["player_points_rebounds_assists"]
    assert "team_totals" in prop_markets_in("bears team total")
    assert strip_prop_words("caitlin clark points") == "caitlin clark"


def upd(chat_id, text="/today", uid=1):
    return {"update_id": uid, "message": {"chat": {"id": chat_id}, "text": text}}


def test_auth_filter_only_configured_chat():
    assert authorized(upd(int(CHAT)), CHAT) and authorized(upd(CHAT), CHAT)
    assert not authorized(upd(999), CHAT)
    assert not authorized(upd(int(CHAT)), "")                     # unset chat id -> nobody
    assert not authorized({"update_id": 3, "callback_query": {}}, CHAT)


class FakeResp:
    def __init__(self, body, code=200):
        self.body, self.status_code = body, code

    def json(self):
        return self.body


class FakeHTTP:
    def __init__(self, updates):
        self.updates, self.sent = updates, []

    def post(self, url, data=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        if method == "getUpdates":
            ups, self.updates = self.updates, []
            return FakeResp({"ok": True, "result": ups})
        self.sent.append((method, data))
        return FakeResp({"ok": True, "result": {}})


def make_bot(cfg, http=None, now=datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc), games=None, dry=False):
    db = DB()
    n = Notifier(cfg, dry_run=dry, printer=lambda s: None)
    n._token, n._chat = "T0KEN", CHAT
    vs = ValueScanner(cfg, db, OddsAPI(cfg, db, api_key=""), n, fetch=lambda p, q: {}, clock=lambda: now)
    vs.scope_games = lambda **kw: games or {}
    return TelegramBot(cfg, db, None, vs, n, http=http or FakeHTTP([]), clock=lambda: now)


def test_poll_queues_only_authorised_messages(cfg):
    http = FakeHTTP([upd(int(CHAT), "/help", 10), upd(777, "/report", 11), upd(int(CHAT), "bears game", 12)])
    bot = make_bot(cfg, http)
    assert bot.poll_once(timeout=0) == 2 and bot.ignored == 1 and bot.offset == 13
    assert [bot.q.get_nowait(), bot.q.get_nowait()] == [(CHAT, "/help"), (CHAT, "bears game")]


def test_replies_go_only_to_configured_chat_and_token_scrubbed(cfg, caplog):
    http = FakeHTTP([])
    bot = make_bot(cfg, http)
    bot.reply("hello")
    assert http.sent == [("sendMessage", {"chat_id": CHAT, "text": "hello", "parse_mode": "HTML",
                                          "disable_web_page_preview": True})]
    assert "T0KEN" not in bot._scrub("error at https://api.telegram.org/botT0KEN/getUpdates")


def test_poll_error_does_not_crash(cfg):
    class Boom:
        def post(self, *a, **k):
            raise ConnectionError("net down botT0KEN")
    bot = make_bot(cfg, Boom())
    with pytest.raises(ConnectionError):
        bot.poll_once(timeout=0)
    bot._stop.set()
    bot._loop()                                                   # returns cleanly once stopped


def G():
    return Game("401", "nfl", "us", "x", "2026-10-04T17:00:00Z", "pre", False, 0, 0, "", "",
                Team("Chicago Bears", "CHI", 0, "3"), Team("Green Bay Packers", "GB", 0, "9"))


def tt_event():
    def bk(key, o, u):
        return {"key": key, "markets": [{"key": "team_totals", "outcomes": [
            {"name": "Over", "description": "Chicago Bears", "point": 23.5, "price": o},
            {"name": "Under", "description": "Chicago Bears", "point": 23.5, "price": u}]}]}
    return {"id": "oe1", "home_team": "Chicago Bears", "away_team": "Green Bay Packers",
            "commence_time": "2026-10-04T17:00:00Z",
            "bookmakers": [bk("pinnacle", -110, -110), bk("fanduel", -112, -108), bk("draftkings", -110, -110),
                           bk("bovada", 120, -150)]}


def test_props_team_cache_first_with_age(cfg):
    bot = make_bot(cfg, games={"nfl": [G()]})
    ev = {k: v for k, v in tt_event().items() if k != "bookmakers"}
    bot.db.kv_set("lastfeatured:nfl", [ev])
    bot.db.kv_set("lastprops:nfl:oe1:team", tt_event())
    bot.db.conn.execute("UPDATE kv SET ts='2026-10-04T15:00:00+00:00'")       # = bot clock
    calls = []
    bot.vs.odds.event_odds = lambda *a, **k: calls.append(a) or None
    out = bot.handle("/props bears")
    assert "<b>Team props: Chicago Bears</b>" in out and "<b>Bears OVER 23.5 points</b>" in out
    assert "+120 @ Bovada" in out and "Packers @ Bears, 12:00 PM CT" in out
    assert "prices just updated" in out and calls == []           # fresh cache -> no credits
    assert "✅" in out.split("\n")[2]     # NFL team-total over +10% ⭐⭐⭐ at Bovada: alertable since Oct 3 2:15 PM
    assert "<b>Bears UNDER 23.5 points</b>" in out
    assert bot.handle("bears team total").startswith("🏈 <b>Team props: Chicago Bears</b>")


def test_stale_cache_fetches_within_allowance_only(cfg):
    later = datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc)
    bot = make_bot(cfg, games={"nfl": [G()]}, now=later)
    ev = {k: v for k, v in tt_event().items() if k != "bookmakers"}
    bot.db.kv_set("lastfeatured:nfl", [ev])
    bot.db.kv_set("lastprops:nfl:oe1:team", tt_event())
    bot.db.conn.execute("UPDATE kv SET ts='2026-10-04T14:00:00+00:00'")
    calls = []
    bot.vs.odds.event_odds = lambda sk, eid, mk, strategy="value": calls.append((eid, mk, strategy)) or tt_event()
    out = bot.handle("/props bears")
    assert calls and calls[0][2] == "query" and "prices just updated" in out
    bot.allowance_left = lambda: 0
    bot.db.conn.execute("UPDATE kv SET ts='2026-10-04T14:00:00+00:00'")
    calls.clear()
    out = bot.handle("/props bears")
    assert calls == [] and "daily lookup limit reached" in out and "from 2 h ago" in out


def test_game_and_unknown(cfg):
    bot = make_bot(cfg, games={"nfl": [G()]})
    assert "couldn't find a game for <b>lions</b>" in bot.handle("/game lions")
    out = bot.handle("bears game")
    assert out.startswith("🏈 <b>Packers @ Bears</b>\n12:00 PM CT")
    assert "didn't catch that" in bot.handle("/foo")
    assert "&lt;team&gt;" in bot.handle("/help")                   # HTML-escaped


def test_edges_recs_report_budget(cfg):
    bot = make_bot(cfg)
    assert "No alerts" in bot.handle("/edges")
    bot.db.insert_alert(dict(dedupe_key="k1", event_id="1", league="nfl", strategy="value", market="team_totals",
                             selection="Over", line=23.5, price=120, book="bovada", fair_p=0.5, edge=0.1, value=1,
                             trigger="market", message="AJ Bet Bot: VALUE Bears TT o23.5 +120"))
    bot.db.insert_alert(dict(dedupe_key="k2", event_id="1", league="nfl", strategy="value", market="team_totals",
                             selection="Over", line=23.5, price=110, book="fanduel", fair_p=0.5, edge=0.02,
                             trigger="watchlist", message="[watchlist] x"))
    e = bot.handle("/edges")
    assert "Team OVER 23.5 points</b> +120 @ Bovada" in e and "edge +10% ⭐⭐⭐" in e and "FanDuel" not in e
    r = bot.handle("/recs")
    assert r.index("Bovada") < r.index("FanDuel") and "📋 watchlist" in r and "prop line shopping, unproven" in r
    rep = bot.handle("/report")
    assert rep.startswith("📊 <b>Paper results</b>") and "All-time:" in rep and "/report full" in rep
    full = bot.handle("/report full")
    assert full.startswith("<pre>") and "paper P&amp;L" in full
    assert f"Lookups left today: <b>{cfg['odds_api']['allocations']['query']['daily']}</b>" in bot.handle("/budget")


def test_digest_once_per_day_and_results_push(cfg):
    sent = []
    bot = make_bot(cfg, now=datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc))   # 10:00 CT
    bot.notifier.send = lambda t, html=None, **_k: sent.append(t) or True
    assert bot.maybe_digest() and not bot.maybe_digest()
    assert sent[0].startswith("☀️ <b>Good morning!")
    assert not bot.push_results([]) and bot.push_results(["nfl CHI ML won +100"])
    assert "🧾 <b>Results</b>" in sent[-1] and "Tonight:" in sent[-1]
    early = make_bot(cfg, now=datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc))  # 07:00 CT
    assert not early.maybe_digest()
    late = make_bot(cfg, now=datetime(2026, 10, 4, 19, 0, tzinfo=timezone.utc))   # 2 pm CT restart
    assert not late.maybe_digest()


def test_start_skips_backlog(cfg):
    http = FakeHTTP([upd(int(CHAT), "/report", 55)])
    bot = make_bot(cfg, http)
    bot._loop = lambda: None
    assert bot.start() and bot.offset == 56


def test_serve_until_answers_queue(cfg):
    import time
    http = FakeHTTP([])
    bot = make_bot(cfg, http)
    bot.q.put("/help")
    assert bot.serve_until(time.time() + 1.2) == 1
    assert http.sent and "Commands" in http.sent[0][1]["text"]


def test_chunks_respect_limit():
    parts = chunks("x" * 5000 + "\nshort", 3900)
    assert all(len(p) <= 3900 for p in parts) and parts[-1].endswith("short")


def test_reply_falls_back_to_plain_text_when_html_rejected(cfg):
    class Resp:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    class H:
        def __init__(self):
            self.sent = []

        def post(self, url, data=None, timeout=None):
            if data.get("parse_mode") == "HTML":
                return Resp(400, {"ok": False, "description": "Bad Request: can't parse entities"})
            self.sent.append(data["text"])
            return Resp(200, {"ok": True, "result": {}})
    h = H()
    bot = make_bot(cfg, h)
    bot.reply("🏈 <b>Steelers @ Browns</b>\nBovada: <b>−150</b> ❌ too expensive &amp; more")
    assert h.sent == ["🏈 Steelers @ Browns\nBovada: −150 ❌ too expensive & more"]


def test_unknown_slash_falls_back_to_free_text():
    assert parse_command("/ steelers") == ("search", "steelers")
    assert parse_command("/steelers") == ("search", "steelers")
    assert parse_command("/caitlin clark points")[0] == "props"
    assert parse_command("/steelers game") == ("game", "steelers")
    assert parse_command("/gmae steelers") == ("game", "steelers")      # typo of a real command
    assert parse_command("/") == ("help", "")


def test_backlog_answers_recent_commands_only(cfg):
    import time as _t
    now = _t.time()
    ups = [{"update_id": 5, "message": {"chat": {"id": int(CHAT)}, "text": "/game steelers", "date": now - 30}},
           {"update_id": 4, "message": {"chat": {"id": int(CHAT)}, "text": "/today", "date": now - 3600}}]
    bot = make_bot(cfg, FakeHTTP(ups))
    assert bot.take_backlog(600) == 1 and bot.q.get_nowait() == (CHAT, "/game steelers") and bot.offset == 6


def test_edges_only_real_priced_alerts_and_bovada_split(cfg):
    import json as _j
    from ajbet import mybook
    bot = make_bot(cfg)
    mybook.configure({"alerts": {"my_book": "bovada", "only_my_book": True}})
    ins = bot.db.insert_alert
    ins(dict(dedupe_key="s", event_id="9", league="uefa.nations", strategy="soccer_grind", market="alternate_totals",
             selection="Under 9.5", line=9.5, price=None, trigger="live total", message="x"))
    ins(dict(dedupe_key="m", event_id="1", league="wnba", strategy="value", market="h2h", selection="Indiana Fever",
             price=165, book="lowvig", fair_p=0.36, edge=-0.04, value=2, trigger="model", message="x"))
    ins(dict(dedupe_key="b", event_id="1", league="nfl", strategy="value", market="player_rush_yds", selection="Under",
             line=95.5, price=-300, book="bovada", edge=0.064, value=1, trigger="market", message="x",
             details_json=_j.dumps({"matchup": "Pittsburgh Steelers @ Cleveland Browns", "ev_min": 0.05})))
    ins(dict(dedupe_key="o", event_id="1", league="wnba", strategy="value", market="player_points", selection="Over",
             line=23.5, price=-105, book="hardrockbet", edge=0.098, value=1, trigger="market", message="x",
             details_json=_j.dumps({"my_price": None})))
    ins(dict(dedupe_key="ls", event_id="2", league="nfl", strategy="line_swing", market="h2h", selection="x",
             price=210, trigger="info", message="x"))
    e = bot.handle("/edges")
    assert "no price" not in e and "Fever to WIN" not in e and "9.5 9.5" not in e
    assert e.index("Alerts at Bovada") < e.index("UNDER 95.5") < e.index("Not at Bovada (watchlist only)") \
        < e.index("Hard Rock")
    assert "Steelers @ Cleveland Browns" in e


def test_soccer_label_not_duplicated():
    from ajbet import friendly as fr
    assert fr.pick_label("uefa.nations", "alternate_totals", "Under 9.5", 9.5) == "UNDER 9.5 total goals"


def test_multi_recipient_broadcast_reply_to_asker_and_unknown_logged(cfg, tmp_path, monkeypatch):
    import json as _j
    import ajbet.alerts as al
    rf = tmp_path / "recipients.json"
    rf.write_text(_j.dumps([{"chat_id": "555", "name": "AJ"}]))
    cfg["telegram"]["recipients_file"] = str(rf)
    cfg["telegram"]["bot"]["unknown_log"] = str(tmp_path / "unk.jsonl")
    ups = [{"update_id": 1, "message": {"chat": {"id": 555, "type": "private"}, "text": "/today"}},
           {"update_id": 2, "message": {"chat": {"id": 999, "type": "private", "first_name": "Rando"},
                                        "from": {"first_name": "Rando", "username": "r"}, "text": "/start"}}]
    h = FakeHTTP(ups)
    db = DB()
    n = Notifier(cfg, dry_run=False, printer=lambda s: None)
    n._token, n._chat = "T0KEN", CHAT
    bot = TelegramBot(cfg, db, None, None, n, http=h)
    assert n.recipients() == [CHAT, "555"]
    assert bot.poll_once(timeout=0) == 1 and bot.q.get_nowait() == ("555", "/today")
    unk = [_j.loads(x) for x in (tmp_path / "unk.jsonl").read_text().splitlines()]
    assert unk[0]["chat_id"] == "999" and unk[0]["first_name"] == "Rando"
    bot.reply("hi", "555")
    assert h.sent[-1][1]["chat_id"] == "555"
    sent = []

    class R:
        status_code = 200
    monkeypatch.setattr(al.requests, "post", lambda url, data=None, timeout=None: sent.append(data["chat_id"]) or R())
    assert n.send("📊 <b>x</b>") and sent == [CHAT, "555"]
