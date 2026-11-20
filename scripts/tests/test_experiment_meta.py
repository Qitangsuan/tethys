"""合成宿主消息，验证保守的暴露鉴别和隐私输出。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiment_meta as m

BODY='## TETHYS\n\n- **预检文件**：先定位'
BLOCK=m.BEGIN+'\n'+BODY+'\n'+m.END

def msg(global_body,source='agent-instructions',next_body=None):
    x='<system-reminder>\n'+m.HEADER+'\n\n'+global_body
    if next_body is not None: x+='\nInstructions from: ./AGENTS.md\n'+next_body
    return {'source':{'kind':source,'form':'instructions','baseline':True,'changes':[{'action':'set','scope':'user-global\x00AGENTS.md'}]},'content':[{'type':'text','text':x+'\n</system-reminder>'}]}

class MetaTests(unittest.TestCase):
    def test_on_off_in_scope(self):
        self.assertEqual(m.evidence(msg('普通全局说明\n'+BLOCK),BODY),'ON')
        self.assertEqual(m.evidence(msg('普通全局说明',next_body=BLOCK),BODY),'OFF')
        self.assertIsNone(m.evidence(msg(BLOCK,source='user'),BODY))

    def test_incomplete_and_residual_unknown(self):
        for b in ('','[truncated]',BODY,'仅剩预检文件','文本\n'+m.BEGIN,'文本\n'+BLOCK.replace('先定位','改了')):
            with self.subTest(b=b): self.assertEqual(m.evidence(msg(b),BODY),'unknown')
        d=msg('普通说明'); d['source']['baseline']=False; self.assertEqual(m.evidence(d,BODY),'unknown')
        d=msg('普通说明'); d['content'][0]['text']=d['content'][0]['text'][:-18]; self.assertEqual(m.evidence(d,BODY),'unknown')

    def test_mixed_privacy_scope_and_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            r=Path(folder); raw=r/'raw'; raw.mkdir(); dist=r/'dist.md'; dist.write_text(BODY,encoding='utf-8')
            p=dict(startMs=1000,arms=['ON','OFF'],runId='fixture',rulesRevision=m.revision(BODY))
            events=[{'type':'session','createdAt':1000,'delegationDepth':1,'isSeeded':True},
                    {'type':'user/message','data':msg(BLOCK)}, {'type':'user/message','data':msg('PRIVATE_MESSAGE')},
                    {'type':'model/selection','data':{'provider':'PRIVATE_PROVIDER','model':'PRIVATE_MODEL'}},
                    {'type':'tool/call','data':{'name':'edit','arguments':'PRIVATE_PATH'}}]
            for name,ts in (('s',1000),('old',999),('future',1000+2*m.DAY)):
                events[0]['createdAt']=ts; (raw/(name+'.jsonl')).write_text('\n'.join(map(json.dumps,events)),encoding='utf-8')
            with patch.object(m,'load_checked', return_value=(p,[])):
                self.assertEqual(m.build(r,raw,dist),1)
                dist.write_text('变更版本',encoding='utf-8')
                with self.assertRaises(ValueError): m.build(r,raw,dist)
            text=(r/'meta.json').read_text(encoding='utf-8'); self.assertNotIn('PRIVATE_',text)
            md=json.loads(text); self.assertEqual(md['runId'],'fixture'); got=md['bySession']['s']; self.assertEqual(got['exposure'],'mixed'); self.assertEqual(got['depth'],1); self.assertEqual(got['task'],'coding')

if __name__=='__main__': unittest.main()
