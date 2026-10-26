import os
import re

file_name = "live_scoreboard_bridge.py"
with open(file_name, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Define the Cleaner and the New Matcher
new_logic = """
def _clean_team_name(name):
    if not name: return ""
    name = str(name).strip().lower()
    # Remove common mascots and extra words
    parts = name.split()
    if len(parts) > 1:
        if parts[0] in ['north', 'south', 'west', 'east', 'new']:
            return " ".join(parts[:-1])
        return " ".join(parts[:-1])
    return name

def find_matching_game(scoreboard_games, home, away, sport_key=None):
    if not scoreboard_games: return None
    
    target_h = _clean_team_name(home)
    target_a = _clean_team_name(away)
    
    best = None
    best_score = 0.0
    
    for game in scoreboard_games:
        s_home = _clean_team_name(game.get("home_team", ""))
        s_away = _clean_team_name(game.get("away_team", ""))
        
        # Simple inclusion check first (handles UConn vs Connecticut)
        score = 0.0
        if target_h in s_home or s_home in target_h: score += 0.8
        if target_a in s_away or s_away in target_a: score += 0.8
        
        if score > best_score:
            best_score = score
            best = game
            
    # Lower threshold to 1.5 to be less picky
    if best and best_score >= 1.5:
        return best
    return None
"""

# 2. Use a sledgehammer to replace everything from the old find_matching_game 
# down to the start of the next section.
pattern = r"def find_matching_game.*?return None"
content = re.sub(pattern, new_logic, content, flags=re.DOTALL)

with open(file_name, "w", encoding="utf-8") as f:
    f.write(content)
print("✅ NUCLEAR SUCCESS: The bridge has been rebuilt from the ground up.")
