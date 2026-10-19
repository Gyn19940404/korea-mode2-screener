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
    from krx_official import krx_daily, krx_index_daily, KrxAuthError, KrxDataNotReady

    rows = krx_daily(key, 'stk_bydd_trd', '20261014')
    rows, status = krx_index_daily(key, 'KOSPI', '20261014')
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

BASE = "https://data-dbg.krx.co.kr/svc/apis"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MARKETS = (("KOSPI", "sto", "stk_bydd_trd"), ("KOSDAQ", "sto", "ksq_bydd_trd"))

# 指数服务的端点名：(category, endpoint) —— category 是 URL 里的服务分组，
# endpoint 是具体服务名。三个指数都走 idx 分组下的 *_dd_trd 服务。
INDEX_ENDPOINTS = {
    "KOSPI":  (("idx", "kospi_dd_trd"),),
    "KOSDAQ": (("idx", "kosdaq_dd_trd"),),
    "KRX":    (("idx", "krx_dd_trd"),),
}

# 指数响应的有效数据行必须带收盘指数（CLSPRC_IDX）。
# KRX 的 idx 响应首行常常是汇总/分组行，CLSPRC_IDX 为空，不能当作指数行情。
INDEX_VALUE_FIELD = "CLSPRC_IDX"
INDEX_NAME_FIELD = "IDX_NM"


# 认证失败：key 无效 / 已过期 / 该服务未申请（KRX 三者返回同一个 401）
class KrxAuthError(RuntimeError):
    pass


# 当日官方数据尚未发布：HTTP 200 但 OutBlock_1 为空或条数不足
class KrxDataNotReady(RuntimeError):
    pass


# HTTP 层失败：带上真实状态码，方便上层诊断记录（而不是记成 null）
class KrxHttpError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


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
            raise KrxHttpError(f'KRX {endpoint} {bas_dd} 反复失败：{last_err}',
                               resp.status_code)

        if resp.status_code != 200:
            _write_diag(diagnostic_path, endpoint, bas_dd, resp, resp.text,
                        diagnostic_extra)
            raise KrxHttpError(
                f'KRX {endpoint} {bas_dd}: HTTP {resp.status_code} {resp.text[:160]}',
                resp.status_code)

        # ---- 200：解析 ----
        try:
            payload = resp.json()
        except Exception:                                          # noqa: BLE001
            _write_diag(diagnostic_path, endpoint, bas_dd, resp, resp.text,
                        diagnostic_extra)
            raise KrxHttpError(
                f'KRX {endpoint} {bas_dd}: 响应不是 JSON：{resp.text[:160]}',
                resp.status_code)

        if isinstance(payload, dict) and 'respMsg' in payload and 'OutBlock_1' not in payload:
            _write_diag(diagnostic_path, endpoint, bas_dd, resp,
                        json.dumps(payload)[:1800], diagnostic_extra)
            raise KrxHttpError(
                f'KRX {endpoint} {bas_dd}: {payload.get("respCode")} {payload.get("respMsg")}',
                resp.status_code)

        rows = payload.get('OutBlock_1', []) if isinstance(payload, dict) else []
        return rows if isinstance(rows, list) else []

    raise RuntimeError(f'KRX {endpoint} {bas_dd}: 未预期的重试终点')   # pragma: no cover


def krx_index_daily(api_key: str, index_name: str, bas_dd: str, *,
                    candidates=None, tries: int = 4, timeout: int = 40,
                    diagnostic_path="krx_index_diagnostic.json",
                    diagnostic_extra: dict | None = None):
    """
    拉某个指数某一天的官方日行情。

    index_name: 'KOSPI' / 'KOSDAQ' / 'KRX'（大小写不限）
    返回 (rows, status)：
      rows   —— OutBlock_1 记录列表，拿到非空即成功
      status —— {
          'index': 'KOSPI',
          'date': '20261014',
          'ok': True,
          'endpoint': 'idx/kospi_dd_trd',
          'rows': 1,
          'attempted': [{'endpoint':..., 'http_status':..., 'ok':..., 'error':...}, ...]
      }
    认证失败（401/403）会抛 KrxAuthError —— 换端点名也救不了，没必须继续试。
    其余失败（端点不存在 404、响应无 OutBlock_1、网络错误）会继续下一个候选；
    全部候选都失败时返回 ([], status)，status['attempted'] 里是完整探测记录。
    每次调用都会把探测结果写到 diagnostic_path（不写密钥）。
    """
    name = str(index_name).strip().upper()
    if candidates is None:
        if name not in INDEX_ENDPOINTS:
            raise ValueError(f'未知指数 {index_name!r}，可选：{", ".join(INDEX_ENDPOINTS)}')
        candidates = INDEX_ENDPOINTS[name]
    else:
        candidates = tuple(candidates)

    attempts: list[dict] = []
    auth_error = None

    for category, endpoint in candidates:
        label = f'{category}/{endpoint}'
        try:
            rows = krx_daily(api_key, endpoint, bas_dd, category=category,
                             tries=tries, timeout=timeout)
        except KrxAuthError as exc:
            # 认证问题是全局的（key 或服务未获批），换名字重试没有意义
            attempts.append({'endpoint': label, 'http_status': 401,
                             'ok': False, 'rows': 0, 'error': str(exc)})
            auth_error = exc
            break
        except Exception as exc:                                  # noqa: BLE001
            attempts.append({'endpoint': label,
                             'http_status': getattr(exc, 'status_code', None),
                             'ok': False, 'rows': 0,
                             'error': f'{type(exc).__name__}: {exc}'[:300]})
            continue

        # 只保留真正的指数行情行：汇总/分组行的 CLSPRC_IDX 为空，先剔除；
        # 再按 IDX_NM 对齐到目标指数，避免拿到同响应里的其他指数。
        picked = _select_index_rows(rows, name)

        attempts.append({'endpoint': label, 'http_status': 200, 'ok': bool(picked),
                         'rows': len(picked), 'error': None})
        if picked:
            status = {'index': name, 'date': bas_dd, 'ok': True,
                      'endpoint': label, 'rows': len(picked),
                      'attempted': attempts}
            _write_index_diag(diagnostic_path, status, diagnostic_extra)
            return picked, status

    status = {'index': name, 'date': bas_dd, 'ok': False,
              'endpoint': None, 'rows': 0, 'attempted': attempts}
    _write_index_diag(diagnostic_path, status, diagnostic_extra)

    if auth_error is not None:
        raise auth_error
    return [], status


def _select_index_rows(rows, name: str) -> list:
    """
    从 idx 响应里挑出目标指数的真实行情行。

    1) 丢掉 CLSPRC_IDX 为空的行 —— 首行通常是汇总/分组行，没有收盘指数；
    2) 有 IDX_NM 时优先精确匹配指数名（'KOSPI' / 'KOSDAQ' / 'KRX'），
       其次匹配以该名字开头的（如 'KOSPI 200'），都不中则保留全部有效行。
    """
    if not isinstance(rows, list):
        return []

    valid = [
        r for r in rows
        if isinstance(r, dict)
        and str(r.get(INDEX_VALUE_FIELD) or '').strip()
    ]
    if not valid:
        return []

    named = [r for r in valid if str(r.get(INDEX_NAME_FIELD) or '').strip()]
    if not named:
        return valid

    exact = [r for r in named
             if str(r[INDEX_NAME_FIELD]).strip().upper() == name]
    if exact:
        return exact

    prefixed = [r for r in named
                if str(r[INDEX_NAME_FIELD]).strip().upper().startswith(name)]
    return prefixed or named


def _write_index_diag(path, status, extra):
    """把一次指数探测的完整尝试记录落盘（密钥不写入）。"""
    if not path:
        return
    diag = dict(status)
    if extra:
        diag.update(extra)
    try:
        Path(path).write_text(json.dumps(diag, ensure_ascii=False, indent=2),
                              encoding='utf-8')
        summary = ', '.join(
            f"{a['endpoint']}={'OK' if a['ok'] else 'FAIL'}"
            for a in diag.get('attempted', [])
        )
        print(f'[KRX诊断] {path}: {summary}')
    except Exception as exc:                                       # noqa: BLE001
        print(f'[KRX诊断] 写文件失败: {exc}')


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


if __name__ == '__main__':
    # 手动验收：三个指数各打一次，只看「拿到几行」，不假设字段结构。
    #   用法: KRX_API_KEY=xxx python scripts/krx_official.py [YYYYMMDD]
    import os
    from datetime import date, timedelta

    key = os.environ.get('KRX_API_KEY', '')
    if not key:
        print('缺少 KRX_API_KEY 环境变量，例如：KRX_API_KEY=xxx python scripts/krx_official.py')
        sys.exit(2)

    if len(sys.argv) > 1:
        bas_dd = sys.argv[1]
    else:
        # 默认取昨天，避开当日数据尚未发布的时段
        bas_dd = (date.today() - timedelta(days=1)).strftime('%Y%m%d')

    print(f'=== KRX 指数连通性测试 {bas_dd} ===')
    failures = 0
    for name in ('KOSPI', 'KOSDAQ', 'KRX'):
        try:
            rows, status = krx_index_daily(
                key, name, bas_dd,
                diagnostic_path=f'krx_index_diagnostic_{name.lower()}.json')
        except KrxAuthError as exc:
            print(f'[{name}] 认证失败: {exc}')
            failures += 1
            continue
        except Exception as exc:                                  # noqa: BLE001
            print(f'[{name}] 异常: {type(exc).__name__}: {exc}')
            failures += 1
            continue

        if rows:
            print(f'[{name}] OK  endpoint={status["endpoint"]}  rows={len(rows)}  '
                  f'首行字段={list(rows[0].keys())[:8]}')
        else:
            print(f'[{name}] FAIL  rows=0  '
                  f'尝试过: {", ".join(a["endpoint"] for a in status["attempted"])}')
            failures += 1

    sys.exit(1 if failures else 0)
