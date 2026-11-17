"""News pause for the fx-experts bot (owner 2026-11-11: "add news pause").

Rule (from the 2026-11-11 research note; a guard, not a proven edge):
  * High-impact ForexFactory events for EITHER currency of the pair.
  * No new entries from 30 min before to 60 min after the release; waiting entry orders on that pair are
    cancelled and re-placed after the window if still valid.
  * The US big three (FOMC / Fed rate decision, Non-Farm Payrolls, CPI): 120 min before to 60 min after.
  * Open trades are left alone (broker stops stay where they are).
Calendar: ForexFactory's weekly JSON, cached in the state folder and refreshed at most every 6 hours.
If it cannot be read, the Fed decision days below (14:00 New York, USD) are used so the biggest event
is still covered.
"""
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) fxexp-news/1.0"
BEFORE_MIN, AFTER_MIN = 30, 60
BIG_BEFORE_MIN, BIG_AFTER_MIN = 120, 60
BIG = re.compile(r"FOMC|Federal Funds|Fed Interest Rate|Non-Farm|Nonfarm|\bCPI\b", re.I)
MAX_AGE_H = 6.0
# Same list as fxbot/newsfilter.py (the Fed's published 2026 calendar). Add 2027 when the Fed publishes it.
FOMC = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]


def _ts(s):
    t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def parse_ff(rows):
    """[{"t": UTC ISO, "ccy": "USD", "title": ...}] for high-impact rows of ForexFactory's weekly JSON."""
    out = []
    for r in rows or []:
        if r.get("impact") != "High" or not r.get("date"):
            continue
        try:
            t = _ts(r["date"]).astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue
        out.append({"t": t.isoformat(), "ccy": str(r.get("country", "")).upper(), "title": str(r.get("title", ""))[:60]})
    return out


def fallback_events():
    out = []
    for d in FOMC:
        y, m, dd = map(int, d.split("-"))
        out.append({"t": datetime(y, m, dd, 14, 0, tzinfo=NY).astimezone(timezone.utc).isoformat(), "ccy": "USD", "title": "FOMC decision"})
    return out


def fetch_ff(timeout=15):
    req = urllib.request.Request(FF_URL, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_events(cache_path, now, fetch=fetch_ff, log=print):
    """(events, source). Uses the cache when fresher than MAX_AGE_H, else refetches; falls back to Fed days."""
    p = Path(cache_path)
    cached = None
    if p.exists():
        try:
            cached = json.loads(p.read_text())
        except ValueError:
            cached = None
    fresh = cached and (now - _ts(cached["fetched"])).total_seconds() < MAX_AGE_H * 3600
    if not fresh and fetch is not None:
        try:
            ev = parse_ff(fetch())
            if ev:
                cached = {"fetched": now.isoformat(), "events": ev, "source": "forexfactory"}
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(cached)); tmp.replace(p)
        except Exception as e:  # network / format problems never stop the bot
            log(f"news calendar refresh failed ({type(e).__name__}); using {'cache' if cached else 'Fed-day fallback'}")
    if cached and cached.get("events") and (now - _ts(cached["fetched"])).total_seconds() < 8 * 86400:
        return cached["events"] + fallback_events(), cached.get("source", "cache")
    return fallback_events(), "fallback (Fed decision days)"


def blocked(pair, now, events):
    """'' or the reason when `now` is inside a release window for either currency of the pair."""
    ccys = set(pair.split("_"))
    for e in events:
        if e["ccy"] not in ccys:
            continue
        big = e["ccy"] == "USD" and bool(BIG.search(e["title"]))
        before, after = (BIG_BEFORE_MIN, BIG_AFTER_MIN) if big else (BEFORE_MIN, AFTER_MIN)
        t = _ts(e["t"])
        if t - timedelta(minutes=before) <= now <= t + timedelta(minutes=after):
            return f"news pause: {e['ccy']} {e['title']} at {t.astimezone(NY):%H:%M} New York"
    return ""
