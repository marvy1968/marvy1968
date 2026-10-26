"""Send consult/gemini_brief.md (plus an optional question) to Gemini and save the reply in consult/replies/.
Needs GEMINI_API_KEY in the environment (/opt/marv-bot/.env); the key is never printed. Usage: python tools/gemini_consult.py [question...]"""
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    env = ROOT / ".env"
    names = []
    if not key:                                         # the VM keeps the key in /opt/marv-bot/.env
        try:
            for line in env.read_text().splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    names.append(k.strip())
                    if k.strip() == "GEMINI_API_KEY":
                        key = v.strip().strip("'\"")
        except OSError as exc:
            print(f"cannot read {env}: {exc.__class__.__name__}")
    if not key:
        print("GEMINI_API_KEY not found. Variable names seen in .env: " + (", ".join(n for n in names if n.isupper() and "GEMINI" in n or "GOOGLE" in n) or "none with GEMINI/GOOGLE"))
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
