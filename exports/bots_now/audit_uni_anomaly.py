#!/usr/bin/env python3
"""
Forensic audit of UNI price anomaly in Yahoo Finance data
"""
import yfinance as yf
import pandas as pd

print("Checking Yahoo Finance UNI-USD data around late Feb 2025...")
df = yf.download("UNI-USD", start="2025-02-20", end="2025-03-10", interval="1d", progress=False, auto_adjust=False)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = [c[0].lower() for c in df.columns]
else:
    df.columns = [c.lower() for c in df.columns]

print(df[["open", "high", "low", "close", "volume"]])
