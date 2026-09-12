# -*- coding: utf-8 -*-
import gzip, json, time
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import FinanceDataReader as fdr
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'stocks_data.js'; TMP=ROOT/'stocks_data.tmp.js'
CACHE_DIR=ROOT/'.data_cache'; CACHE=CACHE_DIR/'history_cache.json.gz'
HISTORY_DAYS=240; MAX_WORKERS=10; RETRIES=3

def sf(v,d=0.0):
    try:return float(v) if v is not None else d
    except:return d
def si(v,d=0):
    try:return int(float(v)) if v is not None else d
    except:return d

def load_cache():
    if not CACHE.exists(): return {}
    try:
        with gzip.open(CACHE,'rt',encoding='utf-8') as f:return json.load(f)
    except Exception as e:
        print('缓存读取失败，将全量重建:',e);return {}

def save_cache(cache):
    CACHE_DIR.mkdir(exist_ok=True)
    with gzip.open(CACHE,'wt',encoding='utf-8') as f:json.dump(cache,f,ensure_ascii=False,separators=(',',':'))

def universe():
    fs=[]
    for m in ('KOSPI','KOSDAQ'):
        d=fdr.StockListing(m).copy()
        if d is None or d.empty: raise RuntimeError(f'{m} 股票列表为空')
        d['Market']=m;fs.append(d)
    return pd.concat(fs,ignore_index=True).drop_duplicates(subset=['Code'])

def fetch(code,old):
    end=datetime.now()
    if old:
        last=datetime.strptime(old[-1][0],'%Y-%m-%d')
        start=last-timedelta(days=10) # 重叠抓取，修正最近数据并补新K线
    else:
        start=end-timedelta(days=420) # 首次足够覆盖约240个交易日
    err=None
    for a in range(RETRIES):
        try:
            df=fdr.DataReader(f'NAVER:{code}',start.strftime('%Y-%m-%d'),end.strftime('%Y-%m-%d'))
            if df is None or df.empty: raise RuntimeError('历史数据为空')
            fresh=[]
            for idx,r in df.iterrows():
                c=si(r.get('Close')); v=si(r.get('Volume'))
                if c<=0: continue
                fresh.append([idx.strftime('%Y-%m-%d'),si(r.get('Open')),si(r.get('High')),si(r.get('Low')),c,v,c*v])
            merged={x[0]:x for x in (old or [])}
            merged.update({x[0]:x for x in fresh})
            hist=[merged[k] for k in sorted(merged)][-HISTORY_DAYS:]
            if len(hist)<20: raise RuntimeError('有效历史不足20日')
            return hist
        except Exception as e:
            err=e;time.sleep(1.2*(a+1))
    print(f'[失败] {code}: {err}');return old or []

def build(code,name,market,marcap,h):
    if len(h)<20:return None
    cs=[x[4] for x in h]; p=cs[-1]; prev=cs[-2]
    ma=lambda n: sum(cs[-n:])/n if len(cs)>=n else sum(cs)/len(cs)
    m5,m10,m20,m30,m60=[ma(n) for n in (5,10,20,30,60)]
    dist=(p/m5-1)*100 if m5 else 0
    wb=cs[-6] if len(cs)>=6 else cs[0]; week=(p/wb-1)*100 if wb else 0
    turns=[x[6]/1e8 for x in h[-20:]]; mn=min(turns); av=sum(turns)/len(turns); turn=h[-1][6]/1e8
    streak=0
    for i in range(len(cs)-1,0,-1):
        if cs[i]>cs[i-1]:streak+=1
        else:break
    trend=25 if m5>=m10>=m20 else (15 if m5>=m10 else 5)
    pos=max(0,25-max(0,abs(dist)-.2)*8); strength=min(18,max(0,week)*1.5); liq=min(12,av/50)
    score=round(min(85,trend+pos+strength+liq+5)); grade='A' if score>=80 else ('B' if score>=68 else 'C')
    return {'name':name,'code':code,'sector':'板块待接入','market':market,
      'marketCapTrillion':marcap/1e12 if marcap else 0,'history':h,'price':p,
      'daychg':(p/prev-1)*100 if prev else 0,'week':week,'turnover':turn,'avgturn':av,
      'minTurn20':mn,'streak':streak,'ma5':m5,'ma10':m10,'ma20':m20,'ma30':m30,'ma60':m60,
      'dist':dist,'rank':0,'sectorPower':0,'score':score,'grade':grade}

def main():
    u=universe(); cache=load_cache(); print('股票总数:',len(u),'缓存股票:',len(cache))
    jobs=[]
    for _,r in u.iterrows():
        code=str(r.get('Code','')).zfill(6); jobs.append((code,str(r.get('Name',code)),str(r.get('Market','')),si(r.get('Marcap',r.get('MarketCap',0)))))
    histories={}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fs={ex.submit(fetch,c,cache.get(c,[])):(c,n,m,mc) for c,n,m,mc in jobs}
        for i,f in enumerate(as_completed(fs),1):
            c,n,m,mc=fs[f]; h=f.result()
            if h: histories[c]=h
            if i%100==0: print(f'进度 {i}/{len(fs)}，有效 {len(histories)}')
    if len(histories)<1200: raise RuntimeError(f'成功股票只有 {len(histories)} 只，拒绝覆盖')
    save_cache(histories)
    res=[]
    for c,n,m,mc in jobs:
        x=build(c,n,m,mc,histories.get(c,[]))
        if x:res.append(x)
    res.sort(key=lambda x:(x['market'],x['code']))
    latest=max(x['history'][-1][0] for x in res)
    meta={'latest_date':latest,'updated_at':datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
      'kospi_count':sum(x['market']=='KOSPI' for x in res),'kosdaq_count':sum(x['market']=='KOSDAQ' for x in res),
      'total_count':len(res),'history_days':HISTORY_DAYS,'source':'FinanceDataReader + NAVER','update_mode':'240日缓存增量'}
    payload='window.DATA_META='+json.dumps(meta,ensure_ascii=False,separators=(',',':'))+';\nwindow.STOCKS_DATA='+json.dumps(res,ensure_ascii=False,separators=(',',':'))+';\n'
    TMP.write_text(payload,encoding='utf-8');TMP.replace(OUT)
    print('更新完成，有效股票:',len(res),'历史上限:',HISTORY_DAYS)
if __name__=='__main__':main()
