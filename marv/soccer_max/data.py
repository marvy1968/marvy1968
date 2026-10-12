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

# March_edge / ESPN league key -> (football-data code, label, file kind)
LEAGUES = {
    "eng.1": ("E0", "Premier League", "main"), "eng.2": ("E1", "Championship", "main"),
    "esp.1": ("SP1", "La Liga", "main"), "ger.1": ("D1", "Bundesliga", "main"), "ita.1": ("I1", "Serie A", "main"),
    "fra.1": ("F1", "Ligue 1", "main"), "ned.1": ("N1", "Eredivisie", "main"), "por.1": ("P1", "Primeira Liga", "main"),
    "usa.1": ("USA", "MLS", "new"), "mex.1": ("MEX", "Liga MX", "new"),
}
ODDS_API_KEYS = {
    "soccer_epl": "eng.1", "soccer_efl_champ": "eng.2", "soccer_spain_la_liga": "esp.1",
    "soccer_germany_bundesliga": "ger.1", "soccer_italy_serie_a": "ita.1", "soccer_france_ligue_one": "fra.1",
    "soccer_netherlands_eredivisie": "ned.1", "soccer_portugal_primeira_liga": "por.1", "soccer_usa_mls": "usa.1",
    "soccer_mexico_ligamx": "mex.1",
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


def load_league(key: str, cache_dir: Path, now: datetime | None = None, refresh_s: float = 6 * 3600,
                first: int = FIRST_SEASON) -> pd.DataFrame:
    """Every finished (and the current season's) match of one league, oldest first."""
    code, _, kind = LEAGUES[key]
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
