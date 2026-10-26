file_name = "live_scoreboard_bridge.py"
with open(file_name, "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    # Force the names to lowercase for comparison
    if "s_home =" in line:
        new_lines.append(line.replace('str(game.get("home_team", ""))', 'str(game.get("home_team", "")).lower()'))
    elif "s_away =" in line:
        new_lines.append(line.replace('str(game.get("away_team", ""))', 'str(game.get("away_team", "")).lower()'))
    # Change exact match '==' to 'in' or partial match
    elif "home_match =" in line and "SequenceMatcher" in line:
        new_lines.append("        home_match = 1.0 if (home.lower() in s_home or s_home in home.lower()) else 0.0\n")
    elif "away_match =" in line and "SequenceMatcher" in line:
        new_lines.append("        away_match = 1.0 if (away.lower() in s_away or s_away in away.lower()) else 0.0\n")
    # Lower the final threshold significantly
    elif "best_score >=" in line:
        new_lines.append("    if best and best_score >= 1.0:\n")
    else:
        new_lines.append(line)

with open(file_name, "w") as f:
    f.writelines(new_lines)
print("✅ SURGICAL SUCCESS: Exact matching replaced with Fuzzy 'In' matching.")
