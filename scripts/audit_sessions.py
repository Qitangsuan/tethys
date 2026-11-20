#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TETHYS v2 数据层普查：重复会话量化（事件序列哈希，v0.2 §2 契约）。

方法（GLM 建议）：
- 同 start（createdAt 毫秒）的会话是候选重复组；
- 组内用「事件序列哈希」判定（不用文件哈希——追加写入分裂会漏）：
  取 tool/call 的 callId 序列 + turn/end 数 + 事件行数的组合哈希；
- 重合率 = 两序列的公共前缀长度 / 较短序列长度；≥0.9 判重复。

输出：data/audit/duplicates.json + 控制台报告。
"""
import glob
import hashlib
import io
import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT_DIR = os.path.join(ROOT, "data", "audit")
os.makedirs(OUT_DIR, exist_ok=True)


def scan(fp):
    sid = os.path.basename(fp).replace(".jsonl", "")
    created_at = None
    call_ids = []
    turns = 0
    events = 0
    tools = 0
    fails_hint = 0
    try:
        lines = io.open(fp, encoding="utf-8").read().splitlines()
    except Exception:
        return None
    for line in lines:
        events += 1
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get("type")
        d = o.get("data") or {}
        if t == "session" and created_at is None:
            created_at = o.get("createdAt") or o.get("time")
        elif t == "tool/call":
            tools += 1
            cid = d.get("callId")
            if cid:
                call_ids.append(str(cid))
        elif t == "turn/end":
            turns += 1
    seq_hash = hashlib.sha256(("\n".join(call_ids)).encode("utf-8")).hexdigest()[:16]
    return {
        "session": sid,
        "createdAt": created_at,
        "events": events,
        "tools": tools,
        "turns": turns,
        "callIds": call_ids,
        "seqHash": seq_hash,
    }


def common_prefix_len(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def main():
    metas = []
    for fp in sorted(glob.glob(os.path.join(RAW, "*.jsonl"))):
        m = scan(fp)
        if m:
            metas.append(m)

    # 关键教训（2026-11-20 实测）：副本的 createdAt 可能不同（62c405ca 是 11-19 才出现的副本，
    # 与 02f7d24d 内容相同但 createdAt 差 12 天），所以**不能按时间分组**，必须两两比对
    # callId 序列前缀重合。74 会话 = 2701 对，可接受。
    dup_groups = []
    for i in range(len(metas)):
        for j in range(i + 1, len(metas)):
            a, b = metas[i], metas[j]
            if not a["callIds"] or not b["callIds"]:
                continue
            cp = common_prefix_len(a["callIds"], b["callIds"])
            shorter = min(len(a["callIds"]), len(b["callIds"])) or 1
            ratio = cp / shorter
            # 至少 10 个 callId 重合才算重复（防极小会话误判）
            if ratio >= 0.9 and cp >= 10:
                dup_groups.append({
                    "a": {k: a[k] for k in ("session", "createdAt", "events", "tools", "turns", "seqHash")},
                    "b": {k: b[k] for k in ("session", "createdAt", "events", "tools", "turns", "seqHash")},
                    "commonPrefix": cp,
                    "overlapRatio": round(ratio, 3),
                    "createdAtDiffMs": abs((a["createdAt"] or 0) - (b["createdAt"] or 0)),
                })

    out = {
        "generatedAt": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "sessions": len(metas),
        "duplicateGroups": dup_groups,
    }
    dst = os.path.join(OUT_DIR, "duplicates.json")
    io.open(dst, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2))

    print(f"[audit] 会话 {len(metas)} 个")
    print(f"[audit] 重复会话组 {len(dup_groups)} 组（callId 序列前缀重合 ≥90% 且 ≥10 条）：")
    for g in dup_groups:
        print(f"  a={g['a']['session'][:26]}（{g['a']['events']} 事件/{g['a']['turns']} 轮）"
              f" b={g['b']['session'][:26]}（{g['b']['events']} 事件/{g['b']['turns']} 轮）"
              f" 前缀 {g['commonPrefix']} 条 / {g['overlapRatio']*100:.0f}%"
              f" createdAt 差 {g['createdAtDiffMs']/86400000:.1f} 天")
    print(f"[audit] 输出 → {dst}")


if __name__ == "__main__":
    main()
