#!/usr/bin/env python3
"""Diagnostic-only, dated comparison of site's 1-day turnover rule vs verified 20-day gate.
This is a Python mirror, NOT a browser DOM test or a production data patch.
"""
import csv
import json
import math
import runpy
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ctx = runpy.run_path(str(ROOT / "scripts" / "mode2_exact_filter_audit.py"))
stocks = ctx["stocks"]
cfg = ctx["cfg"]
check = ctx["check"]
finite = ctx["finite"]
bycode = ctx["bycode"]
meta = ctx["meta"]
asof = ctx["asof"]
gate_rows = ctx["gate"]
out = ROOT / "mode2_rule_comparison"
out.mkdir(exist_ok=True)
gate = {r["code"]: r for r in gate_rows}
if len(gate) != len(gate_rows):
    raise SystemExit("Duplicate gate codes")
if not cfg["turnOn"] or int(cfg["turnDays"]) != 1 or cfg["minturn20"] != 500:
    raise SystemExit("Default webpage turnover settings changed: refuse misleading comparison")
if len({r["asof_date"] for r in gate_rows}) != 1 or meta.get("latest_date", "").replace("-", "") != asof:
    raise SystemExit("Snapshot dates mismatch")
if len(stocks) != len(bycode):
    raise SystemExit("Duplicate site stock codes")
# Gate may include delisted and other stocks not on site. Keep distinct populations.
rows = []
counts = Counter()
for s in stocks:
    code = str(s.get("code", "")).zfill(6)
    reasons, chg, daily_turn = check(s)
    default_pass = not reasons
    g = gate.get(code)
    status = g["gate"] if g else "NO_GATE_RECORD"
    # Keep all non-turnover conditions from the independently mirrored site.
    non_turn_reasons = [r for r in reasons if r != "TURNOVER"]
    other_pass = not non_turn_reasons
    # UNKNOWN must not be silently converted to FAIL.
    gate_candidate = "PASS" if other_pass and status == "PASS" else (
        "UNKNOWN" if other_pass and status in ("UNKNOWN", "NO_GATE_RECORD") else "BLOCKED"
    )
    if default_pass:
        counts["SITE_DEFAULT_PASS"] += 1
        counts["DEFAULT_AND_" + status] += 1
    if gate_candidate == "PASS":
        counts["GATE_20D_CANDIDATE"] += 1
    if gate_candidate == "UNKNOWN":
        counts["GATE_20D_UNKNOWN"] += 1
    if other_pass:
        counts["OTHER_CONDITIONS_PASS"] += 1
    if default_pass and status == "PASS":
        counts["BOTH_PASS"] += 1
    if default_pass and status != "PASS":
        counts["DEFAULT_PASS_NOT_20D_VERIFIED"] += 1
    if gate_candidate == "PASS" and not default_pass:
        counts["20D_PASS_NOT_DEFAULT"] += 1
    rows.append({
        "asof": asof, "code": code, "name": s.get("name", ""),
        "site_default_1d_pass": "YES" if default_pass else "NO",
        "site_default_reasons": ";".join(reasons),
        "other_conditions_pass": "YES" if other_pass else "NO",
        "daily_turnover_100m_site": daily_turn if math.isfinite(daily_turn) else "",
        "20d_gate": status,
        "20d_basis": g["basis"] if g else "",
        "krx_days": g["krx_days"] if g else "",
        "nxt_observed_days": g["nxt_observed_days"] if g else "",
        "20d_gate_candidate": gate_candidate,
    })
with (out / "stock_by_stock_comparison.csv").open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
def sample(predicate):
    selected = [r for r in rows if predicate(r)]
    return ", ".join(f'{r["name"]}({r["code"]})' for r in selected[:30]) or "无"
report = [
    "# 模式2第十一阶段：同日1日成交额与官方20日成交额门槛逐股对照（不发布）",
    f"交易日：{asof}；网站股票池：{len(stocks)}；门槛审计股票池：{len(gate)}",
    f"网页默认1日成交额筛选通过：{counts['SITE_DEFAULT_PASS']}",
    f"其余网页条件（不含成交额）通过：{counts['OTHER_CONDITIONS_PASS']}",
    f"网页默认通过且20日门槛PASS：{counts['BOTH_PASS']}",
    f"网页默认通过但20日门槛未验证PASS：{counts['DEFAULT_PASS_NOT_20D_VERIFIED']}",
    f"20日门槛PASS且其他条件通过：{counts['GATE_20D_CANDIDATE']}",
    f"20日门槛PASS且其他条件通过、但网页默认未通过：{counts['20D_PASS_NOT_DEFAULT']}",
    f"其他条件通过但20日门槛UNKNOWN/无记录：{counts['GATE_20D_UNKNOWN']}",
    "",
    "## 网页默认通过股票（最多30只）",
    sample(lambda r: r["site_default_1d_pass"] == "YES"),
    "",
    "## 网页默认通过、但20日门槛非PASS（最多30只）",
    sample(lambda r: r["site_default_1d_pass"] == "YES" and r["20d_gate"] != "PASS"),
    "",
    "## 20日门槛PASS、其他条件通过、但1日默认未通过（最多30只）",
    sample(lambda r: r["20d_gate_candidate"] == "PASS" and r["site_default_1d_pass"] == "NO"),
    "",
    "注意：PASS_BY_KRX_LOWER_BOUND只证明达到门槛，不代表综合20日平均成交额的精确数值。",
    "UNKNOWN及无记录保持未知；不能当成零成交或不达标。",
    "本报告是Python独立镜像，不是浏览器DOM运行；需进一步核对浏览器实际11只名单。",
    "本任务不修改网站、不发布、不下单；网页历史成交额不得与官方审计成交额混为一谈。",
]
(out / "audit_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
(out / "counts.json").write_text(json.dumps(dict(counts), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("\n".join(report))
