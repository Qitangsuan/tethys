"""M1/M6/M9 regressions, including gate decisions (not only helper behavior)."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_plugins
import classify
import ready
import verify


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.raw = Path(temp.name)
        self.ids = [f"id-{i}" for i in range(10)]

    def session(self, name, ids, timestamp=1000):
        rows = [{"type": "session", "createdAt": timestamp}]
        rows.extend({"type": "tool/call", "data": {"callId": c}} for c in ids)
        (self.raw / (name + ".jsonl")).write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")

    def count(self, n, **fields):
        return ready.count_new_sessions(list(self.raw.glob("*.jsonl")), dict(sessions=n, **fields))[0]

    def gate(self, signals, sources=0):
        sig = self.raw / "signals.json"
        sig.write_text(json.dumps(dict(generatedAt=datetime.now().isoformat(), **signals)), encoding="utf-8")
        output = io.StringIO()
        with patch.object(ready, "RAW", str(self.raw)), patch.object(ready, "SIGNALS", str(sig)), \
                patch.object(ready, "count_src", return_value=sources), redirect_stdout(output):
            result = ready.main()
        return result, output.getvalue()

    def test_exact_threshold_ignores_time(self):
        self.session("old", self.ids)
        self.session("copy", self.ids[:9] + ["other"], 9999)
        self.assertEqual(self.count(1, rawSessionIds=["old"]), 0)

    def test_below_threshold_same_set_is_not_enough(self):
        self.session("old", self.ids)
        self.session("different", self.ids[:8] + self.ids[8:][::-1])
        self.assertEqual(self.count(1, rawSessionIds=["old"]), 1)

    def test_shorter_prefix_bridges_longer_branches_conservatively(self):
        self.session("old", ["head"])
        self.session("branch-b", ["head", "b", "b2"])
        self.session("branch-a", ["head", "a", "a2"])
        self.assertEqual(self.count(1, rawSessionIds=["old"]), 0)

    def test_snapshot_already_contains_copies(self):
        self.session("old", self.ids)
        self.session("old-copy", self.ids, 2000)
        self.session("new", ["new"])
        self.session("new-copy", ["new"], 2000)
        self.assertEqual(self.count(2, rawSessionIds=["old", "old-copy"]), 1)
        self.assertEqual(self.count(2), 0)  # legacy aggregate cannot identify old duplicates

    def test_lost_file_reserves_one_old_slot(self):
        self.session("old", self.ids)
        self.session("renamed", ["old-or-new"])
        self.session("fresh", ["definitely-another"])
        self.assertEqual(self.count(2, rawSessionIds=["old", "gone"]), 1)

    def test_legacy_and_invalid_manifest_lower_bound(self):
        self.session("old", self.ids)
        self.session("new", ["unique"])
        for fields in ({}, {"rawSessionIds": ["old", "extra"]}, {"rawSessionIds": [None]}):
            with self.subTest(fields=fields):
                self.assertEqual(self.count(1, **fields), 1)
        self.assertEqual(self.count(2, rawSessionIds=["old", "old"]), 0)
        self.assertEqual(self.count(True), 0)

    def test_unusable_files_not_counted(self):
        self.session("ok", self.ids)
        self.session("empty", [])
        self.session("missing-id", [None])
        self.session("broken", ["not-to-be-used"])
        with (self.raw / "broken.jsonl").open("a", encoding="utf8") as f:
            f.write("\n{")
        (self.raw / "bad-encoding.jsonl").write_bytes(b"\xff\xff")
        self.assertEqual(self.count(1, rawSessionIds=["ok"]), 0)
        self.assertIsNone(ready.call_prefix(self.raw / "absent.jsonl"))

    def test_copy_only_does_not_trigger_any_raw_based_gate(self):
        self.session("old", self.ids)
        for i in range(6):
            self.session(str(i), self.ids, 2000 + i)
        result, text = self.gate(dict(sessions=1, rawSessionIds=["old"], uniqueCalls=5000), sources=7)
        self.assertEqual(result, 1)
        self.assertIn("估算新增调用 0", text)
        self.assertNotIn("判定缺口", text)

    def test_independent_session_still_triggers_gap(self):
        self.session("old", self.ids)
        self.session("new", ["other"])
        result, text = self.gate(dict(sessions=1, rawSessionIds=["old"], uniqueCalls=5))
        self.assertEqual(result, 0)
        self.assertIn("判定缺口：至少 1", text)

    def test_collection_gap_still_uses_physical_file_count(self):
        self.session("old", self.ids)
        self.session("copy", self.ids)
        result, text = self.gate(dict(sessions=1, rawSessionIds=["old"], uniqueCalls=10), sources=3)
        self.assertEqual(result, 0)
        self.assertIn("源 3 个 zstd > raw 2 个", text)

    def test_classify_manifest_includes_sessions_without_exposure(self):
        self.session("empty", [])
        out = self.raw / "output"
        out.mkdir()
        with patch.object(classify, "RAW", str(self.raw)), patch.object(classify, "OUT_DIR", str(out)), \
                redirect_stdout(io.StringIO()):
            classify.main()
        snapshot = json.loads((out / "failReasons.json").read_text(encoding="utf-8"))
        self.assertEqual((snapshot["sessions"], snapshot["rawSessionIds"], snapshot["sessionTimeline"]),
                         (1, ["empty"], []))


class VerifierTests(unittest.TestCase):
    def test_config_variants_from_real_config_path(self):
        for text in ["CODEX SEARCH IS SIGNED OUT", "API Credentials Are NOT Configured"]:
            self.assertTrue(verify.is_config_kind("tool_error::Error: " + text))
        for text in ['subagent "x" is unavailable', "API credentials service timed out"]:
            self.assertFalse(verify.is_config_kind("tool_error::Error: " + text))

    def test_explicit_patterns_single_source(self):
        self.assertTrue(verify.is_config_kind("CONFIG ERROR", ["Config Error"]))
        self.assertFalse(verify.is_config_kind("CODEX SEARCH IS SIGNED OUT", []))
        self.assertFalse(verify.is_config_kind("ordinary failure", ["", " "]))

    def test_filter_changes_numerator_not_exposure(self):
        cal = verify.read_json(verify.CALIBER)
        cut = verify.since_ts("2026-11-06")
        run = 'tool_error::Error: subagent "x" is unavailable'
        login = "tool_error::Error: CODEX SEARCH IS SIGNED OUT"
        rows = [dict(createdAt=cut, calls=12, kinds={login: 4, run: 2})]
        r = verify.analyze_rule(rows, {"since": "2026-11-06", "detects": [run, login]}, cal)
        self.assertEqual((r["a"]["y"], r["a"]["n"]), (2, 12))

    def test_numeric_sort_has_no_integer_length_limit(self):
        giant = "9" * 5000
        self.assertEqual(sorted(["X", giant, "10", "2", "02", "-1"], key=verify.rule_id_order),
                         ["02", "2", "10", giant, "-1", "X"])

    def test_nonnumeric_id_warns_preserves_family(self):
        cal = verify.read_json(verify.CALIBER)
        data = dict(sessions=0, sessionTimeline=[], generatedAt="fixture", caliber=cal["version"],
                    uniqueCalls=0, totalCalls=0, duplicatedCalls=0, real={})
        cfg = dict(title="t", since="2026-11-06", detects=["a"])
        err = io.StringIO()
        with redirect_stderr(err):
            text = verify.render(data, {"rules": {"Z": cfg, "10": cfg, "2": cfg, "skip": dict(cfg, verify=False)}}, cal)
        self.assertIn("非数字规则键", err.getvalue())
        self.assertIn("警告：非数字规则键", text)
        self.assertIn("m=3", text)
        self.assertLess(text.index("| 2 ·"), text.index("| 10 ·"))
        self.assertLess(text.index("| 10 ·"), text.index("| Z ·"))
        self.assertIn("规则 skip", text)

    def test_audit_uses_classifiers_text_helper(self):
        self.assertIs(audit_plugins.text_of, classify.text_of)


class ReadyGateTests(unittest.TestCase):
    """验证门槛只看后臂规模：刚判完「新增会话=0」不该让门槛永远显示 0（2026-11-24 修）。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.raw = self.tmp / "raw"
        self.raw.mkdir()
        self.effects = self.tmp / "effects.json"
        self.effects.write_text(json.dumps({"rules": {"1": {"since": "2026-11-21"}}}), encoding="utf-8")
        self.caliber = self.tmp / "caliber.json"
        self.caliber.write_text(json.dumps({"statistics": {"minSessionsPerArm": 10}}), encoding="utf-8")

    def run_ready(self, signals, sources=0):
        sig = self.tmp / "signals.json"
        sig.write_text(json.dumps(signals), encoding="utf-8")
        output = io.StringIO()
        with patch.object(ready, "RAW", str(self.raw)), patch.object(ready, "SIGNALS", str(sig)), \
                patch.object(ready, "EFFECTS", str(self.effects)), patch.object(ready, "CALIBER", str(self.caliber)), \
                patch.object(ready, "count_src", return_value=sources), redirect_stdout(output):
            code = ready.main()
        return code, output.getvalue()

    def base(self, timeline):
        return dict(generatedAt=datetime.now().isoformat(), sessions=1, rawSessionIds=["s"],
                    uniqueCalls=10, sessionTimeline=timeline)

    @staticmethod
    def post_ms(days=1):
        base = datetime(2026, 11, 22, tzinfo=__import__("datetime").timezone.utc)
        return int((base + __import__("datetime").timedelta(days=days - 1)).timestamp() * 1000)

    def test_post_arm_short_is_not_verify(self):
        timeline = [dict(session="s", createdAt=self.post_ms(), calls=10, realFails=0, kinds={})]
        code, text = self.run_ready(self.base(timeline))
        self.assertEqual(code, 1)
        self.assertIn("VERDICT=none", text)
        self.assertIn("后臂（since 2026-11-21 之后）：1 个有调用会话 / 10 次调用", text)
        self.assertIn("还需", text)  # 把差距与 ETA 摊开

    def test_post_arm_meeting_both_floors_is_verify(self):
        timeline = [dict(session=f"s{i}", createdAt=self.post_ms(), calls=250, realFails=0, kinds={})
                    for i in range(10)]
        code, text = self.run_ready(self.base(timeline))
        self.assertEqual(code, 0)
        self.assertIn("VERDICT=verify", text)
        self.assertIn("后臂会话 10 ≥ 10", text)
        self.assertIn("后臂调用 2500 ≥ 2000", text)

    def test_pre_arm_calls_do_not_count(self):
        timeline = [dict(session="old", createdAt=self.post_ms(-3), calls=9000, realFails=0, kinds={})]
        code, text = self.run_ready(self.base(timeline))
        self.assertIn("VERDICT=none", text)
        self.assertIn("0 个有调用会话 / 0 次调用", text)

    def test_new_family_backfill_before_previous_judge_is_ignored(self):
        prev = (datetime.now() - __import__("datetime").timedelta(days=1)).isoformat()
        old = int((datetime.now() - __import__("datetime").timedelta(days=3)).timestamp() * 1000)
        sig = dict(newKinds=["k"], real={"k": 3}, samples={"k": [{"time": old}]}, previousGeneratedAt=prev)
        self.assertEqual(ready.find_new_families(sig), [])

    def test_new_family_after_previous_judge_is_reported(self):
        prev = (datetime.now() - __import__("datetime").timedelta(days=1)).isoformat()
        fresh = int(datetime.now().timestamp() * 1000)
        sig = dict(newKinds=["k"], real={"k": 3}, samples={"k": [{"time": fresh}]}, previousGeneratedAt=prev)
        self.assertEqual(ready.find_new_families(sig), [("k", 3)])

    def test_new_family_without_time_evidence_is_kept(self):
        sig = dict(newKinds=["k"], real={"k": 3}, samples={}, previousGeneratedAt="2026-11-24T00:00:00")
        self.assertEqual(ready.find_new_families(sig), [("k", 3)])


if __name__ == "__main__":
    unittest.main()
