#!/usr/bin/env python3
import os, json, time
from pathlib import Path
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

DATE=os.environ.get("TRADE_DATE","20261007")
BASE="https://data.krx.co.kr"
PAGE=BASE+"/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101"
DOWNLOAD=Path.cwd()/".data_cache/krx_browser_download"
DOWNLOAD.mkdir(parents=True,exist_ok=True)

print("KRX 12001 BROWSER DOWNLOAD PROBE V4")
print("TARGET_DATE",DATE)

opt=webdriver.ChromeOptions()
opt.add_argument("--headless=new")
opt.add_argument("--no-sandbox")
opt.add_argument("--disable-dev-shm-usage")
opt.add_argument("--window-size=1920,1080")
opt.add_experimental_option("prefs",{
 "download.default_directory":str(DOWNLOAD),
 "download.prompt_for_download":False,
 "download.directory_upgrade":True,
 "safebrowsing.enabled":True,
})

driver=webdriver.Chrome(options=opt)
try:
    driver.get(PAGE)
    wait=WebDriverWait(driver,30)
    wait.until(lambda d:d.execute_script("return document.readyState")=="complete")
    time.sleep(5)
    print("PAGE_TITLE",driver.title)
    print("PAGE_URL",driver.current_url)
    print("COOKIES",len(driver.get_cookies()))

    # KRX MDI页面是动态装载的；优先按常见查询日期控件寻找。
    date_el=None
    for sel in ["input[name='trdDd']","#trdDd","input.hasDatepicker"]:
        els=driver.find_elements(By.CSS_SELECTOR,sel)
        if els:
            date_el=els[0]; print("DATE_SELECTOR",sel); break
    if date_el is None:
        raise RuntimeError("未找到查询日期控件")

    driver.execute_script("arguments[0].removeAttribute('readonly');",date_el)
    date_el.clear(); date_el.send_keys(DATE)

    # 点击“조회”
    clicked=False
    for xp in ["//button[contains(.,'조회')]","//a[contains(.,'조회')]","//*[@id='jsSearchButton']"]:
        els=driver.find_elements(By.XPATH,xp)
        if els:
            driver.execute_script("arguments[0].click();",els[0]); clicked=True; print("SEARCH_CLICK",xp); break
    if not clicked: raise RuntimeError("未找到조회按钮")
    time.sleep(5)

    # 点击CSV/下载按钮。先找页面中带 download/csv 的元素，再找图标按钮。
    candidates=driver.find_elements(By.CSS_SELECTOR,"a,button")
    dl=None
    for e in candidates:
        try:
            txt=(e.text or "").strip().lower()
            title=(e.get_attribute("title") or "").lower()
            cls=(e.get_attribute("class") or "").lower()
            if "csv" in txt or "csv" in title or "download" in cls or "다운로드" in txt or "다운로드" in title:
                dl=e; print("DOWNLOAD_ELEMENT",txt,title,cls); break
        except Exception: pass
    if dl is None:
        raise RuntimeError("未找到CSV/下载按钮")
    driver.execute_script("arguments[0].click();",dl)

    deadline=time.time()+30
    files=[]
    while time.time()<deadline:
        files=[x for x in DOWNLOAD.iterdir() if x.is_file() and not x.name.endswith(".crdownload")]
        if files: break
        time.sleep(1)
    if not files: raise RuntimeError("点击下载后30秒内没有生成文件")
    f=max(files,key=lambda x:x.stat().st_mtime)
    print("DOWNLOADED",f.name,"bytes",f.stat().st_size)

    # 校验下载内容
    df=None
    if f.suffix.lower()==".csv":
        for enc in ("euc-kr","cp949","utf-8-sig"):
            try:
                df=pd.read_csv(f,encoding=enc); print("ENCODING",enc); break
            except Exception: pass
    elif f.suffix.lower() in (".xls",".xlsx"):
        df=pd.read_excel(f)
    if df is None: raise RuntimeError("下载文件无法解析")
    print("ROWS",len(df))
    print("COLUMNS",list(df.columns))
    if len(df)<2000: raise RuntimeError(f"股票数异常: {len(df)}")

    Path(".data_cache/krx_12001_browser_probe.json").write_text(json.dumps({
      "date":DATE,"rows":len(df),"file":f.name,"columns":list(df.columns)
    },ensure_ascii=False,indent=2),encoding="utf-8")
    print("PASS: Chrome自动进入KRX并下载全市场文件成功")
finally:
    driver.quit()
