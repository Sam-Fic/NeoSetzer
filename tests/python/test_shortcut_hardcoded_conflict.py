#!/usr/bin/env python3
# coding: utf-8
#
# 快捷键偏好页的「固定键」冲突守卫。
#
# Ctrl+L（跳转行）、Ctrl+Y（重做别名）与 Insert（覆写切换）不在
# keyboard_shortcuts 设置里、无法重绑定，常规的 settings 冲突检测看不到它们。
# 守卫缺失时用户可以把可配置键绑到这些键上，改了却不生效且无任何警告。
# F3 / Shift+F3 不在此列：它们已作为 win.find-next / win.find-previous 的
# application accel 别名注册，find_conflicting_action 扫 app.get_accels_for_action
# 时天然可见（由 AliasAccelTest 钉住该覆盖）。这里钉住三件事：
#   1. 固定键表本身：每个固定键都要能查到占用标签；
#   2. 可配置键不在表里（否则正常改绑会被误拦）；
#   3. on_key_pressed 里守卫先于 settings 冲突检测执行、且放行「重申
#      自己当前绑定」的情形（源码扫描断言，与 F4 类测试同风格）。

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'setzer/dialogs/preferences/pages/page_shortcuts.py'


def load_method(relative_path, class_name, method_names, namespace):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    wanted = [node for node in cls.body if isinstance(node, ast.FunctionDef)
              and node.name in method_names]
    # 无显示测试环境没有 setzer.in 注入的 builtins._：注入恒等替换，
    # 被测方法里的 _() 返回英文原文，与下方断言的原文一致。
    namespace['_'] = lambda s: s
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(SOURCE), 'exec'),
         namespace)
    return SimpleNamespace(**{node.name: namespace[node.name] for node in wanted})


class HardcodedConflictTableTest(unittest.TestCase):
    '''固定键表：占用可查、可配置键不误拦。'''

    def setUp(self):
        # find_hardcoded_conflict 不碰 gi：msg 表在方法体内构建，可无显示执行。
        self.fns = load_method(
            'setzer/dialogs/preferences/pages/page_shortcuts.py',
            'PageShortcuts', {'find_hardcoded_conflict'}, {})

    def test_each_hardcoded_key_reports_its_owner(self):
        # 期望值是英文原文（命名空间里的 _ 为恒等替换）。
        cases = {
            '<Control>l': 'Go to line',
            '<Control>y': 'Redo',
            'Insert': 'Toggle insert / overwrite',
        }
        for shortcut, label in cases.items():
            self.assertEqual(self.fns.find_hardcoded_conflict(SimpleNamespace(), shortcut), label,
                             'fixed key {} must be reported'.format(shortcut))

    def test_f3_aliases_are_not_hardcoded(self):
        # F3 / Shift+F3 是 win.find-next/-previous 的 accel 别名，冲突走
        # find_conflicting_action 的 get_accels_for_action 扫描；留在固定键
        # 表里会以错误的来源（'Find' 标签）拦截本应走「换键」对话框的改绑。
        for shortcut in ('F3', '<Shift>F3'):
            self.assertIsNone(self.fns.find_hardcoded_conflict(SimpleNamespace(), shortcut),
                              'alias key {} belongs to accel scanning'.format(shortcut))

    def test_configurable_keys_are_not_intercepted(self):
        # 这些键都在 keyboard_shortcuts 设置里（settings 冲突检测的职责范围），
        # 落进固定键表就会把正常改绑误判成冲突。
        for shortcut in ('<Control>a', '<Control>s', '<Control>d',
                         '<Control><Shift>l', '<Alt>Up', 'F2', 'Escape'):
            self.assertIsNone(self.fns.find_hardcoded_conflict(SimpleNamespace(), shortcut),
                              'configurable key {} must not be treated as '
                              'hardcoded'.format(shortcut))

    def test_case_matches_settings_storage_format(self):
        # settings 存的是 accelerator_name 规范串：字母小写、功能键原样。
        # 表里若写成 '<Control>L' 会永远匹配不到真实事件串。
        for shortcut in ('<Control>l', '<Control>y'):
            self.assertIsNotNone(self.fns.find_hardcoded_conflict(SimpleNamespace(), shortcut))
            self.assertIsNone(self.fns.find_hardcoded_conflict(
                SimpleNamespace(), shortcut.replace('l', 'L').replace('y', 'Y')))


class GuardOrderingTest(unittest.TestCase):
    '''on_key_pressed 里的执行顺序（源码扫描断言）。'''

    def test_guard_runs_before_settings_conflict_check(self):
        source = SOURCE.read_text(encoding='utf-8')
        hardcoded_pos = source.find('find_hardcoded_conflict(shortcut)')
        conflict_pos = source.find('find_conflicting_action(self.current_action_name, shortcut)')
        self.assertGreater(hardcoded_pos, 0)
        self.assertGreater(conflict_pos, hardcoded_pos,
                           '固定键守卫必须先于 settings 冲突检测')

    def test_own_current_binding_is_exempt(self):
        source = SOURCE.read_text(encoding='utf-8')
        # 「重申当前绑定」的放行判断必须出现在固定键守卫之前，否则用户
        # 确认原键也会被弹窗拦截。
        exempt_pos = source.find('shortcuts.get(self.current_action_name)')
        guard_pos = source.find('find_hardcoded_conflict(shortcut)')
        self.assertGreater(exempt_pos, 0)
        self.assertGreater(guard_pos, exempt_pos)

    def test_hardcoded_dialog_does_not_write_settings(self):
        # 固定键冲突对话框只提示换键，不得走 set_shortcut / _pending_assignment。
        source = SOURCE.read_text(encoding='utf-8')
        method_start = source.find('def _show_hardcoded_conflict_dialog')
        method_end = source.find('def find_conflicting_action')
        body = source[method_start:method_end]
        self.assertNotIn('set_shortcut', body)
        self.assertNotIn('_pending_assignment', body)


class AliasAccelTest(unittest.TestCase):
    '''F3 / Ctrl+L 等固定别名必须挂在 application accel 数组上。

    find_conflicting_action 只迭代 keyboard_shortcuts 里的键：别名必须与
    其主绑定同属一个动作（get_accels_for_action 返回含别名的数组），
    否则用户把 F3 绑到别处不会被拦。'''

    def test_find_aliases_live_in_accel_arrays(self):
        from setzer.keyboard_shortcuts.shortcut_accels import (
            ACTION_ALIASES, FIXED_ACCELS, SETTINGS_KEY_TO_ACTION)
        self.assertEqual(ACTION_ALIASES.get('win.find-next'), ['F3'])
        self.assertEqual(ACTION_ALIASES.get('win.find-previous'), ['<Shift>F3'])
        # 别名所属动作必须是同一 settings 键映射的目标，扫描才会连带看到别名。
        self.assertEqual(SETTINGS_KEY_TO_ACTION['find_next'], 'win.find-next')
        self.assertEqual(SETTINGS_KEY_TO_ACTION['find_previous'], 'win.find-previous')
        # Ctrl+L 没有 settings 键（go-to-line 不在偏好页），必须继续留在
        # 固定键表里兜底——因此它不能被移出 find_hardcoded_conflict。
        self.assertEqual(FIXED_ACCELS.get('win.go-to-line'), ['<Control>l'])
        self.assertNotIn('go_to_line', SETTINGS_KEY_TO_ACTION)

    def test_conflict_scan_covers_every_mapped_settings_key(self):
        # 映射表的键必须真实存在于默认键位表里，否则扫描迭代会整体错位。
        import setzer.settings.settings as settings_module
        source = Path(settings_module.__file__).read_text(encoding='utf-8')
        from setzer.keyboard_shortcuts.shortcut_accels import SETTINGS_KEY_TO_ACTION
        for settings_key in SETTINGS_KEY_TO_ACTION:
            self.assertIn("keyboard_shortcuts']['{}']".format(settings_key), source,
                          'settings key {} missing from defaults'.format(settings_key))


if __name__ == '__main__':
    unittest.main()
