#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V0.9.53 独立诊断行情层。
完全不 import scripts/update_data.py；三时间点诊断与正式网页代码物理解耦。
"""
import json, time, threading
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36'
RETRIES=3
_tls=threading.local()

def session():
    if not hasattr(_tls,'s'):
        s=requests.Session(); s.headers.update({'User-Agent':UA,'Referer':'https://finance.naver.com/'}); _tls.s=s
    return _tls.s

def sf(v,d=0.0):
    try:
        if isinstance(v,str): v=v.replace(',','').replace('%','').strip()
        return float(v) if v not in (None,'') else d
    except Exception: return d

def si(v,d=0):
    try:
        if isinstance(v,str): v=v.replace(',','').strip()
        return int(float(v)) if v not in (None,'') else d
    except Exception: return d

def fetch_quote(code):
    url=f'https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:{code}'
    err=None
    for a in range(RETRIES):
        try:
            r=session().get(url,timeout=12); r.raise_for_status(); js=r.json()
            areas=js.get('result',{}).get('areas',[])
            if not areas or not areas[0].get('datas'): raise RuntimeError('polling 无数据')
            d=areas[0]['datas'][0]; price=si(d.get('nv'))
            if price<=0: raise RuntimeError('KRX价格为空')
            return {'price':price,'krxPrice':price,'krxTurnoverWon':si(d.get('aa')),
                    'krxVolume':si(d.get('aq')),'prevClose':si(d.get('sv'))}
        except Exception as e:
            err=e; time.sleep(0.8*(a+1))
    print(f'[诊断KRX失败] {code}: {err}'); return None

def fetch_nxt_official(target_date):
    url='https://www.nextrade.co.kr/menu/transactionStatusMain/menuList.do'
    options=webdriver.ChromeOptions()
    for arg in ('--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--window-size=1920,1080'):
        options.add_argument(arg)
    options.add_argument(f'--user-agent={UA}')
    driver=None; out={}
    def pick(d,*keys):
        for k in keys:
            if k in d and d[k] not in (None,''): return d[k]
    try:
        driver=webdriver.Chrome(options=options); driver.get(url)
        WebDriverWait(driver,30).until(EC.presence_of_element_located((By.ID,'trade1'))); time.sleep(3)
        payload={'scSecuGroup':'STOCK','scAggDd':target_date.replace('-',''),'_search':'false',
                 'nd':str(int(time.time()*1000)),'pageUnit':'1000','pageIndex':'1','sidx':'','sord':'asc'}
        script="""const done=arguments[arguments.length-1],p=arguments[0],x=arguments[1];
        fetch(p,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest','Accept':'application/json, text/javascript, */*; q=0.01'},body:new URLSearchParams(x).toString()})
        .then(async r=>done(JSON.stringify({status:r.status,text:await r.text()})))
        .catch(e=>done(JSON.stringify({status:0,error:String(e)})));"""
        raw=driver.execute_async_script(script,'/brdinfoTime/brdinfoTimeList.do',payload)
        wrap=json.loads(raw)
        if wrap.get('status')!=200: raise RuntimeError(f'NXT XHR HTTP {wrap.get("status")}')
        data=json.loads(wrap.get('text','{}')); records=data.get('brdinfoTimeList') or []
        print(f'[独立诊断NXT] {target_date} 原始={len(records)}只 setTime={data.get("setTime")}')
        for r in records:
            code=str(r.get('isuSrdCd') or '').strip()[-6:]
            if len(code)!=6 or not code.isdigit(): continue
            price=si(r.get('curPrc'))
            if price<=0: continue
            vol=si(pick(r,'accTdQty','acctTdQty','acctQty','accQty'))
            val=si(pick(r,'accTrval','accTrVal','acctTrVal','acctTrval'))
            out[code]={'price':price,'pct':sf(r.get('upDownRate')),'volume':vol,'value':val,
                       'high':si(r.get('hgpr')),'low':si(r.get('lwpr'))}
        if records and len(out)<300: raise RuntimeError(f'NXT解析仅{len(out)}只，拒绝残缺快照')
        return out
    finally:
        if driver is not None: driver.quit()
