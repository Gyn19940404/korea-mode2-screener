#!/usr/bin/env python3
"""Build stock-by-stock 20-day official turnover evidence; diagnostic only."""
import csv
from pathlib import Path
from collections import defaultdict,Counter
R=Path(__file__).resolve().parents[1]
O=R/"turnover_evidence_20d";O.mkdir(exist_ok=True)
def read(path):
    with path.open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
data=read(R/"merged_240d_audit"/"krx_nxt_daily_turnover.csv")
prior=read(R/"nxt_forensic_audit"/"threshold_500eok_20d.csv")
dates=sorted({r["date"].replace("-","") for r in data})
if len(dates)!=240:raise SystemExit("Not 240 trading days")
window=dates[-20:];h=defaultdict(dict)
all_dates=defaultdict(set)
for r in data: all_dates[r["code"]].add(r["date"].replace("-",""))
for r in data:
    d=r["date"].replace("-","")
    if d not in window:continue
    c=r["code"]
    if d in h[c]:raise SystemExit("Duplicate stock/day: "+c+"/"+d)
    k=int(r["krx_turnover_won"]);n=int(r["nxt_turnover_won"]) if r["nxt_turnover_won"] else None
    if k<0 or (n is not None and n<0):raise SystemExit("Negative turnover")
    h[c][d]=(k,n)
p={r["code"]:r for r in prior}
if len(p)!=len(prior):raise SystemExit("Duplicate codes in threshold audit")
missing_in_daily=sorted(set(p)-set(h))
missing_in_gate=sorted(set(h)-set(p))
print(f"Universe diagnostic: gate={len(p)}, daily={len(h)}, missing_in_daily={len(missing_in_daily)}, missing_in_gate={len(missing_in_gate)}")
rows=[];daily=[];counts=Counter()
for c in sorted(set(p)|set(h)):
    history=h.get(c,{})
    km=[d for d in window if d not in history]
    nm=[d for d in window if d not in history or history[d][1] is None]
    kd=20-len(km);nd=20-len(nm)
    ka=sum(v[0] for v in history.values())//20 if kd==20 else None
    ca=sum(v[0]+v[1] for v in history.values())//20 if kd==20 and nd==20 else None
    if kd<20:basis="UNKNOWN";status="UNKNOWN"
    elif nd==20:
        basis="PASS_VERIFIED" if ca>=50_000_000_000 else "FAIL_VERIFIED"
        status="PASS" if ca>=50_000_000_000 else "FAIL"
    elif ka>=50_000_000_000:basis="PASS_BY_KRX_LOWER_BOUND";status="PASS"
    else:basis="UNKNOWN";status="UNKNOWN"
    old=p.get(c)
    if old is None:
        counts["MISSING_IN_GATE"]+=1
    elif old["threshold_500eok_decision"]!=basis or int(old["krx_days"])!=kd or int(old["nxt_observed_days"])!=nd:
        raise SystemExit("Audit mismatch: "+c)
    counts[basis]+=1
    rows.append([window[-1],c,status,basis,kd,nd,"" if ka is None else ka,"" if ca is None else ca,";".join(km),";".join(nm),"NOT_VERIFIED"])
    for d in window:
        v=history.get(d);k=v[0] if v else None;n=v[1] if v else None
        daily.append([c,d,"" if k is None else k,"" if n is None else n,"" if k is None or n is None else k+n])
def write(name,header,records):
    with (O/name).open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f);w.writerow(header);w.writerows(records)
write("stock_evidence.csv",["asof","code","gate","basis","krx_days","nxt_days","krx_avg20_won","verified_combined_avg20_won","missing_krx_dates","missing_nxt_dates","website_field_verified"],rows)
write("universe_discrepancy.csv",["code","issue","all_240d_observed_days","first_observed_date","last_observed_date","recent_20d_observed_days"],[[c,"MISSING_IN_RECENT_20D",len(all_dates[c]),min(all_dates[c]) if all_dates[c] else "",max(all_dates[c]) if all_dates[c] else "",len(h.get(c,{}))] for c in missing_in_daily]+[[c,"MISSING_IN_GATE",len(all_dates[c]),min(all_dates[c]) if all_dates[c] else "",max(all_dates[c]) if all_dates[c] else "",len(h.get(c,{}))] for c in missing_in_gate])
write("daily_evidence.csv",["code","date","krx_turnover_won","nxt_turnover_won","combined_if_verified_won"],daily)
report=["# 模式2逐股20日官方成交额证据审计（不发布）",f"窗口：{window[0]}～{window[-1]}；股票：{len(rows)}；逐日记录：{len(daily)}"]
report += [f"- {key}: {val}" for key,val in sorted(counts.items())]
report += [f"股票池差异：门槛池有、最近20日逐日池无={len(missing_in_daily)}；反向={len(missing_in_gate)}。这不等于全240日无数据。", "逐只首末交易日期见universe_discrepancy.csv；未验证股票仍保持UNKNOWN。"]
report += ["NXT缺失保持空值，不视作零。","网站字段尚未核对；本任务不修改网站，不发布，不触发交易。"]
(O/"audit_report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
print("\n".join(report))
print(f"Universe discrepancies: missing_in_daily={len(missing_in_daily)}, missing_in_gate={len(missing_in_gate)}")
if missing_in_gate:
    raise SystemExit("Unexpected codes in daily evidence: see universe_discrepancy.csv")
if missing_in_daily:
    print("NOTE: historic-only codes included with 0/20 KRX days and UNKNOWN status; not inferred as delisted or zero turnover")
