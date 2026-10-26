import re

file_name = "live_scoreboard_bridge.py"
with open(file_name, "r") as f:
    content = f.read()

# 1. Define the new, aggressive normalization function
new_normalize = """def normalize_team(name: str) -> str:
    if not name: return ""
    s = " ".join(str(name).strip().lower().split())
    s = s.replace(".", "").replace("&", "and").replace("'", "").replace("-", " ")
    
    # Common replacements
    repls = {
        "st ": "saint ", "st. ": "saint ", "76ers": "sixers", 
        "trail blazers": "blazers", "uconn": "connecticut", "tcu": "texas christian",
        "lsu": "louisiana state", "ole miss": "mississippi", "oklahoma st": "oklahoma state",
        "nc state": "north carolina state", "usc": "southern california"
    }
    for k, v in repls.items():
        s = s.replace(k, v)

    # MASCOT STRIPPER: Remove common ending words that cause mismatches
    mascots = [
        "sooners", "buffaloes", "bluejays", "mountaineers", "gamecocks", 
        "volunteers", "wolverines", "lobos", "blue devils", "cardinal", 
        "scarlet knights", "boilermakers", "wildcats", "huskies", "tar heels",
        "tigers", "bulldogs", "panthers", "cougars", "knights", "rams"
    ]
    words = s.split()
    if len(words) > 1 and words[-1] in mascots:
        s = " ".join(words[:-1])
    
    return s.strip()
"""

# 2. Replace the old normalize_team function
# We look for the start of the function and the start of the next one (fetch_all...)
content = re.sub(r'def normalize_team\(name: str\) -> str:.*?def ', new_normalize + '\n\ndef ', content, flags=re.DOTALL)

# 3. Lower the matching threshold from 1.6 to 1.1 (much more forgiving)
content = content.replace('best_score >= 1.6', 'best_score >= 1.1')

# 4. Save it
with open(file_name, "w") as f:
    f.write(content)

print("✅ SURGICAL SUCCESS: Mascot stripping and fuzzy threshold are LIVE.")
