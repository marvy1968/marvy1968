"""Point-in-time soccer data from football-data.co.uk (results, shots, shots on target, corners, xG when published,
and pre-match / closing odds incl. Bovada, Pinnacle, Betfair exchange and the market average).

Main leagues: one CSV per league-season (mmz4281/<yyyy>/<code>.csv). MLS / Liga MX: one CSV per league (new/*.csv,
goals + closing odds only, no shots). Upcoming fixtures with pre-match odds: fixtures.csv. Finished seasons are
cached for good; the current season and fixtures are refreshed when older than a few hours.
"""

import io
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

BASE = "https://www.football-data.co.uk"
FIRST_SEASON = 2018
UA = {"User-Agent": "Mozilla/5.0 (marv-predict-bot)"}
ESPN_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# March_edge / ESPN league key -> (football-data code, label, file kind)
LEAGUES = {
    "eng.1": ("E0", "Premier League", "main"), "eng.2": ("E1", "Championship", "main"),
    "esp.1": ("SP1", "La Liga", "main"), "ger.1": ("D1", "Bundesliga", "main"), "ita.1": ("I1", "Serie A", "main"),
    "fra.1": ("F1", "Ligue 1", "main"), "ned.1": ("N1", "Eredivisie", "main"), "por.1": ("P1", "Primeira Liga", "main"),
    "usa.1": ("USA", "MLS", "new"), "mex.1": ("MEX", "Liga MX", "new"),
    # Oct 16 2026 (Marvin): ESPN scoreboard/schedule history (football-data has no NWSL/USL Super; ARG often 503)
    # kind "espn" -> load_espn_league (goals only, low-confidence; no shots/xG)
    "arg.1": ("ARG", "Liga Profesional Argentina", "espn"),
    "usa.nwsl": ("NWSL", "NWSL", "espn"),
    "usa.w.usl.1": ("USLS", "USL Super League", "espn"),
}
ODDS_API_KEYS = {
    "soccer_epl": "eng.1", "soccer_efl_champ": "eng.2", "soccer_spain_la_liga": "esp.1",
    "soccer_germany_bundesliga": "ger.1", "soccer_italy_serie_a": "ita.1", "soccer_france_ligue_one": "fra.1",
    "soccer_netherlands_eredivisie": "ned.1", "soccer_portugal_primeira_liga": "por.1", "soccer_usa_mls": "usa.1",
    "soccer_mexico_ligamx": "mex.1", "soccer_argentina_primera_division": "arg.1",
}
CODE_TO_KEY = {v[0]: k for k, v in LEAGUES.items()}


def league_key(name: str | None) -> str | None:
    """'eng.1', 'soccer_epl', 'E0' or 'Premier League' -> 'eng.1' (None when not a supported soccer league)."""
    n = (name or "").strip()
    if n.lower() in LEAGUES:
        return n.lower()
    if n.lower() in ODDS_API_KEYS:
        return ODDS_API_KEYS[n.lower()]
    if n.upper() in CODE_TO_KEY:
        return CODE_TO_KEY[n.upper()]
    for k, v in LEAGUES.items():
        if n.lower() == v[1].lower():
            return k
    aliases = {
        "argentina": "arg.1", "primera división": "arg.1", "liga profesional": "arg.1",
        "nwsl": "usa.nwsl", "usl super league": "usa.w.usl.1", "liga mx": "mex.1",
    }
    if n.lower() in aliases:
        return aliases[n.lower()]
    return None


def current_season(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def season_code(y: int) -> str:
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def _get(url: str, path: Path, max_age_s: float | None) -> str | None:
    """Download url to path (cached). max_age_s None = cache forever."""
    if path.exists() and (max_age_s is None or time.time() - path.stat().st_mtime < max_age_s):
        return path.read_bytes().decode("latin-1")
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        body = r.content
        if b"," not in body[:200]:
            raise ValueError("not a CSV")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return body.decode("latin-1")
    except Exception as exc:  # noqa: BLE001 - fall back to a stale copy
        log.warning("football-data %s failed: %s", url, exc)
        return path.read_bytes().decode("latin-1") if path.exists() else None


def _num(df: pd.DataFrame, *cols: str) -> pd.Series:
    """First available column (row-wise first non-null across the candidates) as float."""
    out = pd.Series(np.nan, index=df.index)
    for c in cols:
        if c in df:
            out = out.fillna(pd.to_numeric(df[c], errors="coerce"))
    return out


def _normalize(df: pd.DataFrame, key: str, season: int | None) -> pd.DataFrame:
    df.columns = [str(c).replace("\ufeff", "").replace("ï»¿", "").strip() for c in df.columns]
    home = df["HomeTeam"] if "HomeTeam" in df else df.get("Home")
    away = df["AwayTeam"] if "AwayTeam" in df else df.get("Away")
    if home is None or away is None:
        return pd.DataFrame()
    out = pd.DataFrame({
        "league": key,
        "date": pd.to_datetime(df["Date"], dayfirst=True, errors="coerce"),
        "home": home.astype(str).str.strip(), "away": away.astype(str).str.strip(),
        "hg": _num(df, "FTHG", "HG"), "ag": _num(df, "FTAG", "AG"),
        "hs": _num(df, "HS"), "as_": _num(df, "AS"), "hst": _num(df, "HST"), "ast": _num(df, "AST"),
        "hc": _num(df, "HC"), "ac": _num(df, "AC"), "hxg": _num(df, "HxG"), "axg": _num(df, "AxG"),
        # pre-match prices (what a paper alert would have been offered): Bovada, else market average, else bet365
        "o_h": _num(df, "BVH", "AvgH", "BbAvH", "B365H"), "o_d": _num(df, "BVD", "AvgD", "BbAvD", "B365D"),
        "o_a": _num(df, "BVA", "AvgA", "BbAvA", "B365A"),
        "o_over": _num(df, "BV>2.5", "Avg>2.5", "BbAv>2.5", "B365>2.5"),
        "o_under": _num(df, "BV<2.5", "Avg<2.5", "BbAv<2.5", "B365<2.5"),
        # closing reference for calibration / CLV: Pinnacle, else Betfair exchange, else market average
        "c_h": _num(df, "PSCH", "BFECH", "AvgCH"), "c_d": _num(df, "PSCD", "BFECD", "AvgCD"),
        "c_a": _num(df, "PSCA", "BFECA", "AvgCA"),
        "c_over": _num(df, "PC>2.5", "BFEC>2.5", "AvgC>2.5"), "c_under": _num(df, "PC<2.5", "BFEC<2.5", "AvgC<2.5"),
    })
    if season is None:
        s = df["Season"].astype(str).str[:4]
        out["season"] = pd.to_numeric(s, errors="coerce")
    else:
        out["season"] = season
    out = out[out["date"].notna() & out["home"].ne("nan") & out["away"].ne("nan")]
    return out



def load_espn_league(key: str, cache_dir: Path, now: datetime | None = None,
                     refresh_s: float = 12 * 3600) -> pd.DataFrame:
    """Free ESPN team schedules -> goals-only history (no shots/xG). Used for leagues without football-data.
    Cached under cache_dir/espn_{key}.csv. Low-confidence source for Marv Soccer Predict paper tracking.
    """
    now = now or datetime.now(timezone.utc)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"espn_{key.replace('.', '_')}.csv"
    if path.exists() and time.time() - path.stat().st_mtime < refresh_s:
        try:
            df = pd.read_csv(path)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_convert(None)
                return df
        except Exception as exc:  # noqa: BLE001
            log.warning("espn cache read %s: %s", key, exc)
    teams_url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{key}/teams"
    try:
        tr = requests.get(teams_url, headers=ESPN_UA, timeout=30)
        tr.raise_for_status()
        tjs = tr.json()
        teams = ((tjs.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("espn teams %s: %s", key, exc)
        return pd.read_csv(path) if path.exists() else pd.DataFrame()
    rows, seen = [], set()
    for tw in teams:
        team = tw.get("team") or {}
        tid = team.get("id")
        if not tid:
            continue
        try:
            sr = requests.get(
                f"https://site.api.espn.com/apis/site/v2/sports/soccer/{key}/teams/{tid}/schedule",
                headers=ESPN_UA, timeout=30)
            sr.raise_for_status()
            events = (sr.json() or {}).get("events") or []
        except Exception as exc:  # noqa: BLE001
            log.debug("espn schedule %s %s: %s", key, tid, exc)
            continue
        for e in events:
            eid = str(e.get("id") or "")
            if not eid or eid in seen:
                continue
            comp = (e.get("competitions") or [{}])[0]
            st = ((comp.get("status") or e.get("status") or {}).get("type") or {})
            if (st.get("state") or "") != "post" and not st.get("completed"):
                continue
            comps = {c.get("homeAway"): c for c in (comp.get("competitors") or [])}
            h, a = comps.get("home") or {}, comps.get("away") or {}
            ht = (h.get("team") or {}).get("displayName") or (h.get("team") or {}).get("name")
            at = (a.get("team") or {}).get("displayName") or (a.get("team") or {}).get("name")
            try:
                hg = float((h.get("score") or {}).get("value"))
                ag = float((a.get("score") or {}).get("value"))
            except (TypeError, ValueError):
                continue
            if not ht or not at:
                continue
            seen.add(eid)
            season = None
            try:
                season = int((e.get("season") or {}).get("year") or 0) or None
            except (TypeError, ValueError):
                season = None
            rows.append({
                "league": key, "date": e.get("date"), "home": str(ht).strip(), "away": str(at).strip(),
                "hg": hg, "ag": ag, "hs": None, "as_": None, "hst": None, "ast": None,
                "hc": None, "ac": None, "hxg": None, "axg": None,
                "o_h": None, "o_d": None, "o_a": None, "o_over": None, "o_under": None,
                "c_h": None, "c_d": None, "c_a": None, "c_over": None, "c_under": None,
                "season": season or (now.year if now.month >= 7 else now.year - 1),
                "played": True,
            })
    if not rows:
        log.warning("espn league %s: no finished matches", key)
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_convert(None)
    df = df[df["date"].notna()].sort_values(["date", "home"]).reset_index(drop=True)
    df.to_csv(path, index=False)
    log.info("espn league %s: %d finished matches (%d teams)", key, len(df), len(teams))
    return df


def load_league(key: str, cache_dir: Path, now: datetime | None = None, refresh_s: float = 6 * 3600,
                first: int = FIRST_SEASON) -> pd.DataFrame:
    """Every finished (and the current season's) match of one league, oldest first."""
    code, _, kind = LEAGUES[key]
    if kind == "espn":
        return load_espn_league(key, cache_dir, now, refresh_s=max(refresh_s, 12 * 3600))
    cur = current_season(now)
    frames = []
    if kind == "main":
        for y in range(first, cur + 1):
            txt = _get(f"{BASE}/mmz4281/{season_code(y)}/{code}.csv", cache_dir / f"{code}_{season_code(y)}.csv",
                       refresh_s if y >= cur - 0 else None)
            if txt:
                try:
                    frames.append(_normalize(pd.read_csv(io.StringIO(txt), on_bad_lines="skip"), key, y))
                except Exception as exc:  # noqa: BLE001
                    log.warning("parse %s %s: %s", code, y, exc)
    else:
        txt = _get(f"{BASE}/new/{code}.csv", cache_dir / f"{code}.csv", refresh_s)
        if txt:
            d = _normalize(pd.read_csv(io.StringIO(txt), on_bad_lines="skip"), key, None)
            frames.append(d[d["season"] >= first])
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["date", "home"]).reset_index(drop=True)
    df["played"] = df["hg"].notna() & df["ag"].notna()
    return df


def load_fixtures(cache_dir: Path, refresh_s: float = 3 * 3600) -> pd.DataFrame:
    """Upcoming main-league fixtures with pre-match odds (football-data fixtures.csv)."""
    txt = _get(f"{BASE}/fixtures.csv", cache_dir / "fixtures.csv", refresh_s)
    if not txt:
        return pd.DataFrame()
    raw = pd.read_csv(io.StringIO(txt), on_bad_lines="skip")
    raw.columns = [str(c).replace("\ufeff", "").replace("ï»¿", "").strip() for c in raw.columns]
    frames = []
    for code, grp in raw.groupby("Div"):
        key = CODE_TO_KEY.get(str(code))
        if key:
            d = _normalize(grp.copy(), key, None if "Season" in grp else current_season())
            d["time"] = grp["Time"].astype(str).values if "Time" in grp else ""
            frames.append(d)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
