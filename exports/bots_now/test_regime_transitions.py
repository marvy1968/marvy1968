#!/usr/bin/env python3
"""
G3-B5 Invariant Certification: Macro Regime Symmetric Transition Suite
Tests two-way mutual exclusion across owned inventory and pending order states.
"""

def evaluate_regime_eligibility(
    macro_bull: bool,
    g3b5_positions: dict,
    pending_orders: dict,
    current_nav: float = 100000.0,
    max_positions: int = 3,
    max_gross_risk: float = 0.03
) -> dict:
    """
    Pure in-memory evaluator mirroring canonical allocator gates.
    """
    # 1. Symmetric Inventory & Pending Scan
    has_active_longs = (
        any(p.get("side", "long") == "long" for p in g3b5_positions.values()) or
        any(o.get("side") == "long" for o in pending_orders.values())
    )
    has_active_shorts = (
        any(p.get("side") == "short" for p in g3b5_positions.values()) or
        any(o.get("side") == "short" for o in pending_orders.values())
    )

    held_symbols = set(g3b5_positions.keys()) | {o["symbol"] for o in pending_orders.values()}
    total_positions_count = len(held_symbols)

    # 2. Risk Calculations
    open_risk = sum(
        p.get("owned_qty", 0) * max(0.0, abs(p["entry_px"] - p.get("virtual_sl", p["entry_px"] * 0.85)))
        for p in g3b5_positions.values()
    )
    pending_risk = sum(o["qty"] * o["risk_prs"] for o in pending_orders.values())
    rem_gross_risk = (current_nav * max_gross_risk) - open_risk - pending_risk

    # 3. Gate Logic
    # Long gate: Bull regime + NO short exposure (owned or pending) + slots + budget
    long_regime_mutex_pass = macro_bull and not has_active_shorts
    long_allocator_eligible = long_regime_mutex_pass and (total_positions_count < max_positions) and (rem_gross_risk > 0)

    # Short gate: Bear regime + NO long exposure (owned or pending) + slots + budget
    short_regime_mutex_pass = (not macro_bull) and not has_active_longs
    short_allocator_eligible = short_regime_mutex_pass and (total_positions_count == 0) and (rem_gross_risk > 0)

    return {
        "has_active_longs": has_active_longs,
        "has_active_shorts": has_active_shorts,
        "total_positions_count": total_positions_count,
        "rem_gross_risk": rem_gross_risk,
        "long_regime_mutex_pass": long_regime_mutex_pass,
        "long_allocator_eligible": long_allocator_eligible,
        "short_regime_mutex_pass": short_regime_mutex_pass,
        "short_allocator_eligible": short_allocator_eligible,
    }


def run_test_suite():
    print("=" * 80)
    print(" 🔬 G3-B5 SYMMETRIC REGIME TRANSITION & MUTEX CERTIFICATION")
    print("=" * 80)

    tests = [
        {
            "name": "State 1A: Bull Regime + 1 Long Held (Slots Available)",
            "macro_bull": True,
            "positions": {"MSFT": {"side": "long", "owned_qty": 20, "entry_px": 450.0, "virtual_sl": 435.0}},
            "pending": {},
            "expected_long_mutex": True,
            "expected_long_eligible": True,
            "expected_short_mutex": False,
            "expected_short_eligible": False
        },
        {
            "name": "State 1B: Bull Regime + 3 Longs Held (Capacity Maxed)",
            "macro_bull": True,
            "positions": {
                "MSFT": {"side": "long", "owned_qty": 20, "entry_px": 450.0, "virtual_sl": 435.0},
                "NVDA": {"side": "long", "owned_qty": 40, "entry_px": 125.0, "virtual_sl": 120.0},
                "AVGO": {"side": "long", "owned_qty": 10, "entry_px": 170.0, "virtual_sl": 160.0}
            },
            "pending": {},
            "expected_long_mutex": True,
            "expected_long_eligible": False,  # Mutex passes, but slots full
            "expected_short_mutex": False,
            "expected_short_eligible": False
        },
        {
            "name": "State 2A: Bear Pivot + Long Held (Transition 1: Bull -> Bear)",
            "macro_bull": False,
            "positions": {"MSFT": {"side": "long", "owned_qty": 20, "entry_px": 450.0, "virtual_sl": 435.0}},
            "pending": {},
            "expected_long_mutex": False,
            "expected_long_eligible": False,
            "expected_short_mutex": False,   # Suppressed by held long
            "expected_short_eligible": False
        },
        {
            "name": "State 2B: Bear Pivot + Pending Long Entry in Flight",
            "macro_bull": False,
            "positions": {},
            "pending": {"g3b5_xlk_NVDA_20260827": {"symbol": "NVDA", "qty": 50, "risk_prs": 5.0, "side": "long"}},
            "expected_long_mutex": False,
            "expected_long_eligible": False,
            "expected_short_mutex": False,   # Suppressed by pending long
            "expected_short_eligible": False
        },
        {
            "name": "State 3: Bear Regime + Zero Holdings (Clean Slate)",
            "macro_bull": False,
            "positions": {},
            "pending": {},
            "expected_long_mutex": False,
            "expected_long_eligible": False,
            "expected_short_mutex": True,    # Mutex passes
            "expected_short_eligible": True  # Allocator armed
        },
        {
            "name": "State 4A: Bull Pivot + Short Held (Transition 2: Bear -> Bull)",
            "macro_bull": True,
            "positions": {"AAPL": {"side": "short", "owned_qty": 30, "entry_px": 220.0, "virtual_sl": 228.0}},
            "pending": {},
            "expected_long_mutex": False,   # Suppressed by held short
            "expected_long_eligible": False,
            "expected_short_mutex": False,
            "expected_short_eligible": False
        },
        {
            "name": "State 4B: Bull Pivot + Pending Short Order in Flight",
            "macro_bull": True,
            "positions": {},
            "pending": {"g3b5_short_CAT_20260827": {"symbol": "CAT", "qty": 25, "risk_prs": 8.0, "side": "short"}},
            "expected_long_mutex": False,   # Suppressed by pending short
            "expected_long_eligible": False,
            "expected_short_mutex": False,
            "expected_short_eligible": False
        },
    ]

    all_passed = True
    for i, t in enumerate(tests, 1):
        res = evaluate_regime_eligibility(
            macro_bull=t["macro_bull"],
            g3b5_positions=t["positions"],
            pending_orders=t["pending"]
        )

        long_m_ok = (res["long_regime_mutex_pass"] == t["expected_long_mutex"])
        long_e_ok = (res["long_allocator_eligible"] == t["expected_long_eligible"])
        short_m_ok = (res["short_regime_mutex_pass"] == t["expected_short_mutex"])
        short_e_ok = (res["short_allocator_eligible"] == t["expected_short_eligible"])

        test_passed = long_m_ok and long_e_ok and short_m_ok and short_e_ok
        status_str = "PASS" if test_passed else "FAIL"
        if not test_passed:
            all_passed = False

        print(f"[{status_str}] Test {i}: {t['name']}")
        if not test_passed:
            print(f"       Details: LongMutex={res['long_regime_mutex_pass']} (exp {t['expected_long_mutex']}), "
                  f"LongElig={res['long_allocator_eligible']} (exp {t['expected_long_eligible']}), "
                  f"ShortMutex={res['short_regime_mutex_pass']} (exp {t['expected_short_mutex']}), "
                  f"ShortElig={res['short_allocator_eligible']} (exp {t['expected_short_eligible']})")

    print("-" * 80)
    print(f"OVERALL TRANSITION SUITE: {'CERTIFIED (ALL PASS)' if all_passed else 'FAILED'}")
    print("=" * 80)

if __name__ == "__main__":
    run_test_suite()
