# -*- coding: utf-8 -*-
"""从幸存的 ~/.dsh/AGENTS.md 提取 tethys 注入块，恢复 dist/AGENTS.evolve.md 真源。"""
import io
import os
import re

SRC = os.path.expanduser(os.path.join("~", ".dsh", "AGENTS.md"))
DST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "dist", "AGENTS.evolve.md")

text = io.open(SRC, encoding="utf-8").read()
b = text.index("<!-- tethys:begin")
e = text.index("<!-- tethys:end -->")
block = text[b:e]

lines = block.splitlines()
out = []
for ln in lines:
    if ln.startswith("<!-- tethys:begin"):
        continue  # 去掉标记行（保留标记的说明文字？约定 dist 只含规则正文）
    out.append(ln)
body = "\n".join(out).strip() + "\n"

os.makedirs(os.path.dirname(DST), exist_ok=True)
io.open(DST, "w", encoding="utf-8").write(body)

n_bullets = len(re.findall(r"^- \*\*", body, re.M))
print(f"dist/AGENTS.evolve.md 已恢复: {len(body)} 字符, {n_bullets} 条规则")
for ln in body.splitlines()[:4]:
    print("  " + ln[:100])
