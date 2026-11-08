"""Polymarket US PUBLIC market-data client (no auth). Read-only GETs to gateway.polymarket.us.
There is deliberately NO order method for production. Optional preprod stub lives in preprod.py (flag off)."""
import json, time, urllib.request
BASE = "https://gateway.polymarket.us"
class PMPublic:
    def __init__(self): self._ev = {}
    def _get(self, path):
        for i in range(3):
            try:
                req = urllib.request.Request(BASE + path, headers={"User-Agent": "marv-polymarket-paper/1.0"})
                with urllib.request.urlopen(req, timeout=20) as r: return json.loads(r.read())
            except Exception:
                if i == 2: raise
                time.sleep(1 + i)
    def league_events(self, league):
        if league not in self._ev:
            out, off = [], 0
            while True:
                d = self._get(f"/v2/leagues/{league}/events?limit=50&offset={off}")
                ev = d.get("events", []) or []; out += ev
                if len(ev) < 50 or off > 500: break
                off += 50
            self._ev[league] = out
        return self._ev[league]
    def event(self, slug):
        """2026-10-23: league list now returns only the winner market; full markets (spreads/totals) come per event."""
        k = "ev:" + slug
        if k not in self._ev:
            d = self._get(f"/v1/events?slug={slug}")
            evs = d.get("events", []) or []
            self._ev[k] = evs[0] if evs else None
        return self._ev[k]
    def bbo(self, slug): return self._get(f"/v1/markets/{slug}/bbo").get("marketData", {})
