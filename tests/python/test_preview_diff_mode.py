#!/usr/bin/env python3
# coding: utf-8
#
# 预览分段控件的 diff 档：模式切换、按 name 定位、回落同步。
#
# 两条硬约束（都是踩过的坑）：
#   - 档位按 **name** 定位，不能按索引：diff 档是按需增删的，索引会漂移，
#     `set_active(2)` 曾经指 help，加一档就指到别处。
#   - diff 档亮着时内容必须真的是 diff PDF：结果被清、构建把视图顶回正式
#     PDF、diff PDF 打不开——任一情形都要把模式拨回 pdf，不能让档位说谎。
#
# presenter 方法用 ast 抽取到假对象上测（同 test_change_highlight 的范式），
# 不构造真 GTK widget；档位增删（Adw.ToggleGroup 是真 widget）在
# test_preview_diff_toggle.py 里做有显示守卫的测试。

import ast
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PRESENTER = 'setzer/workspace/preview_panel/preview_panel_presenter.py'

METHODS = ['set_mode', '_diff_available', '_target_preview', '_sync_mode_switch',
           '_on_mode_switch_changed', 'on_diff_state_changed',
           'on_diff_end_requested', 'on_diff_position_changed',
           '_sync_diff_navigator', 'on_diff_nav_previous', 'on_diff_nav_next']


def load_methods(relative_path, class_name, method_names, namespace):
    '''把类里的若干方法 exec 进 namespace 并返回，供拼装假类使用。'''
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    found = {}
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name in method_names:
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'),
                 namespace)
            found[node.name] = namespace[node.name]
    missing = set(method_names) - set(found)
    if missing:
        raise AssertionError('missing methods: {}'.format(sorted(missing)))
    return found


class FakeModeSwitch:
    '''Adw.ToggleGroup 的最小替身：按 name 增删与激活。'''

    def __init__(self, names=('pdf', 'snippet', 'help')):
        self.names = list(names)
        self.active_name = 'pdf'

    def get_toggle_by_name(self, name):
        return object() if name in self.names else None

    def set_active_name(self, name):
        if name not in self.names:
            raise AssertionError('set_active_name on unknown name: {}'.format(name))
        self.active_name = name

    def get_active_name(self):
        return self.active_name


class FakeView:
    def __init__(self, names=('pdf', 'snippet', 'help')):
        self.mode_switch = FakeModeSwitch(names)
        self.diff_toggle_visible = False
        self.diff_end_visible = False
        self.diff_end_requests = []
        self.diff_nav_visible = False
        self.diff_nav_state = None
        self.diff_nav_handlers = None

    def set_diff_toggle_visible(self, visible):
        self.diff_toggle_visible = visible
        if visible and 'diff' not in self.mode_switch.names:
            self.mode_switch.names.append('diff')
        elif not visible and 'diff' in self.mode_switch.names:
            self.mode_switch.names.remove('diff')

    def set_diff_end_visible(self, visible):
        # 结束按钮只跟"结果有无"走（含排版失败的），与档位的 usable 语义不同。
        self.diff_end_visible = visible

    def set_diff_end_handler(self, callback):
        self._diff_end_handler = callback

    def click_diff_end(self):
        self._diff_end_handler()

    def set_diff_nav_visible(self, visible):
        self.diff_nav_visible = visible

    def set_diff_nav_state(self, index, total):
        self.diff_nav_state = (index, total)

    def set_diff_nav_handlers(self, previous, next_):
        self.diff_nav_handlers = (previous, next_)

    def click_diff_nav_previous(self):
        self.diff_nav_handlers[0]()

    def click_diff_nav_next(self):
        self.diff_nav_handlers[1]()


class FakePreview:
    def __init__(self, diff_result=None):
        self.diff_result = diff_result
        self.showing_diff_pdf = False
        self.show_diff_calls = 0
        self.show_main_calls = 0
        # diff 导航器用：矩形列表与其下标（真 Preview 的对应字段）。
        self.diff_rectangles = []
        self.diff_change_index = None
        self.step_calls = []
        # 模拟 "diff PDF 打不开" 的失败模式：show_diff_pdf 不置位。
        self.show_diff_succeeds = True

    @property
    def diff_result_usable(self):
        # 与 Preview.diff_result_usable 同语义（失败结果不算可看）。
        result = self.diff_result
        return result is not None and not result.get('failed')

    def show_diff_pdf(self):
        self.show_diff_calls += 1
        if self.show_diff_succeeds and self.diff_result is not None:
            self.showing_diff_pdf = True
            self.diff_rectangles = list(self.diff_result.get('rectangles') or [])
            if self.diff_rectangles and self.diff_change_index is None:
                self.diff_change_index = 0

    def show_main_pdf(self):
        self.show_main_calls += 1
        self.showing_diff_pdf = False
        self.diff_rectangles = []

    def step_diff_change(self, delta):
        self.step_calls.append(delta)
        total = len(self.diff_rectangles)
        if not total:
            return False
        current = self.diff_change_index if isinstance(self.diff_change_index, int) else 0
        self.diff_change_index = max(0, min(current + delta, total - 1))
        return True


class FakeActions:
    def __init__(self):
        self.update_calls = 0

    def update_actions(self):
        self.update_calls += 1


class FakeWorkspace:
    def __init__(self, preview):
        self._preview = preview
        self.actions = FakeActions()
        self.preview_or_help_calls = []

    def get_preview_target_document(self):
        if self._preview is None:
            return None
        return mock.Mock(preview=self._preview)

    def set_show_preview_or_help(self, *args):
        self.preview_or_help_calls.append(args)


class FakePresenter:
    '''由抽取出来的真方法 + 最小桩组成的假 presenter。'''

    def __init__(self, preview, names=('pdf', 'snippet', 'help')):
        self.mode = 'pdf'
        self.view = FakeView(names)
        self.workspace = FakeWorkspace(preview)
        self._syncing_mode_switch = False
        self.preview_document_calls = []
        self.update_buttons_calls = 0
        self.view.set_diff_nav_handlers(self.on_diff_nav_previous, self.on_diff_nav_next)

    def set_preview_document(self):
        self.preview_document_calls.append(self.mode)

    def update_buttons(self):
        # 真实现里还管着一堆工具栏按钮的显隐；这里只数调用，
        # 导航器的效果由 set_mode/on_diff_state_changed 自己触发。
        self.update_buttons_calls += 1

    def _sync_mode_action_state(self):
        pass


for _name, _fn in load_methods(PRESENTER, 'PreviewPanelPresenter', METHODS, {}).items():
    setattr(FakePresenter, _name, _fn)


DIFF_RESULT = {'rectangles': [], 'changes': [], 'pdf': '/tmp/x-diff.pdf', 'pages': 2,
               'change_count': 3}
DIFF_RECT_1 = {'page': 1, 'h': 10, 'v': 20, 'width': 30, 'height': 5, 'kind': 'add', 'n': 1}
DIFF_RECT_2 = {'page': 2, 'h': 10, 'v': 40, 'width': 30, 'height': 5, 'kind': 'del', 'n': 2}
NAV_RESULT = dict(DIFF_RESULT, rectangles=[DIFF_RECT_1, DIFF_RECT_2], change_count=2)


class SetModeDiffTest(unittest.TestCase):

    def test_pdf_to_diff_shows_diff_pdf(self):
        preview = FakePreview(DIFF_RESULT)
        presenter = FakePresenter(preview)
        presenter.set_mode('diff')
        self.assertEqual(presenter.mode, 'diff')
        self.assertEqual(preview.show_diff_calls, 1)
        self.assertTrue(preview.showing_diff_pdf)
        self.assertEqual(presenter.view.mode_switch.active_name, 'diff')
        # diff 档不换 stack 子页：不走 set_preview_document。
        self.assertEqual(presenter.preview_document_calls, [])

    def test_diff_to_pdf_restores_main_pdf_and_keeps_result(self):
        preview = FakePreview(DIFF_RESULT)
        presenter = FakePresenter(preview)
        presenter.set_mode('diff')
        presenter.set_mode('pdf')
        self.assertEqual(presenter.mode, 'pdf')
        self.assertEqual(preview.show_main_calls, 1)
        self.assertFalse(preview.showing_diff_pdf)
        # 结果保留：切走只是去看别的内容，不是放弃审阅。
        self.assertIs(preview.diff_result, DIFF_RESULT)

    def test_diff_without_result_stays_pdf(self):
        preview = FakePreview(None)
        presenter = FakePresenter(preview)
        presenter.set_mode('diff')
        self.assertEqual(presenter.mode, 'pdf')
        self.assertEqual(preview.show_diff_calls, 0)

    def test_unopenable_diff_falls_back_to_pdf(self):
        preview = FakePreview(DIFF_RESULT)
        preview.show_diff_succeeds = False
        presenter = FakePresenter(preview)
        presenter.set_mode('diff')
        # diff PDF 打不开：模式必须拨回 pdf，别让档位说谎。
        self.assertEqual(presenter.mode, 'pdf')
        self.assertEqual(presenter.view.mode_switch.active_name, 'pdf')

    def test_entering_diff_creates_missing_toggle(self):
        # 面板还没建过 diff 档（例如结果送达时面板没连着）：set_mode 要
        # 自己补档位，否则 _sync_mode_switch 会把它降级回 pdf。
        preview = FakePreview(DIFF_RESULT)
        presenter = FakePresenter(preview, names=('pdf', 'snippet', 'help'))
        self.assertNotIn('diff', presenter.view.mode_switch.names)
        presenter.set_mode('diff')
        self.assertIn('diff', presenter.view.mode_switch.names)
        self.assertEqual(presenter.mode, 'diff')

    def test_snippet_to_diff_switches_without_touching_stack(self):
        preview = FakePreview(DIFF_RESULT)
        presenter = FakePresenter(preview)
        presenter.mode = 'snippet'
        presenter.set_mode('diff')
        self.assertEqual(presenter.mode, 'diff')
        self.assertEqual(preview.show_diff_calls, 1)


class SyncModeSwitchTest(unittest.TestCase):

    def test_name_based_sync(self):
        presenter = FakePresenter(FakePreview(DIFF_RESULT))
        presenter.mode = 'snippet'
        presenter._sync_mode_switch()
        self.assertEqual(presenter.view.mode_switch.active_name, 'snippet')

    def test_missing_diff_toggle_downgrades_to_pdf(self):
        # 档位不在时 set_active_name 会对未知名字打 CRITICAL 并拒绝，所以
        # 必须先在 _sync_mode_switch 里降级——这条兜住"结果已清但档位还没
        # 被移除"的窗口期。
        presenter = FakePresenter(FakePreview(DIFF_RESULT), names=('pdf', 'snippet', 'help'))
        presenter.mode = 'diff'
        presenter._sync_mode_switch()
        self.assertEqual(presenter.mode, 'pdf')
        self.assertEqual(presenter.view.mode_switch.active_name, 'pdf')


class ModeSwitchChangedTest(unittest.TestCase):

    def test_help_by_name(self):
        presenter = FakePresenter(FakePreview(DIFF_RESULT))
        presenter.view.mode_switch.active_name = 'help'
        presenter._on_mode_switch_changed(presenter.view.mode_switch, None)
        self.assertEqual(presenter.workspace.preview_or_help_calls, [(False, True)])
        # 拨回当前模式，恒有一档亮着。
        self.assertEqual(presenter.view.mode_switch.active_name, 'pdf')

    def test_no_active_falls_back_to_current(self):
        presenter = FakePresenter(FakePreview(DIFF_RESULT))
        presenter.mode = 'snippet'
        presenter.view.mode_switch.active_name = None
        presenter._on_mode_switch_changed(presenter.view.mode_switch, None)
        self.assertEqual(presenter.view.mode_switch.active_name, 'snippet')

    def test_diff_name_routes_to_set_mode(self):
        preview = FakePreview(DIFF_RESULT)
        presenter = FakePresenter(preview)
        presenter.view.set_diff_toggle_visible(True)
        presenter.view.mode_switch.active_name = 'diff'
        presenter._on_mode_switch_changed(presenter.view.mode_switch, None)
        self.assertEqual(presenter.mode, 'diff')
        self.assertEqual(preview.show_diff_calls, 1)

    def test_syncing_flag_swallows_callback(self):
        presenter = FakePresenter(FakePreview(DIFF_RESULT))
        presenter._syncing_mode_switch = True
        presenter.view.mode_switch.active_name = 'snippet'
        presenter._on_mode_switch_changed(presenter.view.mode_switch, None)
        self.assertEqual(presenter.mode, 'pdf')


class DiffStateChangedTest(unittest.TestCase):

    def test_result_gone_falls_back_and_hides_toggle(self):
        preview = FakePreview(None)
        presenter = FakePresenter(preview)
        presenter.mode = 'diff'
        presenter.view.set_diff_toggle_visible(True)
        presenter.on_diff_state_changed(preview)
        self.assertFalse(presenter.view.diff_toggle_visible)
        self.assertEqual(presenter.mode, 'pdf')

    def test_view_kicked_back_keeps_result_but_mode_pdf(self):
        # 新构建把视图顶回正式 PDF：档位保留（可再切回去），模式落回 pdf。
        preview = FakePreview(DIFF_RESULT)
        preview.showing_diff_pdf = False
        presenter = FakePresenter(preview)
        presenter.mode = 'diff'
        presenter.view.set_diff_toggle_visible(True)
        presenter.on_diff_state_changed(preview)
        self.assertTrue(presenter.view.diff_toggle_visible)
        self.assertEqual(presenter.mode, 'pdf')
        self.assertEqual(presenter.view.mode_switch.active_name, 'pdf')

    def test_staying_in_diff_view_is_noop(self):
        preview = FakePreview(DIFF_RESULT)
        preview.showing_diff_pdf = True
        presenter = FakePresenter(preview)
        presenter.mode = 'diff'
        presenter.view.set_diff_toggle_visible(True)
        presenter.on_diff_state_changed(preview)
        self.assertEqual(presenter.mode, 'diff')
        self.assertEqual(preview.show_main_calls, 0)

    def test_pdf_mode_just_updates_toggle(self):
        preview = FakePreview(DIFF_RESULT)
        presenter = FakePresenter(preview)
        presenter.on_diff_state_changed(preview)
        self.assertTrue(presenter.view.diff_toggle_visible)
        self.assertEqual(presenter.mode, 'pdf')

    def test_end_button_tracks_result_presence(self):
        # 结束按钮与档位的语义不同：结果存在就给（含排版失败的），结果没了
        # 才收。
        failed = dict(DIFF_RESULT, failed=True)
        preview = FakePreview(failed)
        presenter = FakePresenter(preview)
        presenter.on_diff_state_changed(preview)
        # 失败结果：档位不亮（不可看），但结束按钮给着（可以手动清掉）。
        self.assertFalse(presenter.view.diff_toggle_visible)
        self.assertTrue(presenter.view.diff_end_visible)

        preview.diff_result = None
        presenter.on_diff_state_changed(preview)
        self.assertFalse(presenter.view.diff_end_visible)

    def test_end_button_click_clears_result_and_disarms(self):
        preview = FakePreview(DIFF_RESULT)
        presenter = FakePresenter(preview)
        presenter.view.set_diff_end_handler(presenter.on_diff_end_requested)
        presenter.mode = 'diff'
        presenter.view.set_diff_toggle_visible(True)
        preview.end_diff_review = mock.Mock(
            side_effect=lambda: setattr(preview, 'diff_result', None))
        presenter.view.click_diff_end()
        preview.end_diff_review.assert_called_once()


class ToggleViewGuardTest(unittest.TestCase):
    '''view 侧档位增删的结构守护。

    Adw.ToggleGroup 是真 widget，无显示环境构造会段错误（见项目里 GTK
    headless 的教训），所以这里只钉住两条约定，行为由上面的假对象测试覆盖：
    档位必须按 name 增删、presenter 不许再按索引路由档位。
    '''

    def test_view_adds_diff_toggle_by_name(self):
        source = (ROOT / 'setzer/workspace/preview_panel/preview_panel_viewgtk.py').read_text(
            encoding='utf-8')
        self.assertIn("name='diff'", source)
        self.assertIn("get_toggle_by_name('diff')", source)

    def test_presenter_has_no_index_routing(self):
        source = (ROOT / PRESENTER).read_text(encoding='utf-8')
        # 加档前的写法：set_active(0 if ... else 1) 与 active == 2 指 help。
        # 它们会随 diff 档增删而错位，任何回归都该在这里被拦下。
        self.assertNotIn('set_active(0 if', source)
        self.assertNotIn('active == 2', source)
        self.assertIn('set_active_name', source)
        self.assertIn('get_active_name', source)

    def test_diff_mode_hides_irrelevant_tools(self):
        '''diff 档收缩工具栏：放大镜/主题着色/外部查看器/detach 都不该在
        diff 视图里出现（空间让给改动导航器；recolor 还会涂掉 latexdiff
        的红绿语义）。'''
        source = (ROOT / PRESENTER).read_text(encoding='utf-8')
        self.assertIn('hide_for_mode = snippet_mode or diff_mode', source)
        for widget in ('external_viewer_button', 'recolor_pdf_toggle',
                       'magnifier_toggle'):
            self.assertIn('self.view.{}.set_visible(not hide_for_mode)'.format(widget),
                          source)
        self.assertIn('and not diff_mode', source)

    def test_view_has_diff_navigator(self):
        source = (ROOT / 'setzer/workspace/preview_panel/preview_panel_viewgtk.py').read_text(
            encoding='utf-8')
        self.assertIn('self.diff_nav = Gtk.Box', source)
        self.assertIn('go-previous-symbolic', source)
        self.assertIn('go-next-symbolic', source)
        self.assertIn('def set_diff_nav_state', source)


class DiffNavigatorTest(unittest.TestCase):
    '''工具栏改动导航器：显隐、计数、上下切换的路由。'''

    def test_navigator_shows_with_state_on_diff_entry(self):
        preview = FakePreview(NAV_RESULT)
        presenter = FakePresenter(preview)
        presenter.set_mode('diff')
        self.assertTrue(presenter.view.diff_nav_visible)
        # show_diff_pdf 落第一处 → 计数 1/2。
        self.assertEqual(presenter.view.diff_nav_state, (0, 2))

    def test_navigator_hidden_outside_diff_mode(self):
        preview = FakePreview(NAV_RESULT)
        presenter = FakePresenter(preview)
        presenter.on_diff_state_changed(preview)
        self.assertTrue(presenter.view.diff_toggle_visible)
        self.assertFalse(presenter.view.diff_nav_visible)

    def test_navigator_hidden_when_diff_pdf_unopenable(self):
        # diff PDF 打不开：模式落回 pdf，计数器跟着收（别给一个指不了
        # 任何东西的死按钮）。
        preview = FakePreview(NAV_RESULT)
        preview.show_diff_succeeds = False
        presenter = FakePresenter(preview)
        presenter.set_mode('diff')
        self.assertEqual(presenter.mode, 'pdf')
        self.assertFalse(presenter.view.diff_nav_visible)

    def test_prev_next_route_to_preview(self):
        preview = FakePreview(NAV_RESULT)
        presenter = FakePresenter(preview)
        presenter.set_mode('diff')
        presenter.view.click_diff_nav_next()
        presenter.view.click_diff_nav_previous()
        self.assertEqual(preview.step_calls, [1, -1])
        self.assertTrue(preview.diff_change_index in (0, 1))

    def test_position_change_resyncs_state(self):
        preview = FakePreview(NAV_RESULT)
        presenter = FakePresenter(preview)
        presenter.set_mode('diff')
        preview.diff_change_index = 1
        presenter.on_diff_position_changed(preview)
        self.assertEqual(presenter.view.diff_nav_state, (1, 2))

    def test_leaving_diff_hides_navigator(self):
        preview = FakePreview(NAV_RESULT)
        presenter = FakePresenter(preview)
        presenter.set_mode('diff')
        self.assertTrue(presenter.view.diff_nav_visible)
        presenter.set_mode('pdf')
        self.assertFalse(presenter.view.diff_nav_visible)


if __name__ == '__main__':
    unittest.main()
