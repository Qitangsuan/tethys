#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""潮汐行为审计（A 规则触发检测）：规则要求的行为，在轨迹里真的发生了吗？

检测两条可行为化的规则：
- 规则 6「动手之前先确认目标存在」：read 类调用前 3 个工具调用内有 glob → 有确认行为
- 规则 7「改文件走读→改闭环」：edit 调用前 10 个调用内有对同一 file_path 的 read → 有闭环行为

按规则 since（2026-11-06 UTC）切前/后，比较行为占比。描述性统计，出结论前注意样本量。

输入：data/raw/*.jsonl（事件流） + state/rule-effects.json（since）
输出：data/audit/behavior-audit.md
"""
import glob
import io
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "audit", "behavior-audit.md")
SINCE = "2026-11-06"
READ_TOOLS = {"read", "read_image"}
EDIT_TOOLS = {"edit"}  # write 是新建/整覆盖，不需要前置 read；只有 edit 需要「读→改」闭环


def since_ts(date_str):
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def parse_args(raw):
    """arguments 可能是 JSON 字符串或对象；取 file_path/path。"""
    if isinstance(raw, dict):
        args = raw
    else:
        try:
            args = json.loads(raw) if raw else {}
        except Exception:
            return None
    if not isinstance(args, dict):
        return None
    for key in ("file_path", "path", "target", "file"):
        v = args.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def main():
    cut = since_ts(SINCE)
    # 每条规则：前/后 的 (事件数, 有前缀行为的数)
    stats = {
        "rule6": {0: [0, 0], 1: [0, 0]},  # read 事件：总数, 带 glob 前缀
        "rule7": {0: [0, 0], 1: [0, 0]},  # edit 事件：总数, 带同文件 read 前缀
    }
    sessions = 0
    for fp in sorted(glob.glob(os.path.join(RAW, "*.jsonl"))):
        sessions += 1
        created = None
        calls = []  # 有序：(name, path)
        for ln in io.open(fp, encoding="utf-8"):
            try:
                o = json.loads(ln)
            except Exception:
                continue
            t = o.get("type")
            if t == "session" and created is None:
                created = o.get("createdAt") or 0
            elif t == "tool/call":
                d = o.get("data") or {}
                name = d.get("name") or ""
                path = parse_args(d.get("arguments"))
                calls.append((name, path))
        if created is None:
            continue
        arm = 1 if created >= cut else 0

        # 规则 6：read 前 3 个调用内有 glob
        for i, (name, _) in enumerate(calls):
            if name not in READ_TOOLS:
                continue
            window = calls[max(0, i - 3):i]
            has_glob = any(n2 == "glob" for n2, _ in window)
            stats["rule6"][arm][0] += 1
            if has_glob:
                stats["rule6"][arm][1] += 1

        # 规则 7：edit 前 10 个调用内有对同一 file_path 的 read
        for i, (name, path) in enumerate(calls):
            if name not in EDIT_TOOLS or not path:
                continue
            window = calls[max(0, i - 10):i]
            same_read = any(n2 in READ_TOOLS and p2 == path for n2, p2 in window)
            stats["rule7"][arm][0] += 1
            if same_read:
                stats["rule7"][arm][1] += 1

    lines = [f"# 潮汐行为审计（A 规则触发检测）· {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
             f"检测范围：{sessions} 个会话；切分点 `{SINCE}`（规则 6/7 的 since）。", "",
             "## 规则 6：动手前先确认目标存在（read 前 3 个调用内有 glob）", "",
             "| 臂 | read 事件数 | 带 glob 前缀 | 占比 |", "|---|---:|---:|---:|"]
    for arm, label in ((0, "前（2026-11-06 前）"), (1, "后（含当天起）")):
        n, ok = stats["rule6"][arm]
        lines.append(f"| {label} | {n} | {ok} | {ok / n * 100 if n else 0:.1f}% |")

    lines += ["", "## 规则 7：读→改闭环（edit 前 10 个调用内有读同一文件）", "",
              "| 臂 | edit 事件数 | 带同文件 read 前缀 | 占比 |", "|---|---:|---:|---:|"]
    for arm, label in ((0, "前（2026-11-06 前）"), (1, "后（含当天起）")):
        n, ok = stats["rule7"][arm]
        lines.append(f"| {label} | {n} | {ok} | {ok / n * 100 if n else 0:.1f}% |")

    lines += ["", "## 读法", "",
              "- 这是**行为检测**（规则要求的行为出现率），不是效果验证——它回答「规则被遵循了吗」，不回答「失败降了吗」。",
              "- 样本量看表：read/edit 事件数若太小，占比差异不可靠。",
              "- 规则 6 的「glob 前缀」只是确认行为的一种（Test-Path/Get-Command 在 pwsh 命令内，未计入）。",
              "- 规则 7 的「同文件」按 file_path 字符串精确匹配（大小写/斜杠差异会漏配，是保守估计）。"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n[audit_behavior] 输出 → {OUT}")


if __name__ == "__main__":
    main()
