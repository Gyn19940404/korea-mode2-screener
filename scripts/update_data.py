# -*- coding: utf-8 -*-
"""
V0.9.33 Toss月平均成交额7股反推诊断版
- 历史K线：FinanceDataReader + NAVER（日线，最多240交易日）
- 当日基准：KRX 用 NAVER polling；NXT 用 NXT 官方正規市场页面20:00最终数据
- 当日成交额：NAVER polling 的综合成交额优先；NXT仅在NAVER值缺失时补充，避免重复相加
- 当日市值：采用FDR/KRX当日市值，并把同一公司的优先股/种类股市值合并到普通股，贴近Toss公司市值口径
- 当天涨幅：最终价相对前收盘价

目的：修复V0.9.26：保留已对齐的NXT现价与Toss市值；补近20个交易日NXT历史成交额，并输出综合成交量；默认筛选排除优先股。
历史旧数据的成交额仍可能是近似值；从本版本开始每天保存精确成交额与 NXT 最终价。
"""
import gzip, json, time, threading, re, csv
from datetime import datetime, timedelta, timezone
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
                'krxPrice': krx_price,
                'prevClose': prev_close,
                'daychg': ((krx_price / prev_close - 1) * 100) if prev_close else sf(d.get('cr')),
                'turnoverWon': krx_value,
                'volume': krx_volume,
                'krxVolume': krx_volume,
                'krxTurnoverWon': krx_value,
                'krxOpen': si(d.get('ov')),
                'krxHigh': si(d.get('hv')),
                'krxLow': si(d.get('lv')),
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


def fetch_nxt_official(target_date):
    """
    V0.9.17：不再点击 NXT 网页按钮。
    先用 Chrome 打开 NXT 官网取得 Cloudflare 会话，
    再在浏览器同源环境里直接 POST 官方 XHR：
    /brdinfoTime/brdinfoTimeList.do
    """
    url = 'https://www.nextrade.co.kr/menu/transactionStatusMain/menuList.do'
    api_path = '/brdinfoTime/brdinfoTimeList.do'

    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument(f'--user-agent={UA}')

    driver = None
    out = {}

    def pick(d, *keys):
        for k in keys:
            if k in d and d[k] not in (None, ''):
                return d[k]
        return None

    def parse_record(r):
        # V0.9.22：按 NXT 实际 JSON 解析，并对成交量/成交额字段做容错匹配。
        if not isinstance(r, dict):
            return None

        raw_code = str(r.get('isuSrdCd') or '').strip()   # 例: A000660
        code = raw_code[-6:] if len(raw_code) >= 6 else ''
        if len(code) != 6 or not code.isdigit():
            return None

        price = si(r.get('curPrc'))
        pct = sf(r.get('upDownRate'))
        high = si(r.get('hgpr'))
        low = si(r.get('lwpr'))

        # NXT字段名曾出现细微差异；这里不再只依赖一个固定拼写。
        volume_raw = (
            pick(r, 'accTdQty', 'acctTdQty', 'acctQty', 'accQty')
        )
        value_raw = (
            pick(r, 'accTrval', 'accTrVal', 'acctTrVal', 'acctTrval')
        )

        # 若固定字段仍未命中，则按字段名语义兜底。
        if volume_raw is None or value_raw is None:
            for k, v in r.items():
                nk = str(k).lower().replace('_', '')
                if volume_raw is None and nk.startswith('acc') and 'qty' in nk:
                    volume_raw = v
                if value_raw is None and nk.startswith('acc') and ('trval' in nk or 'value' in nk):
                    value_raw = v

        volume = si(volume_raw)
        value = si(value_raw)

        if price <= 0:
            return None

        return code, {
            'price': price,
            'pct': pct,
            'volume': volume,
            'value': value,
            'high': high,
            'low': low,
            '_raw': r,
        }

    try:
        driver = webdriver.Chrome(options=options)
        driver.get(url)

        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_element_located((By.ID, 'trade1')))
        time.sleep(3)

        target_yyyymmdd = target_date.replace('-', '')
        print(f'NXT直接XHR目标交易日: {target_date} -> scAggDd={target_yyyymmdd}')

        all_records = []
        page_index = 1
        page_unit = 1000

        while page_index <= 10:
            payload = {
                'scSecuGroup': 'STOCK',
                'scAggDd': target_yyyymmdd,
                '_search': 'false',
                'nd': str(int(time.time() * 1000)),
                'pageUnit': str(page_unit),
                'pageIndex': str(page_index),
                'sidx': '',
                'sord': 'asc',
            }

            script = """
            const done = arguments[arguments.length - 1];
            const path = arguments[0];
            const payload = arguments[1];

            const body = new URLSearchParams(payload).toString();

            fetch(path, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json, text/javascript, */*; q=0.01'
                },
                body: body
            })
            .then(async r => {
                const txt = await r.text();
                done(JSON.stringify({status:r.status, text:txt}));
            })
            .catch(err => done(JSON.stringify({status:0, error:String(err)})));
            """

            raw = driver.execute_async_script(script, api_path, payload)
            wrapper = json.loads(raw)
            status = wrapper.get('status', 0)
            if status != 200:
                raise RuntimeError(f'NXT XHR HTTP {status}: {wrapper.get("error","")}')

            body_text = wrapper.get('text', '')
            data = json.loads(body_text)

            records = data.get('brdinfoTimeList') or []
            total_cnt = int(data.get('totalCnt') or data.get('records') or 0)

            print(
                f'NXT XHR page={page_index}, '
                f'本页={len(records)}, totalCnt={total_cnt}, '
                f'setTime={data.get("setTime")}'
            )

            if page_index == 1 and records:
                print('NXT首条字段:', list(records[0].keys()) if isinstance(records[0], dict) else type(records[0]))
                print('NXT首条样本:', json.dumps(records[0], ensure_ascii=False)[:3000])

            all_records.extend(records)

            if not records:
                break
            if total_cnt and len(all_records) >= total_cnt:
                break
            if len(records) < page_unit:
                break
            page_index += 1

        for idx, r in enumerate(all_records):
            parsed = parse_record(r)
            if idx == 0:
                print('NXT首条原始成交字段:',
                      {k: v for k, v in r.items()
                       if ('qty' in str(k).lower() or 'val' in str(k).lower())})
                print('NXT首条解析结果:', parsed)
            if parsed:
                code, vals = parsed
                out[code] = vals

        print(f'NXT官方XHR原始记录: {len(all_records)}只')
        print(f'NXT官方收盘数据: {len(out)}只')

        if len(all_records) >= 300 and len(out) < 300:
            # 接口已经通，但字段名若有差异，不允许错误覆盖旧数据。
            sample = all_records[0] if all_records else {}
            raise RuntimeError(
                'NXT接口已取得数据，但字段解析不足300只。'
                f' 首条样本={json.dumps(sample, ensure_ascii=False)[:1200]}'
            )

        if len(out) == 0:
            # 休市日/非交易日：NXT会正常返回0只。交给主流程与历史最新交易日比较，
            # 不把正常休市误判成程序故障。
            print('[V0.9.48休市识别] NXT当日0只 -> 作为休市候选正常返回')
            return {}

        if len(out) < 300:
            # 非0但不足300只仍视为数据异常，防止残缺行情覆盖正式网页。
            raise RuntimeError(f'NXT官方数据仅抓到 {len(out)} 只，数量异常')

        return out

    finally:
        if driver is not None:
            driver.quit()

def fetch_nxt_history_values(trade_dates):
    """一次Chrome会话批量抓取指定交易日的NXT成交额，返回 {date:{code:valueWon}}。"""
    dates = [str(d) for d in trade_dates if d]
    if not dates:
        return {}
    url = 'https://www.nextrade.co.kr/menu/transactionStatusMain/menuList.do'
    api_path = '/brdinfoTime/brdinfoTimeList.do'
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new'); options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage'); options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080'); options.add_argument(f'--user-agent={UA}')
    driver = None
    result = {}
    script = """
    const done = arguments[arguments.length - 1];
    const body = new URLSearchParams(arguments[1]).toString();
    fetch(arguments[0], {method:'POST', credentials:'same-origin', headers:{
      'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8',
      'X-Requested-With':'XMLHttpRequest','Accept':'application/json, text/javascript, */*; q=0.01'
    }, body}).then(async r=>done(JSON.stringify({status:r.status,text:await r.text()})))
      .catch(err=>done(JSON.stringify({status:0,error:String(err)})));
    """
    try:
        driver = webdriver.Chrome(options=options)
        driver.get(url)
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, 'trade1')))
        time.sleep(2)
        for i, d in enumerate(dates, 1):
            payload={'scSecuGroup':'STOCK','scAggDd':d.replace('-',''),'_search':'false',
                     'nd':str(int(time.time()*1000)),'pageUnit':'1000','pageIndex':'1','sidx':'','sord':'asc'}
            raw=driver.execute_async_script(script, api_path, payload)
            wrap=json.loads(raw)
            if wrap.get('status') != 200:
                print(f'[NXT历史跳过] {d}: HTTP {wrap.get("status")}')
                continue
            data=json.loads(wrap.get('text','{}'))
            records=data.get('brdinfoTimeList') or []
            day={}
            for r in records:
                raw_code=str(r.get('isuSrdCd') or '').strip()
                code=raw_code[-6:] if len(raw_code)>=6 else ''
                if len(code)!=6 or not code.isdigit():
                    continue
                val=si(r.get('accTrval') if r.get('accTrval') is not None else (r.get('accTrVal') if r.get('accTrVal') is not None else (r.get('acctTrVal') if r.get('acctTrVal') is not None else r.get('acctTrval'))))
                if val>0: day[code]=val
            result[d]=day
            print(f'NXT历史成交额 {i}/{len(dates)} {d}: {len(day)}只')
        return result
    finally:
        if driver is not None: driver.quit()


def merge_nxt_history_turnover(histories, nxt_hist, latest_date):
    """旧历史行用 KRX近似成交额 + NXT成交额；已由精确快照校正的行不重复叠加。"""
    changed=0
    for code,h in histories.items():
        for row in h:
            d=row[0]
            if d == latest_date or d not in nxt_hist:
                continue
            precise = len(row)>=8 and si(row[7])==1
            if precise:
                continue
            nv=si(nxt_hist[d].get(code))
            if nv>0:
                row[6]=si(row[6])+nv
                changed+=1
    print(f'NXT历史成交额合并完成: {changed} 条股票日记录')


def write_turnover_diagnostic(histories, nxt_hist, nxt_official, latest_date):
    """V0.9.33：7只Toss对照股，反推“1个月平均成交额”的时间窗与KRX/NXT口径。只诊断，不改变正式筛选公式。"""
    import csv
    targets = {
        '005930': ('삼성전자', 69000.0),
        '000660': ('SK하이닉스', 90000.0),
        '402340': ('SK스퀘어', 7728.2),
        '009150': ('삼성전기', 14000.0),
        '034020': ('두산에너빌리티', 5417.4),
        '006400': ('삼성SDI', 4079.0),
        '196170': ('알테오젠', 3105.1),
    }
    out_path = ROOT / 'turnover_diagnostic.csv'
    summary_path = ROOT / 'turnover_formula_summary.csv'
    rows_out, summary = [], []

    latest_dt = datetime.strptime(str(latest_date), '%Y-%m-%d')
    # “自然1个月”：与网页现有定义一致，latest_date往前1个月，左开右闭。
    y, m = latest_dt.year, latest_dt.month - 1
    if m == 0:
        y -= 1; m = 12
    import calendar
    start_day = min(latest_dt.day, calendar.monthrange(y, m)[1])
    month_start = latest_dt.replace(year=y, month=m, day=start_day)
    month_start_key = month_start.strftime('%Y-%m-%d')

    print('========== V0.9.33 Toss月均成交额 7股反推诊断开始 ==========')
    print(f'[诊断区间] 最新交易日={latest_date} | 自然1个月起点>{month_start_key} | 同时测试20交易日/21交易日/自然1个月')

    formula_errors = {
        'KRX_20': [], '综合_20': [],
        'KRX_21': [], '综合_21': [],
        'KRX_自然月': [], '综合_自然月': [],
    }

    for code, (name, toss_target) in targets.items():
        full = histories.get(code, [])
        if not full:
            print(f'[月均诊断] {name} {code}: 无历史数据')
            continue

        # 最多取35个交易日，足够覆盖20/21交易日和自然1个月。
        h = full[-35:]
        daily = []
        for row in h:
            d = str(row[0])
            stored = si(row[6]) if len(row) > 6 else 0
            precise = len(row) >= 8 and si(row[7]) == 1
            nx = si((nxt_official.get(code) or {}).get('value')) if d == latest_date else si((nxt_hist.get(d) or {}).get(code))

            # 缓存中 precise=1 的行已经是当时保存的综合成交额，不再重复加NXT。
            # 普通FDR历史行则视为KRX/NAVER基础成交额，再叠加对应日NXT成交额。
            if precise:
                total = stored
                krx = max(0, stored - nx) if nx > 0 else stored
            else:
                krx = stored
                total = stored + nx

            daily.append({'date': d, 'krx': krx, 'nxt': nx, 'total': total, 'precise': 1 if precise else 0})
            rows_out.append([name, code, d, krx, nx, total, 1 if precise else 0])

        def avg(rows, key):
            vals = [r[key] for r in rows if r[key] > 0]
            return (sum(vals) / len(vals) / 1e8, len(vals)) if vals else (0.0, 0)

        r20 = daily[-20:]
        r21 = daily[-21:]
        rcal = [r for r in daily if r['date'] > month_start_key and r['date'] <= str(latest_date)]
        k20,n20=avg(r20,'krx'); t20,_=avg(r20,'total')
        k21,n21=avg(r21,'krx'); t21,_=avg(r21,'total')
        kcal,ncal=avg(rcal,'krx'); tcal,_=avg(rcal,'total')

        vals = {
            'KRX_20': k20, '综合_20': t20,
            'KRX_21': k21, '综合_21': t21,
            'KRX_自然月': kcal, '综合_自然月': tcal,
        }
        for f,v in vals.items():
            formula_errors[f].append(abs(v-toss_target))
        closest = min(vals, key=lambda f: abs(vals[f]-toss_target))

        print(f'--- {name} {code} | Toss目标={toss_target:.1f}亿 ---')
        print(f'[公式对比] 20日 KRX={k20:.1f}亿 综合={t20:.1f}亿 | 21日 KRX={k21:.1f}亿 综合={t21:.1f}亿 | 自然月({ncal}日) KRX={kcal:.1f}亿 综合={tcal:.1f}亿')
        print(f'[最接近Toss] {closest}={vals[closest]:.1f}亿 | 误差={abs(vals[closest]-toss_target):.1f}亿')
        summary.append([name,code,toss_target,k20,t20,k21,t21,ncal,kcal,tcal,closest,vals[closest],abs(vals[closest]-toss_target)])

    print('========== 6种公式整体误差（7股平均绝对误差） ==========')
    ranked=[]
    for f,errs in formula_errors.items():
        mae=sum(errs)/len(errs) if errs else 10**99
        ranked.append((mae,f))
    ranked.sort()
    for i,(mae,f) in enumerate(ranked,1):
        print(f'[公式排名] #{i} {f} | 7股平均绝对误差={mae:.1f}亿')
    if ranked:
        print(f'[当前最接近Toss公式] {ranked[0][1]} | MAE={ranked[0][0]:.1f}亿')

    with out_path.open('w', newline='', encoding='utf-8-sig') as f:
        w=csv.writer(f)
        w.writerow(['股票','代码','日期','KRX基础成交额(원)','NXT成交额(원)','综合成交额(원)','缓存精确快照'])
        w.writerows(rows_out)
    with summary_path.open('w', newline='', encoding='utf-8-sig') as f:
        w=csv.writer(f)
        w.writerow(['股票','代码','Toss目标(亿)','20日KRX(亿)','20日综合(亿)','21日KRX(亿)','21日综合(亿)','自然月交易日数','自然月KRX(亿)','自然月综合(亿)','单股最接近公式','最接近值(亿)','绝对误差(亿)'])
        w.writerows(summary)
    print(f'诊断CSV已生成: {out_path.name} / {summary_path.name}')
    print('========== V0.9.33 Toss月均成交额 7股反推诊断结束 ==========')

def apply_snapshot(hist, quote):
    """把当前交易日最终盘口写入K线；若历史源还停在前一日，则新增当天K线而不是覆盖昨天。"""
    if not hist or not quote:
        return hist
    price = si(quote.get('price'))
    if price <= 0:
        return hist

    trade_date = str(quote.get('tradeDate') or '').strip()
    last_date = str(hist[-1][0])
    if trade_date and trade_date > last_date:
        # 历史源尚未发布当天日线：用当天快照新增一根，避免网页继续显示昨天。
        op = si(quote.get('krxOpen')) or price
        hi = max(si(quote.get('krxHigh')), si(quote.get('nxtHigh')), price)
        lows = [x for x in (si(quote.get('krxLow')), si(quote.get('nxtLow')), price) if x > 0]
        lo = min(lows) if lows else price
        vol = si(quote.get('volume'))
        val = si(quote.get('turnoverWon'))
        out = list(hist) + [[trade_date, op, hi, lo, price, vol, val, 1]]
        return out[-HISTORY_DAYS:]

    row = list(hist[-1])
    while len(row) < 8:
        row.append(0)
    row[4] = price
    nh = si(quote.get('nxtHigh')); nl = si(quote.get('nxtLow'))
    kh = si(quote.get('krxHigh')); kl = si(quote.get('krxLow')); ko = si(quote.get('krxOpen'))
    if ko > 0: row[1] = ko
    if kh > 0: row[2] = max(si(row[2]), kh)
    if nh > 0: row[2] = max(si(row[2]), nh)
    lows = [x for x in (si(row[3]), kl, nl) if x > 0]
    if lows: row[3] = min(lows)
    if si(quote.get('volume')) > 0: row[5] = si(quote['volume'])
    if si(quote.get('turnoverWon')) > 0: row[6] = si(quote['turnoverWon'])
    row[7] = 1
    out = list(hist); out[-1] = row
    return out


def build(code, name, market, listing_marcap, h, quote, toss_marcap=0):
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
    prev_turn = h[-2][6] / 1e8 if len(h) >= 2 else 0

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

    # Toss筛选器的市值不是简单用NXT最终价×普通股股数。
    # 使用FDR/KRX当日市值；普通股若存在优先股/种类股，则使用公司合并市值。
    marcap = si(toss_marcap) if si(toss_marcap) > 0 else si(listing_marcap)

    return {
        'name': name, 'code': code, 'sector': '板块待接入', 'market': market,
        'marketCapTrillion': marcap / 1e12 if marcap else 0,
        'ownMarketCapTrillion': si(listing_marcap) / 1e12 if si(listing_marcap) else 0,
        'companyMarketCapTrillion': si(toss_marcap) / 1e12 if si(toss_marcap) else 0,
        'listedShares': si(quote.get('listedShares')) if quote else 0,
        'priceTimesSharesTrillion': (p * si(quote.get('listedShares')) / 1e12) if quote and si(quote.get('listedShares')) else 0,
        'history': h, 'price': p,
        'daychg': ((p / prev - 1) * 100) if prev else 0,
        'week': week, 'turnover': turn, 'prevTurnover': prev_turn, 'avgturn': av, 'minTurn20': mn,
        'krxTurnoverWon': si(quote.get('krxTurnoverWon')) if quote else 0,
        'nxtTurnoverWon': si(quote.get('nxtValue')) if quote else 0,
        'combinedTurnoverWon': si(quote.get('turnoverWon')) if quote else 0,
        'volume': si(quote.get('volume')) if quote else si(h[-1][5]),
        'naverVolume': si(quote.get('krxVolume')) if quote else 0,
        'nxtVolume': si(quote.get('nxtVolume')) if quote else 0,
        'isPreferred': bool(re.search(r'(?:\d+우B|\d+우|우B|우)$', str(name or '').strip())),
        'streak': streak, 'ma5': m5, 'ma10': m10, 'ma20': m20,
        'ma30': m30, 'ma60': m60, 'ma120': m120,
        'dist': dist, 'rank': 0, 'sectorPower': 0,
        'score': score, 'grade': grade,
        'quoteSource': 'NAVER_NXT' if quote and quote.get('useNxt') else 'NAVER_KRX',
        'prevClose': prev,
        'krxNxtConsolidated': bool(quote and quote.get('useNxt')),
    }



def company_root_name(name):
    """把常见优先股/种类股名称归到普通股公司名，用于Toss式公司总市值。"""
    n = str(name or '').strip()
    # 常见：삼성전자우 / 현대차2우B / LG화학우 / 한화3우B
    n = re.sub(r'(?:\d+우B|\d+우|우B|우)$', '', n)
    return n.strip()


def build_toss_marcap_map(u):
    """FDR/KRX当日市值 + 同公司种类股合并。普通股使用公司总市值，种类股保留自身市值。"""
    own = {}
    groups = {}
    names = {}
    for _, r in u.iterrows():
        c = str(r.get('Code', '')).zfill(6)
        n = str(r.get('Name', c))
        mc = si(r.get('Marcap', r.get('MarketCap', 0)))
        own[c] = mc
        names[c] = n
        root = company_root_name(n)
        if root:
            groups[root] = groups.get(root, 0) + mc

    out = {}
    for c, mc in own.items():
        n = names[c]
        root = company_root_name(n)
        is_class_share = (root != n)
        out[c] = mc if is_class_share else max(mc, groups.get(root, mc))
    return out

def main():
    u = universe()

    # V0.9.51 基础股票池分类诊断：只分类，不排除。
    pool_diag = ROOT / 'universe_type_diagnostic.csv'
    pool_counts = {}
    with pool_diag.open('w', encoding='utf-8-sig', newline='') as pf:
        pw = csv.writer(pf)
        pw.writerow(['代码','股票','市场','分类','当前是否前端排除','说明'])
        for _, ur in u.iterrows():
            uc = str(ur.get('Code','')).zfill(6)
            un = str(ur.get('Name', uc)).strip()
            um = str(ur.get('Market',''))
            preferred = bool(
                re.search(r'(?:\\d+우B|\\d+우|우B|우)
            if preferred:
                typ, excluded, note = '优先股/种类股', '是', '当前前端isPreferred已排除'
            elif re.search(r'(?:스팩|SPAC)', un, re.I):
                typ, excluded, note = 'SPAC', '否', '待确认后决定是否从模式2基础池排除'
            elif (
                un.endswith('리츠')
                or '리츠코크렙' in un
                or re.search(r'REIT(?:S)?
                typ, excluded, note = 'REITs', '否', '待确认后决定是否从模式2基础池排除'
            else:
                typ, excluded, note = '普通/其他', '否', ''
            pool_counts[typ] = pool_counts.get(typ, 0) + 1
            pw.writerow([uc, un, um, typ, excluded, note])
    print('[V0.9.51股票池分类] ' + json.dumps(pool_counts, ensure_ascii=False))
    print(f'[V0.9.51股票池分类] 明细已生成: {pool_diag}')

    toss_marcap_map = build_toss_marcap_map(u)
    print('Toss市值口径映射完成:', len(toss_marcap_map), '只')
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
    history_latest_date = max(h[-1][0] for h in histories.values() if h)
    kst_now = datetime.now(timezone(timedelta(hours=9)))
    expected_date = kst_now.strftime('%Y-%m-%d')
    print(f'历史数据最新交易日: {history_latest_date} | KST运行日期: {expected_date}')

    # V0.9.48：优先直接验证“今天”是否存在NXT正式成交数据。
    # 若今天有足够NXT股票，说明是交易日，即使FDR/NAVER历史仍滞后一天，也强制生成今天K线。
    today_nxt = fetch_nxt_official(expected_date)
    if len(today_nxt) >= 300:
        latest_trade_date = expected_date
        nxt_official = today_nxt
        print(f'[V0.9.48当日校验] 今日NXT有效 {len(today_nxt)}只 -> 正式数据日期={latest_trade_date}')
    else:
        # 20:30 KST以后，如果NAVER已经出现大量今日行情，则说明今天是交易日。
        # 此时NXT抓取异常时禁止静默回退到昨天，避免网页继续显示旧日期。
        quote_dates = [str(q.get('tradeDate') or '') for q in quotes.values()]
        today_quote_count = sum(1 for d in quote_dates if d == expected_date)
        after_2030 = (kst_now.weekday() < 5 and
                      (kst_now.hour > 20 or (kst_now.hour == 20 and kst_now.minute >= 30)))
        print(f'[V0.9.48当日校验] 今日NXT={len(today_nxt)}只 | NAVER今日={today_quote_count}只 | 20:30后={after_2030}')
        if after_2030 and today_quote_count >= 500:
            raise RuntimeError(
                f'[V0.9.48] 已确认{expected_date}有交易，但NXT当天仅{len(today_nxt)}只；'
                '拒绝生成上一交易日数据，请重跑。'
            )
        latest_trade_date = history_latest_date
        print(f'[V0.9.48当日校验] 今日未确认完整交易数据 -> 保留最近交易日={latest_trade_date}')
        nxt_official = fetch_nxt_official(latest_trade_date)

    for q in quotes.values():
        q['tradeDate'] = latest_trade_date

    # V0.9.33诊断：额外读取最近35个真实交易日的NXT成交额，仅用于反推Toss“1个月平均”口径。
    # 正式历史合并仍只处理最近20交易日，避免改变V0.9.32基准算法。
    ref_hist = max(histories.values(), key=len)
    trade_dates35 = [r[0] for r in ref_hist[-35:]]
    past_dates35 = [d for d in trade_dates35 if d != latest_trade_date]
    nxt_hist_diag = fetch_nxt_history_values(past_dates35)

    # 先在未修改正式历史前做7股反推诊断。
    write_turnover_diagnostic(histories, nxt_hist_diag, nxt_official, latest_trade_date)

    # 正式逻辑保持V0.9.32：只把最近20交易日的NXT成交额补入非精确历史行。
    trade_dates20 = [r[0] for r in ref_hist[-20:]]
    past_dates20 = [d for d in trade_dates20 if d != latest_trade_date]
    nxt_hist_values = {d: nxt_hist_diag.get(d, {}) for d in past_dates20}
    merge_nxt_history_turnover(histories, nxt_hist_values, latest_trade_date)

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
        # V0.9.35：与 Toss 单日成交额对表确认：单日成交额 = KRX/NAVER aa + NXT accTrval。
        # 例如 삼성SDI：1728.3亿 + 1392.2亿 = 3120.5亿，与 Toss 完全一致。
        # 因此有 NXT 成交额时直接相加；没有 NXT 的股票保持 KRX/NAVER 成交额。
        krx_value = si(q.get('krxTurnoverWon'))
        q['turnoverWon'] = krx_value + nx_value if nx_value > 0 else krx_value
        # V0.9.29：同一最终时点对表确认 Toss 成交量≈NAVER/KRX aq + NXT 当日累计成交量。
        # NXT acctTdQty 为当日累计量；有NXT时使用综合成交量，无NXT时保持NAVER/KRX。
        q['volume'] = si(q.get('krxVolume')) + nx_volume
        merged_nxt += 1

    # V0.9.39：把单日成交额三个口径彻底拆开保存，先诊断，不再猜Toss公式。
    diag_codes = {
        '005930': '삼성전자', '000660': 'SK하이닉스', '402340': 'SK스퀘어', '009150': '삼성전기',
        '034020': '두산에너빌리티', '006400': '삼성SDI', '042700': '한미반도체', '028300': 'HLB',
        '047040': '대우건설', '105560': 'KB금융',
    }
    diag_path = ROOT / 'turnover_raw_diagnostic.csv'
    with diag_path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['日期','股票','代码','NAVER_aq成交量(股)','NXT成交量(股)','当前合并成交量(股)',
                    'NAVER_aa成交额(원)','NXT成交额(원)','当前合并成交额(원)',
                    'NAVER_aa(亿)','NXT(亿)','当前合并(亿)','NAVER_nv价格','NXT价格','最终价格'])
        for dc, dn in diag_codes.items():
            dq = quotes.get(dc, {})
            nx = nxt_official.get(dc, {})
            naverv = si(dq.get('krxTurnoverWon'))
            nxtv = si(nx.get('value'))
            totalv = si(dq.get('turnoverWon'))
            naverq = si(dq.get('krxVolume'))
            nxtq = si(nx.get('volume'))
            totalq = si(dq.get('volume'))
            naverp = si(dq.get('krxPrice', dq.get('price')))
            nxtp = si(nx.get('price'))
            finalp = si(dq.get('price'))
            w.writerow([latest_trade_date, dn, dc, naverq, nxtq, totalq,
                        naverv, nxtv, totalv,
                        round(naverv/1e8,1), round(nxtv/1e8,1), round(totalv/1e8,1),
                        naverp, nxtp, finalp])
            print(f'[V0.9.51量价拆分] {dn} {dc} | NAVER aq={naverq:,}股 / aa={naverv/1e8:.1f}亿 | NXT={nxtq:,}股 / {nxtv/1e8:.1f}亿 | 当前合并={totalq:,}股 / {totalv/1e8:.1f}亿')
    print(f'V0.9.39 成交额原始拆分CSV: {diag_path}')

    # V0.9.41：NXT三时段诊断。正式筛选公式完全不改。
    # 先保存目标股票的官方XHR原始字段，检查接口是否直接提供
    # pre/main/after 等个股分时段字段；同时保存韩国时间，便于后续分时采样。
    kst = timezone(timedelta(hours=9))
    sample_time_kst = datetime.now(kst).strftime('%Y-%m-%d %H:%M:%S KST')
    session_diag = {
        'version': 'V0.9.41',
        'tradeDate': latest_trade_date,
        'sampleTimeKST': sample_time_kst,
        'note': '诊断文件，不改变正式筛选公式。NXT官方正규市场分为盘前/主盘/盘后。',
        'stocks': {}
    }
    session_words = ('pre','main','after','bef','aft','market','mkt','time','tm','qty','val','trval','acc')
    for dc, dn in diag_codes.items():
        nx = nxt_official.get(dc, {})
        raw = nx.get('_raw') if isinstance(nx, dict) else {}
        raw = raw if isinstance(raw, dict) else {}
        likely = {k:v for k,v in raw.items() if any(word in str(k).lower() for word in session_words)}
        session_diag['stocks'][dc] = {
            'name': dn,
            'parsedNxtValueWon': si(nx.get('value')) if isinstance(nx, dict) else 0,
            'parsedNxtVolume': si(nx.get('volume')) if isinstance(nx, dict) else 0,
            'likelySessionFields': likely,
            'allRawFields': raw,
        }
        print(f'[V0.9.41三时段诊断] {dn} {dc} | 采样={sample_time_kst} | NXT累计={si(nx.get("value"))/1e8:.1f}亿 | 候选字段={json.dumps(likely, ensure_ascii=False)[:1800]}')

    session_path = ROOT / 'nxt_session_diagnostic.json'
    session_path.write_text(json.dumps(session_diag, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'V0.9.41 NXT三时段诊断JSON: {session_path}')
    print('[V0.9.41说明] 若原始字段没有直接给出盘前/主盘/盘后个股值，下一步按KST时间点多次采样累计值做差分；本版不会修改筛选成交额。')

    print(f'NXT官方数据成功合并: {merged_nxt}只')
    if merged_nxt < 300:
        raise RuntimeError(f'NXT成功合并仅 {merged_nxt} 只，数量异常，拒绝覆盖正式数据')

    # 先把精确收盘快照写回缓存，防止下一次被历史日线覆盖
    for c, q in quotes.items():
        if c in histories:
            histories[c] = apply_snapshot(histories[c], q)
    save_cache(histories)

    # V0.9.52 交易状态诊断：只诊断，不改变正式股票池。
    # 单日0成交不能直接等同停牌，因此结合最近20个历史交易日和最新NAVER/NXT成交量做保守标记。
    status_path = ROOT / 'trading_status_diagnostic.csv'
    with status_path.open('w', encoding='utf-8-sig', newline='') as sfp:
        sw = csv.writer(sfp)
        sw.writerow(['代码','股票','市场','最新历史日','最新历史成交量','近20日非零成交天数',
                     'NAVER_aq最新成交量','NXT最新成交量','诊断状态','说明'])
        for _, ur in u.iterrows():
            sc = str(ur.get('Code','')).zfill(6)
            sn = str(ur.get('Name', sc)).strip()
            sm = str(ur.get('Market',''))
            hh = histories.get(sc, []) or []
            tail = hh[-20:]
            nz = sum(1 for rr in tail if len(rr) > 5 and si(rr[5]) > 0)
            lastd = tail[-1][0] if tail else ''
            lastv = si(tail[-1][5]) if tail else 0
            qq = quotes.get(sc) or {}
            nq = si(qq.get('krxVolume'))
            xq = si((nxt_official.get(sc) or {}).get('volume'))
            if len(tail) < 20:
                st, note = '历史不足待核验', '不据此删除'
            elif nz == 0 and nq == 0 and xq == 0:
                st, note = '疑似停牌/长期无成交', '需进一步核验交易状态'
            elif nz <= 2 and nq == 0 and xq == 0:
                st, note = '低成交/疑似异常', '可能停牌或极低流动性，需进一步核验'
            else:
                st, note = '有正常成交迹象', ''
            sw.writerow([sc, sn, sm, lastd, lastv, nz, nq, xq, st, note])
    print(f'[V0.9.52交易状态诊断] 已生成: {status_path}')

    res = []
    for c, n, m, mc in jobs:
        x = build(c, n, m, mc, histories.get(c, []), quotes.get(c), toss_marcap_map.get(c, mc))
        if x:
            res.append(x)

    if len(res) < MIN_TOTAL_STOCKS:
        raise RuntimeError(f'最终有效股票只有 {len(res)} 只，拒绝覆盖 stocks_data.js')

    res.sort(key=lambda x: (x['market'], x['code']))
    latest = max(x['history'][-1][0] for x in res)
    if latest_trade_date == expected_date and latest != expected_date:
        raise RuntimeError(f'[V0.9.48] 今日已确认有交易，但最终数据日期仍为 {latest}，拒绝部署旧数据')
    print(f'[V0.9.48最终校验] 目标交易日={latest_trade_date} | 输出交易日={latest}')
    nxt_count = sum(1 for x in res if x.get('quoteSource') == 'NAVER_NXT')
    meta = {
        'latest_date': latest,
        'updated_at': datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S KST'),
        'kospi_count': sum(x['market'] == 'KOSPI' for x in res),
        'kosdaq_count': sum(x['market'] == 'KOSDAQ' for x in res),
        'total_count': len(res),
        'history_days': HISTORY_DAYS,
        'source': 'FinanceDataReader(NAVER history) + NAVER polling(KRX) + NXT official 20:00 close',
        'update_mode': 'V0.9.48 当日交易日强制校验 + 240日缓存增量 + KRX收盘 + NXT官方最终数据',
        'nxt_count': nxt_count,
        'snapshot_rule': 'V0.9.39成交额原始拆分诊断：正式筛选暂保持现有口径；额外保存krxTurnoverWon/nxtTurnoverWon/combinedTurnoverWon，并输出turnover_raw_diagnostic.csv用于与Toss逐日对表',
    }
    payload = 'window.DATA_META=' + json.dumps(meta, ensure_ascii=False, separators=(',', ':')) + ';\nwindow.STOCKS_DATA=' + json.dumps(res, ensure_ascii=False, separators=(',', ':')) + ';\n'
    TMP.write_text(payload, encoding='utf-8')
    TMP.replace(OUT)
    print('更新完成，有效股票:', len(res), 'NXT股票:', nxt_count, '历史上限:', HISTORY_DAYS)


if __name__ == '__main__':
    main()
