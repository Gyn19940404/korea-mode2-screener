#!/usr/bin/env python3
"""Independent comparison: audited turnover gate vs public site stock universe. Not a trade signal."""
import csv,json,re,urllib.request
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"mode2_site_diff";OUT.mkdir(exist_ok=True)
URL="https://gyn19940404.github.io/korea-mode2-screener/stocks_data.js"
req=urllib.request.Request(URL,headers={"User-Agent":"Mode2-audit/1.0","Cache-Control":"no-cache"})
with urllib.request.urlopen(req,timeout=45) as response:
    raw=response.read().decode("utf-8-sig")
# Site generator emits two JSON assignments. Do not execute remote JavaScript.
m=re.fullmatch(r'\s*window\.DATA_META=(\{[^\n]*\});\s*window\.STOCKS_DATA=(\[[^\n]*\]);\s*',raw,re.S)
if not m:raise SystemExit("Website payload format unexpected; refuse unsafe JS execution")
meta=json.loads(m.group(1));stocks=json.loads(m.group(2))
if not isinstance(stocks,list) or len(stocks)<1000:raise SystemExit("Unexpected site universe size")
bycode={}
for s in stocks:
    c=str(s.get("code","")).zfill(6)
    if c in bycode:raise SystemExit("Duplicate site code: "+c)
    bycode[c]=s
src=ROOT/"mode2_turnover_gate_test"/"mode2_turnover_gate.csv"
with src.open(encoding="utf-8-sig",newline="") as f:gate=list(csv.DictReader(f))
dates={r["asof_date"] for r in gate}
if len(dates)!=1:raise SystemExit("Mixed gate dates")
asof=dates.pop()
out=[];counts=Counter()
for r in gate:
    c=r["code"];s=bycode.get(c);status="SITE_PRESENT" if s else "SITE_ABSENT"
    counts[(r["gate"],status)]+=1
    out.append([asof,c,r["gate"],r["basis"],status,s.get("name","") if s else "",s.get("sector","") if s else "",s.get("marketCapTrillion","") if s else "",s.get("turnover","") if s else ""])
with (OUT/"site_gate_comparison.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f);w.writerow(["gate_asof","code","gate","gate_basis","site_universe_status","site_name","site_sector","site_market_cap_trillion","site_daily_turnover_100m_won"]);w.writerows(out)
site_dates={k:v for k,v in meta.items() if "date" in k.lower() or "time" in k.lower() or "snapshot" in k.lower()}
report=["# 模式2成交额审计与正式网站股票池独立对照",f"审计日期：{asof}",f"网站股票池数量：{len(bycode)}",f"网站时间字段：{json.dumps(site_dates,ensure_ascii=False)}","**重要：网页的当日成交额不能与20日平均成交额直接比较。这里只比较股票代码覆盖，不判定网站成交额正确与否。**"]
for (gate_status,site_status),n in sorted(counts.items()):report.append(f"- {gate_status} / {site_status}: {n}")
report+=["","该报告只核对网站股票池覆盖情况，不是最终模式2候选池差异。网站市值与板块仅供诊断，不自动修改网站。","不同日期的快照禁止当成同一天进行数值对照。"]
(OUT/"audit_report.md").write_text("\n".join(report)+"\n",encoding="utf-8")
print("\n".join(report))
