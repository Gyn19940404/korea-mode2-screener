#!/usr/bin/env python3
"""Generate independent, dated Mode2 turnover gate, never production data."""
import csv
import json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"nxt_forensic_audit"/"threshold_500eok_20d.csv"
MERGED=ROOT/"merged_240d_audit"/"krx_nxt_daily_turnover.csv"
OUT=ROOT/"mode2_turnover_gate_test"
OUT.mkdir(exist_ok=True)
dates=set()
with MERGED.open(encoding="utf-8-sig",newline="") as f:
    for r in csv.DictReader(f):dates.add(r["date"].replace("-",""))
dates=sorted(dates)
if len(dates)!=240:raise SystemExit("Expected 240 unique sessions")
asof=dates[-1];start=dates[-20]
mapping={"PASS_VERIFIED":"PASS","PASS_BY_KRX_LOWER_BOUND":"PASS","FAIL_VERIFIED":"FAIL","UNKNOWN":"UNKNOWN"}
rows=[];count=Counter()
with SRC.open(encoding="utf-8-sig",newline="") as f:
    reader=csv.DictReader(f)
    for r in reader:
        code=r["code"];detail=r["threshold_500eok_decision"]
        if detail not in mapping:raise SystemExit(f"Unexpected decision: {detail}")
        gate=mapping[detail]
        kdays=int(r["krx_days"]);ndays=int(r["nxt_observed_days"])
        if gate=="PASS" and kdays!=20:raise SystemExit(f"Insufficient history for PASS {code}")
        if detail in ("PASS_VERIFIED","FAIL_VERIFIED") and ndays!=20:raise SystemExit(f"Not fully matched: {code}")
        if detail=="PASS_BY_KRX_LOWER_BOUND" and int(r["krx_avg20_won"])<50_000_000_000:raise SystemExit(f"Bad lower bound: {code}")
        if detail=="FAIL_VERIFIED" and int(r["combined_avg20_won_if_verified"])>=50_000_000_000:raise SystemExit(f"Bad FAIL: {code}")
        if detail=="PASS_VERIFIED" and int(r["combined_avg20_won_if_verified"])<50_000_000_000:raise SystemExit(f"Bad PASS: {code}")
        count[gate]+=1
        rows.append([asof,start,code,gate,detail,r["krx_days"],r["nxt_observed_days"],r["krx_avg20_won"],r["combined_avg20_won_if_verified"]])
if len({r[2] for r in rows})!=len(rows):raise SystemExit("Duplicate stock codes")
if len(rows)!=2855:raise SystemExit(f"Unexpected number of codes: {len(rows)}")
with (OUT/"mode2_turnover_gate.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f);w.writerow(["asof_date","window_start","code","gate","basis","krx_days","nxt_observed_days","krx_avg20_won","verified_combined_avg20_won"]);w.writerows(rows)
manifest={"asof_date":asof,"window_start":start,"threshold_won":50_000_000_000,"count":dict(count),"source":"KRX+NXT historical artifacts; conservative missing handling","generated_utc":datetime.now(timezone.utc).isoformat(),"production_eligible":False}
(OUT/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
report=["# 模式2成交额三态门槛测试（不发布）",f"快照交易日：{asof}；20日窗口：{start}～{asof}",f"PASS：{count['PASS']}；FAIL：{count['FAIL']}；UNKNOWN：{count['UNKNOWN']}","PASS只代表20日平均成交额满足500亿韩元条件，不代表模式2买点。","KRX下界达标者的综合成交额数值留空，不伪造精确值。","UNKNOWN不自动当FAIL；所有记录带交易日，禁止旧快照冒充最新数据。","此测试产物不写入网站数据，也不触发交易。"]
(OUT/"audit_report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
print("\n".join(report))
