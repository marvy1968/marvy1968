"""On/off switch for systems:  python -m fxexp.switch [on|off] <system-id>   (no args = show status).
Turning a system off stops new entries and its daily trailing; its open trades keep their OANDA stops."""
import json, sys
from .strategies import BY_SID
CFG = "config/config.json"

def main(argv=None, path=CFG):
    argv = sys.argv[1:] if argv is None else argv
    cfg = json.load(open(path)); on = cfg["systems"]
    if argv:
        if len(argv) != 2 or argv[0] not in ("on", "off") or argv[1] not in BY_SID:
            sys.exit(f"usage: python -m fxexp.switch on|off <{'|'.join(BY_SID)}>")
        act, sid = argv
        if act == "on" and sid not in on: on.append(sid)
        if act == "off" and sid in on: on.remove(sid)
        json.dump(cfg, open(path, "w"), indent=2)
    for sid in BY_SID: print(f"{'ON ' if sid in on else 'off'}  {sid}")
    return cfg

if __name__ == "__main__": main()
