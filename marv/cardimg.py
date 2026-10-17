"""Bet-card images (Marvin 2026-10-17): HTML cards rendered to PNG with headless Chromium (.cardvenv, playwright).

Pages: parlays (ranked 2-leg, tier badge per parlay, reason per leg), bet-card tiles (2-column grid: matchup,
kickoff CT, network, Bovada spread/O-U, Marv margin/total, tier/signal label, PLAY box, one-line Marv reason) and a
league slate table (matchup | side lean | O/U projection). Real data only: daily card legs, predictions.json,
state/bovada_prices.json, ESPN scoreboard (network). No win % except proven types (record lines from
dailypicks.sport_breakdown). NBA excluded. MARV_CARD_IMAGES=off disables; any failure -> [] (callers send text).
"""
import html
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
ST = Path(os.environ.get("STATE_DIR", ROOT / "state"))
OUT = ST / "cards"
CT = ZoneInfo("America/Chicago")
PYW = ROOT / ".cardvenv" / "bin" / "python"
PWB = ROOT / ".pw"
LEAGUE = {"cfb": "NCAAF", "nfl": "NFL", "wnba": "WNBA", "soccer": "SOCCER", "mlb": "MLB", "nhl": "NHL",
          "ncaab": "NCAAB", "ncaaw": "NCAAW", "euroleague": "EUROLEAGUE"}
ESPN = {"cfb": "football/college-football", "nfl": "football/nfl", "wnba": "basketball/wnba"}
BADGE = {"HIGH": ("A", "#1f9d55"), "SIGNAL": ("B", "#d69e2e"), "LEAN": ("C", "#c53030")}
TIERTXT = {"HIGH": "A · passed every veto", "SIGNAL": "B · backtested signal", "LEAN": "C · model lean"}


def on() -> bool:
    return os.environ.get("MARV_CARD_IMAGES", "on").lower() not in ("0", "off", "false", "no")


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _j(p, d):
    try:
        return json.loads(Path(p).read_text())
    except Exception:  # noqa: BLE001
        return d


def _sim(a, b):
    from .data.teams import similarity
    return similarity(str(a or ""), str(b or ""))


def _when(start) -> str:
    try:
        return datetime.fromisoformat(str(start).replace("Z", "+00:00")).astimezone(CT).strftime("%a %-I:%M %p CT")
    except Exception:  # noqa: BLE001
        return ""


def _match(rows, home, away, hk="home", ak="away"):
    best, sc = None, 0.0
    for r in rows:
        v = min(_sim(r.get(hk), home), _sim(r.get(ak), away))
        if v > sc:
            best, sc = r, v
    return best if sc >= 0.75 else None


def _bov(sport, home, away):
    evs = (_j(ST / "bovada_prices.json", {}).get(sport) or {}).get("events", [])
    return _match(evs, home, away)


def _marv(sport, home, away):
    P = [v for v in _j(ST / "predictions.json", {}).values() if v.get("sport") == sport]
    return _match(P, home, away)


_NET: dict = {}


def _network(sport, home, away, start):
    if sport not in ESPN:
        return ""
    try:
        day = datetime.fromisoformat(str(start).replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    except Exception:  # noqa: BLE001
        return ""
    key = (sport, day)
    if key not in _NET:
        import requests
        rows = []
        try:
            js = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[sport]}/scoreboard",
                              params={"dates": day, "limit": 400, **({"groups": 80} if sport == "cfb" else {})}, timeout=15).json()
            for ev in js.get("events", []):
                c = ev["competitions"][0]
                t = {x["homeAway"]: x["team"].get("location") or x["team"].get("displayName") for x in c["competitors"]}
                nets = [n for b in c.get("broadcasts", []) for n in b.get("names", [])]
                rows.append({"home": t.get("home"), "away": t.get("away"), "net": "/".join(nets[:2])})
        except Exception:  # noqa: BLE001
            pass
        _NET[key] = rows
    m = _match(_NET[key], home, away)
    return (m or {}).get("net", "")


def _spread_txt(b, home, away):
    if not b:
        return "—", "—"
    sp, tot = (b.get("spread") or [None])[0], (b.get("total") or [None])[0]
    s = "—"
    if sp is not None:
        fav = home if sp <= 0 else away
        s = f"{_short(fav)} {-abs(sp):g}" if sp else "PK"
    return s, (f"{tot:g}" if tot is not None else "—")


def _short(n):
    return str(n).replace(" State", " St").strip()


def _marv_txt(m):
    if not m:
        return "—", "—"
    mg = float(m["model_margin"])
    who = m["home"] if mg >= 0 else m["away"]
    return f"{_short(who)} by {abs(mg):.1f}", f"{float(m['model_total']):.1f}"


def _pick(leg):
    if leg.get("market") == "ml":
        return f"{leg['side']} ML {int(leg['price']):+d}"
    return f"{leg['side']} {float(leg['line']):g} ({int(leg['price']):+d})"


def _reason(leg, m=None):
    w = str(leg.get("why", "")).replace("(tracking, unproven)", "(tracking)")
    w = w.split(" · ")[0] if " · " in w and len(w) > 110 else w
    if m and leg.get("market") == "total" and leg.get("line") is not None:
        w = f"Marv total {float(m['model_total']):.1f} vs {float(leg['line']):g} · " + w
    return w[:150]


def _label(leg):
    if leg.get("master_extreme"):
        return ("B", "#d69e2e", f"🎯 Master Extreme · {leg['master_extreme']}")
    b, c = BADGE.get(leg.get("tier"), ("C", "#c53030"))
    sig = str(leg.get("why", "")).split(":")[0] if leg.get("tier") == "SIGNAL" else ""
    return (b, c, TIERTXT.get(leg.get("tier"), "") + (f" · {sig}" if sig and len(sig) < 24 else ""))


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{width:1080px;background:#eef1f6;font-family:'DejaVu Sans','Liberation Sans',Arial,sans-serif;color:#1a202c}
.hd{background:#0b1f3a;color:#fff;padding:26px 34px 18px;border-bottom:6px solid #e53e3e}
.hd h1{font-size:30px;letter-spacing:1px;font-weight:800}
.hd .sub{font-size:16px;color:#cbd5e0;margin-top:6px;letter-spacing:.5px}
.wrap{padding:22px 26px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.tile{background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.15);padding:14px 16px;border-top:4px solid #0b1f3a}
.mu{font-size:19px;font-weight:800}
.meta{font-size:13px;color:#4a5568;margin:3px 0 9px}
.row{display:flex;justify-content:space-between;font-size:14px;padding:3px 0;border-bottom:1px dashed #e2e8f0}
.row b{font-weight:700}
.lab{display:inline-block;margin:9px 0 7px;font-size:12px;font-weight:700;color:#fff;padding:3px 9px;border-radius:12px}
.play{background:#fffbea;border:2px solid #e53e3e;border-radius:8px;padding:8px 10px;font-size:17px;font-weight:800;color:#9b2c2c}
.play.none{background:#f7fafc;border-color:#a0aec0;color:#4a5568;font-weight:600}
.why{font-size:12.5px;color:#4a5568;margin-top:7px;line-height:1.35}
.par{background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.15);padding:14px 18px;margin-bottom:14px;display:flex;gap:16px}
.rk{font-size:30px;font-weight:900;color:#0b1f3a;width:44px}
.bd{min-width:62px;height:62px;border-radius:50%;color:#fff;font-weight:900;font-size:26px;display:flex;align-items:center;justify-content:center}
.pr{font-size:22px;font-weight:800;margin-bottom:6px}
.leg{font-size:15px;margin:4px 0}
.leg small{display:block;color:#4a5568;font-size:12.5px;margin-left:18px}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.15)}
th{background:#0b1f3a;color:#fff;text-align:left;padding:10px 12px;font-size:14px}
td{padding:9px 12px;font-size:14px;border-bottom:1px solid #e2e8f0}
tr:nth-child(even) td{background:#f7fafc}
.ft{font-size:13px;color:#2d3748;padding:14px 26px 22px;line-height:1.55}
.ft .note{color:#718096;font-size:12px;margin-top:6px}
"""


def _page(title, sub, body, foot):
    return (f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body><div class='hd'><h1>{_e(title)}</h1>"
            f"<div class='sub'>{_e(sub)}</div></div><div class='wrap'>{body}</div><div class='ft'>{foot}</div></body></html>")


def _foot(sport=None):
    from . import dailypicks as D
    lines = []
    try:
        bd = D.sport_breakdown(ST)
        want = D.NAME.get(sport) if sport else None
        for ln in bd.splitlines():
            if not want or f" {want}:" in ln:
                lines.append(_e(ln))
    except Exception:  # noqa: BLE001
        pass
    if sport in (None, "cfb"):
        try:
            from . import cfb_props_shadow as C
            pl = C.card_line()
            if pl:
                lines.append(_e(pl))
        except Exception:  # noqa: BLE001
            pass
    lines.append("<div class='note'>Tiers: A passed every veto · B backtested paper signal · C model lean · 🎯 Master Extreme "
                 "(tracking). Ranked by leg strength, not a probability. No win % unless a type has 100+ graded picks and "
                 "is profitable. Paper tracking only — not real-money advice. NBA excluded until the Oct 27 review.</div>")
    return "<br>".join(lines)


def _sub(sport=None, extra=""):
    d = datetime.now(CT).strftime("%A, %B %-d, %Y").upper()
    return f"{d} // {LEAGUE.get(sport, 'ALL SPORTS') if sport else 'ALL SPORTS'}" + (f" · {extra}" if extra else "")


def _render(htm: str, name: str) -> Path | None:
    OUT.mkdir(parents=True, exist_ok=True)
    h, p = OUT / f"{name}.html", OUT / f"{name}.png"
    h.write_text(htm)
    code = ("import sys\nfrom playwright.sync_api import sync_playwright\nwith sync_playwright() as pw:\n"
            " b=pw.chromium.launch(args=['--no-sandbox'])\n pg=b.new_page(viewport={'width':1080,'height':800},device_scale_factor=1.5)\n"
            " pg.goto('file://'+sys.argv[1]); pg.screenshot(path=sys.argv[2], full_page=True); b.close()\n")
    try:
        subprocess.run(["nice", "-n", "10", str(PYW), "-c", code, str(h), str(p)], check=True, timeout=90,
                       env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(PWB)}, capture_output=True)
        return p if p.exists() and p.stat().st_size > 1000 else None
    except Exception as exc:  # noqa: BLE001
        log.warning("card render %s failed: %s", name, str(exc)[:200])
        return None


# ------------------------------------------------------------------ data
def _card():
    from . import dailypicks as D
    return D._jload(D._dir(ST) / D.CARD, None)


def _plan_legs(card, sport=None):
    """Same legs as the /card plan (Master extremes own their totals) — so images match the text cards."""
    from . import dailypicks as D
    mx = [x for x in D.master_extremes() if x["sport"] not in D.TG_EXCLUDE]
    keys = {D._gkey(x) for x in mx}
    legs = [x for x in card["ml"] + card["ou"] if x["sport"] not in D.TG_EXCLUDE
            and not (x["market"] == "total" and D._gkey(x) in keys)]
    legs = mx + sorted(legs, key=lambda x: -x["strength"])
    return [x for x in legs if not sport or x["sport"] == sport]


def tiles_html(legs, sport=None, title="MARV PREDICT BET CARD"):
    by = {}
    for x in legs:
        by.setdefault((x["sport"], x["home"], x["away"]), []).append(x)
    cells = []
    for (sp, home, away), ls in list(by.items())[:12]:
        st = ls[0].get("start")
        b, m = _bov(sp, home, away), _marv(sp, home, away)
        sps, tot = _spread_txt(b, home, away)
        mm, mt = _marv_txt(m)
        net = _network(sp, home, away, st)
        lead = max(ls, key=lambda x: x["strength"])
        bd, col, lab = _label(lead)
        plays = " + ".join(_pick(x) for x in ls)
        cells.append(
            f"<div class='tile'><div class='mu'>{_e(away)} @ {_e(home)}</div>"
            f"<div class='meta'>{_e(LEAGUE.get(sp, sp))} · {_e(_when(st))}{(' · ' + _e(net)) if net else ''}</div>"
            f"<div class='row'><span>Bovada spread</span><b>{_e(sps)}</b></div>"
            f"<div class='row'><span>Bovada O/U</span><b>{_e(tot)}</b></div>"
            f"<div class='row'><span>Marv margin</span><b>{_e(mm)}</b></div>"
            f"<div class='row'><span>Marv total</span><b>{_e(mt)}</b></div>"
            f"<span class='lab' style='background:{col}'>{_e(bd)} · {_e(lab)}</span>"
            f"<div class='play'>PLAY: {_e(plays)}</div><div class='why'>{_e(_reason(lead, m))}</div></div>")
    body = f"<div class='grid'>{''.join(cells)}</div>" if cells else "<p>No qualifying plays on today's card.</p>"
    return _page(title, _sub(sport, f"{len(by)} game(s) with a play"), body, _foot(sport))


def parlays_html(parlays, sport=None, title="MARV PREDICT · RANKED 2-LEG PARLAYS"):
    blocks = []
    for i, p in enumerate(parlays, 1):
        tiers = [l.get("tier") for l in p["legs"]]
        worst = "LEAN" if "LEAN" in tiers else ("SIGNAL" if "SIGNAL" in tiers else "HIGH")
        bd, col = BADGE[worst]
        legs = "".join(f"<div class='leg'>• <b>{_e(_pick(l))}</b> — {_e(l.get('game'))} [{_e(LEAGUE.get(l['sport'], l['sport']))}] "
                       f"· {_e(_when(l.get('start')))}<small>{_e(_reason(l, _marv(l['sport'], l['home'], l['away'])))}</small></div>"
                       for l in p["legs"])
        blocks.append(f"<div class='par'><div class='rk'>{i}</div><div class='bd' style='background:{col}'>{bd}</div>"
                      f"<div><div class='pr'>{int(p['price']):+d} · {len(p['legs'])} legs</div>{legs}</div></div>")
    body = "".join(blocks) or "<p>No 2-leg parlay today (need 2+ qualifying games).</p>"
    return _page(title, _sub(sport), body, _foot(sport))


def slate_html(sport, days=7):
    now = datetime.now(timezone.utc)
    P = [v for v in _j(ST / "predictions.json", {}).values() if v.get("sport") == sport
         and now < datetime.fromisoformat(v["start"]) <= now + timedelta(days=days)]
    P.sort(key=lambda v: v["start"])
    rows = []
    for v in P[:40]:
        b = _bov(sport, v["home"], v["away"])
        sps, tot = _spread_txt(b, v["home"], v["away"])
        mm, mt = _marv_txt(v)
        side = f"{mm} (Bovada {sps})"
        ou = "—"
        try:
            if tot != "—":
                d = float(v["model_total"]) - float(tot)
                ou = f"{mt} vs {tot} → {'Over' if d > 0 else 'Under'} lean ({d:+.1f})" if abs(d) >= 0.5 else f"{mt} vs {tot} (no lean)"
            else:
                ou = mt
        except Exception:  # noqa: BLE001
            pass
        rows.append(f"<tr><td><b>{_e(v['away'])} @ {_e(v['home'])}</b><br><small>{_e(_when(v['start']))}</small></td>"
                    f"<td>{_e(side)}</td><td>{_e(ou)}</td></tr>")
    body = ("<table><tr><th>Matchup</th><th>ML / side lean (Marv margin)</th><th>O/U projection</th></tr>"
            + "".join(rows) + "</table>") if rows else "<p>No upcoming games in Marv's slate.</p>"
    return _page(f"MARV PREDICT · {LEAGUE.get(sport, sport)} SLATE", _sub(sport, "leans are model output, not plays"),
                 body, _foot(sport))


# ------------------------------------------------------------------ entry points
def images(kind: str, sport: str | None = None) -> list[str]:
    """kind: parlay (/parlay [league]) | plan (/card) | slate. Returns PNG paths ([] when off/failed/NBA)."""
    if not on() or sport == "nba":
        return []
    try:
        from . import dailypicks as D
        card = _card()
        if not card:
            return []
        tag = f"{kind}_{sport or 'all'}"
        out = []
        if kind == "parlay":
            if sport:  # league card: same legs as the /card plan (Master extremes own their totals)
                ps = D._combos(_plan_legs(card, sport), 2, D.TG_TOP)
            else:
                ps = D._cap_reuse(sorted([p for s, v in card["parlays"].items() if s not in D.TG_EXCLUDE for p in v],
                                         key=lambda p: (-p["score"], -p["price"])), 8)
            legs = [x for x in card["ml"] + card["ou"] if x["sport"] not in D.TG_EXCLUDE and (not sport or x["sport"] == sport)]
            if sport:
                legs = _plan_legs(card, sport)
            out += [_render(parlays_html(ps, sport), tag + "_parlays"), _render(tiles_html(legs, sport), tag + "_tiles")]
            if sport in ("nfl", "wnba"):
                out.append(_render(slate_html(sport), tag + "_slate"))
        elif kind == "plan":
            legs = _plan_legs(card, sport)
            ps = D._combos(legs, 2, D.TG_TOP)
            out += [_render(parlays_html(ps, sport, "MARV PREDICT · DAILY ACTION PLAN · 2-LEG PARLAYS"), tag + "_parlays"),
                    _render(tiles_html(legs, sport), tag + "_tiles")]
        elif kind == "slate" and sport:
            out.append(_render(slate_html(sport), tag))
        return [str(p) for p in out if p]
    except Exception:  # noqa: BLE001
        log.exception("card images failed")
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    k = sys.argv[1] if len(sys.argv) > 1 else "parlay"
    sp = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(images(k, sp)))
