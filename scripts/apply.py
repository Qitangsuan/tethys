#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TETHYS v2 注入器：dist/AGENTS.evolve.md → ~/.dsh/AGENTS.md 的 tethys 标记块。

用法：
  python scripts/apply.py            # 注入（幂等，自动备份）
  python scripts/apply.py --revert   # 删除标记块（回滚）
  python scripts/apply.py --list     # 显示注入状态（条数/字符数）

契约（v0.2）：
- dist/AGENTS.evolve.md 是唯一真源；注入块用 BEGIN/END 标记包围。
- 每次注入前备份到 state/backup/AGENTS.md.<ts>.bak。
- 幂等：块已存在则替换内容，不重复追加。
"""
import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist", "AGENTS.evolve.md")
BACKUP_DIR = os.path.join(ROOT, "state", "backup")
AGENTS = os.path.expanduser(os.path.join("~", ".dsh", "AGENTS.md"))

BEGIN = "<!-- tethys:begin 由 dsh-evolve 自动维护，勿手改；撤销：python scripts/apply.py --revert -->"
END = "<!-- tethys:end -->"


def read(path):
    return io.open(path, encoding="utf-8").read() if os.path.exists(path) else ""


def backup(text):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"AGENTS.md.{ts}.bak")
    io.open(dst, "w", encoding="utf-8").write(text)
    return dst


def find_block(text):
    """返回 (begin_idx, end_idx) 或 None。"""
    b = text.find(BEGIN)
    if b < 0:
        return None
    e = text.find(END, b)
    if e < 0:
        return None
    return b, e


def inject():
    body = read(DIST).strip()
    if not body:
        print("[apply] dist/AGENTS.evolve.md 为空，中止")
        return 1
    n_rules = sum(1 for ln in body.splitlines() if ln.startswith("- **"))
    target = read(AGENTS)
    block = f"{BEGIN}\n{body}\n{END}"
    span = find_block(target)
    if span:
        b, e = span
        new_text = target[:b] + block + target[e + len(END):]
        action = "更新"
    else:
        new_text = (target.rstrip() + "\n\n" + block + "\n") if target.strip() else (block + "\n")
        action = "追加"
    if new_text == target:
        print(f"[apply] 内容无变化（{n_rules} 条规则），跳过写盘")
        return 0
    bak = backup(target)
    io.open(AGENTS, "w", encoding="utf-8").write(new_text)
    # 注入后自检
    check = read(AGENTS)
    cspan = find_block(check)
    ok = cspan is not None and check[cspan[0]:cspan[1] + len(END)] == block
    print(f"[apply] {action} {AGENTS}（{len(target)} → {len(new_text)} 字符）")
    print(f"[apply] 备份：{os.path.relpath(bak, ROOT)}")
    print(f"[apply] 自检：{'OK' if ok else 'FAIL（注入块与 dist 不一致）'}")
    return 0 if ok else 1


def revert():
    target = read(AGENTS)
    span = find_block(target)
    if not span:
        print("[apply] 没有 tethys 标记块，无需回滚")
        return 0
    b, e = span
    # 去掉块及块前的空行
    prefix = target[:b].rstrip() + "\n"
    new_text = prefix + target[e + len(END):].lstrip("\n")
    backup(target)
    io.open(AGENTS, "w", encoding="utf-8").write(new_text)
    print(f"[apply] 已回滚，删除标记块（{len(target)} → {len(new_text)} 字符）")
    return 0


def status():
    target = read(AGENTS)
    span = find_block(target)
    if not span:
        print("[apply] 未注入（无 tethys 标记块）")
        return 0
    b, e = span
    block = target[b:e + len(END)]
    n_rules = sum(1 for ln in block.splitlines() if ln.startswith("- **"))
    dist_body = read(DIST).strip()
    n_dist = sum(1 for ln in dist_body.splitlines() if ln.startswith("- **"))
    print(f"[apply] 已注入：{n_rules} 条规则 / {len(block)} 字符（含标记）；dist 规则 {n_dist} 条")
    print(f"[apply] 一致：{'是' if n_rules == n_dist else '否（dist 有变化，建议重跑 apply）'}")
    return 0


def main():
    args = sys.argv[1:]
    if "--revert" in args:
        return revert()
    if "--list" in args:
        return status()
    return inject()


if __name__ == "__main__":
    sys.exit(main())
