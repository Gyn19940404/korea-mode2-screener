# -*- coding: utf-8 -*-
"""
scripts/krx_status.py —— 一键体检：KRX OpenAPI 到底通不通

只读、不写仓库、不发布网页。输出一张 Markdown 表到 stdout，
由 workflow 追加到 Step Summary。

为什么需要
----------
日更唯一卡住的地方是 fetch_krx_official_daily 抛出：

    RuntimeError: KRX official KOSPI 20261015 incomplete: 0; HTTP=200

注意 HTTP=200 —— 认证是通的，只是 OutBlock_1 是空的。而 KRX 对
「key 无效 / 服务未授权」的情况会返回 401，对「该日还没有数据」返回
200+空。所以两种原因可以区分，本脚本就是用这个区别来定位的：

  第一节：逐服务探一次 —— 有 401 的就是没授权
  第二节：逐日往回扫   —— 有数据的最近一天 = KRX 实际可用的最新交易日，
                          它到今天的间隔就是发布滞后

输出结论会自动判断是「授权问题」还是「发布滞后」，以及滞后几个交易日。
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

import requests

BASE = "https://data-dbg.krx.co.kr/svc/apis"
UA = "krx-status/2.0"

# (category, endpoint, 韩文名, 说明, 最少行数, 是否必需)
SERVICES = [
    ("sto", "stk_bydd_trd",      "유가증권 일별매매정보",  "KOSPI 全市场日行情",   700,  True),
    ("sto", "ksq_bydd_trd",      "코스닥 일별매매정보",    "KOSDAQ 全市场日行情",  1200, True),
    ("idx", "kospi_dd_trd",      "KOSPI 시리즈 일별시세",  "基准指数",             1,    True),
    ("idx", "kosdaq_dd_trd",     "KOSDAQ 시리즈 일별시세", "基准指数",             1,    True),
    ("sto", "stk_isu_base_info", "유가증권 종목기본정보",  "上市股数/面值",         700,  False),
    ("sto", "ksq_isu_base_info", "코스닥 종목기본정보",    "上市股数/面值",         1200, False),
]

KST = timedelta(hours=9)


def kst_now() -> datetime:
    return datetime.utcnow() + KST


def recent_weekdays(n: int) -> list:
    """最近 n 个工作日（不含今天，从昨天开始往回），返回 ['YYYYMMDD', ...]。"""
    d = kst_now().date() - timedelta(days=1)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return out


def probe(session, key, category, endpoint, bas_dd):
    """返回 (http状态字符串, 说明, 行数)"""
    url = f"{BASE}/{category}/{endpoint}"
    try:
        r = session.get(url, params={"AUTH_KEY": key, "basDd": bas_dd}, timeout=40)
    except Exception as exc:                                    # noqa: BLE001
        return "ERR", f"网络错误 {type(exc).__name__}", -1

    if r.status_code in (401, 403):
        return "401", "认证失败（key 无效 / 服务未批准 / 已过期）", -1
    if r.status_code == 429:
        return "429", "被限流", -1
    if r.status_code != 200:
        return str(r.status_code), "HTTP 错误", -1

    try:
        payload = r.json()
    except Exception:                                           # noqa: BLE001
        return "200", "响应不是 JSON", -1

    if isinstance(payload, dict) and "respMsg" in payload and "OutBlock_1" not in payload:
        return "200", f'{payload.get("respCode")} {payload.get("respMsg")}', -1

    rows = payload.get("OutBlock_1", []) if isinstance(payload, dict) else []
    n = len(rows) if isinstance(rows, list) else -1
    return "200", ("有空数据" if n == 0 else "有数据"), n


def main():
    key = os.environ.get("KRX_API_KEY", "").strip()
    if not key:
        print("## ❌ KRX_API_KEY 未注入")
        print("\nworkflow 缺少 `env: KRX_API_KEY: ${{ secrets.KRX_API_KEY }}`，"
              "或仓库 Secrets 里没有这个名称。")
        return 1

    days = int(os.environ.get("SWEEP_DAYS", "8") or 8)
    weekdays = recent_weekdays(days)
    latest = weekdays[0]

    print("## KRX OpenAPI 体检")
    print(f"\n密钥已注入（长度 {len(key)}，不显示内容）　"
          f"KST 现在 {kst_now():%Y-%m-%d %H:%M}　"
          f"扫描最近 {days} 个工作日\n")

    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    # ---------------- 第一节：服务授权 ----------------
    print("### 1) 服务授权（基准日 " + latest + "）\n")
    print("| 服务 | 接口 | HTTP | 说明 | 行数 | 结论 |")
    print("|---|---|---|---|---|---|")
    auth_fail = []
    for cat, ep, kor, note, minimum, required in SERVICES:
        st, msg, n = probe(s, key, cat, ep, latest)
        ok = (n >= minimum) or (n == 0 and st == "200")
        verdict = "✅" if n >= minimum else ("🟡" if st == "200" else "❌")
        if st in ("401", "403") and required:
            auth_fail.append(kor)
        print(f"| {kor} | `{cat}/{ep}` | {st} | {msg} | {n} | {verdict} |")

    # ---------------- 第二节：逐日覆盖 ----------------
    print(f"\n### 2) 逐日覆盖（KOSPI `sto/stk_bydd_trd`）\n")
    print("| 日期 | 星期 | HTTP | 行数 | 结论 |")
    print("|---|---|---|---|---|")
    kospi_cov = {}
    wd_kr = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for d in weekdays:
        st, msg, n = probe(s, key, "sto", "stk_bydd_trd", d)
        kospi_cov[d] = n
        dt = datetime.strptime(d, "%Y%m%d").date()
        v = "✅" if n > 0 else ("🟡 空" if st == "200" else "❌")
        print(f"| {d} | {wd_kr[dt.weekday()]} | {st} | {n} | {v} |")

    # ---------------- 结论 ----------------
    have = [d for d in weekdays if kospi_cov.get(d, -1) > 0]
    print("\n### 3) 结论\n")

    if auth_fail:
        print(f"- ❌ **有必需服务返回 401**：{', '.join(auth_fail)}")
        print("  → 去 openapi.krx.co.kr 的 `마이페이지 → 이용현황` 看该服务是否「승인」，")
        print("    再看 `API 인증키 발급내역` 里 key 是否「발급완료」、使用期限是否未过期。")
    elif not have:
        print(f"- ❌ **最近 {days} 个工作日全部 0 行**（HTTP 200 但 OutBlock_1 为空）。")
        print("  → 不是发布滞后能解释的。两种可能：")
        print("    1) 该 key 对 `sto/stk_bydd_trd` 实际未被授权（有些情况会返回 200+空而不是 401）")
        print("    2) 接口/域名有变化。可用「服务 목록」页的 샘플 按钮直接对比返回体")
    else:
        newest = have[0]
        lag = 0
        cur = kst_now().date() - timedelta(days=1)
        while cur.strftime("%Y%m%d") != newest:
            if cur.weekday() < 5:
                lag += 1
            cur -= timedelta(days=1)
        print(f"- ✅ **KRX 实际可用的最新交易日 = {newest}**（工作日回退 {lag} 天）")
        if lag == 0:
            print("  → 与最近工作日一致，日更请求当天数据是可行的。")
        else:
            print(f"  → 日更脚本请求的是「最近一个工作日」({latest})，但 KRX 那时还没有数据，")
            print(f"    所以抛 `incomplete: 0`。**发布滞后约 {lag} 个工作日**。")

    print("\n<sub>说明：`HTTP 200 + 0 行` 与 `HTTP 401` 是两种不同的失败，"
          "前者是「这天还没有数据」，后者是「这个 key 不能用」。</sub>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
