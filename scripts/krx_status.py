# -*- coding: utf-8 -*-
"""
scripts/krx_status.py —— 一键体检：KRX OpenAPI 到底通不通

只读、不写仓库、不发布网页。把每个服务的真实 HTTP 状态和行数打成一张
Markdown 表，输出到 stdout（由 workflow 追加到 Step Summary）。

为什么需要：update_data.py 每天失败但看不出原因。KRX 对所有未授权
情形返回同一个 401，无法从报错区分「key 无效」「服务没批」「数据没发布」。
这个脚本把三件事分开报：HTTP 状态 / 响应体 / 行数。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import requests

BASE = "https://data-dbg.krx.co.kr/svc/apis"
UA = "krx-status/1.0"

# (category, endpoint, 韩文名, 说明, 最少行数, 是否必需)
SERVICES = [
    ("sto", "stk_bydd_trd",      "유가증권 일별매매정보",  "KOSPI 全市场日行情",   700,  True),
    ("sto", "ksq_bydd_trd",      "코스닥 일별매매정보",    "KOSDAQ 全市场日行情",  1200, True),
    ("idx", "kospi_dd_trd",      "KOSPI 시리즈 일별시세",  "基准指数",             1,    True),
    ("idx", "kosdaq_dd_trd",     "KOSDAQ 시리즈 일별시세", "基准指数",             1,    True),
    ("sto", "stk_isu_base_info", "유가증권 종목기본정보",  "上市股数/面值",         700,  False),
    ("sto", "ksq_isu_base_info", "코스닥 종목기본정보",    "上市股数/面值",         1200, False),
]


def probe(session, key, category, endpoint, bas_dd, minimum):
    url = f"{BASE}/{category}/{endpoint}"
    try:
        r = session.get(url, params={"AUTH_KEY": key, "basDd": bas_dd},
                        timeout=40)
    except Exception as exc:                                    # noqa: BLE001
        return "ERR", f"网络错误 {type(exc).__name__}", "", 0, False

    if r.status_code in (401, 403):
        # 认证失败：key 无效 / 该服务未申请 / 授权过期，三者响应相同
        return "401", "认证失败（key 无效 / 服务未批准 / 已过期）", r.text[:90], 0, False

    if r.status_code == 429:
        return "429", "被限流", r.text[:90], 0, False
    if r.status_code != 200:
        return str(r.status_code), "HTTP 错误", r.text[:90], 0, False

    try:
        payload = r.json()
    except Exception:                                           # noqa: BLE001
        return "200", "响应不是 JSON", r.text[:90], 0, False

    if isinstance(payload, dict) and "respMsg" in payload and "OutBlock_1" not in payload:
        return "200", f'{payload.get("respCode")} {payload.get("respMsg")}', "", 0, False

    rows = payload.get("OutBlock_1", []) if isinstance(payload, dict) else []
    n = len(rows) if isinstance(rows, list) else -1
    if n == 0:
        return "200", "非交易日 / 数据未发布", "", 0, True
    ok = n >= minimum
    return "200", "正常" if ok else f"行数不足（要求≥{minimum}）", "", n, ok


def recent_weekday() -> str:
    """最近一个已完成的交易日（走 KST，数据为 T+1 发布）。"""
    now = datetime.utcnow() + timedelta(hours=9)      # KST
    d = now.date() if now.hour >= 19 else now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def main():
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        print("## ❌ KRX_API_KEY 未注入")
        print("\n说明 workflow 里缺少 `env: KRX_API_KEY: ${{ secrets.KRX_API_KEY }}`，"
              "或仓库 Secrets 里没有这个名称。")
        return 1

    bas_dd = os.environ.get("TRADE_DATE", "").strip() or recent_weekday()
    print(f"## KRX OpenAPI 体检 — 基准日 {bas_dd}")
    print(f"\n（密钥已注入，长度 {len(key)}；本表只报状态，不显示密钥）\n")
    print("| 服务 | 接口 | HTTP | 说明 | 行数 | 结论 |")
    print("|---|---|---|---|---|---|")

    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    worst = "OK"
    details = []
    for cat, ep, kor, note, minimum, required in SERVICES:
        status, msg, body, n, ok = probe(s, key, cat, ep, bas_dd, minimum)
        verdict = "✅" if ok else ("🟡" if status == "200" and n == 0 else "❌")
        if not ok and required:
            worst = "FAIL"
        print(f"| {kor} | `{cat}/{ep}` | {status} | {msg} | {n} | {verdict} |")
        if body:
            details.append(f"- `{ep}` → `{body}`")

    # 明细放在表格之后，否则会打断 Markdown 表格渲染
    if details:
        print("\n**响应明细**\n")
        print("\n".join(details))

    print()
    if worst == "OK":
        print("**结论：必需服务全部可用** → 可以跑 `python scripts/update_data.py`")
    else:
        print("**结论：有必需服务不可用** —— 按上表的 HTTP 列对照处理：")
        print("- `401` → 去 openapi.krx.co.kr 的 `마이페이지 → 이용현황` 看该服务是否为「승인」；")
        print("  再看 `API 인증키 발급내역` 里 key 是否「발급완료」、使用期限是否未过期")
        print("- `200` 但行数 0 → 那天是休市日，或官方数据尚未发布（T+1），换个交易日再试")
    return 0 if worst == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
