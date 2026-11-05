#!/usr/bin/env python3
import argparse, json, itertools
from dataclasses import dataclass, asdict
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "crypto_history.duckdb"
REPORTS = ROOT / "reports" / "v4"
REPORTS.mkdir(parents=True, exist_ok=True)
START_EQUITY = 10000.0
MC_SEED = 42

def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def atr(df,n=14):
    pc=df.close.shift(1)
    tr=pd.concat([(df.high-df.low),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()

def adx(df,n=14):
    up=df.high.diff(); dn=-df.low.diff()
    plus=pd.Series(np.where((up>dn)&(up>0),up,0.0),index=df.index)
    minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=df.index)
    a=atr(df,n)
    pdi=100*plus.ewm(alpha=1/n,adjust=False).mean()/a
    mdi=100*minus.ewm(alpha=1/n,adjust=False).mean()/a
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return dx.ewm(alpha=1/n,adjust=False).mean()

def load(symbol):
    con=duckdb.connect(str(DB),read_only=True)
    df=con.execute("""
        SELECT timestamp, open, high, low, close, volume
        FROM bars WHERE symbol=? ORDER BY timestamp
    """,[symbol]).df()
    con.close()
    if df.empty: raise RuntimeError(f"No data for {symbol}")
    df["timestamp"]=pd.to_datetime(df["timestamp"],utc=True)
    return df.drop_duplicates("timestamp").set_index("timestamp").sort_index()

def resample4h(df):
    return df.resample("4h").agg(
        open=("open","first"),high=("high","max"),low=("low","min"),
        close=("close","last"),volume=("volume","sum")
    ).dropna()

def features(doge,btc):
    x=doge.copy(); b=btc.reindex(x.index).ffill()
    x["atr"]=atr(x,14)
    x["atr_pct"]=x["atr"]/x["close"]
    x["atr_rank"]=x["atr_pct"].rolling(100).rank(pct=True)
    x["adx"]=adx(x,14)
    x["ema50"]=ema(x.close,50)
    x["vol20"]=x.volume.rolling(20).mean()
    x["vol_ratio"]=x.volume/x["vol20"].replace(0,np.nan)
    x["btc_ema50"]=ema(b.close,50)
    x["btc_ema200"]=ema(b.close,200)
    x["btc_bull"]=(b.close>x["btc_ema50"])&(x["btc_ema50"]>x["btc_ema200"])
    x["btc_not_bear"]=~((b.close<x["btc_ema50"])&(x["btc_ema50"]<x["btc_ema200"]))
    x["dow"]=x.index.dayofweek
    x["hour"]=x.index.hour
    return x

def make_signal(x,p):
    hh=x.high.shift(1).rolling(int(p["lookback"])).max()
    breakout_atr=(x.close-hh)/x["atr"].replace(0,np.nan)
    s=((x.close>hh)&(x.close>x.ema50)&(x.adx>=p["adx_min"])
       &(x.atr_rank>=p["min_atr_rank"])&(x.vol_ratio>=p["min_vol_ratio"])
       &(breakout_atr>=p["min_breakout_atr"]))
    if p["btc_filter"]=="bull":
        s &= x.btc_bull
    elif p["btc_filter"]=="not_bear":
        s &= x.btc_not_bear
    if p["exclude_weekend"]:
        s &= x.dow<5
    return s.fillna(False)

@dataclass
class Trade:
    entry_time:str; exit_time:str; entry:float; exit:float; pnl:float
    return_pct:float; bars:int; reason:str; dow:int; hour:int
    atr_rank:float; vol_ratio:float; btc_bull:bool; btc_not_bear:bool

def summarize(trades,eq):
    if not trades:
        return {"trades":0,"roi_pct":0.0,"win_rate_pct":0.0,"profit_factor":0.0,
                "expectancy":0.0,"max_drawdown_pct":0.0,"avg_hold_bars":0.0,"trade_list":[]}
    pnl=np.array([t.pnl for t in trades],float)
    wins=pnl[pnl>0]; losses=pnl[pnl<0]
    if len(losses) and abs(losses.sum())>0:
        pf=float(wins.sum()/abs(losses.sum()))
    elif len(wins):
        pf=float("inf")
    else:
        pf=0.0
    curve=np.r_[START_EQUITY,START_EQUITY+np.cumsum(pnl)]
    peak=np.maximum.accumulate(curve)
    dd=float(((peak-curve)/peak*100).max())
    return {
        "trades":len(trades),
        "roi_pct":(eq/START_EQUITY-1)*100,
        "win_rate_pct":float((pnl>0).mean()*100),
        "profit_factor":pf,
        "expectancy":float(pnl.mean()),
        "max_drawdown_pct":dd,
        "avg_hold_bars":float(np.mean([t.bars for t in trades])),
        "trade_list":[asdict(t) for t in trades],
    }

def backtest(x,p,fee_bps,slip_bps,start=None,end=None):
    y=x
    if start is not None: y=y[y.index>=start]
    if end is not None: y=y[y.index<end]
    if len(y)<250: return summarize([],START_EQUITY)
    sig=make_signal(y,p)
    eq=START_EQUITY; trades=[]
    roundtrip_cost=(fee_bps+slip_bps)*2/10000
    i=210
    while i<len(y)-2:
        if not bool(sig.iloc[i]):
            i+=1; continue
        j=i+1
        entry=float(y.open.iloc[j]); a=float(y.atr.iloc[i])
        if not np.isfinite(entry) or not np.isfinite(a) or a<=0:
            i+=1; continue
        stop=entry-float(p["stop_atr"])*a
        risk=entry-stop
        target=entry+float(p["target_r"])*risk
        prev_eq=eq
        qty=(eq*0.005)/risk
        exit_idx=min(j+int(p["max_hold"]),len(y)-1)
        exit_px=None; reason="time"
        for k in range(j,exit_idx+1):
            lo=float(y.low.iloc[k]); hi=float(y.high.iloc[k])
            if lo<=stop:
                exit_px=stop; exit_idx=k; reason="stop"; break
            if hi>=target:
                exit_px=target; exit_idx=k; reason="target"; break
        if exit_px is None:
            exit_px=float(y.close.iloc[exit_idx])
        pnl=qty*(exit_px-entry)-qty*entry*roundtrip_cost
        eq+=pnl
        trades.append(Trade(
            str(y.index[j]),str(y.index[exit_idx]),entry,exit_px,pnl,
            pnl/max(prev_eq,1)*100,exit_idx-j+1,reason,
            int(y.dow.iloc[i]),int(y.hour.iloc[i]),
            float(y.atr_rank.iloc[i]) if np.isfinite(y.atr_rank.iloc[i]) else np.nan,
            float(y.vol_ratio.iloc[i]) if np.isfinite(y.vol_ratio.iloc[i]) else np.nan,
            bool(y.btc_bull.iloc[i]),bool(y.btc_not_bear.iloc[i])
        ))
        i=exit_idx+1
    return summarize(trades,eq)

def fixed_family():
    for vals in itertools.product(
        [25,30],[15,20],[0.50,0.65],[1.0,1.25],[0.0,0.15],
        ["not_bear","bull"],[False,True]
    ):
        lb,adx_min,atr_rank,vol_ratio,batr,btc_filter,weekend=vals
        yield {
            "lookback":lb,"adx_min":adx_min,"min_atr_rank":atr_rank,
            "min_vol_ratio":vol_ratio,"min_breakout_atr":batr,
            "btc_filter":btc_filter,"exclude_weekend":weekend,
            "stop_atr":1.6,"target_r":2.5,"max_hold":12,
        }

def quality_score(m):
    if m["trades"]<15: return -999.0
    pf=m["profit_factor"]
    if not np.isfinite(pf): pf=3.0
    return m["roi_pct"]+min(pf,3)*2-0.75*m["max_drawdown_pct"]

def select_dev(x,start,end,fee,slip):
    rows=[]
    for p in fixed_family():
        m=backtest(x,p,fee,slip,start=start,end=end)
        rows.append({"params":p,"trades":m["trades"],"roi_pct":m["roi_pct"],
                     "profit_factor":m["profit_factor"],"expectancy":m["expectancy"],
                     "max_drawdown_pct":m["max_drawdown_pct"],"score":quality_score(m)})
    rows.sort(key=lambda r:r["score"],reverse=True)
    return rows[0]["params"],rows

def nearby(base):
    variants=[]
    for lb in [25,30]:
      for adx_min in [15,20]:
       for atr_rank in [0.50,0.65]:
        for vol_ratio in [1.0,1.25]:
         p=dict(base)
         p["lookback"]=lb; p["adx_min"]=adx_min
         p["min_atr_rank"]=atr_rank; p["min_vol_ratio"]=vol_ratio
         variants.append(p)
    out=[]; seen=set()
    for p in variants:
        k=json.dumps(p,sort_keys=True)
        if k not in seen:
            seen.add(k); out.append(p)
    return out

def monte_carlo(trades,n=10000):
    pnl=np.array([t["pnl"] for t in trades],float)
    if len(pnl)<10:
        return {"iterations":n,"trades":len(pnl),"warning":"insufficient trades"}
    rng=np.random.default_rng(MC_SEED); rois=[]; dds=[]
    for _ in range(n):
        s=rng.choice(pnl,size=len(pnl),replace=True)
        curve=np.r_[START_EQUITY,START_EQUITY+np.cumsum(s)]
        peak=np.maximum.accumulate(curve)
        rois.append(float((curve[-1]/START_EQUITY-1)*100))
        dds.append(float(((peak-curve)/peak*100).max()))
    arr=np.array(rois)
    return {
        "iterations":n,"trades":len(pnl),
        "probability_profitable_pct":float((arr>0).mean()*100),
        "median_roi_pct":float(np.median(arr)),
        "roi_5th_pct":float(np.percentile(arr,5)),
        "roi_95th_pct":float(np.percentile(arr,95)),
        "median_dd_pct":float(np.median(dds)),
        "dd_95th_pct":float(np.percentile(dds,95)),
    }

def attribution(trades):
    if not trades: return {}
    df=pd.DataFrame(trades); out={}
    for col in ["dow","hour","btc_bull","btc_not_bear"]:
        out[col]=df.groupby(col).pnl.agg(["count","sum","mean"]).reset_index().to_dict(orient="records")
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--fee-bps",type=float,default=15.0)
    ap.add_argument("--slippage-bps",type=float,default=5.0)
    ap.add_argument("--mc",type=int,default=10000)
    ap.add_argument("--holdout-days",type=int,default=240)
    args=ap.parse_args()

    doge=resample4h(load("DOGE/USD"))
    btc=resample4h(load("BTC/USD"))
    x=features(doge,btc)

    full_end=x.index.max()
    holdout_start=full_end-pd.Timedelta(days=args.holdout_days)
    dev=x[x.index<holdout_start]
    hold=x[x.index>=holdout_start]

    selected,dev_grid=select_dev(
        x,dev.index.min(),holdout_start,args.fee_bps,args.slippage_bps
    )

    dev_result=backtest(
        x,selected,args.fee_bps,args.slippage_bps,
        start=dev.index.min(),end=holdout_start
    )

    hold_result=backtest(
        x,selected,args.fee_bps,args.slippage_bps,
        start=holdout_start,end=full_end+pd.Timedelta(seconds=1)
    )

    neighbor_rows=[]
    for p in nearby(selected):
        m=backtest(
            x,p,args.fee_bps,args.slippage_bps,
            start=holdout_start,end=full_end+pd.Timedelta(seconds=1)
        )
        neighbor_rows.append({
            "params":p,"trades":m["trades"],"roi_pct":m["roi_pct"],
            "profit_factor":m["profit_factor"],"expectancy":m["expectancy"],
            "max_drawdown_pct":m["max_drawdown_pct"],"profitable":m["roi_pct"]>0
        })

    profitable_neighbors=sum(r["profitable"] for r in neighbor_rows)
    neighbor_pct=profitable_neighbors/max(len(neighbor_rows),1)*100
    mc=monte_carlo(hold_result["trade_list"],args.mc)

    passed=(
        hold_result["trades"]>=10
        and hold_result["roi_pct"]>0
        and np.isfinite(hold_result["profit_factor"])
        and hold_result["profit_factor"]>=1.25
        and mc.get("probability_profitable_pct",0)>=65.0
        and neighbor_pct>=50.0
    )

    result={
        "methodology":{
            "timeframe":"4h fixed",
            "development_start":str(dev.index.min()),
            "development_end":str(dev.index.max()),
            "holdout_start":str(holdout_start),
            "holdout_end":str(full_end),
            "holdout_days":args.holdout_days,
            "holdout_used_for_selection":False,
            "fee_bps_per_side":args.fee_bps,
            "slippage_bps_per_side":args.slippage_bps,
        },
        "selected_parameters":selected,
        "development":{k:v for k,v in dev_result.items() if k!="trade_list"},
        "holdout":{k:v for k,v in hold_result.items() if k!="trade_list"},
        "holdout_monte_carlo":mc,
        "holdout_parameter_neighborhood":{
            "variants_tested":len(neighbor_rows),
            "profitable_variants":profitable_neighbors,
            "profitable_variant_pct":neighbor_pct,
        },
        "holdout_attribution":attribution(hold_result["trade_list"]),
        "research_gate":{
            "mc_threshold_pct":65.0,
            "minimum_holdout_trades":10,
            "minimum_holdout_pf":1.25,
            "minimum_neighbor_profitable_pct":50.0,
            "passed":passed,
        },
    }

    (REPORTS/"doge_breakout_holdout_v4_latest.json").write_text(
        json.dumps(result,indent=2),encoding="utf-8"
    )
    pd.DataFrame([
        {**r["params"],"trades":r["trades"],"roi_pct":r["roi_pct"],
         "profit_factor":r["profit_factor"],"expectancy":r["expectancy"],
         "max_drawdown_pct":r["max_drawdown_pct"],"profitable":r["profitable"]}
        for r in neighbor_rows
    ]).to_csv(REPORTS/"doge_breakout_holdout_v4_neighbors.csv",index=False)
    pd.DataFrame(hold_result["trade_list"]).to_csv(
        REPORTS/"doge_breakout_holdout_v4_holdout_trades.csv",index=False
    )

    print("\n===== DOGE BREAKOUT HOLDOUT V4 =====")
    print(json.dumps({
        "selected_parameters":selected,
        "development_trades":dev_result["trades"],
        "development_roi_pct":dev_result["roi_pct"],
        "development_pf":dev_result["profit_factor"],
        "development_dd_pct":dev_result["max_drawdown_pct"],
        "holdout_trades":hold_result["trades"],
        "holdout_roi_pct":hold_result["roi_pct"],
        "holdout_pf":hold_result["profit_factor"],
        "holdout_dd_pct":hold_result["max_drawdown_pct"],
        "holdout_expectancy":hold_result["expectancy"],
        "mc_probability_profitable_pct":mc.get("probability_profitable_pct"),
        "mc_dd_95th_pct":mc.get("dd_95th_pct"),
        "neighbor_profitable_pct":neighbor_pct,
        "passed":passed,
    },indent=2))
    print("\nReports:")
    print(REPORTS/"doge_breakout_holdout_v4_latest.json")
    print(REPORTS/"doge_breakout_holdout_v4_neighbors.csv")
    print(REPORTS/"doge_breakout_holdout_v4_holdout_trades.csv")

if __name__=="__main__":
    main()
