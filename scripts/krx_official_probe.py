#!/usr/bin/env python3
import os, json, re, requests
from pathlib import Path

VERSION = "NAVER FCHART REPLAY PROBE V2"
DATE = os.environ.get("TRADE_DATE", "20261007")
TARGETS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "009150": "삼성전기",
    "105560": "KB금융",
    "047040": "대우건설",
}
URL = "https://fchart.stock.naver.com/sise.nhn"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

print(VERSION)
print("TARGET_DATE:", DATE)
out = {}
ok = 0

for code, name in TARGETS.items():
    r = requests.get(URL, params={
        "symbol": code, "timeframe": "day", "count": "300", "requestType": "0"
    }, headers=HEADERS, timeout=20)
    print(code, name, "HTTP", r.status_code, "bytes", len(r.content))
    r.raise_for_status()
    rows = re.findall(r'data="(\\d{8})\\|([^"]+)"', r.text)
    dates = [d for d, _ in rows]
    latest = dates[-1] if dates else ""
    has_target = DATE in dates
    print("  latest=", latest, "target_found=", has_target, "rows=", len(rows))
    out[code] = {"name": name, "latest": latest, "target_found": has_target, "rows": len(rows)}
    ok += int(has_target)

Path(".data_cache").mkdir(exist_ok=True)
Path(".data_cache/naver_fchart_probe.json").write_text(
    json.dumps({"version": VERSION, "target_date": DATE, "found": ok, "targets": out},
               ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print("FOUND_TARGET_DATE:", ok, "/", len(TARGETS))
if ok != len(TARGETS):
    raise RuntimeError(f"目标日 {DATE} 未在全部样本出现: {ok}/{len(TARGETS)}")
print("PASS: 免费NAVER日线可用于目标交易日复盘")
