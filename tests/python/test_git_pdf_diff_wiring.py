#!/usr/bin/env python3
# coding: utf-8
#
# Git 页 LaTeX 文件 diff → PhiTeX PDF diff 管线的接线守护。
#
# 这条链的关键约束都是"顺序"性质的，纯源码顺序断言最稳：
#   - supports_compare / session 存活 / AI 在途查询三个门控必须都在
#     compare 发起之前（JOB-diff session 并发会互相污染）；
#   - 解除 AI 武装（ai_diff.disarm()）必须在发起查询之前，否则后续
#     自动构建的 AI compare 会覆盖 git diff 结果；disarm 又绝不能走
#     end_review（会 stop_compare，打断紧接着的 git compare）；
#   - 交付的 result 必须带 source='git'——preview_presenter 的两条 AI
#     toast（编辑完成 / 排版失败）对 git 路径都不适用，靠这个键早退。

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GIT_SECTION = 'setzer/workspace/sidebar/git/git_section.py'
PREVIEW_PRESENTER = 'setzer/document/preview/preview_presenter.py'


class GitPdfDiffWiringTest(unittest.TestCase):

    def setUp(self):
        self.source = (ROOT / GIT_SECTION).read_text(encoding='utf-8')

    def index(self, needle):
        self.assertIn(needle, self.source)
        return self.source.index(needle)

    def test_gates_precede_compare(self):
        '''supports_compare / session 存活 / AI 在途三个门控都在 compare 前。'''
        compare = self.index("session.compare('HEAD'")
        for gate in ('supports_compare()', 'session.pid is None',
                     'is_query_running()'):
            self.assertLess(self.index(gate), compare)

    def test_disarm_precedes_query_thread(self):
        '''解除 AI 武装（ai_diff.disarm()）在发起查询线程之前。

        且必须用 disarm 而非 end_review：后者会 stop_compare，把紧接
        着要跑的 git compare 所属的 JOB-diff session 停掉。'''
        disarm = self.index('ai_diff.disarm()')
        thread = self.index('_pdf_diff_query')
        self.assertLess(disarm, thread)
        self.assertNotIn('ai_diff.end_review()', self.source)
        # 旧字段名 baseline_path 已不存在于 AiDiff：残留写法是空操作。
        self.assertNotIn('ai_diff.baseline_path', self.source)

    def test_result_carries_git_source(self):
        '''交付的 result 必须带 source='git'（AI toast 靠它早退）。'''
        source_marker = self.index("'source': 'git'")
        set_result = self.index('preview.set_diff_result(result)')
        self.assertLess(source_marker, set_result)

    def test_failed_flag_mirrors_ai_shape(self):
        '''result 与 AiDiff 同形状：failed 键必须在（diff_result_usable 依赖）。'''
        self.assertIn("'failed'", self.source)

    def test_file_rows_are_activatable(self):
        '''文件行必须显式 set_activatable(True)：AdwPreferencesRow 默认 False，
        漏掉这行点击行本体就毫无反应（曾导致「点不出 diff」的 bug）。'''
        source = (ROOT / 'setzer/workspace/sidebar/git/git_section_viewgtk.py').read_text(encoding='utf-8')
        start = source.index('def build_file_row')
        end = source.index('\n    def ', start + 1)
        self.assertIn('row.set_activatable(True)', source[start:end])

    def test_presenter_source_gate_precedes_both_toasts(self):
        '''source 早退必须挡在两条 AI toast（排版失败 / 编辑完成）之前。'''
        source = (ROOT / PREVIEW_PRESENTER).read_text(encoding='utf-8')
        gate = source.index("result.get('source', 'ai') != 'ai'")
        failed_toast = source.index("'ai_diff_failed'")
        edited_toast = source.index("'AI edited this document")
        self.assertLess(gate, failed_toast)
        self.assertLess(gate, edited_toast)


if __name__ == '__main__':
    unittest.main()
