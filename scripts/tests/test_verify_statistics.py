"""统计验证器回归：可解析的分布、退化输入、键和及多重检验族。"""
import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify as v


class NumericTests(unittest.TestCase):
    def test_student_reference(self):
        # t(1) 是 Cauchy；双侧 .05 的 t(10) 临界值为 2.228139。
        self.assertAlmostEqual(v.ibeta(.5, .5, .5), .5)
        x = v.beta_quantile(5, .5, .05)
        self.assertAlmostEqual(math.sqrt(10 * (1 - x) / x), 2.22813885196, places=8)

    def test_exact_by_enumeration(self):
        for n0, n1 in [(20, 50), (50, 50), (33, 7)]:
            q = n1 / (n0 + n1)
            for a in range(8):
                b = 7 - a
                probs = [math.comb(7, j) * q ** j * (1 - q) ** (7 - j) for j in range(8)]
                expected = min(1, 2 * sum(probs[:a+1]), 2 * sum(probs[a:]))
                low, high, p = v.exact_rr(b, n0, a, n1, .05)
                self.assertAlmostEqual(p, expected, places=11)
                for endpoint, lower in ((low, True), (high, False)):
                    if not 0 < endpoint < math.inf:
                        continue
                    q1 = endpoint * n1 / (n0 + endpoint * n1)
                    tail = range(a, 8) if lower else range(a + 1)
                    self.assertAlmostEqual(sum(math.comb(7, j)*q1**j*(1-q1)**(7-j) for j in tail), .025, places=10)

    def test_degenerate(self):
        self.assertEqual(v.exact_rr(0, 30, 0, 50, .05), (0, math.inf, 1))
        self.assertIsNone(v.exact_rr(0, 0, 2, 100, .05))
        lo, hi, p = v.exact_rr(6, 50, 0, 50, .05)
        self.assertEqual(lo, 0)
        self.assertAlmostEqual(hi, .025 ** (-1/6) - 1)
        self.assertAlmostEqual(p, 2/64)

    def test_planning(self):
        self.assertEqual(v.plan_calls(.005, .7, .8, .05, 3), 39972)
        self.assertLess(v.plan_calls(.005, .7, .8, .05, 1), 39972)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.cal = v.read_json(v.CALIBER)
        self.cut = v.since_ts("2026-11-06")
        self.cfg = {"since": "2026-11-06", "detects": ["a", "b"], "detectsPatterns": ["a", "HTTP"]}

    def test_union_config_cut_and_zero_exposure(self):
        rows = [dict(createdAt=self.cut-1, calls=30, kinds={"a": 4, "b": 2}),
                dict(createdAt=self.cut, calls=60, kinds={"a": 1, "b": 5, "HTTP 401": 7}),
                dict(createdAt=self.cut, calls=0, kinds={})]
        r = v.analyze_rule(rows, self.cfg, self.cal)
        self.assertEqual((r["b"]["y"], r["a"]["y"], r["a"]["g"], r["rr"]), (6, 6, 1, .5))
        self.assertEqual(v.decide(r, 1, .05)[1], "样本不足")
        self.assertIsNone(r["cluster"])
        self.assertIsNotNone(r["exact"])

    def test_gate_and_symmetry(self):
        b = v.arm_summary([(30, 1), (50, 0), (70, 2)] * 5)
        a = v.arm_summary([(20, 1), (70, 0), (70, 0)] * 5)
        st = self.cal["statistics"]
        c1, _ = v.cluster_test(b, a, st)
        c2, _ = v.cluster_test(a, b, st)
        self.assertAlmostEqual(c1[0] * c2[1], 1)
        self.assertAlmostEqual(c1[2], c2[2])
        self.assertIsNone(v.cluster_test(b, v.arm_summary([(5, 1)] * 15), st)[0])
        self.assertIsNone(v.cluster_test(b, v.arm_summary([(5, 0)] * 15), st)[0])

    def test_adjustment_and_directions(self):
        r = dict(cluster=(.2, .9, .02), rr=.6)
        self.assertEqual(v.decide(r, 3, .05)[:2], (.06, "无显著"))
        self.assertIn("有效", v.decide(r, 1, .05)[1])
        self.assertIn("变差", v.decide(dict(cluster=(1.2, 2.0, .001), rr=1.5), 3, .05)[1])

    def test_audit_and_family(self):
        s = dict(session="s", createdAt=self.cut, calls=12, realFails=1, kinds={"a":1})
        data = dict(sessionTimeline=[s], generatedAt="fixture", caliber=self.cal["version"],
                    sessions=2, uniqueCalls=12, totalCalls=14, duplicatedCalls=2, real={"a":1})
        effects = {"rules": {"1":dict(self.cfg, title="一"), "2":dict(self.cfg, title="二", detects=[]),
                             "3":dict(title="三", verify=False), "4":dict(title="四", verify_mode="config_ticket")}}
        self.assertIn("m=2", v.render(data, effects, self.cal))
        for k, bad in [("uniqueCalls", 11), ("caliber", "old"), ("real", {"b":1}),
                       ("sessionTimeline", [s, s]), ("sessionTimeline", [dict(s, createdAt=None)]),
                       ("sessionTimeline", [dict(s, realFails=2)])]:
            with self.subTest(field=k), self.assertRaises(ValueError):
                v.audit(dict(data, **{k:bad}), self.cal)

    def test_config_controls_thresholds(self):
        st = copy.deepcopy(self.cal["statistics"])
        arm = v.arm_summary([(10, 1), (10, 0), (10, 2)])
        self.assertIsNone(v.cluster_test(arm, arm, st)[0])
        st.update(minSessionsPerArm=3, minPositiveSessionsPerArm=2, alpha=.1)
        self.assertEqual(v.cluster_test(arm, arm, st)[0][2], 1)


if __name__ == "__main__":
    unittest.main()
