#!/usr/bin/env python3
"""Check same-day Korean OHLCV freshness from free NAVER fchart and polling."""
import os, re, json, datetime
from pathlib import Path
import requests

DATE=os.environ.get("TRADE_DATE","20261007")
CODES={"005930":"삼성전자","000660":"SK하이닉스","009150":"삼성전기","034020":"두산에너빌리티","196170":"알테오젠"}
S=requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://finance.naver.com/"})
out={}
for code,name in CODES.items():
    row={"name":name}
    try:
        r=S.get("https://fchart.stock.naver.com/sise.nhn",params={"symbol":code,"timeframe":"day","count":"300","requestType":"0"},timeout=20)
        r.raise_for_status()
        matches=re.findall(r'data="(\d{8})\|([^"]+)"',r.text)
        row["history_count"]=len(matches)
        row["latest_history_date"]=matches[-1][0] if matches else ""
        hit=next((v for d,v in matches if d==DATE),None)
        row["target_day_ohlcv"]=hit.split("|")[:5] if hit else None
    except Exception as e: row["history_error"]=str(e)
    try:
        r=S.get("https://polling.finance.naver.com/api/realtime",params={"query":"SERVICE_ITEM:"+code},timeout=20)
        r.raise_for_status()
        data=r.json()["result"]["areas"][0]["datas"][0]
        row["polling"]={k:data.get(k) for k in ("nv","sv","aq","aa","cr","ms","ty","cd")}
        row["polling_raw_keys"]=list(data.keys())
    except Exception as e: row["polling_error"]=str(e)
    print(code,name,json.dumps(row,ensure_ascii=False),flush=True)
    out[code]=row
Path(".data_cache").mkdir(exist_ok=True)
Path(".data_cache/naver_freshness_probe.json").write_text(json.dumps({"target":DATE,"results":out},ensure_ascii=False,indent=2),encoding="utf-8")
good=sum(bool(v.get("target_day_ohlcv")) for v in out.values())
print("TARGET_DAY_HISTORY",good,"/",len(out),flush=True)
if good<len(out): raise RuntimeError("目标交易日K线不完整；禁止发布旧数据")
print("PASS: NAVER target-day OHLCV available")
