"""OANDA v20 REST client for the PRACTICE account only (the host is hard-coded; there is no live-host switch anywhere in fxbot).

Used by fxbot.live (our own practice runner). Every order carries clientExtensions.tag = TAG so the runner only ever touches its own trades;
the practice account is shared with the owner's fx-experts bot. Token and account id come from the environment and are never logged.
"""

import os
import time

from .data import PRACTICE, token

TAG = "marv-fx"


class Oanda:
    def __init__(self, account_id: str | None = None, session=None):
        import requests
        self.s = session or requests.Session()
        self._acct = account_id or os.environ.get("OANDA_ACCOUNT_ID", "").strip() or None

    def _req(self, method: str, path: str, tries: int = 4, **kw) -> dict:
        tok = token()
        if not tok:
            raise RuntimeError("no OANDA token: add OANDA_API_TOKEN (practice) to /opt/marv-bot/.env")
        h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept-Datetime-Format": "RFC3339"}
        for k in range(tries):
            r = self.s.request(method, PRACTICE + path, headers=h, timeout=30, **kw)
            if r.status_code == 429 or r.status_code >= 500 or (r.status_code == 401 and k < 2):   # 401s are intermittent on the shared practice token: retry twice before raising
                time.sleep(2 ** k)
                continue
            if r.status_code >= 400:
                raise OandaError(r.status_code, r.text[:500])
            return r.json()
        raise OandaError(r.status_code, r.text[:500])

    @property
    def account(self) -> str:
        if not self._acct:
            accts = self._req("GET", "/v3/accounts").get("accounts", [])
            if len(accts) != 1:
                raise RuntimeError(f"{len(accts)} OANDA accounts on this token: set OANDA_ACCOUNT_ID in /opt/marv-bot/.env")
            self._acct = accts[0]["id"]
        return self._acct

    # ---------------- reads
    def summary(self) -> dict:
        return self._req("GET", f"/v3/accounts/{self.account}/summary")["account"]

    def candles(self, instrument: str, granularity: str, count: int) -> list:
        return self._req("GET", f"/v3/instruments/{instrument}/candles",
                         params={"granularity": granularity, "price": "BA", "count": count}).get("candles", [])

    def price(self, instrument: str) -> tuple[float, float]:
        p = self._req("GET", f"/v3/accounts/{self.account}/pricing", params={"instruments": instrument})["prices"][0]
        return float(p["bids"][0]["price"]), float(p["asks"][0]["price"])

    def open_trades(self) -> list:
        return self._req("GET", f"/v3/accounts/{self.account}/openTrades").get("trades", [])

    def trade(self, trade_id: str) -> dict:
        return self._req("GET", f"/v3/accounts/{self.account}/trades/{trade_id}")["trade"]

    # ---------------- orders (practice)
    def market(self, instrument: str, units: int, sl_price: str, tp_price: str, client_id: str) -> dict:
        """Market order with the stop-loss and take-profit placed AT OANDA on fill. units > 0 buys, < 0 sells. Returns the fill
        transaction (tradeOpened id, price) or raises OandaError / returns {'rejected': reason}."""
        body = {"order": {"type": "MARKET", "instrument": instrument, "units": str(int(units)), "timeInForce": "FOK",
                          "positionFill": "OPEN_ONLY",
                          "stopLossOnFill": {"price": sl_price, "timeInForce": "GTC"},
                          "takeProfitOnFill": {"price": tp_price, "timeInForce": "GTC"},
                          "clientExtensions": {"id": client_id, "tag": TAG, "comment": "marv fx practice"},
                          "tradeClientExtensions": {"id": client_id, "tag": TAG}}}
        js = self._req("POST", f"/v3/accounts/{self.account}/orders", json=body)
        fill = js.get("orderFillTransaction")
        if not fill or not fill.get("tradeOpened"):
            why = (js.get("orderCancelTransaction") or {}).get("reason") or "no fill"
            return {"rejected": why}
        return {"trade_id": fill["tradeOpened"]["tradeID"], "price": float(fill["price"]), "units": int(float(fill["tradeOpened"]["units"]))}

    def close(self, trade_id: str, units: int | None = None) -> dict:
        body = {"units": str(int(units))} if units else {"units": "ALL"}
        js = self._req("PUT", f"/v3/accounts/{self.account}/trades/{trade_id}/close", json=body)
        fill = js.get("orderFillTransaction") or {}
        return {"price": float(fill.get("price", 0) or 0), "pl": float(fill.get("pl", 0) or 0) + float(fill.get("financing", 0) or 0)}

    def set_stop(self, trade_id: str, sl_price: str) -> dict:
        return self._req("PUT", f"/v3/accounts/{self.account}/trades/{trade_id}/orders",
                         json={"stopLoss": {"price": sl_price, "timeInForce": "GTC"}})


class OandaError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"OANDA {status}: {text}")
        self.status = status
