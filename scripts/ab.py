#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TETHYS v2 A/B 对照工具 · v2（窗口化；GLM H2/H5 修复）

用法：
  python scripts/ab.py start --note "..."
  python scripts/ab.py check
  python scripts/ab.py status

契约（v0.2 / qwen 拍板 + GLM 修复）：
- 窗口化（H2）：基线前同长窗口（规则开）vs 基线后窗口（规则关）——不用历史累计快照（会被历史失败摊薄）。
- 每臂调用数下限 MIN_CALLS；不足判样本不足。
- 只比同签名断点；新出现 kind 单列。
- 规则集校验（H5）：caliber.rulesRevision == dist 哈希；且 dist == AGENTS 注入块（否则先 apply）。
"""
import hashlib
import io
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNALS = os.path.join(ROOT, "data", "signals", "failReasons.json")
AB_DIR = os.path.join(ROOT, "data", "ab")
os.makedirs(AB_DIR, exist_ok=True)
BASELINE = os.path.join(AB_DIR, "baseline.json")
DIST = os.path.join(ROOT, "dist", "AGENTS.evolve.md")
AGENTS = os.path.expanduser(os.path.join("~", ".dsh", "AGENTS.md"))
CALIBER = os.path.join(ROOT, "state", "caliber.json")

MIN_CALLS = 500
MIN_KIND = 5
BEGIN = "<!-- tethys:begin"
END = "<!-- tethys:end -->"

CAVEAT = ("替代解释：窗口内可能有配置变更/网络波动/会话构成差异；整块开关会同时影响多条规则，"
          "不能把差异归给单条规则。")


def _read_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_text(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _write_text(path, text):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def rules_revision():
    body = _read_text(DIST).strip()
    return "rules-" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]


def injected_block():
    text = _read_text(AGENTS)
    b = text.find(BEGIN)
    if b < 0:
        return None
    e = text.find(END, b)
    if e < 0:
        return None
    return text[b:e + len(END)]


def check_consistency():
    """H5：规则集三向一致——caliber.rulesRevision == dist 哈希 == AGENTS 注入块。"""
    cal = _read_json(CALIBER)
    expected = cal.get("rulesRevision")
    actual = rules_revision()
    if expected and expected != actual:
        print(f"[ab] 规则集与口径脱钩：caliber.rulesRevision={expected}，dist 实际 {actual}")
        print("[ab] 改了一头没改另一头（R21-R47 空转教训）——先同步再比")
        return False
    block = injected_block()
    if block is not None:
        body = _read_text(DIST).strip()
        want = "<!-- tethys:begin 由 dsh-evolve 自动维护，勿手改；撤销：python scripts/apply.py --revert -->\n" + body + "\n" + END
        if block != want:
            print("[ab] dist 与 AGENTS 注入块不一致——先跑 scripts/apply.py 再比")
            return False
    else:
        print("[ab] AGENTS 无 tethys 注入块——规则未生效，A/B 无意义")
        return False
    return True


def load_signals():
    if not os.path.exists(SIGNALS):
        print("[ab] 先跑 scripts/classify.py 生成 data/signals/failReasons.json")
        sys.exit(1)
    return _read_json(SIGNALS)


def parse_ts(iso):
    try:
        return datetime.fromisoformat(iso).timestamp() * 1000
    except Exception:
        return 0


def window_stats(timeline, start_ts, end_ts):
    calls = 0
    kinds = {}
    for s in timeline:
        c = s.get("createdAt") or 0
        if start_ts <= c < end_ts:
            calls += s.get("calls") or 0
            for k, v in (s.get("kinds") or {}).items():
                kinds[k] = kinds.get(k, 0) + v
    return calls, kinds


def start(note):
    data = load_signals()
    base = {
        "startedAt": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "caliber": data["caliber"],
        "rulesRevision": rules_revision(),
        "sessions": data["sessions"],
        "uniqueCalls": data["uniqueCalls"],
        "real": data["real"],
        "timeline": data["sessionTimeline"],
    }
    _write_text(BASELINE, json.dumps(base, ensure_ascii=False, indent=2))
    print(f"[ab] 基线：{base['startedAt']}；{base['sessions']} 会话 / {base['uniqueCalls']} 唯一调用；"
          f"真失败 {sum(base['real'].values())}")
    print("[ab] 对比方式：基线前同长窗口（规则开）vs 基线后窗口（规则关）")


def check():
    if not os.path.exists(BASELINE):
        print("[ab] 没有基线（先跑 ab.py start）")
        sys.exit(1)
    if not check_consistency():
        sys.exit(1)
    cal = _read_json(CALIBER)
    base = _read_json(BASELINE)
    cur = load_signals()
    if cur.get("caliber") != base.get("caliber"):
        print(f"[ab] 基线口径 {base.get('caliber')} ≠ 当前 {cur.get('caliber')}——先重跑 classify")
        sys.exit(1)

    base_ts = parse_ts(base["startedAt"])
    now_ts = datetime.now().timestamp() * 1000
    window_len = max(now_ts - base_ts, 1)
    # 规则开臂：基线前同长窗口（从基线 timeline 算）
    a_calls, a_kinds = window_stats(base.get("timeline") or [], base_ts - window_len, base_ts)
    # 规则关臂：基线后窗口（从当前 timeline 算）
    b_calls, b_kinds = window_stats(cur.get("sessionTimeline") or [], base_ts, now_ts)

    lines = [f"# A/B 对照（窗口化） · 基线 {base['startedAt']} → {cur['generatedAt']}", ""]
    lines.append(f"窗口长 {window_len / 3600000:.1f} 小时：规则开臂 {a_calls} 调用 / 规则关臂 {b_calls} 调用。")
    lines.append("")
    lines.append("| 失败类型 | 开臂 | 关臂 | 开臂密度/千次 | 关臂密度/千次 | 变化 | 判决 |")
    lines.append("|---|---|---|---|---|---|---|")
    for kind in sorted(set(a_kinds) | set(b_kinds), key=lambda k: -(a_kinds.get(k, 0) + b_kinds.get(k, 0))):
        a = a_kinds.get(kind, 0)
        b = b_kinds.get(kind, 0)
        if a == 0 and b > 0:
            lines.append(f"| {kind[:60]} | 0 | {b} | 0 | — | 新增 | 新出现（口径/环境） |")
            continue
        da = a / a_calls * 1000 if a_calls else 0
        db = b / b_calls * 1000 if b_calls else 0
        if a_calls < MIN_CALLS or b_calls < MIN_CALLS or (a + b) < MIN_KIND:
            verdict, change = "样本不足", "—"
        else:
            pct = (db - da) / da * 100 if da else 0
            change = f"{pct:+.0f}%"
            verdict = "下降" if pct <= -30 else ("上升" if pct >= 30 else "无显著")
        lines.append(f"| {kind[:60]} | {a} | {b} | {da:.2f} | {db:.2f} | {change} | {verdict} |")
    lines += ["", "## 替代解释声明", CAVEAT, "",
              "## 说明", "- 窗口化：两臂都是「窗口内增量」，不用历史累计（H2）。",
              "- 规则集三向一致：caliber.rulesRevision == dist == AGENTS 注入块（H5）。"]
    print("\n".join(lines))
    out = os.path.join(AB_DIR, "result-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".md")
    _write_text(out, "\n".join(lines) + "\n")
    print(f"\n[ab] 输出 → {out}")


def status():
    if not os.path.exists(BASELINE):
        print("[ab] 没有基线")
        return
    base = _read_json(BASELINE)
    print(f"[ab] 基线：{base['startedAt']}（{base['note']}）")
    print(f"[ab] {base['sessions']} 会话 / {base['uniqueCalls']} 唯一调用 / 真失败 {sum(base['real'].values())}")
    print(f"[ab] 规则集：{base.get('rulesRevision', '未记录')}")


def main():
    args = sys.argv[1:]
    note = args[args.index("--note") + 1] if "--note" in args and len(args) > args.index("--note") + 1 else ""
    if "start" in args:
        return start(note)
    if "check" in args:
        return check()
    if "status" in args:
        return status()
    print(__doc__)


if __name__ == "__main__":
    main()
