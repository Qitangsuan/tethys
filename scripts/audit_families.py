#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""潮汐失败族聚类（D）：把长尾 tool_error 归族，减少碎片化。

规则式归类（关键词匹配），族定义集中在 FAMILIES；未匹配的归 other 并保留原键。
输入：data/signals/failReasons.json（real/excluded）
输出：data/audit/family-audit.md
"""
import io
import json
import os
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNALS = os.path.join(ROOT, "data", "signals", "failReasons.json")
CALIBER = os.path.join(ROOT, "state", "caliber.json")
OUT = os.path.join(ROOT, "data", "audit", "family-audit.md")

# 族定义：name -> 关键词列表（任一命中即归入该族，按顺序优先）
FAMILIES = [
    ("exit_code", ["exit_1_real", "exit_code_"]),
    ("edit", ["edit_no_match"]),
    ("read", ["file_read_missing", "read_out_of_range", "file_not_read", "file_conflict"]),
    ("web", ["web_fetch_fail"]),
    ("tool_timeout", ["tool_timeout"]),
    ("excluded_host", ["host_fault", "tool_aborted", "user_cancelled", "clean_exit"]),
    ("deepseek_builtin", ["deepseek", "invalid api key", "authentication fails"]),
    ("provider_config", ["multiple usable web providers", "api key", "HTTP 401", "HTTP 404"]),
    ("network", ["cross-origin redirect", "fetch failed", "web fetch failed", "econn", "timeout"]),
    ("param_format", ["rejected input", "must be an object", "invalid arguments", "missing required"]),
    ("tool_identifier", ["unknown entry id", "no catalogued service", "active teammate", "team member limit"]),
    ("subagent_model", ["subagent", "unavailable", "codex search is signed out", "model_not_found", "no available channel"]),
    ("file_search", ["grep search failed", "glob search failed", "grep produced more raw output",
                     "cannot find module", "url hostname", "target index url"]),
]
FAMILY_LABEL = {
    "deepseek_builtin": "DeepSeek 内置（api key/认证）",
    "provider_config": "provider 配置（歧义/401/404）",
    "network": "网络（跨域重定向/抓取失败/超时）",
    "param_format": "参数格式（input/invalid arguments）",
    "tool_identifier": "工具标识符（entry id/队友/Service）",
    "subagent_model": "子代理/模型（不可用/掉登录/无渠道）",
    "file_search": "文件/搜索（ripgrep/模块/URL）",
    "other": "未归类（保留原键）",
}


def classify_family(kind):
    low = kind.lower()
    for name, kws in FAMILIES:
        if any(kw in low for kw in kws):
            return name
    return "other"


def main():
    with io.open(SIGNALS, encoding="utf-8") as f:
        data = json.load(f)
    with io.open(CALIBER, encoding="utf-8") as f:
        cal = json.load(f)

    real = data["real"]
    excluded = data.get("excluded") or {}
    config_patterns = cal.get("configKinds") or []

    def is_config(kind):
        return any(p in kind for p in config_patterns)

    fam_real = Counter()
    fam_excluded = Counter()
    details = {}
    for kind, n in real.items():
        fam = classify_family(kind)
        fam_real[fam] += n
        details.setdefault(fam, []).append((kind, n))
    for kind, n in excluded.items():
        fam_excluded[classify_family(kind)] += n

    total = sum(real.values())
    lines = [f"# 潮汐失败族聚类（D）· {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
             f"数据快照 `{data['generatedAt']}` · 真失败 {total}（原始键 {len(real)} 个）", "",
             "## 族级分布（真失败）", "",
             "| 族 | 失败次数 | 占比 | 原始键数 | 主要键 |", "|---|---:|---:|---:|---|"]
    rows = sorted(fam_real.items(), key=lambda kv: -kv[1])
    for fam, n in rows:
        items = sorted(details.get(fam, []), key=lambda kv: -kv[1])
        keys = "；".join(f"`{k[:50]}`×{v}" for k, v in items[:3]) + (" …" if len(items) > 3 else "")
        lines.append(f"| {FAMILY_LABEL.get(fam, fam)} | {n} | {n / total * 100 if total else 0:.1f}% | {len(items)} | {keys} |")

    lines += ["", "## 排除类族分布", "", "| 族 | 排除次数 |", "|---|---:|"]
    for fam, n in fam_excluded.most_common():
        lines.append(f"| {FAMILY_LABEL.get(fam, fam)} | {n} |")

    lines += ["", "## 说明", "",
              "- 归类是**关键词规则**（按 FAMILIES 顺序优先），不是语义理解——可能误归/漏归。",
              "- 族化后样本变大，适合观察「哪类问题最花钱」；统计验证仍按规则覆盖的键算（verify.py 口径不变）。",
              "- 未匹配的归「未归类」，保留原键供人工看。"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n[audit_families] 输出 → {OUT}")


if __name__ == "__main__":
    main()
