import os

file_name = "live_scoreboard_bridge.py"
with open(file_name, "r", encoding="utf-8") as f:
    content = f.read()

# Update the NCAAB and WNCAAB URLs to include groups=50 (All D1 games)
ncaab_old = '"basketball_ncaab": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"'
ncaab_new = '"basketball_ncaab": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=50&limit=200"'

wncaab_old = '"basketball_wncaab": "https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard"'
wncaab_new = '"basketball_wncaab": "https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard?groups=50&limit=200"'

if ncaab_old in content:
    content = content.replace(ncaab_old, ncaab_new)
    content = content.replace(wncaab_old, wncaab_new)
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ SUCCESS: ESPN URLs updated to fetch ALL games, not just the Top 25.")
else:
    print("⚠️ FAILED: Could not find the URL strings. They might already be updated or formatted differently.")
