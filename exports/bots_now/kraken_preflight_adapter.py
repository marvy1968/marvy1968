#!/usr/bin/env python3
"""
Kraken Spot API Preflight Adapter & Signature Engine (v2)
Features:
- Robust symbol normalization (XBT/BTC, XDG/DOGE, XXBTZUSD)
- HMAC-SHA512 nonce-based authentication
- Dynamic precision, ordermin, and costmin enforcement
- Deterministic client order ID (userref / cl_ord_id) generation
"""

import os
import time
import hmac
import base64
import hashlib
import urllib.parse
import requests
import json
import logging
import numpy as np

class KrakenAPIClient:
    def __init__(self, api_key: str = None, api_secret: str = None):
        self.api_key = api_key or os.getenv("KRAKEN_API_KEY", "")
        self.api_secret = api_secret or os.getenv("KRAKEN_API_SECRET", "")
        self.base_url = "https://api.kraken.com"
        self.session = requests.Session()
        self.pair_cache = {}

    def _get_kraken_signature(self, urlpath: str, data: dict) -> str:
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()
        signature = hmac.new(base64.b64decode(self.api_secret), message, hashlib.sha512)
        return base64.b64encode(signature.digest()).decode()

    def query_public(self, endpoint: str, params: dict = None) -> dict:
        url = f"{self.base_url}/0/public/{endpoint}"
        res = self.session.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get("error"):
            raise RuntimeError(f"Kraken Public API Error: {data['error']}")
        return data["result"]

    def query_private(self, endpoint: str, data: dict = None) -> dict:
        if not self.api_key or not self.api_secret:
            raise ValueError("API Key and Secret required for private endpoints.")
        
        urlpath = f"/0/private/{endpoint}"
        url = f"{self.base_url}{urlpath}"
        
        if data is None:
            data = {}
        data["nonce"] = int(time.time() * 1000)
        
        headers = {
            "API-Key": self.api_key,
            "API-Sign": self._get_kraken_signature(urlpath, data)
        }
        res = self.session.post(url, headers=headers, data=data, timeout=10)
        res.raise_for_status()
        res_json = res.json()
        if res_json.get("error"):
            raise RuntimeError(f"Kraken Private API Error: {res_json['error']}")
        return res_json["result"]

    def load_pair_constraints(self, pairs: list) -> dict:
        """Loads lot size, minimum order size, cost floor, and decimal precision with alias normalization."""
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
                logging.warning(f"Could not resolve Kraken pair metadata for {target_pair}")

        return self.pair_cache

    def format_order_qty(self, pair: str, target_qty: float, price: float) -> float:
        """Clamps calculated quantity to Kraken lot precision, ordermin, and costmin."""
        info = self.pair_cache.get(pair)
        if not info:
            raise ValueError(f"Pair constraints for {pair} not loaded.")

        lot_dec = info["lot_decimals"]
        clamped_qty = float(np.floor(target_qty * (10 ** lot_dec)) / (10 ** lot_dec))

        if clamped_qty < info["ordermin"]:
            logging.warning(f"Order qty {clamped_qty} below ordermin {info['ordermin']} for {pair}")
            return 0.0
        if (clamped_qty * price) < info["costmin"]:
            logging.warning(f"Order notional ${clamped_qty * price:.2f} below costmin ${info['costmin']} for {pair}")
            return 0.0

        return clamped_qty

    def generate_userref(self, symbol: str, bar_dt_str: str, side: str) -> int:
        """Generates a 32-bit unsigned int deterministic userref for order idempotency."""
        raw_str = f"{symbol}_{bar_dt_str}_{side}_G3B5"
        hash_digest = hashlib.sha256(raw_str.encode()).hexdigest()
        return int(hash_digest[:7], 16)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    client = KrakenAPIClient()

    print("=" * 90)
    print(" 🐙 KRAKEN PUBLIC PREFLIGHT & ASSET-PAIR PRECISION CHECK (v2)")
    print("=" * 90)

    test_pairs = ["BTC/USD", "ETH/USD", "SOL/USD", "SUI/USD", "XRP/USD", "ZEC/USD", "XMR/USD"]
    constraints = client.load_pair_constraints(test_pairs)

    for pair, c in constraints.items():
        print(f"Pair: {pair:<8} | Kraken Code: {c['kraken_pair']:<10} | Lot Dec: {c['lot_decimals']} | Price Dec: {c['pair_decimals']} | Min Qty: {c['ordermin']} | Min Cost: ${c['costmin']}")

    print("\n--- SIZING CLAMP & USERREF VERIFICATION ---")
    raw_btc_qty = 0.123456789
    clamped_btc = client.format_order_qty("BTC/USD", raw_btc_qty, 65000.0)
    userref_btc = client.generate_userref("BTC/USD", "2026-08-28", "buy")
    print(f"BTC/USD -> Raw Qty: {raw_btc_qty} | Clamped: {clamped_btc} | Idempotent Userref: {userref_btc}")

    raw_sui_qty = 4.25
    clamped_sui = client.format_order_qty("SUI/USD", raw_sui_qty, 2.50)
    print(f"SUI/USD -> Raw Qty: {raw_sui_qty} | Clamped (Below Min): {clamped_sui} (Expected: 0.0)")
