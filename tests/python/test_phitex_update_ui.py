#!/usr/bin/env python3
# coding: utf-8
#
# PhiTeX 版本与更新入口。
#
# 两条最容易静默失效的地方：
#   - `phitex upgrade` 在非终端环境（我们这里 stdout 是管道）什么都不问、
#     也什么都不装。不带 -y 就会"看起来升级成功而二进制没变"。
#   - 回填开关状态时若不断开信号回调，打开偏好页就等于替用户写了一次配置。

import ast
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SESSION = 'setzer/document/build_system/phitex_session/phitex_session.py'
PAGE = 'setzer/dialogs/preferences/pages/page_build_system.py'


def load_method(relative_path, class_name, method_name, namespace):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


def service_locator_class():
    '''真类：测试用 mock.patch.object 打它，被 exec 的函数必须看到同一个对象。'''
    import setzer.app.service_locator as SL
    return SL.ServiceLocator


class CommandsTest(unittest.TestCase):

    def setUp(self):
        self.calls = []
        self.proc = mock.Mock()
        self.proc.communicate.return_value = ('phitex 1.0.2\nCopyright X\n', '')
        self.proc.returncode = 0

        def fake_popen(args, **kwargs):
            self.calls.append(list(args))
            return self.proc

        # run() 的默认参数 timeout=_CALL_TIMEOUT_S 在 def 求值时就要能从全局
        # 解析到，否则 exec 阶段就 NameError。
        ns = {'shutil': mock.Mock(which=mock.Mock(return_value='/usr/bin/phitex')),
              'subprocess': mock.Mock(Popen=fake_popen),
              'sys': mock.Mock(platform='linux'),
              '_CALL_TIMEOUT_S': 180}
        # AST 抽取保留了 @staticmethod / @classmethod 装饰器，load_method 返回的
        # 已经是描述符本身——原样放进类即可，再包一层会得到不可调用的对象。
        Fake = type('Fake', (), {
            name: load_method(SESSION, 'PhitexSession', name, ns)
            for name in ('run', 'upgrade', 'installed_version',
                         'update_check_enabled', 'set_update_check')
        })
        self.Fake = Fake

    def test_upgrade_passes_no_confirm(self):
        '''少了 -y，非终端下 PhiTeX 什么都不装，界面却会报成功。'''
        self.Fake.upgrade()
        command = self.calls[-1]
        self.assertEqual(command[0], '/usr/bin/phitex')
        self.assertIn('upgrade', command)
        self.assertIn('-y', command)

    def test_version_probe_is_local(self):
        self.Fake.installed_version()
        self.assertEqual(self.calls[-1], ['/usr/bin/phitex', '--version'])

    def test_release_number_is_the_second_token(self):
        self.assertEqual(self.Fake.installed_version(), '1.0.2')

    def test_unrelated_first_line_yields_none(self):
        self.proc.communicate.return_value = ('some other tool 9.9\n', '')
        self.assertIsNone(self.Fake.installed_version())

    def test_update_check_written_through_phitex(self):
        '''不自己改它的 toml：键写法与优先级是 PhiTeX 的职责。'''
        self.Fake.set_update_check(True)
        self.assertEqual(self.calls[-1],
                         ['/usr/bin/phitex', 'config', 'update-check', 'on'])
        self.Fake.set_update_check(False)
        self.assertEqual(self.calls[-1],
                         ['/usr/bin/phitex', 'config', 'update-check', 'off'])

    def test_update_check_parses_both_spellings(self):
        for text, expected in (('  update-check  true (yours)\n', True),
                               ('  update-check  on (yours)\n', True),
                               ('  update-check  false (yours)\n', False),
                               ('  update-check  off (default)\n', False)):
            self.proc.communicate.return_value = (text, '')
            self.assertEqual(self.Fake.update_check_enabled(), expected, text)

    def test_missing_binary_is_not_reported_as_installed(self):
        self.proc.returncode = 1
        self.assertIsNone(self.Fake.installed_version())


class PageStateTest(unittest.TestCase):

    def setUp(self):
        self.threading = mock.Mock()
        ns = {'GLib': mock.Mock(idle_add=lambda fn, *a: fn(*a) or False),
              'threading': self.threading,
              # exec 出来的函数体里有 _() 调用，测试环境没装 gettext。
              '_': lambda text: text,
              'ServiceLocator': service_locator_class()}
        self.apply = load_method(PAGE, 'PageBuildSystem', '_apply_phitex_state', ns)
        self.end = load_method(PAGE, 'PageBuildSystem', '_end_phitex_sessions', ns)
        toast_ns = dict(ns, Adw=mock.Mock())
        self.toast = load_method(PAGE, 'PageBuildSystem', '_show_toast', toast_ns)

    def page(self):
        obj = mock.Mock()
        obj.on_phitex_update_check_toggled = mock.Mock()
        return obj

    def test_toast_stays_in_the_preferences_window(self):
        '''设置窗口盖在主窗口之上，toast 发到主窗口就会出现在它背后。'''
        obj = self.page()
        self.toast(obj, 'PhiTeX upgraded.')
        obj.preferences.view.add_toast.assert_called_once()
        obj.main_window.toast_overlay.add_toast.assert_not_called()

    def test_toast_falls_back_when_no_preferences_window(self):
        obj = self.page()
        obj.preferences.view = None
        self.toast(obj, 'x')
        obj.main_window.toast_overlay.add_toast.assert_called_once()

    def test_backfill_does_not_write_the_config(self):
        '''回填必须先断开信号，否则打开偏好页就替用户改了设置。'''
        obj = self.page()
        self.apply(obj, '1.0.2', True)
        obj.view.phitex_update_check_switch.handler_block_by_func.assert_called_once_with(
            obj.on_phitex_update_check_toggled)
        obj.view.phitex_update_check_switch.set_active.assert_called_once_with(True)
        obj.view.phitex_update_check_switch.handler_unblock_by_func.assert_called_once()
        obj.view.phitex_version_row.set_subtitle.assert_called_once_with('1.0.2')

    def test_missing_binary_disables_the_controls(self):
        obj = self.page()
        self.apply(obj, None, False)
        obj.view.phitex_upgrade_row.set_sensitive.assert_called_with(False)
        obj.view.phitex_update_check_switch.set_sensitive.assert_called_with(False)

    def test_upgrade_ends_each_running_session_off_the_main_thread(self):
        '''旧 session 跑的是升级前的二进制，不结束它新版本要等重启才生效。'''
        obj = self.page()
        builder = mock.Mock()
        builder.phitex_session = mock.Mock()
        document = mock.Mock()
        document.build_system.builders.get.return_value = builder
        workspace = mock.Mock()
        workspace.open_documents = [document]
        import setzer.app.service_locator as SL
        with mock.patch.object(SL.ServiceLocator, 'get_workspace',
                               staticmethod(lambda: workspace)):
            result = self.end(obj)
        self.assertFalse(result)
        obj.view.group_phitex.set_description.assert_called_with(
            obj.view.phitex_restart_note)
        # 断言 exec 命名空间里那个 threading（函数体真正用的那个）。
        self.threading.Thread.assert_called_once()
        self.threading.Thread.return_value.start.assert_called_once()

    def test_documents_without_a_session_are_skipped(self):
        obj = self.page()
        document = mock.Mock()
        document.build_system.builders.get.return_value = mock.Mock(phitex_session=None)
        workspace = mock.Mock()
        workspace.open_documents = [document]
        import setzer.app.service_locator as SL
        with mock.patch.object(SL.ServiceLocator, 'get_workspace',
                               staticmethod(lambda: workspace)):
            self.end(obj)
        self.threading.Thread.assert_not_called()


class NoNetworkFromTheAppTest(unittest.TestCase):

    def test_the_app_never_talks_to_the_network_itself(self):
        '''联网的只能是用户自己装的那个二进制。'''
        for path in (SESSION, PAGE):
            source = (ROOT / path).read_text(encoding='utf-8')
            for forbidden in ('urllib', 'requests', 'http.client',
                              'api.github.com'):
                self.assertNotIn(forbidden, source, path)


if __name__ == '__main__':
    unittest.main()
