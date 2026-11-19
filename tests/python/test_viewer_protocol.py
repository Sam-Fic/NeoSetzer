#!/usr/bin/env python3
# coding: utf-8
#
# PhiTeX viewer WS 协议的纯逻辑：endpoint 换算、页 hash 表比对、origins
# base64 小端数组解码、最近字形反查、字节偏移→行号、where 输出解析。
#
# 全部 gi-free，CI 无显示直接跑。协议形状的权威是 PhiTeX 源码
# （crates/editor/live/src/view.rs 的 origins_json、crates/cli/viewer/
# viewer.js 的 glyphs2/nearest）；这里用"服务端会产出的字节"构造样例。

import base64
import struct
import unittest

from setzer.helpers import viewer_protocol


def _enc32(values):
    return base64.b64encode(
        b''.join(struct.pack('<I', value) for value in values)).decode()


def _encf(values):
    return base64.b64encode(
        b''.join(struct.pack('<f', value) for value in values)).decode()


def _payload(glyphs, files, files_from=0):
    '''按 view.rs origins_json 的形状构造应答。glyphs 每项
    (file, start, end, synth, x, y)。'''
    synth_words = [0] * ((len(glyphs) + 31) // 32)
    for index, glyph in enumerate(glyphs):
        if glyph[3]:
            synth_words[index >> 5] |= 1 << (index & 31)
    return {
        'n': len(glyphs),
        'files': list(files),
        'files_from': files_from,
        'file': _enc32([glyph[0] for glyph in glyphs]),
        'start': _enc32([glyph[1] for glyph in glyphs]),
        'end': _enc32([glyph[2] for glyph in glyphs]),
        'synth': _enc32(synth_words),
        'x': _encf([glyph[4] for glyph in glyphs]),
        'y': _encf([glyph[5] for glyph in glyphs]),
    }


class ViewerEndpointTest(unittest.TestCase):

    def test_http_to_ws(self):
        self.assertEqual(
            viewer_protocol.viewer_endpoint('http://127.0.0.1:21170/tok/'),
            'ws://127.0.0.1:21170/tok/ws')

    def test_https_to_wss(self):
        self.assertEqual(
            viewer_protocol.viewer_endpoint('https://host/tok/'),
            'wss://host/tok/ws')

    def test_unusable(self):
        self.assertIsNone(viewer_protocol.viewer_endpoint(None))
        self.assertIsNone(viewer_protocol.viewer_endpoint(''))
        self.assertIsNone(viewer_protocol.viewer_endpoint('ftp://x/tok/'))

    def test_parse_start_output(self):
        line = ('started pid 665701 · viewer http://127.0.0.1:21170/'
                'aac5b4e983f85fbc3252c27298939dcd/ · log /tmp/x/session.log')
        self.assertEqual(
            viewer_protocol.parse_viewer_url(line),
            'http://127.0.0.1:21170/aac5b4e983f85fbc3252c27298939dcd/')

    def test_parse_running_output(self):
        line = ('running pid 665701 · viewer http://127.0.0.1:21170/tok/ '
                '· log /tmp/x/session.log')
        self.assertEqual(viewer_protocol.parse_viewer_url(line),
                         'http://127.0.0.1:21170/tok/')

    def test_parse_missing(self):
        self.assertIsNone(viewer_protocol.parse_viewer_url('no url here'))
        self.assertIsNone(viewer_protocol.parse_viewer_url(None))


class PageHashesTest(unittest.TestCase):

    def test_response_shape(self):
        self.assertEqual(
            viewer_protocol.page_hashes_from_response(
                {'pages': ['d3ab361aa71c2c38']}),
            ['d3ab361aa71c2c38'])
        self.assertIsNone(
            viewer_protocol.page_hashes_from_response({'pages': 3}))
        self.assertIsNone(
            viewer_protocol.page_hashes_from_response({'pages': [1, 2]}))
        self.assertIsNone(viewer_protocol.page_hashes_from_response(None))

    def test_changed_pages(self):
        old = ['a', 'b', 'c']
        new = ['a', 'B', 'c', 'd']
        self.assertEqual(viewer_protocol.changed_pages(old, new), {1, 3})
        self.assertEqual(viewer_protocol.changed_pages(['a'], ['a']), set())
        self.assertIsNone(viewer_protocol.changed_pages(None, ['a']))
        self.assertIsNone(viewer_protocol.changed_pages(['a'], None))

    def test_changed_pages_shrinks(self):
        self.assertEqual(viewer_protocol.changed_pages(['a', 'b'], ['a']),
                         {1})


class OriginstDecodeTest(unittest.TestCase):

    def test_decodes_words_and_synth_bits(self):
        payload = _payload(
            [(0, 750, 755, False, 133.8, 339.2),
             (0xFFFFFFFF, 0, 0, True, 300.0, 100.0)],
            ['main.tex', 'sec.tex'])
        files, glyphs = viewer_protocol.decode_origins(payload, [])
        self.assertEqual(files, ['main.tex', 'sec.tex'])
        self.assertEqual(len(glyphs), 2)
        first = glyphs[0]
        self.assertEqual(first['file'], 0)
        self.assertEqual(first['start'], 750)
        self.assertEqual(first['end'], 755)
        self.assertFalse(first['synth'])
        self.assertAlmostEqual(first['x'], 133.8, places=3)
        self.assertAlmostEqual(first['y'], 339.2, places=3)
        self.assertIsNone(glyphs[1]['file'])
        self.assertTrue(glyphs[1]['synth'])

    def test_synth_words_pack_multiple(self):
        glyphs = [(0, i, i + 1, (i % 2 == 1), float(i), 1.0)
                  for i in range(40)]
        payload = _payload(glyphs, ['main.tex'])
        _files, decoded = viewer_protocol.decode_origins(payload, [])
        self.assertEqual([int(g['synth']) for g in decoded],
                         [int(g[3]) for g in glyphs])

    def test_incremental_files_table(self):
        base = _payload([(0, 1, 2, False, 1.0, 1.0)], ['main.tex', 'sec.tex'])
        files, _glyphs = viewer_protocol.decode_origins(base, [])
        more = _payload([(2, 5, 6, False, 1.0, 1.0)], ['other.tex'],
                        files_from=2)
        files, glyphs = viewer_protocol.decode_origins(more, files)
        self.assertEqual(files, ['main.tex', 'sec.tex', 'other.tex'])
        self.assertEqual(glyphs[0]['file'], 2)

    def test_pending_and_malformed(self):
        self.assertIsNone(
            viewer_protocol.decode_origins({'pending': True}, [])[1])
        self.assertIsNone(viewer_protocol.decode_origins({}, [])[1])
        self.assertIsNone(viewer_protocol.decode_origins(None, [])[1])
        # 数组长度不足（协议漂移）：按拿不到处理。
        bad = _payload([(0, 1, 2, False, 1.0, 1.0)], ['main.tex'])
        bad['x'] = base64.b64encode(b'\x00\x00').decode()
        self.assertIsNone(viewer_protocol.decode_origins(bad, [])[1])

    def test_zero_glyph_page(self):
        payload = _payload([], ['main.tex'])
        _files, glyphs = viewer_protocol.decode_origins(payload, [])
        self.assertEqual(glyphs, [])


class NearestGlyphTest(unittest.TestCase):

    def test_picks_nearest(self):
        glyphs = [
            {'file': 0, 'start': 1, 'end': 2, 'synth': False, 'x': 100.0, 'y': 100.0},
            {'file': 0, 'start': 3, 'end': 4, 'synth': False, 'x': 200.0, 'y': 100.0},
        ]
        # 字形原点相对点击的经验偏移：dx = x+2.5-click_x, dy = y-3-click_y。
        found = viewer_protocol.nearest_glyph(glyphs, 102.5, 97.0)
        self.assertEqual(found['start'], 1)
        found = viewer_protocol.nearest_glyph(glyphs, 202.5, 97.0)
        self.assertEqual(found['start'], 3)

    def test_skips_sourceless(self):
        glyphs = [
            {'file': None, 'start': 0, 'end': 0, 'synth': False, 'x': 100.0, 'y': 100.0},
            {'file': 0, 'start': 9, 'end': 10, 'synth': False, 'x': 140.0, 'y': 100.0},
        ]
        found = viewer_protocol.nearest_glyph(glyphs, 100.0, 97.0)
        self.assertIsNotNone(found)
        self.assertEqual(found['start'], 9)

    def test_all_sourceless(self):
        glyphs = [
            {'file': None, 'start': 0, 'end': 0, 'synth': True, 'x': 1.0, 'y': 1.0},
        ]
        self.assertIsNone(viewer_protocol.nearest_glyph(glyphs, 0.0, 0.0))
        self.assertIsNone(viewer_protocol.nearest_glyph([], 0.0, 0.0))
        self.assertIsNone(viewer_protocol.nearest_glyph(None, 0.0, 0.0))

    def test_vertical_weight(self):
        # dy 权重 9×：同一行相邻字优先于下一行更近的字。
        glyphs = [
            {'file': 0, 'start': 1, 'end': 2, 'synth': False, 'x': 100.0, 'y': 100.0},
            {'file': 0, 'start': 2, 'end': 3, 'synth': False, 'x': 130.0, 'y': 112.0},
        ]
        found = viewer_protocol.nearest_glyph(glyphs, 118.0, 97.0)
        self.assertEqual(found['start'], 1)


class OriginToPositionTest(unittest.TestCase):

    def test_byte_offset_to_line(self):
        text = 'intro\nHello world\n'
        offset = text.encode('utf-8').index(b'world')
        glyph = {'file': 0, 'start': offset, 'end': offset + 5,
                 'synth': False, 'x': 0.0, 'y': 0.0}
        position = viewer_protocol.origin_to_position(
            glyph, ['main.tex'], '/proj', {'main.tex': text})
        self.assertEqual(position,
                         {'filename': '/proj/main.tex',
                          'name': 'main.tex', 'line': 2})

    def test_multibyte_prefix(self):
        # 非 ASCII 前缀按字节计：é 两字节，行号不能因此偏。
        text = 'héllo\nsecond\n'
        offset = text.encode('utf-8').index(b'second')
        glyph = {'file': 0, 'start': offset, 'end': offset, 'synth': False,
                 'x': 0.0, 'y': 0.0}
        position = viewer_protocol.origin_to_position(
            glyph, ['main.tex'], '/p', {'main.tex': text})
        self.assertEqual(position['line'], 2)

    def test_dot_slash_prefix(self):
        glyph = {'file': 0, 'start': 0, 'end': 0, 'synth': False,
                 'x': 0.0, 'y': 0.0}
        position = viewer_protocol.origin_to_position(
            glyph, ['./sec.tex'], '/proj', {'sec.tex': 'a\nb\n'})
        self.assertEqual(position['filename'], '/proj/sec.tex')
        self.assertEqual(position['line'], 1)

    def test_absolute_name(self):
        glyph = {'file': 0, 'start': 0, 'end': 0, 'synth': False,
                 'x': 0.0, 'y': 0.0}
        position = viewer_protocol.origin_to_position(
            glyph, ['/abs/x.tex'], '/proj', {'/abs/x.tex': 'only\n'})
        self.assertEqual(position['filename'], '/abs/x.tex')

    def test_texts_callable(self):
        glyph = {'file': 0, 'start': 0, 'end': 0, 'synth': False,
                 'x': 0.0, 'y': 0.0}
        position = viewer_protocol.origin_to_position(
            glyph, ['main.tex'], '/p', lambda name: 'a\nb\nc\n'
                     if name == 'main.tex' else None)
        self.assertEqual(position['line'], 1)

    def test_missing_text(self):
        glyph = {'file': 0, 'start': 0, 'end': 0, 'synth': False,
                 'x': 0.0, 'y': 0.0}
        self.assertIsNone(viewer_protocol.origin_to_position(
            glyph, ['x.tex'], '/p', {}))
        self.assertIsNone(viewer_protocol.origin_to_position(
            None, ['x.tex'], '/p', {}))

    def test_file_index_out_of_range(self):
        glyph = {'file': 5, 'start': 0, 'end': 0, 'synth': False,
                 'x': 0.0, 'y': 0.0}
        self.assertIsNone(viewer_protocol.origin_to_position(
            glyph, ['x.tex'], '/p', {'x.tex': 'a'}))


class WhereJsonTest(unittest.TestCase):

    WHERE_JSON = ('{"approximate":false,"bbox":[131.05,315.95,480.2,362.53],'
                  '"build":2,"number":null,"page":1,'
                  '"pdf":"/tmp/.phitex/png/builds/build-2.pdf",'
                  '"printed_page":null,"target":"sec:test"}')

    def test_parse_and_rectangle(self):
        payload = viewer_protocol.parse_where_json(
            'noise line\n' + self.WHERE_JSON + '\n')
        self.assertEqual(payload['target'], 'sec:test')
        rectangle = viewer_protocol.where_to_rectangle(payload)
        self.assertEqual(rectangle['page'], 1)
        self.assertAlmostEqual(rectangle['h'], 131.05)
        self.assertAlmostEqual(rectangle['v'], 362.53)
        self.assertAlmostEqual(rectangle['width'], 480.2 - 131.05)
        self.assertAlmostEqual(rectangle['height'], 362.53 - 315.95)

    def test_missing_label(self):
        self.assertIsNone(viewer_protocol.parse_where_json(
            'error: no such label'))
        self.assertIsNone(viewer_protocol.parse_where_json(None))

    def test_rectangle_requires_valid_page_and_bbox(self):
        self.assertIsNone(viewer_protocol.where_to_rectangle(None))
        self.assertIsNone(viewer_protocol.where_to_rectangle(
            {'page': 0, 'bbox': [0, 0, 1, 1]}))
        self.assertIsNone(viewer_protocol.where_to_rectangle(
            {'page': 1, 'bbox': [1, 1, 0, 0]}))
        self.assertIsNone(viewer_protocol.where_to_rectangle(
            {'page': 1, 'bbox': None}))


class TextureStillValidTest(unittest.TestCase):
    '''resurl 缓存判定（preview_page_renderer 的纯函数）。'''

    def setUp(self):
        from pathlib import Path
        # preview_page_renderer 顶层 import gi：直接从源码 AST 抽纯函数，
        # 与 test_preview_diff_mode 的 load_method 同一范式。
        import ast
        root = Path(__file__).resolve().parents[2]
        source = (root / 'setzer/document/preview/preview_page_renderer.py'
                  ).read_text(encoding='utf-8')
        tree = ast.parse(source)
        namespace = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and \
                    node.name == 'page_texture_still_valid':
                exec(compile(ast.Module(body=[node], type_ignores=[]),
                             'preview_page_renderer.py', 'exec'), namespace)
        self.fn = namespace['page_texture_still_valid']

    def test_no_texture(self):
        self.assertFalse(self.fn(None, 0, 3, 595, 842, ['h']))

    def test_size_mismatch(self):
        item = [object(), 595, 842, 3, None, 'h']
        self.assertFalse(self.fn(item, 0, 3, 596, 842, ['h']))

    def test_same_version(self):
        item = [object(), 595, 842, 3, None, None]
        self.assertTrue(self.fn(item, 0, 3, 595, 842, None))

    def test_version_changed_hash_same(self):
        item = [object(), 595, 842, 3, None, 'aaa']
        self.assertTrue(self.fn(item, 0, 4, 595, 842, ['aaa']))

    def test_version_changed_hash_differs(self):
        item = [object(), 595, 842, 3, None, 'aaa']
        self.assertFalse(self.fn(item, 0, 4, 595, 842, ['bbb']))

    def test_version_changed_no_table(self):
        item = [object(), 595, 842, 3, None, 'aaa']
        self.assertFalse(self.fn(item, 0, 4, 595, 842, None))

    def test_version_changed_no_label(self):
        # 旧条目（5 元素）/标签 None：判不出内容相同 → 重渲。
        self.assertFalse(self.fn([object(), 595, 842, 3, None], 0, 4, 595, 842, ['aaa']))
        self.assertFalse(self.fn([object(), 595, 842, 3, None, None], 0, 4,
                                 595, 842, ['aaa']))

    def test_page_beyond_table(self):
        item = [object(), 595, 842, 3, None, 'aaa']
        self.assertFalse(self.fn(item, 5, 4, 595, 842, ['aaa']))


if __name__ == '__main__':
    unittest.main()
