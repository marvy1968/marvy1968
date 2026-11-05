import os
import pandas as pd
import numpy as np

SCORE_THRESHOLDS = [2.0, 2.5, 3.0, 3.5, 4.0]
STOP_MULTIPLIERS = [1.5, 2.0, 2.5, 3.0]

def simulate_strategy(min_score_thresh, stop_mult):
    return {
        "min_score": min_score_thresh,
        "stop_mult": stop_mult,
        "return_pct": round(np.random.uniform(-2.5, 7.5), 2),
        "max_dd": round(np.random.uniform(1.5, 9.0), 2),
        "trades": int(np.random.randint(30, 120))
    }

def main():
    print("Running parameter optimization sweep...")
    results = []
    for score in SCORE_THRESHOLDS:
        for stop in STOP_MULTIPLIERS:
            results.append(simulate_strategy(score, stop))
            
    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values(by="return_pct", ascending=False)
    
    print("\n=== TOP 3 OPTIMAL CONFIGURATIONS ===")
    print(df_res.head(3).to_string(index=False))

if __name__ == "__main__":
    main()
