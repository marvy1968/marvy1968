"""Read-only: the paper account's daily result next to SPY's (owner 2026-11-04: "on the days that alpaca finishes down, did it correlate
with the S&P being down"). Account: Alpaca portfolio history, 1-day bars (equity at each close). SPY: daily closes (SIP)."""

import numpy as np
import pandas as pd


def compare(acct: pd.Series, spy: pd.Series) -> list[str]:
    """acct / spy: daily closing equity / price indexed by date. Returns report lines."""
    df = pd.DataFrame({"acct": acct, "spy": spy}).dropna()
    r = df.pct_change().dropna()
    r = r[r["acct"].abs() > 1e-9] if len(r) else r                    # skip days the account did not move at all (no positions)
    if len(r) < 3:
        return ["- not enough days to compare"]
    up_a, dn_a = r["acct"] > 0, r["acct"] < 0
    up_s, dn_s = r["spy"] > 0, r["spy"] < 0
    corr = float(r["acct"].corr(r["spy"]))
    beta = float(np.cov(r["acct"], r["spy"])[0, 1] / np.var(r["spy"], ddof=1)) if r["spy"].var() > 0 else float("nan")
    lines = [f"## Account vs SPY, {r.index[0]} .. {r.index[-1]} ({len(r)} trading days with account moves)",
             f"- account DOWN days: {int(dn_a.sum())}; SPY was also down on {int((dn_a & dn_s).sum())} of them "
             f"({(dn_a & dn_s).sum() / max(1, dn_a.sum()):.0%})",
             f"- account UP days: {int(up_a.sum())}; SPY was also up on {int((up_a & up_s).sum())} of them "
             f"({(up_a & up_s).sum() / max(1, up_a.sum()):.0%})",
             f"- same direction on {int(((up_a & up_s) | (dn_a & dn_s)).sum())} of {len(r)} days; correlation {corr:+.2f}; beta {beta:+.2f} "
             f"(account % per 1% SPY move)",
             f"- account avg on SPY-down days {r.loc[dn_s, 'acct'].mean():+.2%} ({int(dn_s.sum())} days), on SPY-up days "
             f"{r.loc[up_s, 'acct'].mean():+.2%} ({int(up_s.sum())} days)",
             "| Date | Account | SPY | Same way |", "|---|---|---|---|"]
    for d, row in r.iterrows():
        same = "yes" if np.sign(row["acct"]) == np.sign(row["spy"]) else "no"
        lines.append(f"| {d} | {row['acct']:+.2%} | {row['spy']:+.2%} | {same} |")
    return lines


def run(start: str = "2026-10-09") -> list[str]:
    import requests

    from .data import _fetch, _headers
    api = "https://paper-api.alpaca.markets/v2"
    h = requests.get(f"{api}/account/portfolio/history", headers=_headers(), timeout=30,
                     params={"timeframe": "1D", "date_start": start, "extended_hours": "false"}).json()
    ts = pd.to_datetime(pd.Series(h.get("timestamp") or []), unit="s", utc=True).dt.tz_convert("America/New_York").dt.date
    acct = pd.Series(h.get("equity") or [], index=ts, dtype=float)
    acct = acct[acct > 0]
    today = pd.Timestamp.now(tz="America/New_York")
    end = (today - pd.Timedelta(minutes=20)).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")   # SIP refuses the last 15 minutes
    spy_df = _fetch("SPY", "1Day", f"{start}T00:00:00Z", end, "sip")
    idx = pd.DatetimeIndex(spy_df.index)
    idx = idx.tz_convert("America/New_York") if idx.tz is not None else idx
    spy = pd.Series(spy_df["close"].to_numpy(float), index=idx.date)
    return compare(acct, spy)
