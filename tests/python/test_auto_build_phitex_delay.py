#!/usr/bin/env python3
# coding: utf-8
#
# 自动构建延迟：PhiTeX 用短延迟，且不能被任何调用处的地板夹回去。
#
# 上一版常数 (0.5s) 与调用处的 max(..., 500) 正好相等、叠在一起，改小常数
# 会被静默夹回 500ms——不报错、不报错、也不报错。这组测试把"延迟只算一处"
# 钉住。实测依据：写完文件到增量编译完 + PDF 落盘 15–19ms，整条约 70–90ms。

import ast
import importlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUTO_BUILD = 'setzer/workspace/auto_build/auto_build.py'


def load_method(relative_path, class_name, method_name, namespace):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


class FakeSettings:
    def __init__(self, values):
        self.values = values

    def get_value(self, section, key):
        return self.values[key]


class Doc:
    # DocumentSettings.get_document_override 会读 filename。
    filename = '/p/main.tex'

    def __init__(self, live):
        self.build_system = Doc._BS(live)

    class _BS:
        def __init__(self, live):
            self._live = live

        def is_phitex_live_build(self, text=None):
            return self._live


class Holder:
    PHITEX_DELAY_MS = 150
    MIN_DELAY_MS = 500


class DelayTest(unittest.TestCase):

    def setUp(self):
        namespace = {'DocumentSettings': importlib.import_module(
            'setzer.settings.document_settings').DocumentSettings}
        self.values = load_method(AUTO_BUILD, 'AutoBuild', '_auto_build_values', namespace)

    def call(self, live, settings_values):
        obj = Holder()
        obj.settings = FakeSettings(settings_values)
        return self.values(obj, Doc(live))

    def test_phitex_gets_the_short_delay(self):
        enabled, delay_ms = self.call(True, {'auto_build': False, 'auto_build_delay': 2})
        self.assertTrue(enabled)          # 强制开启，不看用户设置
        self.assertEqual(delay_ms, 150)

    def test_other_engines_keep_user_delay_with_the_old_floor(self):
        enabled, delay_ms = self.call(False, {'auto_build': True, 'auto_build_delay': 2})
        self.assertTrue(enabled)
        self.assertEqual(delay_ms, 2000)

    def test_floor_still_applies_to_other_engines_only(self):
        # 用户填 0.2s：传统引擎夹回 500，PhiTeX 用自己的 150。
        self.assertEqual(self.call(False, {'auto_build': True, 'auto_build_delay': 0.2})[1], 500)
        self.assertEqual(self.call(True, {'auto_build': False, 'auto_build_delay': 0.2})[1], 150)

    def test_no_caller_reapplies_a_floor(self):
        '''调用处不得再 max(..., 500)，否则短延迟会被静默夹回。'''
        source = (ROOT / AUTO_BUILD).read_text(encoding='utf-8')
        offenders = [line for line in source.splitlines()
                     if 'max(int(delay * 1000), 500)' in line]
        self.assertEqual(offenders, [])
        # 延迟只在 _auto_build_values 里算一次，两个调用点直接用毫秒值。
        self.assertEqual(source.count('schedule_build(document, delay_ms)'), 2)


if __name__ == '__main__':
    unittest.main()
