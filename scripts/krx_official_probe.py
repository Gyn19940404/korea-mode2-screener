#!/usr/bin/env python3
import os,json,requests
VERSION="KRX MARKETPLACE PROBE V1"
DATE=os.environ.get("TRADE_DATE","20261007")
TARGETS={"005930":"삼성전자","000660":"SK하이닉스","009150":"삼성전기","105560":"KB금융","047040":"대우건설"}
BASE="https://data.krx.co.kr"
P={"bld":"dbms/MDC/STAT/standard/MDCSTAT01501","locale":"ko_KR","mktId":"STK","trdDd":DATE,"share":"1","money":"1","csvxls_isNo":"false"}
s=requests.Session();s.headers.update({"User-Agent":"Mozilla/5.0","Referer":BASE+"/contents/MDC/MAIN/main/index.cmd","Origin":BASE,"Accept":"application/json, text/javascript, */*; q=0.01","X-Requested-With":"XMLHttpRequest"})
print(VERSION);print("TRADE_DATE:",DATE)
h=s.get(BASE+"/contents/MDC/MAIN/main/index.cmd",timeout=30);print("HOME_HTTP:",h.status_code,"cookies",len(s.cookies))
r=s.post(BASE+"/comm/bldAttendant/getJsonData.cmd",data=P,timeout=30)
print("HTTP",r.status_code,"bytes",len(r.content),"type",r.headers.get("content-type"));print("BODY_PREFIX:",r.text[:180].replace("\n"," "))
r.raise_for_status();data=r.json();print("TOP_KEYS:",list(data.keys())[:20])
blocks=[]
for k,v in data.items():
 if isinstance(v,list):
  print("LIST_BLOCK:",k,"rows",len(v))
  if v: blocks.append((k,v))
rows=max(blocks,key=lambda z:len(z[1]))[1] if blocks else []
if rows: print("SAMPLE_KEYS:",list(rows[0].keys())[:40])
out={}
for x in rows:
 code=str(x.get("ISU_SRT_CD") or x.get("ISU_CD") or "").strip()
 if code in TARGETS: out[code]=x
print("FOUND_TARGETS:",len(out),"/",len(TARGETS))
for code,name in TARGETS.items(): print(code,name,json.dumps(out.get(code),ensure_ascii=False)[:1200])
os.makedirs(".data_cache",exist_ok=True)
with open(".data_cache/krx_marketplace_probe.json","w",encoding="utf-8") as f: json.dump({"version":VERSION,"date":DATE,"targets":out},f,ensure_ascii=False,indent=2)
if not out: raise RuntimeError("Marketplace returned no target rows; inspect diagnostics")
