# -*- coding: utf-8 -*-
"""判定器回归测试（v0.2 §2 事件锚点契约 + LLM 式分类）。

跑法：& $py -X utf8 -m unittest discover -s scripts/tests
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.dirname(os.path.abspath(__file__))  # scripts/tests → 取父目录 scripts
sys.path.insert(0, os.path.dirname(SCRIPTS))          # dsh-evolve/scripts
sys.path.insert(0, SCRIPTS)
import classify  # noqa: E402


class TestAnchorContract(unittest.TestCase):
    """事件锚点契约：一条 tool error = 首文本以 Error:/错误： 开头。"""

    def test_error_prefix_is_failure(self):
        kind, real = classify.classify("pwsh", "Error: something broke")
        self.assertTrue(real)
        self.assertIsNotNone(kind)

    def test_chinese_error_prefix(self):
        kind, real = classify.classify("pwsh", "错误：命令失败")
        self.assertTrue(real)

    def test_non_error_prefix_is_not_failure(self):
        kind, real = classify.classify("pwsh", "normal output\nError: look at my text")
        self.assertIsNone(kind)
        self.assertFalse(real)

    def test_empty_is_not_failure(self):
        kind, real = classify.classify("pwsh", "")
        self.assertIsNone(kind)
        self.assertFalse(real)


class TestClassification(unittest.TestCase):
    def test_edit_no_match_not_found(self):
        self.assertEqual(classify.classify("edit", "Error: old_string was not found in file")[0], "edit_no_match")

    def test_edit_no_match_ambiguous(self):
        self.assertEqual(classify.classify("edit", "Error: old_string matched 2 times")[0], "edit_no_match")

    def test_file_not_read(self):
        self.assertEqual(classify.classify("edit", "Error: file has not been read")[0], "file_not_read")

    def test_file_conflict(self):
        self.assertEqual(classify.classify("edit", "Error: file changed since it was read")[0], "file_conflict")

    def test_read_out_of_range(self):
        self.assertEqual(classify.classify("read", "Error: line 999 is out of range for file")[0], "read_out_of_range")

    def test_file_read_missing(self):
        self.assertEqual(classify.classify("read", "Error: cannot read file")[0], "file_read_missing")

    def test_web_fetch_fail(self):
        self.assertEqual(classify.classify("web_fetch", "Error: web fetch failed: TypeError: fetch failed")[0], "web_fetch_fail")

    def test_tool_timeout(self):
        self.assertEqual(classify.classify("pwsh", "Error: tool call timed out after 30000ms")[0], "tool_timeout")

    def test_unclassified_goes_to_tool_error(self):
        kind = classify.classify("cordis_inspect_query", 'Error: unknown entry id "web"')[0]
        self.assertTrue(kind.startswith("tool_error::"))

    def test_first_line_used_as_kind(self):
        kind = classify.classify("x", "Error: weird thing happened\nsecond line")[0]
        self.assertIn("weird thing happened", kind)


class TestExclusion(unittest.TestCase):
    """排除类：标注不计入真失败（is_real=False）。"""

    def test_host_fault_pty(self):
        kind, real = classify.classify("pwsh", "Error: PTY shell exited during startup")
        self.assertEqual(kind, "host_fault")
        self.assertFalse(real)

    def test_user_cancelled(self):
        kind, real = classify.classify("pwsh", "Error: the user cancelled ask_user_question")
        self.assertEqual(kind, "user_cancelled")
        self.assertFalse(real)

    def test_tool_aborted(self):
        kind, real = classify.classify("pwsh", "Error: tool call aborted")
        self.assertEqual(kind, "tool_aborted")
        self.assertFalse(real)


class TestVerifyHelpers(unittest.TestCase):
    """verify 的匹配/配置态判定（口径一致性）。"""

    def setUp(self):
        import verify
        self.verify = verify

    def test_config_kind_detection(self):
        self.assertTrue(self.verify.is_config_kind("tool_error::Error: multiple usable web providers are registered (deepseek-fetch-api, http)"))
        self.assertTrue(self.verify.is_config_kind("tool_error::Error: cross-origin redirect to https://cn.bing.com is not followed"))
        self.assertTrue(self.verify.is_config_kind("tool_error::Error: DeepSeek API error (HTTP 401): api key invalid"))
        self.assertFalse(self.verify.is_config_kind("edit_no_match"))

    def test_match_kind_substring(self):
        cfg = {"detectsPatterns": ["multiple usable web providers"]}
        self.assertTrue(self.verify.match_kind("tool_error::Error: multiple usable web providers are registered", cfg))
        self.assertFalse(self.verify.match_kind("edit_no_match", cfg))

    def test_match_kind_exact(self):
        cfg = {"detects": ["file_not_read"]}
        self.assertTrue(self.verify.match_kind("file_not_read", cfg))


if __name__ == "__main__":
    unittest.main()
