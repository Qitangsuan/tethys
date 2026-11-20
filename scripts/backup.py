#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TETHYS 自动备份：把「可丢失」的项目资产复制到项目目录之外，保留最近 N 份。

备份内容（不含 data/raw——285 MB 可随时重解压）：
- scripts/（代码）、dist/（规则真源）、docs/、README.md、ARCHITECTURE.md
- state/（caliber/rule-effects/changelog/backup）
- data/verify/（判决历史，不可再生）、data/signals/（判定快照）、data/audit/、data/ab/

备份位置：~/.dsh/backups/tethys/<时间戳>/（与项目目录完全解耦——项目目录被删也不丢）
保留：最近 KEEP 份，旧的自动清理。
"""
import glob
import io
import os
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_ROOT = os.path.expanduser(os.path.join("~", ".dsh", "backups", "tethys"))
KEEP = 7

FILES = ["README.md", "ARCHITECTURE.md"]
DIRS = ["scripts", "dist", "docs", "state"]
DATA_DIRS = ["data/verify", "data/signals", "data/audit", "data/ab"]


def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_ROOT, ts)
    os.makedirs(dest, exist_ok=True)

    copied = []
    for f in FILES:
        src = os.path.join(ROOT, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, f))
            copied.append(f)
    for d in DIRS + DATA_DIRS:
        src = os.path.join(ROOT, d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, d), dirs_exist_ok=True)
            copied.append(d + "/")

    total = 0
    for base, _, files in os.walk(dest):
        for f in files:
            total += os.path.getsize(os.path.join(base, f))

    # 保留最近 KEEP 份
    backups = sorted(glob.glob(os.path.join(BACKUP_ROOT, "*")), reverse=True)
    removed = []
    for old in backups[KEEP:]:
        if os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)
            removed.append(os.path.basename(old))

    print(f"[backup] {ts}：复制 {len(copied)} 项，{total / 1024:.0f} KB → {dest}")
    if removed:
        print(f"[backup] 清理旧备份 {len(removed)} 份：{', '.join(removed)}")
    print(f"[backup] 当前保留 {min(len(backups), KEEP)} 份（共 {len(backups)} 份历史）")


if __name__ == "__main__":
    main()
