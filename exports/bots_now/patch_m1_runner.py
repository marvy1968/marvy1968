import re
from pathlib import Path

fpath = Path("/home/moonrider1968/bots/run_m1_fixed_r_backtest.py")
code = fpath.read_text()

# Enhanced file discovery search paths
new_search_logic = """def find_m1_file(symbol: str) -> Optional[Path]:
    search_dirs = [
        Path("/home/moonrider1968/bots/oanda_data/m1"),
        Path("/home/moonrider1968/bots/oanda_data"),
        Path("/home/moonrider1968/bots/data/m1"),
        Path("/home/moonrider1968/bots/data"),
        Path("/home/moonrider1968/bots"),
    ]
    patterns = [
        f"{symbol}_M1.csv",
        f"{symbol}_m1.csv",
        f"{symbol.lower()}_m1.csv",
        f"{symbol.upper()}_M1.csv"
    ]
    for d in search_dirs:
        if d.exists():
            for p in patterns:
                target = d / p
                if target.exists():
                    return target
    return None"""

code = re.sub(r"def find_m1_file\(symbol: str\).*?return None", new_search_logic, code, flags=re.DOTALL)
fpath.write_text(code)
print("✅ Search paths patched.")
