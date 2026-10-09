#!/usr/bin/env python3
"""Exact independent Python mirror of the site's default passes(s), diagnostic only."""
import csv,json,re,urllib.request,math
from collections import Counter
from pathlib import Path
root=Path(__file__).resolve().parents[1];out=root/"mode2_exact_filter_audit";out.mkdir(exist_ok=True)
html=(root/"index.html").read_text(encoding="utf-8")
def tag(k):
 m=re.search(r'<input\b[^>]*\bid=["\']'+re.escape(k)+r'["\'][^>]*>',html)
 if not m:raise ValueError("missing input "+k)
 return m.group(0)
def number(k):
 m=re.search(r'\bvalue=["\']([^"\']+)["\']',tag(k))
 if not m:raise ValueError("missing value "+k)
 return float(m.group(1))
def enabled(k):return bool(re.search(r'\bchecked\b',tag(k)))
cfg={k:number(k) for k in ("cap","chgDays","wmin","wmax","turnDays","minturn20","distMin","distMax","streak")}
cfg.update({k:enabled(k) for k in ("capOn","chgOn","turnOn","distOn","streakOn")})
req=urllib.request.Request("https://gyn19940404.github.io/korea-mode2-screener/stocks_data.js",headers={"User-Agent":"Mode2-exact-audit/1.0","Cache-Control":"no-cache"})
with urllib.request.urlopen(req,timeout=45) as resp:raw=resp.read().decode("utf-8-sig")
m=re.fullmatch(r'\s*window\.DATA_META=(\{[^\n]*\});\s*window\.STOCKS_DATA=(\[[^\n]*\]);\s*',raw,re.S)
if not m:raise ValueError("unexpected site format")
meta=json.loads(m.group(1));stocks=json.loads(m.group(2));bycode={str(s.get("code","")).zfill(6):s for s in stocks}
with (root/"mode2_turnover_gate_test"/"mode2_turnover_gate.csv").open(encoding="utf-8-sig",newline="") as f:gate=list(csv.DictReader(f))
dates={r["asof_date"] for r in gate}
if len(dates)!=1:raise ValueError("mixed gate dates")
asof=dates.pop()
if meta.get("latest_date","").replace("-","")!=asof:raise ValueError("site/gate dates differ; refuse cross-date comparison")
def finite(x):
 try:
  n=float(x);return n if math.isfinite(n) else math.nan
 except (ValueError,TypeError):return math.nan
def nclamp(v,lo,hi):
 return max(lo,min(hi,int(v)))
def pct(s,days):
 h=s.get("history") or []
 if len(h)<=days:return math.nan
 now=finite(h[-1][4]);base=finite(h[-1-days][4])
 return (now/base-1)*100 if now>0 and base>0 else math.nan
def avg_turn(s,days):
 h=s.get("history") or []
 if len(h)<days:return math.nan
 values=[]
 for x in h[-days:]:
  if len(x)>6 and x[6] is not None:values.append(finite(x[6])/1e8)
  else:values.append(finite(x[4])*finite(x[5])/1e8)
 return sum(values)/days if all(math.isfinite(v) for v in values) else math.nan
def check(s):
 why=[]
 if s.get("isPreferred"):why.append("PREFERRED")
 if cfg["capOn"] and finite(s.get("marketCapTrillion"))*10000<cfg["cap"]:why.append("MARKET_CAP")
 chg=pct(s,nclamp(cfg["chgDays"],1,239))
 if cfg["chgOn"] and (not math.isfinite(chg) or chg<cfg["wmin"] or chg>cfg["wmax"]):why.append("CHANGE_5D")
 days=nclamp(cfg["turnDays"],1,240)
 turnover=finite(s.get("turnover")) if days==1 else avg_turn(s,days)
 if cfg["turnOn"] and (not math.isfinite(turnover) or turnover<cfg["minturn20"]):why.append("TURNOVER")
 lo,hi=sorted((cfg["distMin"],cfg["distMax"]))
 d=finite(s.get("dist"))
 if cfg["distOn"] and (d<lo or d>hi):why.append("DIST_MA5")
 if cfg["streakOn"] and finite(s.get("streak"))<cfg["streak"]:why.append("STREAK")
 return why,chg,turnover
rows=[];counts=Counter();all_pass=0
for s in stocks:
 why,_,_=check(s)
 if not why:all_pass+=1
for r in gate:
 if r["gate"]!="PASS":continue
 s=bycode.get(r["code"])
 if s is None:raise ValueError("audited PASS absent "+r["code"])
 why,chg,turn=check(s)
 status="DEFAULT_PASS" if not why else "DEFAULT_BLOCKED"
 counts[status]+=1
 for reason in why:counts["REASON_"+reason]+=1
 rows.append([asof,r["code"],s.get("name",""),status,";".join(why),round(chg,4) if math.isfinite(chg) else "",round(turn,4) if math.isfinite(turn) else "",s.get("dist","")])
if len(rows)!=115:raise ValueError("expected 115 verified PASS records")
with (out/"verified_115_exact_default.csv").open("w",newline="",encoding="utf-8-sig") as f:
 w=csv.writer(f);w.writerow(["asof","code","name","default_status","all_blockers","period_change_percent","selected_turnover_100m","dist_ma5_percent"]);w.writerows(rows)
report=["# 模式2第十阶段：115只成交额达标股票与网页完整默认条件独立复核",f"审计日期：{asof}；网站日期：{meta.get('latest_date')}",f"网站股票池：{len(stocks)}；全网站默认筛选预计通过：{all_pass}",f"115只中完整默认筛选通过：{counts['DEFAULT_PASS']}；排除：{counts['DEFAULT_BLOCKED']}",f"参数：{json.dumps(cfg,ensure_ascii=False)}","本脚本按当前网页periodChange、selectedMonthTurn和passes的逻辑独立计算；不是浏览器执行测试，未来网页逻辑变动须重新核对。"]
for k,v in sorted(counts.items()):
 if k.startswith("REASON_"):report.append(f"- {k}: {v}")
report+=["","同一股票可触发多个原因。PASS是20日历史成交额审计达标；网页默认筛选仍采用1日成交额，不能混为一谈。","本任务不发布、不交易、不修改网站。"]
(out/"audit_report.md").write_text("\n".join(report)+"\n",encoding="utf-8");print("\n".join(report))
