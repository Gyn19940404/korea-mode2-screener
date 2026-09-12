# -*- coding: utf-8 -*-
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import FinanceDataReader as fdr

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "stocks_data.js"
TMP = ROOT / "stocks_data.tmp.js"

HISTORY_DAYS = 80
MAX_WORKERS = 10
RETRIES = 3

def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default

def safe_int(v, default=0):
    try:
        if v is None:
            return default
        return int(float(v))
    except Exception:
        return default

def get_universe():
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        df = fdr.StockListing(market).copy()
        if df is None or df.empty:
            raise RuntimeError(f"{market} 股票列表为空")
        df["Market"] = market
        frames.append(df)

    import pandas as pd
    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.drop_duplicates(subset=["Code"])
    return all_df

def fetch_one(code, name, market, marcap):
    end = datetime.now()
    start = end - timedelta(days=150)

    last_err = None
    for attempt in range(RETRIES):
        try:
            df = fdr.DataReader(f"NAVER:{code}", start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
            if df is None or df.empty or len(df) < 20:
                raise RuntimeError("历史数据不足")
            df = df.tail(HISTORY_DAYS)

            hist = []
            for idx, row in df.iterrows():
                close = safe_int(row.get("Close", 0))
                if close <= 0:
                    continue
                volume = safe_int(row.get("Volume", 0))
                open_ = safe_int(row.get("Open", 0))
                high = safe_int(row.get("High", 0))
                low = safe_int(row.get("Low", 0))
                turnover = close * volume  # Naver daily data does not directly provide 거래대금
                hist.append([
                    idx.strftime("%Y-%m-%d"),
                    open_, high, low, close, volume, turnover
                ])

            if len(hist) < 20:
                raise RuntimeError("有效历史数据不足20日")

            closes = [x[4] for x in hist]
            price = closes[-1]
            prev = closes[-2]
            daychg = (price / prev - 1) * 100 if prev else 0

            ma5 = sum(closes[-5:]) / 5
            ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else ma5
            ma20 = sum(closes[-20:]) / 20
            ma30 = sum(closes[-30:]) / 30 if len(closes) >= 30 else ma20
            ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else ma30

            dist = (price / ma5 - 1) * 100 if ma5 else 0
            week_base = closes[-6] if len(closes) >= 6 else closes[0]
            week = (price / week_base - 1) * 100 if week_base else 0

            turns20 = [x[6] / 1e8 for x in hist[-20:]]
            min_turn20 = min(turns20)
            avg_turn20 = sum(turns20) / len(turns20)
            turnover = hist[-1][6] / 1e8

            streak = 0
            for i in range(len(closes)-1, 0, -1):
                if closes[i] > closes[i-1]:
                    streak += 1
                else:
                    break

            trend_score = 25 if ma5 >= ma10 >= ma20 else (15 if ma5 >= ma10 else 5)
            position_score = max(0, 25 - max(0, abs(dist) - 0.2) * 8)
            strength_score = min(18, max(0, week) * 1.5)
            liquidity_score = min(12, avg_turn20 / 50)
            score = round(min(85, trend_score + position_score + strength_score + liquidity_score + 5))
            grade = "A" if score >= 80 else ("B" if score >= 68 else "C")

            return {
                "name": name,
                "code": code,
                "sector": "板块待接入",
                "market": market,
                "marketCapTrillion": marcap / 1e12 if marcap else 0,
                "history": hist,
                "price": price,
                "daychg": daychg,
                "week": week,
                "turnover": turnover,
                "avgturn": avg_turn20,
                "minTurn20": min_turn20,
                "streak": streak,
                "ma5": ma5,
                "ma10": ma10,
                "ma20": ma20,
                "ma30": ma30,
                "ma60": ma60,
                "dist": dist,
                "rank": 0,
                "sectorPower": 0,
                "score": score,
                "grade": grade
            }
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    print(f"[失败] {code} {name}: {last_err}")
    return None

def main():
    print("读取 KOSPI + KOSDAQ 股票列表...")
    universe = get_universe()
    print("股票总数:", len(universe))

    jobs = []
    for _, row in universe.iterrows():
        code = str(row.get("Code", "")).zfill(6)
        name = str(row.get("Name", code))
        market = str(row.get("Market", ""))
        marcap = safe_int(row.get("Marcap", row.get("MarketCap", 0)))
        jobs.append((code, name, market, marcap))

    results = []
    print("并发获取历史行情...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = [ex.submit(fetch_one, *j) for j in jobs]
        for i, fut in enumerate(as_completed(futs), 1):
            item = fut.result()
            if item:
                results.append(item)
            if i % 100 == 0:
                print(f"进度 {i}/{len(futs)}，成功 {len(results)}")

    if len(results) < 1200:
        raise RuntimeError(f"成功股票只有 {len(results)} 只，低于安全阈值，拒绝覆盖旧数据")

    results.sort(key=lambda x: (x["market"], x["code"]))
    latest_date = max(x["history"][-1][0] for x in results if x["history"])

    meta = {
        "latest_date": latest_date,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kospi_count": sum(1 for x in results if x["market"] == "KOSPI"),
        "kosdaq_count": sum(1 for x in results if x["market"] == "KOSDAQ"),
        "total_count": len(results),
        "history_days": HISTORY_DAYS,
        "source": "FinanceDataReader + NAVER"
    }

    payload = (
        "window.DATA_META=" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + ";\n" +
        "window.STOCKS_DATA=" + json.dumps(results, ensure_ascii=False, separators=(",", ":")) + ";\n"
    )

    TMP.write_text(payload, encoding="utf-8")
    TMP.replace(OUT)
    print("更新完成:", OUT)
    print("有效股票:", len(results))

if __name__ == "__main__":
    main()
