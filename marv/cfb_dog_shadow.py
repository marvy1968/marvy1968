"""CFB pregame ML underdog +200..+299 SHADOW tracker (Marvin 2026-10-16).

Tracking only: no stake, no Telegram, no Kalshi/Polymarket orders.
Sources Bovada moneylines already cached in state/bovada_prices.json (written by the edge board /
parlay price save — 0 extra Odds API credits). Logs each dog once, refreshes the pregame/close
price from later Bovada snapshots, grades from ESPN finals (free), flat 1u W-L / ROI.
At 100 graded picks: flag for review (summary.meets_review_bar / line text).

Run: python -m marv.cfb_dog_shadow [--report]
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ST = Path(os.environ.get("MARV_STATE", ROOT / "state"))
OUTD = ST / "cfb_dog_shadow"
LEDGER = OUTD / "ledger.jsonl"
SUMMARY = OUTD / "summary.json"

DOG_LO, DOG_HI = 200, 299
REVIEW_N = 100
UA = {"User-Agent": "Mozilla/5.0 (marv-cfb-dog-shadow)"}


def payout(a: float) -> float:
    a = float(a)
    return a / 100.0 if a > 0 else 100.0 / -a


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9
    at, bt = set(na.split()), set(nb.split())
    if not at or not bt:
        return 0.0
    return len(at & bt) / max(len(at), len(bt))


def _key(away: str, home: str, start: str) -> str:
    day = (start or "")[:10]
    return f"cfb:{_norm(away)}@{_norm(home)}:{day}"


def _ledger() -> dict:
    out = {}
    if LEDGER.exists():
        for ln in LEDGER.read_text().splitlines():
            try:
                r = json.loads(ln)
                out[r["key"]] = r
            except Exception:
                pass
    return out


def _write_ledger(rows: dict) -> None:
    OUTD.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for k in sorted(rows):
            f.write(json.dumps(rows[k], default=str) + "\n")
    tmp.replace(LEDGER)


def _parse_start(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _bovada_cfb() -> list[dict]:
    path = ST / "bovada_prices.json"
    if not path.exists():
        return []
    try:
        blob = json.loads(path.read_text())
    except Exception:
        return []
    block = blob.get("cfb") or {}
    events = block.get("events") or []
    at = block.get("at")
    for e in events:
        e["_prices_at"] = at
    return events


def _dogs_from_event(e: dict) -> list[tuple[str, str, float]]:
    """[(side, team_name, american_price), ...] underdogs in +200..+299."""
    ml = e.get("ml") or {}
    out = []
    for side, team in (("home", e.get("home")), ("away", e.get("away"))):
        px = ml.get(side)
        if px is None:
            continue
        try:
            p = float(px)
        except (TypeError, ValueError):
            continue
        if DOG_LO <= p <= DOG_HI and team:
            out.append((side, str(team), p))
    return out


def ingest(now: datetime | None = None) -> dict:
    """Log new +200..+299 CFB ML dogs from cached Bovada; refresh close_price while pregame."""
    now = now or datetime.now(timezone.utc)
    led = _ledger()
    events = _bovada_cfb()
    stats = {"events": len(events), "new": 0, "refreshed": 0, "skipped_started": 0}
    for e in events:
        start = _parse_start(e.get("start"))
        away, home = e.get("away") or "", e.get("home") or ""
        if not away or not home or start is None:
            continue
        dogs = _dogs_from_event(e)
        if not dogs:
            # still refresh close on already-logged rows for this game if pregame
            k0 = _key(away, home, e.get("start") or "")
            if k0 in led and led[k0].get("result") is None and start > now:
                ml = e.get("ml") or {}
                side = led[k0].get("side")
                px = ml.get(side)
                if px is not None:
                    try:
                        p = float(px)
                    except (TypeError, ValueError):
                        p = None
                    if p is not None:
                        led[k0]["close_price"] = p
                        led[k0]["close_at"] = e.get("_prices_at") or now.isoformat()
                        led[k0]["price_source"] = "bovada_close"
                        stats["refreshed"] += 1
            continue
        if start <= now:
            stats["skipped_started"] += 1
            continue
        for side, team, price in dogs:
            k = _key(away, home, e.get("start") or "")
            # one dog per game (the +200..+299 side); if both somehow qualify, keep first logged
            if k in led:
                if led[k].get("result") is None and start > now:
                    led[k]["close_price"] = price
                    led[k]["close_at"] = e.get("_prices_at") or now.isoformat()
                    led[k]["price_source"] = "bovada_close"
                    stats["refreshed"] += 1
                continue
            led[k] = {
                "key": k,
                "sport": "cfb",
                "market": "h2h",
                "away": away,
                "home": home,
                "start": e.get("start"),
                "side": side,
                "pick": team,
                "price": price,
                "entry_price": price,
                "close_price": price,
                "close_at": e.get("_prices_at") or now.isoformat(),
                "price_source": "bovada",
                "logged_at": now.isoformat(),
                "result": None,
                "profit": None,
                "graded_at": None,
                "note": "tracking-only CFB ML dog +200..+299 · flat 1u · no Telegram / no orders",
            }
            stats["new"] += 1
    if stats["new"] or stats["refreshed"]:
        _write_ledger(led)
    return stats


def _espn_finals(days: int = 4) -> list[dict]:
    """Finished CFB games from ESPN scoreboard (free), last `days` calendar days UTC."""
    now = datetime.now(timezone.utc)
    out = []
    for i in range(days + 1):
        d = (now - timedelta(days=i)).strftime("%Y%m%d")
        url = ("https://site.api.espn.com/apis/site/v2/sports/football/college-football/"
               f"scoreboard?dates={d}&groups=80&limit=300")
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                js = json.loads(r.read())
        except Exception as ex:
            log.debug("espn cfb %s: %s", d, ex)
            continue
        for e in js.get("events") or []:
            comp = (e.get("competitions") or [{}])[0]
            st = ((comp.get("status") or {}).get("type") or {})
            if not st.get("completed") and (st.get("state") or "") != "post":
                continue
            teams = {c.get("homeAway"): c for c in (comp.get("competitors") or [])}
            h, a = teams.get("home") or {}, teams.get("away") or {}
            try:
                hs = float(h.get("score"))
                as_ = float(a.get("score"))
            except (TypeError, ValueError):
                continue
            out.append({
                "home": (h.get("team") or {}).get("displayName") or "",
                "away": (a.get("team") or {}).get("displayName") or "",
                "hs": hs, "as": as_,
                "start": e.get("date") or comp.get("date"),
                "id": e.get("id"),
            })
    return out


def grade(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    led = _ledger()
    pending = [r for r in led.values() if r.get("result") is None]
    if not pending:
        return {"graded": 0, "pending": 0}
    finals = _espn_finals(5)
    n = 0
    for r in pending:
        start = _parse_start(r.get("start"))
        if start is None or start > now - timedelta(hours=2):
            # allow grade only after expected kickoff + buffer; ESPN may still be live
            if start is None or start > now:
                continue
        g = next((x for x in finals
                  if _sim(x["home"], r["home"]) >= 0.75 and _sim(x["away"], r["away"]) >= 0.75), None)
        if g is None:
            continue
        # ML: pick wins if that side scored more (CFB no draw)
        pick_home = r.get("side") == "home"
        won = (g["hs"] > g["as"]) if pick_home else (g["as"] > g["hs"])
        pushed = g["hs"] == g["as"]
        px = float(r.get("close_price") if r.get("close_price") is not None else r["price"])
        if pushed:
            r["result"], r["profit"] = "push", 0.0
        elif won:
            r["result"], r["profit"] = "win", round(payout(px), 4)
        else:
            r["result"], r["profit"] = "loss", -1.0
        r["graded_at"] = now.isoformat()
        r["final"] = {"home": g["home"], "away": g["away"], "hs": g["hs"], "as": g["as"], "espn_id": g["id"]}
        r["grade_price"] = px
        n += 1
    if n:
        _write_ledger(led)
    return {"graded": n, "pending": len(pending) - n}


def summary() -> dict:
    led = _ledger()
    rows = list(led.values())
    graded = [r for r in rows if r.get("result") in ("win", "loss")]
    pushes = [r for r in rows if r.get("result") == "push"]
    pending = [r for r in rows if r.get("result") is None]
    w = sum(r["result"] == "win" for r in graded)
    lo = sum(r["result"] == "loss" for r in graded)
    pnl = sum(float(r.get("profit") or 0) for r in graded)
    n = len(graded)
    roi = round(100.0 * pnl / n, 1) if n else None
    review = n >= REVIEW_N
    line = (f"CFB dog +200..+299 (tracking-only, flat 1u): {w}-{lo}"
            + (f" ({roi:+.1f}% ROI, n={n})" if n else " (no graded yet)")
            + (f", {len(pending)} pending" if pending else "")
            + (f", {len(pushes)} push" if pushes else "")
            + (" · ✅ 100 graded — FLAG FOR REVIEW" if review else ""))
    s = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "label": "CFB ML underdog +200..+299",
        "tracking_only": True,
        "stake": "flat 1u (shadow — no real stake)",
        "price_band": [DOG_LO, DOG_HI],
        "W": w, "L": lo, "push": len(pushes), "pending": len(pending),
        "n_graded": n, "roi_pct": roi, "pnl_u": round(pnl, 2),
        "meets_review_bar": review,
        "review_bar": f"{REVIEW_N}+ graded picks → flag for Marvin review",
        "line": line,
        "note": "Bovada pregame ML from state/bovada_prices.json (0 Odds API credits); close=last pregame Bovada; ESPN finals",
    }
    OUTD.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(s, indent=1))
    return s


def record_line(state_dir=None) -> str:
    """One-line status for /reportcard / daily report."""
    p = Path(state_dir or ST) / "cfb_dog_shadow" / "summary.json"
    if p.exists():
        try:
            return (json.loads(p.read_text()) or {}).get("line") or ""
        except Exception:
            pass
    return summary().get("line") or ""


def run() -> dict:
    t0 = time.time()
    ing = ingest()
    gr = grade()
    s = summary()
    return {"ingest": ing, "grade": gr, "summary": s, "secs": round(time.time() - t0, 2)}


if __name__ == "__main__":
    if "--report" in sys.argv:
        print(json.dumps(summary(), indent=1))
    else:
        print(json.dumps(run(), indent=1, default=str))
