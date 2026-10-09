#!/usr/bin/env python3
"""Read-only KRX + NXT vs Toss six-stock volume reconciliation."""
import os,json,requests
from pathlib import Path
from scripts.update_data import fetch_nxt_official
DAY=os.getenv("TRADE_DATE","20261008").replace("-","")
KEY=os.getenv("KRX_API_KEY")
if not KEY: raise SystemExit("KRX_API_KEY secret missing")
# Screenshot supplied by user: Toss stock screener 2026-10-08.
TOSS={"005930":30352099,"000660":4373490,"034020":7732776,"009150":897747,"373220":1379171,"005380":1101179}
NAMES={"005930":"삼성전자","000660":"SK하이닉스","034020":"두산에너빌리티","009150":"삼성전기","373220":"LG에너지솔루션","005380":"현대차"}
s=requests.Session();s.headers.update({"AUTH_KEY":KEY,"User-Agent":"Mozilla/5.0"})
official={}
for market,endpoint in (("KOSPI","stk_bydd_trd"),("KOSDAQ","ksq_bydd_trd")):
    r=s.get("https://data-dbg.krx.co.kr/svc/apis/sto/"+endpoint,params={"basDd":DAY},timeout=40)
    r.raise_for_status()
    records=r.json().get("OutBlock_1",[])
    print("KRX",market,"rows",len(records),flush=True)
    for rec in records:
        code=str(rec.get("ISU_CD",""))
        if code in TOSS: official[code]=rec
nxt=fetch_nxt_official(DAY[:4]+"-"+DAY[4:6]+"-"+DAY[6:])
print("NXT parsed stock count",len(nxt),flush=True)
results=[]
for code,name in NAMES.items():
    k=official.get(code);n=nxt.get(code)
    kv=int(k["ACC_TRDVOL"]) if k else None
    nv=int(n["volume"]) if n else None
    tv=TOSS[code] if DAY=="20261008" else None
    combined=kv+nv if kv is not None and nv is not None else None
    item={"code":code,"name":name,"date":DAY,"krx_volume":kv,"nxt_volume":nv,"krx_plus_nxt":combined,"toss_screenshot_volume":tv,"difference_vs_toss":combined-tv if combined is not None and tv is not None else None,"krx_turnover_won":int(k["ACC_TRDVAL"]) if k else None,"nxt_turnover_won":int(n["value"]) if n else None}
    results.append(item);print("COMPARE",json.dumps(item,ensure_ascii=False),flush=True)
Path(".data_cache").mkdir(exist_ok=True)
Path(".data_cache/krx_nxt_toss_compare.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
