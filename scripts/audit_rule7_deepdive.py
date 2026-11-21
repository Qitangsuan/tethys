#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""规则 7 深挖：为什么「读→改闭环」执行率低？

三个问题：
1. 测量误差：路径规范化（大小写/斜杠/相对绝对）+ 窗口放大（10/20/50/不限）后，前置 read 占比是多少？
2. edit_no_match 失败链：失败前是否有前置 read？（有 → 失败与「没读」无关，是文件变了/内容对了）
3. 失败后行为：edit_no_match 失败后是否重读同文件？（重读率 = 规则 7 的「失败分诊」行为）

输出：data/audit/rule7-deepdive.md
"""
import glob
import io
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import classify as C  # noqa: E402

RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "audit", "rule7-deepdive.md")
READ_TOOLS = {"read", "read_image"}


def text_of(result_data):
    out = []
    for c in (result_data.get("message") or {}).get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            out.append(c.get("text") or "")
    return "\n".join(out)


def norm_path(p):
    """规范化路径：统一斜杠、小写、取最后 3 段（避免绝对/相对差异）。"""
    if not p:
        return ""
    p = p.replace("\\", "/").lower().rstrip("/")
    parts = [x for x in p.split("/") if x and x != "."]
    return "/".join(parts[-3:])


def parse_path(raw):
    if isinstance(raw, dict):
        args = raw
    else:
        try:
            args = json.loads(raw) if raw else {}
        except Exception:
            return ""
    if not isinstance(args, dict):
        return ""
    for key in ("file_path", "path", "target", "file"):
        v = args.get(key)
        if isinstance(v, str) and v:
            return norm_path(v)
    return ""


def main():
    windows = {"10": 10, "20": 20, "50": 50, "不限": 10 ** 9}
    stats = {w: [0, 0] for w in windows}  # window -> [edit 数, 带前置 read]
    fail_stats = {"total": 0, "has_pre_read": 0, "retry_read_after": 0, "retry_edit_after": 0}
    seen = set()

    for fp in sorted(glob.glob(os.path.join(RAW, "*.jsonl"))):
        calls = []  # 有序 (name, norm_path, callId)
        results = {}  # callId -> (kind, is_real)
        for ln in io.open(fp, encoding="utf-8"):
            try:
                o = json.loads(ln)
            except Exception:
                continue
            t = o.get("type")
            d = o.get("data") or {}
            if t == "tool/call":
                calls.append((d.get("name") or "", parse_path(d.get("arguments")), d.get("callId")))
            elif t == "tool/result":
                cid = d.get("toolCallId") or (d.get("message") or {}).get("toolCallId")
                if cid in seen:
                    continue
                seen.add(cid)
                name = next((n for n, _, c in calls if c == cid), "")
                kind, is_real = C.classify(name, text_of(d))
                results[cid] = (kind, is_real, name)

        # 1. 前置 read 占比（多窗口）
        for i, (name, path, cid) in enumerate(calls):
            if name != "edit" or not path:
                continue
            for wlabel, w in windows.items():
                window = calls[max(0, i - w):i]
                has = any(n2 in READ_TOOLS and p2 == path for n2, p2, _ in window)
                stats[wlabel][0] += 1
                if has:
                    stats[wlabel][1] += 1

        # 2/3. edit_no_match 失败链
        for i, (name, path, cid) in enumerate(calls):
            r = results.get(cid)
            if not r or r[0] != "edit_no_match" or not path:
                continue
            fail_stats["total"] += 1
            pre = calls[max(0, i - 50):i]
            if any(n2 in READ_TOOLS and p2 == path for n2, p2, _ in pre):
                fail_stats["has_pre_read"] += 1
            post = calls[i + 1:i + 4]
            if any(n2 in READ_TOOLS and p2 == path for n2, p2, _ in post):
                fail_stats["retry_read_after"] += 1
            if any(n2 == "edit" and p2 == path for n2, p2, _ in post):
                fail_stats["retry_edit_after"] += 1

    lines = [f"# 规则 7 深挖 · {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
             "## 1. 测量修正：路径规范化 + 窗口放大后的「edit 前读同文件」占比", "",
             "| 窗口（回去几个调用） | edit 数 | 带前置 read | 占比 |", "|---|---:|---:|---:|"]
    for wlabel in windows:
        n, ok = stats[wlabel]
        lines.append(f"| {wlabel} | {n} | {ok} | {ok / n * 100 if n else 0:.1f}% |")

    lines += ["", "## 2. edit_no_match 失败前是否有前置 read（窗口 50）", "",
              f"- edit_no_match 总数：{fail_stats['total']}",
              f"- 失败前有同文件 read：{fail_stats['has_pre_read']}（{fail_stats['has_pre_read'] / fail_stats['total'] * 100 if fail_stats['total'] else 0:.1f}%）",
              f"- **失败后 1-3 步内重读同文件**：{fail_stats['retry_read_after']}（{fail_stats['retry_read_after'] / fail_stats['total'] * 100 if fail_stats['total'] else 0:.1f}%）",
              f"- 失败后 1-3 步内重试 edit：{fail_stats['retry_edit_after']}（{fail_stats['retry_edit_after'] / fail_stats['total'] * 100 if fail_stats['total'] else 0:.1f}%）",
              "", "## 读法", "",
              "- 若窗口放大后占比大幅上升 → 之前的「没执行」是测量误差（read 离 edit 太远）。",
              "- 若 edit_no_match 失败前大多有 read → 失败主因是「文件变了/内容对不上」，不是「没读」——规则 7 该治的是「重读再改」，不是「先读后改」。",
              "- 失败后重读率低 → 规则 7 的「失败分诊」行为没被执行（失败后直接重试/放弃）。"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n[rule7_deepdive] 输出 → {OUT}")


if __name__ == "__main__":
    main()
