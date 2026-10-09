#!/usr/bin/env python3
"""Independent KRX vs NAVER date-aware quote/volume comparison; read-only."""
import os,json,re,requests
from pathlib import Path
key=os.getenv("KRX_API_KEY")
if not key: raise SystemExit("MISSING_KEY KRX_API_KEY")
date=os.getenv("TRADE_DATE","20261008")
codes={"005930":"삼성전자","000660":"SK하이닉스","034020":"두산에너빌리티","009150":"삼성전기","373220":"LG에너지솔루션","005380":"현대차"}
s=requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0"})
krx={}
for market,endpoint in (("KOSPI","stk_bydd_trd"),("KOSDAQ","ksq_bydd_trd")):
    r=s.get("https://data-dbg.krx.co.kr/svc/apis/sto/"+endpoint,params={"basDd":date},headers={"AUTH_KEY":key},timeout=35)
    r.raise_for_status()
    rows=r.json().get("OutBlock_1",[])
    for row in rows:
        code=str(row.get("ISU_CD","")).strip()
        if code in codes: krx[code]=row
    print("KRX",market,"rows",len(rows),flush=True)
results=[]
for code,name in codes.items():
    item={"code":code,"name":name,"date":date}
    row=krx.get(code)
    if row:
        item["krx"]={k:row.get(k) for k in ("BAS_DD","ISU_CD","ISU_NM","TDD_CLSPRC","ACC_TRDVOL","ACC_TRDVAL","MKTCAP")}
    try:
        r=s.get("https://fchart.stock.naver.com/sise.nhn",params={"symbol":code,"timeframe":"day","count":30,"requestType":0},timeout=20);r.raise_for_status()
        matches=re.findall(r'data="(\\d{8})\\|([^"]+)"',r.text)
        hit=next((v for d,v in matches if d==date),None)
        item["naver_fchart_ohlcv"]=hit.split("|")[:5] if hit else None
        item["naver_latest_date"]=matches[-1][0] if matches else None
    except Exception as e:item["naver_history_error"]=str(e)[:160]
    try:
        r=s.get("https://polling.finance.naver.com/api/realtime",params={"query":"SERVICE_ITEM:"+code},timeout=20);r.raise_for_status()
        q=r.json()["result"]["areas"][0]["datas"][0]
        item["naver_polling_current"]={k:q.get(k) for k in ("nv","sv","aq","aa","ms","cd")}
        item["polling_warning"]="Realtime snapshot; do not compare as historical if snapshot date differs"
    except Exception as e:item["polling_error"]=str(e)[:160]
    if row and item.get("naver_fchart_ohlcv"):
        vals=item["naver_fchart_ohlcv"]
        try:
            item["krx_minus_naver_volume"]=int(str(row["ACC_TRDVOL"]).replace(",",""))-int(vals[4])
            item["krx_minus_naver_close"]=int(str(row["TDD_CLSPRC"]).replace(",",""))-int(vals[3])
        except Exception:pass
    results.append(item)
    print(json.dumps(item,ensure_ascii=False),flush=True)
Path(".data_cache").mkdir(exist_ok=True)
Path(".data_cache/krx_naver_comparison.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
