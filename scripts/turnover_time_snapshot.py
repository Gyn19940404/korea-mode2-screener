#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V0.9.42 - 08:50 / 15:30 / 20:05 KST 成交额快照诊断。
只写 .data_cache/nxt_time_samples.json 并打印日志，不修改 stocks_data.js。
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from update_data import fetch_quote, fetch_nxt_official, si

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / '.data_cache'
OUT = CACHE_DIR / 'nxt_time_samples.json'
KST = timezone(timedelta(hours=9))
TARGETS = {
    '005930': '삼성전자',
    '000660': 'SK하이닉스',
    '402340': 'SK스퀘어',
    '009150': '삼성전기',
    '034020': '두산에너빌리티',
    '006400': '삼성SDI',
    '042700': '한미반도체',
    '028300': 'HLB',
}

def slot_label(dt):
    hm = dt.hour * 60 + dt.minute
    if hm < 9 * 60:
        return '盘前结束附近'
    if hm < 16 * 60:
        return 'KRX收盘附近'
    return 'NXT盘后结束附近'

def main():
    now = datetime.now(KST)
    trade_date = now.strftime('%Y-%m-%d')
    sampled_at = now.strftime('%Y-%m-%d %H:%M:%S KST')
    slot = slot_label(now)
    print(f'[V0.9.42快照] 开始 | {sampled_at} | {slot}')

    # NXT接口需要交易日；若休市/无数据则明确失败，不影响正式网页。
    nxt = fetch_nxt_official(trade_date)
    rows = []
    for code, name in TARGETS.items():
        q = fetch_quote(code) or {}
        nx = nxt.get(code, {}) if isinstance(nxt, dict) else {}
        krx_val = si(q.get('krxTurnoverWon'))
        nxt_val = si(nx.get('value'))
        krx_vol = si(q.get('krxVolume'))
        nxt_vol = si(nx.get('volume'))
        row = {
            'tradeDate': trade_date,
            'sampleTimeKST': sampled_at,
            'slot': slot,
            'code': code,
            'name': name,
            'naverKrxPrice': si(q.get('price')),
            'naverKrxTurnoverWon': krx_val,
            'naverKrxVolume': krx_vol,
            'nxtPrice': si(nx.get('price')),
            'nxtTurnoverWon': nxt_val,
            'nxtVolume': nxt_vol,
            'simpleSumWon': krx_val + nxt_val,
        }
        rows.append(row)
        print(
            f'[V0.9.42快照] {name} {code} | {slot} | '
            f'NAVER/KRX={krx_val/1e8:.1f}亿 | NXT累计={nxt_val/1e8:.1f}亿 | '
            f'简单相加={(krx_val+nxt_val)/1e8:.1f}亿 | '
            f'KRX价={row["naverKrxPrice"]} | NXT价={row["nxtPrice"]}'
        )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {'version': 'V0.9.42', 'samples': []}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding='utf-8'))
            if isinstance(old, dict) and isinstance(old.get('samples'), list):
                data = old
        except Exception:
            pass
    data['version'] = 'V0.9.42'
    data['samples'].append({'sampleTimeKST': sampled_at, 'slot': slot, 'stocks': rows})
    data['samples'] = data['samples'][-30:]
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[V0.9.42快照] 已保存 {OUT} | 累计快照={len(data["samples"])}组')

    # 同日已有多个时间点时，直接打印差分，方便截图对比。
    today = [s for s in data['samples'] if str(s.get('sampleTimeKST','')).startswith(trade_date)]
    if len(today) >= 2:
        print(f'[V0.9.42差分] {trade_date} 已有 {len(today)} 个时间点')
        for code, name in TARGETS.items():
            vals=[]
            for s in today:
                rr=next((r for r in s.get('stocks',[]) if r.get('code')==code), None)
                if rr:
                    vals.append((s.get('sampleTimeKST'), s.get('slot'), rr.get('naverKrxTurnoverWon',0), rr.get('nxtTurnoverWon',0)))
            if len(vals)>=2:
                first,last=vals[0],vals[-1]
                print(f'[V0.9.42差分] {name} | NAVER/KRX增量={(last[2]-first[2])/1e8:.1f}亿 | NXT增量={(last[3]-first[3])/1e8:.1f}亿 | {first[1]} -> {last[1]}')

if __name__ == '__main__':
    main()
