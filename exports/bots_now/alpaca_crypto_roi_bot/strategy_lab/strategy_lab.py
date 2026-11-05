#!/usr/bin/env python3
import argparse, json
from pathlib import Path
import duckdb, numpy as np, pandas as pd, yaml
HERE=Path(__file__).resolve().parent
def cfg(): return yaml.safe_load((HERE/'lab_config.yaml').read_text())
def load_bars(sym):
    con=duckdb.connect(str(HERE/'data'/'crypto_history.duckdb'),read_only=True); d=con.execute('SELECT timestamp,open,high,low,close,volume FROM bars WHERE symbol=? ORDER BY timestamp',[sym]).df(); con.close()
    if d.empty:return d
    d['timestamp']=pd.to_datetime(d['timestamp'],utc=True); return d.set_index('timestamp')
def resample(d,tf):
    if tf=='15min': return d.copy()
    r={'1h':'1h','4h':'4h'}[tf]; return d.resample(r).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum')).dropna()
def rsi(c,n=14):
    q=c.diff(); g=q.clip(lower=0).rolling(n).mean(); l=(-q.clip(upper=0)).rolling(n).mean(); rs=g/l.replace(0,np.nan); return 100-(100/(1+rs))
def ind(d,b=None):
    x=d.copy(); c=x.close; x['ema20']=c.ewm(span=20,adjust=False).mean(); x['ema50']=c.ewm(span=50,adjust=False).mean(); x['ema200']=c.ewm(span=200,adjust=False).mean(); x['rsi']=rsi(c); x['mom20']=c.pct_change(20); x['high20']=x.high.rolling(20).max().shift(1); x['low20']=x.low.rolling(20).min().shift(1); x['vol20']=x.volume.rolling(20).mean(); tr=pd.concat([(x.high-x.low),(x.high-c.shift()).abs(),(x.low-c.shift()).abs()],axis=1).max(axis=1); x['atr']=tr.rolling(14).mean(); x['atrp']=x.atr/c; x['atr_rank']=x.atrp.rolling(100).rank(pct=True); m=c.rolling(20).mean(); sd=c.rolling(20).std(); x['z20']=(c-m)/sd.replace(0,np.nan)
    if b is not None and not b.empty:
        bc=b.close.reindex(x.index).ffill(); x['rs20']=c.pct_change(20)-bc.pct_change(20); x['btc_up']=bc>bc.ewm(span=50,adjust=False).mean()
    else: x['rs20']=np.nan; x['btc_up']=True
    return x
def sig(name,x):
    p=x.shift(1)
    if name=='trend': return (x.close>x.ema20)&(x.ema20>x.ema50)&(x.ema50>x.ema200)&x.rsi.between(50,72)
    if name=='momentum': return (x.mom20>0.04)&(x.close>x.ema50)&(x.volume>x.vol20)
    if name=='breakout': return (x.close>x.high20)&(x.volume>1.25*x.vol20)&(x.atr_rank>0.55)
    if name=='pullback': return (x.ema20>x.ema50)&(x.ema50>x.ema200)&(x.close<=x.ema20*1.01)&x.rsi.between(38,55)&(x.close>p.close)
    if name=='mean_reversion': return (p.z20<-2.0)&(x.z20>p.z20)&(p.rsi<35)&(x.rsi>p.rsi)
    if name=='relative_strength': return (x.rs20>0.03)&x.btc_up&(x.close>x.ema50)&x.rsi.between(45,72)
    if name=='vol_compression': return (p.atr_rank<0.25)&(x.atr_rank>p.atr_rank)&(x.close>x.high20)&(x.volume>x.vol20)
    raise ValueError(name)
def bt(x,s,rc):
    cost=(float(rc['fee_bps_per_side'])+float(rc['slippage_bps_per_side']))/10000; alloc=float(rc['allocation_pct'])/100; maxh=int(rc['max_hold_bars']); stop=float(rc['stop_loss_pct'])/100; take=float(rc['take_profit_pct'])/100; eq=float(rc['starting_equity']); peak=eq; dd=0.; pos=None; trades=[]
    for i in range(1,len(x)):
        row=x.iloc[i]; ts=x.index[i]
        if pos is None:
            if bool(s.iloc[i]) and np.isfinite(row.close):
                dollars=eq*alloc; qty=dollars/(row.close*(1+cost)); pos={'entry':row.close,'qty':qty,'bars':0,'time':ts,'cost':dollars}
        else:
            pos['bars']+=1; ret=row.close/pos['entry']-1; reason=None
            if ret<=-stop: reason='stop'
            elif ret>=take: reason='target'
            elif pos['bars']>=maxh: reason='time'
            elif row.close<row.ema50 and pos['bars']>=4: reason='trend_fail'
            if reason:
                net=pos['qty']*row.close*(1-cost); pnl=net-pos['cost']; eq+=pnl; trades.append({'entry_time':str(pos['time']),'exit_time':str(ts),'pnl':pnl,'return_pct':pnl/pos['cost']*100,'hold_bars':pos['bars'],'exit_reason':reason}); pos=None
        peak=max(peak,eq); dd=max(dd,(peak-eq)/peak if peak else 0)
    p=np.array([t['pnl'] for t in trades],float); w=p[p>0]; l=p[p<0]; pf=float(w.sum()/abs(l.sum())) if len(l) and abs(l.sum())>0 else (None if not len(w) else None)
    return {'ending_equity':eq,'roi_pct':(eq/float(rc['starting_equity'])-1)*100,'max_drawdown_pct':dd*100,'trades':len(trades),'win_rate_pct':float((p>0).mean()*100) if len(p) else 0.0,'profit_factor':pf,'expectancy':float(p.mean()) if len(p) else 0.0,'trades_detail':trades}
def main():
    a=argparse.ArgumentParser(); a.add_argument('--symbols',nargs='*'); a.add_argument('--timeframes',nargs='*'); z=a.parse_args(); c=cfg(); rc=c['research']; syms=z.symbols or c['symbols']; tfs=z.timeframes or rc['timeframes']; raw={s:load_bars(s) for s in set(syms+[c['benchmark']])}
    if raw[c['benchmark']].empty: raise SystemExit('Benchmark data missing. Run download_history.py first.')
    rows=[]; trades=[]
    for tf in tfs:
        b=resample(raw[c['benchmark']],tf)
        for sym in syms:
            d=resample(raw[sym],tf)
            if len(d)<300: continue
            x=ind(d,b if sym!=c['benchmark'] else None).dropna(subset=['ema200'])
            for st in rc['strategies']:
                r=bt(x,sig(st,x).fillna(False),rc); rows.append({'symbol':sym,'timeframe':tf,'strategy':st,**{k:v for k,v in r.items() if k!='trades_detail'}}); trades += [{'symbol':sym,'timeframe':tf,'strategy':st,**t} for t in r['trades_detail']]
                print(f"{sym:9s} {tf:5s} {st:18s} trades={r['trades']:4d} ROI={r['roi_pct']:7.2f}% PF={r['profit_factor']} DD={r['max_drawdown_pct']:6.2f}%")
    out=pd.DataFrame(rows); mintr=int(rc['minimum_trades']); out['eligible']=out.trades>=mintr; out['score']=np.where(out.eligible,out.roi_pct-.75*out.max_drawdown_pct+np.minimum(out.profit_factor.fillna(0),3)*2,-999); out=out.sort_values(['eligible','score'],ascending=[False,False]); rep=HERE/'reports'; out.to_csv(rep/'strategy_rankings_latest.csv',index=False); pd.DataFrame(trades).to_csv(rep/'strategy_trades_latest.csv',index=False); (rep/'strategy_rankings_latest.json').write_text(json.dumps({'eligible_threshold_trades':mintr,'tested_combinations':len(out),'eligible_combinations':int(out.eligible.sum()),'top_ranked':out.head(30).replace({np.nan:None}).to_dict('records'),'note':'Research-only. Walk-forward validation required before promotion.'},indent=2,default=str)); print('\n===== TOP RANKED ====='); print(out[['symbol','timeframe','strategy','trades','roi_pct','max_drawdown_pct','win_rate_pct','profit_factor','expectancy','eligible','score']].head(25).to_string(index=False))
if __name__=='__main__': main()
