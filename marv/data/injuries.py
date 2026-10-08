"""Late-injury veto from several up-to-date injury sources.

Sources (each used only if it answers and is current):
  * ESPN injuries feed (NFL, NBA, WNBA, MLB)
  * NFL: official NFL injury report via nflverse, only when it covers the current week
  * NBA: the league's official injury report PDF (latest one from today)
  * MLB: MLB Stats API 40-man rosters (injured-list status codes)

Key players come from recent box scores:
  * NFL: the quarterback who threw the most passes in his team's latest game
  * NBA / WNBA: top-6 players by minutes over the last 15 games who played in one of the last 3
  * MLB: top-5 hitters by plate appearances over the team's last 10 games
Long-term absences are already in the team's recent stats, so only fresh absences count.

If no source answers for a sport, every game gets vetoed: betting blind on injuries isn't allowed.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from ..data.teams import NFL_TEAMS, normalize
from ..stats.base import fetch

log = logging.getLogger(__name__)
ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/{path}/injuries"
NFL_PLAYERS = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
NFL_INJURIES = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv"
BOX_PLAYERS = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
               "espn_{league}_player_boxscores/player_box_{season}.parquet")
MLB_API = "https://statsapi.mlb.com/api/v1"
RISKY = {"out", "doubtful", "questionable", "day-to-day", "game time decision", "gtd", "injured list",
         "10-day-il", "15-day-il", "60-day-il", "7-day-il", "suspension"}
PATHS = {"nfl": "football/nfl", "nba": "basketball/nba", "wnba": "basketball/wnba", "mlb": "baseball/mlb"}
UA = {"User-Agent": "Mozilla/5.0 (marv-predict-bot)"}

Report = dict[str, list[tuple[str, str, str]]]  # team -> [(player, status, source)]


def _add(reports: Report, team: str, player: str, status: str, source: str) -> None:
    reports.setdefault(normalize(team), []).append((player, status.lower().strip(), source))


# ---------- sources ----------

def espn_injuries(sport: str, reports: Report) -> bool:
    try:
        resp = requests.get(ESPN_INJURIES.format(path=PATHS[sport]), timeout=30, headers=UA)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("ESPN injuries unavailable for %s: %s", sport, exc)
        return False
    for team in data.get("injuries", []):
        name = team.get("displayName") or (team.get("team") or {}).get("displayName", "")
        for inj in team.get("injuries", []):
            athlete = inj.get("athlete") or {}
            _add(reports, name, athlete.get("displayName", ""), str(inj.get("status", "")), "ESPN")
    return bool(data.get("injuries"))


def nfl_official(cache: Path, season: int, week: int | None, reports: Report) -> bool:
    path = fetch(NFL_INJURIES.format(season=season), cache / f"nfl_injuries_{season}.csv", max_age_hours=2)
    if not path or week is None:
        return False
    df = pd.read_csv(path, low_memory=False)
    cur = df[(df["week"] == week) & df["report_status"].notna()]
    if cur.empty:
        log.info("NFL official injury report for week %s not published in nflverse yet", week)
        return False
    for r in cur.itertuples():
        _add(reports, NFL_TEAMS.get(r.team, r.team), r.full_name, str(r.report_status), "NFL report")
    return True


def nba_official(now: datetime, teams: list[str], reports: Report) -> bool:
    from .nba_injury_pdf import latest_report
    found = latest_report(now, teams)
    for team, rows in found.items():
        for player, status in rows:
            reports.setdefault(team, []).append((player, status, "NBA report"))
    return bool(found)


def mlb_rosters(reports: Report) -> bool:
    try:
        s = requests.Session()
        teams = s.get(f"{MLB_API}/teams", params={"sportId": 1}, timeout=30).json().get("teams", [])
        for t in teams:
            roster = s.get(f"{MLB_API}/teams/{t['id']}/roster", params={"rosterType": "40Man"}, timeout=30).json()
            for p in roster.get("roster", []):
                code = (p.get("status") or {}).get("code", "A")
                if code.startswith("D"):  # D7 / D10 / D15 / D60 = injured list
                    _add(reports, t["name"], p["person"]["fullName"], "injured list", "MLB rosters")
        return bool(teams)
    except Exception as exc:
        log.warning("MLB roster status unavailable: %s", exc)
        return False


# ---------- key players ----------

def nfl_starting_qbs(cache: Path, season: int) -> dict[str, set[str]]:
    path = fetch(NFL_PLAYERS.format(season=season), cache / f"nfl_players_week_{season}.csv", max_age_hours=2)
    if not path:
        return {}
    df = pd.read_csv(path, low_memory=False, usecols=["player_display_name", "position", "team", "week", "attempts"])
    qbs = df[(df["position"] == "QB") & (df["attempts"].fillna(0) > 0)]
    latest = qbs.sort_values(["team", "week", "attempts"]).groupby("team").tail(1)
    return {normalize(NFL_TEAMS.get(t, t)): {n} for t, n in zip(latest["team"], latest["player_display_name"])}


def nfl_qb_starts(cache: Path, season: int) -> dict[str, tuple[str, int]]:
    """{team: (latest starting QB, his starts over this and last season, any team)}. A start is a game
    where he threw the most passes for his team; a veteran on a new team keeps his starts."""
    frames = []
    for yr, age in ((season, 2), (season - 1, None)):
        path = fetch(NFL_PLAYERS.format(season=yr), cache / f"nfl_players_week_{yr}.csv", max_age_hours=age)
        if path:
            df = pd.read_csv(path, low_memory=False, usecols=["player_display_name", "position", "team", "week", "attempts"])
            frames.append(df.assign(season=yr))
    if not frames:
        return {}
    df = pd.concat(frames)
    qbs = df[(df["position"] == "QB") & (df["attempts"].fillna(0) > 0)]
    starters = qbs.sort_values("attempts").groupby(["season", "week", "team"]).tail(1)
    starts = starters["player_display_name"].value_counts()
    out = {}
    for team, d in starters.sort_values(["season", "week"]).groupby("team"):
        qb = d["player_display_name"].iloc[-1]
        out[normalize(NFL_TEAMS.get(team, team))] = (qb, int(starts.get(qb, 0)))
    return out


def basketball_key_players(cache: Path, league: str, season: int) -> dict[str, set[str]]:
    path = fetch(BOX_PLAYERS.format(league=league, season=season), cache / f"{league}_player_box_{season}.parquet",
                 max_age_hours=2)
    if not path:
        return {}
    df = pd.read_parquet(path, columns=["game_date", "athlete_display_name", "team_display_name", "minutes"])
    df["minutes"] = pd.to_numeric(df["minutes"], errors="coerce").fillna(0)
    out = {}
    for team, d in df.groupby("team_display_name"):
        dates = sorted(d["game_date"].unique())
        mins = d[d["game_date"].isin(set(dates[-15:]))].groupby("athlete_display_name")["minutes"].mean().nlargest(6)
        active = set(d[d["game_date"].isin(set(dates[-3:])) & (d["minutes"] > 0)]["athlete_display_name"])
        out[normalize(team)] = {p for p in mins.index if p in active}
    return out


def mlb_key_hitters(cache: Path) -> dict[str, set[str]]:
    """From cached MLB Stats API box scores (written by marv.data.mlbstats)."""
    files = sorted((cache / "mlbstats").glob("box_*.json"), key=lambda p: int(p.stem.split("_")[1]))[-400:]
    games: dict[str, list[dict[str, int]]] = {}
    for f in files:
        box = json.loads(f.read_text())
        for side in ("home", "away"):
            t = box["teams"][side]
            pa = {p["person"]["fullName"]: int(p.get("stats", {}).get("batting", {}).get("plateAppearances", 0) or 0)
                  for p in t.get("players", {}).values()}
            games.setdefault(normalize(t["team"]["name"]), []).append(pa)
    out = {}
    for team, logs in games.items():
        recent = logs[-10:]
        total: dict[str, int] = {}
        for g in recent:
            for name, n in g.items():
                total[name] = total.get(name, 0) + n
        played_recently = {n for g in recent[-3:] for n, pa in g.items() if pa > 0}
        top = sorted(total, key=total.get, reverse=True)[:5]
        out[team] = {n for n in top if n in played_recently}
    return out


# ---------- veto ----------

def injury_vetoes(sport: str, cache: Path, season: int, teams: list[str], now: datetime | None = None,
                  week: int | None = None) -> dict[str, list[str]]:
    """{team: [veto reasons]}. Every team is vetoed when no injury source is available."""
    if sport not in PATHS:
        return {}
    now = now or datetime.now()
    reports: Report = {}
    sources = []
    if espn_injuries(sport, reports):
        sources.append("ESPN")
    if sport == "nfl" and nfl_official(cache, season, week, reports):
        sources.append("NFL report")
    if sport == "nba" and nba_official(now, teams, reports):
        sources.append("NBA report")
    if sport == "mlb" and mlb_rosters(reports):
        sources.append("MLB rosters")
    if not sources:
        return {team: ["no current injury data (all injury sources failed)"] for team in teams}
    log.info("%s injury sources: %s", sport, ", ".join(sources))

    if sport == "nfl":
        key = nfl_starting_qbs(cache, season)
    elif sport == "mlb":
        key = mlb_key_hitters(cache)
    else:
        key = basketball_key_players(cache, sport, season)
    out = {}
    for team in teams:
        players = {normalize(p) for p in key.get(normalize(team), set())}
        hits = {}
        for name, status, source in reports.get(normalize(team), []):
            if normalize(name) in players and any(r in status for r in RISKY):
                hits.setdefault(name, f"{name} ({status}, {source})")
        if hits:
            out[team] = [f"key injury: {'; '.join(hits.values())}"]
    return out


def report_text(sport: str, cache: Path, season: int, teams: list[str] | None = None, week: int | None = None,
                now: datetime | None = None) -> str:
    """Readable injury / availability report: every listed player with status and source, key players
    (the ones the veto watches) starred, and for the NFL the regular starters ruled Out/Doubtful."""
    if sport not in PATHS:
        return f"No injury feed for {sport} (college and EuroLeague have none): check team news before betting."
    reports: Report = {}
    sources = [name for name, ok in (("ESPN", espn_injuries(sport, reports)),
                                     ("NFL report", sport == "nfl" and nfl_official(cache, season, week, reports)),
                                     ("NBA report", sport == "nba" and nba_official(now or datetime.now(), teams or [], reports)))
               if ok]
    if not sources:
        return f"{sport.upper()}: every injury source failed right now (Marv vetoes all games when this happens)."
    key = nfl_starting_qbs(cache, season) if sport == "nfl" else basketball_key_players(cache, sport, season)
    want = {normalize(t) for t in teams} if teams else None
    lines = [f"{sport.upper()} injuries · sources: {', '.join(sources)} · ★ = key player Marv's veto watches"]
    for team in sorted(reports):
        if want and not any(w in team or team in w for w in want):
            continue
        keyset = {normalize(p) for p in key.get(team, set())}
        rows = sorted({(p, st, src) for p, st, src in reports[team] if st}, key=lambda r: (normalize(r[0]) not in keyset, r[0]))
        lines.append(f"\n{team.title()}:")
        for p, st, src in rows[:25]:
            star = "★ " if normalize(p) in keyset else "  "
            lines.append(f"  {star}{p}: {st} ({src})")
    if sport == "nfl" and week is not None:
        try:
            from .nfl_availability import availability
            av = availability(cache, [season], season)
            av = av[(av["season"] == season) & (av["week"] == week)]
            if want:
                av = av[av["team"].map(lambda t: any(w in normalize(NFL_TEAMS.get(t, t)) for w in want))]
            lines.append("\nRegular starters (50%+ snaps) ruled Out/Doubtful this week:")
            for r in av.sort_values("snaps_lost", ascending=False).itertuples():
                lines.append(f"  {NFL_TEAMS.get(r.team, r.team)}: {r.starters_out} starters out, {r.snaps_lost:.1f} full-time "
                             f"players' snaps lost{', QB OUT' if r.qb_out else ''}")
            lines.append("  (Marv's NFL veto: 1.5+ full-time starters out, or the starting QB.)")
        except Exception as exc:
            lines.append(f"\n(roster availability unavailable: {exc})")
    return "\n".join(lines)
