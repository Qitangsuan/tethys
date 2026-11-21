#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""潮汐验证器：去重调用作暴露量，会话作推断单位。仅依赖标准库。"""
import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[1]
CALIBER = ROOT / "state/caliber.json"
SIGNALS = ROOT / "data/signals/failReasons.json"
EFFECTS = ROOT / "state/rule-effects.json"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def since_ts(date_str):
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def is_config_kind(kind, patterns=None):
    # 历史调用者可只传 kind；主循环显式传入同一份已加载的配置。
    if patterns is None:
        patterns = read_json(CALIBER)["configKinds"]
    folded = kind.casefold()
    return any(p.casefold() in folded for p in patterns if p.strip())


def rule_id_order(key):
    # JSON 对象键均为字符串。按有效位数、数字串排序，避免 int 的长度限制。
    if key.isascii() and key.isdecimal():
        digits = key.lstrip("0") or "0"
        return (0, len(digits), digits, key)
    return (1, 0, "", key)


def match_kind(kind, cfg):
    return kind in cfg.get("detects", ()) or any(p in kind for p in cfg.get("detectsPatterns", ()))


def audit(data, cal):
    if data.get("caliber") != cal["version"]:
        raise ValueError(f"口径不一致：{data.get('caliber')} ≠ {cal['version']}；请先重跑 classify")
    st = cal["statistics"]
    if not (0 < st["alpha"] < 1 and 0.5 < st["planningPower"] < 1 and st["adjustment"] == "bonferroni"
            and type(st["minSessionsPerArm"]) is int and st["minSessionsPerArm"] >= 2
            and type(st["minPositiveSessionsPerArm"]) is int and st["minPositiveSessionsPerArm"] >= 1
            and all(0 < r < 1 for r in st["planningRR"]) and all(0 < b <= 1000 for b in st["planningBaselinePer1000"])):
        raise ValueError("statistics 中的阈值、规划参数或校正方法无效")
    def count(x):
        return type(x) is int and x >= 0
    seen, counts, exposure = set(), Counter(), 0
    for s in data["sessionTimeline"]:
        sid, n, kinds = s["session"], s["calls"], s["kinds"]
        if not sid or sid in seen or not count(s["createdAt"]) or not s["createdAt"]:
            raise ValueError(f"会话 ID 重复/缺失或无有效时间：{sid}")
        if (not count(n) or not count(s["realFails"]) or not all(count(x) for x in kinds.values())
                or sum(kinds.values()) != s["realFails"] or s["realFails"] > n):
            raise ValueError(f"会话计数不守恒：{sid}")
        seen.add(sid)
        counts.update(kinds)
        exposure += n
    if (not count(data["sessions"]) or len(seen) > data["sessions"] or not count(data["uniqueCalls"])
            or exposure != data["uniqueCalls"] or not all(count(x) for x in data["real"].values())
            or counts != Counter(data["real"])):
        raise ValueError("会话调用和/逐键失败和与全局计数不符，或会话数异常；请检查快照，不生成判决")


# 正则化不完全 beta：对称变换 + 连分式（modified Lentz）。供 t 尾概率和条件精确法共用。
def _beta_fraction(a, b, x):
    tiny = 1e-300
    def nonzero(v):
        return v if abs(v) >= tiny else math.copysign(tiny, v)
    c, d = 1.0, 1 / nonzero(1 - (a + b) * x / (a + 1))
    h = d
    for m in range(1, 10001):
        k = 2 * m
        for w in (m * (b - m) * x / ((a + k - 1) * (a + k)),
                  -(a + m) * (a + b + m) * x / ((a + k) * (a + k + 1))):
            d, c = 1 / nonzero(1 + w * d), nonzero(1 + w / c)
            delta = d * c
            h *= delta
        if abs(delta - 1) < 2e-14:
            return h
    raise ArithmeticError("不完全 beta 未收敛，未生成判决")


def ibeta(a, b, x):
    if x <= 0 or x >= 1:
        return float(x >= 1)
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        result = front * _beta_fraction(a, b, x) / a
    else:
        result = 1 - front * _beta_fraction(b, a, 1 - x) / b
    return min(1.0, max(0.0, result))


def beta_quantile(a, b, p):
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if ibeta(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def exact_rr(y0, n0, y1, n1, alpha):
    """给定两臂事件总数的条件 Poisson：双侧等尾检验及其反演。"""
    if min(n0, n1) == 0:
        return None
    if y0 + y1 == 0:
        return (0.0, math.inf, 1.0)
    q = n1 / (n0 + n1)
    left = ibeta(y0, y1 + 1, 1 - q) if y0 else 1.0
    right = ibeta(y1, y0 + 1, q) if y1 else 1.0
    qlo = beta_quantile(y1, y0 + 1, alpha / 2) if y1 else 0.0
    comp_hi = beta_quantile(y0, y1 + 1, alpha / 2) if y0 else 0.0
    lo = (n0 / n1) * qlo / (1 - qlo)
    hi = (n0 / n1) * (1 - comp_hi) / comp_hi if comp_hi else math.inf
    return lo, hi, min(1.0, 2 * min(left, right))


def quantile(values, p):
    values = sorted(values)
    pos = p * (len(values) - 1)
    i = math.floor(pos)
    return values[i] + (pos - i) * (values[math.ceil(pos)] - values[i])


def arm_summary(rows):
    # rows = [(去重调用数, 匹配失败键和)]；仅正暴露量会话入组。
    g, n, y = len(rows), sum(n for n, _ in rows), sum(y for _, y in rows)
    rates = [1000 * k / c for c, k in rows]
    rate = y / n if n else None
    v = (g / (g - 1) * math.fsum(((k - c * rate) / y) ** 2 for c, k in rows)
         if g > 1 and y else None)
    return dict(g=g, n=n, y=y, rate=rate, v=v, positive=sum(k > 0 for _, k in rows),
                quartiles=[quantile(rates, p) for p in (0.25, 0.5, 0.75)] if rates else [],
                max_share=max((c / n for c, _ in rows), default=0))


def cluster_test(b, a, st):
    """两独立臂，簇残差线性化 + Welch–Satterthwaite t 近似；不是有限样本精确检验。"""
    if not min(b["n"], a["n"]):
        return None, "有一臂没有调用，无法比较事件率"
    if min(b["g"], a["g"]) < st["minSessionsPerArm"]:
        return None, "至少一臂的有调用会话数未达到配置下限"
    if min(b["positive"], a["positive"]) < st["minPositiveSessionsPerArm"]:
        return None, "至少一臂的命中会话数未达到配置下限，不能可靠估计聚类误差"
    v0, v1 = b["v"], a["v"]
    if not v0 or not v1:
        return None, "至少一臂的会话残差无变异，不能可靠估计聚类误差"
    theta, se = math.log(a["rate"] / b["rate"]), math.sqrt(v0 + v1)
    df = (v0 + v1) ** 2 / (v0 ** 2 / (b["g"] - 1) + v1 ** 2 / (a["g"] - 1))
    t = theta / se
    p = ibeta(df / 2, 0.5, df / (df + t * t))
    x = beta_quantile(df / 2, 0.5, st["alpha"])
    half = se * math.sqrt(df * (1 - x) / x)
    return (math.exp(theta - half), math.exp(theta + half), p), None


def analyze_rule(timeline, cfg, cal):
    cut = since_ts(cfg["since"])
    arms, details = [[], []], {}
    for s in timeline:
        if not s["calls"]:
            continue
        i = int(s["createdAt"] >= cut)
        y = 0
        for kind, value in s["kinds"].items():
            if not is_config_kind(kind, cal["configKinds"]) and match_kind(kind, cfg):
                details.setdefault(kind, [0, 0])[i] += value
                y += value
        arms[i].append((s["calls"], y))
    b, a = map(arm_summary, arms)
    cluster, reason = cluster_test(b, a, cal["statistics"])
    rr = None
    if b["n"] and a["n"]:
        rr = a["rate"] / b["rate"] if b["y"] else (math.inf if a["y"] else None)
    return dict(b=b, a=a, rr=rr, details=details, cluster=cluster, reason=reason,
                exact=exact_rr(b["y"], b["n"], a["y"], a["n"], cal["statistics"]["alpha"]))


def decide(result, m, alpha):
    if result["cluster"] is None:
        return None, "样本不足", result["reason"]
    adj = min(1.0, m * result["cluster"][2])
    rr = result["rr"]
    if adj <= alpha:
        verdict = "有效（观察性下降）" if rr < 1 else "变差（观察性上升）"
        return adj, verdict, "键和事件率" + ("下降" if rr < 1 else "上升") + "，且会话检验通过族校正；不作因果判定"
    trend = "下降" if rr < 1 else ("上升" if rr > 1 else "持平")
    return adj, "无显著", f"键和事件率点估计{trend}，未通过族校正，不能认定有效或变差"


def plan_calls(baseline, rr, power, alpha, m):
    z = NormalDist().inv_cdf(1 - alpha / (2 * m)) + NormalDist().inv_cdf(power)
    return math.ceil(z ** 2 * (1 + 1 / rr) / (baseline * math.log(rr) ** 2))


def fmt(x, digits=3):
    if x is None:
        return "—"
    return "∞" if math.isinf(x) else f"{x:.{digits}f}"


def pfmt(p):
    return "—" if p is None else f"{p:.4g}"


def interval(test):
    return "—" if test is None else f"[{fmt(test[0])}, {fmt(test[1])}]"


def render(data, effects, cal):
    audit(data, cal)
    cfgs, st = effects["rules"], cal["statistics"]
    alpha = st["alpha"]
    ids = sorted(cfgs, key=rule_id_order)
    nonnumeric = [k for k in ids if not (k.isascii() and k.isdecimal())]
    warning = None
    if nonnumeric:
        warning = "非数字规则键已排在数字键之后，仍按原规则参与筛选：" + ", ".join(map(repr, nonnumeric))
        print(f"[verify] 警告：{warning}", file=sys.stderr)
    family = [k for k in ids if cfgs[k].get("verify") is not False and cfgs[k].get("verify_mode") != "config_ticket"]
    m = len(family)  # 先定检验族；样本不足也保留名额，不按观测 p 值筛选。
    real = sum(data["real"].values())
    config = sum(n for k, n in data["real"].items() if is_config_kind(k, cal["configKinds"]))
    tl = data["sessionTimeline"]
    level = f"{(1-alpha)*100:g}%"
    lines = ["# 潮汐规则验证｜会话聚类版", "",
             f"快照 `{data['generatedAt']}` · 分类 `{data['caliber']}` · 验证 `{st['revision']}`。",
             f"{data['sessions']} 个会话文件；{sum(s['calls'] > 0 for s in tl)} 个有唯一调用的会话参与比较。"
             f"原始调用 {data['totalCalls']}，去重且已配对的调用 {data['uniqueCalls']}（唯一分母），重复结果 {data['duplicatedCalls']}。",
             f"真失败 {real}，配置态 {config} 不计入分子，剩余 {real-config}；逐会话调用和、逐键失败和已对账。", "",
             f"主判据：后/前事件率比 RR；RR=1 表示率不变。CI 为逐规则 {level} 近似区间，不是同时区间。"
             f"校正前为双侧会话聚类 t 近似；结论用 Bonferroni 调整后 p ≤ {alpha:g}（同窗口 m={m}，未估计者也占名额）。", "",
             "| 规则 | 前失败/调用（每千） | 后失败/调用（每千） | RR | 聚类 CI | 原始 p | 调整后 p | 判决及理由 |",
             "|---|---:|---:|---:|---|---:|---:|---|"]
    if warning:
        lines[2:2] = [f"> 警告：{warning}", ""]
    notes, exact_lines, skips = [], [], []
    for k in ids:
        cfg = cfgs[k]
        if k not in family:
            why = ("转配置工单，不验证" if cfg.get("verify_mode") == "config_ticket" else
                   "不可验证：" + (cfg.get("verify_reason") or cfg.get("note") or "无精确映射"))
            skips.append(f"- 规则 {k}（{cfg['title']}）：{why.rstrip('。')}。")
            continue
        r = analyze_rule(tl, cfg, cal)
        adj, verdict, why = decide(r, m, alpha)
        test, exact = r["cluster"], r["exact"]
        def exposure(arm):
            rate = arm["rate"]
            return f"{arm['y']}/{arm['n']}（{fmt(1000 * rate if rate is not None else None, 2)}）"
        lines.append(f"| {k} · {cfg['title']} | {exposure(r['b'])} | {exposure(r['a'])} | {fmt(r['rr'])} | "
                     f"{interval(test)} | {pfmt(test[2] if test else None)} | {pfmt(adj)} | **{verdict}**：{why}。 |")
        exact_lines.append(f"| {k} | {interval(exact)} | {pfmt(exact[2] if exact else None)} |")
        notes.extend([f"### 规则 {k} · {cfg['since']} 00:00 UTC", ""])
        for label, arm in (("前", r["b"]), ("后", r["a"])):
            q = arm["quartiles"]
            dist = f"{q[1]:.2f} [{q[0]:.2f}, {q[2]:.2f}]" if q else "—"
            notes.append(f"- {label}：会话 {arm['g']}，命中 {arm['positive']}；会话率中位数 [Q1, Q3] = {dist} /千次；"
                         f"最大会话占本臂调用 {arm['max_share']:.1%}。")
        notes.append("- 分项（前→后）：" + ("；".join(f"`{kind}` {v[0]}→{v[1]}" for kind, v in sorted(r["details"].items(), key=lambda item: (-sum(item[1]), item[0]))) or "无匹配事件") + "。")
        notes.append("")
    lines += ["", "## 独立 Poisson 敏感性对照（不能替代主判决）", "",
              f"假设独立、臂内同率，给定总事件数计算等尾精确 p 和 {level} 精确 RR 区间；未校正，不能消除会话相关。", "",
              "| 规则 | 精确 RR CI | 原始 p |", "|---|---|---:|", *exact_lines, "",
              "## 会话与键和明细", "", *notes, "## 未进入检验族", "", *skips, "",
              "## 下一窗口的样本量（规划近似，不是判决门槛）", ""]
    if m:
        lines.extend([f"等臂、独立稀有事件，功效 {st['planningPower']:.0%}、本轮 {m} 重 Bonferroni；期望失败数并非达标配额。", "",
                      "| 基线/千次 | 目标 RR | 每臂调用 | 前/后期望失败 |", "|---:|---:|---:|---:|"])
        for b in st["planningBaselinePer1000"]:
            for rr in st["planningRR"]:
                n = plan_calls(b / 1000, rr, st["planningPower"], alpha, m)
                lines.append(f"| {b:g} | {rr:g} | {n} | {b*n/1000:.1f} / {b*n*rr/1000:.1f} |")
    else:
        lines.append("没有待检验规则，不计算规划表。")
    lines.extend(["", "## 替代解释与边界", "",
                  "- 按会话创建时间切分；跨日会话不能重新分配到逐调用生效时点。会话大小、工具及任务构成、同期配置或网络变动均可能解释率差。",
                  "- 相关重试在同一会话内不当作独立证据；兄弟会话、同一天仍可能相关。少数命中会话或大簇会使近似区间不稳。",
                  "- 规则可能因既往失败升高而加入，存在回归均值；不能据此推断因果、实际覆盖率或单次可避免失败。未显著不等于证明无效。",
                  "- 只校正同一窗口的规则比较，不校正反复查看；正式复核宜预定新窗口。方法、历史判决对照和功效公式见 statistics-design.md。"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", type=Path, default=SIGNALS)
    parser.add_argument("--effects", type=Path, default=EFFECTS)
    parser.add_argument("--caliber", type=Path, default=CALIBER)
    parser.add_argument("--output", type=Path, default=ROOT / "data/verify" / (datetime.now().date().isoformat() + "-statistics.md"))
    args = parser.parse_args()
    try:
        text = render(read_json(args.signals), read_json(args.effects), read_json(args.caliber))
    except (ValueError, KeyError, ArithmeticError) as error:
        parser.exit(1, f"[verify] {error}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print("\n".join(text.splitlines()[:16]))
    print(f"[verify] 输出 → {args.output}")


if __name__ == "__main__":
    main()
