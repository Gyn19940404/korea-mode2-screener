#!/usr/bin/env python3
"""Validate generated webpage data and print a compact six-stock KRX/NXT audit."""
import json,re,sys
from pathlib import Path
raw=Path("stocks_data.js").read_text(encoding="utf-8")
meta_match=re.search(r'window\.DATA_META=(\{.*?\});',raw,re.S)
data_match=re.search(r'window\.STOCKS_DATA=(\[.*\]);\s*$',raw,re.S)
if not meta_match or not data_match:
    sys.exit("ERROR: missing DATA_META or STOCKS_DATA")
meta=json.loads(meta_match.group(1))
stocks=json.loads(data_match.group(1))
day=str(meta.get("latest_date",""))
baseline={"005930":30352099,"000660":4373490,"034020":7732776,"009150":897747,"373220":1379171,"005380":1101179}
by_code={str(x.get("code","")).zfill(6):x for x in stocks}
lines=[f"# 模式2网页成交量/成交额校验",f"交易日期: {day} | 生成时间: {meta.get('updated_at')} | 股票数量: {len(stocks)}","", "| 股票 | KRX量 | NXT量 | 网站量 | 内部差额 | 网站成交额(亿韩元) | Toss差额(仅10/08) |","|---|---:|---:|---:|---:|---:|---:|"]
errors=[]
for code in baseline:
    s=by_code.get(code)
    if not s:
        errors.append(f"missing stock {code}");continue
    krx=int(s.get("naverVolume") or 0)
    nxt=int(s.get("nxtVolume") or 0)
    total=int(s.get("volume") or 0)
    delta=total-krx-nxt
    turnover=int(s.get("combinedTurnoverWon") or 0)
    toss=total-baseline[code] if day=="2026-10-08" else None
    lines.append(f"| {s.get('name')} ({code}) | {krx:,} | {nxt:,} | {total:,} | {delta:+,} | {turnover/1e8:,.1f} | {f'{toss:+,}' if toss is not None else '不适用'} |")
    if delta: errors.append(f"{code}: volume {total} != KRX {krx} + NXT {nxt}")
    if turnover != int(s.get("krxTurnoverWon") or 0)+int(s.get("nxtTurnoverWon") or 0):
        errors.append(f"{code}: turnover mismatch")
lines += ["", "注意：Toss对照仅适用于用户提供的2026-10-08截图；微小差额不代表精确一致。"]
report="\n".join(lines)+"\n"
Path("data_audit_report.md").write_text(report,encoding="utf-8")
print(report)
if errors: sys.exit("DATA AUDIT FAILED: "+"; ".join(errors))
print("DATA AUDIT PASSED: six-stock internal KRX+NXT totals consistent")
