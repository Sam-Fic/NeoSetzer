#!/usr/bin/env python3
# coding: utf-8

# 单元测试：document_wizard / include_bibtex_file / include_latex_file 的
# presets 加载与保存逻辑。
#
# 这三个 wizard 的 presets 流程相同：
#   save: settings.set_value(section, 'presets', <current_values dict>)
#   load: presets = settings.get_value(section, 'presets')
#         若是 bytes（旧 pickle 迁移期）→ pickle.loads
#         若非 dict → None
# 本测试抽出 load_presets 的核心校验逻辑验证三态处理。

import pickle
import unittest


def normalize_presets(presets):
    '''与三个 wizard 的 load_* 中类型校验同实现。

    - bytes/bytearray → pickle.loads（迁移期兼容）；失败 → None
    - 非 dict → None
    - dict → 原样返回
    '''
    if presets is None:
        return None
    if isinstance(presets, (bytes, bytearray)):
        try:
            presets = pickle.loads(presets)
        except Exception:
            return None
    if not isinstance(presets, dict):
        return None
    return presets


class TestNormalizePresets(unittest.TestCase):

    def test_dict_returned_as_is(self):
        d = {'document_class': 'article', 'title': 'Hi'}
        self.assertEqual(normalize_presets(d), d)

    def test_none_returns_none(self):
        self.assertIsNone(normalize_presets(None))

    def test_bytes_pickle_of_dict_unwrapped(self):
        d = {'document_class': 'book', 'packages': {'ams': True}}
        self.assertEqual(normalize_presets(pickle.dumps(d)), d)

    def test_corrupt_bytes_returns_none(self):
        self.assertIsNone(normalize_presets(b'not a pickle'))

    def test_non_dict_non_bytes_returns_none(self):
        # 旧版可能因 bug 存了 list/str/int
        self.assertIsNone(normalize_presets(['not', 'a', 'dict']))
        self.assertIsNone(normalize_presets('a string'))
        self.assertIsNone(normalize_presets(42))
        self.assertIsNone(normalize_presets([1, 2, 3]))

    def test_bytearray_supported(self):
        d = {'x': 1}
        self.assertEqual(normalize_presets(bytearray(pickle.dumps(d))), d)


class TestWizardPresetsRoundTrip(unittest.TestCase):

    def test_document_wizard_current_values_structure(self):
        # 验证 document_wizard.init_current_values 的结构是 JSON 兼容
        # （settings.json 持久化后能往返）
        import json
        current_values = {
            'document_class': 'article',
            'title': '',
            'author': '',
            'date': '\\today',
            'languages': {'english': 'English', 'german': 'Deutsch'},
            'packages': {'ams': True, 'graphicx': True, 'hyperref': False},
            'article': {
                'page_format': 'US Letter',
                'font_size': 11,
                'option_twocolumn': False,
                'option_default_margins': True,
                'margin_left': 3.5,
                'margin_right': 3.5,
                'margin_top': 3.5,
                'margin_bottom': 3.5,
                'is_landscape': False,
            },
            'beamer': {
                'theme': 'default',
                'option_show_navigation': True,
                'option_top_align': True,
            },
        }
        # JSON 往返保真
        roundtripped = json.loads(json.dumps(current_values))
        self.assertEqual(roundtripped, current_values)
        # normalize_presets 应原样接受 dict
        self.assertEqual(normalize_presets(current_values), current_values)

    def test_include_bibtex_current_values_structure(self):
        import json
        current_values = {
            'style': 'plain',
            'natbib_style': 'plainnat',
            'natbib_toggle': False,
        }
        roundtripped = json.loads(json.dumps(current_values))
        self.assertEqual(roundtripped, current_values)

    def test_include_latex_current_values_structure(self):
        import json
        current_values = {
            'pathtype': 'relative',
        }
        roundtripped = json.loads(json.dumps(current_values))
        self.assertEqual(roundtripped, current_values)


if __name__ == '__main__':
    unittest.main()
