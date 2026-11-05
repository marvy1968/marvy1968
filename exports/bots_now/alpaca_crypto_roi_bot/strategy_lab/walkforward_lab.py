#!/usr/bin/env python3
import json, numpy as np, pandas as pd
from pathlib import Path
import strategy_lab as sl
HERE=Path(__file__).resolve().parent
def main():
    c=sl.cfg(); rc=c['research']; rp=HERE/'reports'/'strategy_rankings_latest.csv'
    if not rp.exists(): raise SystemExit('Run strategy_lab.py first.')
    ranked=pd.read_csv(rp); cand=ranked[ranked.eligible==True].head(20)
    if cand.empty: print('No combinations met minimum trade threshold.'); return
    raw={s:sl.load_bars(s) for s in set(cand.symbol.tolist()+[c['benchmark']])}; rows=[]
    for _,q in cand.iterrows():
        sym,tf,st=q.symbol,q.timeframe,q.strategy; d=sl.resample(raw[sym],tf); b=sl.resample(raw[c['benchmark']],tf); x=sl.ind(d,b if sym!=c['benchmark'] else None).dropna(subset=['ema200'])
        if len(x)<1000: continue
        start=int(len(x)*.60); step=max(1,(len(x)-start)//4)
        for w in range(4):
            a=start+w*step; z=len(x) if w==3 else min(len(x),a+step); test=x.iloc[a:z]; r=sl.bt(test,sl.sig(st,test).fillna(False),rc); rows.append({'symbol':sym,'timeframe':tf,'strategy':st,'window':w+1,'test_start':str(test.index.min()),'test_end':str(test.index.max()),**{k:v for k,v in r.items() if k!='trades_detail'}})
    df=pd.DataFrame(rows); rep=HERE/'reports'; df.to_csv(rep/'walkforward_strategy_latest.csv',index=False)
    if df.empty: print('No walk-forward rows produced.'); return
    s=df.groupby(['symbol','timeframe','strategy']).agg(windows=('window','count'),profitable_windows=('roi_pct',lambda x:int((x>0).sum())),total_trades=('trades','sum'),avg_roi_pct=('roi_pct','mean'),worst_dd_pct=('max_drawdown_pct','max'),avg_pf=('profit_factor','mean'),avg_expectancy=('expectancy','mean')).reset_index(); s['profitable_window_pct']=s.profitable_windows/s.windows*100; s['passed']=(s.total_trades>=10)&(s.profitable_window_pct>=50)&(s.avg_roi_pct>0)&(s.avg_expectancy>0); s=s.sort_values(['passed','avg_roi_pct'],ascending=[False,False]); s.to_csv(rep/'walkforward_summary_latest.csv',index=False); (rep/'walkforward_summary_latest.json').write_text(json.dumps(s.replace({np.nan:None}).to_dict('records'),indent=2,default=str)); print(s.to_string(index=False))
if __name__=='__main__': main()
