# -*- coding: utf-8 -*-
"""
scripts/krx_official.py —— KRX OpenAPI 官方调用的唯一入口（修正版）

为什么需要它
------------
仓库里 scripts/update_data.py、krx_240d_history.py、krx_naver_compare.py
三处都这样发请求：

    requests.get(url, params={'basDd': day}, headers={'AUTH_KEY': key})

把密钥放在 **HTTP header** 里，而 query string 上只有 basDd。
KRX 官方文档与官方 Python 封装（pykrx-openapi 的 client.py）用的都是
**query 参数**：

    params={'AUTH_KEY': key, 'basDd': day}

本模块统一按 query 参数发（同时保留 header 兼容写法，两边都带上），
并把「失败原因」写清楚 —— 因为 KRX 对所有未授权情形都返回同一个
401 Unauthorized Key，不区分「key 无效」和「该服务没申请」。

用法
----
    from krx_official import krx_daily, KrxAuthError, KrxDataNotReady

    rows = krx_daily(key, 'stk_bydd_trd', '20261014')
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

BASE = "https://data-dbg.krx.co.kr/svc/apis"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MARKETS = (("KOSPI", "sto", "stk_bydd_trd"), ("KOSDAQ", "sto", "ksq_bydd_trd"))


# 认证失败：key 无效 / 已过期 / 该服务未申请（KRX 三者返回同一个 401）
class KrxAuthError(RuntimeError):
    pass


# 当日官方数据尚未发布：HTTP 200 但 OutBlock_1 为空或条数不足
class KrxDataNotReady(RuntimeError):
    pass


def krx_daily(api_key: str, endpoint: str, bas_dd: str, *,
              category: str = "sto", tries: int = 4, timeout: int = 40,
              diagnostic_path=None,
              diagnostic_extra: dict | None = None) -> list:
    """
    拉某个市场某一天的官方全量行情，返回 OutBlock_1 记录列表。

    - bas_dd: 'YYYYMMDD'
    - 4xx 立即失败（重试无意义）；5xx / 超时 / 429 指数退避重试
    - 任何失败都会把诊断写到 diagnostic_path（含 HTTP 状态与响应体前 1800 字，
      密钥已脱敏），这样下一次失败能直接看出原因，不用再猜
    """
    url = f"{BASE}/{category}/{endpoint}"
    last_err = None

    for attempt in range(1, tries + 1):
        try:
            # ★ 关键修正：AUTH_KEY 走 query 参数（官方文档与官方封装的做法）。
            #   同时保留 header，两种写法都带，避免服务端实现差异。
            resp = requests.get(
                url,
                params={"AUTH_KEY": api_key, "basDd": bas_dd},
                headers={"AUTH_KEY": api_key, "User-Agent": UA,
                         "Accept": "application/json"},
                timeout=timeout,
            )
        except Exception as exc:                                  # noqa: BLE001
            last_err = f'{type(exc).__name__}: {exc}'
            if attempt < tries:
                time.sleep(2 ** attempt)                          # 2s, 4s, 8s
                continue
            _write_diag(diagnostic_path, endpoint, bas_dd, None, last_err,
                        diagnostic_extra)
            raise RuntimeError(f'KRX 网络错误 {endpoint} {bas_dd}: {last_err}') from exc

        # ---- 认证类：立即失败，不做无意义重试 ----
        if resp.status_code in (401, 403):
            _write_diag(diagnostic_path, endpoint, bas_dd, resp, resp.text,
                        diagnostic_extra)
            raise KrxAuthError(
                f'KRX 认证失败 {endpoint} {bas_dd}: HTTP {resp.status_code} {resp.text[:160]}\n'
                '  三种原因无法从响应区分，请到 openapi.krx.co.kr 核对：\n'
                '    · API 인증키 발급내역 里 key 是否为「발급완료」\n'
                '    · 마이페이지 → 이용현황 里该服务是否已「승인」\n'
                '    · 该服务的使用期限是否已过期（到期需点「갱신」）'
            )

        # ---- 限流 / 服务端错误：重试 ----
        if resp.status_code == 429 or resp.status_code >= 500:
            last_err = f'HTTP {resp.status_code}'
            if attempt < tries:
                time.sleep(2 ** attempt)
                continue
            _write_diag(diagnostic_path, endpoint, bas_dd, resp, resp.text,
                        diagnostic_extra)
            raise RuntimeError(f'KRX {endpoint} {bas_dd} 反复失败：{last_err}')

        if resp.status_code != 200:
            _write_diag(diagnostic_path, endpoint, bas_dd, resp, resp.text,
                        diagnostic_extra)
            raise RuntimeError(
                f'KRX {endpoint} {bas_dd}: HTTP {resp.status_code} {resp.text[:160]}')

        # ---- 200：解析 ----
        try:
            payload = resp.json()
        except Exception:                                          # noqa: BLE001
            _write_diag(diagnostic_path, endpoint, bas_dd, resp, resp.text,
                        diagnostic_extra)
            raise RuntimeError(f'KRX {endpoint} {bas_dd}: 响应不是 JSON：{resp.text[:160]}')

        if isinstance(payload, dict) and 'respMsg' in payload and 'OutBlock_1' not in payload:
            _write_diag(diagnostic_path, endpoint, bas_dd, resp,
                        json.dumps(payload)[:1800], diagnostic_extra)
            raise RuntimeError(
                f'KRX {endpoint} {bas_dd}: {payload.get("respCode")} {payload.get("respMsg")}')

        rows = payload.get('OutBlock_1', []) if isinstance(payload, dict) else []
        return rows if isinstance(rows, list) else []

    raise RuntimeError(f'KRX {endpoint} {bas_dd}: 未预期的重试终点')   # pragma: no cover


def _write_diag(path, endpoint, bas_dd, resp, body, extra):
    """把失败现场落盘（密钥不写入）。"""
    if not path:
        return
    diag = {
        'endpoint': endpoint,
        'date': bas_dd,
        'http_status': getattr(resp, 'status_code', None),
        'attempted_url': getattr(resp, 'url', None),
        'response_preview': (body or '')[:1800],
    }
    if extra:
        diag.update(extra)
    try:
        Path(path).write_text(json.dumps(diag, ensure_ascii=False, indent=2),
                              encoding='utf-8')
        print(f'[KRX诊断] 已写出 {path}: {json.dumps(diag, ensure_ascii=False)[:300]}')
    except Exception as exc:                                       # noqa: BLE001
        print(f'[KRX诊断] 写文件失败: {exc}')
