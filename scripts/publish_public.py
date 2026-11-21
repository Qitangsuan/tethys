# -*- coding: utf-8 -*-
"""潮汐公开版一键更新：重建脱敏副本 → 敏感扫描 → git commit + push 到 GitHub。

用法：python scripts/publish_public.py
- 源：本仓库（dsh-evolve）
- 目标：同级目录 dsh-evolve-public（GitHub 公开仓库）
- 每次全量重建 public 目录内容，保留 .git

脱敏规则：
- 排除 state/backup（个人 AGENTS 备份）、scripts/*.cmd（含本机绝对路径）
- README 里的本机绝对路径替换为通用写法
- 推送前扫描：用户名/用户目录/疑似密钥值，命中即中止
"""
import io
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = ROOT
PUBLIC = os.path.join(os.path.dirname(ROOT), "dsh-evolve-public")
GIT = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Git", "cmd", "git.exe")

COPY_DIRS = ["scripts", "docs", "state"]
COPY_FILES = ["README.md", "ARCHITECTURE.md", "statistics-design.md", "ab-design.md"]
EXAMPLE_SRC = os.path.join(PRIVATE, "dist", "AGENTS.evolve.md")
EXAMPLE_DST = os.path.join(PUBLIC, "dist", "AGENTS.example.md")

# 敏感模式：用户目录（任意用户名/斜杠风格）、疑似密钥值
SENSITIVE = {
    "用户路径": re.compile(r"C:[\\/]{1,2}Users[\\/]{1,2}"),
    "疑似密钥值": re.compile(r"sk-[A-Za-z0-9]{16,}"),
}


def sanitize_readme():
    """把 README 里的本机绝对路径换成通用写法。"""
    path = os.path.join(PUBLIC, "README.md")
    t = io.open(path, encoding="utf-8").read()
    # 用户目录前缀用拼接构造，避免本文件出现连续反斜杠字面量（否则会被自己的扫描命中）
    ud = "C:" + chr(92) * 2 + "Users" + chr(92) * 2
    t = re.sub(ud + r'[^"]*?python\.exe', 'python', t)
    t = re.sub(ud + r'[^"]*?node\.exe', 'node', t)
    t = re.sub(ud + r'[^"]*?dsh-evolve', lambda m: 'path\\to\\dsh-evolve', t)
    io.open(path, "w", encoding="utf-8").write(t)


def main():
    # 1. 清理目标（保留 .git 与 LICENSE）
    for name in COPY_DIRS + COPY_FILES + ["dist"]:
        p = os.path.join(PUBLIC, name)
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)

    # 2. 复制（state 排除 backup；scripts 去掉 .cmd）
    for name in COPY_DIRS:
        if name == "state":
            shutil.copytree(os.path.join(PRIVATE, name), os.path.join(PUBLIC, name),
                            ignore=shutil.ignore_patterns("backup"))
        else:
            shutil.copytree(os.path.join(PRIVATE, name), os.path.join(PUBLIC, name))
    for cmd in ("backup.cmd", "ready_check.cmd"):
        p = os.path.join(PUBLIC, "scripts", cmd)
        if os.path.exists(p):
            os.remove(p)
    for name in COPY_FILES:
        shutil.copy2(os.path.join(PRIVATE, name), os.path.join(PUBLIC, name))
    os.makedirs(os.path.join(PUBLIC, "dist"), exist_ok=True)
    shutil.copy2(EXAMPLE_SRC, EXAMPLE_DST)

    # 3. 清理缓存
    for root, dirs, _files in os.walk(PUBLIC):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                dirs.remove(d)

    # 4. README 脱敏 + 敏感扫描
    sanitize_readme()
    hits = []
    for root, dirs, files in os.walk(PUBLIC):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            p = os.path.join(root, f)
            try:
                text = io.open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for label, pat in SENSITIVE.items():
                for m in pat.finditer(text):
                    line_no = text[:m.start()].count("\n") + 1
                    hits.append(f"{label} | {os.path.relpath(p, PUBLIC)}:{line_no}")
    if hits:
        print("!! 敏感命中，停止推送：")
        for h in hits[:20]:
            print("  ", h)
        sys.exit(1)
    print("[1] 公开版重建完成，敏感扫描 0 命中")

    # 5. git 提交 + 推送
    env = dict(os.environ)
    env["PATH"] = r"C:\Program Files\Git\cmd;" + env.get("PATH", "")

    def run(args):
        return subprocess.run([GIT] + args, cwd=PUBLIC, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)

    run(["add", "-A"])
    st = run(["status", "--porcelain"])
    if not st.stdout.strip():
        print("[2] 无变更，跳过提交")
    else:
        run(["commit", "-m", "sync: " + __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")])
        print("[2] 已提交:", st.stdout.strip().splitlines()[0] if st.stdout.strip() else "")
    r = run(["push", "origin", "master"])
    print("[3] push:", (r.stdout + r.stderr).strip()[-300:])
    if r.returncode != 0:
        print("!! push 失败（网络/凭据），可稍后重跑本脚本")
        sys.exit(1)
    print("[3] 推送成功 → https://github.com/Qitangsuan/tethys")


if __name__ == "__main__":
    main()
