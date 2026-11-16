#!/usr/bin/env python3
# coding: utf-8
#
# 增量编译的改动高亮：脏行记录、视口判断、并发与关闭守卫。
#
# 手感上的两条硬约束：
#   - 每半秒就会走一次，绝不能无条件跳页（那等于每次停顿都把用户视野拽走）；
#     但改动全在视口外时必须跳，否则高亮画在看不见的地方等于没反馈。
#   - 程序化读盘（会话恢复 / 外部重载）与撤销/重做不是"用户刚改的地方"，
#     记进脏行就会在打开文档或按一次 Ctrl+Z 时闪出无关的荧光。

import ast
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load_method(relative_path, class_name, method_name, namespace):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


class Iter:
    def __init__(self, line):
        self._line = line

    def get_line(self):
        return self._line


class FakeDocument:
    def __init__(self):
        self._dirty_line_ranges = []
        self._is_shutdown = False
        self._loading_from_disk = False
        self._in_undo = False
        self.MAX_DIRTY_LINE_RANGES = 32
        self.recorded = []


class DirtyLinesTest(unittest.TestCase):

    def setUp(self):
        ns = {}
        self.mark = load_method('setzer/document/document.py', 'Document',
                                '_mark_dirty_lines', ns)
        self.take = load_method('setzer/document/document.py', 'Document',
                                'take_dirty_line_ranges', ns)
        self.records = load_method('setzer/document/document.py', 'Document',
                                   '_records_user_edits', ns)

    def test_adjacent_ranges_merge(self):
        doc = FakeDocument()
        for start, end in ((10, 10), (11, 11), (12, 12)):
            self.mark(doc, start, end)
        self.assertEqual(doc._dirty_line_ranges, [(10, 12)])

    def test_distant_ranges_stay_separate(self):
        doc = FakeDocument()
        self.mark(doc, 5, 6)
        self.mark(doc, 200, 201)
        self.assertEqual(doc._dirty_line_ranges, [(5, 6), (200, 201)])

    def test_reversed_arguments_are_normalized(self):
        doc = FakeDocument()
        self.mark(doc, 9, 3)
        self.assertEqual(doc._dirty_line_ranges, [(3, 9)])

    def test_count_is_capped_keeping_the_most_recent(self):
        doc = FakeDocument()
        for i in range(200):
            self.mark(doc, i * 100, i * 100)
        self.assertLessEqual(len(doc._dirty_line_ranges), doc.MAX_DIRTY_LINE_RANGES)
        self.assertEqual(doc._dirty_line_ranges[-1], (19900, 19900))

    def test_take_clears(self):
        doc = FakeDocument()
        self.mark(doc, 1, 2)
        self.assertEqual(self.take(doc), [(1, 2)])
        self.assertEqual(self.take(doc), [])

    def test_programmatic_and_undo_changes_are_not_recorded(self):
        doc = FakeDocument()
        self.assertTrue(self.records(doc))
        for flag in ('_loading_from_disk', '_in_undo', '_is_shutdown'):
            setattr(doc, flag, True)
            self.assertFalse(self.records(doc), flag)
            setattr(doc, flag, False)


class FlashViewportTest(unittest.TestCase):
    '''flash_changed_rectangles 只在改动全在视口外时才跳页。'''

    def setUp(self):
        ns = {'GLib': mock.Mock(get_monotonic_time=lambda: 0)}
        self.flash = load_method('setzer/document/preview/preview.py', 'Preview',
                                 'flash_changed_rectangles', ns)
        self.show = load_method('setzer/document/preview/preview.py', 'Preview',
                                'show_synctex_rectangles', ns)
        self.scroll = load_method('setzer/document/preview/preview.py', 'Preview',
                                  '_scroll_to_rectangle', ns)

    def preview(self, first_page, last_page):
        preview = mock.Mock()
        preview.layout = mock.Mock()
        # get_page_by_offset 返回 1-based 页号：顶部偏移落在 first_page，
        # 往下一个视口高（+100）落在 last_page。
        preview.layout.get_page_by_offset.side_effect = \
            lambda offset: first_page if offset == 0 else last_page
        preview.layout.get_page_top.return_value = 500.0
        preview.layout.scale_factor = 1.0
        preview.view.content.scrolling_offset_y = 0
        preview.view.content.scrolling_offset_x = 0
        preview.view.content.height = 100
        preview.view.content.width = 100
        preview.show_synctex_rectangles = lambda rectangles: None
        preview._scroll_to_rectangle = mock.Mock(return_value=True)
        return preview

    def test_no_scroll_when_a_changed_page_is_visible(self):
        preview = self.preview(10, 20)
        self.flash(preview, [{'page': 14, 'h': 1, 'v': 2, 'width': 3, 'height': 4}])
        preview._scroll_to_rectangle.assert_not_called()

    def test_scrolls_when_every_changed_page_is_offscreen(self):
        preview = self.preview(10, 20)
        self.flash(preview, [{'page': 44, 'h': 1, 'v': 2, 'width': 3, 'height': 4}])
        preview._scroll_to_rectangle.assert_called_once()

    def test_returns_false_without_rectangles_or_layout(self):
        preview = self.preview(1, 5)
        self.assertFalse(self.flash(preview, []))
        preview.layout = None
        self.assertFalse(self.flash(preview, [{'page': 3}]))

    def test_out_of_range_page_does_not_scroll(self):
        '''get_page_top 越界时不动视野，也别把这一帧当成跳转成功。'''
        preview = mock.Mock()
        preview.layout = mock.Mock()
        preview.layout.get_page_top.return_value = None
        preview.layout.scale_factor = 1.0
        self.assertFalse(self.scroll(preview, {'page': 999, 'h': 0, 'v': 0,
                                              'width': 1, 'height': 1}))
        preview.view.content.scroll_to_position.assert_not_called()


class ChangeHighlightGuardTest(unittest.TestCase):
    '''查询在后台线程跑，构建却可能比它更快。'''

    def setUp(self):
        from setzer.document.change_highlight.change_highlight import ChangeHighlight
        self.ChangeHighlight = ChangeHighlight

    def document(self, ranges):
        doc = mock.Mock()
        doc.take_dirty_line_ranges.return_value = ranges
        return doc

    def test_concurrent_call_does_not_spawn_a_second_thread(self):
        change_highlight = self.ChangeHighlight(self.document([(1, 2)]))
        with change_highlight._lock:
            change_highlight._running = True
        started = []
        with mock.patch('setzer.document.change_highlight.change_highlight.threading.Thread') as thread:
            thread.side_effect = lambda **kw: started.append(kw) or mock.Mock()
            change_highlight.highlight_after_build('/p.pdf', '/p.tex')
        self.assertEqual(started, [])
        self.assertTrue(change_highlight._stale)

    def test_ranges_are_dropped_only_for_an_actual_query(self):
        '''空产物/空源文件时不该白吃掉已攒下的脏行。'''
        change_highlight = self.ChangeHighlight(self.document([(1, 2)]))
        doc = change_highlight.document
        change_highlight.highlight_after_build(None, '/p.tex')
        doc.take_dirty_line_ranges.assert_not_called()

    def test_shutdown_stops_delivery_to_a_destroyed_preview(self):
        doc = self.document([(1, 2)])
        doc.preview = mock.Mock()
        change_highlight = self.ChangeHighlight(doc)
        change_highlight.shutdown()
        change_highlight._show([{'page': 1}])
        doc.preview.flash_changed_rectangles.assert_not_called()

    def test_queries_are_capped(self):
        '''脏行区间再多，也只查最近的 MAX_QUERIES 段（每段都是子进程调用）。'''
        change_highlight = self.ChangeHighlight(
            self.document([(i, i) for i in range(100)]))
        with mock.patch('setzer.document.change_highlight.change_highlight.threading.Thread') as thread:
            thread.return_value = mock.Mock()
            change_highlight.highlight_after_build('/p.pdf', '/p.tex')
        ranges = thread.call_args.kwargs['args'][0]
        self.assertEqual(len(ranges), self.ChangeHighlight.MAX_QUERIES)
        # 留的是最近的段落，不是最旧的。
        self.assertEqual(ranges[-1], (99, 99))


class LivePathOnlyTest(unittest.TestCase):
    '''不许污染其他引擎的手感。'''

    def test_wired_behind_the_phitex_live_gate(self):
        source = (ROOT / 'setzer/document/build_system/build_system.py').read_text(encoding='utf-8')
        index = source.index('self.change_highlight.highlight_after_build')
        window = source[index - 700:index]
        self.assertIn('is_phitex_live_build()', window)


if __name__ == '__main__':
    unittest.main()
