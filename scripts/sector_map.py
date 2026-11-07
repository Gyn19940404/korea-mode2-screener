# -*- coding: utf-8 -*-
"""
板块（업종）映射：从 NAVER 移动端接口抓取「行业 → 成分股」全表。

为什么需要这个文件：
  KRX OpenAPI 的 종목기본정보 不含업종字段，但网页的「板块」列和「强势板块」
  面板都依赖 s.sector。之前一直硬编码成「板块待接入」，等于整个板块功能是空的。

数据来源（公开、无需登录）：
  GET https://m.stock.naver.com/api/stocks/industry?page=N          -> 行业列表（79 个，含当日涨跌幅）
  GET https://m.stock.naver.com/api/stocks/industry/{no}?page=N     -> 该行业成分股（每页 20）

特点：
  · 行业列表自带 changeRate / riseCount / fallCount，可直接做「板块强弱」。
  · 업종归属极少变动，因此本地缓存 7 天，日常运行只读缓存，不重复抓。
  · 全量重建约 226 次请求（79 行业 × 平均 3 页），耗时 2~4 分钟，只在缓存过期时发生。
"""
import json
import time
from datetime import datetime, timedelta, timezone

MOBILE_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
    'AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1'
)
BASE = 'https://m.stock.naver.com/api/stocks/industry'
CACHE_VERSION = 1
PAGE_SIZE = 20          # 接口固定每页 20，不随参数变化
MAX_PAGES_PER_GROUP = 90


def _session():
    import requests
    s = requests.Session()
    s.headers.update({'User-Agent': MOBILE_UA, 'Referer': 'https://m.stock.naver.com/'})
    return s


def _get_json(s, url, tries=4):
    for i in range(tries):
        try:
            r = s.get(url, timeout=20)
            if r.status_code == 200 and r.text.strip().startswith('{'):
                return r.json()
        except Exception:                                          # noqa: BLE001
            pass
        time.sleep(1.2 * (i + 1))
    return None


def _fetch_industry_groups(s):
    """取全部行业（分页）。返回 list[{no,name,changeRate,totalCount,riseCount,fallCount}]"""
    groups, page = [], 1
    while page <= 10:
        j = _get_json(s, f'{BASE}?page={page}')
        if not j:
            break
        g = j.get('groups') or []
        if not g:
            break
        groups.extend(g)
        if len(groups) >= int(j.get('totalCount') or 0):
            break
        page += 1
        time.sleep(0.8)
    return groups


def build_sector_map(verbose=True):
    """全量重建映射。返回 {'mapping':{code:{industry,industryNo}}, 'meta':{no:{...}}}"""
    s = _session()
    groups = _fetch_industry_groups(s)
    if not groups:
        raise RuntimeError('NAVER 行业列表为空，无法建立板块映射')

    mapping, meta = {}, {}
    for gi, g in enumerate(groups, 1):
        no = g.get('no')
        name = str(g.get('name') or '').strip()
        if not no or not name:
            continue
        meta[str(no)] = {
            'name': name,
            'changeRate': g.get('changeRate'),
            'totalCount': g.get('totalCount'),
            'riseCount': g.get('riseCount'),
            'fallCount': g.get('fallCount'),
            'steadyCount': g.get('steadyCount'),
        }
        got, page = 0, 1
        while page <= MAX_PAGES_PER_GROUP:
            j = _get_json(s, f'{BASE}/{no}?page={page}')
            if not j:
                break
            stocks = j.get('stocks') or []
            if not stocks:
                break
            for x in stocks:
                code = str(x.get('itemCode') or '')
                if len(code) == 6 and code.isdigit():
                    mapping[code] = {'industry': name, 'industryNo': str(no)}
            got += len(stocks)
            if got >= int(j.get('totalCount') or 0) or len(stocks) < PAGE_SIZE:
                break
            page += 1
            time.sleep(0.25)
        meta[str(no)]['fetched'] = got
        if verbose and gi % 20 == 0:
            print(f'[板块映射] {gi}/{len(groups)} 个行业，已映射 {len(mapping)} 只')

    if verbose:
        print(f'[板块映射] 完成：{len(mapping)} 只股票 / {len(meta)} 个行业')
    return {'mapping': mapping, 'meta': meta}


def load_sector_map(cache_path, max_age_days=7, verbose=True):
    """
    读缓存；不存在或过期则重建。
    任何失败都退回空映射（板块显示「未分类」），绝不阻断日更。
    """
    now = datetime.now(timezone.utc)
    fresh = False
    payload = None

    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding='utf-8'))
            if payload.get('version') != CACHE_VERSION:
                payload = None
            else:
                ts = datetime.fromisoformat(payload['fetched_at'])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                fresh = (now - ts) < timedelta(days=max_age_days)
        except Exception as exc:                                   # noqa: BLE001
            if verbose:
                print(f'[板块映射] 缓存损坏，将重建: {exc}')
            payload = None

    if payload and fresh:
        if verbose:
            print(f'[板块映射] 使用缓存（{payload["fetched_at"][:10]}，'
                  f'{len(payload["mapping"])} 只）')
        return payload

    try:
        built = build_sector_map(verbose=verbose)
        built.update({
            'version': CACHE_VERSION,
            'fetched_at': now.isoformat(),
        })
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(built, ensure_ascii=False), encoding='utf-8')
        return built
    except Exception as exc:                                       # noqa: BLE001
        if verbose:
            print(f'[板块映射] 重建失败，沿用旧缓存/空表: {type(exc).__name__}: {exc}')
        return payload or {'mapping': {}, 'meta': {}, 'version': CACHE_VERSION,
                           'fetched_at': now.isoformat()}


if __name__ == '__main__':
    from pathlib import Path
    out = load_sector_map(Path('.data_cache/sector_map.json'), max_age_days=0)
    m = out['mapping']
    print('样例:', list(m.items())[:5])
    print('行业数:', len(out['meta']))
    top = sorted(out['meta'].items(), key=lambda kv: -(kv[1].get('totalCount') or 0))[:5]
    for no, info in top:
        print(f"  {info['name']}: {info['totalCount']} 只, 当日 {info.get('changeRate')}%")

