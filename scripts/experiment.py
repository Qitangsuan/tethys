#!/usr/bin/env python3
"""固定 42 对的前瞻实验：一次性终末分析；不读取或改写用户的全局规则。"""
import argparse
import hashlib
import itertools
import json
import math
import os
import random
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import verify as v

DAY = 86400000
FOLLOW = 2 * DAY
PAIRS = 42
RULES = ('6', '7', '9')
# 在正式试验开始前固定。MC 次数、种子与观测结果无关。
DRAWS = 49999
SEED = 310728
TARGET = 6945
TZ = timezone(timedelta(hours=8))


def assigned(plan, at):
    i = (at - plan['startMs']) // DAY
    return int(i) if 0 <= i < len(plan['arms']) else None


def summarise(plan, data, effects, cal, meta):
    v.audit(data, cal)
    arms = plan['arms']
    totals = [[0, 0] for _ in arms]  # [n, y]，包括无暴露的随机化窗口
    sessions = {'ON': [], 'OFF': []}
    sub = defaultdict(lambda: {'ON': [0, 0, 0], 'OFF': [0, 0, 0]})
    evidence = {arm: [0, 0] for arm in sessions}  # 已证实分配一致：调用、会话
    singles = {k: {'ON': 0, 'OFF': 0} for k in RULES}
    for s in data['sessionTimeline']:
        i = assigned(plan, s['createdAt'])
        n = s['calls']
        if i is None or not n:
            continue
        a = arms[i]
        y = 0
        for kind, count in s['kinds'].items():
            if v.is_config_kind(kind, cal['configKinds']):
                continue
            matches = [k for k in RULES if v.match_kind(kind, effects['rules'][k])]
            if matches:
                y += count
            for k in matches:
                singles[k][a] += count
        totals[i][0] += n
        totals[i][1] += y
        sessions[a].append((n, y))
        md = meta.get(s['session'], {})
        actual = md.get('exposure', 'unknown')
        if actual == a:
            evidence[a][0] += n
            evidence[a][1] += 1
        local = datetime.fromtimestamp(s['createdAt']/1000, TZ)
        labels = {'任务代理分类': md.get('task', 'unknown'), '模型服务': md.get('provider', 'unknown'),
                  '配置标识': md.get('config', 'unknown'), '层级': md.get('depth', 'unknown'),
                  '星期': '周末' if local.weekday() >= 5 else '工作日', '时段': '夜间' if local.hour >= 20 or local.hour < 8 else '日间',
                  '指令佐证': actual, '切换缓冲': '首小时' if (s['createdAt']-plan['startMs']) % DAY < 3600000 else '其余时段'}
        for key, value in labels.items():
            slot = sub[(key, str(value))][a]
            slot[0] += n
            slot[1] += y
            slot[2] += 1
    return totals, {a: v.arm_summary(rows) for a, rows in sessions.items()}, sub, evidence, singles


def pair_test(totals, arms):
    # 分母固定在全试验：随机化检验尖锐零假设（整个 (N,Y) 不受分配影响）。
    N, Y = map(sum, zip(*totals))
    if not N or not Y:
        return None, 0
    deltas = []
    for i in range(0, len(arms), 2):
        n0, y0 = totals[i]; n1, y1 = totals[i+1]
        d = (y0-y1)*N - (n0-n1)*Y
        if d:
            deltas.append(d if arms[i] == 'ON' else -d)
    q = len(deltas)
    if not q:
        return 1., 0
    obs = abs(sum(deltas)); hits = 0
    if q <= 16:
        for signs in itertools.product((-1, 1), repeat=q):
            hits += abs(sum(d*s for d,s in zip(deltas, signs))) >= obs
        return hits / 2**q, q
    rng = random.Random(SEED)
    for _ in range(DRAWS):
        mask = rng.getrandbits(q)
        hits += abs(sum(d if (mask >> i) & 1 else -d for i,d in enumerate(deltas))) >= obs
    return (1 + hits) / (DRAWS + 1), q


def pair_ci(totals, arms, st, alpha):
    b, a = st['OFF'], st['ON']; g = len(arms)//2
    if min(b['y'], a['y']) <= 0 or g < 2:
        return None
    scores = []
    for j in range(g):
        z = 0.
        for i in (2*j, 2*j+1):
            arm = arms[i]; n,y = totals[i]; u = st[arm]
            z += (1 if arm == 'ON' else -1) * (y/u['y'] - n/u['n'])
        scores.append(z)
    variance = g/(g-1) * math.fsum(z*z for z in scores)
    if not variance:
        return None
    x = v.beta_quantile((g-1)/2, .5, alpha)
    half = math.sqrt((g-1)*(1-x)/x*variance)
    rr = a['rate']/b['rate']
    return rr*math.exp(-half), rr*math.exp(half)


def check_plan(plan, events):
    p, arms = plan['pairs'], plan['arms']
    if (p != PAIRS or plan['slotHours'] != 24 or len(arms) != 2*p or
            any(set(arms[j:j+2]) != {'ON','OFF'} for j in range(0, len(arms), 2))):
        raise ValueError('不是预注册的 42 对 × 24 小时方案，不能正式解盲')
    if any(plan.get(k) for k in ('testMode','testOnly','isTest','sandboxMode')):
        raise ValueError('演练不能生成正式结论')
    if not events or events[-1]['event'] != 'finished' or any(e['event'] == 'aborted' for e in events):
        raise ValueError('实验未正常结束并恢复；禁止终末解盲')
    issues = []
    times = defaultdict(list)
    for row in events:
        if row['event'] == 'verified' and isinstance(row.get('slot'), int):
            i = row['slot']
            if 0 <= i < len(arms) and arms[i] == row['arm']:
                times[i].append(row['atMs'])
    for i in range(len(arms)):
        ts = plan['startMs'] + i*DAY
        if not any(ts <= t <= ts + 3600000 for t in times[i]):
            issues.append(str(i))
    return ['未在窗口首小时内读回验证的窗：' + ','.join(issues)] if issues else []


def report(plan, events, data, effects, cal, meta, generated):
    quality = check_plan(plan, events)
    if plan['caliber'] != cal['version'] or plan['rulesRevision'] != cal['rulesRevision']:
        raise ValueError('分类或规则版本不符')
    totals, st, strata, evidence, singles = summarise(plan, data, effects, cal, meta)
    for a in ('OFF','ON'):
        g = st[a]
        if (not g['n'] or evidence[a][0] < .8*g['n'] or evidence[a][1] < .8*g['g']):
            quality.append(a+' 臂指令一致的会话或调用覆盖不足 80%')
        if g['n'] < TARGET or g['g'] < cal['statistics']['minSessionsPerArm'] or g['positive'] < cal['statistics']['minPositiveSessionsPerArm']:
            quality.append(a+' 臂未达规划调用量或会话信息下限')
    alpha = cal['statistics']['alpha']
    p, informative = pair_test(totals,plan['arms'])
    both = sum(bool(totals[i][0] and totals[i+1][0]) for i in range(0,len(totals),2))
    if both < 10 or informative < 10:
        quality.append('双窗均有调用或具有非零得分差的配对少于 10 对')
    ci = pair_ci(totals, plan['arms'],st,alpha)
    if ci is None or p is None:
        quality.append('无事件或方差退化，无法估计主区间/检验')
    rr = st['ON']['rate']/st['OFF']['rate'] if st['OFF']['rate'] and st['ON']['n'] else (math.inf if st['ON']['y'] and st['OFF']['n'] else None)
    if quality:
        decision = '不作确认性判决（实施质量或信息不足）'
    elif p <= alpha and ci[1] < 1:
        decision = '支持整块开启降低目标失败率'
    elif p <= alpha and ci[0] > 1:
        decision = '支持整块开启提高目标失败率'
    else:
        decision = '未证实改善或恶化；不等于无效'
    no_large = '区间排除下降至少 50%（不是证明零效应）' if not quality and ci[0] > .5 else '不能排除或未可靠评价 50% 降幅'
    ses,why = v.cluster_test(st['OFF'],st['ON'],cal['statistics'])
    lines = ['# 潮汐前瞻实验｜一次终末分析','',f'快照 {generated}；42 对全部纳入。**{decision}**；{no_large}。', '',
             '主分析按计划分配（ITT），RR=ON/OFF；复合终点只计一次。', '',
             '|臂|失败/调用|会话（命中）|指令相符的会话/调用|','|---|---|---|---|']
    for a in ('OFF','ON'):
        g = st[a]; n,h = evidence[a]
        lines.append(f"|{a}|{g['y']}/{g['n']}|{g['g']}（{g['positive']}）|{h}/{n}|")
    lines += ['',f'RR {v.fmt(rr)}；配对簇近似 95% CI {v.interval(ci)}；配对随机化双侧 p={v.pfmt(p)}（m=1）。',
              f'双窗均有流量 {both} 对；非零得分差 {informative} 对；会话簇敏感性 CI {v.interval(ses)}，p={v.pfmt(ses[2] if ses else None)}。'+(why or ''),
              '质量说明：'+('；'.join(quality) if quality else '通过预定门槛。'), '', '## 复合终点分解（描述，不推断单条规则因果）','']
    lines += [f"- 规则 {k}：OFF {v0['OFF']}，ON {v0['ON']}。" for k,v0 in singles.items()]
    lines += ['', '## 配对窗口（保留空窗；失败/调用）','','|对|先窗|后窗|','|---:|---|---|']
    for i in range(0,len(totals),2):
        lines.append('|'+str(i//2+1)+'|'+ '|'.join(f"{plan['arms'][j]} {totals[j][1]}/{totals[j][0]}" for j in (i,i+1))+'|')
    lines += ['', '## 分层敏感性（描述；不据此翻案）','', '|变量 / 层|OFF 失败/调用（会话）|ON 失败/调用（会话）|层内 RR|','|---|---|---|---|']
    for (key,val),g in sorted(strata.items()):
        n0,y0,g0=g['OFF']; n1,y1,g1=g['ON']
        safe=val.replace('|','/').replace('\n',' ')[:100]
        ratio=(y1/n1)/(y0/n0) if n1 and n0 and y0 else None
        lines.append(f'|{key} / {safe}|{y0}/{n0}（{g0}）|{y1}/{n1}（{g1}）|{v.fmt(ratio)}|')
    lines += ['','这是此机器、此规则版本和日常任务组合的分配效应；模型加载、习惯迁移、同一用户随时间漂移仍限制因果归因。']
    return '\n'.join(lines)+'\n'


def stamp(text):
    dt = datetime.fromisoformat(text)
    # classify.py writes machine-local ISO without offset; this experiment is on a single machine.
    return round(dt.timestamp()*1000)


def check_collection(index, data, stop, generated):
    entries = index['sessions']
    names = {r['file'] for r in entries}
    if (not stop <= index['generatedAt'] <= generated+1000 or len(names) != len(entries)
            or len(entries) != data['sessions'] or any(r.get('source') != 'fresh' or r.get('fail') != 0
            or not r.get('frames') for r in entries)
            or any(s['session']+'.jsonl' not in names for s in data['sessionTimeline'])):
        raise ValueError('终末采集索引陈旧、不完整或有坏帧；须在封存日强制重采后重新 classify')


def seal(run, signals, meta_path, index_path=None):
    from experiment_switch import load_checked
    plan, events = load_checked(run)
    check_plan(plan,events)
    stop = plan['startMs']+len(plan['arms'])*DAY + FOLLOW
    now = datetime.now(timezone.utc).timestamp()*1000
    if not stop <= now < stop + DAY:
        raise ValueError('仅在第 84 天结束后 48–72 小时封存一次，错过不得追挑快照')
    final = run/'final'
    if final.exists():
        raise ValueError('终末结果已封存，禁止覆盖或更换快照')
    raw = signals.read_bytes(); data=json.loads(raw.decode('utf-8-sig'))
    generated = stamp(data['generatedAt'])
    if not stop <= generated <= now or generated >= stop+DAY:
        raise ValueError('不是预定封存日内的有效 classify 快照')
    ib=(index_path or (v.ROOT/'data/raw/index.json')).read_bytes()
    check_collection(json.loads(ib.decode('utf-8-sig')),data,stop,generated)
    mb = meta_path.read_bytes(); md=json.loads(mb.decode('utf-8-sig'))
    if (md.get('runId') != plan['runId'] or md.get('rulesRevision') != plan['rulesRevision']
            or md.get('startMs') != plan['startMs'] or md.get('endMs') != stop-FOLLOW):
        raise ValueError('元数据并非同一试验或规则版本')
    if not generated-1000 <= md.get('generatedAtMs',0) <= now+1000 or not generated-1000 <= meta_path.stat().st_mtime*1000 <= now+1000:
        raise ValueError('元数据过期；须在同一终末快照 classify 后重新提取')
    cal,eff=v.read_json(v.CALIBER),v.read_json(v.EFFECTS)
    text=report(plan,events,data,eff,cal,md['bySession'],data['generatedAt'])
    temp=Path(tempfile.mkdtemp(prefix='.seal-',dir=run))
    try:
        # 不归档 samples；保留能复算的输入，并记录完整来源哈希。
        snap={k:data[k] for k in ('caliber','sessions','generatedAt','uniqueCalls','real','sessionTimeline')}
        (temp/'signals.json').write_text(json.dumps(snap,ensure_ascii=False),encoding='utf-8')
        (temp/'meta.json').write_bytes(mb)
        (temp/'result.md').write_text(text,encoding='utf-8')
        manifest={'sourceSignalsSHA256':hashlib.sha256(raw).hexdigest(),'collectionIndexSHA256':hashlib.sha256(ib).hexdigest(),
                  'runId':plan['runId'],'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in temp.iterdir()}}
        (temp/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        os.rename(temp,final)  # 已存在的非空目录绝不覆盖
    finally:
        if temp.exists():
            shutil.rmtree(temp)  # 仅清理本次创建的试验工作目录
    print(f'已封存：{final / "result.md"}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['seal']); parser.add_argument('--run',required=True,type=Path)
    parser.add_argument('--signals',type=Path,default=v.SIGNALS)
    ns=parser.parse_args()
    try:
        seal(ns.run,ns.signals,ns.run/'meta.json')
    except (OSError,ValueError,RuntimeError,KeyError) as ex:
        parser.exit(1,f'[experiment] {ex}\n')

if __name__=='__main__':
    main()
