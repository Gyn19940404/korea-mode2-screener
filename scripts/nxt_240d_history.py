#!/usr/bin/env python3
"""NXT 240-session historical turnover extraction and coverage audit (standalone)."""
import csv
import json
import os
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"nxt_240d_official"
OUT.mkdir(exist_ok=True)
KRX=ROOT/"krx_240d_official"/"manifest.json"
if not KRX.exists():
    raise SystemExit("Missing KRX manifest: supply the successful KRX artifact")
manifest=json.loads(KRX.read_text(encoding="utf-8"))
dates=sorted(manifest.get("market_counts",{}))
if len(dates)!=240 or manifest.get("errors"):
    raise SystemExit("KRX manifest does not certify 240 sessions")
options=webdriver.ChromeOptions()
for flag in ("--headless=new","--no-sandbox","--disable-dev-shm-usage","--disable-gpu"):
    options.add_argument(flag)
options.add_argument("--window-size=1920,1080")
driver=webdriver.Chrome(options=options)
js="""
const done=arguments[arguments.length-1];
fetch('/brdinfoTime/brdinfoTimeList.do',{method:'POST',credentials:'same-origin',
headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8',
'X-Requested-With':'XMLHttpRequest','Accept':'application/json, text/javascript, */*; q=0.01'},
body:new URLSearchParams(arguments[0]).toString()})
.then(async r=>done(JSON.stringify({status:r.status,text:await r.text()})))
.catch(e=>done(JSON.stringify({status:0,error:String(e)})));
"""
coverage=[]
data=[]
try:
    driver.get("https://www.nextrade.co.kr/menu/transactionStatusMain/menuList.do")
    WebDriverWait(driver,30).until(EC.presence_of_element_located((By.ID,"trade1")))
    time.sleep(2)
    for idx,ds in enumerate(dates,1):
        records=[]
        total=0
        error=""
        for page in range(1,11):
            payload={"scSecuGroup":"STOCK","scAggDd":ds.replace("-",""),
                     "_search":"false","nd":str(int(time.time()*1000)),
                     "pageUnit":"1000","pageIndex":str(page),"sidx":"","sord":"asc"}
            try:
                response=json.loads(driver.execute_async_script(js,payload))
                if response.get("status")!=200:
                    raise RuntimeError(f"HTTP {response.get('status')}")
                body=json.loads(response["text"])
                rows=body.get("brdinfoTimeList") or []
                if not isinstance(rows,list):
                    raise ValueError("Non-list records")
                total=int(body.get("totalCnt") or body.get("records") or 0)
                records.extend(rows)
                if not rows or (total and len(records)>=total) or len(rows)<1000:
                    break
            except Exception as exc:
                error=f"{type(exc).__name__}: {exc}"
                break
        seen=set()
        missing=[]
        duplicate=[]
        parsed=[]
        for row in records:
            code=str(row.get("isuSrdCd") or "")[-6:]
            if len(code)!=6 or not code.isdigit():
                missing.append(str(row.get("isuSrdCd")))
                continue
            if code in seen:
                duplicate.append(code)
                continue
            seen.add(code)
            value=next((row.get(k) for k in ("accTrval","accTrVal","acctTrVal","acctTrval") if row.get(k) is not None),None)
            volume=next((row.get(k) for k in ("accTdQty","acctTdQty","acctQty","accQty") if row.get(k) is not None),None)
            try:
                v=int(str(value).replace(",",""))
                q=int(str(volume).replace(",",""))
                if v<0 or q<0:
                    raise ValueError("negative")
            except (ValueError,TypeError):
                missing.append(code)
                continue
            parsed.append((ds,code,v,q))
        status="OK"
        if error: status="ERROR"
        elif not records: status="EMPTY_UNVERIFIED"
        elif total and len(records)!=total: status="PAGINATION"
        elif duplicate: status="DUPLICATE"
        elif missing: status="PARTIAL"
        data.extend(parsed)
        coverage.append((ds,status,len(records),len(parsed),total,len(duplicate),len(missing),";".join(missing[:20]),error))
        if idx%20==0 or status!="OK":
            print(f"{idx}/240 {ds}: {status}, records={len(records)}, parsed={len(parsed)}",flush=True)
finally:
    driver.quit()
with (OUT/"nxt_daily_turnover.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f);w.writerow(["date","code","nxt_turnover_won","nxt_volume_shares"]);w.writerows(data)
with (OUT/"date_coverage.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f);w.writerow(["date","status","records","parsed","reported_total","duplicates","missing","missing_sample","error"]);w.writerows(coverage)
counts={}
for _,status,*_ in coverage: counts[status]=counts.get(status,0)+1
report=["# NXT 240交易日成交额采集审计",f"KRX基准交易日：{len(dates)}；NXT返回记录：{len(data):,}",f"状态统计：{json.dumps(counts,ensure_ascii=False)}","NXT只覆盖其交易股票；缺少某只股票不自动解释为零成交。","本任务不修改正式网页，也不自动与KRX合并。","","## 非完整日期"]
report += [f"- {d}: {st} 原始{n} 有效{p} 报告{t} 未解析{miss} {err}" for d,st,n,p,t,dup,miss,sample,err in coverage if st!="OK"][:50] or ["- 无"]
(OUT/"audit_report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
print("\n".join(report),flush=True)
if any(st in ("ERROR","PAGINATION","DUPLICATE") for _,st,*_ in coverage):
    raise SystemExit("Critical NXT coverage errors")
