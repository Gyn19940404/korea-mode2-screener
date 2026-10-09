#!/usr/bin/env python3
"""Diagnostic: separate NXT observation coverage from unverified eligibility.
No trading, no website updates, no inference of zero turnover.
"""
import csv
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "merged_240d_audit" / "krx_nxt_daily_turnover.csv"
OUT = ROOT / "nxt_eligibility_diagnostic"
OUT.mkdir(exist_ok=True)

def read(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

raw = read(SOURCE)
# The threshold universe includes historical-only codes absent from the latest 20-day slice.
# Rebuild it from this exact downloaded snapshot rather than mixing run artifacts.
all_history = defaultdict(set)
for record in raw:
    all_history[record["code"]].add(record["date"].replace("-", ""))
dates = sorted({r["date"].replace("-", "") for r in raw})
if len(dates) != 240:
    raise SystemExit(f"Expected 240 KRX sessions, got {len(dates)}")
window = dates[-20:]
history = defaultdict(dict)
for r in raw:
    d = r["date"].replace("-", "")
    if d not in window:
        continue
    code = r["code"]
    if d in history[code]:
        raise SystemExit(f"Duplicate code/day: {code}/{d}")
    k = int(r["krx_turnover_won"])
    n = int(r["nxt_turnover_won"]) if r["nxt_turnover_won"] else None
    if k < 0 or (n is not None and n < 0):
        raise SystemExit(f"Negative turnover: {code}/{d}")
    history[code][d] = (k, n)

prior_path = ROOT / "turnover_evidence_20d" / "stock_evidence.csv"
prior = {r["code"]: r for r in read(prior_path)} if prior_path.exists() else {}
codes = sorted(set(all_history) | set(history) | set(prior))
recent_only = sorted(set(history) - set(all_history))
historic_only = sorted(set(all_history) - set(history))
if recent_only:
    raise SystemExit(f"Unexpected recent-only codes: {recent_only[:10]}")
rows = []
stats = Counter()
threshold = 50_000_000_000
exact_complete = Counter()
krx_proven = Counter()
complete_krx_pass = 0
boundary_rows = []
combined_only_rows = []
combined_only_daily = []
for code in codes:
    h = history.get(code, {})
    kdays = sum(d in h for d in window)
    ndays = sum(d in h and h[d][1] is not None for d in window)
    krx_avg = sum(h[d][0] for d in window if d in h) // 20 if kdays == 20 else None
    if kdays < 20:
        coverage = "KRX_HISTORY_SHORT"
    elif ndays == 20:
        coverage = "NXT_REPORTED_ALL_20"
    elif ndays == 0:
        coverage = "NXT_NOT_REPORTED_ANY_20"
    else:
        coverage = "NXT_REPORTED_SOME_20"
    if kdays == 20 and krx_avg >= 50_000_000_000:
        bound = "PASS_BY_KRX_ALONE"
    elif kdays == 20:
        bound = "KRX_ALONE_INSUFFICIENT"
    else:
        bound = "KRX_HISTORY_INSUFFICIENT"
    # The 20-day sum is exact only when both venue observations exist for every day.
    # For missing NXT rows, observed turnover is a lower bound, never a zero fill.
    observed_sum = sum(k + (n if n is not None else 0) for k, n in h.values())
    observed_lower_bound_avg = observed_sum // 20 if kdays == 20 else None
    complete_avg = observed_lower_bound_avg if kdays == 20 and ndays == 20 else None
    if complete_avg is not None and krx_avg >= threshold:
        complete_krx_pass += 1
    if kdays == 20 and (krx_avg >= threshold or (complete_avg is not None and complete_avg >= threshold)):
        boundary_rows.append([code, coverage, bound, krx_avg, "" if complete_avg is None else complete_avg,
                              "" if complete_avg is None else complete_avg - threshold,
                              "KRX_LOWER_BOUND_PASS" if krx_avg >= threshold else "COMBINED_ONLY_PASS"])
    if complete_avg is not None and complete_avg >= threshold and krx_avg < threshold:
        combined_only_rows.append([code, krx_avg, complete_avg, sum(h[d][1] for d in window) // 20,
                                   complete_avg - threshold, ndays, kdays])
        for day in window:
            k, n = h[day]
            if n is None:
                raise SystemExit(f"Unexpected missing NXT day for combined-only PASS: {code}/{day}")
            combined_only_daily.append([code, day, k, n, k + n])
    if complete_avg is not None:
        exact_complete["PASS" if complete_avg >= threshold else "FAIL"] += 1
    if kdays == 20 and krx_avg >= threshold:
        krx_proven[coverage] += 1
    # A non-reported NXT record is not evidence of ineligibility or zero turnover.
    eligibility = "NOT_ESTABLISHED"
    stats[(coverage, bound)] += 1
    missing = [d for d in window if d not in h or h[d][1] is None]
    rows.append([code, coverage, eligibility, bound, kdays, ndays,
                 "" if krx_avg is None else krx_avg,
                 ";".join(missing), "",
                 "" if observed_lower_bound_avg is None else observed_lower_bound_avg,
                 "" if complete_avg is None else complete_avg,
                 "COMPLETE_OBSERVATIONS_NOT_ELIGIBILITY_PROOF" if complete_avg is not None else "INCOMPLETE_OR_UNVERIFIED"])
with (OUT / "eligibility_coverage_20d.csv").open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["code", "nxt_coverage", "nxt_eligibility", "krx_lower_bound",
                "krx_days", "nxt_reported_days", "krx_avg20_won",
                "nxt_unreported_dates", "authoritative_eligibility_source",
                "observed_avg20_lower_bound_won", "complete_observation_avg20_won",
                "turnover_observation_basis"])
    w.writerows(rows)

with (OUT / "turnover_pass_boundary_20d.csv").open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["code", "nxt_coverage", "krx_bound", "krx_avg20_won",
                "combined_observed_avg20_won", "combined_minus_threshold_won", "pass_basis"])
    w.writerows(sorted(boundary_rows, key=lambda r: (r[6], r[0])))

with (OUT / "combined_only_pass_20d.csv").open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["code", "krx_avg20_won", "combined_avg20_won", "nxt_avg20_contribution_won",
                "margin_above_threshold_won", "nxt_days", "krx_days"])
    w.writerows(sorted(combined_only_rows, key=lambda r: r[4]))

with (OUT / "combined_only_daily_evidence.csv").open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["code", "date", "krx_turnover_won", "nxt_turnover_won", "combined_turnover_won"])
    w.writerows(sorted(combined_only_daily, key=lambda r: (r[0], r[1])))

# Independently reconcile each 20-row daily ledger to its per-stock aggregate.
daily_by_code = defaultdict(list)
for row in combined_only_daily:
    daily_by_code[row[0]].append(row)
for code, krx_avg, combined_avg, nxt_avg, margin, ndays, kdays in combined_only_rows:
    ledger = daily_by_code[code]
    if len(ledger) != 20 or len({r[1] for r in ledger}) != 20:
        raise SystemExit(f"Daily ledger coverage mismatch: {code}")
    if sum(r[2] for r in ledger) // 20 != krx_avg:
        raise SystemExit(f"KRX daily-to-aggregate mismatch: {code}")
    if sum(r[4] for r in ledger) // 20 != combined_avg:
        raise SystemExit(f"Combined daily-to-aggregate mismatch: {code}")
    if sum(r[3] for r in ledger) // 20 != nxt_avg:
        raise SystemExit(f"NXT daily-to-aggregate mismatch: {code}")
    if combined_avg - threshold != margin:
        raise SystemExit(f"Threshold margin mismatch: {code}")

with (OUT / "historical_only_codes.csv").open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["code", "all_240d_observed_days", "first_observed_date", "last_observed_date", "recent_20d_observed_days", "interpretation"])
    for code in historic_only:
        days = sorted(all_history[code])
        w.writerow([code, len(days), days[0], days[-1], 0, "NO_RECENT_KRX_ROWS; cause not established"])

report = [
    "# NXT资格与记录缺失：独立诊断（不发布）",
    f"窗口：{window[0]}～{window[-1]}；股票：{len(rows)}",
    f"240日历史股票池：{len(all_history)}；最近20日出现：{len(history)}；仅历史出现：{len(historic_only)}。",
    "仅历史出现的股票见historical_only_codes.csv；不能据此判断退市、停牌或代码变更。",
    "重要：NXT成交记录出现过，不足以证明该股在窗口内每一天均具交易资格。",
    "重要：NXT成交记录缺失，不能证明该股不具资格、停牌或当日零成交。",
    "因此资格字段统一为NOT_ESTABLISHED，等待独立权威逐日资格/状态清单。",
    "KRX单市场20日均成交额达到500亿韩元时，可以单独证明综合成交额下界达标；这不等于已验证综合均值。",
    "",
    "## 覆盖与KRX下界交叉统计",
]
for (coverage, bound), n in sorted(stats.items()):
    report.append(f"- {coverage} / {bound}: {n}")
if len(combined_only_rows) != exact_complete["PASS"] - complete_krx_pass:
    raise SystemExit("Combined-only PASS partition mismatch")
if complete_krx_pass > exact_complete["PASS"] or complete_krx_pass > sum(krx_proven.values()):
    raise SystemExit("Invalid overlap: KRX pass must imply combined pass when records complete")
if sum(stats.values()) != len(rows):
    raise SystemExit("Coverage category count does not equal stock universe")
report += [
    "",
    "## 独立成交额计算交叉检查",
    f"20日KRX与NXT都有记录：门槛达标{exact_complete['PASS']}只；不达标{exact_complete['FAIL']}只。",
    "这里的完整仅指成交额记录覆盖，不证明逐日NXT交易资格或市场口径已被权威核准。",
    f"交集校验：完整记录且KRX单市场达标={complete_krx_pass}；完整记录且仅合并后达标={exact_complete['PASS'] - complete_krx_pass}。",
    f"并集校验：完整记录合并达标或KRX单独达标={exact_complete['PASS'] + sum(krx_proven.values()) - complete_krx_pass}。",
    "逐股边界证据见turnover_pass_boundary_20d.csv；不能把交集重复计入候选股票数。",
    f"仅靠NXT贡献才达标股票：{len(combined_only_rows)}只；见combined_only_pass_20d.csv，按超过门槛的幅度升序排列。",
    "这份表记录NXT贡献和门槛余量，但不是权威交易资格确认，也不能替代独立官方口径校验。",
    f"逐日证据：combined_only_daily_evidence.csv 共{len(combined_only_daily)}行；逐股核对20日覆盖、KRX/NXT/合计及门槛余量。",
    f"KRX单市场已达标股票合计：{sum(krx_proven.values())}只；按NXT记录覆盖分布：{dict(krx_proven)}。",
    "对于缺少NXT记录的股票，只计算已观察成交额的保守下界，不补零，也不推断精确综合均值。",
    "",
    "## 下一步所需独立证据",
    "需要带日期、股票代码和交易资格/停牌状态的权威NXT清单，以及成交列表缺行是否代表零成交的官方定义。",
    "未取得证据前，不把NOT_REPORTED转换为零，不把NOT_ESTABLISHED转换为不具资格。",
    "本脚本不修改正式网页、不发布、不触发交易。",
]
(OUT / "audit_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
print("\n".join(report))
