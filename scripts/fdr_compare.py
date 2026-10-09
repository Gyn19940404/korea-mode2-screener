#!/usr/bin/env python3
"""Independent FinanceDataReader historical volume comparison; diagnostic only."""
import json
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
    "| 股票 | FDR成交量 | KRX官方量 | KRX+NXT量 | Toss截图量 | FDR-KRX | FDR-综合 |",
    "|---|---:|---:|---:|---:|---:|---:|",
]
errors = []
for code, (name, krx, nxt, toss) in STOCKS.items():
    try:
        frame = fdr.DataReader(code, DATE, END)
        if frame is None or frame.empty:
            raise ValueError("returned empty dataframe")
        dated = frame.loc[frame.index.strftime("%Y-%m-%d") == DATE]
        if dated.empty:
            raise ValueError("requested trading date missing")
        volume = int(dated.iloc[-1]["Volume"])
        lines.append(f"| {name} ({code}) | {volume:,} | {krx:,} | {krx+nxt:,} | {toss:,} | {volume-krx:+,} | {volume-krx-nxt:+,} |")
    except Exception as exc:
        errors.append(f"{code}: {type(exc).__name__}: {exc}")
        lines.append(f"| {name} ({code}) | 读取失败 | {krx:,} | {krx+nxt:,} | {toss:,} | - | - |")
lines.extend(["", f"成功：{len(STOCKS)-len(errors)}/{len(STOCKS)}"])
if errors:
    lines.extend(["", "## 失败详情", *[f"- {e}" for e in errors]])
lines.append("")
lines.append("判读：与KRX数值接近不等于官方独立来源；与Toss接近也不证明统计口径一致。")
Path("fdr_comparison_report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
print("\n".join(lines))
if errors:
    sys.exit(1)
