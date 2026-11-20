"""所有目标、冻结来源和运行记录均在临时目录；不调用 apply 或启动调度。"""
import codecs
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiment_switch as sw


class ExperimentSwitchTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = self.base / 'repo'
        self.run = self.base / 'run'
        self.agents = self.base / '用户文件.md'
        self.body = '## 测试规则\n\n- **一**：甲\n- **二**：乙'
        self.rev = 'rules-' + sw.sha(self.body.encode())[:10]
        files = ((self.body + '\n').encode(), sw.dump({'rules': {'1': {}, '2': {}}}),
                 sw.dump({'version': '测试口径', 'rulesRevision': self.rev}), b'# classify\n', b'# verify\n')
        files += tuple(b'# frozen protocol fixture\n' for _ in sw.SOURCES[len(files):])
        for rel, data in zip(sw.SOURCES, files, strict=True):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.prefix, self.suffix = '用户段一  \r\n\n', '\r\n\n\t用户段二  '
        self.block = sw.BEGIN + '\r\n' + self.body.replace('\n', '\r\n') + '\r\n' + sw.END
        self.original, self.off = self.encoded('utf-8', codecs.BOM_UTF8)
        self.agents.write_bytes(self.original)
        self.start = '2040-07-01T04:00:00+08:00'
        self.ts = int(datetime.fromisoformat(self.start).timestamp() * 1000)
        self.clock = self.enterContext(patch.object(sw, 'now_ms', return_value=self.ts - 1000))
        self.enterContext(patch.object(sw, 'ROOT', self.root))
        self.enterContext(patch.object(Path, 'home', return_value=self.base / 'home'))

    def encoded(self, encoding, bom):
        return (bom + (self.prefix + self.block + self.suffix).encode(encoding),
                bom + (self.prefix + self.suffix).encode(encoding))

    def register(self, bits=(0, 1), run=None, production=False):
        with patch.object(sw.secrets, 'randbits', side_effect=bits) as random_bit:
            sw.init(run or self.run, self.start, self.agents, len(bits), not production)
        self.assertEqual([args.args for args in random_bit.call_args_list], [(1,)] * len(bits))

    def tick(self, slot, delay=0):
        self.clock.return_value = self.ts + slot * sw.DAY + delay
        return sw.control(self.run, 'tick')

    def test_future_registration_and_prestart_do_not_write_target_or_log(self):
        with patch.object(sw.os, 'replace', wraps=sw.os.replace) as replace:
            self.register()
            self.assertNotIn(self.agents, [c.args[1] for c in replace.call_args_list])
        plan, events = sw.load_checked(self.run)
        self.assertEqual(plan['arms'], ['OFF', 'ON', 'ON', 'OFF'])
        self.assertEqual((plan['startMs'], plan['slotHours'], plan['pairs'], plan['testMode']),
                         (self.ts, 24, 2, True))
        self.assertEqual((plan['caliber'], plan['rulesRevision']), ('测试口径', self.rev))
        self.assertEqual([e['event'] for e in events], ['registered'])
        for rel in sw.SOURCES:
            self.assertEqual((self.run / 'frozen' / rel).read_bytes(), (self.root / rel).read_bytes())
        with patch.object(sw, 'atomic', side_effect=AssertionError('不应写盘')):
            self.assertIsNone(sw.control(self.run, 'tick')['scheduledSlot'])
            self.assertEqual(sw.control(self.run, 'status')['arm'], 'ON')
        self.assertEqual(self.agents.read_bytes(), self.original)

    def test_readback_time_same_arm_boundary_end_and_offline_load(self):
        self.register()
        self.clock.side_effect = (self.ts + 51, self.ts + 77)
        e = sw.control(self.run, 'tick')
        self.clock.side_effect = None
        self.assertEqual((e['event'], e['atMs'], e['lateMs'], e['readbackSha256'], e['ruleIds']),
                         ('verified', self.ts + 77, 77, sw.sha(self.off), []))
        self.assertEqual(self.agents.read_bytes(), self.off)
        e = self.tick(1, 220)
        self.assertEqual(e['ruleIds'], ['1', '2'])
        self.assertEqual(e['readbackSha256'], sw.sha(self.original))
        self.assertEqual(self.agents.read_bytes(), self.original)
        self.tick(1, 300)
        self.assertEqual(self.tick(2)['event'], 'verified')
        self.tick(3)
        self.assertEqual(self.tick(4, 83)['event'], 'finished')
        self.assertEqual(self.agents.read_bytes(), self.original)
        self.assertEqual((self.run / 'original.bin').read_bytes(), self.original)
        self.assertFalse(self.agents.with_name(self.agents.name + '.experiment-owner').exists())
        self.agents.unlink()
        plan, events = sw.load_checked(self.run)
        self.assertEqual([x['slot'] for x in events if x['event'] == 'verified'], [0, 1, 2, 3])
        self.assertTrue(all(e['machine'] == plan['machine'] for e in events))
        self.assertEqual(self.tick(9), events[-1])

    def test_complete_missed_days_are_not_backfilled(self):
        self.register()
        late = self.tick(2, 7000)
        self.assertEqual((late['arm'], late['skippedSlots'], late['lateMs']), ('ON', [0, 1], 7000))
        self.assertEqual(self.tick(7)['skippedSlots'], [3])
        _, events = sw.load_checked(self.run)
        self.assertEqual([e['slot'] for e in events if e['event'] == 'verified'], [2])

    def test_aborted_run_cannot_restart_or_unblind(self):
        self.register()
        self.tick(0)
        e = sw.control(self.run, 'rollback')
        self.assertEqual((e['event'], e['arm'], e['slot']), ('aborted', 'ON', None))
        self.assertEqual(self.agents.read_bytes(), self.original)
        self.assertEqual(self.tick(2), e)
        self.assertEqual(sw.control(self.run, 'rollback'), e)
        with self.assertRaisesRegex(sw.IntegrityError, '提前终止'):
            sw.load_checked(self.run)

    def test_production_pairs_and_future_zoned_start(self):
        for start, pairs, mode, target in ((self.start, 1, False, self.agents),
                                           (self.start, 43, True, self.agents),
                                           (self.start, 0, True, self.agents),
                                           (self.start, 1, True, None),
                                           ('2040-07-01T04:00:00', 42, False, self.agents),
                                           ('2030-01-01T00:00:00Z', 42, False, self.agents)):
            with self.subTest(start=start, pairs=pairs, mode=mode), self.assertRaises(ValueError):
                sw.init(self.run, start, target, pairs, mode)
        self.assertFalse(self.run.exists())
        self.register((0, 1) * 21, production=True)
        plan, _ = sw.load_checked(self.run)
        self.assertEqual((plan['pairs'], len(plan['arms']), plan['testMode']), (42, 84, False))

    def test_encodings_and_outside_bytes(self):
        for n, (codec, bom) in enumerate((('utf-8', b''), ('utf-16-le', codecs.BOM_UTF16_LE),
                                           ('utf-16-be', codecs.BOM_UTF16_BE), ('gb18030', b''),
                                           ('utf-32-le', codecs.BOM_UTF32_LE))):
            with self.subTest(codec=codec):
                orig, off = self.encoded(codec, bom)
                self.agents.write_bytes(orig)
                self.run = self.base / f'run-{n}'
                self.clock.return_value = self.ts - 1000
                self.register((0,))
                self.tick(0)
                self.assertEqual(self.agents.read_bytes(), off)
                self.assertEqual(self.tick(2)['arm'], 'ON')
                self.assertEqual(self.agents.read_bytes(), orig)

    def test_ambiguous_incomplete_and_stale_blocks_fail_closed(self):
        variants = ('', self.block + '\n' + self.block, self.block.replace(sw.END, ''),
                    self.block.replace(sw.BEGIN, ''), self.block.replace('**二**', '**三**'),
                    self.block + '\n<!-- tethys:begin', self.block.replace('勿手改', '可手改'),
                    sw.END + '\n' + sw.BEGIN)
        for i, text in enumerate(variants):
            with self.subTest(i=i):
                body = (self.prefix + text + self.suffix).encode()
                self.agents.write_bytes(body)
                with self.assertRaises(sw.IntegrityError):
                    sw.init(self.base / f'run-{i}', self.start, self.agents, 1, True)
                self.assertEqual(self.agents.read_bytes(), body)

    def test_default_target_is_the_sandbox_home_and_test_mode_refuses_it(self):
        home_target = Path.home() / '.dsh' / 'AGENTS.md'
        home_target.parent.mkdir(parents=True)
        home_target.write_bytes(self.original)
        with self.assertRaisesRegex(sw.IntegrityError, '全局'):
            sw.init(self.run, self.start, home_target, 1, True)
        sw.init(self.run, self.start)
        p, _ = sw.load_checked(self.run)
        self.assertEqual(Path(p['agents']), home_target)
        self.assertFalse(p['testMode'])
        sw.control(self.run, 'rollback')
        self.assertEqual(home_target.read_bytes(), self.original)

    def test_interrupting_edit_before_atomic_replace_is_not_overwritten(self):
        self.register()
        original_atomic = sw.atomic
        edited = self.original + b' intervening edit'
        def intervening_edit(path, *args, **kwargs):
            if path == self.agents:
                self.agents.write_bytes(edited)
            return original_atomic(path, *args, **kwargs)
        with patch.object(sw, 'atomic', side_effect=intervening_edit), self.assertRaisesRegex(sw.IntegrityError, '替换前'):
            self.tick(0)
        self.assertEqual(self.agents.read_bytes(), edited)
        self.assertTrue((self.run / 'PENDING.json').exists())
        with self.assertRaises(sw.IntegrityError):
            sw.load_checked(self.run)

    def test_external_target_edits_are_preserved_and_disqualification_sticks(self):
        self.register()
        self.tick(0)
        edited = self.off + '人工文字'.encode()
        self.agents.write_bytes(edited)
        for action in ('status', 'rollback', 'tick'):
            with self.assertRaises(sw.IntegrityError):
                sw.control(self.run, action)
            self.assertEqual(self.agents.read_bytes(), edited)
        record = json.loads((self.run / 'INVALID.json').read_bytes())
        self.assertEqual(record['readbackSha256'], sw.sha(edited))
        self.agents.write_bytes(self.off)
        with self.assertRaises(sw.IntegrityError):
            sw.load_checked(self.run)
        self.assertEqual(sw.control(self.run, 'rollback')['event'], 'aborted')
        self.assertEqual(self.agents.read_bytes(), self.original)

    def test_each_live_source_drift_is_sticky_but_safe_rollback_still_works(self):
        for i, rel in enumerate(sw.SOURCES):
            with self.subTest(rel=rel):
                self.run = self.base / f'run-{i}'
                self.clock.return_value = self.ts - 100
                self.register((0,))
                self.tick(0)
                source = self.root / rel
                original = source.read_bytes()
                source.write_bytes(original + b' ')
                with self.assertRaisesRegex(sw.IntegrityError, '来源已漂移'):
                    sw.load_checked(self.run)
                source.write_bytes(original)
                with self.assertRaises(sw.IntegrityError):
                    self.tick(1)
                self.assertEqual(sw.control(self.run, 'rollback')['event'], 'aborted')
                self.assertEqual(self.agents.read_bytes(), self.original)

    def test_tamper_with_plan_log_backup_or_snapshots(self):
        rels = ('plan.json', 'events.jsonl', 'original.bin', 'seal.json', 'frozen/scripts/verify.py')
        for i, rel in enumerate(rels):
            with self.subTest(rel=rel):
                self.agents = self.base / f'用户-{i}.md'
                self.agents.write_bytes(self.original)
                self.run = self.base / f'run-{i}'
                self.register((0,))
                f = self.run / rel
                f.write_bytes(f.read_bytes()[:-4])
                with self.assertRaises(sw.IntegrityError):
                    sw.load_checked(self.run)
                with self.assertRaises(sw.IntegrityError):
                    sw.control(self.run, 'tick')
                self.assertEqual(self.agents.read_bytes(), self.original)

    def test_two_run_owners_and_process_lock(self):
        self.register()
        with self.assertRaises(sw.BusyError):
            sw.init(self.base / 'second', self.start, self.agents, 1, True)
        with sw.locked(self.run):
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(Path(sw.__file__)),
                                     'status', '--run', str(self.run)], capture_output=True,
                                    encoding='utf-8', timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('占用', result.stderr)
        self.assertFalse((self.run / 'INVALID.json').exists())
        sw.control(self.run, 'rollback')
        self.register((1,), run=self.base / 'third')

    def test_crash_after_target_replace_before_log_commit_blocks_report_and_tick(self):
        self.register()
        with patch.object(sw, 'commit', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.tick(0)
        self.assertEqual(self.agents.read_bytes(), self.off)
        self.assertTrue((self.run / 'PENDING.json').exists())
        for operation in (lambda: sw.load_checked(self.run), lambda: self.tick(1),
                          lambda: sw.control(self.run, 'rollback')):
            with self.assertRaisesRegex(sw.IntegrityError, '人工'):
                operation()
        self.assertEqual(self.agents.read_bytes(), self.off)
        self.assertEqual((self.run / 'original.bin').read_bytes(), self.original)

    def test_crash_after_event_tail_before_seal_and_after_seal_before_clear(self):
        for i, fail_on in enumerate(('seal.json', 'PENDING.json')):
            with self.subTest(fail_on=fail_on):
                self.run = self.base / f'run-{i}'
                self.agents = self.base / f'用户-{i}.md'
                self.agents.write_bytes(self.original)
                self.clock.return_value = self.ts - 200
                self.register((0,))
                atomic = sw.atomic
                def crash(path, *args, **kwargs):
                    if path.name == fail_on:
                        raise KeyboardInterrupt
                    return atomic(path, *args, **kwargs)
                with patch.object(sw, 'atomic', side_effect=crash), \
                     patch.object(sw, 'clear_pending', side_effect=KeyboardInterrupt), \
                     self.assertRaises(KeyboardInterrupt):
                    self.tick(0)
                with self.assertRaises(sw.IntegrityError):
                    sw.load_checked(self.run)
                self.assertEqual(self.agents.read_bytes(), self.off)

    def test_clock_boundary_and_backwards_clock_stop(self):
        self.register()
        self.clock.side_effect = [self.ts + sw.DAY - 1, self.ts + sw.DAY, self.ts + sw.DAY]
        with self.assertRaisesRegex(sw.IntegrityError, '跨过'):
            sw.control(self.run, 'tick')
        self.clock.side_effect = None
        with self.assertRaises(sw.IntegrityError):
            sw.load_checked(self.run)
        self.run = self.base / 'another'
        self.agents = self.base / 'another.md'
        self.agents.write_bytes(self.original)
        self.register((1,))
        self.tick(0, 3)
        with self.assertRaisesRegex(sw.IntegrityError, '时钟倒退'):
            self.tick(0, 2)

    def test_cli_smoke_using_temporary_repo_and_home(self):
        script = self.root / 'scripts' / 'experiment_switch.py'
        shutil.copyfile(sw.__file__, script)
        env = dict(os.environ, HOME=str(self.base / 'home'), USERPROFILE=str(self.base / 'home'))
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        for cmd, expected in ((['init', '--start', tomorrow, '--agents', str(self.agents), '--pairs', '1',
                                '--test-mode'], 'registered'), (['status'], 'registered'),
                              (['tick'], 'registered'), (['rollback'], 'aborted'), (['tick'], 'aborted')):
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(script), *cmd, '--run', str(self.run)],
                                    cwd=self.base, env=env, capture_output=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['event'], expected)
        self.assertEqual(self.agents.read_bytes(), self.original)
        self.assertFalse((self.base / 'home' / '.dsh').exists())


if __name__ == '__main__':
    unittest.main()
