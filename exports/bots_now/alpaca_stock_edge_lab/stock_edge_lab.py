#!/usr/bin/env python3
import argparse, os, json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import duckdb, numpy as np, pandas as pd
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parent
DB=ROOT/"data"/"stock_history.duckdb"
UNIVERSE=ROOT/"config"/"universe.txt"
REPORTS=ROOT/"reports"; REPORTS.mkdir(parents=True,exist_ok=True)
ROUND_TRIP_COST=0.0005
MC_SEED=42

def symbols():
    return [x.strip().upper() for x in UNIVERSE.read_text().splitlines() if x.strip() and not x.startswith("#")]

def download(years=5,feed=None):
    load_dotenv(ROOT/".env"); load_dotenv(Path.home()/".env")
    key=os.getenv("APCA_API_KEY_ID"); secret=os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret: raise SystemExit("Missing APCA_API_KEY_ID/APCA_API_SECRET_KEY")
    from alpaca.data.historical.stock import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    client=StockHistoricalDataClient(key,secret)
    end=datetime.now(timezone.utc)-timedelta(minutes=20)
    start=end-timedelta(days=int(years*365.25))
    kwargs=dict(symbol_or_symbols=symbols(),timeframe=TimeFrame.Hour,start=start,end=end,adjustment="all")
    if feed: kwargs["feed"]=feed
    bars=client.get_stock_bars(StockBarsRequest(**kwargs)).df
    if bars.empty: raise SystemExit("No historical stock bars returned")
    bars=bars.reset_index()
    keep=["symbol","timestamp","open","high","low","close","volume","trade_count","vwap"]
    for c in keep:
        if c not in bars: bars[c]=np.nan
    bars=bars[keep]; bars["timestamp"]=pd.to_datetime(bars["timestamp"],utc=True)
    con=duckdb.connect(str(DB)); con.register("bars_df",bars)
    con.execute("CREATE OR REPLACE TABLE bars AS SELECT * FROM bars_df")
    con.execute("CREATE INDEX IF NOT EXISTS idx_bars ON bars(symbol,timestamp)")
    con.close()
    print("Database:",DB); print("Rows:",len(bars)); print("Symbols:",bars.symbol.nunique())

def load_bars():
    if not DB.exists(): raise SystemExit(f"Missing {DB}; run download first")
    con=duckdb.connect(str(DB),read_only=True)
    df=con.execute("SELECT symbol,timestamp,open,high,low,close,volume FROM bars ORDER BY symbol,timestamp").df()
    con.close(); df["timestamp"]=pd.to_datetime(df["timestamp"],utc=True); return df

def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def atr(g,n=14):
    pc=g.close.shift(1)
    return pd.concat([(g.high-g.low),(g.high-pc).abs(),(g.low-pc).abs()],axis=1).max(axis=1).rolling(n).mean()
def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0).rolling(n).mean(); dn=(-d.clip(upper=0)).rolling(n).mean()
    rs=up/dn.replace(0,np.nan); return 100-(100/(1+rs))

def features(g):
    g=g.sort_values("timestamp").copy()
    g["ema20"]=ema(g.close,20); g["ema50"]=ema(g.close,50); g["ema200"]=ema(g.close,200)
    g["atr"]=atr(g); g["rsi"]=rsi(g.close); g["ret5"]=g.close.pct_change(5); g["ret20"]=g.close.pct_change(20)
    g["vol20"]=g.volume.rolling(20).mean(); g["vol_ratio"]=g.volume/g.vol20.replace(0,np.nan)
    g["hh20"]=g.high.shift(1).rolling(20).max(); g["pullback_atr"]=(g.close-g.ema20)/g.atr.replace(0,np.nan)
    g["breakout_atr"]=(g.close-g.hh20)/g.atr.replace(0,np.nan)
    g["sig_breakout"]=(g.close>g.hh20)&(g.ema20>g.ema50)&(g.ema50>g.ema200)&(g.vol_ratio>=1.2)&(g.breakout_atr>=0)
    g["sig_pullback"]=(g.ema20>g.ema50)&(g.ema50>g.ema200)&(g.pullback_atr.between(-0.8,0.25))&(g.rsi.between(40,60))
    g["sig_momentum"]=(g.ret20>0.08)&(g.ret5>0)&(g.ema20>g.ema50)&(g.rsi.between(50,72))
    g["sig_reversal"]=(g.close>g.ema200)&(g.rsi<32)&(g.pullback_atr<-1.3)
    for h in [3,6,12,24]: g[f"future_{h}"]=g.close.shift(-h)/g.close-1
    return g

def metric(ret):
    r=np.asarray(ret,dtype=float); r=r[np.isfinite(r)]
    if not len(r): return dict(trades=0,roi_pct=0.0,win_rate_pct=0.0,profit_factor=0.0,expectancy_bps=0.0,max_drawdown_pct=0.0)
    equity=np.cumprod(1+r); peak=np.maximum.accumulate(np.r_[1.0,equity])[1:]; dd=equity/peak-1
    wins=r[r>0].sum(); losses=abs(r[r<0].sum()); pf=999.0 if losses==0 and wins>0 else (wins/losses if losses else 0.0)
    return dict(trades=int(len(r)),roi_pct=float((equity[-1]-1)*100),win_rate_pct=float((r>0).mean()*100),
                profit_factor=float(pf),expectancy_bps=float(r.mean()*10000),max_drawdown_pct=float(abs(dd.min())*100))

def monte_carlo(ret,n):
    r=np.asarray(ret,dtype=float); r=r[np.isfinite(r)]
    if len(r)<20: return dict(iterations=n,trades=len(r),warning="insufficient trades")
    rng=np.random.default_rng(MC_SEED); finals=[]; dds=[]
    for _ in range(n):
        s=rng.choice(r,size=len(r),replace=True); eq=np.cumprod(1+s); peak=np.maximum.accumulate(np.r_[1.0,eq])[1:]
        finals.append((eq[-1]-1)*100); dds.append(abs((eq/peak-1).min())*100)
    a=np.asarray(finals)
    return dict(iterations=n,trades=len(r),probability_profitable_pct=float((a>0).mean()*100),
                median_roi_pct=float(np.median(a)),roi_5th_pct=float(np.percentile(a,5)),
                roi_95th_pct=float(np.percentile(a,95)),dd_95th_pct=float(np.percentile(dds,95)))

def run(mc=10000,train_days=730,block_days=90):
    raw=load_bars()
    feat=pd.concat([features(g) for _,g in raw.groupby("symbol")],ignore_index=True)
    start,end=feat.timestamp.min(),feat.timestamp.max(); forward_start=start+pd.Timedelta(days=train_days)
    fwd=feat[feat.timestamp>=forward_start].copy()
    rows=[]; cache={}
    for strat in ["breakout","pullback","momentum","reversal"]:
        sig=f"sig_{strat}"
        for h in [3,6,12,24]:
            selected=fwd[fwd[sig]&fwd[f"future_{h}"].notna()].copy()
            selected["net_return"]=selected[f"future_{h}"]-ROUND_TRIP_COST
            overall=metric(selected.net_return); block_stats=[]; cur=forward_start
            while cur<end:
                nxt=min(cur+pd.Timedelta(days=block_days),end+pd.Timedelta(seconds=1))
                m=metric(selected.loc[(selected.timestamp>=cur)&(selected.timestamp<nxt),"net_return"])
                block_stats.append({**m,"start":str(cur),"end":str(nxt),"profitable":m["roi_pct"]>0}); cur=nxt
            pct=100*sum(x["profitable"] for x in block_stats)/max(len(block_stats),1)
            rows.append(dict(strategy=strat,holding_bars=h,profitable_block_pct=pct,**overall))
            cache[(strat,h)]=(selected,block_stats)

    rank=pd.DataFrame(rows)
    rank["prelim_pass"]=(rank.trades>=50)&(rank.profit_factor>=1.25)&(rank.expectancy_bps>=3)&(rank.profitable_block_pct>=50)&(rank.max_drawdown_pct<=25)
    rank["mc_probability_profitable_pct"]=0.0; rank["mc_dd_95th_pct"]=np.nan; rank["passed"]=False
    mc_details={}
    for idx,row in rank[rank.prelim_pass].iterrows():
        sel=cache[(row.strategy,int(row.holding_bars))][0]; m=monte_carlo(sel.net_return,mc)
        mc_details[f"{row.strategy}_{int(row.holding_bars)}"]=m
        rank.loc[idx,"mc_probability_profitable_pct"]=m.get("probability_profitable_pct",0.0)
        rank.loc[idx,"mc_dd_95th_pct"]=m.get("dd_95th_pct",np.nan)
        rank.loc[idx,"passed"]=m.get("probability_profitable_pct",0)>=65
    rank["score"]=rank.profit_factor.clip(upper=5)*10+rank.profitable_block_pct*.2+rank.expectancy_bps*.15-rank.max_drawdown_pct*.25
    rank=rank.sort_values("score",ascending=False).reset_index(drop=True)
    passing=rank[rank.passed]; verdict="STOCK_EDGE_SURVIVED" if len(passing) else "STOCK_EDGE_NOT_YET_VALIDATED"
    champion=None; by_symbol={}
    if len(passing):
        champ=passing.iloc[0]; champion=champ.to_dict(); sel=cache[(champ.strategy,int(champ.holding_bars))][0]
        for sym,g in sel.groupby("symbol"): by_symbol[sym]=metric(g.net_return)
    report=dict(methodology=dict(universe=symbols(),timeframe="1Hour",development_days=train_days,
               forward_start=str(forward_start),forward_end=str(end),block_days=block_days,
               round_trip_cost_bps=ROUND_TRIP_COST*10000,parameter_optimization=False,execution_enabled=False),
               verdict=verdict,champion=champion,champion_by_symbol=by_symbol,monte_carlo=mc_details,
               gates=dict(minimum_trades=50,minimum_pf=1.25,minimum_expectancy_bps=3,
                          minimum_profitable_block_pct=50,maximum_drawdown_pct=25,minimum_mc_probability_pct=65))
    (REPORTS/"stock_edge_lab_v1_latest.json").write_text(json.dumps(report,indent=2,default=str))
    rank.to_csv(REPORTS/"stock_edge_lab_v1_rankings.csv",index=False)
    print("\n===== STOCK EDGE LAB V1 ====="); print(rank.head(16).to_string(index=False))
    print("\nFINAL VERDICT:",verdict); print("JSON:",REPORTS/"stock_edge_lab_v1_latest.json")

def main():
    ap=argparse.ArgumentParser(); sp=ap.add_subparsers(dest="cmd",required=True)
    d=sp.add_parser("download"); d.add_argument("--years",type=float,default=5); d.add_argument("--feed",default=None)
    r=sp.add_parser("run"); r.add_argument("--mc",type=int,default=10000); r.add_argument("--train-days",type=int,default=730); r.add_argument("--block-days",type=int,default=90)
    a=ap.parse_args(); download(a.years,a.feed) if a.cmd=="download" else run(a.mc,a.train_days,a.block_days)

if __name__=="__main__": main()
