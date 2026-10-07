"""Official NBA injury report (PDF published through the day at ak-static.cms.nba.com).

Finds the most recent report from the last 18 hours, extracts its text and returns
{normalized team: [(player "First Last", status)]}.
"""

import io
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from .teams import normalize

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
URL = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{stamp}.pdf"
STATUS = r"(Out|Questionable|Doubtful|Probable|Available)"
PLAYER = re.compile(r"([A-Z][A-Za-z'\.\-]+(?:\s(?:Jr\.|Sr\.|II|III|IV))?),\s*"
                    r"([A-Z][A-Za-z'\.\-]+(?:\s[A-Z][A-Za-z'\.\-]+)?)\s+" + STATUS + r"\b")


def candidate_urls(now: datetime, hours: int = 18) -> list[str]:
    """Newest first. Reports are stamped hourly (older seasons) or every 15 minutes (newer)."""
    now = now.astimezone(ET)
    urls = []
    for back in range(hours * 4):
        t = now - timedelta(minutes=15 * back)
        t = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)
        ampm = t.strftime("%p")
        hour12 = t.strftime("%I")
        if t.minute == 0:
            urls.append(URL.format(stamp=f"{t:%Y-%m-%d}_{hour12}{ampm}"))
        urls.append(URL.format(stamp=f"{t:%Y-%m-%d}_{hour12}_{t.minute:02d}{ampm}"))
    return list(dict.fromkeys(urls))


def parse_report(text: str, teams: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Walk the report text; a team name sets the current team, player rows attach to it."""
    team_norm = {normalize(t): t for t in teams}
    out: dict[str, list] = {}
    current = None
    for line in text.splitlines():
        flat = normalize(line)
        for key in sorted(team_norm, key=len, reverse=True):
            if key and key in flat:
                current = key
                break
        if current is None:
            continue
        for last, first, status in PLAYER.findall(line):
            out.setdefault(current, []).append((f"{first} {last}".strip(), status.lower()))
    return out


def latest_report(now: datetime, teams: list[str]) -> dict[str, list[tuple[str, str]]]:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (marv-predict-bot)"
    for url in candidate_urls(now):
        try:
            resp = session.get(url, timeout=20)
        except requests.RequestException as exc:
            log.warning("NBA injury report unreachable: %s", exc)
            return {}
        if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
            continue
        try:
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(resp.content)).pages)
        except Exception as exc:
            log.warning("could not read NBA injury report %s: %s", url, exc)
            return {}
        log.info("NBA injury report: %s", url.rsplit("/", 1)[-1])
        return parse_report(text, teams)
    return {}
