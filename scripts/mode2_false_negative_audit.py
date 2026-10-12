#!/usr/bin/env python3
"""Diagnostic: rank UNKNOWN stocks by turnover required to reach the 20-day gate."""
import csv
from pathlib import Path
from collections import defaultdict
root=Path(__file__).resolve().parents[1]
src=root/'turnover_evidence_20d'
out=root/'false_negative_audit_20d'
out.mkdir(exist_ok=True)
def read(name):
    with (src/name).open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))
stocks=read('stock_evidence.csv')
daily=defaultdict(list)
for r in read('daily_evidence.csv'):
    daily[r['code']].append(r)
if not stocks or len({r['code'] for r in stocks})!=len(stocks) or set(daily)!={r['code'] for r in stocks}:
    raise SystemExit('Stock universe mismatch')
rows=[]
for s in stocks:
    code=s['code']; ds=daily[code]
    if len(ds)!=20 or len({r['date'] for r in ds})!=20:
        raise SystemExit('Incomplete daily grid: '+code)
    krx=[int(r['krx_turnover_won']) for r in ds if r['krx_turnover_won']!='']
    nxt=[int(r['nxt_turnover_won']) for r in ds if r['nxt_turnover_won']!='']
    if len(krx)!=int(s['krx_days']) or len(nxt)!=int(s['nxt_days']) or min(krx+nxt+[0])<0:
        raise SystemExit('Invalid source values: '+code)
    missing=40-len(krx)-len(nxt)
    gap=max(0,50_000_000_000*20-sum(krx)-sum(nxt))
    gate=s['gate']
    if gate=='FAIL' and missing:raise SystemExit('Unverified FAIL: '+code)
    if gate=='UNKNOWN' and (not missing or not gap):raise SystemExit('Invalid UNKNOWN: '+code)
    if gate not in ('PASS','FAIL','UNKNOWN'):raise SystemExit('Unknown gate: '+code)
    rows.append([code,gate,s['basis'],20-len(krx),20-len(nxt),gap,(gap+missing-1)//missing if missing and gap else '', 'POTENTIAL_FALSE_NEGATIVE' if gate=='UNKNOWN' else 'NOT_POTENTIAL'])
rows.sort(key=lambda r:(r[7]!='POTENTIAL_FALSE_NEGATIVE',r[5],r[0]))
with (out/'false_negative_boundary.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.writer(f);w.writerow(['code','gate','basis','missing_krx_days','missing_nxt_days','gap_total_won','required_per_missing_market_day_won','classification']);w.writerows(rows)
potential=[r for r in rows if r[7]=='POTENTIAL_FALSE_NEGATIVE']
lines=['# 第二十二阶段：20日成交额潜在漏选审计（不发布）',f'股票总数：{len(rows)}；UNKNOWN潜在漏选：{len(potential)}', '缺失成交额不补零；潜在漏选不等于已经达标。', '差额为距离20日总门槛的已知缺口；缺失日均摊金额只是数学边界，不是预测。','## 缺口最小的20只']
lines += [f'- {r[0]}：20日缺口 {r[5]:,} 韩元；KRX缺{r[3]}日，NXT缺{r[4]}日' for r in potential[:20]]
(out/'audit_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('\n'.join(lines))
