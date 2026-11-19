#!/usr/bin/env python3
# coding: utf-8
#
# phitex session compare --json 的解析、bbox → 预览矩形坐标换算、AI 基线文件。
#
# 坐标换算的硬约束：compare 的 bbox 是 [x0,y0,x1,y1]（points、页左上原点、
# y 向下），预览的 synctex 惯例是 v = 底边距页顶的距离——两条坐标系的页顶
# 是同一参照物，所以 v 恰好等于 y1，高 top = v - height = y0。换算错了，
# diff 高亮会上下颠倒或整段偏移。

import json
import os
import subprocess
import tempfile
import unittest

from setzer.helpers import phitex_compare


def make_payload(changes=None, **overrides):
    '''贴近 compare.rs changes_for 真实 schema 的样例。'''
    payload = {
        'rev': 'the files as they are',
        'label': 'baseline → the files as they are',
        'pdf': '/tmp/proj/.phitex/compare/main-diff.pdf',
        'tex': '/tmp/proj/.phitex/compare/tex',
        'pages': 2,
        'changes': changes if changes is not None else [],
        'errors': [],
        'ms': 12.5,
    }
    payload.update(overrides)
    return payload


class ParseCompareJsonTest(unittest.TestCase):

    def test_single_object_with_noise_lines(self):
        payload = make_payload()
        stdout = 'some preamble line\n{}\ntrailing text'.format(json.dumps(payload))
        self.assertEqual(phitex_compare.parse_compare_json(stdout), payload)

    def test_error_object_parsed(self):
        stdout = '{"error": "no version to compare with: give a revision", "status": 4}'
        payload = phitex_compare.parse_compare_json(stdout)
        self.assertIsNotNone(payload)
        self.assertEqual(payload.get('status'), 4)

    def test_empty_and_garbage(self):
        self.assertIsNone(phitex_compare.parse_compare_json(''))
        self.assertIsNone(phitex_compare.parse_compare_json(None))
        self.assertIsNone(phitex_compare.parse_compare_json('no json here'))
        # 第一行是坏 JSON、第二行是好的：跳过坏行。
        stdout = '{broken\n{"pages": 1, "changes": []}'
        payload = phitex_compare.parse_compare_json(stdout)
        self.assertEqual(payload.get('pages'), 1)

    def test_multi_line_json_not_matched(self):
        # _first_json_object 语义：只认单行整体 loads 的对象。跨行 JSON 不认。
        stdout = '{\n  "pages": 1\n}'
        self.assertIsNone(phitex_compare.parse_compare_json(stdout))


class CompareFailedTest(unittest.TestCase):

    def test_error_without_changes_is_failure(self):
        payload = {'error': 'no phitex watch runs here', 'status': 3}
        self.assertTrue(phitex_compare.compare_failed(payload))

    def test_tex_errors_with_changes_are_not_failure(self):
        # diff 构建有 TeX 错误：exit 1，但 payload 仍带 changes（定位全 null）。
        payload = make_payload(errors=['main.tex:12: Undefined control sequence'],
                               pdf=None, pages=None)
        self.assertFalse(phitex_compare.compare_failed(payload))

    def test_none_is_failure(self):
        self.assertTrue(phitex_compare.compare_failed(None))


class ChangesToRectanglesTest(unittest.TestCase):

    def test_y_flip_and_fields(self):
        changes = [{'n': 1, 'kind': 'add', 'page': 1,
                    'bbox': [61.0, 681.53, 254.92, 692.61],
                    'file': 'main.tex', 'line': 9,
                    'old_text': '', 'new_text': 'added', 'section': 'One'}]
        rects = phitex_compare.changes_to_rectangles(changes)
        self.assertEqual(len(rects), 1)
        rect = rects[0]
        # v = 底边距页顶 = y1（y 向下系里的下边）。
        self.assertAlmostEqual(rect['v'], 692.61)
        self.assertAlmostEqual(rect['h'], 61.0)
        self.assertAlmostEqual(rect['width'], 254.92 - 61.0)
        self.assertAlmostEqual(rect['height'], 692.61 - 681.53)
        self.assertEqual(rect['page'], 1)
        self.assertEqual(rect['kind'], 'add')
        self.assertEqual(rect['n'], 1)

    def test_null_page_or_bbox_skipped(self):
        changes = [
            {'n': 1, 'kind': 'del', 'page': None, 'bbox': None,
             'file': 'main.tex', 'line': 3, 'old_text': 'x', 'new_text': ''},
            {'n': 2, 'kind': 'add', 'page': 2, 'bbox': None,
             'file': 'main.tex', 'line': 4, 'old_text': '', 'new_text': 'y'},
            {'n': 3, 'kind': 'add', 'page': None, 'bbox': [1, 2, 3, 4],
             'file': 'main.tex', 'line': 5, 'old_text': '', 'new_text': 'z'},
        ]
        self.assertEqual(phitex_compare.changes_to_rectangles(changes), [])

    def test_malformed_entries_skipped(self):
        changes = [
            'not a dict',
            {'n': 2, 'kind': 'add', 'page': 1, 'bbox': [0, 0], 'file': 'm'},
            {'n': 3, 'kind': 'add', 'page': 1, 'bbox': 'bad', 'file': 'm'},
            {'n': 4, 'kind': 'add', 'page': 1, 'bbox': [5, 5, 2, 9], 'file': 'm'},  # x1<=x0
            {'n': 5, 'kind': 'add', 'page': True, 'bbox': [1, 2, 3, 4], 'file': 'm'},  # bool 冒充页号
            {'n': 6, 'kind': 'add', 'page': 1, 'bbox': [1, 2, 3, 4], 'file': 'm'},  # 合法
        ]
        rects = phitex_compare.changes_to_rectangles(changes)
        self.assertEqual(len(rects), 1)
        self.assertEqual(rects[0]['n'], 6)

    def test_missing_kind_defaults_to_change(self):
        changes = [{'n': 1, 'page': 1, 'bbox': [1, 2, 3, 4], 'file': 'm'}]
        rects = phitex_compare.changes_to_rectangles(changes)
        self.assertEqual(rects[0]['kind'], 'change')

    def test_empty_and_none(self):
        self.assertEqual(phitex_compare.changes_to_rectangles([]), [])
        self.assertEqual(phitex_compare.changes_to_rectangles(None), [])


class BaselineTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.doc_dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_roundtrip(self):
        path = phitex_compare.write_baseline('hello\nworld', self.doc_dir, 'main.tex')
        self.assertIsNotNone(path)
        self.assertTrue(path.endswith(os.path.join('.neosetzer', 'ai-baseline', 'main.tex')))
        self.assertEqual(phitex_compare.read_baseline(path), 'hello\nworld')
        # baseline_matches 现在比的是"武装时刻记下的正文"（不再回头读文件）。
        self.assertTrue(phitex_compare.baseline_matches(
            'hello\nworld', phitex_compare.read_baseline(path)))
        self.assertFalse(phitex_compare.baseline_matches('changed', 'hello\nworld'))

    def test_overwrite(self):
        first = phitex_compare.write_baseline('v1', self.doc_dir, 'main.tex')
        second = phitex_compare.write_baseline('v2', self.doc_dir, 'main.tex')
        self.assertEqual(first, second)
        self.assertEqual(phitex_compare.read_baseline(second), 'v2')

    def test_read_missing_is_none(self):
        self.assertIsNone(phitex_compare.read_baseline(
            os.path.join(self.doc_dir, 'nope', 'missing.tex')))

    def test_clear_tolerant(self):
        path = phitex_compare.write_baseline('x', self.doc_dir, 'main.tex')
        phitex_compare.clear_baseline(self.doc_dir, 'main.tex')
        self.assertFalse(os.path.exists(path))
        # 再删一次（不存在）不抛异常。
        phitex_compare.clear_baseline(self.doc_dir, 'main.tex')


if __name__ == '__main__':
    unittest.main()


class GitSnapshotTest(unittest.TestCase):
    '''git 通道：私有快照提交（papilot 的"私有 index"做法）。

    硬约束：用户的 HEAD、staging、worktree 一概不能动；sha 必须能被
    rev-parse 解析（PhiTeX 的 gitoxide 按 sha 读对象，不要求可达）。'''

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _git(self, *args):
        return subprocess.run(['git', '-C', self.repo, '-c', 'user.name=t',
                               '-c', 'user.email=t@t'] + list(args),
                              capture_output=True, text=True)

    def _write(self, name, text):
        with open(os.path.join(self.repo, name), 'w', encoding='utf-8') as handle:
            handle.write(text)

    def _head(self):
        return self._git('rev-parse', 'HEAD').stdout.strip()

    def _status(self):
        return self._git('status', '--porcelain').stdout

    def test_snapshot_in_repo_without_commit(self):
        # 没 HEAD（刚 init）：从空树起步，快照仍应成功。
        self._git('init', '-q')
        self._write('main.tex', 'hello')
        status_before = self._status()
        sha = phitex_compare.write_git_snapshot(self.repo)
        self.assertIsNotNone(sha)
        # 快照不许改变用户的 git 状态（untracked 文件仍 untracked，
        # index/HEAD 一概不动）——不是"干净"，而是"与快照前逐字一致"。
        self.assertEqual(self._status(), status_before)
        # sha 可解析，且能读出快照里的文件（不要求可达）。
        show = self._git('show', '{}:main.tex'.format(sha))
        self.assertEqual(show.stdout, 'hello')

    def test_snapshot_tracks_subfile_changes(self):
        # 多文件的核心卖点：子文件内容进了快照，compare 能看见它的改动。
        self._git('init', '-q')
        self._write('main.tex', 'main')
        self._write('sec.tex', 'v1')
        self._git('add', '-A')
        self._git('commit', '-m', 'init')
        self._write('sec.tex', 'v1 edited')
        status_before = self._status()
        sha = phitex_compare.write_git_snapshot(self.repo)
        # 快照在编辑之后：sha 树里是编辑后的 sec.tex。
        self.assertEqual(self._git('show', '{}:sec.tex'.format(sha)).stdout,
                         'v1 edited')
        self.assertEqual(self._head(), self._git('rev-parse', 'HEAD').stdout.strip())
        self.assertEqual(self._status(), status_before)

    def test_snapshot_ignores_gitignored_files(self):
        self._git('init', '-q')
        self._write('.gitignore', 'build/\n')
        os.makedirs(os.path.join(self.repo, 'build'))
        self._write(os.path.join('build', 'main.aux'), 'artifact')
        self._write('main.tex', 'real text')
        sha = phitex_compare.write_git_snapshot(self.repo)
        show = self._git('ls-tree', '-r', '--name-only', sha)
        self.assertIn('main.tex', show.stdout)
        self.assertNotIn('build/main.aux', show.stdout)

    def test_not_a_repo_returns_none(self):
        self._write('main.tex', 'x')
        self.assertIsNone(phitex_compare.write_git_snapshot(self.repo))


class LooksLikeRevTest(unittest.TestCase):

    def test_sha_is_rev(self):
        self.assertTrue(phitex_compare.looks_like_rev(
            'bcc26f912f88346b55950d3fabc7761b82e4e546'))
        self.assertTrue(phitex_compare.looks_like_rev('HEAD~2'))
        self.assertTrue(phitex_compare.looks_like_rev('staged'))

    def test_absolute_paths_are_not_rev(self):
        # 文件通道永远传绝对路径（write_baseline 的返回值），所以判据就是
        # "非绝对路径即 rev"——PhiTeX 那边按存在性自行判别两种形态。
        self.assertFalse(phitex_compare.looks_like_rev('/tmp/e2e/main.tex'))
        self.assertTrue(phitex_compare.looks_like_rev('baseline.tex'))
        self.assertFalse(phitex_compare.looks_like_rev(''))
        self.assertFalse(phitex_compare.looks_like_rev(None))


class PairMovesTest(unittest.TestCase):

    @staticmethod
    def _change(n, kind, old='', new=''):
        return {'n': n, 'kind': kind, 'old_text': old, 'new_text': new}

    def test_exact_fingerprint_pairs(self):
        changes = [self._change(1, 'del', old='The proof relies on a diagonal argument.'),
                   self._change(2, 'add', new='the proof relies on a  diagonal, argument!')]
        phitex_compare.pair_moves(changes)
        self.assertEqual([c['kind'] for c in changes], ['moved', 'moved'])
        self.assertEqual(changes[0]['move_of'], 2)
        self.assertEqual(changes[1]['move_of'], 1)

    def test_similar_long_fragments_pair(self):
        # 同一段话挪走后排版微调了几个词：≥20 字母数字 + 高相似 → moved。
        long_a = 'convergence under mild assumptions on the network topology'
        long_b = 'Convergence, under mild assumptions about the network topology.'
        changes = [self._change(1, 'del', old=long_a),
                   self._change(2, 'add', new=long_b)]
        phitex_compare.pair_moves(changes)
        self.assertEqual([c['kind'] for c in changes], ['moved', 'moved'])

    def test_short_unrelated_pairs_stay(self):
        changes = [self._change(1, 'del', old='removed'),
                   self._change(2, 'add', new='added text here')]
        phitex_compare.pair_moves(changes)
        self.assertEqual([c['kind'] for c in changes], ['del', 'add'])

    def test_each_side_consumed_once(self):
        same = 'x' * 40
        changes = [self._change(1, 'del', old=same),
                   self._change(2, 'add', new=same),
                   self._change(3, 'add', new=same)]
        phitex_compare.pair_moves(changes)
        kinds = [c['kind'] for c in changes]
        # 只配一对：剩一个 add 保持原样。
        self.assertEqual(kinds.count('moved'), 2)
        self.assertEqual(kinds.count('add'), 1)

    def test_change_kind_untouched(self):
        changes = [self._change(1, 'change', old='a' * 40, new='b' * 40)]
        phitex_compare.pair_moves(changes)
        self.assertEqual(changes[0]['kind'], 'change')

    def test_rectangles_carry_moved_kind(self):
        changes = [self._change(1, 'del', old='The proof relies on a diagonal argument.'),
                   {'n': 2, 'kind': 'add', 'page': 1, 'bbox': [1, 2, 3, 4],
                    'new_text': 'the proof relies on a diagonal argument'}]
        phitex_compare.pair_moves(changes)
        rects = phitex_compare.changes_to_rectangles(changes)
        self.assertEqual(rects[0]['kind'], 'moved')
