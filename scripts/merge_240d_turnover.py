#!/usr/bin/env python3
"""Audit and merge official KRX and NXT historical turnover without modifying production."""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
K=ROOT/"krx_240d_official"
N=ROOT/"nxt_240d_official"
OUT=ROOT/"merged_240d_audit"
OUT.mkdir(exist_ok=True)
manifest=json.loads((K/"manifest.json").read_text(encoding="utf-8"))
dates=sorted(manifest["market_counts"])
if len(dates)!=240 or manifest.get("errors"):
    raise SystemExit("Invalid KRX 240-session manifest")
def load(path, expected):
    data={}
    duplicates=[]
    with path.open(encoding="utf-8-sig",newline="") as f:
        reader=csv.DictReader(f)
        if not set(expected).issubset(reader.fieldnames or []):
            raise SystemExit(f"Missing columns in {path}: {expected}")
        for r in reader:
            day=r["date"].replace("-","")
            code=r["code"].strip()
            if day not in dates or len(code)!=6 or not code.isalnum():
                raise SystemExit(f"Invalid date/code in {path}: {day} {code}")
            key=(day,code)
            if key in data:
                duplicates.append(key)
                continue
            value=int(r[expected[-1]])
            if value<0:
                raise SystemExit(f"Negative turnover: {key}")
            data[key]=value
    if duplicates:
        raise SystemExit(f"Duplicate stock-day keys in {path}: {duplicates[:10]}")
    return data
krx=load(K/"krx_daily_turnover.csv",["date","code","turnover_won"])
nxt=load(N/"nxt_daily_turnover.csv",["date","code","nxt_turnover_won"])
coverage=list(csv.DictReader((N/"date_coverage.csv").open(encoding="utf-8-sig",newline="")))
if len(coverage)!=240 or {r["date"].replace("-","") for r in coverage}!=set(dates):
    raise SystemExit("NXT date coverage does not match KRX")
bad=[r for r in coverage if r["status"]!="OK" or int(r["parsed"])!=int(r["records"]) or (int(r["reported_total"]) and int(r["records"])!=int(r["reported_total"]))]
if bad:
    raise SystemExit(f"NXT coverage not fully verified: {bad[:5]}")
nxt_only=sorted(set(nxt)-set(krx))
by_day=defaultdict(list)
for day,code in nxt_only:
    by_day[day].append(code)
if nxt_only:
    print(f"WARNING: {len(nxt_only)} NXT-only stock-day rows, inspect before merging",flush=True)
with (OUT/"nxt_only_codes.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f);w.writerow(["date","code","nxt_turnover_won"])
    for day,code in nxt_only:
        w.writerow([day,code,nxt[day,code]])
with (OUT/"krx_nxt_daily_turnover.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f);w.writerow(["date","code","krx_turnover_won","nxt_turnover_won","combined_turnover_won","nxt_match"])
    for day,code in sorted(krx):
        k=krx[day,code]
        n=nxt.get((day,code))
        w.writerow([day,code,k,"" if n is None else n,"" if n is None else k+n,"YES" if n is not None else "NO"])
report=[
    "# KRX + NXT 240日历史成交额合并审计",
    f"KRX基准日期：{len(dates)}；KRX记录：{len(krx):,}；NXT记录：{len(nxt):,}",
    f"NXT与KRX可匹配：{len(nxt)-len(nxt_only):,}；NXT仅有：{len(nxt_only):,}",
    "重要：NXT缺失记录不能直接按零成交，因此未匹配股票的综合成交额保留为空。",
    "此数据尚不能直接用于正式模式2的20日综合均成交额筛选。",
    "NXT-only股票日示例："+", ".join(f"{d}:{c}" for d,c in nxt_only[:20]),
]
(OUT/"audit_report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
print("\n".join(report))
if nxt_only:
    sys.exit(1)
