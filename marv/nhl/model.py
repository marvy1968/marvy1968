"""Marv Predict NHL model: the Master blueprint adapted to hockey.

  EV = [P_MC(5k) x dec - 1] x TCI x PPE x UF x TF
  P_MC  5,000 Monte Carlo games: Poisson goals per team from ratings, regulation tie -> OT/SO, empty-net goal
        bump on 1-goal regulation finishes (pulled goalie), so puck line +-1.5 and totals are priced from the same sims.
  TCI   'territory' = shot-share rating (the hockey 'trench'): EW shots for / against, opponent-adjusted.
  PPE   finishing/efficiency = regressed shooting% x goalie: the CONFIRMED starting goalie's regressed save% replaces
        the team average when known (goalie confirmation). Unknown starter -> team blend.
  UF    binary filter: kill the bet if early season (<8 games for a team), unknown goalie on live card,
        or both teams on a back-to-back (fatigue chaos).
  TF    last-10 goal-diff momentum, clipped to +-2.5% win-prob.
  Situational: back-to-back penalty, travel (time-zone hops since last game), home ice.
Ratings are built strictly from games BEFORE the game being priced (walk-forward).
"""
import math
from collections import defaultdict, deque
import numpy as np

TZ = {"BOS": -5, "BUF": -5, "DET": -5, "FLA": -5, "MTL": -5, "OTT": -5, "TBL": -5, "TOR": -5, "CAR": -5, "CBJ": -5,
      "NJD": -5, "NYI": -5, "NYR": -5, "PHI": -5, "PIT": -5, "WSH": -5, "CHI": -6, "DAL": -6, "MIN": -6, "NSH": -6,
      "STL": -6, "WPG": -6, "COL": -7, "UTA": -7, "ARI": -7, "CGY": -7, "EDM": -7, "ANA": -8, "LAK": -8, "SJS": -8,
      "SEA": -8, "VAN": -8, "VGK": -8}

DEFAULT = dict(decay=0.985, lg_goals=3.05, home=0.045, sh_k=900.0, sv_k=1500.0, w_shot=0.55, b2b=0.06,
               tz=0.012, tf=0.025, en_p=0.42, ot_home=0.52)


class Ratings:
    """Walk-forward team + goalie state. update() after each game; features() before."""
    def __init__(self, p=DEFAULT):
        self.p = p
        self.sf = defaultdict(lambda: 30.0); self.sa = defaultdict(lambda: 30.0)
        self.gf = defaultdict(lambda: p["lg_goals"]); self.ga = defaultdict(lambda: p["lg_goals"])
        self.n = defaultdict(int); self.last = {}; self.lastz = {}
        self.form = defaultdict(lambda: deque(maxlen=10))
        self.gsv = defaultdict(lambda: [0.0, 0.0])  # goalie saves, shots
        self.tsv = defaultdict(lambda: [0.0, 0.0])  # team-level goalie saves, shots
        self.lg = [0.0, 0.0, 0.0]  # goals, shots, games
        self.season = None

    def new_season(self, s):
        if self.season is not None and s != self.season:
            r = 0.6  # regress team ratings 40% to the mean between seasons
            for d, m in ((self.sf, 30.0), (self.sa, 30.0), (self.gf, self.p["lg_goals"]), (self.ga, self.p["lg_goals"])):
                for k in list(d): d[k] = m + r * (d[k] - m)
            self.n.clear(); self.form.clear()
        self.season = s

    def lg_sv(self):
        g, s, _ = self.lg
        return 1 - g / s if s > 1000 else 0.900

    def goalie_sv(self, gid, team):
        lsv = self.lg_sv(); k = self.p["sv_k"]
        if gid is not None and gid in self.gsv:
            sv, sh = self.gsv[gid]
        else:
            sv, sh = self.tsv[team]
        return (sv + k * lsv) / (sh + k)

    def lam(self, att, dfn, dgoalie, home, rest_att, rest_def, tz_att):
        p = self.p; lsh = self.lg[1] / max(self.lg[2] * 2, 1) or 30.0
        lsh = lsh if self.lg[2] > 50 else 30.0
        shots = self.sf[att] * self.sa[dfn] / lsh  # TCI: territory / shot volume
        # PPE: finishing (regressed team sh%) vs opponent goalie
        lsv = self.lg_sv()
        sh_pct = (self.gf[att] + p["sh_k"] / 30 * (1 - lsv)) / (self.sf[att] + p["sh_k"] / 30)
        sh_pct = (1 - lsv) + (sh_pct - (1 - lsv)) * 0.5
        sv = self.goalie_sv(dgoalie, dfn)
        lam_shot = shots * (1 - sv) * (sh_pct / (1 - lsv))
        lam_goal = self.gf[att] * self.ga[dfn] / p["lg_goals"]
        lam = p["w_shot"] * lam_shot + (1 - p["w_shot"]) * lam_goal
        lam *= (1 + p["home"]) if home else (1 - p["home"])
        lam *= (1 - p["b2b"]) if rest_att else 1.0
        lam *= (1 + p["b2b"] * 0.6) if rest_def else 1.0
        lam *= (1 - p["tz"] * tz_att)
        return max(lam, 1.2)

    def features(self, g):
        h, a = g["home"], g["away"]
        def b2b(t):
            return t in self.last and (np.datetime64(g["date"]) - np.datetime64(self.last[t])).astype(int) <= 1
        def tzhop(t, venue):
            return abs(TZ.get(venue, -6) - self.lastz.get(t, TZ.get(t, -6)))
        hb, ab = b2b(h), b2b(a)
        lh = self.lam(h, a, g.get("agoalie"), True, hb, ab, tzhop(h, h))
        la = self.lam(a, h, g.get("hgoalie"), False, ab, hb, tzhop(a, h))
        fh = sum(self.form[h]) / max(len(self.form[h]), 1); fa = sum(self.form[a]) / max(len(self.form[a]), 1)
        tf = float(np.clip((fh - fa) * 0.01, -self.p["tf"], self.p["tf"]))
        uf = 0 if (self.n[h] < 8 or self.n[a] < 8 or (hb and ab)) else 1
        return dict(lh=lh, la=la, tf=tf, uf=uf, hb=hb, ab=ab, tzh=tzhop(h, h), tza=tzhop(a, h))

    def update(self, g):
        d = self.p["decay"]; h, a = g["home"], g["away"]
        # regulation-ish goals: strip OT/SO winner goal for scoring rates
        hg, ag = g["hg"], g["ag"]
        if g.get("last") in ("OT", "SO"):
            if hg > ag: hg -= 1
            else: ag -= 1
        hs, as_ = g.get("hs") or 30, g.get("as") or 30
        for t, sf, sa, gf, ga in ((h, hs, as_, hg, ag), (a, as_, hs, ag, hg)):
            self.sf[t] = d * self.sf[t] + (1 - d) * sf; self.sa[t] = d * self.sa[t] + (1 - d) * sa
            self.gf[t] = d * self.gf[t] + (1 - d) * gf; self.ga[t] = d * self.ga[t] + (1 - d) * ga
            self.n[t] += 1; self.last[t] = g["date"]; self.lastz[t] = TZ.get(h, -6)
            self.form[t].append(gf - ga)
        for gid, team, shots, ga in ((g.get("hgoalie"), h, as_, ag), (g.get("agoalie"), a, hs, hg)):
            for key, dd in ((gid, self.gsv), (team, self.tsv)):
                if key is None: continue
                sv, sh = dd[key]; dd[key] = [0.995 * sv + (shots - ga), 0.995 * sh + shots]
        self.lg = [0.999 * self.lg[0] + hg + ag, 0.999 * self.lg[1] + hs + as_, 0.999 * self.lg[2] + 1]


def simulate(lh, la, p=DEFAULT, n=5000, rng=None):
    """5,000 Monte Carlo games -> dict of probabilities: home ML, home -1.5, away -1.5, total distribution."""
    rng = rng or np.random.default_rng(7)
    H = rng.poisson(lh, n); A = rng.poisson(la, n)
    diff = H - A
    # empty-net: trailing team pulls goalie in 1-goal games -> leader scores EN goal w.p. en_p (opp scores tie ~ 0.1)
    one = np.abs(diff) == 1; u = rng.random(n)
    en = one & (u < p["en_p"]); eq = one & (u > 0.90)
    H = H + (en & (diff > 0)); A = A + (en & (diff < 0))
    H = H + (eq & (diff < 0)); A = A + (eq & (diff > 0))
    tie = H == A
    otw = rng.random(n) < (p["ot_home"] + 0.5 * (lh - la) / (lh + la))
    hw = (H > A) | (tie & otw)
    Hf = H + (tie & otw); Af = A + (tie & ~otw)
    return dict(home=float(hw.mean()), reg_tie=float(tie.mean()), h_m15=float((Hf - Af >= 2).mean()),
                a_m15=float((Af - Hf >= 2).mean()), totals=Hf + Af)


def p_over(tot, line):
    if float(line).is_integer():
        o, pu = float((tot > line).mean()), float((tot == line).mean())
        return o / max(1 - pu, 1e-9)
    return float((tot > line).mean())


def ml_prob(a):
    a = float(a); return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def dec(a):
    a = float(a); return 1 + a / 100 if a > 0 else 1 + 100 / -a
