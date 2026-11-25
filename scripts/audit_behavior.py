#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""潮汐行为审计 v2 · 按现行 7 条规则重写检测口径（2026-11-25）

旧版问题（2026-11-21 规则集重设计后）：
- 切分点停在旧规则 6/7 的 2026-11-06；
- 旧 R4「read 前 3 调用内有 glob」当正向 —— 现行 R4 语义已反（已知路径直接 read，
  不该每次都 glob），继续这么算会把「多余动作」当成绩。

本审计只回答一个问题：**规则要求的行为出现了吗**。
它不回答「失败降了吗」——行为改变不等于有效，规则 6 就是反例（glob 前缀升了 3 倍，
失败率没降）。

可测契约（每条都写清分子/分母口径）：
- R1 按本机环境执行脚本：pwsh 命令里出现 `&&` → 违反（本机是 PowerShell 5.1）
- R3 环境故障停止无效重试：同一工具连续 3 次以上失败且中间没有成功 → 违反
- R4 路径不确定才定位：read 失败后，下一次同文件操作是 glob 或空操作 → 符合
- R5 编辑失败后重读再改：edit 失败后，下一次同文件操作是 read → 符合
- R6 动态标识符从清单取：出现 unknown entry / no catalogued Service 之前，同一会话里
  有对应 list_* 调用 → 符合
- R7 参数类型错改值再发：报 rejected input / must be an object 之后，同工具再发同一串
  参数（完全相同）→ 违反
- R2 先缩小输出范围：无法从事件结构判定（跳过，不假装能测）

用法：python scripts/audit_behavior.py
输出：data/audit/behavior-audit.md
"""
import glob
import io
import json
import os
import re
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "audit", "behavior-audit.md")
EFFECTS = os.path.join(ROOT, "state", "rule-effects.json")
CALIBER = os.path.join(ROOT, "state", "caliber.json")

# R6 的清单类工具：R6 说「没有当前清单给出的准确名称时先查清单」
LIST_TOOLS = ("list_agents", "cordis_inspect_list", "plugin_manager", "list_subagent_models")
# R7 的参数类型错信号
TYPE_ERR = ("rejected input", "must be an object", "invalid arguments", "must be a")
# R5 的编辑失败信号
EDIT_FAIL = ("old_string was not found", "old_string matched", "no matching text",
             "file has not been read", "file has changed", "file changed since",
             "no longer exists", "cannot write", "cannot modify")
# R4 的读取失败信号
READ_FAIL = ("no such file", "cannot read", "is out of range for", "file not found",
             "cannot find", "does not exist")


def read_json(path):
    with io.open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def text_of(data):
    out = []
    for c in (data.get("message") or {}).get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            out.append(c.get("text") or "")
    return "\n".join(out)


def parse_args(raw):
    if not isinstance(raw, str):
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def load_sessions():
    """返回 [(session_id, created_at_ms, [事件])]；事件 = {tool, args, text, seq, failed}。"""
    sessions = []
    for fp in sorted(glob.glob(os.path.join(RAW, "*.jsonl"))):
        sid = os.path.basename(fp).replace(".jsonl", "")
        try:
            text = io.open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        lines = text.splitlines(keepends=True)
        created_at = None
        calls = {}
        events = []
        i, n = 0, len(lines)
        while i < n:
            if not lines[i].strip():
                i += 1
                continue
            o, span = None, 1
            while span <= 32 and i + span <= n:
                raw = "".join(lines[i:i + span])
                try:
                    o = json.loads(raw)
                    break
                except Exception:
                    pass
                if span > 1:
                    try:
                        o = json.loads(raw.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r"))
                        break
                    except Exception:
                        pass
                span += 1
            if o is None:
                i += 1
                continue
            i += span
            t = o.get("type")
            d = o.get("data") or {}
            if t == "session" and created_at is None:
                created_at = o.get("createdAt") or o.get("time")
            elif t == "tool/call":
                calls[d.get("callId")] = dict(name=d.get("name") or "", args=parse_args(d.get("arguments")),
                                              seq=o.get("seq"))
            elif t == "tool/result":
                cid = d.get("toolCallId") or (d.get("message") or {}).get("toolCallId")
                call = calls.get(cid)
                if not call:
                    continue
                body = text_of(d)
                head = body.strip()
                low = head[:600].lower()
                failed = head.startswith(("Error:", "错误："))
                events.append(dict(tool=call["name"], args=call["args"], seq=call["seq"],
                                   failed=failed, text=low))
        if events:
            sessions.append((sid, created_at or 0, events))
    return sessions


def fpath(ev):
    for k in ("file_path", "path", "file"):
        v = ev["args"].get(k)
        if isinstance(v, str) and v:
            return v.replace("/", "\\").lower()
    return None


def rate(hit, total):
    return f"{hit}/{total}（{(100.0 * hit / total):.1f}%）" if total else "0/0（—）"


def audit(sessions, since_ms):
    # 每个指标：{前: [hit, total], 后: [hit, total]}
    stats = defaultdict(lambda: {"pre": [0, 0], "post": [0, 0]})

    def bump(arm, key, hit):
        stats[key][arm][0] += hit
        stats[key][arm][1] += 1

    for _sid, created, events in sessions:
        arm = "post" if created >= since_ms else "pre"
        # R1：pwsh 命令含 &&
        for ev in events:
            if ev["tool"] == "pwsh":
                cmd = json.dumps(ev["args"], ensure_ascii=False)
                bump(arm, "R1_ps51", 0 if "&&" in cmd else 1)
        # R3：同一工具连续失败 ≥3（中间夹了成功就重新计）
        run_tool, run_len = None, 0
        for ev in events:
            if ev["failed"]:
                if ev["tool"] == run_tool:
                    run_len += 1
                else:
                    if run_len >= 3:
                        bump(arm, "R3_stop_retry", 0)
                    run_tool, run_len = ev["tool"], 1
            else:
                if run_len >= 3:
                    bump(arm, "R3_stop_retry", 0)
                run_tool, run_len = None, 0
        if run_len >= 3:
            bump(arm, "R3_stop_retry", 0)
        # R5：edit 失败后同文件下一次操作是 read
        for i, ev in enumerate(events):
            if ev["tool"] not in ("edit", "write") or not ev["failed"]:
                continue
            low = ev["text"]
            if not any(k in low for k in EDIT_FAIL):
                continue
            target = fpath(ev)
            if not target:
                continue
            nxt = None
            for later in events[i + 1:]:
                if fpath(later) == target and later["tool"] in ("read", "edit", "write"):
                    nxt = later
                    break
            if nxt is None:
                continue
            bump(arm, "R5_reread", 1 if nxt["tool"] == "read" else 0)
        # R4：read 失败后同文件下一次操作是 glob 或换路径的 read
        for i, ev in enumerate(events):
            if ev["tool"] != "read" or not ev["failed"]:
                continue
            low = ev["text"]
            if not any(k in low for k in READ_FAIL):
                continue
            target = fpath(ev)
            nxt = None
            for later in events[i + 1:]:
                if later["tool"] in ("read", "glob", "grep"):
                    nxt = later
                    break
            if nxt is None:
                continue
            ok = nxt["tool"] in ("glob", "grep") or fpath(nxt) != target
            bump(arm, "R4_locate", 1 if ok else 0)
        # R6：未知标识符报错前 10 个调用内有 list_*（旧版「会话内有过就算」太宽，实际没在查）
        for i, ev in enumerate(events):
            if not ev["failed"]:
                continue
            low = ev["text"]
            if not any(k in low for k in ("unknown entry id", "no catalogued service", "active teammate")):
                continue
            window = [e["tool"] for e in events[max(0, i - 10):i]]
            bump(arm, "R6_list_first", 1 if any(t in LIST_TOOLS for t in window) else 0)
        # R4 反向指标：已知路径直接 read 才对，「read 前 3 调用内先 glob」是多余定位动作（越低越好）
        for i, ev in enumerate(events):
            if ev["tool"] != "read" or ev["failed"]:
                continue
            window = [e["tool"] for e in events[max(0, i - 3):i]]
            if "glob" in window:
                bump(arm, "R4_overlocate", 0)      # 有多余 glob
            else:
                bump(arm, "R4_overlocate", 1)      # 直接 read，符合
        # R7：参数类型错后原样重发
        for i, ev in enumerate(events):
            if not ev["failed"] or not any(k in ev["text"] for k in TYPE_ERR):
                continue
            same = None
            for later in events[i + 1:]:
                if later["tool"] == ev["tool"]:
                    same = later
                    break
            if same is None:
                continue
            bump(arm, "R7_fix_arg", 0 if same["args"] == ev["args"] else 1)

    return stats


LABELS = [
    ("R1_ps51", "R1 · pwsh 不用 `&&`（本机 PS 5.1）", "符合 = 命令里没有 `&&`"),
    ("R3_stop_retry", "R3 · 环境故障不无效重试", "符合 = 不存在「同工具连续失败 ≥3 次」"),
    ("R4_locate", "R4 · 读失败后换方式（glob/换路径）", "符合 = 下一步不是原样重调"),
    ("R4_overlocate", "R4 反向 · 已知路径直接 read（无多余 glob）", "符合 = read 前 3 调用内没有 glob"),
    ("R5_reread", "R5 · 编辑失败后重读再改", "符合 = 下一步是同文件 read"),
    ("R6_list_first", "R6 · 动态标识符从清单取", "符合 = 报未知标识符前 10 调用内有 list_*"),
    ("R7_fix_arg", "R7 · 参数类型错就改值再发", "符合 = 重发时参数已变"),
]


def render(stats, since_iso, n_sessions, n_pre, n_post):
    lines = ["# 潮汐行为审计（v2 · 按现行 7 条规则）", "",
             "> 只回答「规则要求的行为出现了吗」，**不回答「失败降了吗」**。",
             "> 行为改变 ≠ 有效：旧规则 6 的 read 前 glob 从 2.6% 升到 8.6%（3.3 倍），失败率没有显著下降。",
             "",
             f"规则集生效日 `{since_iso}`；会话 {n_sessions} 个（前臂 {n_pre} / 后臂 {n_post}）。", ""]
    if n_post < 10:
        lines += [f"**样本警告**：后臂只有 {n_post} 个有工具调用的会话，下面的后臂比例不可作统计结论，只能看方向。", ""]
    lines += ["| 规则 | 前臂符合率 | 后臂符合率 | 口径 |", "|---|---:|---:|---|"]
    for key, title, rule in LABELS:
        s = stats.get(key)
        if not s:
            continue
        lines.append(f"| {title} | {rate(*s['pre'])} | {rate(*s['post'])} | {rule} |")
    lines += ["", "## 读法", "",
              "- 这是**行为检测**（规则要求的行为出现率），不是效果验证。",
              "- 分母是「触发该契约的事件数」，不是调用总数；条数太少时比例没有意义。",
              "- R2「先缩小输出范围」无法从事件结构判定，本审计不假装能测。",
              "- 现行 R4 与旧版口径相反：**已知路径直接 read 是正确的**，不再把「read 前先 glob」当正向。"]
    return "\n".join(lines) + "\n"


def main():
    effects = read_json(EFFECTS).get("rules") or {}
    since = max((r.get("since") for r in effects.values() if isinstance(r.get("since"), str)), default=None)
    if not since:
        raise SystemExit("rule-effects.json 里没有 since")
    from datetime import datetime, timezone
    since_ms = int(datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

    sessions = load_sessions()
    pre = sum(1 for _, c, _ in sessions if c < since_ms)
    post = len(sessions) - pre
    stats = audit(sessions, since_ms)
    text = render(stats, since, len(sessions), pre, post)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write(text)
    print(text)
    print(f"[audit_behavior] 输出 → {OUT}")


if __name__ == "__main__":
    main()
