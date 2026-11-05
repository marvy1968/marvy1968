"""Sector news engine + swing retest (research only).

    python news_engine.py --start 2017-01-01 --end 2021-12-31

HYPOTHESIS (from the news/no-news literature): price drops WITHOUT news tend to reverse; drops WITH news
tend to continue. The swing study lost money on dips that kept falling -> filter them by news.

ENGINE
  1. Headlines + summaries from Alpaca's news API (Benzinga), cached on disk, fetched once.
  2. classify(): finance keyword lexicon -> score in [-1, 1] and a negative-event flag
     (downgrade, guidance cut, offering, lawsuit/probe, failed trial, bankruptcy ...).
  3. Stock news state at each close: articles and negative events in the last 3 trading days.
     An article counts for day d only if published before d's 16:00 ET close (else it counts for d+1).
  4. Sector news index: stocks mapped to 11 sectors by SEC SIC code; daily sector sentiment over
     5 days, ranked against that sector's own previous 250 days. Bottom third = "negative regime".

PREREGISTERED ARMS (same swing rules as swing.py, only the entry filter differs)
  A  baseline                     (swing v1)
  B  no-news dips      [PRIMARY]  skip if the stock had ANY article in the last 3 days
  C  no bad news                  skip if the stock had a NEGATIVE-EVENT article in the last 3 days
  D  sector news                  skip if the stock's sector is in a negative news regime
PASS RULE: primary arm B needs avg trade > 0 with t >= 2.0 and positive in BOTH halves.
Secondary arms (C, D) need t >= 2.5 because testing several arms raises the chance of a lucky one.
"""
import argparse
import glob
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

NEWS_DAYS = 3
SECTOR_DAYS = 5
SECTOR_LOOKBACK = 250
SECTOR_NEG_PCT = 1 / 3
CACHE = "data_cache"

NEG = [r"downgrade[sd]?", r"cut to (?:sell|underperform|neutral|hold)", r"lower(?:s|ed)? (?:its |fy |full[- ]year )?guidance",
       r"cuts? (?:its |fy |full[- ]year )?guidance", r"(?:misses|missed|below) (?:estimates|expectations|consensus)",
       r"\bmisses\b", r"weak(?:er)?[- ]than[- ]expected", r"(?:public|secondary|stock|share|equity) offering",
       r"priced? (?:its |an? )?offering", r"\bdilut", r"class action", r"\blawsuit", r"investigation", r"\bprobe\b",
       r"subpoena", r"\bfraud", r"sec charges", r"bankruptcy", r"chapter 11", r"going concern", r"\bdefault",
       r"delist", r"\brecall", r"trading halt", r"resign", r"steps down", r"complete response letter",
       r"fda (?:rejects|declines)", r"(?:trial|study) (?:fails|failed|misses)", r"fails? to meet", r"discontinu",
       r"layoffs?", r"restructuring", r"impairment", r"short report", r"short seller", r"\bwarns?\b",
       r"price target (?:cut|lowered)", r"lowers? price target", r"suspend", r"plunge", r"tumble", r"\bsinks?\b"]
POS = [r"upgrade[sd]?", r"raise[sd]? (?:its |fy |full[- ]year )?guidance", r"(?:beats?|tops?|above) (?:estimates|expectations|consensus)",
       r"\bbeats\b", r"record (?:revenue|sales|quarter|earnings)", r"buyback", r"repurchase", r"raises? (?:its |quarterly )?dividend",
       r"fda approv", r"\bapproval\b", r"to be acquired", r"merger agreement", r"\bacquire[sd]? by\b", r"wins? contract",
       r"contract award", r"partnership", r"raises? price target", r"price target raised", r"initiated? (?:at|with) (?:buy|outperform|overweight)",
       r"\bsurges?\b", r"\bsoars?\b", r"\bjumps?\b", r"\brall(?:y|ies)\b", r"strong(?:er)?[- ]than[- ]expected"]
_NEG, _POS = re.compile("|".join(NEG)), re.compile("|".join(POS))


def classify(text):
    """-> (score in [-1,1], negative_event: bool)."""
    t = (text or "").lower()
    n, p = len(_NEG.findall(t)), len(_POS.findall(t))
    score = 0.0 if n + p == 0 else (p - n) / (p + n)
    return score, bool(n and score < 0)


# ---------------- SEC industry codes -> 11 sectors ----------------
def sic_to_sector(sic):
    try:
        s = int(sic)
    except (TypeError, ValueError):
        return None
    R = [((100, 999), "Consumer Staples"), ((1311, 1389), "Energy"), ((1000, 1499), "Materials"),
         ((1500, 1799), "Industrials"), ((2000, 2199), "Consumer Staples"), ((2200, 2399), "Consumer Disc."),
         ((2400, 2499), "Materials"), ((2500, 2599), "Consumer Disc."), ((2600, 2699), "Materials"),
         ((2700, 2799), "Communication"), ((2830, 2839), "Health Care"), ((2840, 2844), "Consumer Staples"),
         ((2800, 2899), "Materials"), ((2900, 2999), "Energy"), ((3100, 3199), "Consumer Disc."),
         ((3000, 3399), "Materials"), ((3400, 3499), "Industrials"), ((3570, 3579), "Technology"),
         ((3500, 3599), "Industrials"), ((3630, 3639), "Consumer Disc."), ((3600, 3699), "Technology"),
         ((3711, 3716), "Consumer Disc."), ((3700, 3799), "Industrials"), ((3840, 3851), "Health Care"),
         ((3800, 3899), "Technology"), ((3900, 3999), "Consumer Disc."), ((4000, 4799), "Industrials"),
         ((4800, 4899), "Communication"), ((4900, 4999), "Utilities"), ((5120, 5129), "Health Care"),
         ((5140, 5149), "Consumer Staples"), ((5000, 5199), "Industrials"), ((5400, 5499), "Consumer Staples"),
         ((5912, 5912), "Consumer Staples"), ((5200, 5999), "Consumer Disc."), ((6798, 6798), "Real Estate"),
         ((6500, 6553), "Real Estate"), ((6000, 6799), "Financials"), ((7370, 7379), "Technology"),
         ((7000, 7299), "Consumer Disc."), ((7300, 7399), "Industrials"), ((7800, 7999), "Communication"),
         ((8000, 8099), "Health Care"), ((8200, 8299), "Consumer Disc."), ((8000, 8999), "Industrials")]
    for (lo, hi), sec in R:
        if lo <= s <= hi:
            return sec
    return None


def load_sectors(symbols, path=os.path.join(CACHE, "sec_sectors.json")):
    import requests
    ua = os.getenv("SEC_USER_AGENT")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    need = [s for s in symbols if s not in cache]
    if need:
        if not ua:
            raise SystemExit('SEC requires a contact: export SEC_USER_AGENT="Your Name your@email.com"')
        hdr = {"User-Agent": ua}
        tick = requests.get("https://www.sec.gov/files/company_tickers.json", headers=hdr, timeout=30).json()
        cik = {v["ticker"].upper(): int(v["cik_str"]) for v in tick.values()}
        print(f"SEC: looking up industry codes for {len(need)} symbols (cached after first run)...")
        for k, s in enumerate(need):
            if s not in cik:
                cache[s] = None
                continue
            try:
                r = requests.get(f"https://data.sec.gov/submissions/CIK{cik[s]:010d}.json", headers=hdr, timeout=30)
                cache[s] = r.json().get("sic") if r.ok else None
            except Exception:   # noqa: BLE001
                cache[s] = None
            time.sleep(0.12)                      # SEC fair-access limit: 10 requests/second
            if k % 200 == 0:
                json.dump(cache, open(path, "w"))
        json.dump(cache, open(path, "w"))
    return {s: sic_to_sector(cache.get(s)) for s in symbols}


# ---------------- news download ----------------
def fetch_news(symbols, start, end, batch=20):
    """Headlines + summaries for all symbols, cached per batch (safe to interrupt and resume)."""
    import config
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest
    d = os.path.join(CACHE, "news")
    os.makedirs(d, exist_ok=True)
    client = NewsClient(config.API_KEY, config.API_SECRET, raw_data=True)
    t0 = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    t1 = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc)
    frames = []
    batches = [symbols[k:k + batch] for k in range(0, len(symbols), batch)]
    for n, grp in enumerate(batches):
        f = os.path.join(d, f"{start}_{end}_{'-'.join(grp)[:120]}_{len(grp)}.pkl")
        if os.path.exists(f):
            frames.append(pd.read_pickle(f))
            continue
        rows, token = [], None
        while True:
            for attempt in range(5):
                try:
                    kw = dict(symbols=",".join(grp), start=t0, end=t1, sort="asc")   # SDK pages through all results
                    if token:
                        kw["page_token"] = token
                    resp = client.get_news(NewsRequest(**kw))
                    break
                except Exception as e:   # noqa: BLE001
                    time.sleep(2 ** attempt)
                    if attempt == 4:
                        raise SystemExit(f"news download failed: {e}")
            for a in resp.get("news", []):
                rows.append((a["created_at"], tuple(a.get("symbols") or ()), a.get("headline", ""), a.get("summary", "")))
            token = resp.get("next_page_token")
            time.sleep(0.31)                      # stay under 200 requests/minute
            if not token:
                break
        df = pd.DataFrame(rows, columns=["created_at", "symbols", "headline", "summary"])
        df.to_pickle(f)
        frames.append(df)
        print(f"  news batch {n + 1}/{len(batches)}: {len(df)} articles")
    news = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["created_at", "symbols", "headline", "summary"])
    return news.drop_duplicates(subset=["created_at", "headline"])


# ---------------- features (all causal) ----------------
def assign_trading_day(created_at, dates):
    """First trading day whose 16:00 ET close is at or after publication."""
    t = pd.DatetimeIndex(pd.to_datetime(created_at, utc=True)).tz_convert("America/New_York").as_unit("ns")
    closes = (pd.DatetimeIndex(dates).tz_localize("America/New_York") + pd.Timedelta(hours=16)).as_unit("ns")
    return np.searchsorted(closes.asi8, t.asi8, side="left")   # == len(dates) -> after the last day


def news_features(news, dates, symbols, sectors):
    dates = pd.DatetimeIndex(dates)
    cols = sorted(symbols)
    sec_names = sorted({s for s in sectors.values() if s})
    cnt = pd.DataFrame(0.0, index=dates, columns=cols)
    neg = cnt.copy()
    sec_sum = pd.DataFrame(0.0, index=dates, columns=sec_names)
    sec_n = sec_sum.copy()
    if len(news):
        k = assign_trading_day(news["created_at"], dates)
        sc = [classify(f"{h} {s}") for h, s in zip(news["headline"], news["summary"])]
        df = pd.DataFrame({"k": k, "sym": list(news["symbols"]), "sc": [x[0] for x in sc],
                           "ev": [float(x[1]) for x in sc]}).explode("sym")
        df = df[(df["k"] < len(dates)) & df["sym"].isin(set(cols))]
        if len(df):
            df["date"] = dates[df["k"].to_numpy(int)]
            g = df.groupby(["date", "sym"])
            cnt = g.size().unstack(fill_value=0).reindex(index=dates, columns=cols, fill_value=0).astype(float)
            neg = g["ev"].sum().unstack(fill_value=0).reindex(index=dates, columns=cols, fill_value=0)
            df["sector"] = df["sym"].map(sectors)
            gs = df.dropna(subset=["sector"]).groupby(["date", "sector"])["sc"]
            sec_sum = gs.sum().unstack(fill_value=0).reindex(index=dates, columns=sec_names, fill_value=0)
            sec_n = gs.size().unstack(fill_value=0).reindex(index=dates, columns=sec_names, fill_value=0)
    cnt3 = cnt.rolling(NEWS_DAYS, min_periods=1).sum()
    neg3 = neg.rolling(NEWS_DAYS, min_periods=1).sum()
    idx = sec_sum.rolling(SECTOR_DAYS, min_periods=1).sum() / (sec_n.rolling(SECTOR_DAYS, min_periods=1).sum() + 10)
    pct = idx.rolling(SECTOR_LOOKBACK, min_periods=60).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)
    sec_neg = pct < SECTOR_NEG_PCT
    sec_neg_stock = pd.DataFrame({s: (sec_neg[sectors[s]] if sectors.get(s) in sec_neg else False)
                                  for s in cols}, index=dates).astype(bool)
    return dict(count3=cnt3, neg3=neg3, sector_neg=sec_neg_stock, sector_index=idx, sector_pct=pct)


# ---------------- retest ----------------
def _row(name, tr, eq):
    import swing
    if tr.empty:
        return dict(arm=name, trades=0)
    r = tr["ret_pct"]
    yrs = sorted(pd.to_datetime(tr["entry_date"]).dt.year.unique())
    cut = yrs[int(len(yrs) * 2 / 3)] if len(yrs) >= 3 else yrs[-1]
    y = pd.to_datetime(tr["entry_date"]).dt.year
    p = swing.perf(eq)
    return dict(arm=name, trades=len(tr), avg_pct=round(r.mean(), 3),
                t_stat=round(r.mean() / (r.std() / np.sqrt(len(r))), 2) if len(r) > 1 else np.nan,
                win_pct=round((r > 0).mean() * 100, 1),
                profit_factor=round(r[r > 0].sum() / -r[r < 0].sum(), 2) if (r < 0).any() else np.inf,
                pick_avg=round(r[y < cut].mean(), 3), check_avg=round(r[y >= cut].mean(), 3),
                cagr_pct=p["cagr_pct"], max_dd_pct=p["max_dd_pct"])


def retest(panel, feats, start, end, bench, out="news_results"):
    import swing
    os.makedirs(out, exist_ok=True)
    syms = sorted(panel)
    al = lambda m: (~m.reindex(columns=syms).fillna(False).astype(bool))     # noqa: E731
    arms = {"A baseline": None,
            "B no-news dips [PRIMARY]": al(feats["count3"] > 0),
            "C no bad news": al(feats["neg3"] > 0),
            "D sector news": al(feats["sector_neg"])}
    rows, base_tr = [], None
    for name, allow in arms.items():
        print(f"  running arm {name} ...")
        tr, eq = swing.run(panel, start, end, allow=allow)
        tr.to_csv(os.path.join(out, f"trades_{name.split()[0]}.csv"), index=False)
        rows.append(_row(name, tr, eq))
        if base_tr is None:
            base_tr = tr
    for b, s in bench.items():
        p = swing.perf(s.loc[pd.Timestamp(start):pd.Timestamp(end)])
        rows.append(dict(arm=f"buy & hold {b}", cagr_pct=p["cagr_pct"], max_dd_pct=p["max_dd_pct"]))
    table = pd.DataFrame(rows).set_index("arm")
    print("\n================ SECTOR NEWS ENGINE: SWING RETEST ================")
    print("NOTE: survivorship bias (today's listings only) still flatters every arm.")
    print(table.to_string())
    table.to_csv(os.path.join(out, "arms.csv"))

    # the most direct evidence: baseline trades split by news state at the signal
    if base_tr is not None and len(base_tr):
        sd = pd.to_datetime(base_tr["signal_date"])
        c3 = [feats["count3"].at[d, s] if s in feats["count3"] else 0 for d, s in zip(sd, base_tr["symbol"])]
        n3 = [feats["neg3"].at[d, s] if s in feats["neg3"] else 0 for d, s in zip(sd, base_tr["symbol"])]
        g = np.where(np.array(n3) > 0, "negative-event news", np.where(np.array(c3) > 0, "other news", "no news"))
        bt = base_tr.assign(news=g, bad_exit=(base_tr["reason"] != "engine_neutral"))
        split = bt.groupby("news").agg(trades=("ret_pct", "count"), avg_pct=("ret_pct", "mean"),
                                       win_pct=("ret_pct", lambda x: (x > 0).mean() * 100),
                                       stop_or_time_pct=("bad_exit", lambda x: x.mean() * 100))
        print("\n--- baseline trades split by news at the signal (no re-run, same trades) ---")
        print(split.round(2).to_string())
        print(f"news coverage: {np.mean(np.array(c3) > 0) * 100:.0f}% of baseline signals had an article in the prior 3 days")

    b = table.loc["B no-news dips [PRIMARY]"]
    ok_b = b.get("avg_pct", -1) > 0 and b.get("t_stat", 0) >= 2.0 and b.get("pick_avg", -1) > 0 and b.get("check_avg", -1) > 0
    print(f"\nPRIMARY (B): {'PASS' if ok_b else 'FAIL'}  (needs avg > 0, t >= 2.0, both halves positive)")
    for arm in ("C no bad news", "D sector news"):
        r = table.loc[arm]
        ok = r.get("avg_pct", -1) > 0 and r.get("t_stat", 0) >= 2.5 and r.get("pick_avg", -1) > 0 and r.get("check_avg", -1) > 0
        print(f"secondary ({arm.split()[0]}): {'PASS' if ok else 'FAIL'}  (needs t >= 2.5, both halves positive)")
    print(f"\nFiles written to {out}/")


def _find_swing_panel():
    files = glob.glob(os.path.join(CACHE, "daily_*.pkl"))
    return max(files, key=os.path.getsize) if files else None


def main():
    import swing
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--panel", help="daily panel cache to reuse (default: the swing study's)")
    ap.add_argument("--use-holdout", action="store_true")
    a = ap.parse_args()
    start, end = pd.Timestamp(a.start).date(), pd.Timestamp(a.end).date()
    if end > swing.DISCOVERY_END and not a.use_holdout:
        raise SystemExit(f"--end is after {swing.DISCOVERY_END}: HELD OUT. Freeze rules first.")
    path = a.panel or _find_swing_panel()
    if not path:
        raise SystemExit("run swing.py once first (its daily data is reused here)")
    print(f"using daily panel {path}")
    panel = pd.read_pickle(path)
    feats0 = {s: swing.obos_frame(d)[["adv20"]] for s, d in panel.items() if len(d) >= 30}
    adv = pd.DataFrame({s: f["adv20"] for s, f in feats0.items()}).sort_index()
    close = pd.DataFrame({s: panel[s]["close"] for s in adv.columns}).sort_index()
    mem = swing.membership(adv, close).loc[pd.Timestamp(start):pd.Timestamp(end)]
    members = sorted(mem.columns[mem.any()])
    print(f"universe members during the period: {len(members)} symbols")
    sectors = load_sectors(members)
    print(f"sector mapped: {sum(v is not None for v in sectors.values()) / len(members) * 100:.0f}% of members")
    news = fetch_news(members, start - timedelta(days=10), end + timedelta(days=1))
    print(f"articles: {len(news)}")
    feats = news_features(news, close.index, members, sectors)
    bench = {}
    for b in ("SPY", "IWM", "IJH"):
        p = swing.load_panel([b], start - timedelta(days=5), end + timedelta(days=1))
        if b in p:
            bench[b] = p[b]["close"]
    retest(panel, feats, start, end, bench)


if __name__ == "__main__":
    main()
