import json

# --- CONFIGURABLE BOOSTS & THRESHOLDS ---
AJ_FOUL_BOOST = 4.5       # Leader in bonus + being fouled
AJ_PACE_BOOST = 3.0       # Predictable late-game slowing
AJ_FT_BOOST = 2.5         # 80%+ FT shooters on floor
AJ_REF_BOOST = 3.5        # Ref history (Ben Taylor/Scott Foster)
MARKET_ANCHOR_LIMIT = 8.5 # Max allowed distance from Vegas line

def build_aj_edge_alerts(sample):
    """
    AJ-Edge v3.0: The 'Consistency' Engine.
    Incorporates Market Anchoring, Tanking Detection, and Vegas Lag.
    """
    out = []
    
    # 🕒 1. STATE VARIABLES
    sec_left = (sample.get("minutes_left", 0) * 60) + sample.get("seconds_left", 0)
    score_diff = abs(sample.get("score_diff", 0))
    ft_rate = sample.get("ft_rate", 0.70)
    market_spread = sample.get("market_spread", 0)
    
    # 🚦 2. WINDOWS & PENALTIES
    is_lag_window = 15 < sec_left < 240
    
    # Tanking/Star-Out Detector
    starters_out = sample.get("starters_out_count", 0)
    tanking_penalty = -4.0 if starters_out >= 3 else 0.0
    
    # Travel/B2B Fatigue
    travel_miles = sample.get("travel_miles", 0)
    is_b2b = sample.get("is_back_to_back", False)
    fatigue_penalty = -5.0 if (travel_miles > 1000 or is_b2b) else 0.0

    # Referee Intelligence
    ref_name = sample.get("crew_chief", "Unknown")
    ref_impact = AJ_REF_BOOST if ref_name in ["Ben Taylor", "Scott Foster", "James Capers"] else 0.0

    for c in sample.get("candidates", []):
        boost = 0.0
        base_conf = float(c.get("confidence", 0) or 0)
        
        # --- AJ-LAG LOGIC: THE CLOSED DOOR ---
        if sec_left < 120 and score_diff >= 8:
            boost += 12.0
            
        # --- THE FOUL GAME INJECTOR ---
        if sample.get("foul_pressure") and ft_rate > 0.78:
            boost += AJ_FT_BOOST + AJ_FOUL_BOOST

        # --- REF & SYSTEM IMPACT ---
        if c.get("selection").lower() == "over":
            boost += ref_impact
        
        # Apply Structural Penalties
        boosted_conf = base_conf + boost + tanking_penalty + fatigue_penalty
        boosted_conf = min(99.0, max(0.0, boosted_conf))

        # --- PILLAR 1: THE MARKET ANCHOR (Vegas Sanity Check) ---
        raw_edge = abs(boosted_conf - abs(market_spread))
        is_anchored = False
        
        if raw_edge > MARKET_ANCHOR_LIMIT:
            # We are too far from the market. Apply 30% Skepticism Penalty.
            boosted_conf = boosted_conf * 0.70
            is_anchored = True

        # --- PILLAR 2: TAGGING & FILTERING ---
        a = dict(c)
        a["source"] = "aj_edge"
        a["confidence"] = round(boosted_conf, 2)
        
        # Only output if we hit the high-conviction 95% Consistency target
        if a["confidence"] >= 95.0:
            if is_anchored:
                tag = "⚠️ [AJ-ANCHOR]"
                a["stake_cap"] = 0.5 # Tell main loop to use small units
            elif is_lag_window:
                tag = "🚨 [AJ-LAG]"
                a["stake_cap"] = 3.0 # High conviction
            else:
                tag = "🔥 [AJ]"
                a["stake_cap"] = 1.0

            a["summary"] = (
                f"{tag} {c.get('market').upper()} | {c.get('selection').upper()} \n"
                f"💰 CONF: {a['confidence']}% | DIFF: {score_diff} \n"
                f"⏱️ TIME: {sample.get('minutes_left')}:{sample.get('seconds_left'):02d} | REF: {ref_name}"
            )
            out.append(a)

    return out
