"""Small JSON state on disk: opening-line memory and the pick ledger (track record)."""

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .engine import grade
from .models import Game, Pick, Prediction


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _load(self, name: str, default):
        path = self.root / name
        try:
            return json.loads(path.read_text()) if path.exists() else default
        except json.JSONDecodeError:
            return default

    def _save(self, name: str, data) -> None:
        tmp = self.root / f"{name}.tmp"
        tmp.write_text(json.dumps(data, indent=1, default=str))
        tmp.replace(self.root / name)

    # Opening lines: sources without an "open" value get the first line we saw. Each run also stores the
    # latest line, so lines.json keeps open -> last-before-kickoff (≈ close) for a year of games.
    def remember_lines(self, sport: str, games: list[Game]) -> None:
        memory = self._load("lines.json", {})
        cutoff = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        memory = {k: v for k, v in memory.items() if v.get("start", "") >= cutoff}
        now = datetime.now(timezone.utc)
        for game in games:
            if not game.odds:
                continue
            key = f"{sport}:{game.id}"
            first = memory.setdefault(key, {"start": game.start.isoformat(),
                                             "spread": game.odds.spread, "total": game.odds.total})
            first.setdefault("home", game.home)
            first.setdefault("away", game.away)
            if game.odds.spread_open is not None:
                first["spread_open_src"] = game.odds.spread_open
            if game.odds.total_open is not None:
                first["total_open_src"] = game.odds.total_open
            if game.start > now:  # last line seen before kickoff
                first["spread_last"], first["total_last"] = game.odds.spread, game.odds.total
                first["seen_last"] = now.isoformat()
            if game.odds.spread_open is None:
                game.odds.spread_open = first.get("spread")
            if game.odds.total_open is None:
                game.odds.total_open = first.get("total")
        self._save("lines.json", memory)

    # Pick ledger.
    def new_picks(self, sport: str, preds: list[Prediction]) -> int:
        """Qualified picks (market + side) that aren't in the ledger yet, i.e. not sent before."""
        ledger = self._load("picks.json", [])
        have = {(p["sport"], p["game_id"], p["market"], p.get("side")) for p in ledger}
        return sum(1 for pred in preds for pick in pred.picks
                   if pick.active and (sport, pred.game.id, pick.market, pick.side) not in have)

    def log_picks(self, sport: str, preds: list[Prediction]) -> None:
        ledger = self._load("picks.json", [])
        index = {(p["sport"], p["game_id"], p["market"]): p for p in ledger}
        now = datetime.now(timezone.utc).isoformat()
        for pred in preds:
            for pick in pred.picks:
                if not pick.active:
                    continue
                key = (sport, pred.game.id, pick.market)
                entry = index.get(key)
                if entry and entry.get("result") is not None:
                    continue
                record = {"sport": sport, "game_id": pred.game.id, "start": pred.game.start.isoformat(),
                          "home": pred.game.home, "away": pred.game.away, **asdict(pick),
                          "logged_at": now, "result": None}
                record.pop("vetoes")
                if entry:
                    entry.update(record)  # keep the latest number before kickoff
                else:
                    ledger.append(record)
                    index[key] = record
        self._save("picks.json", ledger)

    def resolve(self, sport: str, finished: list[Game]) -> int:
        ledger = self._load("picks.json", [])
        by_id = {g.id: g for g in finished if g.completed}
        resolved = 0
        for entry in ledger:
            if entry["sport"] != sport or entry["result"] is not None or entry["game_id"] not in by_id:
                continue
            pick = Pick(entry["market"], entry["side"], entry["line"], entry["price"],
                        entry["prob"], entry["ev"], entry["edge"])
            entry["result"] = grade(pick, by_id[entry["game_id"]])
            resolved += 1
        if resolved:
            self._save("picks.json", ledger)
        return resolved

    def record(self, sport: str, days: int = 30) -> dict[str, tuple[int, int, int, float]]:
        """{market: (wins, losses, pushes, units)} over the last `days` days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        out: dict[str, list] = {}
        for e in self._load("picks.json", []):
            if e["sport"] != sport or e["result"] is None or e["start"] < cutoff:
                continue
            w, l, p, u = out.setdefault(e["market"], [0, 0, 0, 0.0])
            r = e["result"]
            out[e["market"]] = [w + (r > 0), l + (r < 0), p + (r == 0), u + r]
        return {k: tuple(v) for k, v in out.items()}
