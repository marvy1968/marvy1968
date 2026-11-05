#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/"data"/"stock_history.duckdb"
REPORTS=ROOT/"reports"/"v3"
REPORTS.mkdir(parents=True,exist_ok=True)

INITIAL_EQUITY=100000.0
HOLDING_BARS=24
RISK_PER_TRADE=0.005
MAX_POSITION_PCT=0.10
MAX_CONCURRENT=5
ROUND_TRIP_COST_BPS=5.0

MIN_OOS_TRADES=100
MIN_PF=1.20
MIN_EXPECTANCY_BPS=3.0
MAX_DD_PCT=10.0
MIN_PROFITABLE_WINDOWS_PCT=60.0
MIN_MC_PROB=65.0
MIN_NEIGHBOR_SURVIVAL_PCT=50.0

PARAM_GRID=[]
for rs in [0.05,0.08,0.10,0.12]:
    for vol in [1.0,1.2,1.4,1.6]:
        for atr in [0.03,0.04,0.05]:
            for ext in [0.04,0.06,0.08]:
                for spy in ["above_200","above_50_200"]:
                    PARAM_GRID.append((rs,vol,atr,ext,spy))

SECTOR_MAP={
"AAPL":"Technology","MSFT":"Technology","NVDA":"Technology","AVGO":"Technology","ORCL":"Technology","CRM":"Technology","CSCO":"Technology","IBM":"Technology",
"AMZN":"Consumer Discretionary","HD":"Consumer Discretionary","MCD":"Consumer Discretionary","NKE":"Consumer Discretionary",
"COST":"Consumer Staples","WMT":"Consumer Staples",
"JPM":"Financials","BAC":"Financials","GS":"Financials","V":"Financials",
"XOM":"Energy","CVX":"Energy","COP":"Energy","SLB":"Energy","EOG":"Energy",
"LLY":"Health Care","UNH":"Health Care","JNJ":"Health Care","ABBV":"Health Care","MRK":"Health Care",
"CAT":"Industrials","GE":"Industrials","RTX":"Industrials","HON":"Industrials","UNP":"Industrials",
"PLD":"Real Estate","AMT":"Real Estate","EQIX":"Real Estate","SPG":"Real Estate","O":"Real Estate",
"NEE":"Utilities","SO":"Utilities","DUK":"Utilities","AEP":"Utilities","EXC":"Utilities",
"LIN":"Materials","SHW":"Materials","FCX":"Materials","NEM":"Materials","APD":"Materials",
"META":"Communication Services","GOOGL":"Communication Services","T":"Communication Services","VZ":"Communication Services","TMUS":"Communication Services","CMCSA":"Communication Services","CHTR":"Communication Services",
}

def safe_pf(pnl):
    pnl=np.asarray(pnl,float)
    gains=pnl[pnl>0].sum(); losses=-pnl[pnl<0].sum()
    if losses<=0: return 999.0 if gains>0 else 0.0
    return float(gains/losses)

def max_dd(equity):
    e=np.asarray(equity,float)
    if not len(e): return 0.0
    peak=np.maximum.accumulate(e)
    return float((((peak-e)/np.maximum(peak,1e-12)).max())*100)

def load_data():
    if not DB.exists(): raise SystemExit(f"Missing database: {DB}")
    con=duckdb.connect(str(DB),read_only=True)
    df=con.execute("SELECT symbol,timestamp,open,high,low,close,volume FROM bars ORDER BY symbol,timestamp").df()
    con.close()
    df["timestamp"]=pd.to_datetime(df["timestamp"],utc=True)
    df["symbol"]=df["symbol"].astype(str).str.upper().str.strip()
    return df

def prep_symbol(g):
    g=g.copy().sort_values("timestamp")
    c=g.close.astype(float); h=g.high.astype(float); l=g.low.astype(float); v=g.volume.astype(float)
    g["mom24"]=c.pct_change(24); g["mom48"]=c.pct_change(48)
    g["ma20"]=c.rolling(20).mean(); g["ma50"]=c.rolling(50).mean(); g["ma200"]=c.rolling(200).mean()
    g["vol20"]=v.rolling(20).mean(); g["vol_ratio"]=v/g.vol20.replace(0,np.nan)
    prev=c.shift(1)
    tr=pd.concat([(h-l),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    g["atr14"]=tr.rolling(14).mean(); g["atr_pct"]=g.atr14/c.replace(0,np.nan)
    g["extension20"]=c/g.ma20.replace(0,np.nan)-1
    g["trend_quality"]=g.ma20/g.ma50.replace(0,np.nan)-1
    g["future_close"]=c.shift(-HOLDING_BARS)
    g["raw_forward_return"]=g.future_close/c.replace(0,np.nan)-1
    return g

def prepare(df):
    data=pd.concat([prep_symbol(g) for _,g in df.groupby("symbol",sort=False)],ignore_index=True)
    spy=data[data.symbol=="SPY"][["timestamp","close","ma50","ma200","mom24"]].copy()
    if spy.empty: raise SystemExit("SPY missing from database.")
    spy=spy.rename(columns={"close":"spy_close","ma50":"spy_ma50","ma200":"spy_ma200","mom24":"spy_mom24"})
    data=data.merge(spy,on="timestamp",how="left")
    data["relative_strength"]=data.mom24-data.spy_mom24
    data["sector"]=data.symbol.map(SECTOR_MAP).fillna("Other")
    return data

def regime_mask(x,regime):
    if regime=="above_50_200":
        return (x.spy_close>x.spy_ma200)&(x.spy_ma50>x.spy_ma200)
    return x.spy_close>x.spy_ma200

def candidates(df,p):
    rs,vol,atr,ext,spy=p
    x=df.copy()
    mask=((x.symbol!="SPY")&(x.close>x.ma20)&(x.ma20>x.ma50)&(x.ma50>x.ma200)&
          (x.mom24>0)&(x.mom48>0)&(x.trend_quality>0)&(x.relative_strength>=rs)&
          (x.vol_ratio>=vol)&(x.atr_pct>0)&(x.atr_pct<=atr)&
          (x.extension20>=0)&(x.extension20<=ext)&x.raw_forward_return.notna())
    mask &= regime_mask(x,spy)
    x=x.loc[mask].copy()
    if x.empty: return x
    x["quality_score"]=(x.relative_strength*4+x.mom24*2+x.trend_quality*2+
                        np.log1p(x.vol_ratio.clip(lower=0))*0.25-x.extension20*1.5)
    return x.sort_values(["timestamp","quality_score"],ascending=[True,False])

def portfolio_sim(c):
    if c.empty: return pd.DataFrame()
    open_positions=[]; trades=[]; equity=INITIAL_EQUITY
    for ts,batch in c.groupby("timestamp",sort=True):
        open_positions=[p for p in open_positions if p["exit_time"]>ts]
        avail=MAX_CONCURRENT-len(open_positions)
        if avail<=0: continue
        used={p["symbol"] for p in open_positions}
        for _,row in batch.head(avail*2).iterrows():
            if row.symbol in used: continue
            atr_pct=float(row.atr_pct)
            if not np.isfinite(atr_pct) or atr_pct<=0: continue
            stop_dist=max(atr_pct*1.5,0.01)
            risk_dollars=equity*RISK_PER_TRADE
            position=min(risk_dollars/stop_dist,equity*MAX_POSITION_PCT)
            if position<=0: continue
            raw_ret=float(row.raw_forward_return)
            net_ret=raw_ret-ROUND_TRIP_COST_BPS/10000.0
            pnl=position*net_ret
            equity+=pnl
            exit_time=ts+pd.Timedelta(hours=HOLDING_BARS)
            trades.append({"entry_time":ts,"exit_time":exit_time,"symbol":row.symbol,"sector":row.sector,
                           "position_dollars":position,"raw_return":raw_ret,"net_return":net_ret,
                           "pnl":pnl,"equity":equity,"quality_score":row.quality_score,
                           "relative_strength":row.relative_strength,"vol_ratio":row.vol_ratio,
                           "atr_pct":row.atr_pct,"extension20":row.extension20})
            open_positions.append({"symbol":row.symbol,"exit_time":exit_time}); used.add(row.symbol)
            if len(open_positions)>=MAX_CONCURRENT: break
    return pd.DataFrame(trades)

def summarize(t):
    if t.empty:
        return dict(trades=0,roi_pct=0.0,win_rate_pct=0.0,profit_factor=0.0,expectancy_bps=0.0,max_drawdown_pct=0.0)
    pnl=t.pnl.to_numpy(float)
    returns=(t.pnl/t.position_dollars.replace(0,np.nan)).dropna()
    eq=np.r_[INITIAL_EQUITY,t.equity.to_numpy(float)]
    return dict(trades=int(len(t)),roi_pct=float((t.equity.iloc[-1]/INITIAL_EQUITY-1)*100),
                win_rate_pct=float((pnl>0).mean()*100),profit_factor=safe_pf(pnl),
                expectancy_bps=float(returns.mean()*10000),max_drawdown_pct=max_dd(eq))

def score_stats(s):
    if s["trades"]<40: return -999.0
    return s["expectancy_bps"]+20*math.log(max(s["profit_factor"],0.01))-0.75*s["max_drawdown_pct"]

def pdict(p):
    return {"relative_strength_min":p[0],"volume_ratio_min":p[1],"max_atr_pct":p[2],"max_extension20":p[3],"spy_regime":p[4]}

def monte_carlo(t,n):
    if len(t)<MIN_OOS_TRADES:
        return {"iterations":n,"trades":int(len(t)),"warning":"insufficient trades"}
    r=(t.pnl/t.position_dollars.replace(0,np.nan)).dropna().to_numpy(float)
    rng=np.random.default_rng(42); finals=[]; dds=[]
    for _ in range(n):
        s=rng.choice(r,size=len(r),replace=True)
        eq=1.0; curve=[eq]
        for x in s:
            eq*=1.0+MAX_POSITION_PCT*x; curve.append(eq)
        finals.append((eq-1)*100); dds.append(max_dd(curve))
    a=np.asarray(finals)
    return {"iterations":n,"trades":int(len(r)),
            "probability_profitable_pct":float((a>0).mean()*100),
            "median_roi_pct":float(np.median(a)),
            "roi_5th_pct":float(np.percentile(a,5)),
            "roi_95th_pct":float(np.percentile(a,95)),
            "dd_95th_pct":float(np.percentile(dds,95))}

def make_windows(start,end,train_days,test_days):
    out=[]; train_start=start; train_end=train_start+pd.Timedelta(days=train_days)
    while train_end<end:
        test_start=train_end
        test_end=min(test_start+pd.Timedelta(days=test_days),end+pd.Timedelta(seconds=1))
        out.append((train_start,train_end,test_start,test_end))
        train_start+=pd.Timedelta(days=test_days)
        train_end=train_start+pd.Timedelta(days=train_days)
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mc",type=int,default=10000)
    ap.add_argument("--train-days",type=int,default=730)
    ap.add_argument("--test-days",type=int,default=180)
    args=ap.parse_args()

    print("===== STOCK WALK-FORWARD LAB V3 =====")
    print("Research only. Execution enabled: NO")
    raw=load_data()
    print(f"Rows loaded: {len(raw):,}")
    print(f"Symbols: {raw.symbol.nunique()}")
    data=prepare(raw)
    windows=make_windows(data.timestamp.min(),data.timestamp.max(),args.train_days,args.test_days)
    print(f"Walk-forward windows: {len(windows)}")

    all_oos=[]; window_rows=[]; selected=[]
    for wi,(tr0,tr1,te0,te1) in enumerate(windows,1):
        train=data[(data.timestamp>=tr0)&(data.timestamp<tr1)].copy()
        test=data[(data.timestamp>=te0)&(data.timestamp<te1)].copy()
        ranked=[]
        for p in PARAM_GRID:
            tt=portfolio_sim(candidates(train,p)); ss=summarize(tt)
            ranked.append((score_stats(ss),p,ss))
        ranked.sort(key=lambda x:x[0],reverse=True)
        _,champ,trstats=ranked[0]
        oot=portfolio_sim(candidates(test,champ)); os=summarize(oot)
        if not oot.empty:
            oot=oot.copy(); oot["window"]=wi; all_oos.append(oot)
        selected.append(pdict(champ))
        window_rows.append({"window":wi,"train_start":str(tr0),"train_end":str(tr1),
                            "test_start":str(te0),"test_end":str(te1),**pdict(champ),
                            "train_trades":trstats["trades"],"train_pf":trstats["profit_factor"],
                            "train_expectancy_bps":trstats["expectancy_bps"],"train_dd_pct":trstats["max_drawdown_pct"],
                            "test_trades":os["trades"],"test_roi_pct":os["roi_pct"],"test_pf":os["profit_factor"],
                            "test_expectancy_bps":os["expectancy_bps"],"test_dd_pct":os["max_drawdown_pct"],
                            "profitable":os["roi_pct"]>0})
        print(f"Window {wi}: {pdict(champ)} | OOS trades={os['trades']} ROI={os['roi_pct']:.2f}% PF={os['profit_factor']:.2f} EXP={os['expectancy_bps']:.2f}bps DD={os['max_drawdown_pct']:.2f}%")

    oos=pd.concat(all_oos,ignore_index=True) if all_oos else pd.DataFrame()
    overall=summarize(oos)
    profitable=sum(r["profitable"] for r in window_rows)
    profitable_pct=100*profitable/max(len(window_rows),1)

    sel_df=pd.DataFrame(selected)
    neighbor_rows=[]; neighbor_survival=0.0; dominant=None
    if not sel_df.empty:
        dominant=sel_df.value_counts().index[0]
        dtuple=tuple(dominant)
        dom=(float(dtuple[0]),float(dtuple[1]),float(dtuple[2]),float(dtuple[3]),str(dtuple[4]))
        oos_start=windows[0][2]
        for p in PARAM_GRID:
            if abs(p[0]-dom[0])<=0.03 and abs(p[1]-dom[1])<=0.2 and abs(p[2]-dom[2])<=0.01 and abs(p[3]-dom[3])<=0.02 and p[4]==dom[4]:
                tt=portfolio_sim(candidates(data[data.timestamp>=oos_start],p)); ss=summarize(tt)
                neighbor_rows.append({**pdict(p),**ss,"survived":ss["roi_pct"]>0 and ss["profit_factor"]>=1.0})
        neighbor_survival=100*sum(r["survived"] for r in neighbor_rows)/max(len(neighbor_rows),1)

    mc=monte_carlo(oos,args.mc) if not oos.empty else {"warning":"no oos trades"}
    by_symbol={}; by_sector={}
    if not oos.empty:
        for sym,g in oos.groupby("symbol"): by_symbol[sym]=summarize(g)
        for sec,g in oos.groupby("sector"): by_sector[sec]=summarize(g)

    mc_prob=mc.get("probability_profitable_pct",0.0) or 0.0
    passed=(overall["trades"]>=MIN_OOS_TRADES and overall["profit_factor"]>=MIN_PF and
            overall["expectancy_bps"]>=MIN_EXPECTANCY_BPS and overall["max_drawdown_pct"]<=MAX_DD_PCT and
            profitable_pct>=MIN_PROFITABLE_WINDOWS_PCT and mc_prob>=MIN_MC_PROB and
            neighbor_survival>=MIN_NEIGHBOR_SURVIVAL_PCT)

    verdict="STOCK_EDGE_VALIDATED" if passed else "STOCK_EDGE_NOT_VALIDATED"
    report={"methodology":{"version":"v3","strategy":"momentum walk-forward","train_days":args.train_days,
                           "test_days":args.test_days,"holding_bars":HOLDING_BARS,
                           "parameter_selection":"inside each training window only","execution_enabled":False,
                           "round_trip_cost_bps":ROUND_TRIP_COST_BPS},
            "summary":{**overall,"windows":len(window_rows),"profitable_windows":profitable,
                       "profitable_window_pct":profitable_pct,"neighbor_survival_pct":neighbor_survival},
            "monte_carlo":mc,"window_results":window_rows,
            "dominant_parameter_family":None if dominant is None else list(dominant),
            "neighbor_results":neighbor_rows,"by_symbol":by_symbol,"by_sector":by_sector,
            "research_gate":{"minimum_oos_trades":MIN_OOS_TRADES,"minimum_pf":MIN_PF,
                             "minimum_expectancy_bps":MIN_EXPECTANCY_BPS,"maximum_dd_pct":MAX_DD_PCT,
                             "minimum_profitable_windows_pct":MIN_PROFITABLE_WINDOWS_PCT,
                             "minimum_mc_probability_pct":MIN_MC_PROB,
                             "minimum_neighbor_survival_pct":MIN_NEIGHBOR_SURVIVAL_PCT,"passed":passed},
            "verdict":verdict}

    (REPORTS/"stock_walkforward_v3_latest.json").write_text(json.dumps(report,indent=2,default=str))
    pd.DataFrame(window_rows).to_csv(REPORTS/"stock_walkforward_v3_windows.csv",index=False)
    if not oos.empty: oos.to_csv(REPORTS/"stock_walkforward_v3_oos_trades.csv",index=False)
    pd.DataFrame(neighbor_rows).to_csv(REPORTS/"stock_walkforward_v3_neighbors.csv",index=False)

    print("\n========== V3 FINAL RESULT ==========")
    print(json.dumps({"oos_trades":overall["trades"],"oos_roi_pct":overall["roi_pct"],
                      "oos_pf":overall["profit_factor"],"oos_expectancy_bps":overall["expectancy_bps"],
                      "oos_max_dd_pct":overall["max_drawdown_pct"],"profitable_window_pct":profitable_pct,
                      "neighbor_survival_pct":neighbor_survival,
                      "mc_probability_profitable_pct":mc.get("probability_profitable_pct"),
                      "mc_dd_95th_pct":mc.get("dd_95th_pct"),"passed":passed},indent=2))
    print("\nFINAL VERDICT:",verdict)

if __name__=="__main__":
    main()
