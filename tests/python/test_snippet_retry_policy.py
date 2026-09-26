#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# GPL-3.0-or-later

'''retry_policy 的纯逻辑测试：冷却计数、身份变更重置、原因保留。'''

import unittest

from setzer.document.snippet_preview.retry_policy import RetryPolicy


class RetryPolicyTest(unittest.TestCase):

    IDENTITY_A = (('main.tex', 1, 2), 100)
    IDENTITY_B = (('main.tex', 1, 2), 500)

    def test_first_attempt_is_allowed(self):
        self.assertTrue(RetryPolicy().should_attempt(self.IDENTITY_A))

    def test_blocks_after_max_consecutive_failures(self):
        policy = RetryPolicy(max_consecutive=2)
        self.assertTrue(policy.should_attempt(self.IDENTITY_A))
        policy.record_failure('! LaTeX Error: File `x.sty\' not found.')
        self.assertTrue(policy.should_attempt(self.IDENTITY_A))
        policy.record_failure('! LaTeX Error: File `x.sty\' not found.')
        self.assertFalse(policy.should_attempt(self.IDENTITY_A))
        self.assertFalse(policy.should_attempt(self.IDENTITY_A))

    def test_identity_change_clears_cooldown(self):
        # 换到另一张图（identity 的图起点变了）立刻获得新机会。
        policy = RetryPolicy(max_consecutive=1)
        policy.should_attempt(self.IDENTITY_A)
        policy.record_failure('boom')
        self.assertFalse(policy.should_attempt(self.IDENTITY_A))
        self.assertTrue(policy.should_attempt(self.IDENTITY_B))
        self.assertEqual(policy.consecutive_failures, 0)

    def test_root_preamble_change_clears_cooldown(self):
        # root 文件被修好（签名变了）后不必等手动 Retry。
        policy = RetryPolicy(max_consecutive=1)
        self.assertFalse(self._after_one_failure(policy, ('sig1', 0)))
        self.assertTrue(policy.should_attempt(('sig2', 0)))

    def _after_one_failure(self, policy, identity):
        policy.should_attempt(identity)
        policy.record_failure('boom')
        return policy.should_attempt(identity)

    def test_success_resets_counter(self):
        policy = RetryPolicy(max_consecutive=2)
        policy.should_attempt(self.IDENTITY_A)
        policy.record_failure('boom')
        policy.record_success()
        self.assertEqual(policy.consecutive_failures, 0)
        self.assertEqual(policy.last_reason, '')
        self.assertTrue(policy.should_attempt(self.IDENTITY_A))

    def test_reset_keeps_identity_and_reason(self):
        policy = RetryPolicy(max_consecutive=1)
        policy.should_attempt(self.IDENTITY_A)
        policy.record_failure('! Undefined control sequence.')
        self.assertFalse(policy.should_attempt(self.IDENTITY_A))

        policy.reset()
        self.assertTrue(policy.should_attempt(self.IDENTITY_A))
        self.assertEqual(policy.last_reason, '! Undefined control sequence.')

    def test_empty_reason_keeps_previous(self):
        policy = RetryPolicy()
        policy.should_attempt(self.IDENTITY_A)
        policy.record_failure('! Package pgf Error: No shape named A is known.')
        policy.record_failure('')
        self.assertEqual(policy.last_reason,
                         '! Package pgf Error: No shape named A is known.')

    def test_identity_change_drops_stale_reason(self):
        policy = RetryPolicy()
        policy.should_attempt(self.IDENTITY_A)
        policy.record_failure('boom')
        policy.should_attempt(self.IDENTITY_B)
        self.assertEqual(policy.last_reason, '')

    def test_default_max_is_two(self):
        self.assertEqual(RetryPolicy().max_consecutive, 2)


if __name__ == '__main__':
    unittest.main()
