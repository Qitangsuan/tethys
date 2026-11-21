#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""潮汐规则审计：B 规则集质量审查（冗余/冲突/覆盖空白）+ C 覆盖模拟（理论收益）。

输入：data/signals/failReasons.json + state/rule-effects.json + state/caliber.json
输出：data/audit/rules-audit.md（可直接给人看）

B. 规则集审查
- 冗余：两条规则匹配的失败键有交集（同族被多条管）
- 覆盖空白：真失败里没有被任何规则 detects/patterns 覆盖的族
- 冲突信号：规则文本里的矛盾词（重试/别重试、先/后）——粗筛，需人工确认

C. 覆盖模拟（上限，不是真实效果）
- 每条规则覆盖的失败数/占比、消除后整体失败率
- 理论收益排序
"""
import io
import json
import os
import re
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNALS = os.path.join(ROOT, "data", "signals", "failReasons.json")
EFFECTS = os.path.join(ROOT, "state", "rule-effects.json")
CALIBER = os.path.join(ROOT, "state", "caliber.json")
OUT = os.path.join(ROOT, "data", "audit", "rules-audit.md")


def load(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def is_config(kind, patterns):
    return any(p in kind for p in patterns)


def match_kind(kind, cfg):
    if kind in (cfg.get("detects") or []):
        return True
    for p in cfg.get("detectsPatterns") or []:
        if p in kind:
            return True
    return False


def main():
    data = load(SIGNALS)
    effects = load(EFFECTS)
    cal = load(CALIBER)
    rules = effects["rules"]
    real = data["real"]
    total_calls = data["uniqueCalls"]
    config_patterns = cal["configKinds"]

    lines = [f"# 潮汐规则审计 · {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
             f"数据快照 `{data['generatedAt']}` · 口径 `{data['caliber']}` · "
             f"{data['sessions']} 会话 / {total_calls} 唯一调用 / 真失败 {sum(real.values())}", "",
             "## C. 覆盖模拟（理论收益上限，不是真实效果）", "",
             "> 假设：某条规则能**完全消除**它覆盖的失败族。这是上限估计，真实效果要等 A/B。", "",
             "| 规则 | 覆盖键 | 覆盖失败数 | 占真失败 | 消除后总失败率 | 理论收益排行 |",
             "|---|---|---:|---:|---:|---:|"]
    rows = []
    covered_by_rule = {}
    for key in sorted(rules, key=lambda k: int(k)):
        cfg = rules[key]
        covered = [k for k in real if match_kind(k, cfg) and not is_config(k, config_patterns)]
        covered_by_rule[key] = covered
        n = sum(real[k] for k in covered)
        if n > 0:
            rows.append((int(key), key, cfg.get("title", ""), len(covered), n))
    rows.sort(key=lambda r: -r[4])
    rule_rank = {r[1]: i + 1 for i, r in enumerate(rows)}
    for key in sorted(rules, key=lambda k: int(k)):
        cfg = rules[key]
        covered = covered_by_rule[key]
        n = sum(real[k] for k in covered)
        if cfg.get("verify") is False and not covered:
            lines.append(f"| {key} | —（无精确覆盖） | — | — | — | 不可参与收益计算 |")
            continue
        pct = n / sum(real.values()) * 100 if real else 0
        after = (sum(real.values()) - n) / total_calls * 1000 if total_calls else 0
        lines.append(f"| {key} | {len(covered)} 个 | {n} | {pct:.1f}% | {after:.2f}/千次 | #{rule_rank.get(key, '-')} |")

    # B1 冗余：规则两两覆盖交集
    lines += ["", "## B1. 规则冗余（两条规则管同一族失败）", ""]
    pairs = []
    keys = [k for k in rules if covered_by_rule.get(k)]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            inter = set(covered_by_rule[a]) & set(covered_by_rule[b])
            if inter:
                pairs.append((a, b, sorted(inter)))
    if pairs:
        lines.append("| 规则对 | 共同覆盖的失败键 |")
        lines.append("|---|---|")
        for a, b, inter in pairs:
            show = ", ".join(f"`{k[:60]}`" for k in inter[:4]) + (" …" if len(inter) > 4 else "")
            lines.append(f"| {a} ↔ {b} | {show} |")
    else:
        lines.append("无重叠。")

    # B2 覆盖空白：真失败里没有任何规则覆盖的
    lines += ["", "## B2. 覆盖空白（无任何规则覆盖的失败族）", ""]
    all_covered = set()
    for key in rules:
        all_covered.update(covered_by_rule.get(key, []))
    uncovered = [(k, v) for k, v in real.items()
                 if k not in all_covered and not is_config(k, config_patterns)]
    uncovered.sort(key=lambda kv: -kv[1])
    if uncovered:
        lines.append(f"共 {len(uncovered)} 个未覆盖族（按次数）：")
        lines.append("| 失败键 | 次数 |")
        lines.append("|---|---:|")
        for k, v in uncovered[:20]:
            lines.append(f"| `{k[:90]}` | {v} |")
        lines.append("")
        lines.append(f"> 未覆盖族合计 {sum(v for _, v in uncovered)} 次，"
                     f"占真失败 {sum(v for _, v in uncovered) / sum(real.values()) * 100:.1f}%。")
    else:
        lines.append("全部真失败都被至少一条规则覆盖。")

    # B3 规则文本冲突信号（粗筛）
    lines += ["", "## B3. 规则文本冲突信号（粗筛，需人工确认）", ""]
    conflict_pairs = [("重试", "别重试"), ("先", "后"), ("必须", "不要"), ("默认", "禁止")]
    hits = []
    for key in sorted(rules, key=lambda k: int(k)):
        text = rules[key].get("title", "") + " " + rules[key].get("note", "")
        for a, b in conflict_pairs:
            if a in text and b in text:
                hits.append(f"规则 {key}（{rules[key].get('title', '')}）同时含「{a}」与「{b}」")
    if hits:
        for h in hits:
            lines.append(f"- {h}")
    else:
        lines.append("无粗筛命中（规则文本没有明显的矛盾词对）。")

    lines += ["", "## 说明", "",
              "- B1 冗余只算 `detects`/`detectsPatterns` 的**键级交集**；规则文本里的语义重叠（如两条规则都在教怎么排查工具错误）需要人工读。",
              "- B2 覆盖空白是「有没有规则管这类失败」的客观统计；空白多不代表要加规则（可能是环境问题/不可控）。",
              "- C 覆盖模拟是**上限**：假设规则能完全消除该族失败，忽略了规则执行成本和对其他失败的影响。",
              "- 统计口径与 verify.py 一致：callId 去重、配置态剔除。"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:24]))
    print(f"\n[audit_rules] 输出 → {OUT}")


if __name__ == "__main__":
    main()
