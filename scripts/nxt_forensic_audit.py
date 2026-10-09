#!/usr/bin/env python3
"""Forensic NXT observation and turnover threshold audit. No missing->zero inference."""
import csv
from collections import defaultdict,Counter
from pathlib import Path
R=Path(__file__).resolve().parents[1]; O=R/"nxt_forensic_audit";O.mkdir(exist_ok=True)
H=defaultdict(dict);dates=set()
with (R/"merged_240d_audit"/"krx_nxt_daily_turnover.csv").open(encoding="utf-8-sig",newline="") as f:
    for r in csv.DictReader(f):
        d=r["date"].replace("-","");c=r["code"];dates.add(d)
        H[c][d]=(int(r["krx_turnover_won"]),int(r["nxt_turnover_won"]) if r["nxt_turnover_won"]!="" else None)
D=sorted(dates);W=D[-20:];assert len(D)==240 and len(W)==20
TARGET="488280";detail=[]
for d in D:
    if d in H[TARGET]:
        k,n=H[TARGET][d]
        detail.append([d,k,"" if n is None else n,"OBSERVED" if n is not None else "NOT_REPORTED"])
with (O/"488280_daily_history.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f);w.writerow(["date","krx_turnover_won","nxt_turnover_won","nxt_status"]);w.writerows(detail)
stats=Counter();out=[];THRESHOLD=50_000_000_000
for c,h in sorted(H.items()):
    kdays=sum(d in h for d in W)
    ndays=sum(d in h and h[d][1] is not None for d in W)
    if kdays<20:
        status="INSUFFICIENT_KRX_HISTORY";avgk="";avgall="";decision="UNKNOWN"
    else:
        avgk=sum(h[d][0] for d in W)//20
        if ndays==20:
            avgall=sum(h[d][0]+h[d][1] for d in W)//20
            status="VERIFIED_DUAL_20D"
            decision="PASS_VERIFIED" if avgall>=THRESHOLD else "FAIL_VERIFIED"
        else:
            avgall=""
            status="NXT_UNVERIFIED"
            decision="PASS_BY_KRX_LOWER_BOUND" if avgk>=THRESHOLD else "UNKNOWN"
    stats[decision]+=1
    out.append([c,kdays,ndays,status,avgk,avgall,decision])
with (O/"threshold_500eok_20d.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f);w.writerow(["code","krx_days","nxt_observed_days","coverage","krx_avg20_won","combined_avg20_won_if_verified","threshold_500eok_decision"]);w.writerows(out)
recent=[r for r in detail if r[0] in W]
changes=[]
for a,b in zip(recent,recent[1:]):
    if a[3]!=b[3]:changes.append(f"{a[0]}({a[3]}) -> {b[0]}({b[3]})")
report=["# NXT缺失取证与500亿成交额门槛审计",f"窗口：{W[0]}～{W[-1]}","## 488280最近20日观察状态",f"KRX记录：{len(recent)}；NXT返回：{sum(r[3]=='OBSERVED' for r in recent)}",f"状态切换：{'; '.join(changes) or '无'}","此处仅证明数据返回状态发生变化，不证明退市、暂停交易或NXT资格变更。","","## 500亿韩元20日平均成交额判定"]
for k,v in sorted(stats.items()):report.append(f"- {k}: {v}")
report+=["","判定逻辑：KRX与NXT成交额均非负，KRX 20日均成交额已达500亿则综合值必达标；反过来KRX未达标且NXT不完整不能判为不达标。","PASS_BY_KRX_LOWER_BOUND只是数学下界证明，不代表综合成交额数值已核实。","FAIL_VERIFIED只用于20日双市场记录均匹配的股票，仍需验证数据源市场覆盖口径。","历史不足20天的股票单独保留UNKNOWN。","本审计不改正式网页，也不把NXT缺失视作零。"]
(O/"audit_report.md").write_text("\n".join(report)+"\n",encoding="utf-8");print("\n".join(report))
