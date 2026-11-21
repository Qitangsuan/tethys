#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""潮汐模型审计（E 跨模型失败率对比）：每个模型被用了多少次、哪类失败最多。

数据来源：data/raw 事件流。
- model/selection 事件（provider/model）更新「当前模型」
- tool/call 落到当前模型；tool/result 配对后按 classify 口径判定真失败
- callId 全局去重（与 classify.py 一致）

输出：data/audit/model-audit.md
"""
import glob
import io
import json
import os
import sys
from collections import defaultdict, Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import classify as C  # noqa: E402

RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "audit", "model-audit.md")


def text_of(result_data):
    out = []
    for c in (result_data.get("message") or {}).get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            out.append(c.get("text") or "")
    return "\n".join(out)


def main():
    # model -> {"calls": n, "fails": n, "kinds": Counter(), "sessions": set()}
    stats = defaultdict(lambda: {"calls": 0, "fails": 0, "kinds": Counter(), "sessions": set()})
    seen = set()
    for fp in sorted(glob.glob(os.path.join(RAW, "*.jsonl"))):
        sid = os.path.basename(fp).replace(".jsonl", "")
        current = "unknown"
        calls = {}  # callId -> (name, model)
        for ln in io.open(fp, encoding="utf-8"):
            try:
                o = json.loads(ln)
            except Exception:
                continue
            t = o.get("type")
            d = o.get("data") or {}
            if t == "model/selection":
                current = f"{d.get('provider')}/{d.get('model')}"
            elif t == "assistant/message":
                src = (d.get("message") or {}).get("source") or {}
                if isinstance(src, dict) and src.get("provider") and src.get("model"):
                    current = f"{src.get('provider')}/{src.get('model')}"
            elif t == "tool/call":
                cid = d.get("callId")
                if cid:
                    calls[cid] = (d.get("name") or "", current)
            elif t == "tool/result":
                cid = d.get("toolCallId") or (d.get("message") or {}).get("toolCallId")
                if cid in seen:
                    continue
                seen.add(cid)
                call = calls.get(cid)
                if not call:
                    continue
                name, model = call
                st = stats[model]
                st["calls"] += 1
                st["sessions"].add(sid)
                kind, is_real = C.classify(name, text_of(d))
                if is_real:
                    st["fails"] += 1
                    st["kinds"][kind] += 1

    lines = [f"# 潮汐模型审计（E 跨模型失败率对比）· {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
             "> 口径：callId 去重 + classify 判定（含配置态剔除？否——本表不剔除，看原始分布）。", "",
             "| 模型 | 会话数 | 调用数 | 真失败 | 失败率 | Top 失败类型 |",
             "|---|---:|---:|---:|---:|---|"]
    rows = sorted(stats.items(), key=lambda kv: -kv[1]["calls"])
    for model, st in rows:
        if st["calls"] == 0:
            continue
        rate = st["fails"] / st["calls"] * 100
        top = "；".join(f"{k[:40]}×{n}" for k, n in st["kinds"].most_common(3)) or "—"
        lines.append(f"| {model} | {len(st['sessions'])} | {st['calls']} | {st['fails']} | {rate:.2f}% | {top} |")

    lines += ["", "## 说明", "",
              "- 模型按 `model/selection` 事件跟踪；会话内切模型时按时间顺序归因。",
              "- 样本量：调用数 < 100 的模型失败率不可靠，只作参考。",
              "- 本表**不剔除配置态**（provider 配置/web 问题会集中到某些模型），看原始分布。"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n[audit_models] 输出 → {OUT}")


if __name__ == "__main__":
    main()
