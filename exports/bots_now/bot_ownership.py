#!/usr/bin/env python3
"""
bot_ownership.py  --  per-bot order tagging + ownership ledger for ONE shared
Alpaca PAPER account (added 2026-10-01).

Every VM bot that trades the shared paper account creates an `Ownership`
object with a unique client_order_id prefix (vs-, ms-, orb-, hm-, ...).

Rules enforced here:
  * every order the bot submits carries  <prefix>...  as client_order_id
  * a JSON ledger (~/bots/ownership/<bot>_ledger.json) records the bot's own
    entry / exit orders per symbol; the owned qty is computed from those
    orders' fills (entry fills - own bracket-leg fills - own exit fills)
  * exits are only sent for symbols in the ledger, only if the LIVE broker
    position exists in the expected direction, and qty is capped at
    min(own qty, live qty, qty_available)  ->  an exit can never flip a
    position or close shares that belong to another bot
  * only this bot's own orders / bracket legs are ever cancelled
  * entries are skipped when the account already has a position or an open
    order on that symbol from anyone (Alpaca nets per symbol)

Set BOT_OWNERSHIP_DRY_RUN=1 to log instead of sending POST/DELETE requests.
"""
import os
import json
import time
import uuid
import fcntl
import logging
import datetime as _dt
from contextlib import contextmanager
from pathlib import Path

import requests

LEDGER_DIR = Path(os.getenv("BOT_OWNERSHIP_DIR", "/home/moonrider1968/bots/ownership"))
FOREIGN_PREFIXES = ("ab-", "sw-", "mr-")          # box bots - never ours
TERMINAL = {"filled", "canceled", "cancelled", "expired", "rejected", "replaced", "done_for_day"}
EPS = 1e-9


def norm(sym):
    return str(sym).replace("/", "").upper()


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _f(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def fmt_qty(q):
    q = float(q)
    if abs(q - round(q)) < 1e-9:
        return str(int(round(q)))
    return ("%.9f" % q).rstrip("0").rstrip(".")


class Ownership:
    def __init__(self, bot, prefix, key=None, secret=None, base_url=None,
                 logger=None, session=None, crypto=False, ledger_dir=None):
        if not prefix.endswith("-") or prefix in FOREIGN_PREFIXES:
            raise ValueError("bad prefix %r" % prefix)
        self.bot = bot
        self.prefix = prefix
        self.crypto = crypto
        self.log = logger or logging.getLogger(bot)
        key = key or os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
        secret = secret or os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
        base = (base_url or os.getenv("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets").strip().rstrip("/")
        if base.endswith("/v2"):
            base = base[:-3]
        if "paper-api" not in base:
            raise RuntimeError("bot_ownership: refusing non-paper base url")
        self.base = base + "/v2"
        self.headers = {"APCA-API-KEY-ID": key or "", "APCA-API-SECRET-KEY": secret or "",
                        "Content-Type": "application/json"}
        self.http = session or requests.Session()
        self.dry_run = os.getenv("BOT_OWNERSHIP_DRY_RUN") == "1"
        d = Path(ledger_dir) if ledger_dir else LEDGER_DIR
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / ("%s_ledger.json" % bot)
        self.lockpath = d / ("%s_ledger.lock" % bot)

    # ------------------------------------------------------------ http
    def _req(self, method, path, params=None, body=None):
        if self.dry_run and method in ("POST", "DELETE", "PATCH"):
            self.log.info("[OWNERSHIP DRY-RUN] %s %s %s", method, path, body or params or "")
            if method == "POST" and path == "/orders":
                return 200, dict(body, id="dry-" + uuid.uuid4().hex, status="new", filled_qty="0")
            return 204, {}
        try:
            r = self.http.request(method, self.base + path, headers=self.headers,
                                  params=params, json=body, timeout=10)
            try:
                data = r.json()
            except Exception:
                data = {}
            return r.status_code, data
        except Exception as e:
            self.log.error("[OWNERSHIP] %s %s failed: %s", method, path, e)
            return 0, {}

    # ------------------------------------------------------------ ids
    def new_coid(self, tag=""):
        tag = "".join(c for c in str(tag).lower() if c.isalnum())[:10]
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%m%d%H%M%S")
        coid = "%s%s%s-%s" % (self.prefix, (tag + "-") if tag else "", ts, uuid.uuid4().hex[:8])
        return coid[:48]

    def is_own(self, coid):
        return str(coid or "").startswith(self.prefix)

    # ------------------------------------------------------------ ledger io
    @contextmanager
    def _locked(self):
        with open(self.lockpath, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict) and isinstance(data.get("positions"), dict):
                return data
        except Exception:
            pass
        return {"bot": self.bot, "prefix": self.prefix, "positions": {}}

    def _save(self, data):
        data["updated_at"] = _now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        os.replace(tmp, self.path)

    def ledger(self):
        with self._locked():
            return self._load()["positions"]

    def owned(self, symbol):
        return self.ledger().get(norm(symbol))

    def owned_symbols(self):
        return [v.get("symbol", k) for k, v in self.ledger().items()]

    def set_meta(self, symbol, **meta):
        with self._locked():
            data = self._load()
            rec = data["positions"].get(norm(symbol))
            if rec is not None:
                rec.setdefault("meta", {}).update(meta)
                self._save(data)

    def drop(self, symbol, why=""):
        with self._locked():
            data = self._load()
            if data["positions"].pop(norm(symbol), None) is not None:
                self._save(data)
                self.log.info("[OWNERSHIP] %s removed from %s ledger %s", symbol, self.bot, why)

    # ------------------------------------------------------------ broker reads
    def get_position(self, symbol):
        st, data = self._req("GET", "/positions/" + norm(symbol))
        if st == 200 and isinstance(data, dict) and data.get("symbol"):
            return data
        if st == 404:
            return None
        if st == 0 or st >= 500 or st == 429:
            raise RuntimeError("position lookup failed for %s (HTTP %s)" % (symbol, st))
        return None

    def list_positions(self):
        st, data = self._req("GET", "/positions")
        if st != 200 or not isinstance(data, list):
            raise RuntimeError("positions lookup failed (HTTP %s)" % st)
        return data

    def list_open_orders(self):
        st, data = self._req("GET", "/orders", params={"status": "open", "limit": 500, "nested": "false"})
        if st != 200 or not isinstance(data, list):
            raise RuntimeError("open orders lookup failed (HTTP %s)" % st)
        return data

    def get_order(self, oid):
        st, data = self._req("GET", "/orders/" + str(oid), params={"nested": "true"})
        return data if st == 200 and isinstance(data, dict) else None

    def account_busy(self, symbol):
        """Return a reason string if ANY position or open order exists on symbol
        (any bot, any prefix, untagged).  Fails closed on API errors."""
        s = norm(symbol)
        try:
            for p in self.list_positions():
                if norm(p.get("symbol")) == s:
                    return "account already holds %s %s" % (p.get("qty"), symbol)
            for o in self.list_open_orders():
                if norm(o.get("symbol")) == s:
                    return "open order %s on %s" % (str(o.get("client_order_id"))[:12], symbol)
        except Exception as e:
            return "broker check failed (%s)" % e
        return None

    # ------------------------------------------------------------ orders
    def submit(self, payload, role="entry", tag="", meta=None):
        """Submit an order tagged with this bot's prefix. role='entry' records a new
        owned position; role='exit' links the order to the existing ledger record."""
        body = dict(payload)
        body["client_order_id"] = self.new_coid(tag or role[:2])
        st, data = self._req("POST", "/orders", body=body)
        if st not in (200, 201) or not isinstance(data, dict) or not data.get("id"):
            self.log.error("[OWNERSHIP] order rejected %s %s: HTTP %s %s", body.get("symbol"),
                           body.get("side"), st, str(data)[:300])
            return None
        sym = norm(body["symbol"])
        with self._locked():
            led = self._load()
            rec = led["positions"].get(sym)
            if role == "entry":
                side = "long" if body.get("side") == "buy" else "short"
                if rec is None or rec.get("side") != side:
                    rec = {"symbol": body["symbol"], "side": side, "qty": 0.0, "orders": [],
                           "opened_at": _now(), "meta": {}}
                    led["positions"][sym] = rec
                if meta:
                    rec.setdefault("meta", {}).update(meta)
            if rec is not None:
                rec["orders"].append({"id": data["id"], "coid": body["client_order_id"], "role": role,
                                      "qty": body.get("qty"), "at": _now()})
            self._save(led)
        self.log.info("[OWNERSHIP] %s %s %s %s coid=%s", role.upper(), body.get("side"), body.get("qty"),
                      body["symbol"], body["client_order_id"])
        return data

    def entry(self, payload, tag="en", meta=None, check_busy=True):
        if check_busy:
            why = self.account_busy(payload["symbol"])
            if why:
                self.log.info("[OWNERSHIP] skip entry %s: %s", payload["symbol"], why)
                return None
        return self.submit(payload, role="entry", tag=tag, meta=meta)

    def _filled_following_replacements(self, order, depth=0):
        """filled qty of an order, following replaced_by chains (e.g. a ratcheted stop)."""
        if not order:
            return 0.0, False
        st = order.get("status")
        if st == "replaced" and order.get("replaced_by") and depth < 6:
            return self._filled_following_replacements(self.get_order(order["replaced_by"]), depth + 1)
        return _f(order.get("filled_qty")), st not in TERMINAL

    def cancel_order(self, oid):
        st, _ = self._req("DELETE", "/orders/" + str(oid))
        return st in (200, 204)

    # ------------------------------------------------------------ reconcile
    def reconcile(self, symbol=None):
        """Recompute owned qty from this bot's own orders and the live position.
        Drops records that are fully closed / never filled / externally flattened /
        flipped.  Returns the ledger dict."""
        with self._locked():
            led = self._load()
            changed = False
            try:
                live = {norm(p["symbol"]): p for p in self.list_positions()}
            except Exception as e:
                self.log.warning("[OWNERSHIP] reconcile skipped (%s)", e)
                return led["positions"]
            for sym, rec in list(led["positions"].items()):
                if symbol and sym != norm(symbol):
                    continue
                net, pending = 0.0, False
                for o in rec.get("orders", []):
                    if not o.get("done"):
                        od = self.get_order(o["id"])
                        if od is None:
                            if str(o["id"]).startswith("dry-"):
                                pending = True
                            continue
                        filled = _f(od.get("filled_qty"))
                        legs_filled, legs_open = 0.0, False
                        for leg in od.get("legs") or []:
                            lf, lopen = self._filled_following_replacements(leg)
                            legs_filled += lf
                            legs_open = legs_open or lopen
                        is_open = od.get("status") not in TERMINAL
                        o["filled"], o["legs_filled"] = filled, legs_filled
                        if not is_open and not legs_open:
                            o["done"] = True
                            changed = True
                        if is_open:
                            pending = True
                    sign = 1.0 if o["role"] == "entry" else -1.0
                    net += sign * _f(o.get("filled"))
                    if o["role"] == "entry":
                        net -= _f(o.get("legs_filled"))
                net = max(0.0, round(net, 9))
                pos = live.get(sym)
                pos_side = None
                if pos is not None:
                    pos_side = "long" if _f(pos.get("qty")) > 0 else "short"
                reason = None
                if pos is None and not pending:
                    reason = "(no live position)"
                elif pos is not None and pos_side != rec["side"] and not pending:
                    reason = "(live position is %s, ledger %s)" % (pos_side, rec["side"])
                elif net <= EPS and not pending:
                    reason = "(own qty fully closed)"
                if reason:
                    led["positions"].pop(sym)
                    self.log.info("[OWNERSHIP] %s removed from %s ledger %s", rec.get("symbol", sym), self.bot, reason)
                    changed = True
                    continue
                if abs(_f(rec.get("qty")) - net) > EPS:
                    rec["qty"] = net
                    changed = True
            if changed:
                self._save(led)
            return led["positions"]

    # ------------------------------------------------------------ exits
    def cancel_own_orders(self, symbol=None):
        """Cancel only this bot's open orders (prefix match) and the open bracket
        legs of this bot's own entry orders.  Never touches anything else."""
        n = 0
        sym = norm(symbol) if symbol else None
        try:
            for o in self.list_open_orders():
                if self.is_own(o.get("client_order_id")) and (sym is None or norm(o.get("symbol")) == sym):
                    n += self.cancel_order(o["id"])
        except Exception as e:
            self.log.warning("[OWNERSHIP] open-order scan failed: %s", e)
        for s, rec in self.ledger().items():
            if sym and s != sym:
                continue
            for o in rec.get("orders", []):
                if o["role"] != "entry" or o.get("done"):
                    continue
                od = self.get_order(o["id"])
                for leg in (od or {}).get("legs") or []:
                    cur = leg
                    depth = 0
                    while cur and cur.get("status") == "replaced" and cur.get("replaced_by") and depth < 6:
                        cur = self.get_order(cur["replaced_by"]); depth += 1
                    if cur and cur.get("status") not in TERMINAL:
                        n += self.cancel_order(cur["id"])
        return n

    def safe_exit(self, symbol, reason="", wait_sec=6.0):
        """Close (part of) a position this bot owns.  Returns qty sent (0 if nothing)."""
        sym = norm(symbol)
        led = self.reconcile(symbol)
        rec = led.get(sym)
        if not rec:
            self.log.info("[OWNERSHIP] exit %s skipped: not owned by %s (%s)", symbol, self.bot, reason)
            return 0
        for o in rec.get("orders", []):
            if o["role"] == "exit":
                od = self.get_order(o["id"])
                if od and od.get("status") not in TERMINAL:
                    self.log.info("[OWNERSHIP] exit %s skipped: own exit %s still working", symbol, o["coid"])
                    return 0
        try:
            pos = self.get_position(symbol)
        except Exception as e:
            self.log.error("[OWNERSHIP] exit %s aborted: %s", symbol, e)
            return 0
        if pos is None:
            self.log.warning("[OWNERSHIP] exit %s skipped: no live position (%s)", symbol, reason)
            self.drop(symbol, "(no live position at exit)")
            return 0
        live_qty = _f(pos.get("qty"))
        live_side = "long" if live_qty > 0 else "short"
        if live_side != rec["side"]:
            self.log.warning("[OWNERSHIP] exit %s skipped: live %s %s but we own %s", symbol,
                             live_side, live_qty, rec["side"])
            self.drop(symbol, "(direction mismatch)")
            return 0
        own = _f(rec.get("qty"))
        q = min(own, abs(live_qty))
        if q <= EPS:
            self.log.info("[OWNERSHIP] exit %s skipped: own qty %s live %s", symbol, own, live_qty)
            return 0
        # release shares held by OUR bracket legs (never other bots' legs)
        avail = abs(_f(pos.get("qty_available", pos.get("qty"))))
        if avail + EPS < q:
            self.cancel_own_orders(symbol)
            t0 = time.time()
            while time.time() - t0 < wait_sec:
                time.sleep(0.5)
                try:
                    pos = self.get_position(symbol)
                except Exception:
                    pos = None
                if pos is None:
                    self.log.info("[OWNERSHIP] %s position gone while releasing legs", symbol)
                    return 0
                avail = abs(_f(pos.get("qty_available", pos.get("qty"))))
                if avail + EPS >= q:
                    break
            if self.dry_run:
                avail = q
            q = min(q, avail, abs(_f(pos.get("qty"))))
        if not self.crypto:
            q = float(int(q + 1e-9))
        if q <= EPS:
            self.log.warning("[OWNERSHIP] exit %s: 0 qty available after releasing own legs (held by others?)", symbol)
            return 0
        payload = {"symbol": rec.get("symbol", symbol), "qty": fmt_qty(q),
                   "side": "sell" if rec["side"] == "long" else "buy",
                   "type": "market", "time_in_force": "gtc" if self.crypto else "day"}
        res = self.submit(payload, role="exit", tag="x")
        if res is None:
            return 0
        self.log.info("[OWNERSHIP] EXIT %s %s qty=%s (own=%s live=%s) %s", symbol, rec["side"], fmt_qty(q),
                      fmt_qty(own), fmt_qty(live_qty), reason)
        return q

    def close_all_own(self, reason=""):
        self.cancel_own_orders()
        out = {}
        for s in list(self.reconcile().keys()):
            out[s] = self.safe_exit(s, reason)
        return out
