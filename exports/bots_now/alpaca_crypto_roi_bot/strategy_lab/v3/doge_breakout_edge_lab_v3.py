#!/usr/bin/env python3
import argparse, json
from dataclasses import dataclass, asdict
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "crypto_history.duckdb"
REPORTS = ROOT / "reports" / "v3"
REPORTS.mkdir(parents=True, exist_ok=True)
START_EQUITY = 10000.0

def ema(s,n): return s.ewm(span=n, adjust=False).mean()

def atr(df,n=14):
    pc=df.close.shift(1)
    tr=pd.concat([(df.high-df.low),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()

def adx(df,n=14):
    up=df.high.diff()
    dn=-df.low.diff()
    plus=pd.Series(np.where((up>dn)&(up>0),up,0.0), index=df.index)
    minus=pd.Series(np.where((dn>up)&(dn>0),dn,0.0), index=df.index)
    a=atr(df,n)
    pdi=100*plus.ewm(alpha=1/n,adjust=False).mean()/a
    mdi=100*minus.ewm(alpha=1/n,adjust=False).mean()/a
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return dx.ewm(alpha=1/n,adjust=False).mean()

def load(symbol):
    con=duckdb.connect(str(DB), read_only=True)
    df=con.execute("""
        SELECT timestamp, open, high, low, close, volume
        FROM bars WHERE symbol=? ORDER BY timestamp
    """,[symbol]).df()
    con.close()
    if df.empty:
        raise RuntimeError(f"No data for {symbol}")
    df["timestamp"]=pd.to_datetime(df["timestamp"], utc=True)
    return df.drop_duplicates("timestamp").set_index("timestamp").sort_index()

def resample(df,tf):
    rule={"2h":"2h","3h":"3h","4h":"4h","6h":"6h","8h":"8h"}[tf]
    return df.resample(rule).agg(
        open=("open","first"), high=("high","max"), low=("low","min"),
        close=("close","last"), volume=("volume","sum")
    ).dropna()

def features(doge, btc):
    x=doge.copy()
    b=btc.reindex(x.index).ffill()
    x["atr"]=atr(x,14)
    x["atr_pct"]=x["atr"]/x["close"]
    x["atr_rank"]=x["atr_pct"].rolling(100).rank(pct=True)
    x["adx"]=adx(x,14)
    x["ema50"]=ema(x.close,50)
    x["ema200"]=ema(x.close,200)
    x["vol20"]=x.volume.rolling(20).mean()
    x["vol_ratio"]=x.volume/x["vol20"].replace(0,np.nan)
    x["btc_ema50"]=ema(b.close,50)
    x["btc_ema200"]=ema(b.close,200)
    x["btc_bull"]=(b.close>x["btc_ema50"])&(x["btc_ema50"]>x["btc_ema200"])
    x["btc_bear"]=(b.close<x["btc_ema50"])&(x["btc_ema50"]<x["btc_ema200"])
    x["dow"]=x.index.dayofweek
    x["hour"]=x.index.hour
    return x

def signal(x,p):
    hh=x.high.shift(1).rolling(int(p["lookback"])).max()
    breakout_atr=(x.close-hh)/x["atr"].replace(0,np.nan)
    s=(x.close>hh)
    s &= (x.close>x.ema50)
    s &= (x.adx>=p["adx_min"])
    s &= (x.vol_ratio>=p["min_vol_ratio"])
    s &= (x.atr_rank>=p["min_atr_rank"])
    s &= (breakout_atr>=p["min_breakout_atr"])
    if p["btc_filter"]=="bull":
        s &= x.btc_bull
    elif p["btc_filter"]=="not_bear":
        s &= ~x.btc_bear
    if p["exclude_weekend"]:
        s &= (x.dow<5)
    return s.fillna(False)

@dataclass
class Trade:
    entry_time:str
    exit_time:str
    entry:float
    exit:float
    pnl:float
    return_pct:float
    bars:int
    reason:str
    dow:int
    hour:int
    atr_rank:float
    vol_ratio:float
    btc_bull:bool
    btc_bear:bool

def metrics(trades,equity):
    if not trades:
        return dict(trades=0,roi_pct=0.0,win_rate_pct=0.0,profit_factor=0.0,
                    expectancy=0.0,max_drawdown_pct=0.0,avg_hold_bars=0.0,trade_list=[])
    pnl=np.array([t.pnl for t in trades],float)
    wins=pnl[pnl>0]; losses=pnl[pnl<0]
    pf=float(wins.sum()/abs(losses.sum())) if len(losses) and abs(losses.sum()) else (999.0 if len(wins) else 0.0)
    curve=np.r_[START_EQUITY, START_EQUITY+np.cumsum(pnl)]
    peak=np.maximum.accumulate(curve)
    dd=((peak-curve)/peak*100).max()
    return dict(
        trades=len(trades),
        roi_pct=(equity/START_EQUITY-1)*100,
        win_rate_pct=float((pnl>0).mean()*100),
        profit_factor=pf,
        expectancy=float(pnl.mean()),
        max_drawdown_pct=float(dd),
        avg_hold_bars=float(np.mean([t.bars for t in trades])),
        trade_list=[asdict(t) for t in trades]
    )

def backtest(x,p,fee_bps,slip_bps,start=None,end=None):
    y=x
    if start is not None: y=y[y.index>=start]
    if end is not None: y=y[y.index<end]
    if len(y)<250: return metrics([],START_EQUITY)
    sig=signal(y,p)
    eq=START_EQUITY; trades=[]
    cost_rt=(fee_bps+slip_bps)*2/10000
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
        qty=(eq*0.005)/risk
        exit_idx=min(j+int(p["max_hold"]),len(y)-1)
        exit_px=None; reason="time"
        for k in range(j,exit_idx+1):
            lo=float(y.low.iloc[k]); hi=float(y.high.iloc[k])
            if lo<=stop:
                exit_px=stop; exit_idx=k; reason="stop"; break
            if hi>=target:
                exit_px=target; exit_idx=k; reason="target"; break
        if exit_px is None: exit_px=float(y.close.iloc[exit_idx])
        pnl=qty*(exit_px-entry)-qty*entry*cost_rt
        prev_eq=eq; eq+=pnl
        trades.append(Trade(
            str(y.index[j]),str(y.index[exit_idx]),entry,exit_px,pnl,
            pnl/max(prev_eq,1)*100,exit_idx-j+1,reason,
            int(y.dow.iloc[i]),int(y.hour.iloc[i]),
            float(y.atr_rank.iloc[i]) if np.isfinite(y.atr_rank.iloc[i]) else np.nan,
            float(y.vol_ratio.iloc[i]) if np.isfinite(y.vol_ratio.iloc[i]) else np.nan,
            bool(y.btc_bull.iloc[i]),bool(y.btc_bear.iloc[i])
        ))
        i=exit_idx+1
    return metrics(trades,eq)

def candidate_grid():
    for tf in ["2h","3h","4h","6h","8h"]:
      for lb in [15,20,25,30]:
       for adx_min in [15,20,25]:
        for vol in [1.0,1.25,1.5]:
         for atr_rank in [0.35,0.5,0.65]:
          for batr in [0.0,0.15,0.30]:
           for btc_filter in ["none","not_bear","bull"]:
            for weekend in [False,True]:
             yield tf,dict(
                lookback=lb,adx_min=adx_min,min_vol_ratio=vol,min_atr_rank=atr_rank,
                min_breakout_atr=batr,btc_filter=btc_filter,exclude_weekend=weekend,
                stop_atr=1.6,target_r=2.5,max_hold=12
             )

def score(m):
    if m["trades"]<12: return -999
    return m["roi_pct"] + min(m["profit_factor"],3)*2 - 0.75*m["max_drawdown_pct"]

def anchored_walkforward(raw_doge,raw_btc,fee,slip):
    start=raw_doge.index.min(); end=raw_doge.index.max()
    train_end=start+pd.Timedelta(days=365)
    test_len=pd.Timedelta(days=120)
    windows=[]
    cache={}
    for tf in ["2h","3h","4h","6h","8h"]:
        d=resample(raw_doge,tf); b=resample(raw_btc,tf); cache[tf]=features(d,b)
    while train_end+test_len<=end:
        test_end=train_end+test_len
        best=None; bestm=None; bests=-1e18
        for tf,p in candidate_grid():
            m=backtest(cache[tf],p,fee,slip,start=start,end=train_end)
            s=score(m)
            if s>bests:
                bests=s; best=(tf,p.copy()); bestm=m
        tf,p=best
        test=backtest(cache[tf],p,fee,slip,start=train_end,end=test_end)
        windows.append({
            "train_start":str(start),"train_end":str(train_end),
            "test_start":str(train_end),"test_end":str(test_end),
            "timeframe":tf,"params":p,
            "train":{k:v for k,v in bestm.items() if k!="trade_list"},
            "test":{k:v for k,v in test.items() if k!="trade_list"},
            "trades":test["trade_list"]
        })
        train_end=test_end
    return windows

def monte_carlo(trades,n=10000):
    pnl=np.array([t["pnl"] for t in trades],float)
    if len(pnl)<10:
        return {"trades":len(pnl),"iterations":n,"warning":"insufficient trades"}
    rng=np.random.default_rng(42); rois=[]; dds=[]
    for _ in range(n):
        s=rng.choice(pnl,size=len(pnl),replace=True)
        curve=np.r_[START_EQUITY,START_EQUITY+np.cumsum(s)]
        peak=np.maximum.accumulate(curve)
        dds.append(float(((peak-curve)/peak*100).max()))
        rois.append(float((curve[-1]/START_EQUITY-1)*100))
    return {
        "trades":len(pnl),"iterations":n,
        "probability_profitable_pct":float((np.array(rois)>0).mean()*100),
        "median_roi_pct":float(np.median(rois)),
        "roi_5th_pct":float(np.percentile(rois,5)),
        "roi_95th_pct":float(np.percentile(rois,95)),
        "median_dd_pct":float(np.median(dds)),
        "dd_95th_pct":float(np.percentile(dds,95)),
    }

def summarize(windows):
    rois=[w["test"]["roi_pct"] for w in windows]
    dds=[w["test"]["max_drawdown_pct"] for w in windows]
    pfs=[w["test"]["profit_factor"] for w in windows if w["test"]["trades"]>0]
    trades=[t for w in windows for t in w["trades"]]
    profitable=sum(r>0 for r in rois)
    return {
        "windows":len(windows),
        "profitable_windows":profitable,
        "profitable_window_pct":profitable/max(len(windows),1)*100,
        "avg_oos_roi_pct":float(np.mean(rois)) if rois else 0.0,
        "worst_oos_dd_pct":float(max(dds)) if dds else 0.0,
        "avg_oos_pf":float(np.mean(pfs)) if pfs else 0.0,
        "oos_trades":len(trades),
        "oos_trade_list":trades
    }

def attribution(trades):
    if not trades: return {}
    df=pd.DataFrame(trades); out={}
    for col in ["dow","hour","btc_bull","btc_bear"]:
        out[col]=df.groupby(col).pnl.agg(["count","sum","mean"]).reset_index().to_dict(orient="records")
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--fee-bps",type=float,default=15.0)
    ap.add_argument("--slippage-bps",type=float,default=5.0)
    ap.add_argument("--mc",type=int,default=10000)
    args=ap.parse_args()

    doge=load("DOGE/USD"); btc=load("BTC/USD")
    wf=anchored_walkforward(doge,btc,args.fee_bps,args.slippage_bps)
    s=summarize(wf)
    mc=monte_carlo(s["oos_trade_list"],args.mc)
    result={
        "summary":{k:v for k,v in s.items() if k!="oos_trade_list"},
        "monte_carlo":mc,
        "regime_attribution":attribution(s["oos_trade_list"]),
        "windows":wf,
        "research_gate":{
            "mc_threshold_pct":65.0,
            "passed_mc":mc.get("probability_profitable_pct",0)>=65.0,
            "note":"Research-only. No order execution."
        }
    }
    (REPORTS/"doge_breakout_edge_lab_v3_latest.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    rows=[]
    for i,w in enumerate(wf,1):
        rows.append({
            "window":i,"timeframe":w["timeframe"],**w["params"],
            "test_trades":w["test"]["trades"],"test_roi_pct":w["test"]["roi_pct"],
            "test_pf":w["test"]["profit_factor"],"test_dd_pct":w["test"]["max_drawdown_pct"],
            "test_expectancy":w["test"]["expectancy"]
        })
    pd.DataFrame(rows).to_csv(REPORTS/"doge_breakout_edge_lab_v3_windows.csv",index=False)
    pd.DataFrame(s["oos_trade_list"]).to_csv(REPORTS/"doge_breakout_edge_lab_v3_trades.csv",index=False)

    print("\n===== DOGE BREAKOUT EDGE LAB V3 =====")
    print(json.dumps({
        "windows":s["windows"],
        "profitable_windows":s["profitable_windows"],
        "profitable_window_pct":s["profitable_window_pct"],
        "avg_oos_roi_pct":s["avg_oos_roi_pct"],
        "worst_oos_dd_pct":s["worst_oos_dd_pct"],
        "avg_oos_pf":s["avg_oos_pf"],
        "oos_trades":s["oos_trades"],
        "mc_probability_profitable_pct":mc.get("probability_profitable_pct"),
        "mc_dd_95th_pct":mc.get("dd_95th_pct"),
        "passed_mc_65":result["research_gate"]["passed_mc"]
    },indent=2))
    print("\nSelected timeframe/params by OOS window:")
    print(pd.DataFrame(rows).to_string(index=False))
    print("\nReports:")
    print(REPORTS/"doge_breakout_edge_lab_v3_latest.json")
    print(REPORTS/"doge_breakout_edge_lab_v3_windows.csv")
    print(REPORTS/"doge_breakout_edge_lab_v3_trades.csv")

if __name__=="__main__":
    main()
