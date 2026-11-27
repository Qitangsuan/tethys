#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TETHYS v2 频率门槛：数据够不够开轮次。

用法：python scripts/ready.py

**分两级判**（2026-11-24 修）：旧版把「样本量够验证」和「有事可做」混成一个 GO，
新失败族一出现就报 GO，会诱导开一个必然「样本不足」的验证轮次。

验证样本门槛（任一满足 → VERDICT=verify，可出判决）：
1. 后臂有调用会话数 ≥ caliber.statistics.minSessionsPerArm
2. 后臂调用数 ≥ MIN_ARM_CALLS
（后臂 = 规则 since 之后创建的会话；旧版拿「上次判定以来的新增会话」当门槛，
刚判完就归零，永远报不出真实差距，2026-11-24 改）

有事可做（任一满足 → VERDICT=work，只归因/改规则，不出判决）：
3. 采集有缺口（源 zstd > raw jsonl）
4. 判定有缺口（按 callId 前缀去重、扣除快照后仍有新增会话）
5. 新增会话/估算调用达到重跑判定门槛
6. **新失败族**（H6）：真失败里出现未被任何规则覆盖的 kind，且该族有样本发生在
   上次判定之后（补采到的历史失败不算）
7. **14 天兜底**：距上次判定 > 14 天（防规则腐化静默积累）

三级结论：verify（样本够）/ work（有事可做）/ none（只跑离线采集+判定）。
返回码：0 = verify 或 work（有事推进），1 = none。
"""
import glob
import io
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
SIGNALS = os.path.join(ROOT, "data", "signals", "failReasons.json")
EFFECTS = os.path.join(ROOT, "state", "rule-effects.json")
CALIBER = os.path.join(ROOT, "state", "caliber.json")
SESSIONS = os.path.expanduser(os.path.join("~", ".dsh", "sessions"))

MIN_NEW_SESSIONS = 5
MIN_NEW_CALLS = 2000
MIN_ARM_CALLS = 2000   # 后臂调用下限（占位门槛；真实功效下限见 statistics-design.md 的规划表）
STAT_POWER_CALLS = 13075  # 检测 50% 降幅所需每臂调用（基线 5/千次，power 0.8，Bonferroni m=4）
FALLBACK_DAYS = 14
MIN_NEW_FAMILY = 2
MIN_ETA_WINDOW_DAYS = 3  # 观测窗口短于此天数不给 ETA：几小时窗口会把「调用/天」放大成荒谬数字


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


def post_arm(signals):
    """按规则生效日（所有规则 since 的最晚值，UTC 口径与 verify 一致）算后臂规模。

    返回 (会话数, 调用数, since 字符串)；取不到 since 时返回 None。
    这是「现在能不能出判决」的主判据：verify 的分臂就是按 since 切的，
    拿「上次判定以来的新增」当门槛会永远显示 0（刚判完就归零）。
    """
    try:
        rules = _read_json(EFFECTS).get("rules") or {}
    except (OSError, ValueError):
        return None
    dates = [r["since"] for r in rules.values()
             if isinstance(r, dict) and isinstance(r.get("since"), str) and r["since"]]
    if not dates:
        return None
    since = max(dates)
    try:
        cut = int(datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return None
    g = n = 0
    for s in signals.get("sessionTimeline") or []:
        calls = s.get("calls") or 0
        if calls and (s.get("createdAt") or 0) >= cut:
            g += 1
            n += calls
    return g, n, since


def eta_note(arm_calls, since, now=None):
    """按后臂样本估算攒到门槛要多久；窗口不足 MIN_ETA_WINDOW_DAYS 天时只报样本太少。

    刚重置（since 就是今天或昨天）时调用绝对量很小，拿几天当分母会把速度放大成
    荒谬值（5 次调用 / 1 天 → 5.0 次/天 → 还要 399 天）。窗口不足时不给数字，
    避免拿噪声当趋势；窗口够了再按旧口径算。
    """
    now = now or datetime.now(timezone.utc)
    since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    window_days = (now - since_dt).days
    if window_days < MIN_ETA_WINDOW_DAYS:
        return f"样本太少，无法估算（窗口 {max(0, window_days)} 天）"
    days = max(1, window_days)
    per_day = arm_calls / days
    if per_day > 0:
        eta_floor = max(0.0, (MIN_ARM_CALLS - arm_calls) / per_day)
        eta_power = max(0.0, (STAT_POWER_CALLS - arm_calls) / per_day)
        return (f"按当前 {per_day:.1f} 调用/天：达 {MIN_ARM_CALLS} 调用还需 ~{eta_floor:.0f} 天，"
                f"达功效规划 {STAT_POWER_CALLS} 调用还需 ~{eta_power:.0f} 天")
    return "后臂没有真实使用，无法估算攒样本速度"


def min_sessions_per_arm():
    try:
        return int(_read_json(CALIBER)["statistics"]["minSessionsPerArm"])
    except (OSError, ValueError, KeyError, TypeError):
        return 10


def find_new_families(signals):
    """H6：相对上次判定新出现的失败族（classify 输出 newKinds；首跑不报）。

    只认「上次判定之后真实发生」的失败：samples 里有样本时间时，取该族最大发生时间与
    previousGeneratedAt 比较；全部样本早于上次判定 = 补采到的历史数据，不算新族。
    没有可用时间证据时保守保留（宁可多报一次，不静默吞掉真新族）。
    """
    if not signals:
        return []
    new = signals.get("newKinds")
    if not new:
        return []
    real = signals.get("real") or {}
    samples = signals.get("samples") or {}
    prev_ms = None
    prev_iso = signals.get("previousGeneratedAt")
    if prev_iso:
        try:
            prev_ms = int(datetime.fromisoformat(prev_iso).timestamp() * 1000)
        except (TypeError, ValueError):
            prev_ms = None
    families = []
    for kind in new:
        n = real.get(kind, 0)
        if n < 1:
            continue
        times = [s.get("time") for s in samples.get(kind) or [] if type(s.get("time")) is int]
        if prev_ms is not None and times and max(times) <= prev_ms:
            continue
        families.append((kind, n))
    return families


def main():
    src = count_src()
    files = glob.glob(os.path.join(RAW, "*.jsonl"))
    raw = len(files)
    signals = _read_json(SIGNALS) if os.path.exists(SIGNALS) else None

    notes = []       # 口径/背景说明，不影响判决
    sample_hits = []  # 验证样本门槛：够才能出判决
    work_hits = []    # 有事可做：值得归因/设计规则，但不支持验证判决

    if not signals:
        notes.append("尚未运行判定（无 failReasons.json）")
        work_hits.append("尚未运行判定，先跑离线采集+判定")
    else:
        judged_sessions = signals.get("sessions", 0)
        judged_calls = signals.get("uniqueCalls", 0)
        new_sessions, basis = count_new_sessions(files, signals)
        avg_calls = (judged_calls / judged_sessions if type(judged_sessions) is int and judged_sessions > 0
                     and type(judged_calls) is int and judged_calls >= 0 else 0)
        est_new_calls = int(new_sessions * avg_calls)
        notes.append(f"新增口径：{basis}；新增会话 {new_sessions}，估算新增调用 {est_new_calls}")

        # 观测器自检：数据缺口和历史损坏都要说出来，不许静默当正常（v1 教训）
        if signals.get("previousHistoryOk") is False:
            notes.append("!! kind-history 损坏：本轮新失败族按全量重报，判据不可信，先查 data/signals/kind-history.json")
        if signals.get("badLines") or signals.get("unreadableSessions"):
            notes.append(f"!! 判定数据缺口：无法解析的原始行 {signals.get('badLines', 0)}、"
                         f"读不出的会话 {signals.get('unreadableSessions', 0)}")

        # 验证判决只看后臂规模：verify 按规则 since 分臂，后臂不够就只是归因轮。
        arm = post_arm(signals) if signals.get("sessionTimeline") else None
        if arm:
            arm_sessions, arm_calls, since = arm
            floor = min_sessions_per_arm()
            notes.append(f"后臂（since {since} 之后）：{arm_sessions} 个有调用会话 / {arm_calls} 次调用"
                         f"（会话下限 {floor}，调用下限 {MIN_ARM_CALLS}）")
            if arm_sessions >= floor:
                sample_hits.append(f"后臂会话 {arm_sessions} ≥ {floor}")
            if arm_calls >= MIN_ARM_CALLS:
                sample_hits.append(f"后臂调用 {arm_calls} ≥ {MIN_ARM_CALLS}")
            if not sample_hits:
                notes.append("后臂样本不足 —— 只能出行为层/归因结论，不能出效果判决")
                # 把「还差多久」摊开：这是路线判断的依据，不是催数据
                try:
                    notes.append(eta_note(arm_calls, since))
                except (TypeError, ValueError):
                    pass
        else:
            notes.append("没有可用的会话时间线，无法算后臂规模")

        if new_sessions >= MIN_NEW_SESSIONS:
            work_hits.append(f"新增会话 {new_sessions} ≥ {MIN_NEW_SESSIONS}，值得重跑判定")
        if est_new_calls >= MIN_NEW_CALLS:
            work_hits.append(f"估算新增调用 {est_new_calls} ≥ {MIN_NEW_CALLS}，值得重跑判定")

        if src > raw:
            work_hits.append(f"采集缺口：源 {src} 个 zstd > raw {raw} 个")
        if new_sessions > 0:
            work_hits.append(f"判定缺口：至少 {new_sessions} 个去重会话未判定")

        new_fams = find_new_families(signals)
        if new_fams:
            work_hits.append(f"新失败族 {len(new_fams)} 个：" +
                             "; ".join(f"{k[:40]}×{n}" for k, n in sorted(new_fams, key=lambda x: -x[1])[:3]))

        try:
            gen = datetime.fromisoformat(signals.get("generatedAt", ""))
            age_days = (datetime.now() - gen).days
            if age_days > FALLBACK_DAYS:
                work_hits.append(f"14 天兜底：距上次判定已 {age_days} 天")
        except (TypeError, ValueError) as exc:
            # 窄捕获：只吞「解析不了时间」这一种，别的异常要露出来
            notes.append(f"无法解析上次判定时间（{type(exc).__name__}: {exc}），按兜底处理")
            work_hits.append("判定时间不可解析，按 14 天兜底处理")

    # 数据缺口不改变 verdict，但必须跟着结论一起出现在人眼前——
    # 「样本数够」和「数据干净」是两件事，混成一个 GO 会让人拿脏样本出判决。
    data_gaps = []
    if signals:
        if signals.get("badLines"):
            data_gaps.append(f"坏行 {signals['badLines']}")
        if signals.get("unreadableSessions"):
            data_gaps.append(f"读不出会话 {signals['unreadableSessions']}")
        if signals.get("orphanCalls"):
            data_gaps.append(f"有调用无结果 {signals['orphanCalls']}")

    verdict = "verify" if sample_hits else ("work" if work_hits else "none")

    print(f"[ready] 源 {src} 个 zstd / raw {raw} 个 jsonl / 判定快照 "
          f"{signals.get('sessions', '—') if signals else '无'}")
    for r in notes:
        print(f"  - {r}")
    if sample_hits:
        print(f"[ready] 验证样本门槛：达到（{'；'.join(sample_hits)}）")
    else:
        print("[ready] 验证样本门槛：未达——可跑采集/判定/归因，不可出验证判决")
    for r in work_hits:
        print(f"  · 有事可做：{r}")
    label = {"verify": "GO（够开验证轮次；够不够出判决由 verify 的统计门判，不由本脚本判）",
             "work": "PARTIAL（只可归因，不可出验证判决）",
             "none": "NO-GO（数据不够，只做离线部分）"}[verdict]
    if verdict == "verify" and data_gaps:
        label += f" · ⚠ 数据有缺口（{'、'.join(data_gaps)}），判决须注明"
    print(f"[ready] VERDICT={verdict} · {label}")
    return 0 if verdict != "none" else 1


if __name__ == "__main__":
    raise SystemExit(main())
