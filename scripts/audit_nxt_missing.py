#!/usr/bin/env python3
"""Classify missing NXT observations without assuming zero volume or turnover."""
import csv
from collections import defaultdict,Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"merged_240d_audit"/"krx_nxt_daily_turnover.csv"
OUT=ROOT/"nxt_missing_audit"
OUT.mkdir(exist_ok=True)
by_code=defaultdict(dict)
dates=set()
with SRC.open(encoding="utf-8-sig",newline="") as f:
    for r in csv.DictReader(f):
        d=r["date"].replace("-","")
        code=r["code"]
        dates.add(d)
        by_code[code][d]=(int(r["krx_turnover_won"]),None if not r["nxt_turnover_won"] else int(r["nxt_turnover_won"]))
window=sorted(dates)[-20:]
if len(window)!=20: raise SystemExit("Not enough sessions")
rows=[]
stats=Counter()
for code,h in sorted(by_code.items()):
    kdays=sum(d in h for d in window)
    ndays=sum(d in h and h[d][1] is not None for d in window)
    missing=[d for d in window if d in h and h[d][1] is None]
    if kdays<20: status="KRX_HISTORY_SHORT"
    elif ndays==20: status="DUAL_MARKET_COMPLETE"
    elif ndays==0: status="NO_NXT_OBSERVATIONS"
    else: status="INTERMITTENT_NXT_OBSERVATIONS"
    stats[status]+=1
    rows.append([code,status,kdays,ndays,len(missing),";".join(missing),sum(h[d][0] for d in window if d in h)//20 if kdays==20 else ""])
with (OUT/"nxt_missing_20d.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f);w.writerow(["code","status","krx_days","nxt_days","nxt_missing_days","missing_dates","krx_avg20_won"]);w.writerows(rows)
report=["# NXT最近20日缺失情况分类","本报告不把NXT缺失记录认定为零成交，也不修改正式模式2。",f"20日窗口：{window[0]}～{window[-1]}",f"历史股票代码：{len(rows)}",""]
for name,count in sorted(stats.items()): report.append(f"- {name}: {count}")
intermittent=[r for r in rows if r[1]=="INTERMITTENT_NXT_OBSERVATIONS"]
report += ["","## NXT间歇记录逐股核查"]
report += [f"- 股票 {r[0]}：KRX {r[2]}/20天，NXT {r[3]}/20天，缺失日期 {r[5] or '无'}" for r in intermittent] or ["- 无"]
report += ["","## 关键限制","仅凭NXT成交列表缺少股票，不能确认其当日NXT成交额为0。","要确认零成交，仍需独立的NXT可交易证券清单、交易状态或权威零成交定义。","本阶段不将未验证的综合20日均成交额推送到正式网页。"]
(OUT/"audit_report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
print("\n".join(report))
