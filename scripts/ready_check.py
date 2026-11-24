#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""潮汐数据门槛每日检查：跑 ready.py，结果写状态文件；有事可做时在桌面留标记（零打扰）。

用法：python scripts/ready_check.py
计划任务：每天 10:00 跑一次（错过开机补跑）。

ready.py 现在分三级：verify（样本够，可出验证判决）/ work（只可归因）/ none。
桌面标记按级区分，别把「有新失败族」说成「数据量够了」。
"""
import io
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "data", "status.md")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
MARKER_VERIFY = "潮汐-数据够了-"
MARKER_WORK = "潮汐-有事可做-"
OLD_MARKER_WORK = "潮汐-有新失败族-"


def parse_verdict(out):
    for line in (out or "").splitlines():
        if line.startswith("[ready] VERDICT="):
            return line.split("VERDICT=", 1)[1].split()[0].strip()
    return None


def main():
    py = sys.executable
    r = subprocess.run(
        [py, "-X", "utf8", os.path.join(ROOT, "scripts", "ready.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    verdict = parse_verdict(out)
    if verdict is None:  # 旧版 ready.py 无 VERDICT 行时按返回码回退
        verdict = "work" if r.returncode == 0 else "none"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    # 状态文件（每次覆盖，最近一次结果）
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    label = {"verify": "**verify（样本够，可开验证轮次）**",
             "work": "**work（有事可做，只可归因，不出验证判决）**",
             "none": "none（数据不够，静默）"}[verdict]
    io.open(STATUS, "w", encoding="utf-8").write(
        f"# 潮汐数据门槛状态\n\n最近检查：{ts}\n结果：{label}\n\n```\n{out.strip()}\n```\n")

    # 有活可做时在桌面留标记（区分 verify / work，不重复刷屏）
    existing = []
    if os.path.isdir(DESKTOP):
        existing = [f for f in os.listdir(DESKTOP)
                    if f.startswith(MARKER_VERIFY) or f.startswith(MARKER_WORK)
                    or f.startswith(OLD_MARKER_WORK)]
    if verdict in ("verify", "work") and not existing:
        if verdict == "verify":
            name = f"{MARKER_VERIFY}{time.strftime('%Y%m%d')}.txt"
            title = "潮汐样本量够了（可出验证判决）"
        else:
            name = f"{MARKER_WORK}{time.strftime('%Y%m%d')}.txt"
            title = "潮汐有事可做（只可归因，样本量不够出判决）"
        io.open(os.path.join(DESKTOP, name), "w", encoding="utf-8").write(
            f"{title}（{ts}）\n\n{out.strip()}\n\n要不要开一轮？打开 DSH 跟 lead 说一声。\n")

    print(out.strip())
    print(f"[ready_check] {ts} → {verdict}"
          f"{'（桌面已留标记）' if verdict in ('verify', 'work') and not existing else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
