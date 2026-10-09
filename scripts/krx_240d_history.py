#!/usr/bin/env python3
"""Build an independently audited 240-session KRX official turnover dataset.

Diagnostic only. Never modifies production stocks_data.js or history cache.
"""
import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "krx_240d_official"
OUT.mkdir(exist_ok=True)
KEY = os.environ.get("KRX_API_KEY", "").strip()
if not KEY:
    raise SystemExit("KRX_API_KEY missing")
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
END = (os.environ.get("KRX_HISTORY_END") or "").strip() or TODAY.isoformat()
END_DATE = datetime.strptime(END, "%Y-%m-%d").date()
if END_DATE > TODAY:
    raise SystemExit("End date is in the future")
START_DATE = END_DATE - timedelta(days=420)
DATES = [START_DATE + timedelta(days=i) for i in range((END_DATE - START_DATE).days + 1) if (START_DATE + timedelta(days=i)).weekday() < 5]
URL = "https://data-dbg.krx.co.kr/svc/apis/sto/"
MARKETS = {"KOSPI": ("stk_bydd_trd", 700), "KOSDAQ": ("ksq_bydd_trd", 1200)}

def number(value):
    return int(str(value).replace(",", "").strip())

def request_day(day):
    ds = day.strftime("%Y%m%d")
    results = {}
    for market, (endpoint, minimum) in MARKETS.items():
        last_error = None
        for attempt in range(4):
            try:
                response = requests.get(URL + endpoint, params={"basDd": ds}, headers={"AUTH_KEY": KEY, "User-Agent": "Mozilla/5.0"}, timeout=35)
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("OutBlock_1", [])
                if not isinstance(rows, list):
                    raise ValueError("OutBlock_1 not a list")
                if rows and len(rows) < minimum:
                    raise ValueError(f"{market} insufficient rows {len(rows)}")
                parsed = {}
                for row in rows:
                    code = str(row.get("ISU_CD", "")).strip()
                    if len(code) != 6 or not code.isalnum():
                        continue
                    if str(row.get("BAS_DD", ds)).strip() != ds:
                        raise ValueError(f"date mismatch: {code}")
                    volume = number(row["ACC_TRDVOL"])
                    turnover = number(row["ACC_TRDVAL"])
                    if volume < 0 or turnover < 0:
                        raise ValueError(f"negative trade value: {code}")
                    parsed[code] = {"volume": volume, "turnover_won": turnover}
                results[market] = parsed
                break
            except Exception as exc:
                last_error = str(exc)
                time.sleep(1.5 * (attempt + 1))
        else:
            return ds, None, f"{market}: {last_error}"
    if not any(results.values()):
        return ds, {}, None  # Weekend-filtered public holiday or unavailable date; audited below.
    if not all(results.values()):
        return ds, None, "One market empty while other market nonempty"
    return ds, results, None

days = {}
failures = {}
with ThreadPoolExecutor(max_workers=4) as executor:
    futures = [executor.submit(request_day, d) for d in DATES]
    for idx, future in enumerate(as_completed(futures), 1):
        ds, data, error = future.result()
        if error:
            failures[ds] = error
        elif data:
            days[ds] = data
        if idx % 25 == 0:
            print(f"Checked {idx}/{len(DATES)} weekdays, trading={len(days)}, errors={len(failures)}", flush=True)

sessions = sorted(days)[-240:]
if len(sessions) < 240:
    failures["coverage"] = f"Only {len(sessions)} valid trading sessions, expected 240"
data = {}
counts = {}
if len(sessions) == 240:
    for ds in sessions:
        merged = {}
        for market in MARKETS:
            for code, rec in days[ds][market].items():
                if code in merged:
                    failures[ds] = f"duplicate code across markets: {code}"
                merged[code] = {"market": market, **rec}
        data[ds] = merged
        counts[ds] = {market: len(days[ds][market]) for market in MARKETS}

with (OUT / "krx_daily_turnover.csv").open("w", encoding="utf-8-sig", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(["date", "code", "market", "volume_shares", "turnover_won", "source"])
    for ds in sessions:
        for code, record in sorted(data.get(ds, {}).items()):
            writer.writerow([ds, code, record["market"], record["volume"], record["turnover_won"], "KRX_OPEN_API"])

metadata = {
    "source": "KRX Open API stk_bydd_trd + ksq_bydd_trd",
    "scope": "KRX-only; NXT not included; do not use as consolidated turnover",
    "end_date_requested": END, "first_session": sessions[0] if sessions else None,
    "last_session": sessions[-1] if sessions else None,
    "session_count": len(sessions), "rows": sum(map(len, data.values())),
    "market_counts": counts, "errors": failures,
}
(OUT / "manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
status = "PASS" if len(sessions) == 240 and not failures else "FAIL"
report = [
    "# KRX官方240交易日历史成交额审计",
    f"**状态：{status}**",
    f"目标截止日期：{END}；实际区间：{metadata['first_session']} ～ {metadata['last_session']}",
    f"有效交易日：{len(sessions)}/240；股票日记录：{metadata['rows']:,}；请求异常：{len(failures)}",
    "口径：仅KRX（KOSPI＋KOSDAQ），不含NXT；不可直接替代模式2的KRX＋NXT综合20日均成交额。",
    "历史日期来自API实际返回的交易记录；不按工作日直接冒充交易日。",
    "",
    "## 异常（最多前20条）",
    *([f"- {k}: {v}" for k, v in sorted(failures.items())[:20]] or ["- 无"]),
]
(OUT / "audit_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
print("\n".join(report), flush=True)
if status != "PASS":
    sys.exit(1)
