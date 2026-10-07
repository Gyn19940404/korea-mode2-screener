#!/usr/bin/env python3
import os, json, requests
from datetime import datetime, timedelta, timezone

VERSION="KRX MARKETPLACE PROBE V1"
DATE=os.environ.get("TRADE_DATE","20261007")
TARGETS={"005930":"삼성전자","000660":"SK하이닉스","009150":"삼성전기","105560":"KB금융","047040":"대우건설"}
BASE="https://data.krx.co.kr"
# KRX Data Marketplace [12001] 전종목 시세 backend used by the web statistics screen.
CANDIDATES=[
 ("MDCSTAT01501","/comm/bldAttendant/getJsonData.cmd"),
]
PARAMS={
 "bld":"dbms/MDC/STAT/standard/MDCSTAT01501",
 "locale":"ko_KR",
 "mktId":"STK",
 "trdDd":DATE,
 "share":"1",
 "money":"1",
 "csvxls_isNo":"false",
}

s=requests.Session()
s.headers.update({
 "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
 "Referer":BASE+"/contents/MDC/MAIN/main/index.cmd",
 "Origin":BASE,
 "Accept":"application/json, text/javascript, */*; q=0.01",
 "X-Requested-With":"XMLHttpRequest",
})
print(VERSION)
print("TRADE_DATE:",DATE)
# establish cookies/session first
home=s.get(BASE+"/contents/MDC/MAIN/main/index.cmd",timeout=30)
print("HOME_HTTP:",home.status_code,"cookies",len(s.cookies))

out={}
for label,path in CANDIDATES:
    r=s.post(BASE+path,data=PARAMS,timeout=30)
    print("ENDPOINT:",label,"HTTP",r.status_code,"bytes",len(r.content),"type",r.headers.get("content-type"))
    print("BODY_PREFIX:",r.text[:180].replace("\n"," "))
    if r.status_code!=200:
        continue
    try:data=r.json()
    except Exception as e:
        print("JSON_ERROR:",repr(e)); continue
    print("TOP_KEYS:",list(data.keys())[:20])
    blocks=[]
    for k,v in data.items():
        if isinstance(v,list):
            print("LIST_BLOCK:",k,"rows",len(v))
            if v: blocks.append((k,v))
    rows=max(blocks,key=lambda x:len(x[1]))[1] if blocks else []
    if rows:
        print("SAMPLE_KEYS:",list(rows[0].keys())[:30])
    for x in rows:
        code=str(x.get("ISU_SRT_CD") or x.get("ISU_CD") or x.get("short_code") or "").strip()
        if code in TARGETS:
            out[code]=x

print("FOUND_TARGETS:",len(out),"/",len(TARGETS))
for code,name in TARGETS.items():
    x=out.get(code)
    if not x:
        print(code,name,"NOT_FOUND"); continue
    print(code,name,json.dumps(x,ensure_ascii=False)[:1000])

os.makedirs(".data_cache",exist_ok=True)
with open(".data_cache/krx_marketplace_probe.json","w",encoding="utf-8") as f:
    json.dump({"version":VERSION,"date":DATE,"targets":out},f,ensure_ascii=False,indent=2)
if not out:
    raise RuntimeError("Marketplace endpoint returned no target rows; inspect diagnostics")
