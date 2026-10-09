#!/usr/bin/env python3
"""Independent FinanceDataReader historical volume comparison; diagnostic only."""
import sys
from datetime import datetime
from pathlib import Path

import FinanceDataReader as fdr

DATE = "2026-10-08"
END = "2026-10-09"  # exclusive for date-range readers
STOCKS = {
    "005930": ("三星电子", 21259056, 9091993, 30352099),
    "000660": ("SK海力士", 3203231, 1170235, 4373490),
    "034020": ("斗山能源", 4584327, 3148448, 7732776),
    "009150": ("三星电机", 554370, 343370, 897747),
    "373220": ("LG新能源", 919337, 459818, 1379171),
    "005380": ("现代汽车", 685775, 415395, 1101179),
}
lines = [
    "# FinanceDataReader 独立成交量对照",
    f"目标交易日：{DATE} | 测试时间(UTC)：{datetime.utcnow().isoformat(timespec='seconds')}Z",
    "",
    "本报告仅作诊断；FDR数据来源和KRX/NXT/Toss统计口径可能不同，不自动修改正式网页。",
    "",
    "| 股票 | FDR默认 | FDR NAVER | FDR KRX | 官方KRX | 官方NXT | Toss | 默认-KRX | FDR KRX-官方KRX |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
]
errors = []
def read_volume(symbol, code):
    # KRX backend may reject very short periods; query a wider window, then select DATE.
    start = "2026-09-01" if symbol.startswith("KRX:") else DATE
    frame = fdr.DataReader(symbol, start, END)
    if frame is None or frame.empty:
        raise ValueError("empty dataframe")
    dated = frame.loc[frame.index.strftime("%Y-%m-%d") == DATE]
    if dated.empty:
        raise ValueError("requested date missing")
    return int(dated.iloc[-1]["Volume"])

for code, (name, krx, nxt, toss) in STOCKS.items():
    vals = {}
    for label, symbol in [("default", code), ("NAVER", f"NAVER:{code}"), ("KRX", f"KRX:{code}")]:
        try:
            vals[label] = read_volume(symbol, code)
        except Exception as exc:
            vals[label] = None
            errors.append(f"{code} {label}: {type(exc).__name__}: {exc}")
    def fmt(x):
        return f"{x:,}" if x is not None else "失败"
    def diff(x, y):
        return f"{x-y:+,}" if x is not None else "-"
    lines.append(f"| {name} ({code}) | {fmt(vals['default'])} | {fmt(vals['NAVER'])} | {fmt(vals['KRX'])} | {krx:,} | {nxt:,} | {toss:,} | {diff(vals['default'], krx)} | {diff(vals['KRX'], krx)} |")
lines.extend(["", f"全部来源成功股票数：{len(STOCKS)-len({e.split()[0] for e in errors})}/{len(STOCKS)}"])
if errors:
    lines.extend(["", "## 失败详情", *[f"- {e}" for e in errors]])
lines.append("")
lines.append("判读：FDR KRX和FDR NAVER是否可用取决于安装版本及上游接口。数值差异不能直接证明盘前/盘后遗漏；需取得按交易时段拆分的官方成交量才能归因。")
Path("fdr_comparison_report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
print("\n".join(lines))
if errors:
    print("PARTIAL RESULT: some FDR sources unavailable; report preserved for investigation")
