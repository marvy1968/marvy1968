import os

file_name = "live_scoreboard_bridge.py"
with open(file_name, "r", encoding="utf-8") as f:
    content = f.read()

# This new version is much smarter: it just drops the mascot (the last word)
smarter_cleaner = """
def _clean_mascot(name):
    if not name: return ""
    name = str(name).strip()
    parts = name.split()
    # If it's "Arizona Wildcats", just keep "Arizona"
    # If it's "UConn", keep "UConn"
    if len(parts) > 1:
        # Special case for 3-word names like "North Carolina Tar Heels"
        if len(parts) > 2 and parts[0].lower() in ['north', 'south', 'west', 'east', 'new']:
            return " ".join(parts[:-1]).lower()
        return " ".join(parts[:-1]).lower()
    return name.lower()

def find_matching_game(scoreboard_games, home, away, sport_key=None):"""

if "def _clean_mascot" in content:
    import re
    # Replace the old limited mascot list version with this smart one
    content = re.sub(r'def _clean_mascot.*?def find_matching_game', smarter_cleaner, content, flags=re.DOTALL)
    
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ SUCCESS: Bridge is now using Smart-City matching!")
else:
    print("⚠️ FAILED: Could not find the cleaner function to replace.")
