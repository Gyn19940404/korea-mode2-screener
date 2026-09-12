# -*- coding: utf-8 -*-
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from pykrx import stock

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "stocks_data.js"
NEEDED_DAYS = 65

def nearest_business_day():
    today = datetime.now().strftime("%Y%m%d")
    try:
        return stock.get_nearest_business_day_in_a_week(today, prev=True)
    except Exception:
        d = datetime.now()
        for _ in range(14):
            ds = d.strftime("%Y%m%d")
            try:
                df = stock.get_market_ohlcv_by_ticker(ds, market="KOSPI")
                if df is not None and not df.empty:
                    return ds
            except Exception:
                pass
            d -= timedelta(days=1)
        raise RuntimeError("无法找到最近交易日")

def collect_days(latest, needed=NEEDED_DAYS):
    days = []
    frames = {}
    d = datetime.strptime(latest, "%Y%m%d")
    attempts = 0
    while len(days) < needed and attempts < 160:
        attempts += 1
        if d.weekday() < 5:
            ds = d.strftime("%Y%m%d")
            try:
                df = stock.get_market_ohlcv_by_ticker(ds, market="ALL")
                if df is not None and not df.empty and "종가" in df.columns and (df["종가"] > 0).sum() > 1000:
                    frames[ds] = df
                    days.append(ds)
                    print(f"{len(days):02d}/{needed} {ds} {len(df):,} rows")
                time.sleep(0.12)
            except Exception as e:
                print("skip", ds, e)
                time.sleep(0.8)
        d -= timedelta(days=1)

    if len(days) < 20:
        raise RuntimeError(f"有效交易日只有 {len(days)} 天")
    days.sort()
    return days, frames

def safe_int(v):
    try:
        if v is None:
            return 0
        return int(v)
    except Exception:
        return 0

def main():
    latest = nearest_business_day()
    print("latest:", latest)

    kospi = set(stock.get_market_ticker_list(latest, market="KOSPI"))
    kosdaq = set(stock.get_market_ticker_list(latest, market="KOSDAQ"))
    tickers = sorted(kospi | kosdaq)
    print("tickers:", len(tickers))

    names = {}
    for i, ticker in enumerate(tickers, 1):
        try:
            names[ticker] = stock.get_market_ticker_name(ticker) or ticker
        except Exception:
            names[ticker] = ticker
        if i % 500 == 0:
            print("names:", i)

    days, frames = collect_days(latest)

    cap = stock.get_market_cap_by_ticker(latest, market="ALL")
    histories = {t: [] for t in tickers}

    for ds in days:
        df = frames[ds]
        for t in tickers:
            if t not in df.index:
                continue
            r = df.loc[t]
            close = safe_int(r.get("종가", 0))
            if close <= 0:
                continue
            histories[t].append([
                f"{ds[:4]}-{ds[4:6]}-{ds[6:]}",
                safe_int(r.get("시가", 0)),
                safe_int(r.get("고가", 0)),
                safe_int(r.get("저가", 0)),
                close,
                safe_int(r.get("거래량", 0)),
                safe_int(r.get("거래대금", 0))
            ])

    result = []
    for t in tickers:
        h = histories.get(t, [])
        if len(h) < 20:
            continue

        closes = [x[4] for x in h]
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

        turns20 = [x[6] / 1e8 for x in h[-20:]]
        min_turn20 = min(turns20)
        avg_turn20 = sum(turns20) / len(turns20)
        turnover = h[-1][6] / 1e8

        streak = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                streak += 1
            else:
                break

        mcap = 0
        try:
            if t in cap.index and "시가총액" in cap.columns:
                mcap = safe_int(cap.loc[t, "시가총액"])
        except Exception:
            mcap = 0

        # V0.8 still uses the existing individual-stock score.
        # Sector strength / leader score is intentionally postponed to V0.9.
        trend_score = 25 if ma5 >= ma10 >= ma20 else (15 if ma5 >= ma10 else 5)
        position_score = max(0, 25 - max(0, abs(dist) - 0.2) * 8)
        strength_score = min(18, max(0, week) * 1.5)
        liquidity_score = min(12, avg_turn20 / 50)
        score = round(min(85, trend_score + position_score + strength_score + liquidity_score + 5))
        grade = "A" if score >= 80 else ("B" if score >= 68 else "C")

        result.append({
            "name": names.get(t, t),
            "code": t,
            "sector": "板块待接入",
            "market": "KOSPI" if t in kospi else "KOSDAQ",
            "marketCapTrillion": mcap / 1e12,
            "history": h,
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
        })

    if len(result) < 1500:
        raise RuntimeError(f"全市场有效股票只有 {len(result)} 只，疑似抓取不完整，拒绝发布")

    meta = {
        "latest_date": f"{latest[:4]}-{latest[4:6]}-{latest[6:]}",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kospi_count": sum(1 for x in result if x["market"] == "KOSPI"),
        "kosdaq_count": sum(1 for x in result if x["market"] == "KOSDAQ"),
        "total_count": len(result),
        "history_days": len(days),
        "source": "KRX via pykrx"
    }

    payload = (
        "window.DATA_META=" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + ";\n" +
        "window.STOCKS_DATA=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")) + ";\n"
    )
    OUT.write_text(payload, encoding="utf-8")
    print("written:", OUT, "stocks:", len(result))

if __name__ == "__main__":
    main()
