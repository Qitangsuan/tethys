#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TETHYS v2 频率门槛：数据够不够开轮次。

用法：python scripts/ready.py

门槛（v0.2 §4，任一满足即 GO）：
1. 新增会话 ≥ 5
2. 新增调用 ≥ 2000（按平均调用/会话估算）
3. 采集有缺口（源 zstd > raw jsonl）
4. 判定有缺口（按 callId 前缀去重、扣除快照后仍有新增会话）
5. **新失败族**（H6 修复）：真失败里出现未被任何规则覆盖的 kind（≥2 次）
6. **14 天兜底**：距上次判定 > 14 天 → 强制 GO（防规则腐化静默积累）

未达门槛：只跑采集+判定（离线零 token），不跑提案/验证/落地。
"""
import glob
import io
import json
import os
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
SIGNALS = os.path.join(ROOT, "data", "signals", "failReasons.json")
EFFECTS = os.path.join(ROOT, "state", "rule-effects.json")
SESSIONS = os.path.expanduser(os.path.join("~", ".dsh", "sessions"))

MIN_NEW_SESSIONS = 5
MIN_NEW_CALLS = 2000
FALLBACK_DAYS = 14
MIN_NEW_FAMILY = 2


def _read_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def count_src():
    return len(glob.glob(os.path.join(SESSIONS, "**", "*.jsonl.zstd"), recursive=True))


def call_prefix(path):
    """保留有序 callId 的前 ceil(90%)；不取时间、不用可能碰撞的摘要。"""
    ids = []
    try:
        with io.open(path, encoding="utf-8-sig") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    return None
                if event.get("type") == "tool/call":
                    data = event.get("data")
                    cid = data.get("callId") if isinstance(data, dict) else None
                    if not isinstance(cid, str) or not cid.strip():
                        return None
                    ids.append(cid)
    except (OSError, UnicodeError, ValueError):
        return None
    return tuple(ids[:(9 * len(ids) + 9) // 10]) if ids else None


def count_new_sessions(files, signals):
    """返回新增会话保守数及口径说明。旧快照不把正暴露时间线当完整名册。"""
    judged = signals.get("sessions")
    if type(judged) is not int or judged < 0:
        return 0, "快照会话数无效，新增按 0；请重跑离线判定"
    manifest = signals.get("rawSessionIds")
    known = set()
    if (isinstance(manifest, list) and len(manifest) == judged
            and all(isinstance(s, str) and s for s in manifest)
            and len(set(manifest)) == judged):
        known = set(manifest)
    tagged = []
    matched = 0
    for path in files:
        prefix = call_prefix(path)
        if prefix is not None:
            old = os.path.splitext(os.path.basename(path))[0] in known
            tagged.append((prefix, old))
            matched += int(old)
    # 两序列达到“较短者的前 90% 相同”等价于两个截断键互为前缀。
    # 排序后短键在其所有后代之前；短样本串联的组保守合并。
    root = None
    groups = with_old = 0
    anchored = False
    for key, old in sorted(tagged):
        if root is None or key[:len(root)] != root:
            root, anchored = key, False
            groups += 1
        if old and not anchored:
            anchored = True
            with_old += 1
    reserve = judged - matched
    count = max(0, groups - with_old - reserve)
    basis = "快照名册" if known or judged == 0 else "旧/无效名册，按快照文件数扣减"
    return count, (f"{basis}；有效指纹组 {groups}，含旧会话 {with_old} 组，"
                   f"未定位旧文件预留 {reserve}，无有效序列不计 {len(files) - len(tagged)} 个；新增保守计 {count}")


def find_new_families(signals):
    """H6：相对上次判定新出现的失败族（classify 输出 newKinds；首跑不报）。"""
    if not signals:
        return []
    new = signals.get("newKinds")
    if not new:
        return []
    real = signals.get("real") or {}
    return [(k, real.get(k, 0)) for k in new if real.get(k, 0) >= 1]


def main():
    src = count_src()
    files = glob.glob(os.path.join(RAW, "*.jsonl"))
    raw = len(files)
    signals = _read_json(SIGNALS) if os.path.exists(SIGNALS) else None

    reasons = []
    go = False

    if not signals:
        reasons.append("尚未运行判定（无 failReasons.json）")
        go = True
    else:
        judged_sessions = signals.get("sessions", 0)
        judged_calls = signals.get("uniqueCalls", 0)
        new_sessions, basis = count_new_sessions(files, signals)
        reasons.append(f"新增口径：{basis}")
        avg_calls = (judged_calls / judged_sessions if type(judged_sessions) is int and judged_sessions > 0
                     and type(judged_calls) is int and judged_calls >= 0 else 0)
        est_new_calls = int(new_sessions * avg_calls)

        if new_sessions >= MIN_NEW_SESSIONS:
            reasons.append(f"新增会话 {new_sessions} ≥ {MIN_NEW_SESSIONS}")
            go = True
        if est_new_calls >= MIN_NEW_CALLS:
            reasons.append(f"估算新增调用 {est_new_calls} ≥ {MIN_NEW_CALLS}")
            go = True
        if src > raw:
            reasons.append(f"采集缺口：源 {src} 个 zstd > raw {raw} 个")
            go = True
        if new_sessions > 0:
            reasons.append(f"判定缺口：至少 {new_sessions} 个去重会话未判定")
            go = True

        new_fams = find_new_families(signals)
        if new_fams:
            reasons.append(f"新失败族 {len(new_fams)} 个：" +
                           "; ".join(f"{k[:40]}×{n}" for k, n in sorted(new_fams, key=lambda x: -x[1])[:3]))
            go = True

        try:
            gen = datetime.fromisoformat(signals.get("generatedAt", ""))
            age_days = (datetime.now() - gen).days
            if age_days > FALLBACK_DAYS:
                reasons.append(f"14 天兜底：距上次判定已 {age_days} 天")
                go = True
        except Exception:
            reasons.append("无法解析上次判定时间，按兜底处理")
            go = True

        if not go:
            reasons.append(f"数据不足（新增会话 {new_sessions} < {MIN_NEW_SESSIONS}、"
                           f"估算新增调用 {est_new_calls} < {MIN_NEW_CALLS}）——只跑离线采集/判定，不跑提案")

    print(f"[ready] 源 {src} 个 zstd / raw {raw} 个 jsonl / 判定快照 "
          f"{signals.get('sessions', '—') if signals else '无'}")
    for r in reasons:
        print(f"  - {r}")
    print(f"[ready] {'GO（数据够，可开轮次）' if go else 'NO-GO（数据不够，只做离线部分）'}")
    return 0 if go else 1


if __name__ == "__main__":
    raise SystemExit(main())
