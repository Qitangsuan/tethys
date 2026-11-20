#!/usr/bin/env python3
"""从任务包的聚合快照估算潮汐配对实验的吞吐与样本量；仅输出到终端。"""
import sys

sys.dont_write_bytecode = True  # 导入同目录 verify 时也不创建 __pycache__。

import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from verify import arm_summary, audit, is_config_kind, match_kind, plan_calls

PACK = Path(__file__).resolve().parents[1] / 'Temp' / 'WorkBuddy任务包-潮汐AB设计.md'
BEIJING = timezone(timedelta(hours=8))
RULES = ('6', '7', '9')
PAIRS = 42
USABLE = (1.0, 0.75, 0.50)


def read_sections():
    text = PACK.read_text(encoding='utf-8-sig')
    result = []
    for section in ('1.3', '1.4', '1.5'):
        pattern = (rf'^### {re.escape(section)}[^\S\n]+[^\n]*\n\s*'
                   r'```json[ \t]*\n(.*?)^```[ \t]*$')
        blocks = re.findall(pattern, text, re.MULTILINE | re.DOTALL)
        if len(blocks) != 1:
            raise ValueError(f'任务包 {section} 必须恰有一个紧随标题的 JSON 块')
        result.append(json.loads(blocks[0]))
    return result


def historical(data, effects, cal):
    audit(data, cal)
    matched = {key: tuple(r for r in RULES if match_kind(key, effects['rules'][r]))
               for key in data['real'] if not is_config_kind(key, cal['configKinds'])}
    per_rule = Counter()
    rows, dates = [], []
    calls, sessions = Counter(), Counter()
    for s in data['sessionTimeline']:
        day = datetime.fromtimestamp(s['createdAt'] / 1000, BEIJING).date()
        dates.append(day)
        if not s['calls']:
            continue
        y = 0
        for key, n in s['kinds'].items():
            ids = matched.get(key, ())
            if ids:
                y += n  # 逐键只加一次，即使键同时属于多条规则。
                for r in ids:
                    per_rule[r] += n
        rows.append((s['calls'], y))
        sessions[day] += 1
        calls[day] += s['calls']
    arm = arm_summary(rows)
    if arm['v'] is None:
        raise ValueError('估计历史簇方差需要至少两个有暴露会话和一个主终点事件')
    if arm['y'] != sum(n for k, n in data['real'].items() if matched.get(k)):
        raise ValueError('全局与逐会话的复合分子不一致')
    first, last = min(dates), max(dates)
    daily = [(first + timedelta(days=i)) for i in range((last - first).days + 1)]
    days = len(daily)
    print(f"历史：{data['sessions']} 个会话文件；{arm['g']} 个有暴露会话；"
          f"原始调用 {data['totalCalls']}，去重且已配对调用 {arm['n']}（唯一分母）。")
    print(f'北京时间 {first} 至 {last}，按会话创建日归集：{days} 个自然日，'
          f'其中 {sum(calls[d] == 0 for d in daily)} 个零调用日。')
    print('日期 | 有暴露会话 | 去重调用')
    for d in daily:
        print(f'{d} | {sessions[d]} | {calls[d]}')
    for label, values in [('每日有暴露会话', [sessions[d] for d in daily]),
                          ('每日调用', [calls[d] for d in daily]),
                          ('每有暴露会话调用', [n for n, _ in rows])]:
        print(f'{label}：均值 {sum(values)/len(values):.4f}，中位数 {median(values):g}，最大 {max(values)}')
    print('规则分项（可能重叠）：' + '，'.join(f'{r}={per_rule[r]}' for r in RULES))
    print(f"并集 {arm['y']}/{arm['n']} = {1000*arm['rate']:.6f}/千次；"
          f"{arm['positive']} 个命中会话。历史混合率用作基线情景，不是 OFF 臂实测率。")
    # 另以真实试验边界 04:00 回放两种配对起相；仅检查曝光，不拟造历史 ON/OFF。
    shifted = {(datetime.fromtimestamp(s['createdAt']/1000, BEIJING) - timedelta(hours=4)).date()
               for s in data['sessionTimeline'] if s['calls']}
    grid = [min(shifted)+timedelta(days=j) in shifted for j in range((max(shifted)-min(shifted)).days+1)]
    for offset in (0, 1):
        pairs = list(zip(grid[offset::2], grid[offset+1::2]))
        print(f'04:00 分窗历史回放，起相位 {offset}：双窗均有流量 {sum(a and b for a,b in pairs)}/{len(pairs)} 对。')
    return arm, days


def capacity(call_day, session_day):
    print('\n历史均值投影；可用比例同时缩放调用和等价会话，不表示删除窗口。')
    print('可用比例 | 周期 | 合计调用 | 合计等价会话 | 每臂调用 | 每臂等价会话')
    for share in USABLE:
        for label, days in [('单窗（24h）', 1), ('每对', 2), ('每6对', 12),
                            ('月度（30天）', 30), ('全程（42对）', 2 * PAIRS)]:
            calls, sessions = call_day * share * days, session_day * share * days
            arms = f'{calls/2:.2f} | {sessions/2:.2f}' if days > 1 else '当窗仅一臂 | 另一臂无暴露'
            print(f'{share:.0%} | {label} | {calls:.2f} | {sessions:.2f} | {arms}')


def planning(arm, days, st):
    call_day, session_day = arm['n'] / days, arm['g'] / days
    size = arm['n'] / arm['g']
    d = arm['y'] * arm['v']
    capacity(call_day, session_day)
    print(f"\n历史会话簇 v(logλ)={arm['v']:.9f}；D=Y×v={d:.6f}；工作倍率 max(1,D)={max(1,d):.6f}。")
    alpha, power = st['alpha'], st['planningPower']
    baselines = [b / 1000 for b in st['planningBaselinePer1000']]
    rrs = st['planningRR']
    if (alpha, power, baselines, rrs) != (0.05, 0.8, [0.005, 0.02], [0.5, 0.7]):
        raise ValueError('任务包规划参数与本设计不一致')
    print('整块 ON:OFF 的单一复合终点，双侧 α=.05，目标功效 .80，m=1；每对各臂一窗。')
    print('等价会话=目标调用/历史每会话平均调用，不是独立样本数；基线按未舍入值计算。')
    print('整天估时向上取整；单臂暴露日按100%吞吐；两臂历时=2×每臂目标/可用日调用，再向上取整。')
    print('完整配对估时=2×向上取整(每臂目标/每窗可用调用)；这些天数不改变预定84天期限。')
    print('最低均量为目标除以固定窗数的均值（显示值四舍五入），不是单窗/单对纳入门槛。')
    print('基线/千 | RR | 情景 | 每臂调用 | 每臂等价会话 | 单臂暴露日 | 两臂自然日(100%/75%/50%) | '
          '完整配对天数(100%/75%/50%) | 每对最低均量(调用/等价会话) | 每窗最低均量(调用/等价会话)')
    for b in [*baselines, arm['rate']]:
        for rr in rrs:
            independent = plan_calls(b, rr, power, alpha, 1)
            for label, n in [('独立', independent), ('工作倍率', math.ceil(independent * max(1, d)))]:
                equiv = n / size
                duration = '/'.join(str(math.ceil(2 * n / (p * call_day))) for p in USABLE)
                paired = '/'.join(str(2 * math.ceil(n / (p * call_day))) for p in USABLE)
                print(f'{1000*b:.6f} | {rr:g} | {label} | {n} | {equiv:.2f} | '
                      f'{math.ceil(n/call_day)} | {duration} | {paired} | '
                      f'{2*n/PAIRS:.2f}/{2*equiv/PAIRS:.2f} | {n/PAIRS:.2f}/{equiv/PAIRS:.2f}')
    print('非保证功效：独立 Wald 近似及历史会话簇倍率均不估计未来配对窗口的方差、时间相关或臂间吞吐差。')
    print('固定42对、84天；保留低流量、零流量窗口，不事后筛窗、补窗或看到显著即停。未达规划量不等于无效。')


def main():
    data, effects, cal = read_sections()
    arm, days = historical(data, effects, cal)
    planning(arm, days, cal['statistics'])


if __name__ == '__main__':
    main()
