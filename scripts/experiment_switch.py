#!/usr/bin/env python3
"""本地配对试验开关。只按调用时刻执行，不安装定时器，不补写历史暴露。

init --run DIR --start ISO_WITH_TZ [--agents FILE] [--pairs 42]
演练：另加 --test-mode --agents 临时文件，可设 1..42 对。建议起点为当地 04:00。
同一目标从注册到恢复独占；不要同时运行 apply.py 或手工编辑目标。
"""
import argparse
import codecs
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('dist/AGENTS.evolve.md', 'state/rule-effects.json', 'state/caliber.json',
           'scripts/classify.py', 'scripts/verify.py', 'scripts/experiment.py',
           'scripts/experiment_switch.py', 'scripts/experiment_meta.py', 'ab-design.md', 'scripts/collect.mjs')
BEGIN = '<!-- tethys:begin 由 dsh-evolve 自动维护，勿手改；撤销：python scripts/apply.py --revert -->'
END = '<!-- tethys:end -->'
DAY = 86_400_000
TERMINAL = ('aborted', 'finished')
MANUAL = ('安全停机：不要再 tick 或清除 INVALID.json/PENDING.json。保存 run 和目标现件副本；'
          '核对 plan.json 的 ON/OFF 哈希和 original.bin。仅在确认无在途进程、目标为已知 OFF 时恢复'
          '原件；有人工改动则逐段合并，勿直接覆盖。优先重试 rollback；若事务/日志损坏，人工复核'
          '恢复后仅移除内容指向本 run 的目标旁 .experiment-owner 文件，保留 run 作废记录。')


class IntegrityError(ValueError):
    pass


class BusyError(RuntimeError):
    pass


def require(ok, message):
    if not ok:
        raise IntegrityError(message)


def now_ms():
    return time.time_ns() // 1_000_000


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def plain(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not path.is_symlink(),
            f'不是独立的常规文件：{path}')
    return path.read_bytes()


def sync_dir(directory):
    if os.name != 'nt':
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def create(path, data):
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)


def atomic(path, data, before=None):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.experiment-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if before is not None:
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
            require(plain(path) == before, '替换前检测到目标已被其他写入者改动')
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        Path(tmp).unlink(missing_ok=True)


@contextmanager
def locked(run, init=False):
    """稳定锁文件不可删除；进程退出时由内核解锁。"""
    path = run / '.lock'
    if init:
        try:
            create(path, b'0')
        except FileExistsError:
            pass
    with path.open('r+b') as handle:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BusyError('此 run 正被其他命令占用，未执行；请稍后重试') from exc
        yield


def normal(text):
    return text.replace('\r\n', '\n').replace('\r', '\n')


def images(original, body):
    # 文本只用于验证，删除始终按原始字节偏移，绝不重新编码用户文字。
    for bom, encoding in ((codecs.BOM_UTF32_LE, 'utf-32-le'), (codecs.BOM_UTF32_BE, 'utf-32-be'),
                          (codecs.BOM_UTF16_LE, 'utf-16-le'), (codecs.BOM_UTF16_BE, 'utf-16-be'),
                          (codecs.BOM_UTF8, 'utf-8')):
        if original.startswith(bom):
            break
    else:
        bom, encoding = b'', 'utf-8'
        try:
            original.decode(encoding)
        except UnicodeDecodeError:
            encoding = 'gb18030'
    text = original[len(bom):].decode(encoding)
    require(len(re.findall(r'<!--\s*tethys\s*:', text, re.I)) == 2
            and len(re.findall(r'\btethys\s*:\s*(?:begin|end)\b', text, re.I)) == 2
            and text.count(BEGIN) == text.count(END) == 1, '须有且仅有一个完整 ON 标记块')
    prefix, _, tail = text.partition(BEGIN)
    inside, end, suffix = tail.partition(END)
    require(end and normal(inside) == '\n' + body + '\n' and
            (not prefix or prefix.endswith(('\r', '\n'))) and
            (not suffix or suffix.startswith(('\r', '\n'))), 'ON 块与 dist 或标记行不一致')
    first = original.index(BEGIN.encode(encoding), len(bom))
    last = original.index(END.encode(encoding), first) + len(END.encode(encoding))
    require(original[len(bom):first].decode(encoding) == prefix and
            original[last:].decode(encoding) == suffix, '不能安全定位编码边界')
    return {'ON': original, 'OFF': original[:first] + original[last:]}


def configuration(files):
    body = normal(files[SOURCES[0]].decode('utf-8')).strip()
    mapping = json.loads(files[SOURCES[1]])['rules']
    cal = json.loads(files[SOURCES[2]])
    count = sum(line.startswith('- **') for line in body.split('\n'))
    ids = [str(i) for i in range(1, count + 1)]
    rev = 'rules-' + sha(body.encode())[:10]
    require(count and set(ids) == set(mapping), 'dist 与规则映射编号不一致')
    require(cal['rulesRevision'] == rev and isinstance(cal['version'], str) and cal['version'],
            'dist 与 caliber 修订号不一致或口径为空')
    return body, {'ruleIds': ids, 'caliber': cal['version'], 'rulesRevision': rev}


def owner_path(plan):
    target = Path(plan['agents'])
    return target.with_name(target.name + '.experiment-owner')


def owner_bytes(run, plan):
    return dump({'run': str(run), 'runId': plan['runId']})


def check_owner(run, plan):
    require(plain(owner_path(plan)) == owner_bytes(run, plan), '目标所有权登记不匹配')


def release(run, plan):
    # 终态提交后才释放，允许上次退出时释放失败的终态命令重试。
    path = owner_path(plan)
    try:
        if plain(path) == owner_bytes(run, plan):
            path.unlink()
            sync_dir(path.parent)
    except FileNotFoundError:
        pass


def event(plan, kind, at, slot, arm, observed, **extra):
    return dict(atMs=at, atUtc=utc(at), event=kind, slot=slot, arm=arm,
                machine=socket.gethostname(), readbackSha256=observed,
                ruleIds=(plan['ruleIds'] if arm == 'ON' else []), **extra)


def commit(run, plan_data, events):
    log = b''.join(map(dump, events))
    atomic(run / 'events.jsonl', log)
    atomic(run / 'seal.json', dump({'plan': sha(plan_data), 'events': sha(log), 'count': len(events)}))


def clear_pending(run):
    (run / 'PENDING.json').unlink()
    sync_dir(run)


def validate_events(p, events):
    last_time, last_slot = None, -1
    require(events and events[0]['event'] == 'registered', '缺少初始登记')
    for i, e in enumerate(events):
        k, at, s, arm = (e[x] for x in ('event', 'atMs', 'slot', 'arm'))
        require(type(at) is int and (last_time is None or at >= last_time) and e['atUtc'] == utc(at)
                and e['machine'] == p['machine'], '事件机器或实际时间不一致')
        require(arm in ('ON', 'OFF') and e['readbackSha256'] == p['imageSha256'][arm]
                and e['ruleIds'] == (p['ruleIds'] if arm == 'ON' else []), '读回哈希或生效规则不一致')
        if k == 'registered':
            require(i == 0 and at < p['startMs'] and s is None and arm == 'ON', '不是前瞻登记')
        elif k == 'verified':
            require(type(s) is int and last_slot < s < len(p['arms']) and arm == p['arms'][s]
                    and (at - p['startMs']) // DAY == s, '核验事件与实际时刻或计划不符')
            last_slot = s
        else:
            require(k in TERMINAL and i == len(events) - 1 and s is None and arm == 'ON'
                    and (k != 'finished' or at >= p['startMs'] + len(p['arms']) * DAY), '终态事件无效')
        last_time = at


def read_run(run):
    plan_data = plain(run / 'plan.json')
    log = plain(run / 'events.jsonl')
    seal = json.loads(plain(run / 'seal.json'))
    require(seal['plan'] == sha(plan_data) and seal['events'] == sha(log), '计划或日志封存哈希不符')
    plan = json.loads(plan_data)
    events = [json.loads(line) for line in log.splitlines()]
    pairs = plan['pairs']
    require(len(events) == seal['count'] and plan['slotHours'] == 24 and type(plan['startMs']) is int
            and type(pairs) is int and 1 <= pairs <= 42 and type(plan['testMode']) is bool
            and (pairs == 42 or plan['testMode']) and len(plan['arms']) == 2 * pairs
            and all(set(plan['arms'][i:i+2]) == {'ON', 'OFF'} for i in range(0, 2*pairs, 2)),
            '冻结计划或事件数无效')
    require(set(plan['sourceSha256']) == set(SOURCES), '来源清单不一致')
    files = {k: plain(run / 'frozen' / k) for k in SOURCES}
    require({k: sha(v) for k, v in files.items()} == plan['sourceSha256'], '冻结副本漂移')
    body, config = configuration(files)
    require(all(plan[k] == v for k, v in config.items()), '规则或口径与冻结来源不符')
    arms = images(plain(run / 'original.bin'), body)
    require({k: sha(v) for k, v in arms.items()} == plan['imageSha256'], '原始备份哈希不一致')
    validate_events(plan, events)
    return plan_data, plan, events, arms


def check_sources(plan):
    for k in SOURCES:
        require(sha(plain(ROOT / k)) == plan['sourceSha256'][k], f'来源已漂移：{k}')


def invalidate(run, reason, plan=None, observed=None):
    # 不把损坏或未提交的日志重新封存。独立且只写一次的故障记录同样阻断解盲。
    at = now_ms()
    s = None if not plan else (at - plan['startMs']) // DAY
    if s is not None and not 0 <= s < len(plan['arms']):
        s = None
    record = dict(atMs=at, atUtc=utc(at), machine=socket.gethostname(), slot=s,
                  event='invalid', arm='UNKNOWN', reason=str(reason), readbackSha256=observed, ruleIds=[])
    try:
        create(run / 'INVALID.json', dump(record))
    except FileExistsError:
        pass


ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError)


def load_checked(run):
    """校验当前源码、冻结材料及封存日志，不打开目标。正式分析另须非 testMode 且 finished。"""
    run = Path(run).resolve()
    with locked(run):
        plan = None
        try:
            require(not (run / 'PENDING.json').exists(), '存在未决事务')
            require(not (run / 'INVALID.json').exists(), '已有完整性失败记录')
            _, plan, events, _ = read_run(run)
            check_sources(plan)
        except ERRORS as exc:
            invalidate(run, exc, plan)
            raise IntegrityError(f'{exc}；{MANUAL}') from exc
        require(events[-1]['event'] != 'aborted', '试验已提前终止，不可正式解盲')
        return plan, events


def init(run, start, agents=None, pairs=42, test_mode=False):
    dt = datetime.fromisoformat(start)
    require(dt.utcoffset() is not None, '起点必须带时区偏移')
    start_ms = (dt - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(milliseconds=1)
    require(start_ms > now_ms(), '只接受未来起点，建议当地 04:00')
    require(type(pairs) is int and 1 <= pairs <= 42 and (pairs == 42 or test_mode),
            '生产固定 42 对；缩短必须显式 --test-mode')
    default = Path.home() / '.dsh' / 'AGENTS.md'
    require(not test_mode or agents is not None, '测试必须显式指定 --agents 临时文件')
    target = Path(agents) if agents is not None else default
    require(not target.is_symlink(), '不接受符号链接目标')
    target = target.resolve()
    require(not test_mode or target != default.absolute(), '测试不能使用真实全局文件')
    run = Path(run).resolve()
    require(not target.is_relative_to(run) and not run.is_relative_to(target)
            and run != target.with_name(target.name + '.experiment-owner'), 'run 与目标或所有权文件不能重叠')
    run.mkdir(mode=0o700, parents=True, exist_ok=True)
    with locked(run, init=True):
        require(not any(f.name != '.lock' for f in run.iterdir()), '必须使用全新或空的 run 目录')
        files = {k: plain(ROOT / k) for k in SOURCES}
        body, config = configuration(files)
        img = images(plain(target), body)
        schedule = []
        for _ in range(pairs):
            schedule.extend(('ON', 'OFF') if secrets.randbits(1) else ('OFF', 'ON'))
        p = dict(startMs=start_ms, startUtc=utc(start_ms), slotHours=24, pairs=pairs, arms=schedule,
                 agents=str(target), testMode=test_mode, machine=socket.gethostname(), runId=secrets.token_hex(16),
                 imageSha256={k: sha(v) for k, v in img.items()}, sourceSha256={k: sha(v) for k, v in files.items()},
                 **config)
        raw = dump(p)
        create(run / 'PENDING.json', dump(event(p, 'intent', now_ms(), None, 'ON',
                                                p['imageSha256']['ON'], action='init', agents=str(target))))
        for k, v in files.items():
            dest = run / 'frozen' / k
            dest.parent.mkdir(parents=True, exist_ok=True)
            create(dest, v)
        create(run / 'original.bin', img['ON'])
        create(run / 'plan.json', raw)
        try:
            create(owner_path(p), owner_bytes(run, p))
        except FileExistsError as exc:
            raise BusyError('目标已有试验登记；不能重复注册，请使用原 run') from exc
        require(plain(target) == img['ON'], '登记期间目标被改动')
        check_sources(p)
        at = now_ms()
        require(at < start_ms, '登记完成时已到起点，请用新的未来起点重新注册')
        commit(run, raw, [event(p, 'registered', at, None, 'ON', p['imageSha256']['ON'])])
        clear_pending(run)
        return {'event': 'registered', 'startUtc': p['startUtc'], 'pairs': pairs, 'testMode': test_mode}


def control(run, action):
    require(action in ('tick', 'status', 'rollback'), '未知命令')
    run = Path(run).resolve()
    with locked(run):
        p = observed = None
        try:
            require(not (run / 'PENDING.json').exists(), '事务中断或未提交')
            raw, p, events, img = read_run(run)
            previous = events[-1]
            if action != 'rollback':
                require(not (run / 'INVALID.json').exists(), '已有完整性失败记录')
                check_sources(p)
            if previous['event'] in TERMINAL:
                if action != 'status':
                    release(run, p)
                return previous
            require(socket.gethostname() == p['machine'], '非登记机器')
            check_owner(run, p)
            target = Path(p['agents'])
            current = plain(target)
            observed = sha(current)
            require(current == img[previous['arm']], f'目标偏离上次读回；现有哈希 {observed}，不覆盖')
            at = now_ms()
            require(at >= previous['atMs'], '时钟倒退')
            s = (at - p['startMs']) // DAY
            if action == 'status' or (action == 'tick' and (s < 0 or s == previous['slot'])):
                return dict(event=previous['event'], arm=previous['arm'], lastVerifiedSlot=previous['slot'],
                            scheduledSlot=s if 0 <= s < len(p['arms']) else None, testMode=p['testMode'])
            if action == 'rollback' or s >= len(p['arms']):
                kind, arm, slot = ('aborted' if action == 'rollback' else 'finished'), 'ON', None
            else:
                kind, arm, slot = 'verified', p['arms'][s], s
            # 意图先落盘，读回、日志和封条全部持久化之后才能清除。任何中间崩溃均禁止继续。
            create(run / 'PENDING.json', dump(event(p, 'intent', at, slot, previous['arm'], observed,
                                                    action=kind, requestedArm=arm)))
            if current != img[arm]:
                atomic(target, img[arm], before=current)
            actual = plain(target)
            observed = sha(actual)
            require(actual == img[arm], '替换后实际读回不符')
            verified = now_ms()
            require(verified >= at, '读回时时钟倒退')
            require(slot is None or (verified - p['startMs']) // DAY == slot, '操作跨过 slot 边界，时段不确定')
            prior = previous['slot'] if previous['slot'] is not None else -1
            until = slot if slot is not None else min(max(s, 0), len(p['arms']))
            late = (verified - p['startMs'] - until * DAY) if kind != 'aborted' else None
            e = event(p, kind, verified, slot, arm, observed, lateMs=late,
                      skippedSlots=list(range(prior + 1, until)), invalid=(run / 'INVALID.json').exists())
            commit(run, raw, events + [e])
            clear_pending(run)
            if kind in TERMINAL:
                release(run, p)
            return e
        except ERRORS as exc:
            invalidate(run, exc, p, observed)
            raise IntegrityError(f'{exc}；{MANUAL}') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('init', 'tick', 'status', 'rollback'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--run', required=True, type=Path)
        if name == 'init':
            cmd.add_argument('--start', required=True)
            cmd.add_argument('--agents', type=Path)
            cmd.add_argument('--pairs', type=int, default=42)
            cmd.add_argument('--test-mode', action='store_true')
    a = parser.parse_args(argv)
    try:
        result = (init(a.run, a.start, a.agents, a.pairs, a.test_mode) if a.action == 'init'
                  else control(a.run, a.action))
    except (*ERRORS, BusyError) as exc:
        print(f'[experiment_switch] {exc}', file=sys.stderr)
        if a.action == 'init' and (a.run / 'PENDING.json').exists():
            print(MANUAL, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
