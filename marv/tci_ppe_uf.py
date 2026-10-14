"""TCI / PPE / UF layer (paper/demo only; approved by Marvin 2026-10-13 from experiments/tci_ppe_uf).

* TCI (Trench Control Index) and PPE (Pacing & Possession Efficiency): pre-game team means of per-game
  dominance stats, z-scored across teams. Marv's probability gets a log-odds shift of K=0.10 per SD:
  side markets (ml/spread) by (TCI_home-TCI_away)+(PPE_home-PPE_away) toward the home side, totals by
  PPE_home+PPE_away toward the Over. Monte Carlo count is unchanged.
* UF gate (bankroll protection): 0 for rivalry chaos / emotional spot / late-injury volatility.
  Enforced (UF=0 -> no bet) only for UF_GATED sports; logged for every pick everywhere.
* Soccer moneyline / 1X2 / draw picks are dropped from the play list.
* Every pick carries prob_old / edge_old / old_active so the ledger tracks old vs new side by side;
  state/tci_ppe_uf/ab_ledger.json holds every pick active under either model, graded on resolve.
Fail-open: any error leaves Marv's original picks untouched. Kill switch: env MARV_TPU=0.
"""
import json, logging, math, os, re, warnings

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

log = logging.getLogger(__name__)
K = 0.10
UF_GATED = {"cfb", "soccer"}
DROP_ML = {"soccer"}
BLOWOUT = {"nfl": 17, "cfb": 21, "wnba": 20, "nba": 20, "soccer": 3}
ENABLED = os.environ.get("MARV_TPU", "1") != "0"
# TF_dampened (Master formula, 2026-10-13): bounded short-term momentum applied AFTER TCI/PPE/UF.
# trend = mean of the last TF_N games' result vs the market line (cover margin / total residual),
# prob shift = TF_MAX * tanh(trend_diff / sd), so never more than +-2.5 points. Off unless MARV_TF=1.
TF_ON = os.environ.get("MARV_TF", "0") == "1"
TF_MAX, TF_N = 0.025, 4
SD = {"nfl": (13.5, 13.0), "cfb": (15.5, 16.0), "wnba": (11.5, 15.0), "nba": (12.0, 18.0), "ncaab": (11.0, 16.0),
      "ncaaw": (11.0, 16.0), "nhl": (1.6, 1.8), "mlb": (4.2, 4.4), "soccer": (1.6, 1.6)}


def _sig(x): return 1 / (1 + math.exp(-x))
def _logit(p): p = min(max(p, 1e-4), 1 - 1e-4); return math.log(p / (1 - p))


# ------------------------------------------------------------------ team features (pre-game only)
def _z(df: pd.DataFrame, now) -> dict:
    df = df[(df.date < pd.Timestamp(now).tz_localize(None)) & (df.date >= pd.Timestamp(now).tz_localize(None) - pd.Timedelta(days=400))]
    m = df.groupby("team")[["tci", "ppe"]].mean()
    m = m[df.groupby("team").size() >= 2]
    if m.empty:
        return {}
    m = (m - m.mean()) / m.std()
    return m.fillna(0).to_dict("index")


def _nfl(C):
    st = pd.concat([pd.read_csv(C / f"nfl_stats_team_week_{y}.csv") for y in _years(C, "nfl_stats_team_week_{}.csv")])
    st["plays"] = st.attempts + st.carries + st.sacks_suffered
    st["epa"] = (st.passing_epa.fillna(0) + st.rushing_epa.fillna(0)) / st.plays
    st["sackr"] = st.sacks_suffered / (st.attempts + st.sacks_suffered)
    st["press"] = st.def_sacks.fillna(0) + st.def_qb_hits.fillna(0)
    td = st.passing_tds + st.rushing_tds
    st["rz"] = td / (td + st.fg_att.fillna(0)).replace(0, np.nan)
    o = st[["game_id", "team", "epa", "sackr", "plays"]].rename(columns={"team": "opp", "epa": "oepa", "sackr": "osackr", "plays": "oplays"})
    st = st.merge(o, on="game_id"); st = st[st.team != st.opp]
    st["tci"] = (st.epa - st.oepa) * 10 + (st.osackr - st.sackr) * 10 + st.press * 0.05
    st["ppe"] = (st.plays - st.oplays) * 0.02 + st.rz.fillna(0.5)
    g = pd.read_csv(C / "nflverse_games.csv", usecols=["game_id", "gameday"])
    st = st.merge(g, on="game_id"); st["date"] = pd.to_datetime(st.gameday)
    return st


def _cfb(C):
    ys = _years(C, "cfb_teamgames_{}.parquet")
    tg = pd.concat([pd.read_parquet(C / f"cfb_teamgames_{y}.parquet") for y in ys]).drop_duplicates(["game_id", "team"])
    tg["game_id"] = tg.game_id.astype(str)
    o = tg[["game_id", "team", "success_rate", "stuff_rate", "sack_rate", "plays"]].rename(columns=lambda c: c if c == "game_id" else "o_" + c)
    tg = tg.merge(o, on="game_id"); tg = tg[tg.team != tg.o_team]
    tg["tci"] = (tg.success_rate - tg.o_success_rate) * 10 + (tg.o_stuff_rate - tg.stuff_rate) * 5 + (tg.o_sack_rate - tg.sack_rate) * 5
    tg["ppe"] = (tg.plays - tg.o_plays) * 0.02 + tg.redzone_td_rate.fillna(0.6)
    g = pd.concat([pd.read_parquet(C / f"cfb_games_{y}.parquet", columns=["game_id", "date"]) for y in ys])
    g["game_id"] = g.game_id.astype(str)
    tg = tg.merge(g, on="game_id"); tg["date"] = pd.to_datetime(tg.date)
    return tg


def _hoops(C, key):
    ys = _years(C, key + "_box_{}.parquet")
    b = pd.concat([pd.read_parquet(C / f"{key}_box_{y}.parquet") for y in ys])
    for c in ("field_goals_attempted", "offensive_rebounds", "total_turnovers", "free_throws_attempted", "field_goals_made",
              "three_point_field_goals_made", "total_rebounds", "points_in_paint"):
        b[c] = pd.to_numeric(b[c], errors="coerce")
    b["date"] = pd.to_datetime(b.game_date); b["gid"] = b.game_id.astype(str)
    b["poss"] = b.field_goals_attempted - b.offensive_rebounds + b.total_turnovers + 0.44 * b.free_throws_attempted
    b["efg"] = (b.field_goals_made + 0.5 * b.three_point_field_goals_made) / b.field_goals_attempted
    o = b[["gid", "team_display_name", "total_rebounds", "points_in_paint", "efg"]].rename(columns=lambda c: c if c == "gid" else "o_" + c)
    b = b.merge(o, on="gid"); b = b[b.team_display_name != b.o_team_display_name]
    b["tci"] = (b.total_rebounds - b.o_total_rebounds) * 0.1 + (b.points_in_paint - b.o_points_in_paint) * 0.05
    b["ppe"] = (b.poss - b.poss.mean()) * 0.05 + (b.efg - b.o_efg) * 10
    return b.rename(columns={"team_display_name": "team"})


def _years(C, pat):
    y = datetime.now().year
    return [v for v in (y - 1, y) if (C / pat.format(v)).exists()]


LOADERS = {"nfl": _nfl, "cfb": _cfb, "wnba": lambda C: _hoops(C, "wnba"), "nba": lambda C: _hoops(C, "nba")}


def team_z(sport: str, state_dir: Path, now) -> dict:
    """{team: {tci, ppe}} z-scores from games before `now`; cached per sport per day (keeps the VM light)."""
    if sport not in LOADERS:
        return {}
    od = Path(state_dir) / "tci_ppe_uf"; od.mkdir(exist_ok=True)
    f = od / f"z_{sport}_{pd.Timestamp(now).date()}.json"
    if f.exists():
        return json.loads(f.read_text())
    z = _z(LOADERS[sport](Path(state_dir) / "cache"), now)
    f.write_text(json.dumps(z))
    for old in od.glob(f"z_{sport}_*.json"):
        if old != f:
            old.unlink(missing_ok=True)
    return z


def _lookup(z: dict, team: str):
    if team in z:
        return z[team]
    try:
        from .data.teams import NFL_TEAMS
        ab = {v: k for k, v in NFL_TEAMS.items()}.get(team)
        if ab and ab in z:
            return z[ab]
    except Exception:  # noqa: BLE001
        pass
    from .soccer_max.names import resolve
    t = resolve(team, list(z))
    return z.get(t) if t else None


# ------------------------------------------------------------------ UF
def _last_margin(history, team, now):
    gs = [g for g in history if g.completed and g.home_score is not None and g.start < now and team in (g.home, g.away)]
    if not gs:
        return 0.0
    g = max(gs, key=lambda g: g.start)
    return (g.home_score - g.away_score) if g.home == team else (g.away_score - g.home_score)


def _cfb_conf(state_dir: Path) -> dict:
    try:
        y = datetime.now().year
        s = pd.read_parquet(Path(state_dir) / "cache" / f"cfb_sched_{y}.parquet", columns=["home_team", "home_conference", "away_team", "away_conference"])
        d = dict(zip(s.home_team, s.home_conference)); d.update(dict(zip(s.away_team, s.away_conference)))
        return d
    except Exception:
        return {}


def uf_flag(sport: str, game, history, now, state_dir: Path, conf: dict | None = None) -> tuple[int, str]:
    why = []
    odds = game.odds
    spread = abs(odds.spread) if odds and odds.spread is not None else None
    if sport == "nfl" and game.info.get("div_game"):
        why.append("rivalry")
    if sport == "cfb" and conf:
        from .soccer_max.names import resolve
        h, a = conf.get(resolve(game.home, list(conf)) or ""), conf.get(resolve(game.away, list(conf)) or "")
        if h and h == a and spread is not None and spread <= 7:
            why.append("rivalry")
    if sport == "wnba" and spread is not None and spread <= 4:
        recent = [g for g in history if {g.home, g.away} == {game.home, game.away} and timedelta(0) < game.start - g.start <= timedelta(days=4)]
        if recent:
            why.append("rivalry")
    blow = BLOWOUT.get(sport)
    if blow and min(_last_margin(history, game.home, now), _last_margin(history, game.away, now)) <= -blow:
        why.append("emotional")
    if game.info.get("late_injury"):
        why.append("injury")
    return (0 if why else 1), ",".join(why)


def tf_shifts(sport: str, game, history, now) -> tuple[float, float]:
    """(side shift toward home, total shift toward Over) in probability points, each clamped to +-TF_MAX."""
    def trend(team):
        gs = sorted([g for g in history if g.completed and g.home_score is not None and g.start < now and team in (g.home, g.away)
                     and g.odds and g.odds.spread is not None], key=lambda g: g.start)[-TF_N:]
        if len(gs) < 2:
            return 0.0, 0.0
        m = [((g.home_score - g.away_score) + g.odds.spread) * (1 if g.home == team else -1) for g in gs]
        t = [g.home_score + g.away_score - g.odds.total for g in gs if g.odds.total is not None]
        return sum(m) / len(m), (sum(t) / len(t) if t else 0.0)
    sdm, sdt = SD.get(sport, (12.0, 14.0))
    (mh, th), (ma, ta) = trend(game.home), trend(game.away)
    side = max(-TF_MAX, min(TF_MAX, TF_MAX * math.tanh((mh - ma) / sdm)))
    tot = max(-TF_MAX, min(TF_MAX, TF_MAX * math.tanh((th + ta) / 2 / sdt)))
    return side, tot


def _reprice(sport, pk, newp, tag):
    b = (pk.price / 100) if pk.price > 0 else (100 / -pk.price)
    pk.ev = pk.ev + (newp - pk.prob) * (1 + b)
    pk.edge = pk.ev / (1 + b)
    pk.prob = newp
    need = _need(sport, pk)
    pk.vetoes = [v for v in pk.vetoes if not re.match(r"edge [+-]", v)]
    if pk.edge < need:
        pk.vetoes.append(f"edge {pk.edge:+.1%} below {need:.0%} ({tag})")


# ------------------------------------------------------------------ MASTER architecture (2026-10-13, paper/demo only)
# MC baseline -> x TCI*PPE at full spec weight (bounded) -> binary UF gate on ALL sports -> TF_dampened.
# Shadow layer: never changes pk.prob / pk.vetoes / pred.home_win (Telegram + legacy feed unchanged).
# Results go to pred.master_* -> predictions.json -> pick_feed (MARV_MASTER=1) + experiments/master/master.py.
MASTER_ON = os.environ.get("MARV_MASTER", "0") == "1"
K_MASTER = 0.25          # full-spec TCI*PPE weight per SD (legacy layer uses K=0.10)
MASTER_LOGIT_CAP = 0.50  # max |log-odds| shift from TCI*PPE
MASTER_PROB_CAP = 0.08   # max |prob| change from TCI*PPE vs the MC baseline
WX_WIND, WX_TEMP = 20.0, 20.0  # severe weather (mph / deg F), only where the feed carries it (NFL)
# UF v2 (2026-10-13, experiments/master/uf_opt): per-reason stake multipliers instead of a blanket kill.
# Default for every reason is 0.0 (full kill, = v1). Validated on Sep21-Oct12 AND Aug01-Oct12 (both improved):
#   WNBA emotional spot -> half stake (blowout losses in a 2-3 day schedule were not a losing signal: skips were mostly winners)
#   injury on SIDES -> half stake (announced Q/D is priced into the spread; totals keep the full kill)
# Rejected (hurt in >=1 window): edge-size override, rivalry-only-if-small-edge, starter-only injury, rivalry/emotional half.
# v1 binary decision is still logged (master_uf_old / active_master_old) for A/B. Rollback: env MARV_UF_V2=0.
UF_V2 = os.environ.get("MARV_UF_V2", "1") != "0"
UF_POLICY = {"emotional@wnba": 0.5, "injury@sides": 0.5}


def _uf_stake(sport: str, kind: str, reasons: list) -> float:
    """Stake multiplier 0 / 0.5 / 1 for a Master pick ('sides' or 'totals') given its UF reasons (min over reasons)."""
    s = 1.0
    for r in reasons:
        base = re.split(r"[:(]", r, 1)[0].strip()
        m = UF_POLICY.get(f"{base}@{sport}", UF_POLICY.get(f"{base}@{kind}", 0.0)) if UF_V2 else 0.0
        s = min(s, m)
    return s


def _master_injury(sport, game, state_dir):
    """Late-injury ambiguity: key player Questionable/Doubtful/GTD (news <72h) in clvinj's injury_snap."""
    try:
        import sqlite3
        db = Path(state_dir) / "marv_clv.db"
        if not db.exists():
            return ""
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        since = (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M")
        rows = c.execute("select team, player, status from injury_snap where sport=? and key_player=1 and news_ts>=? and "
                         "lower(status) in ('questionable','doubtful','day-to-day','game-time decision')", (sport, since)).fetchall()
        c.close()
        nn = lambda s: re.sub(r"[^a-z]", "", str(s).lower())
        hits = [f"{p} {s}" for t, p, s in rows if nn(t) and (nn(t) in (nn(game.home), nn(game.away)) or nn(game.home) in nn(t) or nn(game.away) in nn(t))]
        return ("injury:" + "/".join(hits[:2])) if hits else ""
    except Exception:  # noqa: BLE001
        return ""


def _master(sport, pred, g, side_shift, tot_shift, uf, why, tf_side, tf_tot, state_dir):
    reasons = [x for x in why.split(",") if x]
    inj = _master_injury(sport.key, g, state_dir)
    if inj:
        reasons.append(inj)
    try:
        w, t = float(g.info.get("wind") or 0), g.info.get("temp")
        if w >= WX_WIND or (t is not None and float(t) <= WX_TEMP):
            reasons.append(f"weather(wind {w:.0f}, temp {t})")
    except Exception:  # noqa: BLE001
        pass
    muf_old = 0 if reasons else 1
    st_side, st_tot = _uf_stake(sport.key, "sides", reasons), _uf_stake(sport.key, "totals", reasons)
    muf = 1 if st_side > 0 else 0  # side gate (pick_feed / sides); totals use master_uf_stake_total
    base = float(pred.home_win_old)
    def full(p0, s_k):  # s_k is the legacy K=0.10 shift; scale to K_MASTER, bound both ways
        sh = max(-MASTER_LOGIT_CAP, min(MASTER_LOGIT_CAP, s_k * K_MASTER / K))
        p = _sig(_logit(p0) + sh)
        return min(p0 + MASTER_PROB_CAP, max(p0 - MASTER_PROB_CAP, p))
    hw = full(base, side_shift)
    hw_tci = hw
    if TF_ON:
        hw = min(0.999, max(0.001, hw + tf_side))
    pred.master_home_win, pred.master_home_win_tci = round(hw, 4), round(hw_tci, 4)
    pred.master_uf, pred.master_uf_reason = muf, ",".join(reasons)
    pred.master_uf_old, pred.master_uf_stake_side, pred.master_uf_stake_total = muf_old, st_side, st_tot
    pred.master_tot_shift = round(max(-MASTER_LOGIT_CAP, min(MASTER_LOGIT_CAP, tot_shift * K_MASTER / K)), 4)
    out = []
    for pk in pred.picks:
        if pk.market not in ("ml", "spread"):
            continue  # totals: Master uses the extremes rule only (experiments/master/master.py)
        s = side_shift if pk.side == g.home else -side_shift
        p0 = float(getattr(pk, "prob_old", pk.prob))
        p = full(p0, s)
        if TF_ON:
            p = min(0.999, max(0.001, p + (tf_side if pk.side == g.home else -tf_side)))
        b = (pk.price / 100) if pk.price > 0 else (100 / -pk.price)
        edge = (p * (1 + b) - 1) / (1 + b)
        hard = [v for v in pk.vetoes if not v.startswith("UF=") and not re.match(r"edge [+-]", v) and "moneyline dropped" not in v]
        need = _need(sport, pk)
        base_ok = not hard and edge >= need and not (sport.key in DROP_ML and pk.market == "ml")
        act = base_ok and muf == 1
        out.append({"market": pk.market, "side": pk.side, "line": pk.line, "price": pk.price, "prob_mc": round(p0, 4),
                    "prob_legacy": round(pk.prob, 4), "prob_master": round(p, 4), "edge_master": round(edge, 4),
                    "need": need, "active_legacy": bool(pk.active), "active_master": bool(act),
                    "active_master_old": bool(base_ok and muf_old == 1), "uf_stake": st_side if act else 0.0,
                    "why_not": ([] if act else hard + ([f"edge {edge:+.3f}<{need}"] if edge < need else []) + ([f"UF=0 ({pred.master_uf_reason})"] if muf == 0 else []))
                    + ([f"UF half stake ({pred.master_uf_reason})"] if act and st_side < 1 else [])})
    if UVF_DOG_ON:
        try:
            _uvf_dog(sport, g, out, side_shift, tf_side, muf, st_side)
        except Exception:  # noqa: BLE001
            log.exception("UVF dog layer failed (no effect)")
    pred.master_picks = out


# UVF model-edge dog (2026-10-13, experiments/upset/bt_dog.py + an_dog.py). Paper/demo only. Flag MARV_UVF_DOG=1.
# edge = P(dog) [MC -> legacy TCI*PPE K=0.10 shift -> +TF_side, the backtested spec] - vig-free market implied.
# ADOPTED: WNBA dogs +120 or longer with edge in [6%, 10%) and UF gate open -> active Master pick at HALF stake.
#   (WNBA 2018-25 n=181 +10.7% ROI, OOS 2025-26 n=66 +27.1%, Aug1-Oct12 n=5 +63%, Sep21-Oct12 n=1 +145%: tiny 2026 n)
#   10%+ edges are excluded (overconfidence: OOS -0.8%, chaos 10%+ negative). NFL/CFB: every bucket rejected -> uvf_edge logged only.
UVF_DOG_ON = os.environ.get("MARV_UVF_DOG", "0") == "1"
UVF_DOG_RULES = {"wnba": (0.06, 0.10)}
UVF_DOG_MIN_PRICE = 120


def _uvf_dog(sport, g, out, side_shift, tf_side, muf, st_side):
    o = g.odds
    if not o or o.home_ml is None or o.away_ml is None:
        return
    def imp(a): a = float(a); return 100 / (a + 100) if a > 0 else -a / (-a + 100)
    ih, ia = imp(o.home_ml), imp(o.away_ml)
    for e in out:
        if e["market"] != "ml" or e["price"] is None or e["price"] < UVF_DOG_MIN_PRICE:
            continue
        home = e["side"] == g.home
        p = _sig(_logit(e["prob_mc"]) + (side_shift if home else -side_shift)) + (tf_side if home else -tf_side)
        p = min(0.999, max(0.001, p))
        vf = (ih if home else ia) / (ih + ia)
        e["uvf_p"], e["uvf_edge"] = round(p, 4), round(p - vf, 4)
        lo_hi = UVF_DOG_RULES.get(sport.key)
        if lo_hi and lo_hi[0] <= p - vf < lo_hi[1] and muf == 1:
            e["uvf_dog"] = True
            if not e["active_master"]:
                e["active_master"], e["uf_stake"] = True, min(0.5, st_side)
                e["why_not"] = ["UVF Value Dog (half stake, paper)"]
# ------------------------------------------------------------------ apply to engine predictions
def _need(sport, pick) -> float:
    v = sport.veto
    if pick.market == "spread":
        need = v.spread_edge
        for t, e in v.spread_tiers:
            if pick.line is not None and abs(pick.line) >= t:
                need = max(need, e)
        return need
    return {"total": v.total_edge, "ml": v.ml_edge}.get(pick.market, v.ml_edge)


def apply(sport, preds, history, state_dir: Path, now=None):
    """Mutates predictions in place (new formula); returns them. Fail-open."""
    if not ENABLED or not preds:
        return preds
    now = now or datetime.now(timezone.utc)
    try:
        z = team_z(sport.key, state_dir, now)
    except Exception:
        log.exception("TCI/PPE features failed for %s; no adjustment", sport.key); z = {}
    conf = _cfb_conf(state_dir) if sport.key == "cfb" else None
    for pred in preds:
        try:
            g = pred.game
            zh, za = _lookup(z, g.home), _lookup(z, g.away)
            side_shift = tot_shift = 0.0
            if zh and za:
                side_shift = K * ((zh["tci"] - za["tci"]) + (zh["ppe"] - za["ppe"]))
                tot_shift = K * (zh["ppe"] + za["ppe"])
            uf, why = uf_flag(sport.key, g, history, now, state_dir, conf)
            try:
                tf_side, tf_tot = tf_shifts(sport.key, g, history, now)
            except Exception:  # noqa: BLE001
                tf_side = tf_tot = 0.0
            pred.home_win_old = pred.home_win
            if side_shift:
                pred.home_win = _sig(_logit(pred.home_win) + side_shift)
            pred.uf, pred.uf_reason, pred.tci_ppe_shift = uf, why, round(side_shift, 4)
            if TF_ON and tf_side:
                pred.home_win = min(0.999, max(0.001, pred.home_win + tf_side))
            pred.tf_shift = round(tf_side, 4)
            pred.notes.append(f"TCI/PPE shift {side_shift:+.2f} (tot {tot_shift:+.2f}); TF {tf_side:+.3f}/{tf_tot:+.3f}"
                              f"{'' if TF_ON else ' (off)'}; UF={uf}{' (' + why + ')' if why else ''}")
            for pk in pred.picks:
                pk.prob_old, pk.edge_old, pk.old_active = pk.prob, pk.edge, pk.active
                pk.uf, pk.uf_reason = uf, why
                if pk.market in ("ml", "spread"):
                    s = side_shift if pk.side == g.home else -side_shift
                elif pk.market == "total":
                    s = tot_shift if pk.side == "Over" else -tot_shift
                else:
                    s = 0.0
                if s:
                    _reprice(sport, pk, _sig(_logit(pk.prob) + s), "TCI/PPE")
                # TF_dampened, after TCI/PPE (UF / ML-drop vetoes below are unaffected by it)
                pk.prob_pre_tf, pk.edge_pre_tf, pk.tf_shift = pk.prob, pk.edge, 0.0
                pk.pre_tf_active = not [v for v in pk.vetoes if not v.startswith("UF=") and "moneyline dropped" not in v]
                if pk.market in ("ml", "spread", "total"):
                    sh = (tf_side if pk.side == g.home else -tf_side) if pk.market != "total" else (tf_tot if pk.side == "Over" else -tf_tot)
                    pk.tf_shift = round(sh, 4)
                    if TF_ON and sh:
                        _reprice(sport, pk, min(0.999, max(0.001, pk.prob + sh)), "TF")
                if sport.key in DROP_ML and pk.market in ("ml", "draw"):
                    pk.vetoes.append("soccer moneyline dropped from play list")
                if sport.key in UF_GATED and uf == 0:
                    pk.vetoes.append(f"UF=0 ({why}): no bet")
            if MASTER_ON:
                try:
                    _master(sport, pred, g, side_shift, tot_shift, uf, why, tf_side, tf_tot, state_dir)
                except Exception:  # noqa: BLE001
                    log.exception("MASTER layer failed on %s (legacy untouched)", g.id)
        except Exception:
            log.exception("TCI/PPE/UF failed on %s; pick left as-is", pred.game.id)
    _ab_log(sport.key, preds, state_dir)
    return preds


# ------------------------------------------------------------------ side-by-side ledger
def _ab_path(state_dir):
    p = Path(state_dir) / "tci_ppe_uf"; p.mkdir(exist_ok=True); return p / "ab_ledger.json"


def _ab_log(sport, preds, state_dir):
    try:
        p = _ab_path(state_dir)
        led = json.loads(p.read_text()) if p.exists() else {}
        now = datetime.now(timezone.utc).isoformat()
        for pred in preds:
            if pred.game.start <= datetime.now(timezone.utc):
                continue
            for pk in pred.picks:
                old, new = bool(getattr(pk, "old_active", pk.active)), pk.active
                k = f"{sport}:{pred.game.id}:{pk.market}"
                pre = bool(getattr(pk, "pre_tf_active", new))
                if not (old or new or pre):
                    if k in led and led[k].get("result") is None:  # no longer a play under either: keep latest flags
                        led[k].update(old_active=False, new_active=False, updated=now)
                    continue
                if k in led and led[k].get("result") is not None:
                    continue
                led[k] = {"sport": sport, "game_id": pred.game.id, "start": pred.game.start.isoformat(), "home": pred.game.home,
                          "away": pred.game.away, "market": pk.market, "side": pk.side, "line": pk.line, "price": pk.price,
                          "prob_old": round(getattr(pk, "prob_old", pk.prob), 4), "prob_new": round(pk.prob, 4),
                          "edge_old": round(getattr(pk, "edge_old", pk.edge), 4), "edge_new": round(pk.edge, 4),
                          "old_active": old, "new_active": new, "uf": getattr(pk, "uf", None), "uf_reason": getattr(pk, "uf_reason", ""),
                          "tf_on": TF_ON, "tf_shift": getattr(pk, "tf_shift", 0.0),
                          "prob_pre_tf": round(getattr(pk, "prob_pre_tf", pk.prob), 4), "edge_pre_tf": round(getattr(pk, "edge_pre_tf", pk.edge), 4),
                          "pre_tf_active": bool(getattr(pk, "pre_tf_active", new)) and (pk.market not in ("ml", "draw") or sport not in DROP_ML)
                                           and not (sport in UF_GATED and getattr(pk, "uf", 1) == 0),
                          "vetoes": pk.vetoes, "updated": now, "result": None}
        p.write_text(json.dumps(led, indent=1))
    except Exception:
        log.exception("A/B ledger write failed")


def ab_resolve(sport, finished, state_dir) -> int:
    try:
        from .engine import grade
        from .models import Pick
        p = _ab_path(state_dir)
        if not p.exists():
            return 0
        led = json.loads(p.read_text()); by = {g.id: g for g in finished if g.completed}; n = 0
        for e in led.values():
            if e["sport"] == sport and e["result"] is None and e["game_id"] in by:
                e["result"] = grade(Pick(e["market"], e["side"], e["line"], e["price"], e["prob_new"], 0, 0), by[e["game_id"]]); n += 1
        if n:
            p.write_text(json.dumps(led, indent=1))
        return n
    except Exception:
        log.exception("A/B ledger grading failed"); return 0


def ab_record(state_dir) -> dict:
    p = _ab_path(state_dir)
    led = json.loads(p.read_text()) if p.exists() else {}
    out = {}
    for model, fld in (("old", "old_active"), ("new", "new_active"), ("new_no_tf", "pre_tf_active")):
        r = [e["result"] for e in led.values() if e.get(fld) and e.get("result") is not None]
        out[model] = {"n": len(r), "W": sum(x > 0 for x in r), "L": sum(x < 0 for x in r), "units": round(sum(r), 2)}
    return out


# ------------------------------------------------------------------ soccer node helpers
def soccer_uf(results: pd.DataFrame, home: str, away: str, day) -> tuple[int, str]:
    try:
        d = results[results["date"] < pd.Timestamp(day)]
        worst = 0
        for t in (home, away):
            r = d[(d.home == t) | (d.away == t)].tail(1)
            if len(r):
                r = r.iloc[0]; m = (r.hg - r.ag) if r.home == t else (r.ag - r.hg); worst = min(worst, m)
        return (0, "emotional") if worst <= -BLOWOUT["soccer"] else (1, "")
    except Exception:
        return 1, ""
