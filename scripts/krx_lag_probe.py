#!/usr/bin/env python3
"""KRX official Open API availability lag probe. Never prints the authentication key."""
import os, json, datetime, requests
from pathlib import Path
key=os.getenv("KRX_AUTH_KEY") or os.getenv("AUTH_KEY")
if not key:
    raise SystemExit("MISSING_KEY: configure repository secret KRX_AUTH_KEY (or AUTH_KEY)")
days=[x.strip() for x in os.getenv("TEST_DATES","20261009,20261008,20261007").split(",") if x.strip()]
session=requests.Session()
session.headers.update({"AUTH_KEY":key,"User-Agent":"korea-mode2-screener/krx-lag-probe"})
summary=[]
for day in days:
    for market,api in (("KOSPI","stk_bydd_trd"),("KOSDAQ","ksq_bydd_trd")):
        item={"requested":day,"market":market}
        try:
            url=f"https://data-dbg.krx.co.kr/svc/apis/sto/{api}"
            response=session.get(url,params={"basDd":day},timeout=35)
            item["http_status"]=response.status_code
            try:
                payload=response.json()
                rows=payload.get("OutBlock_1",[]) if isinstance(payload,dict) else []
                if isinstance(rows,dict): rows=[rows]
                item["rows"]=len(rows) if isinstance(rows,list) else 0
                if isinstance(payload,dict):
                    item["response_keys"]=list(payload.keys())[:8]
                item["returned_dates"]=sorted(set(str(r.get("BAS_DD","")) for r in rows if isinstance(r,dict)))[:8]
                examples=[]
                for r in rows:
                    if not isinstance(r,dict): continue
                    if any(s in str(r.get("ISU_NM","")) for s in ("삼성전자","SK하이닉스","두산에너빌리티")):
                        examples.append({k:r.get(k) for k in ("BAS_DD","ISU_CD","ISU_NM","TDD_CLSPRC","ACC_TRDVOL","ACC_TRDVAL","MKTCAP")})
                item["examples"]=examples[:5]
                if not rows: item["empty_or_error"]=str(payload)[:250]
            except ValueError:
                item["non_json_response"]=response.text[:180]
        except requests.RequestException as exc:
            item["request_error"]=type(exc).__name__+": "+str(exc)[:150]
        summary.append(item)
        print(json.dumps(item,ensure_ascii=False),flush=True)
Path(".data_cache").mkdir(exist_ok=True)
Path(".data_cache/krx_lag_probe.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
for day in days:
    records=[r for r in summary if r["requested"]==day]
    print("DATE_RESULT",day," ".join(f'{r["market"]}:{r.get("rows",0)} rows HTTP {r.get("http_status","ERR")}' for r in records))
