import re

path = "/home/moonrider1968/bots/trade_velez_weinstein_swing.py"
with open(path, "r") as f:
    code = f.read()

# Pattern matching the candidate generation loop inside fetch_signals
old_pattern = r"(def fetch_signals\(\):[\s\S]*?candidates = \[\])[\s\S]*?(return candidates)"

new_loop = '''def fetch_signals():
    all_syms = list(set(GROWTH_SYMBOLS + SECTOR_55_SYMBOLS + [BENCHMARK]))
    print(f"[*] Downloading market data for {len(all_syms)} symbols...")
    raw = yf.download(all_syms, period="1y", interval="1d", progress=False, group_by="ticker")

    import pandas as pd
    candidates = []

    # Benchmark trend
    try:
        spy_df = raw[BENCHMARK].dropna() if isinstance(raw.columns, pd.MultiIndex) else raw.dropna()
        spy_close = float(spy_df['Close'].iloc[-1])
        spy_sma50 = float(spy_df['Close'].rolling(50).mean().iloc[-1])
        market_bullish = spy_close >= spy_sma50
    except Exception:
        market_bullish = True

    scan_list = list(set(GROWTH_SYMBOLS + SECTOR_55_SYMBOLS))
    for sym in scan_list:
        if sym == BENCHMARK:
            continue
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if sym not in raw.columns.levels[0]:
                    continue
                df = raw[sym].dropna().copy()
            else:
                continue

            if len(df) < 150:
                continue

            close = float(df['Close'].iloc[-1])
            sma200 = float(df['Close'].rolling(200).mean().iloc[-1]) if len(df) >= 200 else float(df['Close'].rolling(150).mean().iloc[-1])
            sma50 = float(df['Close'].rolling(50).mean().iloc[-1])
            sma20 = float(df['Close'].rolling(20).mean().iloc[-1])

            # Weinstein Stage 2: Price above rising 50/200 SMA
            stage2 = close > sma200 and close > sma50 and sma50 > sma200

            # Velez V-Bar / Momentum filter: close near upper 20 SMA band
            velez_setup = close >= sma20

            if stage2 and velez_setup:
                score = round(((close - sma50) / sma50) * 100, 2)
                candidates.append({
                    "symbol": sym,
                    "close": round(close, 2),
                    "sma200": round(sma200, 2),
                    "sma50": round(sma50, 2),
                    "score": score,
                    "sector": "Stage 2 Leader"
                })
        except Exception:
            continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates'''

code = re.sub(old_pattern, new_loop, code)

with open(path, "w") as f:
    f.write(code)

print("[✓] Patched fetch_signals with robust MultiIndex parsing and Stage 2 math.")
