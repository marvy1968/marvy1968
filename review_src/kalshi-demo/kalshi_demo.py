"""Kalshi DEMO-only REST client. Refuses any non-demo host. RSA-PSS SHA256 / Ed25519 signing per Kalshi API v2."""
import base64, json, os, time, uuid, requests
from urllib.parse import urlparse
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"
ALLOWED_HOSTS = {"demo-api.kalshi.co", "external-api.demo.kalshi.co"}

class ProductionRefused(RuntimeError): pass

def _dollars_to_cents(val):
    if val is None or val == "":
        return None
    try:
        return max(0, min(100, int(round(float(val) * 100))))
    except (TypeError, ValueError):
        return None

def _normalize_market(m):
    """Fill legacy yes_ask/yes_bid cents from dollar fields when missing."""
    if not isinstance(m, dict):
        return m
    if m.get("yes_ask") in (None, ""):
        cents = _dollars_to_cents(m.get("yes_ask_dollars"))
        if cents is not None:
            m["yes_ask"] = cents
    if m.get("yes_bid") in (None, ""):
        cents = _dollars_to_cents(m.get("yes_bid_dollars"))
        if cents is not None:
            m["yes_bid"] = cents
    return m

class KalshiDemo:
    def __init__(self, key_id=None, key_path=None, base=None):
        self.base = (base or os.environ.get("KALSHI_DEMO_BASE") or DEMO_BASE).rstrip("/")
        host = urlparse(self.base).hostname or ""
        if host not in ALLOWED_HOSTS or "demo" not in host:
            raise ProductionRefused(f"host {host!r} is not a Kalshi DEMO host - refused")
        self.key_id = key_id or os.environ.get("KALSHI_DEMO_KEY_ID")
        key_path = key_path or os.environ.get("KALSHI_DEMO_KEY_PATH")
        self.key = None
        if self.key_id and key_path and os.path.exists(key_path):
            with open(key_path, "rb") as f:
                self.key = serialization.load_pem_private_key(f.read(), password=None)
        self.s = requests.Session()

    @property
    def has_key(self): return bool(self.key_id and self.key)

    def _hdr(self, method, path):
        ts = str(int(time.time() * 1000))
        sp = urlparse(self.base + path).path
        msg = (ts + method + sp).encode()
        if isinstance(self.key, Ed25519PrivateKey):
            sig = self.key.sign(msg)
        else:
            sig = self.key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                                hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(), "Content-Type": "application/json"}

    def _req(self, method, path, auth=False, **kw):
        h = self._hdr(method, path) if auth else {}
        r = self.s.request(method, self.base + path, headers=h, timeout=15, **kw)
        r.raise_for_status()
        return r.json()

    def markets(self, **params):
        data = self._req("GET", "/markets", params=params)
        if isinstance(data, dict) and isinstance(data.get("markets"), list):
            data["markets"] = [_normalize_market(m) for m in data["markets"]]
        return data

    def events(self, **params): return self._req("GET", "/events", params=params)
    def orderbook(self, ticker): return self._req("GET", f"/markets/{ticker}/orderbook")
    def balance(self): return self._req("GET", "/portfolio/balance", auth=True)
    def positions(self): return self._req("GET", "/portfolio/positions", auth=True)

    def place_order(self, ticker, side, count, limit_cents, meta=None):
        """2026-10-31 kalfix (Marvin): IOC first; if only part fills, the remainder rests as a limit at the SAME price
        (good_till_canceled + expiration_ts = REST_S, default 300 s) and sweep_resting() cancels it after that.
        Logs intended vs filled. Off: env KALSHI_REST_PARTIAL=off | data/KALSHI_REST_PARTIAL_OFF (old IOC-only)."""
        r = self._place_ioc(ticker, side, count, limit_cents)
        try:
            filled = float(r.get("fill_count") or 0)
        except (TypeError, ValueError):
            filled = 0.0
        r["intended_count"] = int(count); r["filled_ioc"] = filled
        rem = int(count) - int(filled)
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        on = os.environ.get("KALSHI_REST_PARTIAL", "on").lower() not in ("off", "0", "false", "no") and not os.path.exists(os.path.join(d, "KALSHI_REST_PARTIAL_OFF"))
        if rem <= 0 or not on:
            return r
        rest_s = int(os.environ.get("KALSHI_REST_S", "300")); exp = int(time.time()) + rest_s
        book_px = int(limit_cents) if side != "no" else 100 - int(limit_cents)
        body = {"ticker": ticker, "client_order_id": str(uuid.uuid4()), "side": "ask" if side == "no" else "bid",
                "count": f"{rem}.00", "price": f"{book_px / 100.0:.4f}", "time_in_force": "good_till_canceled",
                "expiration_ts": exp, "self_trade_prevention_type": "taker_at_cross"}
        try:
            rr = self._req("POST", "/portfolio/events/orders", auth=True, json=body)
            r["rest"] = {"order_id": rr.get("order_id"), "count": rem, "price_c": int(limit_cents), "expires_ts": exp,
                         "fill_count": rr.get("fill_count")}
            st = {"ts": int(time.time()), "ticker": ticker, "side": side, "parent_order_id": r.get("order_id"), **r["rest"]}
            with open(os.path.join(d, "resting_orders.jsonl"), "a") as f: f.write(json.dumps(dict(st, ev="placed")) + "\n")
            if meta is not None:   # companion demo_log row so grader.py joins the remainder's fills to the pick
                row = {k: meta.get(k) for k in ("date", "pick", "ticker", "side", "ask", "fair_c", "edge_c", "me_alert_id", "me_type",
                                                "me_game_key", "src", "source", "prop_player", "prop_market", "event_id", "kalshi_line",
                                                "bet_type", "lane", "prob_src") if k in meta}
                from datetime import datetime as _dt
                row.update(ts=_dt.now().astimezone().isoformat(), key=str(meta.get("key")) + ":rest", mode="demo", rest_of=r.get("order_id"),
                           order=dict(rr, resting=True, expires_ts=exp), intended_count=rem)
                with open(os.path.join(d, "demo_log.jsonl"), "a") as f: f.write(json.dumps(row) + "\n")
        except Exception as ex:
            r["rest_error"] = str(ex)[:160]
        return r

    def cancel_order(self, order_id):
        return self._req("DELETE", f"/portfolio/events/orders/{order_id}", auth=True)

    def sweep_resting(self, max_age_s=None):
        """Cancel resting remainders past expiry (exchange expiry is primary; this is the backstop) and log filled vs intended."""
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"); p = os.path.join(d, "resting_orders.jsonl")
        if not os.path.exists(p): return []
        rows = [json.loads(l) for l in open(p) if l.strip()]
        done = {x.get("order_id") for x in rows if x.get("ev") != "placed"}
        now = int(time.time()); out = []
        for x in rows:
            if x.get("ev") != "placed" or x.get("order_id") in done or now < int(x.get("expires_ts") or 0): continue
            rec = {k: x.get(k) for k in ("order_id", "parent_order_id", "ticker", "side", "count", "price_c")}
            try:
                cr = self.cancel_order(x["order_id"]); red = float(cr.get("reduced_by") or 0)
                rec.update(ev="cancelled", reduced_by=red, rest_filled=round(float(x["count"]) - red, 2))
            except Exception as ex:
                code = getattr(getattr(ex, "response", None), "status_code", None)
                rec.update(ev="expired_or_filled", http=code)    # already expired/filled on the exchange; grader reads real fills
            rec["swept_ts"] = now; out.append(rec)
            with open(p, "a") as f: f.write(json.dumps(rec) + "\n")
        return out

    def _place_ioc(self, ticker, side, count, limit_cents):
        """Buy YES (side 'yes': book side bid at limit) or buy NO (side 'no', 2026-10-15: book side ask = sell YES at
        100 - limit; Kalshi fills it as a NO position, fill side 'no'). limit_cents is the price paid per contract."""
        price_cents = int(limit_cents)
        book_px = price_cents if side != "no" else 100 - price_cents
        price_dollars = f"{book_px / 100.0:.4f}"
        body = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "side": "ask" if side == "no" else "bid",
            "count": f"{int(count)}.00",
            "price": price_dollars,
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
        }
        self.ensure_shard_funds(ticker, price_cents * int(count) + 200)  # + fee buffer
        return self._req("POST", "/portfolio/events/orders", auth=True, json=body)

    def ensure_shard_funds(self, ticker, need_cents):
        """Exchange sharding: collateral must sit on the market's shard (MLB/NBA/tennis = 3).
        Move demo funds from shard 0 if the target shard is short. 404 insufficient_shard_balance otherwise."""
        try:
            idx = int((self._req("GET", f"/markets/{ticker}").get("market") or {}).get("exchange_index") or 0)
        except Exception:
            return
        if idx == 0:
            return
        bd = {int(b.get("exchange_index", 0)): round(float(b.get("balance") or 0) * 100)
              for b in (self.balance().get("balance_breakdown") or [])}
        short = int(need_cents) - bd.get(idx, 0)
        if short <= 0:
            return
        amt = min(short, bd.get(0, 0))
        if amt <= 0:
            return
        self._req("POST", "/portfolio/intra_exchange_instance_transfer", auth=True, json={
            "client_transfer_id": str(uuid.uuid4()), "source": "event_contract", "destination": "event_contract",
            "amount": int(amt) * 100, "source_exchange_shard": 0, "destination_exchange_shard": idx})
        for _ in range(10):  # async; wait until funds land
            time.sleep(1)
            bd2 = {int(b.get("exchange_index", 0)): round(float(b.get("balance") or 0) * 100)
                   for b in (self.balance().get("balance_breakdown") or [])}
            if bd2.get(idx, 0) >= need_cents:
                return


# ---- 2026-11-10 LIVE hook (Marvin: "Build both and push go-live to around Nov 22"). NO-OP unless LIVE=1. ----
# After the demo order (success or error), an allowlisted lane is mirrored ONCE to the separate PROD client in
# live/kalshi_prod.py (this demo class still refuses prod hosts). The demo result/exception is returned/raised unchanged.
# live_meta: optional record for callers that do not pass meta (meta also drives demo rest-row logging, so it is not reused).
# Rollback: cp kalshi_demo.py.bak-20261110-live kalshi_demo.py
_LIVE_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live", "live.env")


def _live_on():
    ev = os.environ.get("LIVE")
    if ev in ("0", "1"): return ev == "1"
    try:
        return any(l.strip() == "LIVE=1" for l in open(_LIVE_ENV))
    except OSError:
        return False


def _live_hook_mirror(ticker, side, limit_cents, meta):
    try:
        import importlib.util as _ilu
        _sp = _ilu.spec_from_file_location("kalshi_live_hook", os.path.join(os.path.dirname(os.path.abspath(__file__)), "live", "kalshi_hook.py"))
        _m = _ilu.module_from_spec(_sp); _sp.loader.exec_module(_m)
        return _m.mirror(ticker, side, limit_cents, meta)
    except Exception as ex:
        print("LIVE hook load error", type(ex).__name__, str(ex)[:120])


_demo_place_order = KalshiDemo.place_order


def _place_order_with_live(self, ticker, side, count, limit_cents, meta=None, live_meta=None):
    try:
        return _demo_place_order(self, ticker, side, count, limit_cents, meta=meta)
    finally:
        if _live_on():
            _live_hook_mirror(ticker, side, limit_cents, live_meta if live_meta is not None else meta)


KalshiDemo.place_order = _place_order_with_live
KalshiDemo.LIVE_HOOK = True
