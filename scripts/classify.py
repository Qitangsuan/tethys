#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TETHYS v2 判定器 · v2.1（按 qwen 拍板加 callId 去重）

契约（v0.2 §2）：
- 一条 tool error = tool/result 首文本以 "Error:"/"错误：" 开头（isError 不可靠，不作主判据）
- tool/call 与 tool/result 按 callId 配对
- **callId 全局去重**（qwen 拍板 B：副本会话/子代理继承的重复调用只计一次）
- 排除类（标注不计真失败）：host_fault / user_cancelled / tool_aborted
- 口径版本：caliber-2026-11-20-2
"""
import glob
import io

def _read_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_text(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _write_text(path, text):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT_DIR = os.path.join(ROOT, "data", "signals")
os.makedirs(OUT_DIR, exist_ok=True)

CALIBER_PATH = os.path.join(ROOT, "state", "caliber.json")
_CAL = _read_json(CALIBER_PATH)
CALIBER = _CAL["version"]
ERR_PREFIX = tuple(_CAL["anchors"]["errorPrefix"])
_EXIT_CODE = re.compile(_CAL["anchors"]["exitCodePattern"])
_CLEAN_EXIT = set(_CAL["anchors"]["cleanExitCodes"])
_HOST_FAULT = tuple(m.lower() for m in _CAL["exclusion"]["host_fault"])
_USER_CANCEL = tuple(m.lower() for m in _CAL["exclusion"]["user_cancelled"])
_ABORT = tuple(m.lower() for m in _CAL["exclusion"]["tool_aborted"])
MAX_SAMPLES = 3


def text_of(result_data):
    out = []
    for c in (result_data.get("message") or {}).get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            out.append(c.get("text") or "")
    return "\n".join(out)


def classify(tool, text):
    """返回 (kind, is_real)。kind=None 表示非失败。"""
    if not text:
        return None, False
    head = text.strip()
    low = head[:600].lower()
    if not head.startswith(ERR_PREFIX):
        # H4（GLM）：pwsh 失败锚点扩展——旧版第二大族 exit_1_real 是 stdout + [exit code: N]，无 Error: 前缀
        m = _EXIT_CODE.search(head)
        if m:
            code = int(m.group(1))
            if code != 0:
                has_err = any(w in low for w in ("error", "错误", "failed", "cannot", "not found", "denied"))
                if code in _CLEAN_EXIT and not has_err:
                    return "clean_exit", False
                return ("exit_1_real" if code == 1 else f"exit_code_{code}"), True
        return None, False
    low = head[:600].lower()
    if any(m in low for m in _HOST_FAULT):
        return "host_fault", False
    if any(m in low for m in _USER_CANCEL):
        return "user_cancelled", False
    if any(m in low for m in _ABORT):
        return "tool_aborted", False
    if "old_string was not found" in low or "no matching text" in low or "old_string matched" in low:
        return "edit_no_match", True
    if "file has not been read" in low:
        return "file_not_read", True
    if ("file changed since" in low or "file has changed" in low or "no longer exists" in low):
        return "file_conflict", True
    if "is out of range for" in low:
        return "read_out_of_range", True
    if ("cannot read" in low or "no such file" in low
            or ("not found" in low and ("file" in low or "path" in low))):
        return "file_read_missing", True
    if "fetch failed" in low or "web fetch failed" in low or "econn" in low:
        return "web_fetch_fail", True
    if "out of memory" in low or "timeout" in low or "timed out" in low:
        return "tool_timeout", True
    first = head.splitlines()[0][:80]
    return f"tool_error::{first}", True


def main():
    files = sorted(glob.glob(os.path.join(RAW, "*.jsonl")))
    real = {}
    excluded = {}
    samples = {}
    timeline = []
    total_calls = 0
    unique_calls = 0
    dup_calls = 0
    unrouted = 0
    bad_lines = 0
    unreadable = 0
    orphan_calls = 0
    seen_calls = set()

    for fp in files:
        sid = os.path.basename(fp).replace(".jsonl", "")
        calls = {}
        resolved = set()
        created_at = None
        session_calls = 0
        session_fails = 0
        session_kinds = {}
        try:
            text = _read_text(fp)
        except Exception:
            unreadable += 1
            continue
        if not text.strip():
            unreadable += 1
            continue
        # keepends：保留原始分隔符，拼接时才能原样还原被拆开的记录
        lines = text.splitlines(keepends=True)
        i = 0
        n_lines = len(lines)
        while i < n_lines:
            if not lines[i].strip():
                i += 1
                continue
            # 容错解析：tool/result 正文可能带裸换行（写盘时未转义），导致一条 JSON 被行切成多行。
            # 两级修复：① keepends 原样拼回（正常的多行 JSON / \u2028 类分隔）；② 把裸换行转义回 \n 再解。
            # 最多拼 32 行；全失败才记坏行（不静默吞）。
            o = None
            span = 1
            while span <= 32 and i + span <= n_lines:
                raw = "".join(lines[i:i + span])
                try:
                    o = json.loads(raw)
                    break
                except Exception:
                    pass
                if span > 1:
                    try:
                        esc = raw.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r")
                        o = json.loads(esc)
                        break
                    except Exception:
                        pass
                span += 1
            if o is None:
                bad_lines += 1
                i += 1
                continue
            i += span
            typ = o.get("type")
            d = o.get("data") or {}
            if typ == "session" and created_at is None:
                created_at = o.get("createdAt") or o.get("time")
            elif typ == "tool/call":
                calls[d.get("callId")] = {
                    "name": d.get("name") or "",
                    "seq": o.get("seq"),
                    "time": o.get("time"),
                }
                total_calls += 1
            elif typ == "tool/result":
                cid = d.get("toolCallId") or (d.get("message") or {}).get("toolCallId")
                call = calls.get(cid)
                if not call:
                    unrouted += 1
                    continue
                resolved.add(cid)
                if cid in seen_calls:
                    dup_calls += 1
                    continue
                seen_calls.add(cid)
                unique_calls += 1
                session_calls += 1
                txt = text_of(d)
                kind, is_real = classify(call["name"], txt)
                if kind is None:
                    continue
                bucket = real if is_real else excluded
                bucket[kind] = bucket.get(kind, 0) + 1
                if is_real:
                    session_fails += 1
                    session_kinds[kind] = session_kinds.get(kind, 0) + 1
                if len(samples.setdefault(kind, [])) < MAX_SAMPLES:
                    samples[kind].append({
                        "session": sid, "seq": call["seq"], "time": call["time"],
                        "tool": call["name"],
                        "first_line": txt.strip().splitlines()[0][:200] if txt.strip() else "",
                    })
        # 有 call 没有 result 的（用户中断、会话被截断、或解析失败）——报出来，别当不存在
        orphan_calls += sum(1 for cid in calls if cid not in resolved)
        if session_calls:
            timeline.append({
                "session": sid, "createdAt": created_at,
                "calls": session_calls, "realFails": session_fails,
                "kinds": session_kinds,
            })

    # H6：新失败族检测（相对上次判定的新增 kind；首跑建立基线、不报新）
    # 观测器不许静默失明（v1 教训：doctor.py 把坏 JSON 吞成 OK，44 轮「指标干净」全建在会说谎的自检上）：
    # kind-history 读不出来时不再当作「全部已知」，而是按全量重报并显式告警，让上层看见。
    hist_path = os.path.join(OUT_DIR, "kind-history.json")
    cur_kinds = set(real.keys())
    prev_kinds = cur_kinds
    prev_updated = None
    history_ok = True
    if os.path.exists(hist_path):
        try:
            hist = _read_json(hist_path)
            prev_kinds = set(hist.get("kinds") or [])
            prev_updated = hist.get("updatedAt")
        except Exception as exc:
            history_ok = False
            prev_kinds = set()
            print(f"[classify] !! kind-history.json 读取失败（{type(exc).__name__}: {exc}）；"
                  f"本轮按全量重报新失败族，不静默吞掉")
    new_kinds = sorted(cur_kinds - prev_kinds)
    _write_text(hist_path, json.dumps(
        {"updatedAt": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
         "kinds": sorted(cur_kinds)}, ensure_ascii=False, indent=2))

    out = {
        "caliber": CALIBER,
        "previousKinds": sorted(prev_kinds),
        "newKinds": new_kinds,
        # 上次判定时间（ISO，本地时区）；ready 用它区分「新出现的失败族」与「新采到的旧失败」
        "previousGeneratedAt": prev_updated,
        # false = 历史不可读、本轮新族判定不可信；ready 要把它当告警，不当正常
        "previousHistoryOk": history_ok,
        "generatedAt": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "sessions": len(files),
        "rawSessionIds": [os.path.splitext(os.path.basename(fp))[0] for fp in files],  # ready 的完整快照名册
        "totalCalls": total_calls,
        "uniqueCalls": unique_calls,
        "duplicatedCalls": dup_calls,
        "unroutedResults": unrouted,
        "badLines": bad_lines,
        "unreadableSessions": unreadable,
        "orphanCalls": orphan_calls,
        "real": dict(sorted(real.items(), key=lambda kv: -kv[1])),
        "excluded": dict(sorted(excluded.items(), key=lambda kv: -kv[1])),
        "samples": samples,
        "sessionTimeline": sorted(timeline, key=lambda t: t["createdAt"] or 0),
    }
    dst = os.path.join(OUT_DIR, "failReasons.json")
    _write_text(dst, json.dumps(out, ensure_ascii=False, indent=2))

    print(f"[classify] 会话 {out['sessions']} / 调用 {total_calls}（唯一 {unique_calls}，重复 {dup_calls} / "
          f"{(dup_calls / total_calls * 100 if total_calls else 0):.1f}%）/ 未配对 {unrouted}")
    if bad_lines or unreadable or orphan_calls:
        print(f"[classify] !! 数据缺口：无法解析的行 {bad_lines}，读不出的会话 {unreadable}，"
              f"有调用无结果的 {orphan_calls}（不是 0 才算正常）")
    print(f"[classify] 真失败 {sum(real.values())}:")
    for k, n in out["real"].items():
        print(f"  {k}: {n}")
    print(f"[classify] 排除类 {sum(excluded.values())}:")
    for k, n in out["excluded"].items():
        print(f"  {k}: {n}")
    print(f"[classify] 输出 → {dst}")


if __name__ == "__main__":
    main()
