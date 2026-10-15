"""Value Play layer (NFL/CFB) — TRACKING ONLY, tagged on the daily card; never feeds Kalshi/Polymarket unless
state/value_play/validation.json says passed=True (it does not: see experiments/value_play/vp_result.json).

Math (reuses marv/edges.py): implied -> proportional no-vig -> P shrunk toward no-vig (logit blend, w) ->
EV = P*B - (1-P) -> quarter-Kelly capped at 2% bankroll -> feed stake multiplier (1.0 if Kelly >= 1.5%, else 0.5).
Sides (spread/ML) need EV > THR and the key-number rule; totals use ONLY the extremes rule (|proj - line| >= 3).
Prices: Bovada snapshot (state/bovada_prices.json, read-only). No win % is ever printed.
"""
import json, math, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from . import edges as E

ST = Path(os.environ.get("STATE_DIR", "/opt/marv-bot/state")); OUT = ST / "value_play"
SD = {"nfl": 13.5, "cfb": 15.5}
CFG = {"w": 1.0, "thr": 0.045, "key_rule": "cross", "tot_ext": 3.0, "kelly_frac": 0.25, "cap": 0.02}  # spec defaults; train picked w=1.0
KEYS = (3, 7, 10)


def _ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def _w(s): return (str(s or "").lower().split() or [""])[-1]


def validation():
    try: return json.loads((OUT / "validation.json").read_text())
    except Exception: return {"passed": False}


def _bov(sport, home, away):
    try: ev = json.loads((ST / "bovada_prices.json").read_text())[sport]["events"]
    except Exception: return None
    for e in ev:
        if _w(e.get("home")) == _w(home) and _w(e.get("away")) == _w(away): return e


def classify(sline, sproj, home):
    """Marvin's tiers. sline = side's handicap (+ getting points); sproj = Marv projected margin for the side."""
    cross = [k for k in KEYS for s in (k, -k) if min(-sline, sproj) < s < max(-sline, sproj)]
    if sline >= 10: return "Maximum Value Zone (Double-Digit Inflated Dog)", cross
    if 0 < sline < 10 and home: return "Moderate Value Zone (Single-Digit Home Dog)", cross
    if cross: return f"Moderate Value Zone (Crossing Key Number {min(cross)})", cross
    return "Value Zone (No Key Number)", cross


def plays(now=None):
    now = now or datetime.now(timezone.utc); cfg = dict(CFG, **(validation().get("cfg") or {}))
    try: P = json.loads((ST / "predictions.json").read_text())
    except Exception: return []
    out = []
    for key, v in P.items():
        sp = v.get("sport")
        if sp not in SD or v.get("home_win") is None: continue
        try: st = datetime.fromisoformat(str(v["start"]).replace("Z", "+00:00"))
        except Exception: continue
        if not (now < st <= now + timedelta(days=7)): continue
        b = _bov(sp, v["home"], v["away"])
        if not b: continue
        uf = int(v.get("master_uf", v.get("uf", 1)))
        mm, mt = float(v.get("model_margin") or 0), v.get("model_total")
        hw = float(v.get("master_home_win") or v["home_win"])
        notes = [n for n in v.get("notes", []) if n.startswith("TCI/PPE")]
        base = dict(sport=sp, key=key, game=f"{v['away']} @ {v['home']}", home=v["home"], away=v["away"], start=v["start"],
                    market_line=dict(spread_home=(b.get("spread") or [None])[0], ml_home=(b.get("ml") or {}).get("home"),
                                     ml_away=(b.get("ml") or {}).get("away"), total=(b.get("total") or [None])[0]),
                    proj=dict(home_margin=round(mm, 1), total=mt), uf=uf)
        cands = []
        if b.get("spread"):
            hl, hp, ap = b["spread"]
            ph = 1 - _ncdf((-hl - mm) / SD[sp])
            for side, team, sl, pr, p, home in ((1, v["home"], hl, hp, ph, True), (-1, v["away"], -hl, ap, 1 - ph, False)):
                nv = E.devig([hp, ap], "proportional")[0 if side > 0 else 1]
                cands.append(("spread", team, sl, pr, p, nv, home, mm if side > 0 else -mm))
        ml = b.get("ml") or {}
        if ml.get("home") and ml.get("away"):
            nvh = E.devig([ml["home"], ml["away"]], "proportional")[0]
            cands += [("ml", v["home"], None, ml["home"], hw, nvh, True, mm), ("ml", v["away"], None, ml["away"], 1 - hw, 1 - nvh, False, -mm)]
        for mkt, team, sl, pr, p, nv, home, sproj in cands:
            P_ = E.blend(p, nv, cfg["w"]); ev = E.edge(P_, pr); k = E.kelly(P_, pr, cfg["kelly_frac"], cfg["cap"])
            if mkt == "spread":
                tier, cross = classify(sl, sproj, home)
                if cfg["key_rule"] == "cross" and not cross: continue
            else:
                tier, cross = ("Value Zone (Moneyline)", [])
            if ev <= cfg["thr"] or uf == 0: continue
            why = f"Marv margin {sproj:+.1f} for {team} vs market {('+' if sl > 0 else '') + str(sl) if sl is not None else 'ML'}"
            if cross: why += f" (crosses {', '.join(map(str, sorted(set(cross))))})"
            if notes: why += "; " + notes[0].split(";")[0]
            out.append(dict(base, market=mkt, pick=f"{team} {('+' if sl > 0 else '') + str(sl)}" if sl is not None else f"{team} ML",
                            price=pr, ev=round(ev, 4), kelly=round(k, 4), stake=1.0 if k >= 0.015 else 0.5, tier=tier, why=why))
        if b.get("total") and mt is not None:
            L, op, up = b["total"]; d = float(mt) - float(L)
            if abs(d) >= cfg["tot_ext"] and uf:
                pr = op if d > 0 else up; p = 1 - _ncdf(-abs(d) / 13.0)
                nv = E.devig([op, up], "proportional")[0 if d > 0 else 1]
                P_ = E.blend(p, nv, cfg["w"]); ev = E.edge(P_, pr); k = E.kelly(P_, pr, cfg["kelly_frac"], cfg["cap"])
                if ev > 0:
                    out.append(dict(base, market="total", pick=f"{'Over' if d > 0 else 'Under'} {L}", price=pr, ev=round(ev, 4),
                                    kelly=round(k, 4), stake=1.0 if k >= 0.015 else 0.5,
                                    tier="High Shootout" if d > 0 else "Defensive Grind",
                                    why=f"Marv total {float(mt):.1f} vs {L} ({d:+.1f}, extremes rule)"))
    out.sort(key=lambda x: -x["ev"])
    return out


def card_lines(sports_excl=()):
    """Ranked Value Plays block for the daily card. EV shown, tagged unproven; no win %, no 'locks'."""
    ps = [p for p in plays() if p["sport"] not in sports_excl]
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "card.json").write_text(json.dumps(ps, indent=1))
        led = OUT / "ledger.jsonl"; seen = {json.loads(l)["id"] for l in open(led)} if led.exists() else set()
        with open(led, "a") as f:
            for p in ps:
                i = f"{p['key']}:{p['market']}:{p['pick']}"
                if i not in seen: f.write(json.dumps(dict(p, id=i, logged=datetime.now(timezone.utc).isoformat())) + "\n")
    except Exception: pass
    tag = "" if validation().get("passed") else " · TRACKING, failed unseen-game test"
    L = ["", f"━━ 5️⃣ VALUE PLAYS (ranked by EV{tag}) ━━"]
    if not ps: L.append(" none qualified today"); return L
    for i, p in enumerate(ps[:8], 1):
        m = p["market_line"]; s = m["spread_home"]
        L.append(f" {i}) {p['game']}")
        L.append(f"    Line: {p['home'].split()[-1]} {('+' if (s or 0) > 0 else '') + str(s) if s is not None else 'n/a'} · ML {p['away'].split()[-1]} {m['ml_away']:+} / {p['home'].split()[-1]} {m['ml_home']:+}"
                 if m.get("ml_home") and m.get("ml_away") else f"    Line: spread {s}")
        L.append(f"    Marv: {p['home'].split()[-1]} {p['proj']['home_margin']:+.1f} · total {p['proj']['total']}")
        L.append(f"    {p['tier']} → Value Play: {p['pick']} ({p['price']:+}) · EV {p['ev']*100:+.1f}% · stake ×{p['stake']} (unproven)")
        L.append(f"    Why: {p['why']}")
    return L
