#!/usr/bin/env python3
"""Independent NXT historical availability probe; no production writes."""
import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "nxt_history_probe"
OUT.mkdir(exist_ok=True)
DATES = ["2025-10-16", "2025-12-01", "2026-02-02", "2026-04-01",
         "2026-06-01", "2026-08-03", "2026-09-14", "2026-09-30", "2026-10-08"]
custom = os.environ.get("NXT_TEST_DATES", "").strip()
if custom:
    DATES = [datetime.strptime(s.strip(), "%Y-%m-%d").date().isoformat()
             for s in custom.split(",") if s.strip()]
if len(DATES) > 30:
    raise SystemExit("Maximum 30 dates per diagnostic run")

options = webdriver.ChromeOptions()
for flag in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"):
    options.add_argument(flag)
options.add_argument("--window-size=1920,1080")
driver = webdriver.Chrome(options=options)
script = """
const done=arguments[arguments.length-1];
fetch(arguments[0],{method:'POST',credentials:'same-origin',
headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8',
'X-Requested-With':'XMLHttpRequest','Accept':'application/json, text/javascript, */*; q=0.01'},
body:new URLSearchParams(arguments[1]).toString()})
.then(async r=>done(JSON.stringify({status:r.status,text:await r.text()})))
.catch(e=>done(JSON.stringify({status:0,error:String(e)})));
"""
results = []
raw_rows = []
try:
    driver.get("https://www.nextrade.co.kr/menu/transactionStatusMain/menuList.do")
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, "trade1")))
    time.sleep(2)
    for ds in DATES:
        seen = set()
        rows_total = 0
        parsed = 0
        status = "OK"
        detail = ""
        total_count = 0
        for page in range(1, 11):
            payload = {"scSecuGroup":"STOCK","scAggDd":ds.replace("-",""),
                       "_search":"false","nd":str(int(time.time()*1000)),
                       "pageUnit":"1000","pageIndex":str(page),"sidx":"","sord":"asc"}
            try:
                wrap = json.loads(driver.execute_async_script(script, "/brdinfoTime/brdinfoTimeList.do", payload))
                if wrap.get("status") != 200:
                    raise RuntimeError(f"HTTP {wrap.get('status')}: {wrap.get('error','')}")
                body = json.loads(wrap.get("text", "{}"))
                records = body.get("brdinfoTimeList") or []
                total_count = int(body.get("totalCnt") or body.get("records") or 0)
                if not isinstance(records, list):
                    raise ValueError("records not a list")
                rows_total += len(records)
                for row in records:
                    code = str(row.get("isuSrdCd") or "")[-6:]
                    if not (len(code) == 6 and code.isdigit()):
                        continue
                    if code in seen:
                        status = "DUPLICATE"
                    seen.add(code)
                    val = next((row.get(k) for k in ("accTrval","accTrVal","acctTrVal","acctTrval") if row.get(k) is not None), None)
                    vol = next((row.get(k) for k in ("accTdQty","acctTdQty","acctQty","accQty") if row.get(k) is not None), None)
                    if val is None or vol is None:
                        continue
                    try:
                        v = int(str(val).replace(",", ""))
                        q = int(str(vol).replace(",", ""))
                    except (ValueError, TypeError):
                        continue
                    if v < 0 or q < 0:
                        continue
                    parsed += 1
                    raw_rows.append([ds, code, v, q])
                if not records or (total_count and rows_total >= total_count) or len(records) < 1000:
                    break
            except Exception as exc:
                status = "ERROR"
                detail = f"{type(exc).__name__}: {exc}"
                break
        if status == "OK" and rows_total == 0:
            status = "EMPTY_UNVERIFIED"
        if status == "OK" and parsed != len(seen):
            status = "PARTIAL"
        if status == "OK" and total_count and rows_total < total_count:
            status = "PAGINATION_INCOMPLETE"
        results.append([ds, status, rows_total, len(seen), parsed, total_count, detail])
        print(f"{ds}: {status} raw={rows_total} parsed={parsed} expected={total_count}", flush=True)
finally:
    driver.quit()

with (OUT / "nxt_historical_sample.csv").open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["date", "code", "nxt_turnover_won", "nxt_volume_shares"])
    w.writerows(raw_rows)
with (OUT / "date_coverage.csv").open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["date", "status", "raw_records", "unique_codes", "parsed_records", "reported_total", "detail"])
    w.writerows(results)
report = ["# NXT历史日期覆盖率诊断（样本）",
          "本次只检查指定日期是否可查询，尚未证明240日完整可用。",
          "不将空结果自动当作零成交；不修改正式选股器。",
          "", "| 日期 | 状态 | 原始条数 | 有效条数 | 报告总数 |",
          "|---|---|---:|---:|---:|"]
report += [f"| {d} | {st} | {n} | {p} | {t} |" for d, st, n, _, p, t, _ in results]
report += ["", "只有全部样本正常且日期覆盖可信，才考虑全量240日下载和与KRX合并。"]
(OUT / "audit_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
print("\n".join(report))
if any(row[1] not in ("OK", "EMPTY_UNVERIFIED") for row in results):
    raise SystemExit("Some dates returned errors or incomplete records; inspect audit")
