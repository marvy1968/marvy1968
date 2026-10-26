import os

file_name = "live_scoreboard_bridge.py"
with open(file_name, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
skip = False

smart_func = [
    "def _clean_mascot(name):\n",
    "    if not name: return \"\"\n",
    "    name = str(name).strip()\n",
    "    parts = name.split()\n",
    "    if len(parts) > 1:\n",
    "        # Handle 'North Carolina', 'South Carolina', etc.\n",
    "        if parts[0].lower() in ['north', 'south', 'west', 'east', 'new']:\n",
    "            return \" \".join(parts[:-1]).lower()\n",
    "        # Otherwise just drop the last word (the mascot)\n",
    "        return \" \".join(parts[:-1]).lower()\n",
    "    return name.lower()\n"
]

for line in lines:
    if "def _clean_mascot" in line:
        new_lines.extend(smart_func)
        skip = True
        continue
    if skip and "def find_matching_game" in line:
        skip = False
    if not skip:
        new_lines.append(line)

with open(file_name, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("✅ SUCCESS: Smart-City Matching is now active!")
