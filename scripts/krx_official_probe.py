#!/usr/bin/env python3
import os, json, requests
from datetime import datetime, timedelta, timezone

KST=timezone(timedelta(hours=9))
DATE=os.environ.get("TRADE_DATE") or (datetime.now(KST)-timedelta(days=1)).strftime("%Y%m%d")
KEY=os.environ["KRX_API_KEY"].strip()\nprint("KRX PROBE V2")\nprint("TRADE_DATE:", DATE)\nprint("KEY_PRESENT:", bool(KEY), "KEY_LENGTH:", len(KEY))
TARGETS={"005930":"삼성전자","000660":"SK하이닉스","402340":"SK스퀘어","009150":"삼성전기","105560":"KB금융","034020":"두산에너빌리티","006400":"삼성SDI","042700":"한미반도체","028300":"HLB","047040":"대우건설"}
URLS=[
 ("KOSPI","https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"),
 ("KOSDAQ","https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"),
]
def num(v):
    try:return int(str(v).replace(",","").strip() or 0)
    except:return 0
rows={}
for market,url in URLS:
    r=requests.get(url,headers={"AUTH_KEY":KEY,"Accept":"application/json"},params={"basDd":DATE},timeout=30)
    print(market,"HTTP",r.status_code,"bytes",len(r.content))
    r.raise_for_status()
    data=r.json()
    items=data.get("OutBlock_1") or data.get("output") or []
    print(market,"rows",len(items))
    for x in items:
        code=str(x.get("ISU_CD","")).strip()
        if code in TARGETS:
            rows[code]={
              "market":market,"code":code,"name":x.get("ISU_NM") or TARGETS[code],
              "close":num(x.get("TDD_CLSPRC")),"change":x.get("CMPPREVDD_PRC"),
              "pct":x.get("FLUC_RT"),"volume":num(x.get("ACC_TRDVOL")),
              "turnoverWon":num(x.get("ACC_TRDVAL")),"marketCapWon":num(x.get("MKTCAP")),
              "listedShares":num(x.get("LIST_SHRS"))
            }
print("\n=== KRX OFFICIAL 10-STOCK CHECK",DATE,"===")
for code,name in TARGETS.items():
    x=rows.get(code)
    if not x: print(code,name,"NOT FOUND"); continue
    print(f'{code} {name} | price={x["close"]:,} | volume={x["volume"]:,} | turnover={x["turnoverWon"]/1e8:.1f}亿 | mcap={x["marketCapWon"]/1e12:.3f}兆 | shares={x["listedShares"]:,}')
os.makedirs(".data_cache",exist_ok=True)
with open(".data_cache/krx_official_10stocks.json","w",encoding="utf-8") as f:
    json.dump({"tradeDate":DATE,"stocks":list(rows.values())},f,ensure_ascii=False,indent=2)
