#!/usr/bin/env python3
"""Audit default website filter against 20-day turnover gate, without changing production."""
import csv,json,re,urllib.request
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/"mode2_filter_diagnostic";OUT.mkdir(exist_ok=True)
html=(ROOT/"index.html").read_text(encoding="utf-8")
def input_tag(key):
    m=re.search(r'<input\b[^>]*\bid=["\']'+re.escape(key)+r'["\'][^>]*>',html)
    if not m:raise SystemExit("Missing filter: "+key)
    return m.group(0)
def val(key):
    m=re.search(r'\bvalue=["\']([^"\']+)["\']',input_tag(key))
    if not m:raise SystemExit("Missing default: "+key)
    return float(m.group(1))
def checked(key):return bool(re.search(r'\bchecked\b',input_tag(key)))
settings={k:val(k) for k in ("cap","chgDays","wmin","wmax","turnDays","minturn20","distMin","distMax","streak")}
settings.update({k:checked(k) for k in ("capOn","chgOn","turnOn","distOn","streakOn")})
url="https://gyn19940404.github.io/korea-mode2-screener/stocks_data.js"
req=urllib.request.Request(url,headers={"User-Agent":"Mode2-filter-audit/1.0","Cache-Control":"no-cache"})
with urllib.request.urlopen(req,timeout=45) as resp:raw=resp.read().decode("utf-8-sig")
m=re.fullmatch(r'\s*window\.DATA_META=(\{[^\n]*\});\s*window\.STOCKS_DATA=(\[[^\n]*\]);\s*',raw,re.S)
if not m:raise SystemExit("Unexpected site payload")
meta=json.loads(m.group(1));stocks=json.loads(m.group(2))
bycode={str(s.get("code","")).zfill(6):s for s in stocks}
with (ROOT/"mode2_turnover_gate_test"/"mode2_turnover_gate.csv").open(encoding="utf-8-sig",newline="") as f:gate=list(csv.DictReader(f))
gate_dates={r["asof_date"] for r in gate}
if len(gate_dates)!=1:raise SystemExit("Mixed audit dates")
asof=gate_dates.pop()
# Default screen uses 1-day turnover. Other conditions are independently diagnostic;
# periodChange(s,5) needs exact JS logic and is deliberately NOT reimplemented.
rows=[];counts=Counter()
for r in gate:
    if r["gate"]!="PASS":continue
    c=r["code"];s=bycode.get(c)
    if s is None:raise SystemExit("PASS stock absent from deployed site: "+c)
    reasons=[]
    if s.get("isPreferred"):reasons.append("PREFERRED")
    cap=float(s.get("marketCapTrillion") or 0)*10000
    if settings["capOn"] and cap<settings["cap"]:reasons.append("MARKET_CAP")
    if settings["distOn"]:
        try:
            d=float(s.get("dist"))
            if not settings["distMin"]<=d<=settings["distMax"]:reasons.append("DIST_MA5")
        except (ValueError,TypeError):reasons.append("DIST_MISSING")
    if settings["turnOn"] and settings["turnDays"]==1:
        if float(s.get("turnover") or 0)<settings["minturn20"]:reasons.append("DAILY_TURNOVER")
    if settings["streakOn"] and float(s.get("streak") or 0)<settings["streak"]:reasons.append("STREAK")
    # Not a definitive pass because exact 5-day JS return calculations are not mirrored.
    status="OTHER_CHECKS_REQUIRED" if not reasons else "BLOCKED_BY_KNOWN_DEFAULT_CONDITION"
    counts[status]+=1
    for reason in reasons:counts["REASON_"+reason]+=1
    rows.append([asof,c,s.get("name",""),r["basis"],status,";".join(reasons),s.get("marketCapTrillion",""),s.get("turnover",""),s.get("dist","")])
with (OUT/"pass_115_default_filter_diagnostic.csv").open("w",encoding="utf-8-sig",newline="") as f:
    w=csv.writer(f);w.writerow(["audit_asof","code","name","gate_basis","partial_filter_status","known_blockers","site_market_cap_trillion","site_daily_turnover_100m_won","site_dist_ma5_percent"]);w.writerows(rows)
if len(rows)!=115:raise SystemExit("Expected 115 verified threshold passes")
report=["# 115只成交额达标股票与网页默认条件诊断",f"历史成交额审计日期：{asof}；网页数据日期：{meta.get('latest_date','UNKNOWN')}",f"网页默认参数：{json.dumps(settings,ensure_ascii=False)}","**关键：网页默认turnDays=1，即筛1日成交额；审计PASS指20日平均成交额达标。二者不同，不可直接互相替代。**","本报告只计算市值、距5日线、优先股、默认1日成交额等明确条件。","近N日涨幅使用网页JavaScript自定义算法，此处没有复刻，因此OTHER_CHECKS_REQUIRED不等于网页最终入选。"]
for k,v in sorted(counts.items()):report.append(f"- {k}: {v}")
report+=["","同一股票可能同时触发多项排除原因，原因数不应相加。","网页日期若与历史审计日期不同，禁止把结果视为同日筛选差异。","不修改正式网站、不下单。"]
(OUT/"audit_report.md").write_text("\n".join(report)+"\n",encoding="utf-8");print("\n".join(report))
