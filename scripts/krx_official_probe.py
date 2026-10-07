#!/usr/bin/env python3
import os, json, requests
from datetime import datetime, timedelta, timezone

VERSION="KRX PROBE V6 STRICT-DATE"
KST=timezone(timedelta(hours=9))
INPUT_DATE=os.environ.get("TRADE_DATE") or datetime.now(KST).strftime("%Y%m%d")
KEY=os.environ.get("KRX_API_KEY","").strip()
print(VERSION)
print("INPUT_DATE:",INPUT_DATE)
print("KEY_PRESENT:",bool(KEY),"KEY_LENGTH:",len(KEY))
if not KEY: raise RuntimeError("KRX_API_KEY missing")

TARGETS={"005930":"삼성전자","000660":"SK하이닉스","402340":"SK스퀘어","009150":"삼성전기","105560":"KB금융","034020":"두산에너빌리티","006400":"삼성SDI","042700":"한미반도체","028300":"HLB","047040":"대우건설"}
URLS={"KOSPI":"https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd","KOSDAQ":"https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"}

def request_day(market,date):
    r=requests.get(URLS[market],headers={"AUTH_KEY":KEY,"Accept":"application/json"},params={"basDd":date},timeout=30)
    if r.status_code!=200:
        print(market,date,"HTTP",r.status_code,"BODY",r.text[:300])
        return None
    data=r.json()
    items=data.get("OutBlock_1") or data.get("output") or []
    print(market,date,"HTTP 200 rows",len(items))
    return items

start=datetime.strptime(INPUT_DATE,"%Y%m%d")
selected=None
selected_data=None
exact_date_available=False
print("\n=== AUTO DATE BACKTRACK ===")
for offset in range(10):
    d=(start-timedelta(days=offset)).strftime("%Y%m%d")
    kospi=request_day("KOSPI",d)
    kosdaq=request_day("KOSDAQ",d)
    if kospi is not None and kosdaq is not None and len(kospi)>0 and len(kosdaq)>0:
        selected=d
        selected_data={"KOSPI":kospi,"KOSDAQ":kosdaq}
        exact_date_available=(d==INPUT_DATE)
        print("SELECTED_DATE:",selected)
        break
if not selected:
    raise RuntimeError("No populated KOSPI+KOSDAQ date found in 10-day lookback")

def num(v):
    try:return int(str(v).replace(",","").strip() or 0)
    except (TypeError,ValueError):return 0

rows={}
for market,items in selected_data.items():
    for x in items:
        code=str(x.get("ISU_CD","")).strip()
        if code in TARGETS:
            rows[code]={"market":market,"code":code,"name":x.get("ISU_NM") or TARGETS[code],
                "close":num(x.get("TDD_CLSPRC")),"change":x.get("CMPPREVDD_PRC"),"pct":x.get("FLUC_RT"),
                "volume":num(x.get("ACC_TRDVOL")),"turnoverWon":num(x.get("ACC_TRDVAL")),
                "marketCapWon":num(x.get("MKTCAP")),"listedShares":num(x.get("LIST_SHRS"))}

print("EXACT_DATE_AVAILABLE:", exact_date_available)
if not exact_date_available:
    print("DATE_MISMATCH: requested",INPUT_DATE,"but latest populated KRX date is",selected)
    print("VALIDATION_STATUS: BLOCKED_FOR_KB_TOSS_COMPARISON")
else:
    print("VALIDATION_STATUS: EXACT_DATE_OK")

print("\n=== KRX OFFICIAL 10-STOCK CHECK",selected,"===")
for code,name in TARGETS.items():
    x=rows.get(code)
    if not x:
        print(code,name,"NOT FOUND"); continue
    print(f'{code} {name} | price={x["close"]:,} | volume={x["volume"]:,} | turnover={x["turnoverWon"]/1e8:.1f}亿 | mcap={x["marketCapWon"]/1e12:.3f}兆 | shares={x["listedShares"]:,}')
print("FOUND_TARGETS:",len(rows),"/",len(TARGETS))
print("\n=== BOUNDARY AUDIT ===")
print("Purpose: KRX official daily fields are the KRX-side baseline only.")
print("Do NOT infer consolidated KRX+NXT from ACC_TRDVOL/ACC_TRDVAL.")
print("Audit fields: close, volume, turnover, marketCap, listedShares.")
print("Next acceptance test: compare this KRX baseline + official NXT against KB/Toss, stock by stock.")
print("CONTROL_NO_NXT: 047040 대우건설 should match KRX-side broker data without an NXT addition.")
print("HIGH_DELTA_TARGET: 009150 삼성전기 is the priority mismatch diagnostic.")
if len(rows)<8: raise RuntimeError(f"Only {len(rows)}/10 target stocks found")

os.makedirs(".data_cache",exist_ok=True)
with open(".data_cache/krx_official_10stocks.json","w",encoding="utf-8") as f:
    json.dump({"version":VERSION,"inputDate":INPUT_DATE,"selectedDate":selected,"exactDateAvailable":exact_date_available,"validationStatus":"EXACT_DATE_OK" if exact_date_available else "BLOCKED_DATE_MISMATCH","stocks":list(rows.values())},f,ensure_ascii=False,indent=2)
