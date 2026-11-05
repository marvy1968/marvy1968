#!/usr/bin/env python3
import argparse, os, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests, duckdb, pandas as pd, yaml
from dotenv import load_dotenv
HERE=Path(__file__).resolve().parent; BOT=HERE.parent; load_dotenv(BOT/'.env')
def cfg():
    return yaml.safe_load((HERE/'lab_config.yaml').read_text())
def iso(x): return x.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
def fetch(sym,start,end,dc):
    url=f"https://data.alpaca.markets/v1beta3/crypto/{dc['location']}/bars"
    h={}; k=os.getenv('APCA_API_KEY_ID'); s=os.getenv('APCA_API_SECRET_KEY')
    if k and s: h={'APCA-API-KEY-ID':k,'APCA-API-SECRET-KEY':s}
    p={'symbols':sym,'timeframe':dc['base_timeframe'],'start':iso(start),'end':iso(end),'limit':int(dc['page_limit']),'sort':'asc'}
    rows=[]; token=None
    while True:
        if token: p['page_token']=token
        elif 'page_token' in p: p.pop('page_token')
        r=requests.get(url,headers=h,params=p,timeout=60); r.raise_for_status(); j=r.json()
        for b in j.get('bars',{}).get(sym,[]):
            rows.append({'timestamp':b.get('t'),'symbol':sym,'open':b.get('o'),'high':b.get('h'),'low':b.get('l'),'close':b.get('c'),'volume':b.get('v'),'trade_count':b.get('n'),'vwap':b.get('vw')})
        token=j.get('next_page_token')
        if not token: break
        time.sleep(float(dc['sleep_seconds']))
    return rows
def main():
    a=argparse.ArgumentParser(); a.add_argument('--years',type=float); a.add_argument('--start'); a.add_argument('--end'); a.add_argument('--symbols',nargs='*'); z=a.parse_args()
    c=cfg(); dc=c['download']; syms=z.symbols or c['symbols']; end=datetime.fromisoformat(z.end).replace(tzinfo=timezone.utc) if z.end else datetime.now(timezone.utc)
    start=datetime.fromisoformat(z.start).replace(tzinfo=timezone.utc) if z.start else end-timedelta(days=int(365.25*(z.years or dc['years'])))
    db=HERE/'data'/'crypto_history.duckdb'; con=duckdb.connect(str(db))
    con.execute('CREATE TABLE IF NOT EXISTS bars(timestamp TIMESTAMPTZ,symbol VARCHAR,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE,trade_count BIGINT,vwap DOUBLE)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_bars_symbol_time ON bars(symbol,timestamp)')
    print(f'Database: {db}\nWindow: {start.isoformat()} -> {end.isoformat()}\nTimeframe: {dc["base_timeframe"]}')
    for sym in syms:
        print(f'\nDownloading {sym} ...',flush=True); rows=fetch(sym,start,end,dc)
        if not rows: print('  no bars returned'); continue
        df=pd.DataFrame(rows); df['timestamp']=pd.to_datetime(df['timestamp'],utc=True)
        con.execute('DELETE FROM bars WHERE symbol=? AND timestamp>=? AND timestamp<=?',[sym,start,end]); con.register('incoming',df)
        con.execute('INSERT INTO bars SELECT timestamp,symbol,open,high,low,close,volume,trade_count,vwap FROM incoming'); con.unregister('incoming')
        n,first,last=con.execute('SELECT COUNT(*),MIN(timestamp),MAX(timestamp) FROM bars WHERE symbol=?',[sym]).fetchone(); print(f'  downloaded={len(df):,} stored={n:,} range={first} -> {last}')
    print('\nDatabase summary:'); print(con.execute('SELECT symbol,COUNT(*) bars,MIN(timestamp) first_bar,MAX(timestamp) last_bar FROM bars GROUP BY symbol ORDER BY symbol').df().to_string(index=False)); con.close()
if __name__=='__main__': main()
