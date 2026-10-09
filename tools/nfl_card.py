"""Marv Predict NFL H2H card (ratings-only, paper, no Telegram) -> state/marv_predict/h2h_nfl.json for the
March_edge H2H overlay and Marv's queries (marv.bridge.overlay / game_h2h).

Same rules as the walk-forward backtest (/opt/marv-bot/state/marv_predict/proven-logic.md, NFL section):
  ratings margin/total from opponent-adjusted off/def ratings (marv.stats.nfl params, games before now);
  category points: 1 better offense rating, 1 better defense rating;
  pick: 2-0 sweep with |margin| >= 3 -> that side; otherwise (split or close game) star H2H tally
        (QB pass yds/g, RB1 rush yds/g, WR1 rec yds/g, K FG%), star tie -> margin side;
  close game (|margin| < 3): the H2H pick is printed WITHOUT a % (a 50/50 game has no honest edge %);
  O/U: rating total side + trend fade tag (both teams over 2+ of last 3 -> UNDER; both 0-1 -> OVER).
No probability is written here; marv/proven.py decides what may print a %.
Usage: python tools/nfl_card.py [YYYY-MM-DD] [OUTDIR]
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/opt/marv-bot")
from marv.data.nflverse import NFL_TEAMS  # noqa: E402
from marv.models import Game  # noqa: E402
from marv.ratings import fit_ratings  # noqa: E402
from marv.stats.nfl import NFL  # noqa: E402

ROOT = Path("/opt/marv-bot/state")
CACHE = ROOT / "cache"
H2H_OUT = ROOT / "marv_predict" / "h2h_nfl.json"
CLOSE = 3.0
TOL = {"QB": 5, "RB": 3, "WR": 3, "K": 0.02}


def stars(pw, season, week, abbr):
    d = pw[(pw.season == season) & (pw.team == abbr) & (pw.week < week)]
    if d.week.nunique() < 2:
        d = pw[(pw.season == season - 1) & (pw.team == abbr)]
    if d.empty:
        return {}
    ng = max(d.week.nunique(), 1)
    out = {}
    for k, col, pos in (("QB", "passing_yards", ("QB",)), ("RB", "rushing_yards", ("RB", "FB")), ("WR", "receiving_yards", ("WR",))):
        s = d[d.position.isin(pos)].groupby("player_display_name")[col].sum()
        if len(s):
            out[k] = (s.idxmax(), float(s.max() / ng))
    kk = d.groupby("player_display_name")[["fg_made", "fg_att"]].sum()
    kk = kk[kk.fg_att >= 5]
    if len(kk):
        n = kk.fg_att.idxmax()
        out["K"] = (n, float(kk.loc[n, "fg_made"] / kk.loc[n, "fg_att"]))
    return out


def main(day=None, outdir=None):
    now = pd.Timestamp(day, tz="UTC") if day else pd.Timestamp.now(tz="UTC")
    g = pd.read_csv(CACHE / "nflverse_games.csv")
    g["ko"] = pd.to_datetime(g.gameday + " " + g.gametime.fillna("13:00")).dt.tz_localize("America/New_York").dt.tz_convert("UTC")
    g["home"] = g.home_team.map(lambda a: NFL_TEAMS.get(a, a))
    g["away"] = g.away_team.map(lambda a: NFL_TEAMS.get(a, a))
    done = g[g.home_score.notna() & (g.ko < now) & (g.ko >= now - timedelta(days=600))]
    games = [Game(id=r.game_id, sport="nfl", start=r.ko.to_pydatetime(), home=r.home, away=r.away,
                  neutral=r.location == "Neutral", completed=True, home_score=r.home_score, away_score=r.away_score)
             for r in done.itertuples()]
    rt = fit_ratings(games, NFL.rating_params, as_of=now.to_pydatetime())
    up = g[g.home_score.isna() & (g.ko > now - timedelta(hours=4)) & (g.ko < now + timedelta(days=8))]
    pw = pd.concat([pd.read_csv(CACHE / f"nfl_players_week_{y}.csv", low_memory=False)
                    for y in (now.year - 1, now.year) if (CACHE / f"nfl_players_week_{y}.csv").exists()], ignore_index=True)
    pw = pw[pw.season_type.isin(["REG", "POST"])]
    for c in ("passing_yards", "rushing_yards", "receiving_yards", "fg_made", "fg_att"):
        pw[c] = pd.to_numeric(pw[c], errors="coerce").fillna(0)
    a = g[g.home_score.notna() & g.total_line.notna()]
    tr = pd.concat([pd.DataFrame({"ko": a.ko, "team": a[s], "over": a.home_score + a.away_score > a.total_line}) for s in ("home", "away")])
    last3 = tr.sort_values("ko").groupby("team").tail(3).groupby("team").over.agg(["sum", "count"])
    lines = json.loads((ROOT / "lines.json").read_text()) if (ROOT / "lines.json").exists() else {}
    recs, md = [], [f"# Marv Predict NFL H2H card (ratings-only, PAPER) built {now:%Y-%m-%d %H:%M} UTC", ""]
    for r in up.sort_values("ko").itertuples():
        if rt.games_played.get(r.home, 0) < 4 or rt.games_played.get(r.away, 0) < 4:
            continue
        hs, as_ = rt.expected(r.home, r.away, r.location == "Neutral")
        margin, total = hs - as_, hs + as_
        off = np.sign(rt.offense[r.home] - rt.offense[r.away])
        dfn = np.sign(rt.defense[r.away] - rt.defense[r.home])
        ch, ca = int(off > 0) + int(dfn > 0), int(off < 0) + int(dfn < 0)
        sh, sa = stars(pw, r.season, r.week, r.home_team), stars(pw, r.season, r.week, r.away_team)
        th = ta = 0
        parts = []
        for k in ("QB", "RB", "WR", "K"):
            if k in sh and k in sa:
                x, y = sh[k][1], sa[k][1]
                if abs(x - y) > TOL[k]:
                    th, ta = (th + 1, ta) if x > y else (th, ta + 1)
                fmt = (lambda v: f"{v:.0%}") if k == "K" else (lambda v: f"{v:.0f}/g")
                parts.append(f"{k} {sa[k][0]} {fmt(y)} v {sh[k][0]} {fmt(x)}")
        close = abs(margin) < CLOSE
        if (ch == 2 or ca == 2) and not close:
            pick, method = (r.home if ch == 2 else r.away), "category sweep 2-0"
        elif th != ta:
            pick, method = (r.home if th > ta else r.away), "star H2H tie-break"
        else:
            pick, method = (r.home if margin > 0 else r.away), "ratings margin"
        ln = lines.get(f"nfl:{r.game_id}", {})
        tline = ln.get("total_last", ln.get("total")) if ln else None
        tline = tline if tline is not None else (r.total_line if pd.notna(r.total_line) else None)
        fade = ""
        lh = last3.loc[r.home] if r.home in last3.index else None
        la = last3.loc[r.away] if r.away in last3.index else None
        if lh is not None and la is not None and lh["count"] == 3 and la["count"] == 3:
            fade = "UNDER" if lh["sum"] >= 2 and la["sum"] >= 2 else "OVER" if lh["sum"] <= 1 and la["sum"] <= 1 else ""
        rec = dict(game_id=r.game_id, start=r.ko.isoformat(), home=r.home, away=r.away, margin=round(margin, 1),
                   rating_total=round(total, 1), cat_home=ch, cat_away=ca, stars_home=th, stars_away=ta,
                   stars_detail="; ".join(parts), pick=pick, method=method, close=bool(close), fade=fade,
                   total_line=tline)
        recs.append(rec)
        md.append(f"**{r.away} @ {r.home}** {r.ko.tz_convert('America/Chicago'):%a %I:%M %p} CT")
        md.append(f"  H2H pick: {pick} ({'CLOSE game, no %' if close else 'unproven — no %'}) | {method} | cat {ch}-{ca} "
                  f"(home-away) | stars {th}-{ta} | margin {margin:+.1f} | rating total {total:.1f} vs {tline}"
                  + (f" | fade {fade}" if fade else ""))
        md.append(f"  {'; '.join(parts)}")
        md.append("")
    H2H_OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = H2H_OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"card_day": str(now.date()), "built": datetime.now(timezone.utc).isoformat(),
                               "games": recs}, indent=1))
    tmp.replace(H2H_OUT)
    if outdir:
        Path(outdir).mkdir(parents=True, exist_ok=True)
        (Path(outdir) / f"nfl_card_{now.date()}.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "-" else None, sys.argv[2] if len(sys.argv) > 2 else None)
