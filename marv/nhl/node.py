"""Marv Predict NHL node: daily tracking card. Never sends Telegram, never prints a win %.

  python -m marv.nhl.node STATE_DIR   -> refreshes history (NHL API), walks ratings forward, prices today's
  games (ESPN lines), writes STATE_DIR/marv_predict/nhl/card_<date>.json and appends reads to reads.json,
  grades finished reads. Feed rows (feed.json) are written ONLY if nhl_validation.json says passed=True.
"""
import json, sys, subprocess, urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
import numpy as np
from .model import Ratings, simulate, p_over, ml_prob, dec, DEFAULT

ESPN = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA"}


def _get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))


def run(state_dir):
    od = Path(state_dir) / "marv_predict" / "nhl"; od.mkdir(parents=True, exist_ok=True)
    hist = od / "hist"; hist.mkdir(exist_ok=True)
    subprocess.run([sys.executable, str(Path(__file__).with_name("data.py")), str(hist)], timeout=3000, check=False)
    val = json.loads((od / "nhl_validation.json").read_text()) if (od / "nhl_validation.json").exists() else {}
    p = dict(DEFAULT, **(val.get("params") or {})); thr = val.get("thresholds") or {"ml": 0.05, "tot": 0.5, "pl": 0.03}
    G = sorted(json.load(open(hist / "games.json")).values(), key=lambda g: (g["start"] or g["date"], g["id"]))
    R = Ratings(p)
    for g in G:
        y, m = int(g["date"][:4]), int(g["date"][5:7]); R.new_season(y if m >= 8 else y - 1); R.update(g)
    today = date.today(); R.new_season(today.year if today.month >= 8 else today.year - 1)
    sb = _get(f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={today:%Y%m%d}")
    card = []
    for e in sb.get("events", []):
        c = e["competitions"][0]; t = {x["homeAway"]: x["team"] for x in c["competitors"]}
        h, a = ESPN.get(t["home"]["abbreviation"], t["home"]["abbreviation"]), ESPN.get(t["away"]["abbreviation"], t["away"]["abbreviation"])
        g = {"date": str(today), "home": h, "away": a, "hgoalie": None, "agoalie": None}  # starter unknown -> team blend
        f = R.features(g); m = simulate(f["lh"], f["la"], p, 5000)
        ph = float(np.clip(m["home"] + f["tf"], .02, .98))
        o = (c.get("odds") or [{}])[0]; reads = []
        hml = (o.get("homeTeamOdds") or {}).get("moneyLine"); aml = (o.get("awayTeamOdds") or {}).get("moneyLine")
        if f["uf"] and hml and aml:
            mh = ml_prob(hml) / (ml_prob(hml) + ml_prob(aml))
            for side, pp, mp, am in ((t["home"]["displayName"], ph, mh, hml), (t["away"]["displayName"], 1 - ph, 1 - mh, aml)):
                if pp - mp >= thr["ml"] and pp * dec(am) > 1: reads.append({"market": "ml", "pick": side, "price": am})
        L = o.get("overUnder"); mu = float(m["totals"].mean())
        if f["uf"] and L and abs(mu - float(L)) >= thr["tot"]:
            reads.append({"market": "total", "pick": f"{'Over' if mu > L else 'Under'} {L}"})
        card.append({"game_id": e["id"], "start": e["date"], "home": t["home"]["displayName"], "away": t["away"]["displayName"],
                     "proj_total": round(mu, 2), "uf": f["uf"], "b2b": [bool(f["hb"]), bool(f["ab"])], "reads": reads,
                     "status": "tracking — unproven, no %", "_p_home_internal": round(ph, 4)})
    (od / f"card_{today}.json").write_text(json.dumps(card, indent=1, default=float))
    passed = bool(val.get("passed"))
    feed = []
    if passed:
        for x in card:
            for r in x["reads"]:
                if r["market"] == "ml":
                    feed.append({"sport": "nhl", "market": "h2h", "home": x["home"], "away": x["away"], "start": x["start"],
                                 "game": x["game_id"], "pick": r["pick"], "src": "marv_nhl", "stake": 1.0,
                                 "p": x["_p_home_internal"] if r["pick"] == x["home"] else 1 - x["_p_home_internal"]})
    (od / "feed.json").write_text(json.dumps({"passed": passed, "updated": datetime.now(timezone.utc).isoformat(), "rows": feed}))
    return len(card), passed


if __name__ == "__main__":
    print(run(sys.argv[1]))
