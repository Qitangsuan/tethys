"""配对随机化分析及冻结快照校验（只使用临时/静态数据）。"""
import itertools
import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiment as ex
import experiment_plan as ep
import verify as v


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self.cal=v.read_json(v.CALIBER); self.effects=v.read_json(v.EFFECTS)
        self.plan=dict(startMs=2*ex.DAY,pairs=42,slotHours=24,arms=['ON','OFF']*42,caliber=self.cal['version'],rulesRevision=self.cal['rulesRevision'])
        self.events=[dict(event='verified',slot=i,arm=a,atMs=(2+i)*ex.DAY) for i,a in enumerate(self.plan['arms'])]+[dict(event='finished')]

    def test_half_open_cut(self):
        t=self.plan['startMs']; end=t+84*ex.DAY
        self.assertEqual([ex.assigned(self.plan,i) for i in (t-1,t,t+ex.DAY-1,t+ex.DAY,end-1,end)], [None,0,0,1,83,None])

    def test_itt_despite_contamination(self):
        t=self.plan['startMs']; s=dict(session='s',calls=50,kinds={'file_conflict':4,'HTTP 401':1},realFails=5,createdAt=t)
        d=dict(caliber=self.cal['version'],sessionTimeline=[s],uniqueCalls=50,sessions=1,real=s['kinds']); meta={'s':{'exposure':'OFF'}}
        self.effects['rules']['6']['detects'].append('file_conflict')
        w,arms,_,evidence,singles=ex.summarise(self.plan,d,self.effects,self.cal,meta)
        self.assertEqual(w[0],[50,4]); self.assertEqual(len(w),84)
        self.assertEqual(singles['6']['ON'],4); self.assertEqual(singles['7']['ON'],0)
        self.assertEqual(arms['ON']['n'],50); self.assertEqual(evidence['ON'],[0,0]); self.assertEqual(arms['OFF']['n'],0)

    def test_refuse_peeks_and_test_mode(self):
        for plan,ev in [(self.plan,self.events[:-1]),(dict(self.plan,testMode=True),self.events),(dict(self.plan,pairs=1),self.events),(self.plan,[dict(event='aborted')]+self.events)]:
            with self.assertRaises(ValueError): ex.check_plan(plan,ev)
        self.assertEqual(ex.check_plan(self.plan,self.events),[])
        self.assertTrue(ex.check_plan(self.plan, self.events[1:]))

    def test_randomization_reference(self):
        arms=['ON','OFF']*6; rows=[[100,0],[100,1]]*6
        self.assertEqual(ex.pair_test(rows,arms), (2/64,6))
        self.assertEqual(ex.pair_test(rows+[[0,0],[0,0]],arms+['ON','OFF']), (2/64,6))
        self.assertEqual(ex.pair_test([[100,0]]*12,arms),(None,0))
        # 穷举两臂率相等时稳健；整体互换 ON/OFF 不改变双侧 p。
        rows=[[10,1],[20,2],[5,1],[2,1],[40,0],[3,2]]
        self.assertEqual(ex.pair_test(rows,arms[:6]),ex.pair_test(rows,arms[1:7]))

    def test_interval_is_pair_level(self):
        arms=['ON','OFF']*4; rows=[[9,1],[17,4],[30,1],[20,2],[8,2],[10,3],[40,1],[17,1]]
        stats={k:v.arm_summary([row for row,a in zip(rows,arms) if a==k]) for k in ('ON','OFF')}
        l,u=ex.pair_ci(rows,arms,stats,.05)
        l1,u1=ex.pair_ci(rows,arms[1:]+arms[:1],{'ON':stats['OFF'],'OFF':stats['ON']},.05)
        self.assertAlmostEqual(u*l1,1); self.assertAlmostEqual(l*u1,1)

    def test_full_terminal_report(self):
        t=self.plan['startMs']; tl=[]; meta={}
        for i,arm in enumerate(self.plan['arms']):
            sid=str(i); n=170+i; y=(1+i%3 if arm=='ON' else 6+i%2)
            tl.append(dict(session=sid,createdAt=t+i*ex.DAY,calls=n,realFails=y,kinds={'file_conflict':y})); meta[sid]={'exposure':arm}
        d=dict(caliber=self.cal['version'],sessionTimeline=tl,uniqueCalls=sum(s['calls'] for s in tl),sessions=84,real={'file_conflict':sum(s['realFails'] for s in tl)})
        text=ex.report(self.plan,self.events,d,self.effects,self.cal,meta,'合成测试')
        self.assertIn('支持整块开启降低',text); self.assertIn('42 对全部纳入',text)
        text=ex.report(self.plan,self.events,d,self.effects,self.cal,{},'合成测试'); self.assertIn('不作确认性判决',text)

    def test_terminal_seal_from_real_controller_in_isolated_run(self):
        import tempfile, json, time
        from datetime import datetime, timezone
        from unittest.mock import patch
        import experiment_switch as sw
        # 合成时间线通到封存，再次封存不可覆盖。使用临时假目标，不读写真实 AGENTS。
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); target=base/'fixture.md'; run=base/'trial'
            target.write_text('fixture\n'+sw.BEGIN+'\n'+(v.ROOT/'dist/AGENTS.evolve.md').read_text(encoding='utf-8').strip()+'\n'+sw.END+'\n',encoding='utf-8')
            now=time.time_ns()//1000000; start=now-86*ex.DAY-3600000
            with patch.object(sw,'now_ms',return_value=start-1000) as clock:
                sw.init(run,datetime.fromtimestamp(start/1000,timezone.utc).isoformat(),target)
                for i in range(85):
                    clock.return_value=start+i*ex.DAY+2000; sw.control(run,'tick')
            plan,_=sw.load_checked(run); total=84*200; rows=[]; by={}
            for i,arm in enumerate(plan['arms']):
                y=3+(i%4); sid=str(i)
                rows.append(dict(session=sid,createdAt=start+i*ex.DAY+5000,calls=200,realFails=y,kinds={'file_conflict':y})); by[sid]={'exposure':arm}
            d=dict(sessionTimeline=rows,sessions=84,uniqueCalls=total,real={'file_conflict':sum(s['realFails'] for s in rows)},caliber=plan['caliber'],generatedAt=datetime.fromtimestamp((now-1000)/1000,timezone.utc).isoformat())
            sig=base/'signals.json'; sig.write_text(json.dumps(d),encoding='utf-8'); meta=run/'meta.json'; meta.write_text(json.dumps(dict(runId=plan['runId'],rulesRevision=plan['rulesRevision'],startMs=start,endMs=start+84*ex.DAY,generatedAtMs=now,bySession=by)),encoding='utf-8')
            idx=base/'index.json'; idx.write_text(json.dumps({'generatedAt':now-2000,'sessions':[dict(file=s['session']+'.jsonl',source='fresh',frames=1,fail=0) for s in rows]}),encoding='utf-8')
            ex.seal(run,sig,meta,idx); self.assertTrue((run/'final/manifest.json').exists())
            with self.assertRaisesRegex(ValueError,'禁止覆盖'): ex.seal(run,sig,meta,idx)

    def test_fresh_complete_index_required(self):
        index={'generatedAt':501,'sessions':[dict(file='s.jsonl',source='fresh',fail=0,frames=1)]}
        data={'sessions':1,'sessionTimeline':[dict(session='s')]}
        ex.check_collection(index,data,500,505)
        for bad in [dict(index,generatedAt=499),dict(index,sessions=[]),dict(index,sessions=[dict(index['sessions'][0],source='cache')]),dict(index,sessions=[dict(index['sessions'][0],fail=1)])]:
            with self.assertRaises(ValueError): ex.check_collection(bad,data,500,505)

    def test_frozen_pack_union(self):
        d,e,c=ep.read_sections(); n=sum(s['calls'] for s in d['sessionTimeline']); self.assertEqual(n,9487)
        self.assertEqual(sum(y for k,y in d['real'].items() if not v.is_config_kind(k,c['configKinds']) and any(v.match_kind(k,e['rules'][j]) for j in ex.rules_of(e))),112)
        self.assertEqual(math.ceil(v.plan_calls(112/n,.5,.8,.05,1)*1.672660),6945)

if __name__=='__main__': unittest.main()
