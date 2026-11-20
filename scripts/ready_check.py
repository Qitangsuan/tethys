#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""潮汐数据门槛每日检查：跑 ready.py，结果写状态文件；GO 时在桌面留标记（零打扰，仅数据够时提醒）。

用法：python scripts/ready_check.py
计划任务：每天 10:00 跑一次（错过开机补跑）。
"""
import io
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "data", "status.md")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
MARKER_PREFIX = "潮汐-数据够了-"


def main():
    py = sys.executable
    r = subprocess.run(
        [py, "-X", "utf8", os.path.join(ROOT, "scripts", "ready.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    go = r.returncode == 0
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    # 状态文件（每次覆盖，最近一次结果）
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    io.open(STATUS, "w", encoding="utf-8").write(
        f"# 潮汐数据门槛状态\n\n最近检查：{ts}\n结果：{'**GO（数据够，可开轮次）**' if go else 'NO-GO（数据不够，静默）'}\n\n"
        f"```\n{out.strip()}\n```\n")

    # GO 时在桌面留标记（区分「量够」vs「有新失败族」，不重复刷屏）
    existing = [f for f in os.listdir(DESKTOP) if f.startswith(MARKER_PREFIX)] if os.path.isdir(DESKTOP) else []
    if go and not existing:
        volume_ok = ("新增会话" in out) or ("新增调用" in out)
        if volume_ok:
            name = f"{MARKER_PREFIX}{time.strftime('%Y%m%d')}.txt"
            title = "潮汐数据量够了"
        else:
            name = f"潮汐-有新失败族-{time.strftime('%Y%m%d')}.txt"
            title = "潮汐发现新失败族（不是数据量够）"
        io.open(os.path.join(DESKTOP, name), "w", encoding="utf-8").write(
            f"{title}（{ts}）\n\n{out.strip()}\n\n要不要开一轮？打开 DSH 跟 lead 说一声。\n")

    print(out.strip())
    print(f"[ready_check] {ts} → {'GO（桌面已留标记）' if go and not existing else ('GO' if go else 'NO-GO')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
