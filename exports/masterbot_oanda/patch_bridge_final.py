import os

file_name = "live_scoreboard_bridge.py"
with open(file_name, "r", encoding="utf-8") as f:
    content = f.read()

# The helper function to strip suffixes
cleaner_func = """
def _clean_mascot(name):
    if not name: return ""
    suffixes = [
        "Sooners", "Buffaloes", "Bluejays", "Mountaineers", "Gamecocks", 
        "Volunteers", "Wolverines", "Huskies", "Fighting Irish", 
        "Scarlet Knights", "Cardinal", "Golden Hurricane", "Lobos", "Blue Devils"
    ]
    name = str(name).strip()
    for s in suffixes:
        if name.endswith(s):
            name = name[:-(len(s))].strip()
    return name.lower()

def find_matching_game(scoreboard_games, home, away, sport_key=None):"""

# Update the main function definition and internal logic
if "def find_matching_game" in content and "_clean_mascot" not in content:
    # 1. Inject the cleaner function
    content = content.replace("def find_matching_game(scoreboard_games, home, away, sport_key=None):", cleaner_func)
    
    # 2. Update the target names to be cleaned
    content = content.replace("best_score = 0.0", "best_score = 0.0\n    home_c = _clean_mascot(home)\n    away_c = _clean_mascot(away)")
    
    # 3. Update the loop variables to be cleaned
    # We target the common patterns for g_home/g_away or similar
    import re
    content = re.sub(r's_home\s*=\s*str\(game\.get\("home_team", ""\)\)', 's_home = _clean_mascot(game.get("home_team", ""))', content)
    content = re.sub(r's_away\s*=\s*str\(game\.get\("away_team", ""\)\)', 's_away = _clean_mascot(game.get("away_team", ""))', content)
    
    # 4. Update the similarity check to use the cleaned versions
    content = content.replace("home_match = SequenceMatcher(None, s_home, home).ratio()", "home_match = SequenceMatcher(None, s_home, home_c).ratio()")
    content = content.replace("away_match = SequenceMatcher(None, s_away, away).ratio()", "away_match = SequenceMatcher(None, s_away, away_c).ratio()")

    with open(file_name, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ SUCCESS: Scoreboard Bridge is now mascot-aware!")
else:
    print("⚠️ FAILED: Bridge logic not found or already patched.")
