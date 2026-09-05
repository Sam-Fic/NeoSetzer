#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic

# coding: utf-8

# 单元测试：setzer.helpers.zoom_math（纯逻辑）+ setzer.app.ui_zoom（接管语义）
#
# zoom_math 无 gi 依赖，直接导入。UIZoomManager 依赖 Gtk.Settings 与
# preferences 持久化：测试注入最小 gi 桩（真实 gi 已加载时让位）并把模块级
# Gtk 名字替换为可控伪造对象，覆盖：
# - 100% 不写 dpi（不接管系统设置）；
# - 非 100% 写入 原始dpi × 倍率（-1 哨兵基准按 96×1024 换算）；
# - reset 把原始值（含 -1）原样写回；
# - 阶梯边界（约 51%–236%）、边界内往返精确还原、损坏持久化值规范化；
# - 惰性自举（TestUIZoomLazyBootstrap）：即使 setzer.in 的 init() 因入口脚本
#   陈旧而未执行，缩放仍真接管 dpi（不只变百分比）；
# - 图标/em 字号镜像样式表的装载与卸载（TestUIZoomIconCss）；
# - 倍率变化的副作用回调（TestUIZoomObservers）。

import sys
import types
import unittest

from setzer.helpers import zoom_math


# 最小 gi 桩：仅满足 setzer.app.ui_zoom 模块头的
# `import gi / from gi.repository import Gtk, Gdk`（同 conftest_stub 的让位策略）。
if 'gi' not in sys.modules:
    _gi = types.ModuleType('gi')
    _gi.require_version = lambda *a, **kw: None
    _repo = types.ModuleType('gi.repository')
    _repo.Gtk = types.ModuleType('Gtk')
    _repo.Gdk = types.ModuleType('Gdk')
    _gi.repository = _repo
    sys.modules['gi'] = _gi
    sys.modules['gi.repository'] = _repo

# discover 模式下，字母序靠前的测试（如 test_settings*）可能已通过
# conftest_stub 注入了无 Gdk 的桩，令上面的让位分支失效、下面的导入因缺
# Gdk 失败（真 gi / CI 单文件进程不受影响）。幂等补齐缺失的模块名即可：
# ui_zoom 导入期只要求名字存在，测试运行期统一用伪造对象替换 ui_zoom.Gtk/Gdk。
_repo = sys.modules.get('gi.repository')
if _repo is not None:
    for _name in ('Gtk', 'Gdk'):
        if not hasattr(_repo, _name):
            setattr(_repo, _name, types.ModuleType(_name))

import setzer.app.ui_zoom as ui_zoom  # 桩必须在导入前就位


class FakeCssProvider(object):
    '''可观测的 Gtk.CssProvider 伪造：记录每次 load_from_string 的样式表文本。'''

    raise_on_load = False

    def __init__(self):
        self.texts = []

    def load_from_string(self, text):
        if FakeCssProvider.raise_on_load:
            raise RuntimeError('CSS parse error')
        self.texts.append(text)


class FakeStyleContext(object):
    '''可观测的 Gtk.StyleContext 伪造：记录 display 级 provider 的装卸。'''

    added = []
    removed = []

    @staticmethod
    def add_provider_for_display(display, provider, priority):
        FakeStyleContext.added.append((display, provider, priority))

    @staticmethod
    def remove_provider_for_display(display, provider):
        FakeStyleContext.removed.append((display, provider))


class FakeDisplay(object):
    '''Gdk.Display 占位（只用作 provider 归属的标识）。'''


def make_gtk(gtk_settings):
    '''组装 ui_zoom 需要的 Gtk 命名空间（Settings/CssProvider/StyleContext）。

    不直接替整块 Gtk：那样会让「只供 Settings 的用例」意外抛 AttributeError，
    被 _apply() 的 try 吞掉而掩盖真实行为。'''
    return types.SimpleNamespace(
        Settings=types.SimpleNamespace(get_default=lambda: gtk_settings),
        CssProvider=FakeCssProvider,
        StyleContext=FakeStyleContext,
        STYLE_PROVIDER_PRIORITY_USER=800)


def make_gdk(display):
    '''组装 Gdk 命名空间；display=None 模拟 headless / 主窗口未建。'''
    return types.SimpleNamespace(
        Display=types.SimpleNamespace(get_default=lambda: display))


class FakeGtkSettings(object):
    '''可观测的 Gtk.Settings 伪造（仅实现 gtk-xft-dpi 属性）。'''

    def __init__(self, initial_dpi):
        self.dpi = initial_dpi
        self.written = []

    def get_property(self, name):
        assert name == 'gtk-xft-dpi'
        return self.dpi

    def set_property(self, name, value):
        assert name == 'gtk-xft-dpi'
        self.dpi = value
        self.written.append(value)


class FakePreferences(object):
    '''最小 preferences 伪造（get_value / set_value）。缺省值同真实
    Settings：键不存在时返回 defaults 中的 1.0。'''
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []

    def get_value(self, section, item):
        assert section == 'preferences'
        return self.values.get(item, 1.0)

    def set_value(self, section, item, value):
        assert section == 'preferences'
        self.values[item] = value
        self.writes.append((item, value))


class TestZoomMath(unittest.TestCase):

    def test_ladder_spans_expected_range(self):
        # K_MAX=9（235.8%），K_MIN=-7（51.3%）；覆盖设计目标 50%–250%
        self.assertEqual(zoom_math.K_MAX, 9)
        self.assertEqual(zoom_math.K_MIN, -7)
        self.assertTrue(0.5 < zoom_math.level_from_index(zoom_math.K_MIN) < 0.6)
        self.assertTrue(2.3 < zoom_math.level_from_index(zoom_math.K_MAX) < 2.4)

    def test_ladder_index_zero_is_exactly_default(self):
        self.assertEqual(zoom_math.level_from_index(0), 1.0)

    def test_step_in_from_default(self):
        self.assertAlmostEqual(zoom_math.next_zoom_in(1.0), 1.1)

    def test_step_out_from_default(self):
        self.assertAlmostEqual(zoom_math.next_zoom_out(1.0), 1.0 / 1.1)

    def test_step_in_at_top_is_noop(self):
        top = zoom_math.level_from_index(zoom_math.K_MAX)
        self.assertEqual(zoom_math.next_zoom_in(top), top)

    def test_step_out_at_bottom_is_noop(self):
        bottom = zoom_math.level_from_index(zoom_math.K_MIN)
        self.assertEqual(zoom_math.next_zoom_out(bottom), bottom)

    def test_roundtrip_within_bounds_is_exact(self):
        # 阶梯互逆性：索引 ±k 任意往返精确还原（k 取到边界 K_MAX）
        for steps in (1, 5, 9):
            level = 1.0
            for _ in range(steps):
                level = zoom_math.next_zoom_in(level)
            for _ in range(steps):
                level = zoom_math.next_zoom_out(level)
            self.assertEqual(level, 1.0)

    def test_clamp_normalizes_out_of_range_persisted_values(self):
        # 手改配置写进 99.0 / 0.01：吸附到阶梯边界，而非崩溃或原样通过
        self.assertEqual(zoom_math.clamp_level(99.0),
                         zoom_math.level_from_index(zoom_math.K_MAX))
        self.assertEqual(zoom_math.clamp_level(0.01),
                         zoom_math.level_from_index(zoom_math.K_MIN))

    def test_clamp_rejects_corrupt_values(self):
        # NaN / Inf / 非正数 / 非数值 → 1.0
        self.assertEqual(zoom_math.clamp_level(float('nan')), 1.0)
        self.assertEqual(zoom_math.clamp_level(float('inf')), 1.0)
        self.assertEqual(zoom_math.clamp_level(0), 1.0)
        self.assertEqual(zoom_math.clamp_level(-1.5), 1.0)
        self.assertEqual(zoom_math.clamp_level('abc'), 1.0)
        self.assertEqual(zoom_math.clamp_level(None), 1.0)

    def test_clamp_snaps_off_ladder_values_to_nearest_step(self):
        # 1.03 更近 k=0（1.0）→ 吸附为 1.0；1.14 更近 k=1（1.1）
        self.assertEqual(zoom_math.clamp_level(1.03), 1.0)
        self.assertAlmostEqual(zoom_math.clamp_level(1.14), 1.1)

    def test_is_default(self):
        self.assertTrue(zoom_math.is_default(1.0))
        self.assertFalse(zoom_math.is_default(1.1))
        self.assertFalse(zoom_math.is_default(0.9))

    def test_can_zoom_bounds(self):
        self.assertTrue(zoom_math.can_zoom_in(1.0))
        self.assertFalse(zoom_math.can_zoom_in(
            zoom_math.level_from_index(zoom_math.K_MAX)))
        self.assertTrue(zoom_math.can_zoom_out(1.0))
        self.assertFalse(zoom_math.can_zoom_out(
            zoom_math.level_from_index(zoom_math.K_MIN)))

    def test_zoomed_dpi_positive_base(self):
        # 96 dpi 基准（Windows 入口强制值）：k=2（1.21×）→ 98304×1.1² = 118948
        self.assertEqual(zoom_math.zoomed_dpi(96 * 1024, 1.21), 118948)

    def test_zoomed_dpi_sentinel_base_uses_96(self):
        # -1 / None 哨兵基准：按 96 dpi 换算，与系统默认视觉一致
        self.assertEqual(zoom_math.zoomed_dpi(-1, 1.0), 96 * 1024)
        self.assertEqual(zoom_math.zoomed_dpi(None, 1.1), 108134)

    def test_zoomed_dpi_hidpi_base_preserved(self):
        # 144 dpi（1.5× HiDPI 文字）基准：缩放乘在系统基准之上，不覆盖它。
        # 倍率取阶梯值 k=7（1.9487×）——非阶梯值会先被 clamp_level 吸附。
        k7 = zoom_math.level_from_index(7)
        self.assertEqual(zoom_math.zoomed_dpi(144 * 1024, 1.0), 144 * 1024)
        self.assertEqual(zoom_math.zoomed_dpi(144 * 1024, k7),
                         int(round(144 * 1024 * k7)))

    def test_format_percent(self):
        self.assertEqual(zoom_math.format_percent(1.0), '100%')
        self.assertEqual(zoom_math.format_percent(1.1), '110%')
        self.assertEqual(zoom_math.format_percent(1.0 / 1.1), '91%')


def reset_manager_state():
    """UIZoomManager 是纯 static class，跨用例必须整体重置——含三个一次性
    初始化标志与镜像样式表 provider 的状态。"""
    ui_zoom.UIZoomManager.zoom_level = 1.0
    ui_zoom.UIZoomManager._settings = None
    ui_zoom.UIZoomManager._base_dpi = None
    ui_zoom.UIZoomManager._dpi_ready = False
    ui_zoom.UIZoomManager._level_restored = False
    ui_zoom.UIZoomManager._explicit_init = False
    ui_zoom.UIZoomManager._css_provider = None
    ui_zoom.UIZoomManager._css_installed = False
    ui_zoom.UIZoomManager._app_declarations = None
    ui_zoom.UIZoomManager._observers = []
    FakeStyleContext.added = []
    FakeStyleContext.removed = []


class TestUIZoomManager(unittest.TestCase):

    def setUp(self):
        reset_manager_state()
        self.gtk_settings = FakeGtkSettings(96 * 1024)
        self.prefs = FakePreferences()
        self.display = FakeDisplay()
        self._orig_gtk = ui_zoom.Gtk
        self._orig_gdk = ui_zoom.Gdk
        ui_zoom.Gtk = make_gtk(self.gtk_settings)
        ui_zoom.Gdk = make_gdk(self.display)

    def tearDown(self):
        ui_zoom.Gtk = self._orig_gtk
        ui_zoom.Gdk = self._orig_gdk

    def test_init_at_default_does_not_touch_dpi(self):
        # 100% 不接管：不写 gtk-xft-dpi，也不写持久化
        ui_zoom.UIZoomManager.init(self.prefs)
        self.assertEqual(self.gtk_settings.written, [])
        self.assertEqual(self.prefs.writes, [])
        self.assertFalse(ui_zoom.UIZoomManager.is_zoomed())

    def test_init_restores_persisted_zoom(self):
        # 96×1024 × 1.1² = 118948（1.21 吸附到阶梯 k=2）
        ui_zoom.UIZoomManager.init(FakePreferences({'ui_zoom_level': 1.21}))
        self.assertEqual(self.gtk_settings.dpi, 118948)
        self.assertAlmostEqual(ui_zoom.UIZoomManager.zoom_level, 1.21)

    def test_init_clamps_corrupt_persisted_value(self):
        ui_zoom.UIZoomManager.init(FakePreferences({'ui_zoom_level': 'broken'}))
        self.assertEqual(ui_zoom.UIZoomManager.zoom_level, 1.0)
        self.assertEqual(self.gtk_settings.written, [])

    def test_zoom_in_applies_and_persists(self):
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        expected_dpi = int(round(96 * 1024 * 1.1))
        self.assertEqual(self.gtk_settings.dpi, expected_dpi)
        self.assertEqual(ui_zoom.UIZoomManager.get_zoom_percent(), '110%')
        self.assertAlmostEqual(self.prefs.values['ui_zoom_level'], 1.1, places=6)

    def test_reset_writes_back_original_raw_value(self):
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        ui_zoom.UIZoomManager.reset()
        # 原样写回启动时读到的 96×1024
        self.assertEqual(self.gtk_settings.dpi, 96 * 1024)
        self.assertEqual(self.prefs.values['ui_zoom_level'], 1.0)
        self.assertEqual(ui_zoom.UIZoomManager.get_zoom_percent(), '100%')

    def test_sentinel_base_restored_on_reset(self):
        # Wayland 常见的 -1（未设置）哨兵：缩放后 reset 应写回 -1 本身
        self.gtk_settings = FakeGtkSettings(-1)
        ui_zoom.Gtk = make_gtk(self.gtk_settings)
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        # -1 基准按 96 dpi 换算：96×1024×1.1
        self.assertEqual(self.gtk_settings.dpi, int(round(96 * 1024 * 1.1)))
        ui_zoom.UIZoomManager.reset()
        self.assertEqual(self.gtk_settings.dpi, -1)

    def test_hidpi_base_is_multiplied_not_replaced(self):
        # HiDPI 文字基准（144×1024）之上叠加，不吞掉系统缩放
        self.gtk_settings = FakeGtkSettings(144 * 1024)
        ui_zoom.Gtk = make_gtk(self.gtk_settings)
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        self.assertEqual(self.gtk_settings.dpi, int(round(144 * 1024 * 1.1)))
        ui_zoom.UIZoomManager.reset()
        self.assertEqual(self.gtk_settings.dpi, 144 * 1024)

    def test_bounds_stop_at_limits(self):
        ui_zoom.UIZoomManager.init(self.prefs)
        for _ in range(40):
            ui_zoom.UIZoomManager.zoom_in()
        top = zoom_math.level_from_index(zoom_math.K_MAX)
        self.assertEqual(ui_zoom.UIZoomManager.zoom_level, top)
        self.assertFalse(ui_zoom.UIZoomManager.can_zoom_in())
        self.assertTrue(ui_zoom.UIZoomManager.can_zoom_out())
        for _ in range(80):
            ui_zoom.UIZoomManager.zoom_out()
        bottom = zoom_math.level_from_index(zoom_math.K_MIN)
        self.assertEqual(ui_zoom.UIZoomManager.zoom_level, bottom)
        self.assertFalse(ui_zoom.UIZoomManager.can_zoom_out())
        self.assertTrue(ui_zoom.UIZoomManager.can_zoom_in())

    def test_full_roundtrip_lands_exactly_on_default(self):
        # 阶梯范围内（9 步到顶）in/out 往返恰回 100%，dpi 同步写回基准
        ui_zoom.UIZoomManager.init(self.prefs)
        for _ in range(9):
            ui_zoom.UIZoomManager.zoom_in()
        for _ in range(9):
            ui_zoom.UIZoomManager.zoom_out()
        self.assertEqual(ui_zoom.UIZoomManager.zoom_level, 1.0)
        self.assertFalse(ui_zoom.UIZoomManager.is_zoomed())
        self.assertEqual(self.gtk_settings.dpi, 96 * 1024)

    def test_noop_at_bounds_does_not_persist(self):
        # 已在顶端时再 zoom_in：级别不变，不写 dpi、不落盘
        ui_zoom.UIZoomManager.init(self.prefs)
        top = zoom_math.level_from_index(zoom_math.K_MAX)
        ui_zoom.UIZoomManager.zoom_level = top
        writes_before = len(self.prefs.writes)
        dpi_writes_before = len(self.gtk_settings.written)
        ui_zoom.UIZoomManager.zoom_in()
        self.assertEqual(ui_zoom.UIZoomManager.zoom_level, top)
        self.assertEqual(len(self.prefs.writes), writes_before)
        self.assertEqual(len(self.gtk_settings.written), dpi_writes_before)

    def test_missing_gtk_settings_degrades_gracefully(self):
        # 无 Display（headless）：读不到 Gtk.Settings → 不崩溃，倍率照常维护
        ui_zoom.Gtk = make_gtk(None)
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        self.assertAlmostEqual(ui_zoom.UIZoomManager.zoom_level, 1.1)
        self.assertAlmostEqual(self.prefs.values['ui_zoom_level'], 1.1, places=6)

    def test_no_settings_persists_nothing(self):
        # init 未注入 settings（极端防御路径）：zoom 仍可工作，只是不落盘
        ui_zoom.UIZoomManager.init(None)
        ui_zoom.UIZoomManager.zoom_in()
        self.assertAlmostEqual(ui_zoom.UIZoomManager.zoom_level, 1.1)


class TestUIZoomLazyBootstrap(unittest.TestCase):
    '''惰性自举：即使 setzer.in（meson configure_file 模板）的 init() 调用因
    入口脚本陈旧而未执行，缩放也必须真生效（不能只变百分比）。'''

    def setUp(self):
        reset_manager_state()
        self.gtk_settings = FakeGtkSettings(96 * 1024)
        self.prefs = FakePreferences()
        self.display = FakeDisplay()
        self._orig_gtk = ui_zoom.Gtk
        self._orig_gdk = ui_zoom.Gdk
        # 默认堵住 ServiceLocator 兜底路径，避免单测写用户配置（各例按需改）
        self._orig_locate = ui_zoom.UIZoomManager.__dict__['_locate_settings']
        ui_zoom.UIZoomManager._locate_settings = lambda: None
        ui_zoom.Gtk = make_gtk(self.gtk_settings)
        ui_zoom.Gdk = make_gdk(self.display)

    def tearDown(self):
        ui_zoom.Gtk = self._orig_gtk
        ui_zoom.Gdk = self._orig_gdk
        ui_zoom.UIZoomManager._locate_settings = self._orig_locate

    def test_zoom_in_without_init_still_writes_dpi(self):
        ui_zoom.UIZoomManager._locate_settings = lambda: self.prefs
        ui_zoom.UIZoomManager.zoom_in()
        self.assertEqual(self.gtk_settings.dpi, int(round(96 * 1024 * 1.1)))
        self.assertEqual(ui_zoom.UIZoomManager.get_zoom_percent(), '110%')
        self.assertAlmostEqual(self.prefs.values['ui_zoom_level'], 1.1, places=6)

    def test_read_only_entry_restores_persisted_zoom(self):
        # 状态栏/菜单只是读百分比，也应触发自举并恢复上一次的缩放
        ui_zoom.UIZoomManager._locate_settings = lambda: FakePreferences({'ui_zoom_level': 1.21})
        self.assertEqual(ui_zoom.UIZoomManager.get_zoom_percent(), '121%')
        self.assertEqual(self.gtk_settings.dpi, int(round(96 * 1024 * 1.21)))

    def test_lazy_step_from_restored_level_is_relative_to_restored(self):
        # 自举在计算新级别前完成：从持久化的 110% 放大一步得 121%，而非 110%
        ui_zoom.UIZoomManager._locate_settings = lambda: FakePreferences({'ui_zoom_level': 1.1})
        ui_zoom.UIZoomManager.zoom_in()
        self.assertAlmostEqual(ui_zoom.UIZoomManager.zoom_level, 1.21, places=6)
        self.assertEqual(self.gtk_settings.dpi, int(round(96 * 1024 * 1.21)))

    def test_explicit_init_none_does_not_autolocate(self):
        # 显式 init(None) = 故意不落盘，不得被 ServiceLocator 兜底覆盖
        called = []
        ui_zoom.UIZoomManager._locate_settings = lambda: called.append(1) or self.prefs
        ui_zoom.UIZoomManager.init(None)
        ui_zoom.UIZoomManager.zoom_in()
        self.assertEqual(called, [])
        self.assertIsNone(ui_zoom.UIZoomManager._settings)
        self.assertEqual(self.prefs.writes, [])
        # 不接管不影响 dpi 通道：基准仍正常写入
        self.assertEqual(self.gtk_settings.dpi, int(round(96 * 1024 * 1.1)))

    def test_no_display_yet_retries_later(self):
        # 主窗口/Display 未就绪：不固化基准，但倍率与百分比照常
        ui_zoom.UIZoomManager._locate_settings = lambda: self.prefs
        ui_zoom.Gtk = make_gtk(None)
        ui_zoom.UIZoomManager.zoom_in()
        self.assertAlmostEqual(ui_zoom.UIZoomManager.zoom_level, 1.1)
        self.assertFalse(ui_zoom.UIZoomManager._dpi_ready)
        self.assertEqual(self.gtk_settings.written, [])
        # Display 就绪后下一次调用补上基准并接管
        ui_zoom.Gtk = make_gtk(self.gtk_settings)
        ui_zoom.UIZoomManager.zoom_in()
        self.assertTrue(ui_zoom.UIZoomManager._dpi_ready)
        self.assertEqual(self.gtk_settings.written[0], int(round(96 * 1024 * 1.1)))
        self.assertEqual(self.gtk_settings.dpi, int(round(96 * 1024 * 1.21)))

    def test_base_not_recaptured_after_external_dpi_change(self):
        # 接管后基准只读一次：系统 dpi 中途变化不会把我们的写入当成新基准
        ui_zoom.UIZoomManager._locate_settings = lambda: self.prefs
        ui_zoom.UIZoomManager.zoom_in()
        self.gtk_settings.dpi = 144 * 1024      # 外部（如 GNOME 文本缩放）改变
        ui_zoom.UIZoomManager.zoom_in()
        self.assertEqual(self.gtk_settings.dpi, int(round(96 * 1024 * 1.21)))


class TestUIZoomIconCss(unittest.TestCase):
    '''图标/em 字号的镜像样式表（gtk-xft-dpi 不缩放 CSS px，见 helpers/zoom_css）。'''

    def setUp(self):
        reset_manager_state()
        self.gtk_settings = FakeGtkSettings(96 * 1024)
        self.prefs = FakePreferences()
        self.display = FakeDisplay()
        self._orig_gtk = ui_zoom.Gtk
        self._orig_gdk = ui_zoom.Gdk
        self._orig_raise = FakeCssProvider.raise_on_load
        FakeCssProvider.raise_on_load = False
        ui_zoom.Gtk = make_gtk(self.gtk_settings)
        ui_zoom.Gdk = make_gdk(self.display)

    def tearDown(self):
        ui_zoom.Gtk = self._orig_gtk
        ui_zoom.Gdk = self._orig_gdk
        FakeCssProvider.raise_on_load = self._orig_raise

    def _css(self):
        provider = ui_zoom.UIZoomManager._css_provider
        return provider.texts[-1] if provider is not None else ''

    def test_default_zoom_installs_nothing(self):
        # 100% 零干预：不建 provider、不装 display 级样式
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.is_zoomed()
        self.assertIsNone(ui_zoom.UIZoomManager._css_provider)
        self.assertEqual(FakeStyleContext.added, [])

    def test_zoom_in_installs_scaled_icon_sizes(self):
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        css = self._css()
        self.assertIn('* { -gtk-icon-size: 18px; }', css)          # 16 × 1.1
        self.assertIn('.large-icons { -gtk-icon-size: 35px; }', css)  # 32 × 1.1 = 35.2
        self.assertIn('check, radio { -gtk-icon-size: 15px; }', css)   # 14 × 1.1 = 15.4
        self.assertEqual(len(FakeStyleContext.added), 1)
        self.assertEqual(FakeStyleContext.added[0][0], self.display)
        # 必须高于 USER(800)：Setzer 自研样式表也装在 USER，否则压不住
        self.assertGreater(FakeStyleContext.added[0][2], 800)

    def test_repeated_zoom_reuses_single_provider(self):
        # provider 只装一次，刷新靠 load_from_string 换内容（不堆积 display 级 provider）
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        ui_zoom.UIZoomManager.zoom_in()
        self.assertEqual(len(FakeStyleContext.added), 1)
        self.assertIn('* { -gtk-icon-size: 19px; }', self._css())  # 16 × 1.21

    def test_reset_uninstalls_provider(self):
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        ui_zoom.UIZoomManager.reset()
        self.assertEqual(len(FakeStyleContext.removed), 1)
        self.assertIsNone(ui_zoom.UIZoomManager._css_provider)
        self.assertFalse(ui_zoom.UIZoomManager._css_installed)
        # 文字缩放同步交还系统（原始 dpi 原样写回）
        self.assertEqual(self.gtk_settings.dpi, 96 * 1024)

    def test_app_css_declarations_are_mirrored(self):
        # 自研样式（style_gtk.css）里的 48px 图标与 em 字号按同一倍率跟随
        ui_zoom.UIZoomManager._app_declarations = (
            ('.sidebar-empty-state image', '-gtk-icon-size', 48.0),
            ('.drop-label', 'font-size', 1.1),
        )
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        css = self._css()
        self.assertIn('.sidebar-empty-state image { -gtk-icon-size: 53px; }', css)
        self.assertIn('.drop-label { font-size: calc(1.1em * 1.1); }', css)

    def test_no_display_skips_css_without_breaking_text_zoom(self):
        # headless / 主窗口未建：dpi 照写（Gtk.Settings 可用），CSS 不装
        ui_zoom.Gdk = make_gdk(None)
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        self.assertEqual(self.gtk_settings.dpi, int(round(96 * 1024 * 1.1)))
        self.assertEqual(FakeStyleContext.added, [])

    def test_css_parse_failure_degrades_to_text_only_zoom(self):
        # 主题升级导致选择器非法等：图标不随动，文字缩放/百分比/落盘照常
        FakeCssProvider.raise_on_load = True
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        self.assertEqual(self.gtk_settings.dpi, int(round(96 * 1024 * 1.1)))
        self.assertAlmostEqual(ui_zoom.UIZoomManager.zoom_level, 1.1)
        self.assertFalse(ui_zoom.UIZoomManager._css_installed)
        self.assertEqual(FakeStyleContext.added, [])


class TestUIZoomObservers(unittest.TestCase):
    '''倍率变化的副作用回调（FontManager 靠它重发编辑器字号 CSS）。'''

    def setUp(self):
        reset_manager_state()
        self.gtk_settings = FakeGtkSettings(96 * 1024)
        self.prefs = FakePreferences()
        self.display = FakeDisplay()
        self._orig_gtk = ui_zoom.Gtk
        self._orig_gdk = ui_zoom.Gdk
        ui_zoom.Gtk = make_gtk(self.gtk_settings)
        ui_zoom.Gdk = make_gdk(self.display)
        self.calls = []

    def tearDown(self):
        ui_zoom.Gtk = self._orig_gtk
        ui_zoom.Gdk = self._orig_gdk

    def test_observer_called_with_new_level(self):
        ui_zoom.UIZoomManager.add_observer(self.calls.append)
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        ui_zoom.UIZoomManager.zoom_in()
        self.assertEqual(len(self.calls), 2)
        self.assertAlmostEqual(self.calls[0], 1.1, places=6)
        self.assertAlmostEqual(self.calls[1], 1.21, places=6)

    def test_noop_does_not_notify(self):
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.add_observer(self.calls.append)
        ui_zoom.UIZoomManager.reset()
        self.assertEqual(self.calls, [])

    def test_restored_zoom_notifies_at_init(self):
        # 启动恢复非 100% 也要通知（编辑器 CSS 需在首帧就乘上倍率）
        ui_zoom.UIZoomManager.add_observer(self.calls.append)
        ui_zoom.UIZoomManager.init(FakePreferences({'ui_zoom_level': 1.21}))
        self.assertEqual(len(self.calls), 1)
        self.assertAlmostEqual(self.calls[0], 1.21, places=6)

    def test_broken_observer_does_not_break_zoom(self):
        def broken(level):
            raise RuntimeError('main window not ready')
        ui_zoom.UIZoomManager.add_observer(broken)
        ui_zoom.UIZoomManager.add_observer(self.calls.append)
        ui_zoom.UIZoomManager.init(self.prefs)
        ui_zoom.UIZoomManager.zoom_in()
        self.assertAlmostEqual(ui_zoom.UIZoomManager.zoom_level, 1.1)
        self.assertEqual(len(self.calls), 1)

    def test_add_observer_is_idempotent(self):
        ui_zoom.UIZoomManager.add_observer(self.calls.append)
        ui_zoom.UIZoomManager.add_observer(self.calls.append)
        self.assertEqual(len(ui_zoom.UIZoomManager._observers), 1)


if __name__ == '__main__':
    unittest.main()
