"""Minimal OANDA v20 REST client (practice by default). Docs: https://developer.oanda.com/rest-live-v20/
Write calls are refused unless the environment is 'practice' or FX_ALLOW_LIVE=YES."""
import os, time, json
import requests
import pandas as pd

HOSTS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}

class OandaError(RuntimeError):
    pass

class Oanda:
    def __init__(self, token, account, env="practice", dry_run=False, min_interval=0.06):
        if env not in HOSTS: raise ValueError("env must be practice or live")
        if env == "live" and os.environ.get("FX_ALLOW_LIVE") != "YES":
            raise OandaError("live environment refused (set FX_ALLOW_LIVE=YES only with explicit approval)")
        self.base, self.account, self.env, self.dry = HOSTS[env], account, env, dry_run
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                               "Accept-Datetime-Format": "RFC3339"})
        self.min_interval, self._last = min_interval, 0.0
        self.writes = []   # log of write calls (and of would-be writes in dry-run)

    def _req(self, method, path, params=None, body=None):
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0: time.sleep(wait)
        for attempt in range(4):
            self._last = time.time()
            r = self.s.request(method, self.base + path, params=params, data=json.dumps(body) if body is not None else None, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1.5 * (attempt + 1)); continue
            if r.status_code >= 400:
                raise OandaError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
            return r.json() if r.text else {}
        raise OandaError(f"{method} {path} failed after retries")

    def _write(self, method, path, body=None):
        self.writes.append({"method": method, "path": path, "body": body})
        if self.dry: return {"dryRun": True}
        return self._req(method, path, body=body)

    # ---- reads -----------------------------------------------------------
    def accounts(self): return self._req("GET", "/v3/accounts")["accounts"]
    def summary(self): return self._req("GET", f"/v3/accounts/{self.account}/summary")["account"]
    def instruments(self, names=None):
        p = {"instruments": ",".join(names)} if names else None
        return {i["name"]: i for i in self._req("GET", f"/v3/accounts/{self.account}/instruments", params=p)["instruments"]}
    def pricing(self, names):
        js = self._req("GET", f"/v3/accounts/{self.account}/pricing", params={"instruments": ",".join(names)})
        out = {}
        for p in js["prices"]:
            if p.get("bids") and p.get("asks"):
                out[p["instrument"]] = (float(p["bids"][0]["price"]), float(p["asks"][0]["price"]), p.get("tradeable", True))
        return out
    def candles(self, instrument, granularity="D", count=None, frm=None, price="M"):
        p = {"granularity": granularity, "price": price, "dailyAlignment": 17, "alignmentTimezone": "America/New_York"}
        if count: p["count"] = min(int(count), 5000)
        if frm: p["from"] = frm
        js = self._req("GET", f"/v3/instruments/{instrument}/candles", params=p)
        rows = []
        for c in js["candles"]:
            m = c["mid"]
            rows.append({"time": pd.Timestamp(c["time"]), "open": float(m["o"]), "high": float(m["h"]), "low": float(m["l"]),
                         "close": float(m["c"]), "volume": int(c.get("volume", 0)), "complete": c["complete"]})
        return pd.DataFrame(rows)
    def open_trades(self): return self._req("GET", f"/v3/accounts/{self.account}/openTrades")["trades"]
    def pending_orders(self): return self._req("GET", f"/v3/accounts/{self.account}/pendingOrders")["orders"]
    def trade(self, tid): return self._req("GET", f"/v3/accounts/{self.account}/trades/{tid}")["trade"]
    def closed_trades(self, count=500):
        return self._req("GET", f"/v3/accounts/{self.account}/trades", params={"state": "CLOSED", "count": count})["trades"]

    # ---- writes ----------------------------------------------------------
    def create_order(self, order): return self._write("POST", f"/v3/accounts/{self.account}/orders", {"order": order})
    def replace_order(self, oid, order): return self._write("PUT", f"/v3/accounts/{self.account}/orders/{oid}", {"order": order})
    def cancel_order(self, oid): return self._write("PUT", f"/v3/accounts/{self.account}/orders/{oid}/cancel")
    def set_stop(self, tid, price): return self._write("PUT", f"/v3/accounts/{self.account}/trades/{tid}/orders", {"stopLoss": {"price": price, "timeInForce": "GTC"}})
    def close_trade(self, tid): return self._write("PUT", f"/v3/accounts/{self.account}/trades/{tid}/close", {"units": "ALL"})
