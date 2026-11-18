#!/usr/bin/env python3
# coding: utf-8
#
# 影子 SyncTeX（phitex 呈现 + 原生引擎后台精确定位）的关键契约。
#
# 这条旁路的安全边界全是"宁可降级也不装错数据"：
#   - 指纹覆盖主 tex + 递归 include + bib，源码一改指纹就变；
#   - 引擎判定链：config 显式 > session.log > magic comment > fontspec > pdflatex；
#   - 防抖合并连续脏标记，stop 后在途结果靠代数计数丢弃；
#   - 安装前布局校验 + 指纹复核，任一不过即放弃本轮；
#   - sidecar 三态（shadow 新鲜 / shadow 陈旧 / phitex）驱动门控与修复链。
# build_system / builder 的挂载方法走 ast 提取 + exec 隔离（与
# test_phitex_error_resilience.py 同范式）；shadow_synctex 直接 import
# （开发机/CI 都有 GTK 环境，与 test_phitex_session 同），真实 gi 先加载
# 后 conftest_stub.install() 自动让位；GLib 在 setUp 里换成确定性桩。

import ast
import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BUILD_SYSTEM = 'setzer/document/build_system/build_system.py'
BUILDER = 'setzer/document/build_system/builder/builder_build_latex.py'

import setzer.document.build_system.shadow_synctex.shadow_synctex as ss
from setzer.helpers import synctex_query
from setzer.helpers.synctex_folder import synctex_folder

from tests.python import conftest_stub


def load_methods(relative_path, class_name, method_names, namespace):
    '''把类里的若干方法抽出来 exec 到隔离 namespace，返回 SimpleNamespace。'''
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    wanted = [node for node in cls.body if isinstance(node, ast.FunctionDef)
              and node.name in method_names]
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(source), 'exec'),
         namespace)
    return SimpleNamespace(**{node.name: namespace[node.name] for node in wanted})


def make_glib():
    '''独立 GLib 桩：timeout 登记表 + idle_add 记录，不执行回调。'''
    glib = conftest_stub.make_glib_stub()
    glib.idle_calls = []
    glib.idle_add = lambda fn, *args: glib.idle_calls.append((fn, args))
    return glib


def write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def minimal_pdf(path, n_pages=1, width=612, height=792):
    '''Poppler 可加载的最小 PDF（实测无需合法 xref，trailer 即可）。'''
    kids = ' '.join('{} 0 R'.format(3 + i) for i in range(n_pages))
    objects = ['1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj',
               '2 0 obj << /Type /Pages /Kids [{}] /Count {} >> endobj'.format(kids, n_pages)]
    for i in range(n_pages):
        objects.append('{} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 {} {}] >> endobj'.format(
            3 + i, width, height))
    data = '%PDF-1.4\n' + '\n'.join(objects) + '\ntrailer << /Root 1 0 R >>\n%%EOF\n'
    with open(path, 'wb') as f:
        f.write(data.encode('ascii'))


class FingerprintTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.main = os.path.join(self.dir, 'main.tex')
        self.chapter = os.path.join(self.dir, 'chapter.tex')
        self.refs = os.path.join(self.dir, 'refs.bib')
        write(self.main, '\\documentclass{article}\n\\input{chapter}\n\\bibliography{refs}\n')
        write(self.chapter, 'hello\n')
        write(self.refs, '@book{a, title={A}}\n')

    def fingerprint(self):
        return ss.source_fingerprint(self.main)

    def test_stable_and_sensitive_to_every_source(self):
        base = self.fingerprint()
        self.assertEqual(base, self.fingerprint())
        write(self.chapter, 'changed\n')
        self.assertNotEqual(base, self.fingerprint())
        changed = self.fingerprint()
        write(self.refs, '@book{b, title={B}}\n')
        self.assertNotEqual(changed, self.fingerprint())
        changed = self.fingerprint()
        write(self.main, '\\documentclass{book}\n\\input{chapter}\n\\bibliography{refs}\n')
        self.assertNotEqual(changed, self.fingerprint())

    def test_recursive_include_and_comma_bib_list(self):
        sub = os.path.join(self.dir, 'sub.tex')
        other = os.path.join(self.dir, 'other.bib')
        write(self.chapter, '\\input{sub}\n')
        write(sub, 'deep\n')
        write(other, '@book{c, title={C}}\n')
        write(self.main, '\\input{chapter}\n\\bibliography{refs,other}\n')
        base = self.fingerprint()
        write(sub, 'deeper\n')
        self.assertNotEqual(base, self.fingerprint())
        changed = self.fingerprint()
        write(other, '@book{d, title={D}}\n')
        self.assertNotEqual(changed, self.fingerprint())

    def test_missing_files_are_skipped(self):
        write(self.main, '\\input{nonexistent}\n\\bibliography{gone}\n')
        self.assertIsNotNone(self.fingerprint())

    def test_commented_include_is_not_collected(self):
        extra = os.path.join(self.dir, 'extra.tex')
        write(extra, 'original\n')
        write(self.main, '\\input{chapter}\n% \\input{extra}\n')
        base = self.fingerprint()
        write(extra, 'modified\n')
        self.assertEqual(base, self.fingerprint())

    def test_file_cap_limits_collection(self):
        with mock.patch.object(ss, 'MAX_FINGERPRINT_FILES', 2):
            base = self.fingerprint()
            # 第三个文件（refs.bib）在收集上限之外，改它不影响指纹。
            write(self.refs, '@book{z, title={Z}}\n')
            self.assertEqual(base, self.fingerprint())
            write(self.chapter, 'within cap\n')
            self.assertNotEqual(base, self.fingerprint())

    def test_missing_main_returns_none(self):
        self.assertIsNone(ss.source_fingerprint(os.path.join(self.dir, 'nope.tex')))


class EngineDetectTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.main = os.path.join(self.dir, 'main.tex')
        write(self.main, '\\documentclass{article}\n')
        self.config = mock.patch.object(
            ss.phitex_session.PhitexSession, 'run', return_value=(0, ''))
        self.run_mock = self.config.start()
        self.addCleanup(self.config.stop)

    def session_log(self, text):
        directory = os.path.join(self.dir, '.phitex')
        os.makedirs(directory, exist_ok=True)
        write(os.path.join(directory, 'session.log'), text)

    def test_explicit_config_wins_over_session_log(self):
        self.run_mock.return_value = (0, '  engine  xelatex (yours)\n')
        self.session_log('PhiTeX 1.0.2  main.tex → main.pdf  pdflatex · 1 thread\n')
        self.assertEqual(ss.detect_engine(self.main), 'xelatex')

    def test_default_config_falls_to_last_session_log_header(self):
        self.run_mock.return_value = (0, '  engine  pdflatex (default)\n')
        self.session_log(
            'PhiTeX 1.0.2  main.tex → main.pdf  pdflatex · 1 thread\n'
            'ok: 1 clean build\n'
            'PhiTeX 1.0.4  main.tex → main.pdf  xelatex · 1 thread\n')
        self.assertEqual(ss.detect_engine(self.main), 'xelatex')

    def test_dvi_engine_alias_maps_to_pdflatex(self):
        self.run_mock.return_value = (0, '  engine  latex (yours)\n')
        self.assertEqual(ss.detect_engine(self.main), 'pdflatex')

    def test_magic_comment_program(self):
        write(self.main, '% !TEX program = xelatex\n\\documentclass{article}\n')
        self.assertEqual(ss.detect_engine(self.main), 'xelatex')

    def test_phitex_program_comment_is_not_a_native_engine(self):
        write(self.main, '% !TEX program = phitex\n\\documentclass{article}\n')
        self.assertEqual(ss.detect_engine(self.main), 'pdflatex')

    def test_fontspec_detection(self):
        write(self.main, '\\usepackage[no-math]{fontspec}\n')
        self.assertEqual(ss.detect_engine(self.main), 'xelatex')

    def test_fallback_pdflatex(self):
        self.run_mock.return_value = (None, None)
        self.assertEqual(ss.detect_engine(self.main), 'pdflatex')


class SidecarTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name
        self.tex = '/proj/main.tex'
        self.active = ss.active_gz_path(self.folder, self.tex)

    def test_roundtrip_and_corrupt(self):
        self.assertIsNone(ss.read_sidecar(self.folder))
        ss.write_sidecar(self.folder, 'shadow', 'fp', 'xelatex')
        data = ss.read_sidecar(self.folder)
        self.assertEqual(data['source'], 'shadow')
        self.assertEqual(data['fingerprint'], 'fp')
        self.assertEqual(data['engine'], 'xelatex')
        write(os.path.join(self.folder, ss.SIDECAR_NAME), '{broken')
        self.assertIsNone(ss.read_sidecar(self.folder))

    def test_mark_phitex_source_backs_up_and_marks(self):
        with open(self.active, 'wb') as f:
            f.write(b'phitex-gz')
        ss.mark_phitex_source(self.folder, self.tex)
        with open(ss.backup_gz_path(self.folder), 'rb') as f:
            self.assertEqual(f.read(), b'phitex-gz')
        self.assertEqual(ss.read_sidecar(self.folder)['source'], 'phitex')

    def test_restore_phitex(self):
        with open(self.active, 'wb') as f:
            f.write(b'shadow-gz')
        with open(ss.backup_gz_path(self.folder), 'wb') as f:
            f.write(b'phitex-gz')
        ss.write_sidecar(self.folder, 'shadow', 'fp', 'xelatex')
        self.assertTrue(ss.restore_phitex(self.folder, self.tex))
        with open(self.active, 'rb') as f:
            self.assertEqual(f.read(), b'phitex-gz')
        self.assertEqual(ss.read_sidecar(self.folder)['source'], 'phitex')

    def test_restore_without_backup_is_a_noop(self):
        self.assertFalse(ss.restore_phitex(self.folder, self.tex))


class LayoutTest(unittest.TestCase):

    def test_size_comparison(self):
        with mock.patch.object(ss, '_pdf_page_sizes',
                               side_effect=[[(612.0, 792.0)], [(612.0, 792.0)]]):
            self.assertTrue(ss.layout_matches('a.pdf', 'b.pdf'))
        with mock.patch.object(ss, '_pdf_page_sizes',
                               side_effect=[[(612.0, 792.0)], [(612.0, 792.4)]]):
            self.assertTrue(ss.layout_matches('a.pdf', 'b.pdf'))
        with mock.patch.object(ss, '_pdf_page_sizes',
                               side_effect=[[(612.0, 792.0)], [(612.0, 700.0)]]):
            self.assertFalse(ss.layout_matches('a.pdf', 'b.pdf'))
        with mock.patch.object(ss, '_pdf_page_sizes',
                               side_effect=[[(612.0, 792.0)], [(612.0, 792.0)] * 2]):
            self.assertFalse(ss.layout_matches('a.pdf', 'b.pdf'))
        with mock.patch.object(ss, '_pdf_page_sizes',
                               side_effect=[None, [(612.0, 792.0)]]):
            self.assertFalse(ss.layout_matches('a.pdf', 'b.pdf'))

    def test_real_poppler_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, 'a.pdf')
            b = os.path.join(tmp, 'b.pdf')
            c = os.path.join(tmp, 'c.pdf')
            minimal_pdf(a, 1)
            minimal_pdf(b, 1)
            minimal_pdf(c, 2)
            self.assertTrue(ss.layout_matches(a, b))
            self.assertFalse(ss.layout_matches(a, c))
            self.assertFalse(ss.layout_matches(a, os.path.join(tmp, 'missing.pdf')))


class QueryPdfPathTest(unittest.TestCase):
    '''synctex CLI 优先读 PDF 旁边的 gz（实测），-o 必须映射进查询目录
    才能让影子 gz 生效；查询目录没 gz 时原样保留回退路线。'''

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name

    def test_maps_into_directory_when_gz_present(self):
        with open(os.path.join(self.folder, 'main.synctex.gz'), 'wb') as f:
            f.write(b'gz')
        self.assertEqual(
            synctex_query.query_pdf_path('/out/main.pdf', self.folder),
            os.path.join(self.folder, 'main.pdf'))

    def test_keeps_path_when_directory_has_no_gz(self):
        self.assertEqual(
            synctex_query.query_pdf_path('/out/main.pdf', self.folder),
            '/out/main.pdf')

    def test_empty_directory_keeps_path(self):
        self.assertEqual(synctex_query.query_pdf_path('/out/main.pdf', None),
                         '/out/main.pdf')
        self.assertEqual(synctex_query.query_pdf_path('/out/main.pdf', ''),
                         '/out/main.pdf')


class InstallTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name
        self.tex = '/proj/main.tex'
        self.glib = make_glib()
        self.patcher = mock.patch.object(ss, 'GLib', self.glib)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.installed = []
        self.runner = ss.ShadowSynctexRunner(
            self.tex, '/proj/out', self.folder,
            on_installed=lambda: self.installed.append(True))
        self.active = ss.active_gz_path(self.folder, self.tex)
        self.shadow_gz = os.path.join(self.folder, 'shadow.gz')
        with open(self.shadow_gz, 'wb') as f:
            f.write(b'shadow-gz')

    def test_install_backs_up_replaces_and_notifies(self):
        with open(self.active, 'wb') as f:
            f.write(b'phitex-gz')
        ss.write_sidecar(self.folder, 'phitex')
        self.runner._install(self.shadow_gz, 'fp1', 'xelatex')
        with open(self.active, 'rb') as f:
            self.assertEqual(f.read(), b'shadow-gz')
        with open(ss.backup_gz_path(self.folder), 'rb') as f:
            self.assertEqual(f.read(), b'phitex-gz')
        sidecar = ss.read_sidecar(self.folder)
        self.assertEqual(sidecar['source'], 'shadow')
        self.assertEqual(sidecar['fingerprint'], 'fp1')
        self.assertEqual(sidecar['engine'], 'xelatex')
        self.assertEqual(len(self.glib.idle_calls), 1)
        self.assertFalse(os.path.exists(self.active + '.tmp'))

    def test_install_keeps_existing_backup(self):
        with open(self.active, 'wb') as f:
            f.write(b'phitex-gz')
        with open(ss.backup_gz_path(self.folder), 'wb') as f:
            f.write(b'older-phitex')
        self.runner._install(self.shadow_gz, 'fp1', 'xelatex')
        with open(ss.backup_gz_path(self.folder), 'rb') as f:
            self.assertEqual(f.read(), b'older-phitex')

    def test_install_does_not_backup_a_shadow_active(self):
        with open(self.active, 'wb') as f:
            f.write(b'old-shadow')
        ss.write_sidecar(self.folder, 'shadow', 'fp0', 'xelatex')
        self.runner._install(self.shadow_gz, 'fp1', 'xelatex')
        self.assertFalse(os.path.exists(ss.backup_gz_path(self.folder)))


class RunnerSchedulingTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = self.tmp.name
        self.glib = make_glib()
        self.patcher = mock.patch.object(ss, 'GLib', self.glib)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.runner = ss.ShadowSynctexRunner('/proj/main.tex', '/proj/out', self.folder)

    def fire_timers(self):
        sources = dict(self.glib._sources)
        for source_id, (_delay, callback, args) in sources.items():
            self.glib.Source.remove(source_id)
            callback(*args)

    def test_notify_merges_into_one_debounced_run(self):
        done = threading.Event()
        calls = []

        def fake(generation):
            calls.append(generation)
            done.set()

        self.runner._build_and_install = fake
        self.runner.notify_dirty()
        self.runner.notify_dirty()
        self.runner.notify_dirty()
        self.assertEqual(len(self.glib._sources), 1)
        self.fire_timers()
        self.assertTrue(done.wait(5))
        self.assertEqual(len(calls), 1)
        # 跑完没有新脏标记：不再排期。
        time.sleep(0.05)
        self.assertEqual(len(self.glib._sources), 0)

    def test_dirty_during_run_reschedules_after_finish(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def fake(generation):
            started.set()
            release.wait(5)
            finished.set()

        self.runner._build_and_install = fake
        self.runner.notify_dirty()
        self.fire_timers()
        self.assertTrue(started.wait(5))
        # 运行中再次 notify：只置脏，不另起定时器。
        self.runner.notify_dirty()
        self.assertEqual(len(self.glib._sources), 0)
        release.set()
        self.assertTrue(finished.wait(5))
        deadline = time.time() + 5
        while not self.glib._sources and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.glib._sources), 1)
        self.runner.stop()

    def test_stop_discards_inflight_and_ignores_notify(self):
        started = threading.Event()
        release = threading.Event()
        aborted = []

        def fake(generation):
            started.set()
            release.wait(5)
            aborted.append(self.runner._aborted(generation))

        self.runner._build_and_install = fake
        self.runner.notify_dirty()
        self.fire_timers()
        self.assertTrue(started.wait(5))
        self.runner.stop()
        self.runner.notify_dirty()
        self.assertEqual(len(self.glib._sources), 0)
        release.set()
        deadline = time.time() + 5
        while not aborted and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(aborted, [True])


class RunnerBuildTest(unittest.TestCase):
    '''_build_and_install 的降级与陈旧丢弃分支（引擎/校验/指纹全 mock）。'''

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.tex = os.path.join(self.dir, 'main.tex')
        write(self.tex, 'hello\n')
        self.outdir = os.path.join(self.dir, 'out')
        os.makedirs(self.outdir)
        self.folder = os.path.join(self.dir, 'cache')
        self.glib = make_glib()
        self.patcher = mock.patch.object(ss, 'GLib', self.glib)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.runner = ss.ShadowSynctexRunner(self.tex, self.outdir, self.folder)
        # 模拟 _on_timer 已递增代数：_build_and_install(1) 才是当前代。
        self.runner._generation = 1
        self.active = ss.active_gz_path(self.folder, self.tex)
        # 引擎子进程替身：写出 gz/pdf/aux（aux 两遍相同 → 收敛）。
        self.passes = []

        def fake_pass(engine, shadow):
            self.passes.append(engine)
            stem = 'main'
            with open(os.path.join(shadow, stem + '.synctex.gz'), 'wb') as f:
                f.write(b'shadow-gz')
            minimal_pdf(os.path.join(shadow, stem + '.pdf'))
            aux = os.path.join(shadow, stem + '.aux')
            if not os.path.exists(aux):
                write(aux, '\\relax\n')
            return 0

        self.runner._run_pass = fake_pass
        self.patches = [
            mock.patch.object(ss, 'detect_engine', return_value='xelatex'),
            mock.patch.object(ss.shutil, 'which', return_value='/usr/bin/xelatex'),
            mock.patch.object(ss, 'layout_matches', return_value=True),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_happy_path_installs_after_convergence(self):
        with mock.patch.object(ss, 'source_fingerprint', return_value='fp1'):
            self.runner._build_and_install(1)
        self.assertEqual(len(self.passes), 2)   # 首遍建 aux，次遍确认不动
        with open(self.active, 'rb') as f:
            self.assertEqual(f.read(), b'shadow-gz')
        sidecar = ss.read_sidecar(self.folder)
        self.assertEqual(sidecar['source'], 'shadow')
        self.assertEqual(sidecar['fingerprint'], 'fp1')

    def test_missing_engine_degrades(self):
        with mock.patch.object(ss.shutil, 'which', return_value=None), \
                mock.patch.object(ss, 'source_fingerprint', return_value='fp1'):
            self.runner._build_and_install(1)
        self.assertEqual(self.passes, [])
        self.assertFalse(os.path.exists(self.active))
        self.assertIsNone(ss.read_sidecar(self.folder))

    def test_layout_mismatch_degrades(self):
        with mock.patch.object(ss, 'layout_matches', return_value=False), \
                mock.patch.object(ss, 'source_fingerprint', return_value='fp1'):
            self.runner._build_and_install(1)
        self.assertFalse(os.path.exists(self.active))
        self.assertIsNone(ss.read_sidecar(self.folder))

    def test_stale_fingerprint_discards_and_marks_dirty(self):
        with mock.patch.object(ss, 'source_fingerprint',
                               side_effect=['fp1', 'fp2']):
            self.runner._build_and_install(1)
        self.assertFalse(os.path.exists(self.active))
        self.assertTrue(self.runner._dirty)

    def test_failed_pass_degrades(self):
        self.runner._run_pass = lambda engine, shadow: 1
        with mock.patch.object(ss, 'source_fingerprint', return_value='fp1'):
            self.runner._build_and_install(1)
        self.assertFalse(os.path.exists(self.active))

    def test_stop_mid_build_discards(self):
        runner = self.runner

        def fake_pass(engine, shadow):
            # 第一遍正常产出后停掉 runner：下一轮循环头部的代数检查
            # 必须丢弃本轮，即使产物齐全、校验全过。
            with open(os.path.join(shadow, 'main.synctex.gz'), 'wb') as f:
                f.write(b'shadow-gz')
            minimal_pdf(os.path.join(shadow, 'main.pdf'))
            write(os.path.join(shadow, 'main.aux'), '\\relax\n')
            runner.stop()
            return 0

        self.runner._run_pass = fake_pass
        with mock.patch.object(ss, 'source_fingerprint', return_value='fp1'):
            self.runner._build_and_install(1)
        self.assertFalse(os.path.exists(self.active))


class BuildSystemGatingTest(unittest.TestCase):
    '''sidecar 三态下的门控与修复链开关（ast 隔离 build_system 方法）。'''

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = self.tmp.name
        self.tex = os.path.join(self.config, 'main.tex')
        write(self.tex, 'hello\n')
        self.folder = synctex_folder(self.config, self.tex)
        os.makedirs(self.folder)
        self.glib = make_glib()
        self.patcher = mock.patch.object(ss, 'GLib', self.glib)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.service_locator = SimpleNamespace(
            get_config_folder=lambda: self.config)
        self.fns = load_methods(
            BUILD_SYSTEM, 'BuildSystem',
            {'synctex_is_fresh', '_compute_synctex_fresh', '_shadow_sidecar',
             '_phitex_synctex_lines_unreliable', 'update_can_sync',
             '_on_shadow_synctex_setting_changed'},
            {'shadow_synctex': ss, 'synctex_folder': synctex_folder,
             'ServiceLocator': self.service_locator, 'time': time})
        self.holder = SimpleNamespace(
            document=SimpleNamespace(
                filename=self.tex,
                preview=SimpleNamespace(poppler_document=object())),
            has_synctex_file=True,
            last_build_interpreter='phitex',
            _synctex_fresh_cache=None,
            add_change_code=mock.Mock(),
            get_effective_interpreter=lambda: 'phitex',
            builders={'build_latex': SimpleNamespace(
                shadow_runner=None, last_output_directory=self.config,
                _on_shadow_synctex_installed=mock.Mock())})
        for name in ('synctex_is_fresh', '_compute_synctex_fresh',
                     '_shadow_sidecar', '_phitex_synctex_lines_unreliable',
                     'update_can_sync', '_on_shadow_synctex_setting_changed'):
            setattr(self.holder, name,
                    types.MethodType(getattr(self.fns, name), self.holder))

    def mark_shadow(self, fingerprint):
        ss.write_sidecar(self.folder, 'shadow', fingerprint, 'xelatex')

    def test_no_sidecar_keeps_legacy_behaviour(self):
        self.assertTrue(self.holder.synctex_is_fresh())
        self.assertTrue(self.holder._phitex_synctex_lines_unreliable())

    def test_phitex_sidecar_keeps_repair_chain(self):
        ss.write_sidecar(self.folder, 'phitex')
        self.assertTrue(self.holder.synctex_is_fresh())
        self.assertTrue(self.holder._phitex_synctex_lines_unreliable())

    def test_fresh_shadow_disables_repair_chain(self):
        self.mark_shadow(ss.source_fingerprint(self.tex))
        self.assertTrue(self.holder.synctex_is_fresh())
        self.assertFalse(self.holder._phitex_synctex_lines_unreliable())

    def test_stale_shadow_gates_and_restores_repair_chain(self):
        self.mark_shadow('outdated')
        self.assertFalse(self.holder.synctex_is_fresh())
        self.assertTrue(self.holder._phitex_synctex_lines_unreliable())

    def test_update_can_sync_follows_freshness(self):
        self.mark_shadow(ss.source_fingerprint(self.tex))
        self.holder.update_can_sync()
        self.assertTrue(self.holder.can_sync)
        write(self.tex, 'edited\n')
        self.holder.update_can_sync()
        self.assertFalse(self.holder.can_sync)

    def test_freshness_is_cached_but_invalidated_by_update(self):
        self.mark_shadow(ss.source_fingerprint(self.tex))
        self.assertTrue(self.holder.synctex_is_fresh())
        write(self.tex, 'edited\n')
        # TTL 内仍读缓存（mapper 逐行修复的高频路径）。
        self.assertTrue(self.holder.synctex_is_fresh())
        self.holder.update_can_sync()
        self.assertFalse(self.holder.synctex_is_fresh())

    def test_setting_off_stops_runner_and_restores_backup(self):
        active = ss.active_gz_path(self.folder, self.tex)
        with open(active, 'wb') as f:
            f.write(b'shadow-gz')
        with open(ss.backup_gz_path(self.folder), 'wb') as f:
            f.write(b'phitex-gz')
        self.mark_shadow('fp')
        runner = mock.Mock()
        self.holder.builders['build_latex'].shadow_runner = runner
        with mock.patch.object(ss, 'setting_enabled', return_value=False):
            self.holder._on_shadow_synctex_setting_changed(
                None, ('preferences', 'phitex_shadow_synctex', False))
        runner.stop.assert_called_once()
        self.assertIsNone(self.holder.builders['build_latex'].shadow_runner)
        with open(active, 'rb') as f:
            self.assertEqual(f.read(), b'phitex-gz')
        self.assertEqual(ss.read_sidecar(self.folder)['source'], 'phitex')

    def test_setting_on_wakes_a_runner_without_waiting_for_build(self):
        self.holder.has_synctex_file = True
        with mock.patch.object(ss, 'setting_enabled', return_value=True):
            self.holder._on_shadow_synctex_setting_changed(
                None, ('preferences', 'phitex_shadow_synctex', True))
        runner = self.holder.builders['build_latex'].shadow_runner
        self.assertIsNotNone(runner)
        self.assertEqual(len(self.glib._sources), 1)
        runner.stop()

    def test_unrelated_setting_change_is_ignored(self):
        self.holder._on_shadow_synctex_setting_changed(
            None, ('preferences', 'auto_build', True))
        self.assertIsNone(self.holder.builders['build_latex'].shadow_runner)
        self.assertEqual(len(self.glib._sources), 0)


class FakeRunner:
    instances = []

    def __init__(self, tex_filename, output_directory, folder, on_installed=None):
        self.tex_filename = tex_filename
        self.output_directory = output_directory or os.path.dirname(tex_filename)
        self.folder = folder
        self.on_installed = on_installed
        self.stopped = False
        self.notified = 0
        FakeRunner.instances.append(self)

    def notify_dirty(self):
        self.notified += 1

    def stop(self):
        self.stopped = True


class BuilderMountTest(unittest.TestCase):
    '''synctex 复制成功后的挂载：标记来源 + 唤醒运行器（ast 隔离）。'''

    def setUp(self):
        FakeRunner.instances = []
        self.mark = mock.Mock()
        self.fake_ss = SimpleNamespace(
            mark_phitex_source=self.mark,
            setting_enabled=lambda: True,
            ShadowSynctexRunner=FakeRunner)
        self.fns = load_methods(
            BUILDER, 'BuilderBuildLaTeX',
            {'_after_synctex_copy', '_shadow_runner_for', 'stop_phitex_session'},
            {'shadow_synctex': self.fake_ss, 'synctex_folder': synctex_folder,
             'os': os})
        self.builder = SimpleNamespace(
            config_folder='/config', phitex_session=None, shadow_runner=None,
            _on_shadow_synctex_installed=mock.Mock())
        for name in ('_after_synctex_copy', '_shadow_runner_for',
                     'stop_phitex_session'):
            setattr(self.builder, name,
                    types.MethodType(getattr(self.fns, name), self.builder))
        self.query = SimpleNamespace(tex_filename='/proj/main.tex')

    def test_phitex_copy_marks_source_and_notifies(self):
        self.builder._after_synctex_copy(self.query, '/proj/out', 'phitex')
        self.mark.assert_called_once()
        self.assertEqual(len(FakeRunner.instances), 1)
        self.assertEqual(FakeRunner.instances[0].notified, 1)
        self.builder._after_synctex_copy(self.query, '/proj/out', 'phitex')
        # 复用同一运行器，不重建。
        self.assertEqual(len(FakeRunner.instances), 1)
        self.assertEqual(FakeRunner.instances[0].notified, 2)

    def test_disabled_setting_marks_but_does_not_run(self):
        self.fake_ss.setting_enabled = lambda: False
        self.builder._after_synctex_copy(self.query, '/proj/out', 'phitex')
        self.mark.assert_called_once()
        self.assertEqual(FakeRunner.instances, [])

    def test_native_engine_marks_but_does_not_run(self):
        self.builder._after_synctex_copy(self.query, '/proj/out', 'xelatex')
        self.mark.assert_called_once()
        self.assertEqual(FakeRunner.instances, [])

    def test_output_directory_change_rebuilds_runner(self):
        self.builder._after_synctex_copy(self.query, '/proj/out', 'phitex')
        self.builder._after_synctex_copy(self.query, '/proj/other', 'phitex')
        self.assertEqual(len(FakeRunner.instances), 2)
        self.assertTrue(FakeRunner.instances[0].stopped)
        self.assertFalse(FakeRunner.instances[1].stopped)

    def test_stop_phitex_session_also_stops_runner(self):
        self.builder._after_synctex_copy(self.query, '/proj/out', 'phitex')
        runner = FakeRunner.instances[0]
        self.builder.stop_phitex_session()
        self.assertTrue(runner.stopped)
        self.assertIsNone(self.builder.shadow_runner)


class SettingsDefaultTest(unittest.TestCase):

    def test_shadow_synctex_defaults_to_enabled(self):
        from setzer.settings.settings import Settings
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(tmp)
            self.assertIs(settings.defaults['preferences']['phitex_shadow_synctex'], True)
            self.assertIs(settings.get_value('preferences', 'phitex_shadow_synctex'), True)


if __name__ == '__main__':
    unittest.main()