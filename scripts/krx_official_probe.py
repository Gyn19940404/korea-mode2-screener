#!/usr/bin/env python3
import os, io, json, requests, pandas as pd
from pathlib import Path

DATE=os.environ.get("TRADE_DATE","20261007")
BASE="https://data.krx.co.kr"
GEN=BASE+"/comm/fileDn/GenerateOTP/generate.cmd"
DOWN=BASE+"/comm/fileDn/download_csv/download.cmd"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36"

s=requests.Session()
s.headers.update({"User-Agent":UA,"Referer":BASE+"/contents/MDC/MAIN/main/index.cmd","Origin":BASE})
payload={
 "locale":"ko_KR","mktId":"ALL","trdDd":DATE,
 "share":"1","money":"1","csvxls_isNo":"false",
 "name":"fileDown","url":"dbms/MDC/STAT/standard/MDCSTAT01501"
}
print("KRX 12001 AUTO CSV PROBE V3")
print("TARGET_DATE",DATE)
r=s.post(GEN,data=payload,timeout=30)
print("OTP_HTTP",r.status_code,"bytes",len(r.content),"prefix",r.text[:80].replace("\n"," "))
r.raise_for_status()
otp=r.text.strip()
if not otp or "LOGOUT" in otp.upper() or len(otp)<10:
    raise RuntimeError("OTP生成失败: "+otp[:200])
d=s.post(DOWN,data={"code":otp},headers={"Referer":GEN},timeout=30)
print("CSV_HTTP",d.status_code,"bytes",len(d.content),"type",d.headers.get("content-type"))
d.raise_for_status()
if len(d.content)<10000 or b"LOGOUT" in d.content[:500].upper():
    raise RuntimeError("CSV下载异常")
df=None
for enc in ("euc-kr","cp949","utf-8-sig"):
    try:
        df=pd.read_csv(io.BytesIO(d.content),encoding=enc)
        print("ENCODING",enc)
        break
    except Exception:
        pass
if df is None: raise RuntimeError("CSV解析失败")
print("ROWS",len(df))
print("COLUMNS",list(df.columns))
required={"종목코드","종목명","시장구분","종가","등락률","시가","고가","저가","거래량","거래대금","시가총액"}
missing=required-set(df.columns)
if missing: raise RuntimeError("缺字段: "+str(sorted(missing)))
if len(df)<2000: raise RuntimeError(f"股票数异常: {len(df)}")
Path(".data_cache").mkdir(exist_ok=True)
Path(".data_cache/krx_12001_probe.csv").write_bytes(d.content)
Path(".data_cache/krx_12001_probe.json").write_text(json.dumps({
 "date":DATE,"rows":len(df),"columns":list(df.columns)
},ensure_ascii=False,indent=2),encoding="utf-8")
print("PASS: KRX 12001 CSV 自动下载成功")
