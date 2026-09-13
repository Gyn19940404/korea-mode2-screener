# -*- coding: utf-8 -*-
"""
V0.9.11 NXT官方20:00收盘版
- 历史K线：FinanceDataReader + NAVER（日线，最多240交易日）
- 当日基准：KRX 用 NAVER polling；NXT 用 NXT 官方正規市场页面20:00最终数据
- 当日成交额：KRX 实际交易额 + NXT 实际交易额（如有）
- 当日市值：最终价 × 上市股数
- 当天涨幅：最终价相对前收盘价

目的：让“现价 / 当天涨幅 / 当天成交额 / 市值”尽量与 Toss 在 NXT 收盘后的口径一致。
历史旧数据的成交额仍可能是近似值；从本版本开始每天保存精确成交额与 NXT 最终价。
"""
import gzip, json, time, threading
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import FinanceDataReader as fdr
import pandas as pd
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'stocks_data.js'
TMP = ROOT / 'stocks_data.tmp.js'
CACHE_DIR = ROOT / '.data_cache'
CACHE = CACHE_DIR / 'history_cache.json.gz'

HISTORY_DAYS = 240
MAX_WORKERS_HISTORY = 10
MAX_WORKERS_QUOTE = 16
RETRIES = 3
MIN_TOTAL_STOCKS = 2000
MIN_KOSPI = 700
MIN_KOSDAQ = 1200

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36'
_tls = threading.local()


def session():
    if not hasattr(_tls, 's'):
        s = requests.Session()
        s.headers.update({'User-Agent': UA, 'Referer': 'https://finance.naver.com/'})
        _tls.s = s
    return _tls.s


def sf(v, d=0.0):
    try:
        if isinstance(v, str):
            v = v.replace(',', '').replace('%', '').strip()
        return float(v) if v not in (None, '') else d
    except Exception:
        return d


def si(v, d=0):
    try:
        if isinstance(v, str):
            v = v.replace(',', '').strip()
        return int(float(v)) if v not in (None, '') else d
    except Exception:
        return d


def load_cache():
    if not CACHE.exists():
        return {}
    try:
        with gzip.open(CACHE, 'rt', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print('缓存读取失败，将全量重建:', e)
        return {}


def save_cache(cache):
    CACHE_DIR.mkdir(exist_ok=True)
    with gzip.open(CACHE, 'wt', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, separators=(',', ':'))


def universe():
    fs = []
    for m in ('KOSPI', 'KOSDAQ'):
        d = fdr.StockListing(m).copy()
        if d is None or d.empty:
            raise RuntimeError(f'{m} 股票列表为空')
        d['Market'] = m
        fs.append(d)
    all_u = pd.concat(fs, ignore_index=True).drop_duplicates(subset=['Code'])
    kc = int((all_u['Market'] == 'KOSPI').sum())
    qc = int((all_u['Market'] == 'KOSDAQ').sum())
    print(f'股票列表校验: KOSPI {kc}只 / KOSDAQ {qc}只 / 合计 {len(all_u)}只')
    if len(all_u) < MIN_TOTAL_STOCKS or kc < MIN_KOSPI or qc < MIN_KOSDAQ:
        raise RuntimeError(f'股票列表异常，拒绝继续：KOSPI {kc} / KOSDAQ {qc} / 合计 {len(all_u)}')
    return all_u


def fetch_history(code, old):
    end = datetime.now()
    if old:
        last = datetime.strptime(old[-1][0], '%Y-%m-%d')
        start = last - timedelta(days=10)
    else:
        start = end - timedelta(days=430)
    err = None
    for a in range(RETRIES):
        try:
            df = fdr.DataReader(f'NAVER:{code}', start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
            if df is None or df.empty:
                raise RuntimeError('历史数据为空')
            fresh = []
            for idx, r in df.iterrows():
                c = si(r.get('Close')); v = si(r.get('Volume'))
                if c <= 0:
                    continue
                # 第8字段 0 = 历史估算；1 = 已由收盘快照校正
                fresh.append([idx.strftime('%Y-%m-%d'), si(r.get('Open')), si(r.get('High')), si(r.get('Low')), c, v, c * v, 0])

            old_map = {x[0]: x for x in (old or [])}
            merged = dict(old_map)
            for row in fresh:
                prev = old_map.get(row[0])
                # 已经保存过精确收盘/NXT快照的数据，不再被FDR日线覆盖
                if prev and len(prev) >= 8 and si(prev[7]) == 1:
                    merged[row[0]] = prev
                else:
                    merged[row[0]] = row
            hist = [merged[k] for k in sorted(merged)][-HISTORY_DAYS:]
            if len(hist) < 20:
                raise RuntimeError('有效历史不足20日')
            return hist
        except Exception as e:
            err = e
            time.sleep(1.2 * (a + 1))
    print(f'[历史失败] {code}: {err}')
    return old or []


def fetch_quote(code):
    """获取 Naver KRX 收盘快照。NXT 最终价改由 NXT 官方页面单独获取。"""
    url = f'https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:{code}'
    err = None
    for a in range(RETRIES):
        try:
            r = session().get(url, timeout=12)
            r.raise_for_status()
            js = r.json()
            areas = js.get('result', {}).get('areas', [])
            if not areas or not areas[0].get('datas'):
                raise RuntimeError('polling 无数据')
            d = areas[0]['datas'][0]

            krx_price = si(d.get('nv'))
            prev_close = si(d.get('sv'))
            krx_value = si(d.get('aa'))
            krx_volume = si(d.get('aq'))
            listed = si(d.get('countOfListedStock'))

            if krx_price <= 0:
                raise RuntimeError('KRX价格为空')

            return {
                'price': krx_price,
                'prevClose': prev_close,
                'daychg': ((krx_price / prev_close - 1) * 100) if prev_close else sf(d.get('cr')),
                'turnoverWon': krx_value,
                'volume': krx_volume,
                'listedShares': listed,
                'useNxt': False,
                'nxtPrice': 0,
                'nxtValue': 0,
                'nxtVolume': 0,
                'nxtHigh': 0,
                'nxtLow': 0,
                'tradeDate': '',
            }
        except Exception as e:
            err = e
            time.sleep(0.8 * (a + 1))
    print(f'[KRX快照失败] {code}: {err}')
    return None


def fetch_nxt_official():
    """
    从 NXT 官方“正規市场(종목)”页面读取最终数据。
    官方页面注明行情约20分钟延迟，因此自动任务放到20:30(KST)运行。
    返回: {股票代码: {price, volume, value, high, low, pct}}
    """
    url = 'https://www.nextrade.co.kr/menu/transactionStatusMain/menuList.do'
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument(f'--user-agent={UA}')

    driver = None
    out = {}
    try:
        driver = webdriver.Chrome(options=options)
        driver.get(url)

        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_element_located((By.ID, 'trade1')))
        time.sleep(4)

        # 尽量把“每页显示数量”调到最大，减少翻页。
        try:
            for sel_el in driver.find_elements(By.TAG_NAME, 'select'):
                try:
                    sel = Select(sel_el)
                    numeric = []
                    for op in sel.options:
                        val = (op.get_attribute('value') or '').strip()
                        txt = (op.text or '').strip()
                        cand = val if val.isdigit() else txt.replace(',', '')
                        if cand.isdigit():
                            numeric.append((int(cand), op))
                    if numeric:
                        n, op = max(numeric, key=lambda x: x[0])
                        if 20 <= n <= 5000:
                            sel.select_by_visible_text(op.text)
                            time.sleep(2)
                except Exception:
                    pass
        except Exception:
            pass

        seen_signatures = set()

        for page_no in range(1, 80):
            time.sleep(1.0)
            rows = driver.find_elements(By.XPATH, "//table[@id='trade1']/tbody/tr")
            page_codes = []

            for row in rows:
                cells = [c.text.strip() for c in row.find_elements(By.TAG_NAME, 'td')]
                if len(cells) < 5:
                    continue

                code_match = None
                for c in cells[:3]:
                    m = re.search(r'(?<!\d)(\d{6})(?!\d)', c.replace(' ', ''))
                    if m:
                        code_match = m.group(1)
                        break
                if not code_match:
                    continue

                # 官方列顺序：
                # 종목코드, 종목명, 상장시장, 현재가, 대비, 등락률, 시가, 고가, 저가, 거래량, 거래대금 ...
                try:
                    code = code_match
                    price = si(cells[3])
                    pct = sf(cells[5]) if len(cells) > 5 else 0
                    high = si(cells[7]) if len(cells) > 7 else 0
                    low = si(cells[8]) if len(cells) > 8 else 0
                    volume = si(cells[9]) if len(cells) > 9 else 0
                    value = si(cells[10]) if len(cells) > 10 else 0
                except Exception:
                    continue

                if price > 0 and (volume > 0 or value > 0):
                    out[code] = {
                        'price': price,
                        'pct': pct,
                        'volume': volume,
                        'value': value,
                        'high': high,
                        'low': low,
                    }
                    page_codes.append(code)

            sig = tuple(page_codes[:3] + page_codes[-3:])
            if not page_codes or sig in seen_signatures:
                break
            seen_signatures.add(sig)

            # 找“下一页”。如果没有，就说明已经是最后一页或一次性显示全部。
            next_candidates = driver.find_elements(
                By.XPATH,
                "//a[contains(normalize-space(.),'다음') or contains(@class,'next') or contains(@title,'다음')]"
                " | //button[contains(normalize-space(.),'다음') or contains(@class,'next') or contains(@title,'다음')]"
            )
            clicked = False
            for btn in next_candidates:
                try:
                    cls = (btn.get_attribute('class') or '').lower()
                    aria = (btn.get_attribute('aria-disabled') or '').lower()
                    if 'disabled' in cls or aria == 'true':
                        continue
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(2)
                    clicked = True
                    break
                except Exception:
                    pass
            if not clicked:
                break

        print(f'NXT官方收盘数据: {len(out)}只')
        if len(out) < 300:
            raise RuntimeError(f'NXT官方数据仅抓到 {len(out)} 只，数量异常')
        return out

    finally:
        if driver is not None:
            driver.quit()


def apply_snapshot(hist, quote):
    """把今天最终盘口写回最后一根K线，之后筛选与图表使用同一基准。"""
    if not hist or not quote:
        return hist
    row = list(hist[-1])
    while len(row) < 8:
        row.append(0)

    price = si(quote.get('price'))
    if price <= 0:
        return hist

    # 收盘价采用NXT最终价（如有）；高低价把KRX与NXT合并。
    row[4] = price
    nh = si(quote.get('nxtHigh'))
    nl = si(quote.get('nxtLow'))
    if nh > 0:
        row[2] = max(si(row[2]), nh)
    if nl > 0:
        row[3] = min(x for x in (si(row[3]), nl) if x > 0)
    if si(quote.get('volume')) > 0:
        row[5] = si(quote['volume'])
    if si(quote.get('turnoverWon')) > 0:
        row[6] = si(quote['turnoverWon'])
    row[7] = 1

    out = list(hist)
    out[-1] = row
    return out


def build(code, name, market, listing_marcap, h, quote):
    if len(h) < 20:
        return None
    h = apply_snapshot(h, quote)
    cs = [x[4] for x in h]
    p = cs[-1]
    prev = si(quote.get('prevClose')) if quote else cs[-2]

    def ma(n):
        return sum(cs[-n:]) / n if len(cs) >= n else sum(cs) / len(cs)

    m5, m10, m20, m30, m60 = [ma(n) for n in (5, 10, 20, 30, 60)]
    m120 = ma(120)
    dist = (p / m5 - 1) * 100 if m5 else 0
    wb = cs[-6] if len(cs) >= 6 else cs[0]
    week = (p / wb - 1) * 100 if wb else 0

    turns20 = [x[6] / 1e8 for x in h[-20:]]
    mn = min(turns20)
    av = sum(turns20) / len(turns20)
    turn = h[-1][6] / 1e8

    streak = 0
    for i in range(len(cs) - 1, 0, -1):
        if cs[i] > cs[i - 1]:
            streak += 1
        else:
            break

    trend = 25 if m5 >= m10 >= m20 else (15 if m5 >= m10 else 5)
    pos = max(0, 25 - max(0, abs(dist) - .2) * 8)
    strength = min(18, max(0, week) * 1.5)
    liq = min(12, av / 50)
    score = round(min(85, trend + pos + strength + liq + 5))
    grade = 'A' if score >= 80 else ('B' if score >= 68 else 'C')

    # Toss/NXT收盘口径：用最终价×上市股数重算市值；无上市股数时回退FDR listing市值。
    listed = si(quote.get('listedShares')) if quote else 0
    marcap = p * listed if p > 0 and listed > 0 else listing_marcap

    return {
        'name': name, 'code': code, 'sector': '板块待接入', 'market': market,
        'marketCapTrillion': marcap / 1e12 if marcap else 0,
        'history': h, 'price': p,
        'daychg': ((p / prev - 1) * 100) if prev else 0,
        'week': week, 'turnover': turn, 'avgturn': av, 'minTurn20': mn,
        'streak': streak, 'ma5': m5, 'ma10': m10, 'ma20': m20,
        'ma30': m30, 'ma60': m60, 'ma120': m120,
        'dist': dist, 'rank': 0, 'sectorPower': 0,
        'score': score, 'grade': grade,
        'quoteSource': 'NAVER_NXT' if quote and quote.get('useNxt') else 'NAVER_KRX',
        'prevClose': prev,
        'krxNxtConsolidated': bool(quote and quote.get('useNxt')),
    }


def main():
    u = universe()
    cache = load_cache()
    print('股票总数:', len(u), '缓存股票:', len(cache))

    jobs = []
    for _, r in u.iterrows():
        code = str(r.get('Code', '')).zfill(6)
        jobs.append((code, str(r.get('Name', code)), str(r.get('Market', '')), si(r.get('Marcap', r.get('MarketCap', 0)))))

    # 1) 240日历史
    histories = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_HISTORY) as ex:
        fs = {ex.submit(fetch_history, c, cache.get(c, [])): (c, n, m, mc) for c, n, m, mc in jobs}
        for i, f in enumerate(as_completed(fs), 1):
            c, n, m, mc = fs[f]
            h = f.result()
            if h:
                histories[c] = h
            if i % 100 == 0:
                print(f'历史进度 {i}/{len(fs)}，有效 {len(histories)}')

    if len(histories) < MIN_TOTAL_STOCKS:
        raise RuntimeError(f'成功历史只有 {len(histories)} 只（最低要求 {MIN_TOTAL_STOCKS}），拒绝覆盖 stocks_data.js')

    # 2) 当天 Naver KRX + NXT 收盘快照
    quotes = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_QUOTE) as ex:
        fs = {ex.submit(fetch_quote, c): c for c, _, _, _ in jobs}
        for i, f in enumerate(as_completed(fs), 1):
            c = fs[f]
            q = f.result()
            if q:
                quotes[c] = q
            if i % 200 == 0:
                nxtn = sum(1 for x in quotes.values() if x.get('useNxt'))
                print(f'快照进度 {i}/{len(fs)}，有效 {len(quotes)}，NXT {nxtn}')

    if len(quotes) < MIN_TOTAL_STOCKS:
        raise RuntimeError(f'当日快照只有 {len(quotes)} 只，拒绝覆盖 stocks_data.js')

    # 3) NXT 官方20:00最终数据（官网约20分钟延迟，因此任务安排在20:30 KST）
    nxt_official = fetch_nxt_official()

    # 把 NXT 官方最终价/成交量/成交额合并进 KRX 快照
    merged_nxt = 0
    for c, nx in nxt_official.items():
        q = quotes.get(c)
        if not q:
            continue
        nx_price = si(nx.get('price'))
        nx_volume = si(nx.get('volume'))
        nx_value = si(nx.get('value'))
        if nx_price <= 0 or (nx_volume <= 0 and nx_value <= 0):
            continue

        q['useNxt'] = True
        q['nxtPrice'] = nx_price
        q['nxtVolume'] = nx_volume
        q['nxtValue'] = nx_value
        q['nxtHigh'] = si(nx.get('high'))
        q['nxtLow'] = si(nx.get('low'))

        q['price'] = nx_price
        q['daychg'] = ((nx_price / q['prevClose'] - 1) * 100) if q.get('prevClose') else sf(nx.get('pct'))
        q['turnoverWon'] = si(q.get('turnoverWon')) + nx_value
        q['volume'] = si(q.get('volume')) + nx_volume
        merged_nxt += 1

    print(f'NXT官方数据成功合并: {merged_nxt}只')
    if merged_nxt < 300:
        raise RuntimeError(f'NXT成功合并仅 {merged_nxt} 只，数量异常，拒绝覆盖正式数据')

    # 先把精确收盘快照写回缓存，防止下一次被历史日线覆盖
    for c, q in quotes.items():
        if c in histories:
            histories[c] = apply_snapshot(histories[c], q)
    save_cache(histories)

    res = []
    for c, n, m, mc in jobs:
        x = build(c, n, m, mc, histories.get(c, []), quotes.get(c))
        if x:
            res.append(x)

    if len(res) < MIN_TOTAL_STOCKS:
        raise RuntimeError(f'最终有效股票只有 {len(res)} 只，拒绝覆盖 stocks_data.js')

    res.sort(key=lambda x: (x['market'], x['code']))
    latest = max(x['history'][-1][0] for x in res)
    nxt_count = sum(1 for x in res if x.get('quoteSource') == 'NAVER_NXT')
    meta = {
        'latest_date': latest,
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'kospi_count': sum(x['market'] == 'KOSPI' for x in res),
        'kosdaq_count': sum(x['market'] == 'KOSDAQ' for x in res),
        'total_count': len(res),
        'history_days': HISTORY_DAYS,
        'source': 'FinanceDataReader(NAVER history) + NAVER polling(KRX) + NXT official 20:00 close',
        'update_mode': '240日缓存增量 + KRX收盘 + NXT官方20:00最终数据',
        'nxt_count': nxt_count,
        'snapshot_rule': 'NXT官方有实际成交则使用20:00最终价；成交额=KRX+NXT；无NXT成交则使用KRX',
    }
    payload = 'window.DATA_META=' + json.dumps(meta, ensure_ascii=False, separators=(',', ':')) + ';\nwindow.STOCKS_DATA=' + json.dumps(res, ensure_ascii=False, separators=(',', ':')) + ';\n'
    TMP.write_text(payload, encoding='utf-8')
    TMP.replace(OUT)
    print('更新完成，有效股票:', len(res), 'NXT股票:', nxt_count, '历史上限:', HISTORY_DAYS)


if __name__ == '__main__':
    main()
