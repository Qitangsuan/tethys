#!/usr/bin/env python3
"""提取无正文的前瞻实验暴露佐证；不推测模型遵从性，也不操作真实开关。"""
import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from collections import Counter
from pathlib import Path
from experiment_switch import BEGIN, END, DAY, load_checked

HEADER = 'Instructions from: ~/.dsh/AGENTS.md'
TOOLS = {'edit':'coding','write':'coding','pwsh':'coding','bash':'coding','read':'coding',
         'glob':'coding','grep':'coding','web_fetch':'research','web_search':'research'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def revision(body):
    return 'rules-' + sha(body.encode('utf-8'))[:10]


def evidence(d, body):
    src=d.get('source') or {}
    if src.get('kind') != 'agent-instructions':
        return None  # 普通用户引用同一段文字不是证据
    if src.get('form') != 'instructions' or src.get('baseline') is not True:
        return 'unknown'
    if not any(isinstance(x,dict) and x.get('action')=='set' and x.get('scope')=='user-global\x00AGENTS.md'
               for x in src.get('changes',[])):
        return 'unknown'
    items=d.get('content') or []
    if len(items)!=1 or items[0].get('type')!='text': return 'unknown'
    txt=items[0].get('text','').replace('\r\n','\n').strip()
    if not (txt.startswith('<system-reminder>') and txt.endswith('</system-reminder>')): return 'unknown'
    txt=txt[:-len('</system-reminder>')]
    heads=list(re.finditer(r'^Instructions from: [^\n]+$',txt,re.M))
    hits=[j for j,m in enumerate(heads) if m.group()==HEADER]
    if len(hits)!=1: return 'unknown'
    j=hits[0]; global_text=txt[heads[j].end():heads[j+1].start() if j+1<len(heads) else len(txt)].strip()
    if not global_text or re.search(r'\[truncated\]|\[\.\.\. truncated|内容已截断',global_text,re.I): return 'unknown'
    markers=re.findall(r'<!--\s*tethys\s*:',global_text,re.I)
    if markers:
        if len(markers)!=2 or global_text.count(BEGIN)!=1 or global_text.count(END)!=1: return 'unknown'
        before,_,tail=global_text.partition(BEGIN); inside,sep,after=tail.partition(END)
        if not sep or inside.strip()!=body: return 'unknown'
        return 'ON'
    # 防止只移除标记、留下规则；不声称能检测所有同义改写。
    headlines=re.findall(r'^- \*\*(.*?)\*\*',body,re.M)
    if 'TETHYS' in global_text or any(h in global_text for h in headlines): return 'unknown'
    return 'OFF'


def hashed_label(values):
    # 不将自由文本、账号、自定义模型名、URL 等写入产物。
    return 'id-'+sha(json.dumps(sorted(values),ensure_ascii=False).encode())[:16] if values else 'unknown'


def extract(path,start,end,body):
    with path.open(encoding='utf-8-sig') as handle:
        header=None
        for line in handle:
            try: o=json.loads(line)
            except ValueError: continue
            if o.get('type')=='session':
                header=o; break
        if not header or not isinstance(header.get('createdAt'),int) or not start<=header['createdAt']<end:
            return None
        exposures=set(); providers=set(); configurations=set(); tools=Counter(); calls=0; human=False; broken=False
        for line in handle:
            try: o=json.loads(line)
            except ValueError:
                broken=True; continue
            typ=o.get('type'); d=o.get('data') or {}
            if typ=='user/message':
                answer=evidence(d,body)
                if answer: exposures.add(answer)
                if (d.get('source') or {}).get('kind')=='user': human=True
            elif typ=='model/selection':
                p,m=d.get('provider'),d.get('model')
                if isinstance(p,str) and p:
                    providers.add(p)
                    if isinstance(m,str) and m: configurations.add((p,m))
            elif typ=='tool/call':
                calls+=1; tools[TOOLS.get(d.get('name'),'other')]+=1
        exposure=('mixed' if {'ON','OFF'}<=exposures else 'unknown' if broken or len(exposures)!=1 else next(iter(exposures)))
        top=tools.most_common(2)
        task=('chat' if human else 'unknown') if not calls else (top[0][0] if len(top)==1 or top[0][1]>top[1][1] else 'other')
        depth=header.get('delegationDepth'); depth=depth if type(depth) is int and depth>=0 else 'unknown'
        return dict(exposure=exposure,task=task,provider=hashed_label(providers),config=hashed_label(configurations),
                    depth=depth,seeded=header['isSeeded'] if type(header.get('isSeeded')) is bool else 'unknown')


def build(run,raw,dist=None):
    plan,_=load_checked(run)
    body=(dist or (run/'frozen/dist/AGENTS.evolve.md')).read_text(encoding='utf-8').strip()
    if revision(body)!=plan['rulesRevision']: raise ValueError('规则修订不符')
    if not raw.is_dir(): raise ValueError('指定 raw 目录不存在')
    start=plan['startMs']; end=start+len(plan['arms'])*DAY; rows={}
    for path in sorted(raw.glob('*.jsonl')):
        md=extract(path,start,end,body)
        if md is not None: rows[path.stem]=md
    obj=dict(runId=plan['runId'],rulesRevision=plan['rulesRevision'],startMs=start,endMs=end,
             generatedAtMs=time.time_ns()//1000000,bySession=rows)
    fd,tmp=tempfile.mkstemp(dir=run,prefix='.meta-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False)
        os.replace(tmp,run/'meta.json')
    finally: Path(tmp).unlink(missing_ok=True)
    return len(rows)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--run',required=True,type=Path)
    p.add_argument('--raw',required=True,type=Path); p.add_argument('--dist',type=Path); a=p.parse_args()
    try: n=build(a.run,a.raw,a.dist)
    except (OSError,ValueError,KeyError,RuntimeError) as e: p.exit(1,f'[meta] {e}\n')
    print(f'已生成无正文元数据：{n} 个会话（未解盲）。')

if __name__=='__main__': main()
