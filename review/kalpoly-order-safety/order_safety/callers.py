"""Ledger helpers for the rank-1 state machine.

These functions are not the DEMO POST path. They do not build the venue JSON
and they do not replace _place_ioc. The source-backed client is demo_client.
The running caller modules stay blocked; see caller_status.

Source lines, export SHA256 in PROVENANCE.md:
  runner.py 162-167 and 211-216
  me_kalshi.py 344-351
  live_runner.py 111-115
  kalshi_nba_pacer.py 196-200
  marv_cbb_kalshi.py 57-61
  live_props.py 273-278
"""

from __future__ import annotations

from .host import assert_demo_base
from .machine import NoRealPolymarketOrderApi, OrderMachine

DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"

# Ledger-test labels. Whether a caller is integrated is recorded in caller_status.
CALL_SITES = {
    "runner": "kalshi-demo/runner.py:162-167",
    "runner_props": "kalshi-demo/runner.py:211-216",
    "me_kalshi": "kalshi-demo/me_kalshi.py:344-351",
    "live_runner": "kalshi-demo/live_runner.py:111-115",
    "nba_pacer": "kalshi-demo/kalshi_nba_pacer.py:196-200",
    "marv_cbb": "kalshi-demo/marv_cbb_kalshi.py:57-61",
    "live_props": "marv-feed/live_props/live_props.py:273-278",
}


def place_demo(machine: OrderMachine, transport, *, caller: str, account_label: str, decision_id: str,
               ticker: str, side: str, count: int, limit_cents: int, base_url: str = DEMO_BASE) -> dict:
    if caller not in CALL_SITES:
        raise ValueError("caller")
    assert_demo_base(base_url)
    return machine.submit(
        transport, venue="kalshi_demo", mode="demo", account_label=account_label, decision_id=decision_id,
        ticker=ticker, side=side, count=count, limit_cents=limit_cents, caller=caller, base_url=base_url)


def place_ioc_then_rest(machine: OrderMachine, ioc_transport, rest_transport, *, caller: str,
                        account_label: str, decision_id: str, ticker: str, side: str, count: int,
                        limit_cents: int, rest_enabled: bool = True, base_url: str = DEMO_BASE) -> dict:
    """Same branching as KalshiDemo.place_order: IOC first, one remainder only if the fill count is known."""
    parent = place_demo(
        machine, ioc_transport, caller=caller, account_label=account_label, decision_id=decision_id,
        ticker=ticker, side=side, count=count, limit_cents=limit_cents, base_url=base_url)
    child = None
    if rest_enabled and parent["state"] == "partial":
        child = machine.open_resting_child(
            rest_transport, parent_intent_id=parent["intent_id"], account_label=account_label,
            base_url=base_url, caller=caller)
    return {"parent": parent, "child": child}


def paper_fill(machine: OrderMachine, transport, *, account_label: str, decision_id: str, slug: str,
               side: str, count: int, limit_cents: int, fee_text: str) -> dict:
    """Local paper persistence only. There is no Polymarket order API in this candidate."""
    if not isinstance(fee_text, str):
        raise ValueError("fee_text")
    return machine.submit(
        transport, venue="polymarket_paper", mode="paper", account_label=account_label,
        decision_id=decision_id, ticker=slug, side=side, count=count, limit_cents=limit_cents,
        caller="poly_paper", base_url=None, fee_text=fee_text)


def paper_fill_refuses_network(base_url: str) -> None:
    raise NoRealPolymarketOrderApi("Polymarket paper candidate has no order API: " + str(base_url))
