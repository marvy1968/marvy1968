import os

file_name = "live_scoreboard_bridge.py"
with open(file_name, "r") as f:
    content = f.read()

# This replaces the "return None" failure with a print statement so we can see the data
debug_logic = """    if best and best_score >= 1.1:
        return best
    
    # DEBUG: Print what was available if we failed to match
    sb_teams = [f"{g.get('away_team')} @ {g.get('home_team')}" for g in scoreboard_games]
    print(f"⚠️ MATCH FAIL: {away} @ {home} | Available on Scoreboard: {sb_teams}")
    return None"""

if "return None" in content:
    import re
    # Replace the last occurrence of the return None block in find_matching_game
    new_content = content.replace("if best and best_score >= 1.1:\n        return best\n    return None", debug_logic)
    
    with open(file_name, "w") as f:
        f.write(new_content)
    print("✅ DEBUG ACTIVATED: The bot will now report exactly what it sees on the scoreboard.")
else:
    print("⚠️ FAILED: Could not find the return block to patch.")
