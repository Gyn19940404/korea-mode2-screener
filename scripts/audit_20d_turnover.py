#!/usr/bin/env python3
"""Conservative 20-session coverage audit of KRX+NXT joined official history.

Does not equate missing NXT rows with zero. Does not publish trading signals.
"""
import csv
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"merged_240d_audit"/"krx_nxt_daily_turnover.csv"
OUT=ROOT/"turnover_20d_audit"
OUT.mkdir(exist_ok=True)
by_stock=defaultdict(dict)
days=set()
with SRC.open(encoding="utf-8-sig",newline="") as f:
    for r in csv.DictReader(f):
        day=r["date"].replace("-","")
        code=r["code"]
        days.add(day)
        by_stock[code][day]=(int(r["krx_turnover_won"]),None if not r["nxt_turnover_won"] else int(r["nxt_turnover_won"]))
dates=sorted(days)
if len(dates)!=240:
    raise SystemExit(f"Expected 240 dates, got {len(dates)}")
end=dates[-1]
window=dates[-20:]
rows=[]
complete=0
for code,history in sorted(by_stock.items()):
    krx_days=sum(d in history for d in window)
    nxt_days=sum(d in history and history[d][1] is not None for d in window)
    krx_sum=sum(history[d][0] for d in window if d in history)
    combined_sum=sum(history[d][0]+history[d][1] for d in window if d in history and history[d][1] is not None)
    eligible=krx_days==20 and nxt_days==20
    if eligible: complete+=1
    rows.append([code,krx_days,nxt_days,20-nxt_days,
                 str(krx_sum//20) if krx_days==20 else "",
                 str(combined_sum//20) if eligible else "",
                 "COMPLETE_MATCHED" if eligible else "NXT_COVERAGE_UNVERIFIED"])
with (OUT/"latest_20d_coverage.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f)
    w.writerow(["code","krx_days","nxt_matched_days","nxt_unmatched_days","krx_avg20_won","combined_avg20_won_only_if_all_matched","status"])
    w.writerows(rows)
report=[
    "# 官方KRX+NXT最近20交易日覆盖率审计",
    f"截止交易日：{end}；20日窗口：{window[0]}～{end}",
    f"历史股票代码：{len(rows)}；20天均有KRX与NXT匹配记录：{complete}",
    f"未满足全部20日双市场匹配：{len(rows)-complete}",
    "本报告严格区分：NXT无返回记录 ≠ 已确认NXT零成交。",
    "仅20日均存在双市场匹配记录时，才计算综合20日均成交额；其余留空。",
    "即使全部匹配，也需要核查市场口径、单位和正式网页映射，才能上线。",
    "该审计不改动正式选股器。"
]
(OUT/"audit_report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
print("\n".join(report))
