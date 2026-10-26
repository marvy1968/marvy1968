"""Send consult/gemini_brief.md (plus an optional question) to Gemini and save the reply in consult/replies/.
Needs GEMINI_API_KEY in the environment (/opt/marv-bot/.env); the key is never printed. Usage: python tools/gemini_consult.py [question...]"""
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    env = ROOT / ".env"
    if not key and env.exists():                       # the VM keeps the key in /opt/marv-bot/.env
        for line in env.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
    if not key:
        print("GEMINI_API_KEY is not set (add it to /opt/marv-bot/.env)")
        return 2
    text = (ROOT / "consult/gemini_brief.md").read_text()
    if len(sys.argv) > 1:
        text += "\n\n## Follow-up question\n" + " ".join(sys.argv[1:])
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                      headers={"x-goog-api-key": key}, json={"contents": [{"parts": [{"text": text}]}]}, timeout=300)
    if r.status_code != 200:
        print(f"Gemini error {r.status_code}: {r.text[:300].replace(key, '***')}")
        return 1
    out = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
    path = ROOT / "consult/replies" / f"gemini-{time.strftime('%Y%m%d-%H%M%S')}.md"
    try:
        path.write_text(out)
        print(f"saved {path.relative_to(ROOT)}\n")
    except OSError:                                    # the service user may not own the folder: the job result carries the reply
        pass
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
