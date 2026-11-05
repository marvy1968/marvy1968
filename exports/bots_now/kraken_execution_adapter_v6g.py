#!/usr/bin/env python3
"""
Hardened Phase 6G Kraken Execution & Private Interface Adapter
Institutional Invariants:
1. 32-hex character deterministic cl_ord_id schema (strict RFC-compliant hex format).
2. Hard class-level safety blocks: FORBIDDEN_PRIVATE_ENDPOINTS = {"AddOrder", "CancelOrder", "CancelAll"}.
3. Explicit READ_ONLY_PRIVATE_ENDPOINTS allowlist.
4. Strict fail-closed JSON state loader (raises RuntimeError on corruption).
5. Duplicate cl_ord_id rejection in OrderJournalManager.
6. Monotonic thread-safe microsecond nonce generator with fresh nonce on retry.
7. Immediate journal initialization on startup (zero-intent days persist empty file).
8. Atomic crash-recovery state machine.
"""

import os
import sys
import time
import json
import hmac
import base64
import hashlib
import tempfile
import threading
import urllib.parse
import requests
import logging
import numpy as np
from datetime import datetime, timezone

DATA_DIR = "/home/moonrider1968/bots/crypto_data"
JOURNAL_PATH = os.path.join(DATA_DIR, "order_intent_journal.json")
POSITIONS_PATH = os.path.join(DATA_DIR, "reconciled_active_positions.json")

FORBIDDEN_PRIVATE_ENDPOINTS = {"AddOrder", "CancelOrder", "CancelAll", "CancelAllOrdersAfter"}
READ_ONLY_PRIVATE_ENDPOINTS = {
    "Balance", "TradeBalance", "OpenOrders", "ClosedOrders",
    "QueryOrders", "TradesHistory", "QueryTrades", "OpenPositions"
}


# --- FAIL-CLOSED STRICT JSON LOADER ---
def strict_json_load(path: str, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise RuntimeError(f"FATAL CORRUPTION in state file {path}: {e}") from e


def atomic_json_dump(data, target_path: str):
    dirname = os.path.dirname(target_path) or "."
    os.makedirs(dirname, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=dirname, delete=False, encoding="utf-8") as tf:
            temp_name = tf.name
            json.dump(data, tf, indent=2)
            tf.flush()
            os.fsync(tf.fileno())

        os.replace(temp_name, target_path)
        temp_name = None

        dir_fd = os.open(dirname, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        if temp_name and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        raise


# --- MONOTONIC MICROSECOND NONCE GENERATOR ---
class MonotonicNonceGenerator:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_nonce = 0

    def next_nonce(self) -> int:
        with self._lock:
            now_micros = int(time.time() * 1_000_000)
            if now_micros <= self._last_nonce:
                now_micros = self._last_nonce + 1
            self._last_nonce = now_micros
            return now_micros


# --- HARDENED KRAKEN API CLIENT ---
class KrakenAPIClient:
    def __init__(self, api_key: str = None, api_secret: str = None):
        self.api_key = api_key or os.getenv("KRAKEN_API_KEY", "")
        self.api_secret = api_secret or os.getenv("KRAKEN_API_SECRET", "")
        self.base_url = "https://api.kraken.com"
        self.session = requests.Session()
        self.nonce_gen = MonotonicNonceGenerator()
        self.pair_cache = {}

    def _get_kraken_signature(self, urlpath: str, data: dict) -> str:
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode("utf-8")
        message = urlpath.encode("utf-8") + hashlib.sha256(encoded).digest()
        signature = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        return base64.b64encode(signature.digest()).decode("utf-8")

    def _execute_request_with_retry(self, method: str, url: str, endpoint: str = "", is_private: bool = False, headers: dict = None, data: dict = None, params: dict = None, max_retries: int = 3) -> dict:
        backoff_sec = 1.0
        req_data = dict(data) if data is not None else {}

        for attempt in range(1, max_retries + 1):
            try:
                if is_private:
                    req_data["nonce"] = self.nonce_gen.next_nonce()
                    urlpath = f"/0/private/{endpoint}"
                    headers["API-Sign"] = self._get_kraken_signature(urlpath, req_data)

                if method.upper() == "GET":
                    res = self.session.get(url, params=params, headers=headers, timeout=10)
                else:
                    res = self.session.post(url, data=req_data, headers=headers, timeout=10)

                if res.status_code in [429, 500, 502, 503, 504]:
                    if attempt < max_retries:
                        time.sleep(backoff_sec)
                        backoff_sec *= 2.0
                        continue
                    res.raise_for_status()

                res_json = res.json()
                errors = res_json.get("error", [])

                if errors:
                    err_str = " | ".join(errors)
                    is_rate_limit = any("Rate limit" in e or "EAPI:Rate limit" in e for e in errors)
                    is_temporary = any("EService:Unavailable" in e or "EService:Busy" in e for e in errors)

                    if (is_rate_limit or is_temporary) and attempt < max_retries:
                        time.sleep(backoff_sec)
                        backoff_sec *= 2.0
                        continue
                    raise RuntimeError(f"Kraken API Error: {err_str}")

                return res_json["result"]

            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as ex:
                if attempt < max_retries:
                    time.sleep(backoff_sec)
                    backoff_sec *= 2.0
                    continue
                raise RuntimeError(f"Network transport failure after {max_retries} attempts: {ex}")

    def query_public(self, endpoint: str, params: dict = None) -> dict:
        url = f"{self.base_url}/0/public/{endpoint}"
        return self._execute_request_with_retry("GET", url, params=params)

    def query_private(self, endpoint: str, data: dict = None) -> dict:
        if endpoint in FORBIDDEN_PRIVATE_ENDPOINTS:
            raise PermissionError(f"CRITICAL SAFETY VIOLATION: Endpoint '{endpoint}' is permanently disabled in this execution environment.")

        if endpoint not in READ_ONLY_PRIVATE_ENDPOINTS:
            raise PermissionError(f"UNAUTHORIZED ENDPOINT: '{endpoint}' is not in the certified read-only allowlist: {sorted(READ_ONLY_PRIVATE_ENDPOINTS)}")

        if not self.api_key or not self.api_secret:
            raise ValueError("Private query rejected: KRAKEN_API_KEY or KRAKEN_API_SECRET missing.")

        urlpath = f"/0/private/{endpoint}"
        url = f"{self.base_url}{urlpath}"
        headers = {"API-Key": self.api_key}

        return self._execute_request_with_retry("POST", url, endpoint=endpoint, is_private=True, headers=headers, data=data)

    def load_pair_constraints(self, pairs: list) -> dict:
        result = self.query_public("AssetPairs")
        alias_map = {
            "BTC/USD": ["XBT/USD", "XXBTZUSD", "XBTUSD", "BTCUSD", "BTC/USD"],
            "DOGE/USD": ["XDG/USD", "XXDGZUSD", "XDGUSD", "DOGEUSD", "DOGE/USD"],
            "ETH/USD": ["ETH/USD", "XETHZUSD", "ETHUSD"],
            "SOL/USD": ["SOL/USD", "SOLUSD"],
            "SUI/USD": ["SUI/USD", "SUIUSD"],
            "XRP/USD": ["XRP/USD", "XXRPZUSD", "XRPUSD"],
            "ZEC/USD": ["ZEC/USD", "XZECZUSD", "ZECUSD"],
            "XMR/USD": ["XMR/USD", "XXMRZUSD", "XMRUSD"],
        }

        for target_pair in pairs:
            aliases = alias_map.get(target_pair, [target_pair, target_pair.replace("/", "")])
            matched = False
            for k_name, details in result.items():
                wsname = details.get("wsname", "")
                altname = details.get("altname", "")
                if wsname in aliases or altname in aliases or k_name in aliases:
                    self.pair_cache[target_pair] = {
                        "kraken_pair": k_name,
                        "altname": altname,
                        "wsname": wsname,
                        "pair_decimals": int(details["pair_decimals"]),
                        "lot_decimals": int(details["lot_decimals"]),
                        "ordermin": float(details.get("ordermin", "0.0001")),
                        "costmin": float(details.get("costmin", "0.50"))
                    }
                    matched = True
                    break
            if not matched:
                logging.warning(f"Could not resolve metadata for pair: {target_pair}")

        return self.pair_cache

    def format_order_qty(self, pair: str, target_qty: float, price: float) -> float:
        info = self.pair_cache.get(pair)
        if not info:
            raise ValueError(f"Pair constraints for {pair} not loaded in cache.")

        lot_dec = info["lot_decimals"]
        clamped_qty = float(np.floor(target_qty * (10 ** lot_dec)) / (10 ** lot_dec))

        if clamped_qty < info["ordermin"]:
            logging.warning(f"Quantity {clamped_qty} below ordermin {info['ordermin']} for {pair}")
            return 0.0
        if (clamped_qty * price) < info["costmin"]:
            logging.warning(f"Notional value ${clamped_qty * price:.2f} below costmin ${info['costmin']} for {pair}")
            return 0.0

        return clamped_qty

    @staticmethod
    def generate_cl_ord_id(symbol: str, bar_dt_str: str, side: str, seq: int = 1) -> str:
        payload = f"{symbol}_{bar_dt_str}_{side.upper()}_{seq}_G3B5_KRAKEN_SALT"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# --- HARDENED ORDER JOURNAL & RECOVERY ENGINE ---
class OrderJournalManager:
    def __init__(self, journal_path: str = JOURNAL_PATH, positions_path: str = POSITIONS_PATH):
        self.journal_path = journal_path
        self.positions_path = positions_path
        
        # Load or initialize state files immediately
        self.journal = strict_json_load(self.journal_path, default=[])
        if not os.path.exists(self.journal_path):
            atomic_json_dump(self.journal, self.journal_path)

        self.positions = strict_json_load(self.positions_path, default={})
        if not os.path.exists(self.positions_path):
            atomic_json_dump(self.positions, self.positions_path)

    def record_intent(self, cl_ord_id: str, symbol: str, side: str, qty: float, price: float, stop_loss: float, dt_str: str) -> dict:
        for existing in self.journal:
            if existing.get("cl_ord_id") == cl_ord_id:
                raise ValueError(f"Duplicate cl_ord_id rejection: Order intent with id '{cl_ord_id}' already exists in journal.")

        entry = {
            "cl_ord_id": cl_ord_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "target_price": price,
            "stop_loss": stop_loss,
            "timestamp": dt_str,
            "status": "INTENT_RECORDED",
            "kraken_txid": None,
            "fill_price": None,
            "fill_qty": None,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        self.journal.append(entry)
        atomic_json_dump(self.journal, self.journal_path)
        return entry

    def update_intent_status(self, cl_ord_id: str, status: str, txid: str = None, fill_px: float = None, fill_qty: float = None):
        for entry in self.journal:
            if entry["cl_ord_id"] == cl_ord_id:
                entry["status"] = status
                if txid:
                    entry["kraken_txid"] = txid
                if fill_px:
                    entry["fill_price"] = fill_px
                if fill_qty:
                    entry["fill_qty"] = fill_qty
                entry["updated_at"] = datetime.now(timezone.utc).isoformat()
                break
        atomic_json_dump(self.journal, self.journal_path)

    def reconcile_on_restart(self, mock_broker_open_orders: dict = None, mock_broker_trades: dict = None) -> dict:
        reconciled_events = []
        for entry in self.journal:
            cl_id = entry["cl_ord_id"]
            sym = entry["symbol"]

            if entry["status"] in ["INTENT_RECORDED", "SUBMITTED"]:
                matched_trade = mock_broker_trades.get(cl_id) if mock_broker_trades else None
                if matched_trade:
                    entry["status"] = "RECONCILED"
                    entry["fill_price"] = matched_trade["price"]
                    entry["fill_qty"] = matched_trade["vol"]
                    entry["kraken_txid"] = matched_trade["txid"]

                    if entry["side"].lower() == "buy":
                        self.positions[sym] = {
                            "symbol": sym,
                            "entry_dt": entry["timestamp"],
                            "entry_px": matched_trade["price"],
                            "qty": matched_trade["vol"],
                            "entry_cost_total": matched_trade["price"] * matched_trade["vol"] * 1.0010,
                            "stop_px": entry["stop_loss"],
                            "cl_ord_id": cl_id
                        }
                    reconciled_events.append((cl_id, "RECONSTRUCTED_POSITION"))
                else:
                    is_open = mock_broker_open_orders.get(cl_id) if mock_broker_open_orders else None
                    if not is_open:
                        entry["status"] = "ABANDONED_OR_LOST"
                        reconciled_events.append((cl_id, "MARKED_ABANDONED"))

        atomic_json_dump(self.journal, self.journal_path)
        atomic_json_dump(self.positions, self.positions_path)
        return {"reconciled_count": len(reconciled_events), "events": reconciled_events}
