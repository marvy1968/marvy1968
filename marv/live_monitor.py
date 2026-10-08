"""In-game monitor: after every quarter (or half), re-price the moneyline and over/under.

`python -m marv live` runs as a service. Every couple of minutes it reads ESPN's live
scoreboard for the enabled sports. When a period ends it combines Marv's pregame projection
(state/predictions.json), the current score and clock, and this game's actual scoring pace into a
live win probability and projected total, compares them with the current line (The Odds API live
prices when ODDS_API_KEY is set, otherwise the pregame line) and sends one Telegram update per
period, plus a final result.

Live numbers are fair prices from a model, not backtested picks: updates say "lean" unless the
edge is large, and nothing here changes the pregame plays.
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import bridge
from .data.espn import ESPNClient
from .data.teams import similarity
from .markets import american_to_prob, prob_to_american

log = logging.getLogger(__name__)

# periods, minutes per period, label, ESPN scoreboard path
STRUCTURE = {
    "nfl": (4, 15, "Q", "football/nfl"),
    "cfb": (4, 15, "Q", "football/college-football?groups=80"),
    "wnba": (4, 10, "Q", "basketball/wnba"),
    "nba": (4, 12, "Q", "basketball/nba"),
    "ncaab": (2, 20, "H", "basketball/mens-college-basketball?groups=50"),
    "ncaaw": (4, 10, "Q", "basketball/womens-college-basketball?groups=50"),
}
LIVE_EDGE = 0.06  # fair probability must beat the price by this much to call it a live play


def clock_minutes(clock) -> float:
    if clock is None:
        return 0.0
    m = re.match(r"^\s*(\d+):(\d+(?:\.\d+)?)\s*$", str(clock))
    if m:
        return int(m.group(1)) + float(m.group(2)) / 60
    try:
        return float(clock) / 60  # seconds
    except ValueError:
        return 0.0


def minutes_left(sport: str, period: int, clock, end_of_period: bool) -> float:
    n, length, _, _ = STRUCTURE[sport]
    if period > n:  # overtime
        return 0.0 if end_of_period else clock_minutes(clock)
    in_period = 0.0 if end_of_period else clock_minutes(clock)
    return max(0.0, (n - period) * length + in_period)


def period_name(sport: str, period: int) -> str:
    n, _, label, _ = STRUCTURE[sport]
    if period > n:
        return "OT" if period == n + 1 else f"{period - n}OT"
    if label == "H":
        return "1st half" if period == 1 else "2nd half"
    return f"Q{period}"


def _find_rec(preds: dict, sport: str, home: str, away: str) -> dict | None:
    best, score = None, 0.0
    for rec in preds.values():
        if rec["sport"] != sport:
            continue
        s = min(similarity(home, rec["home"]), similarity(away, rec["away"]))
        if s > score:
            best, score = rec, s
    return best if score >= 0.75 else None


def ingame_state(model, sport: str, summary: dict, game, rec: dict, ended_period: int) -> dict | None:
    """The quarter-by-quarter model's projection from the game's own stats, or None if unavailable."""
    from .ingame import plays as PL
    if model is None or ended_period not in getattr(model, "heads", {}):
        return None
    plays = (PL.espn_football if sport in ("nfl", "cfb") else PL.espn_basketball)(summary, str(game.id))
    if plays.empty:
        return None
    builder = PL.football_states if sport in ("nfl", "cfb") else PL.basketball_states
    st = builder(plays, (ended_period,))
    if st.empty:
        return None
    o = game.odds
    st["pre_spread"] = o.spread if o and o.spread is not None else -rec["model_margin"]
    st["pre_total"] = o.total if o and o.total is not None else rec["model_total"]
    st["h_score"], st["a_score"] = float(game.info.get("live_home")), float(game.info.get("live_away"))
    from .ingame.model import add_score_cols
    st = model.predict(add_score_cols(st))
    r = st.iloc[0]
    return {"p_home": float(r["p_home"]), "exp_total": float(r["exp_total"]), "exp_margin": float(r["exp_margin"]),
            "frame": st, "stats": r}


def stat_line(sport: str, r, home: str, away: str) -> str:
    """Short in-game stat comparison for the Telegram update."""
    if sport in ("nfl", "cfb"):
        return (f"Stats {away} / {home}: yds/play {r['a_ypp']:.1f} / {r['h_ypp']:.1f} · success "
                f"{r['a_success_rate']:.0%} / {r['h_success_rate']:.0%} · turnovers {r['a_turnover']:.0f} / {r['h_turnover']:.0f}")
    return (f"Stats {away} / {home}: eFG {r['a_efg']:.0%} / {r['h_efg']:.0%} · rebounds "
            f"{r['a_oreb'] + r['a_dreb']:.0f} / {r['h_oreb'] + r['h_dreb']:.0f} · turnovers {r['a_tov']:.0f} / {r['h_tov']:.0f}")


def update_text(sport: str, game, rec: dict, ended_period: int, final: bool, line_total: float | None,
                price_home: float | None, price_away: float | None, live_prices: bool, ingame: dict | None = None) -> str:
    hs, as_ = game.info.get("live_home"), game.info.get("live_away")
    if final:
        hs, as_ = game.home_score, game.away_score
    left = 0.0 if final else minutes_left(sport, ended_period, game.info.get("clock"), True)
    st = bridge.live_state(rec, sport, hs, as_, left)
    if ingame and not final:
        sd_t = st["sd_t"]
        st = {**st, "p_home": ingame["p_home"], "exp_total": ingame["exp_total"], "exp_margin": ingame["exp_margin"]}
        st["sd_t"] = sd_t
    p_home = (1.0 if hs > as_ else 0.0) if final else st["p_home"]
    fav, p_fav = (game.home, p_home) if p_home >= 0.5 else (game.away, 1 - p_home)
    head = (f"🏁 FINAL" if final else f"🔄 End of {period_name(sport, ended_period)}") + \
        f" — {game.away} {as_:.0f} – {hs:.0f} {game.home}"
    pre_fav = rec["home"] if rec["home_win"] >= 0.5 else rec["away"]
    pre_p = max(rec["home_win"], 1 - rec["home_win"])
    lines = [head]
    if final:
        called = (rec["home_win"] >= 0.5) == (hs > as_)
        lines.append(f"Marv pregame: {pre_fav} {pre_p:.0%} {'✅' if called else '❌'} · "
                     f"projected total {rec['model_total']:.1f}, actual {hs + as_:.0f}")
        return "\n".join(lines)
    lines.append(f"{left:.0f} min left · Marv live: {fav} win {p_fav:.0%} (fair {prob_to_american(p_fav):+.0f}) "
                 f"· pregame {pre_fav} {pre_p:.0%}")
    lines.append(f"Projected final total {st['exp_total']:.1f} (pregame {rec['model_total']:.1f})")
    if ingame:
        lines.append(stat_line(sport, ingame["stats"], game.home, game.away) + " · model uses the game's stats")
    if line_total is not None:
        po = bridge.p_over(st, line_total)
        if ingame:
            po = float(ingame["model"].p_over(ingame["frame"], [line_total])[0])
        side, p = ("Over", po) if po >= 0.5 else ("Under", 1 - po)
        edge = p - american_to_prob(-110)
        if live_prices:
            tag = "▶️ live play" if edge >= LIVE_EDGE else "lean"
            lines.append(f"Total {line_total:g} (live line): {side} {p:.0%} · edge {edge:+.0%} · {tag}")
        else:
            lines.append(f"Total {line_total:g} (pregame line, reference only): {side} {p:.0%}")
    price = price_home if p_home >= 0.5 else price_away
    if price:
        edge = p_fav - american_to_prob(price)
        if live_prices:
            tag = "▶️ live play" if edge >= LIVE_EDGE else "lean"
            lines.append(f"Moneyline {fav} {price:+.0f} (live line): edge {edge:+.0%} · {tag}")
        else:
            lines.append("No live odds feed (set ODDS_API_KEY): compare Marv's fair price with your book.")
    return "\n".join(lines)


class LiveMonitor:
    def __init__(self, settings, sports: list[str]):
        self.s = settings
        self.sports = [k for k in sports if k in STRUCTURE]
        self.state_path = Path(settings.state_dir) / "live_state.json"
        self.client = ESPNClient()
        self.models, self._models_at = {}, 0.0
        self._refresh_models()

    def _refresh_models(self) -> None:
        """(Re)load the quarter-by-quarter models; the daily run retrains them weekly."""
        from .ingame.model import InGameModel, model_path
        if time.time() - self._models_at < 3600:
            return
        self.models = {k: InGameModel.load(model_path(Path(self.s.state_dir), k)) for k in self.sports}
        self._models_at = time.time()

    def _load(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        data = {k: v for k, v in data.items() if v.get("seen", "") >= cutoff}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(data))

    def _live_odds(self, sport_key: str, games) -> bool:
        if not self.s.odds_api_key:
            return False
        from .data import oddsapi
        from .sports import SPORTS
        events = []
        for key in SPORTS[sport_key].odds_api_keys:
            try:
                events += oddsapi.fetch(self.s.odds_api_key, key)
            except Exception as exc:
                log.warning("live odds %s: %s", key, exc)
        return oddsapi.attach(games, events, book=self.s.odds_book) > 0

    def tick(self, send) -> int:
        """One pass over all live games; returns the number of updates sent."""
        self._refresh_models()
        preds_path = Path(self.s.state_dir) / "predictions.json"
        preds = json.loads(preds_path.read_text()) if preds_path.exists() else {}
        state = self._load()
        now = datetime.now(timezone.utc)
        sent = 0
        for sport in self.sports:
            path = STRUCTURE[sport][3]
            try:
                events = self.client.scoreboard(path, now - timedelta(hours=12), now)
            except Exception as exc:
                log.warning("live scoreboard %s: %s", sport, exc)
                continue
            from .data.espn import parse_event
            games = [g for g in (parse_event(e, sport) for e in events) if g]
            active = [g for g in games if g.info.get("state") in ("in", "post")]
            due = []
            for g in active:
                key = f"{sport}:{g.id}"
                rec_state = state.setdefault(key, {"reported": 0, "final": False})
                rec_state["seen"] = now.isoformat()
                if g.info.get("state") == "post" or g.completed:
                    if rec_state["reported"] > 0 and not rec_state["final"]:
                        due.append((g, rec_state["reported"], True))
                    continue
                period = int(g.info.get("period") or 0)
                status = str(g.info.get("status_name") or "")
                ended = period if status in ("STATUS_END_PERIOD", "STATUS_HALFTIME") else period - 1
                if ended > rec_state["reported"] and g.info.get("live_home") is not None:
                    due.append((g, ended, False))
            if not due:
                continue
            live_prices = self._live_odds(sport, [g for g, _, _ in due])
            for g, ended, final in due:
                rec = _find_rec(preds, sport, g.home, g.away)
                if rec is None:
                    continue  # Marv had no pregame projection for this game
                o = g.odds
                ingame = None
                if not final and self.models.get(sport) is not None:
                    try:
                        summary = self.client.summary(STRUCTURE[sport][3], str(g.id))
                        ingame = ingame_state(self.models[sport], sport, summary, g, rec, ended)
                        if ingame:
                            ingame["model"] = self.models[sport]
                    except Exception as exc:
                        log.warning("in-game stats %s %s: %s", sport, g.id, exc)
                text = update_text(sport, g, rec, ended, final, o.total if o else None,
                                   o.home_ml if o else None, o.away_ml if o else None, live_prices, ingame)
                send(text)
                sent += 1
                key = f"{sport}:{g.id}"
                if final:
                    state[key]["final"] = True
                else:
                    state[key]["reported"] = ended
        self._save(state)
        return sent

    def run(self, send, interval: int = 120) -> None:
        log.info("live monitor watching %s every %ds", ", ".join(self.sports), interval)
        while True:
            try:
                self.tick(send)
            except Exception:
                log.exception("live monitor tick failed")
            time.sleep(interval)
