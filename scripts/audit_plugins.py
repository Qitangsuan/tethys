#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""潮汐插件健康度审查（audit_plugins）：哪个插件在干活/添乱/该卸。

数据：
- 工具→插件映射：扫 profile node_modules 里第三方包的工具注册（`tools.register("name"`）
- 调用/失败：扫 data/raw（tool/call + tool/result，classify 口径，callId 去重）

输出：data/audit/plugin-audit.md
- 插件使用率（调用数/会话数）
- 插件错误率 + 失败归因（该插件的工具失败属于哪些失败族）
- 状态：活跃 / 静默（0 调用）/ 添乱（失败率高）
"""
import glob
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import classify as C  # noqa: E402

RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "audit", "plugin-audit.md")
PROFILE_NM = os.path.expanduser(os.path.join("~", ".dsh", "profiles", "desktop", "node_modules"))

# 内置工具（DSH 核心，不归插件）
BUILTIN = {
    "read", "write", "edit", "glob", "grep", "pwsh", "bash", "job_list", "job_output", "job_kill",
    "web_fetch", "web_search", "subagent", "subagent_fork", "list_agents", "send_message", "wait_agent",
    "todo_write", "skill", "ask_user_question", "canvas_state", "view_canvas", "present",
    "schedule_create", "schedule_list", "schedule_view", "schedule_edit",
    "cordis_inspect_list", "cordis_inspect_query", "load_workspace_dependencies",
    "create_goal", "get_goal", "update_goal", "create_worktree", "working_directory",
    "read_image", "present",
    "plugin_manager", "plugin_market",
    "remote_create_session", "remote_send_message", "remote_list_hosts", "remote_list_workspaces",
    "remote_list_sessions", "remote_read_session", "remote_wait_sessions", "remote_interrupt_session",
    "remote_upload_file", "remote_download_file", "remote_get_operation", "remote_list_presets",
    "remote_select_preset",
    "session_search", "session_trace", "session_event_search", "session_event_read",
    "session_event_trace",
    # 运行时/内置（不在 node_modules 源码里，归内置）
    "team_task_update", "team_task_get", "team_task_create", "team_task_list", "spawn_teammate",
    "run_code", "memory_update", "memory_save", "memory_search", "memory_archive",
    "context_recall", "send_meme",
}

# 已知映射（扫不到注册、但确定归属插件）
KNOWN_MAP = {
    "save_token_expand": "dsh-plugin-save-token",
    "generate_image": "dsh-image-gen", "generate_images": "dsh-image-gen",
    "edit_image": "dsh-image-gen", "imagegen": "dsh-image-gen",
    "canvas_state": "canvas-workbench", "view_canvas": "canvas-workbench",
    "find_photos": "dsh-design-studio", "find_inspiration": "dsh-design-studio",
    "pick_style": "dsh-design-studio", "check_design": "dsh-design-studio",
    "look_design": "dsh-design-studio", "compare_design": "dsh-design-studio",
    "preview_page": "dsh-design-studio", "handoff_design": "dsh-design-studio",
    "extract_design_system": "dsh-design-studio", "save_frame": "dsh-design-studio",
    "modlens_read_image": "@liustack/modlens",
    "deepseek_tts": "@local/dsh-deepseek-app", "deepseek_asr": "@local/dsh-deepseek-app",
    "deepseek_ocr": "@local/dsh-deepseek-app",
    "schedule_quick": "@local/dsh-schedule-quick",
    "mimo_understand_image": "@yyfather/dsh-mimo-plugin", "mimo_understand_audio": "@yyfather/dsh-mimo-plugin",
    "mimo_transcribe_audio": "@yyfather/dsh-mimo-plugin", "mimo_understand_video": "@yyfather/dsh-mimo-plugin",
    "mimo_tts": "@yyfather/dsh-mimo-plugin", "mimo_design_voice": "@yyfather/dsh-mimo-plugin",
    "mimo_clone_voice": "@yyfather/dsh-mimo-plugin", "mimo_list_models": "@yyfather/dsh-mimo-plugin",
    "mimo_web_search": "@yyfather/dsh-mimo-plugin",
}


def extract_tool_map():
    """扫第三方包源码，提取 工具名 → 包名。"""
    mapping = {}
    if not os.path.isdir(PROFILE_NM):
        return mapping
    for entry in os.listdir(PROFILE_NM):
        scoped = entry.startswith("@")
        pkg_dirs = []
        if scoped:
            for sub in os.listdir(os.path.join(PROFILE_NM, entry)):
                pkg_dirs.append((f"{entry}/{sub}", os.path.join(PROFILE_NM, entry, sub)))
        else:
            pkg_dirs.append((entry, os.path.join(PROFILE_NM, entry)))
        for pkg_name, pkg_dir in pkg_dirs:
            if pkg_name.startswith("@deepseek-ai/"):
                continue  # 内置包，工具是核心
            pat = re.compile(r"tools\.register\(\s*\{\s*name:\s*['\"]([\w.:-]+)['\"]")
            names = []
            for root, _dirs, files in os.walk(pkg_dir, followlinks=True):
                for f in files:
                    if not f.endswith(".js"):
                        continue
                    try:
                        text = io.open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
                    except Exception:
                        continue
                    names += pat.findall(text)
            if names:
                for n in set(names):
                    mapping[n] = pkg_name
    return mapping


def text_of(result_data):
    out = []
    for c in (result_data.get("message") or {}).get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            out.append(c.get("text") or "")
    return "\n".join(out)


def main():
    tool_map = extract_tool_map()
    tool_map.update(KNOWN_MAP)
    tool_calls = Counter()
    tool_fails = Counter()
    tool_kinds = defaultdict(Counter)
    tool_sessions = defaultdict(set)
    seen = set()
    for fp in sorted(glob.glob(os.path.join(RAW, "*.jsonl"))):
        sid = os.path.basename(fp).replace(".jsonl", "")
        calls = {}
        for ln in io.open(fp, encoding="utf-8"):
            try:
                o = json.loads(ln)
            except Exception:
                continue
            t = o.get("type")
            d = o.get("data") or {}
            if t == "tool/call":
                cid = d.get("callId")
                name = d.get("name") or ""
                if cid:
                    calls[cid] = name
                tool_calls[name] += 1
                tool_sessions[name].add(sid)
            elif t == "tool/result":
                cid = d.get("toolCallId") or (d.get("message") or {}).get("toolCallId")
                if cid in seen:
                    continue
                seen.add(cid)
                name = calls.get(cid)
                if not name:
                    continue
                kind, is_real = C.classify(name, text_of(d))
                if is_real:
                    tool_fails[name] += 1
                    tool_kinds[name][kind] += 1

    # 按插件汇总
    plugins = defaultdict(lambda: {"tools": [], "calls": 0, "fails": 0, "sessions": set(), "kinds": Counter()})
    for name, n in tool_calls.items():
        plugin = tool_map.get(name, "（内置/未识别）" if name in BUILTIN else "（未知工具）")
        p = plugins[plugin]
        p["tools"].append(name)
        p["calls"] += n
        p["fails"] += tool_fails.get(name, 0)
        p["sessions"].update(tool_sessions.get(name, set()))
        p["kinds"].update(tool_kinds.get(name, Counter()))

    total_calls = sum(tool_calls.values())
    total_fails = sum(tool_fails.values())
    lines = [f"# 潮汐插件健康度审查 · {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
             f"数据：{len(seen)} 次配对调用 / 真失败 {total_fails}；工具映射：{len(tool_map)} 个插件工具。", "",
             "| 插件 | 工具数 | 调用数 | 会话数 | 真失败 | 失败率 | 状态 |", "|---|---:|---:|---:|---:|---:|---|"]
    rows = []
    for plugin, p in plugins.items():
        calls = p["calls"]
        rate = p["fails"] / calls * 100 if calls else 0
        if calls == 0:
            state = "静默"
        elif rate >= 20:
            state = "**添乱**"
        elif rate <= 5:
            state = "活跃"
        else:
            state = "正常"
        rows.append((plugin, p, rate, state))
    rows.sort(key=lambda r: -r[1]["calls"])
    for plugin, p, rate, state in rows:
        lines.append(f"| {plugin} | {len(p['tools'])} | {p['calls']} | {len(p['sessions'])} | "
                     f"{p['fails']} | {rate:.1f}% | {state} |")

    # 失败归因（有失败的工具）
    lines += ["", "## 失败归因（哪个工具/插件在添乱）", "", "| 工具 | 插件 | 失败数 | 主要失败类型 |", "|---|---|---:|---|"]
    rows2 = []
    for name, n in tool_fails.items():
        plugin = tool_map.get(name, "（内置/未识别）" if name in BUILTIN else "（未知工具）")
        top = "；".join(f"{k[:40]}×{c}" for k, c in tool_kinds[name].most_common(2))
        rows2.append((name, plugin, n, top))
    rows2.sort(key=lambda r: -r[2])
    for name, plugin, n, top in rows2[:20]:
        lines.append(f"| `{name}` | {plugin} | {n} | {top} |")

    # 静默/未知：从映射反查「有工具但一次没被调用」的插件
    called_plugins = {p for p in plugins}
    silent_by_map = defaultdict(list)
    for tool, plugin in tool_map.items():
        if tool not in tool_calls:
            silent_by_map[plugin].append(tool)
    silent = [f"{p}（工具：{', '.join(ts[:5])}）" for p, ts in silent_by_map.items() if p not in called_plugins]
    unknown = [n for n in tool_calls if n not in tool_map and n not in BUILTIN]
    lines += ["", "## 静默插件（有工具定义、但本快照 0 调用）", ""]
    lines += [f"- {s}" for s in sorted(silent)] or ["（无）"]
    lines += ["", "## 未识别工具（不在内置清单、也没扫到注册）", ""]
    lines += [f"- `{n}` ×{tool_calls[n]}" for n in sorted(unknown, key=lambda x: -tool_calls[x])] or ["（无）"]
    lines += ["", "## 说明", "",
              "- 「静默」= 本快照内 0 次调用：可能是没用过、也可能是工具名没扫到（插件目录/注册方式不同）。",
              "- 「添乱」= 失败率 ≥20%（样本少时不可靠，看调用数）。",
              "- 工具→插件映射来自扫 `tools.register(\"name\"` 模式；扫不到的工具归「内置/未识别」。",
              "- 与 dsh-plugin-prune 的差异：本表按**失败归因**看插件，不只使用量。"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:30]))
    print(f"\n[audit_plugins] 输出 → {OUT}")


if __name__ == "__main__":
    main()
