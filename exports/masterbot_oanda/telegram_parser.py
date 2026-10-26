from __future__ import annotations


def parse_telegram_command(message: str) -> dict[str, str]:
    text = message.lower().strip()

    text = text.replace("@", " vs ")
    text = text.replace(" v ", " vs ")
    text = text.replace(" versus ", " vs ")

    tokens = text.split()
    if not tokens:
        raise ValueError("Empty command")

    mode = "full"
    if tokens[-1] in {"1h", "2h", "full"}:
        mode = tokens[-1]
        tokens = tokens[:-1]

    if not tokens:
        raise ValueError("Missing teams")

    if "vs" in tokens:
        idx = tokens.index("vs")
        away_team = " ".join(tokens[:idx]).strip()
        home_team = " ".join(tokens[idx + 1:]).strip()
    else:
        if len(tokens) < 2:
            raise ValueError("Invalid format. Use: aub vs duke 2h")
        away_team = tokens[0].strip()
        home_team = " ".join(tokens[1:]).strip()

    if not away_team or not home_team:
        raise ValueError("Could not parse both teams")

    return {
        "away_team": away_team,
        "home_team": home_team,
        "mode": mode,
    }
