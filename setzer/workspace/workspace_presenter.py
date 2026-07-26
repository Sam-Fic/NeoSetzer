#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

from gi.repository import Gio, GLib

from setzer.app.service_locator import ServiceLocator
from setzer.app.font_manager import FontManager

import os.path


class WorkspacePresenter(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.settings = ServiceLocator.get_settings()

        # 拖动分隔条时 notify::sidebar-width-fraction 每像素触发一次。
        # 原实现每帧调 set_value → add_change_code('settings_changed') →
        # 通知全部 ~10 个 settings 观察者做字符串比较。去抖后合并为拖动
        # 停止后一次 idle 落盘，消除拖动期间的级联通知。
        self._sidebar_width_idle_id = None
        self._preview_width_idle_id = None
        # _deferred_post_activate 的 50ms timeout id。快速切换文档时取消
        # 旧 timeout，避免对已非 active 的文档状态做 sidebar/preview 显隐。
        self._dpa_timeout_id = None

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('new_inactive_document', self.on_new_inactive_document)
        self.workspace.connect('root_state_change', self.on_root_state_change)
        self.workspace.connect('set_show_symbols_or_document_structure', self.on_set_show_symbols_or_document_structure)
        self.workspace.connect('set_show_preview_or_help', self.on_set_show_preview_or_help)
        self.workspace.connect('show_build_log_state_change', self.on_show_build_log_state_change)
        self.settings.connect('settings_changed', self.on_settings_changed)

        self.main_window.mode_stack.set_visible_child_name('welcome_screen')
        # 初始默认欢迎页模式：headerbar 迁到 welcome_overlay，否则无文档时
        # open/create 按钮不可见、用户无法开始编辑（详见 reparent_headerbar 注释）。
        self.main_window.reparent_headerbar(to_welcome=True)
        self.update_font()
        self.update_colors()
        self.setup_paneds()

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item in ['font_string', 'use_system_font']:
            self.update_font()

        if item == 'color_scheme':
            self.update_colors()

    def on_new_document(self, workspace, document):
        self.main_window.document_stack.add_child(document.view)
        # 挂钩 build 完成事件：首次编译成功后，若用户已开启预览
        # （show_preview=True），之前因「从未编译」而被抑制的预览侧栏
        # 需要重新评估显隐——此时文档已有 PDF，预览有内容可展示。
        if document.is_latex_document():
            document.build_system.connect('build_state', self.on_build_state)

    def on_build_state(self, build_system, message):
        # 编译成功后重新评估预览侧栏显隐。用 idle 延迟到当前 build_state
        # 通知链完成之后，避免在 build 回调中同步操作 OverlaySplitView。
        if message == 'success':
            GLib.idle_add(self.update_preview_help_visibility)

    def on_document_removed(self, workspace, document):
        self.main_window.document_stack.remove(document.view)

        if self.workspace.active_document == None:
            self.main_window.mode_stack.set_visible_child_name('welcome_screen')
            # 回到欢迎页：headerbar 迁到 welcome_overlay，保证 open/create 可见。
            self.main_window.reparent_headerbar(to_welcome=True)

    def on_new_active_document(self, workspace, document):
        # 快速切换文档时取消上一次尚未触发的 _deferred_post_activate，
        # 避免对旧文档状态做 sidebar/preview 显隐（新文档会重新调度）。
        if self._dpa_timeout_id is not None:
            GLib.Source.remove(self._dpa_timeout_id)
            self._dpa_timeout_id = None
        self.main_window.mode_stack.set_visible_child_name('documents')
        self.main_window.reparent_headerbar(to_welcome=False)
        self.main_window.document_stack.set_visible_child(document.view)
        self.focus_active_document()

        if document.is_latex_document():
            # autocomplete 延迟到 idle 构造（_init_latex_features），首次
            # 激活时 document.autocomplete 属性尚不存在（__init__ 未设默认值）。
            # 用 getattr 显式取默认 None，替代原 try/except AttributeError: pass——
            # 后者会静默吞掉 widget / view 意外为 None 等其他 AttributeError，
            # 让自动补全失效时无任何线索（用户直到按键才察觉）。getattr + None
            # 检查把「lazy 未就绪」与「异常状态」区分开：未就绪跳过等 idle 补做
            # （_init_latex_features 检查 is_active 后挂载），异常状态则正常冒泡。
            autocomplete = getattr(document, 'autocomplete', None)
            if autocomplete is not None and autocomplete.widget is not None:
                self.main_window.preview_paned_overlay.add_overlay(autocomplete.widget.view)

        # sidebar/preview 可见性更新延迟到首轮 size_allocate 之后：mode_stack
        # 切换使 sidebar_split / preview_split（Adw.OverlaySplitView）从不可见
        # 变为可见，首次分配尚未完成。此时同步调 set_show_sidebar(True) 会让
        # OverlaySplitView 在总宽度=0/未确定的状态下分配 sidebar + content，
        # 内部计算可能产生负宽度（GTK 警告：AdwBin width=-2147482112），导致
        # 界面错乱一会才恢复。
        #
        # 调度方式选择（经运行时证据验证）：
        # - GLib.idle_add 默认优先级 PRIORITY_DEFAULT_IDLE (200) 低于
        #   GDK_PRIORITY_REDRAW (120)，首轮 GtkSource.View 渲染产生的 ~1.5s
        #   连续帧会持续抢占 idle → sidebar/preview 延迟 1.5s 出现。
        # - GLib.idle_add PRIORITY_HIGH_IDLE (100) 高于 REDRAW，在首轮
        #   size_allocate 之前就执行 → 负宽度 bug 复发 + 掉帧。
        # - GLib.timeout_add(50, ...) 在 50ms 后以 PRIORITY_DEFAULT (0) 触发，
        #   高于 REDRAW 故不被帧抢占；50ms 足够首轮 layout（~16ms/帧 × 3 帧）
        #   完成，又远小于 1.5s。经实测 _deferred_post_activate 在 ~50ms 触发，
        #   sidebar/preview 与文档视图几乎同时出现。30ms 经用户验证偏早
        #   （首轮分配未完全稳定），50ms 为最佳平衡点。
        # build_log 刷新也一并合并（不涉及布局）。
        self._dpa_timeout_id = GLib.timeout_add(50, self._deferred_post_activate)

    def _deferred_post_activate(self):
        '''on_new_active_document 中延迟到首轮 size_allocate 之后的后续更新。
        由 GLib.timeout_add(50, ...) 触发，避免在 OverlaySplitView 首次分配
        期间调 set_show_sidebar 导致负尺寸分配。'''
        self._dpa_timeout_id = None
        self.update_sidebar_visibility(False)
        self.refresh_build_log_if_open()
        self.update_preview_help_visibility(False)
        return False

    def on_root_state_change(self, workspace, state):
        self.update_build_log_visibility()
        self.update_preview_help_visibility(False)

    def on_new_inactive_document(self, workspace, document):
        if document.is_latex_document():
            # 同 on_new_active_document：getattr 安全访问，避免静默吞
            # AttributeError（文档刚切换为 inactive 时 autocomplete 可能
            # 尚未 lazy 构造完成，此时无可移除的 overlay，跳过即可）。
            autocomplete = getattr(document, 'autocomplete', None)
            if autocomplete is not None and autocomplete.widget is not None:
                self.main_window.preview_paned_overlay.remove_overlay(autocomplete.widget.view)

    def on_set_show_symbols_or_document_structure(self, workspace):
        if self.workspace.show_symbols:
            self.main_window.sidebar.set_visible_child_name('symbols')
        elif self.workspace.show_document_structure:
            self.main_window.sidebar.set_visible_child_name('document_structure')
        self.focus_active_document()

        self.update_sidebar_visibility()

    def on_set_show_preview_or_help(self, workspace):
        if self.workspace.show_preview:
            self.main_window.preview_help_stack.set_visible_child_name('preview')
            self.focus_active_document()
        elif self.workspace.show_help:
            self.main_window.preview_help_stack.set_visible_child_name('help')
            if self.main_window.help_panel.stack.get_visible_child_name() == 'search':
                self.main_window.help_panel.search_entry.set_text('')
                self.main_window.help_panel.search_entry.grab_focus()
            else:
                self.focus_active_document()
        else:
            self.focus_active_document()
        # 所有切换路径（按钮 / 快捷键 / 状态恢复）都收口于此，
        # 顺手同步两个 switch 按钮的图标，确保始终展示"目标面板"图标，
        # 不依赖任何私有状态布尔。
        self.main_window.preview_panel.presenter._sync_switch_icons()
        # 用户手动切换预览/帮助：即使文档从未编译也展开（显示 "No preview
        # available" 占位，提示用户点编译按钮）。suppress_unbuilt=False 跳过
        # 「从未编译则抑制」的自动展开逻辑。
        self.update_preview_help_visibility(suppress_unbuilt=False)

    def on_show_build_log_state_change(self, workspace, show_build_log):
        self.update_build_log_visibility()

    def update_sidebar_visibility(self, animate=True):
        sidebar_visible_for_latex_docs = self.workspace.show_symbols or self.workspace.show_document_structure
        show_sidebar = self.workspace.get_active_latex_document() and sidebar_visible_for_latex_docs
        self.main_window.sidebar_split.set_show_sidebar(show_sidebar)

    def update_build_log_visibility(self, animate=True):
        '''Pass-10: 从 set_visible 底部面板改为 present/close 弹窗。

        show_build_log=True 时 present dialog（若尚未打开）；False 时 close。
        present 前先调 presenter.populate 刷新内容（覆盖 autoshow 触发时
        items 已更新但 view 未同步的情况，以及启动时恢复弹窗状态）。
        '''
        show_build_log = self.workspace.get_root_or_active_latex_document() and self.workspace.show_build_log
        build_log = self.workspace.build_log
        if show_build_log:
            if not build_log.is_open:
                # present 前刷新内容：确保打开的是当前文档的最新 build_log。
                # populate 会处理 document 为 None 的情况（显示 empty_label）。
                if build_log.view.presenter is not None:
                    build_log.view.presenter.populate()
                build_log.view.present()
                build_log.on_present()
        else:
            # close 幂等：未打开时 close 无副作用。
            build_log.view.close()

    def refresh_build_log_if_open(self):
        '''切换文档时：弹窗若打开，刷新内容；不自动开关。

        与 update_build_log_visibility 的区别：后者根据 workspace.show_build_log
        状态决定 present/close；前者仅在已打开时刷新内容，保留弹窗状态。
        '''
        build_log = self.workspace.build_log
        if build_log.is_open and build_log.view.presenter is not None:
            build_log.view.presenter.populate()

    def update_preview_help_visibility(self, animate=True, suppress_unbuilt=True):
        show_preview = self.workspace.show_preview
        show_help = self.workspace.show_help
        target_doc = self.workspace.get_root_or_active_latex_document()
        preview_help_visible = (show_preview or show_help) and target_doc is not None
        # 新建/从未编译过的文档没有 PDF，预览侧栏只会显示空白占位（"No preview
        # available"）。自动展开（文档激活时）默认抑制，避免无意义地占据屏幕空间。
        # 一旦文档首次编译成功（document_has_been_built=True）或重新打开时磁盘上
        # 已有 PDF（poppler_document 非 None），恢复正常显隐
        # （on_build_state 在编译成功后回调本方法重新评估）。
        # 用户手动点预览按钮时传 suppress_unbuilt=False，始终展开（显示占位提示
        # 用户去编译）。help 侧栏与编译无关，始终尊重用户设置，不受此抑制影响。
        if suppress_unbuilt and preview_help_visible and show_preview and not show_help:
            if not target_doc.build_system.document_has_been_built and target_doc.preview.poppler_document is None:
                preview_help_visible = False
        # preview_split 为 Adw.OverlaySplitView，set_show_sidebar() 自带滑入/滑出动画
        # （与 sidebar_split 一致），故 toggle preview / help 有滑入动画。
        self.main_window.preview_split.set_show_sidebar(preview_help_visible)
        if preview_help_visible:
            self.main_window.headerbar.preview_help_toggle.set_active(True)
        elif not show_preview and not show_help:
            self.main_window.headerbar.preview_help_toggle.set_active(False)

    def focus_active_document(self):
        active_document = self.workspace.get_active_document()
        if active_document != None:
            active_document.view.source_view.grab_focus()

    def update_font(self):
        if self.settings.get_value('preferences', 'use_system_font'):
            FontManager.font_string = FontManager.default_font_string
        else:
            FontManager.font_string = self.settings.get_value('preferences', 'font_string')
        FontManager.propagate_font_setting()

    def update_colors(self):
        # 自定义主题系统已移除，改为跟随系统 Libadwaita 调色板。
        # 自绘控件通过 ColorManager 的内置色回退取色。
        try: self.workspace.help_panel.update_colors()
        except AttributeError: pass

    def setup_paneds(self):
        sidebar_visible_for_latex_docs = self.workspace.show_symbols or self.workspace.show_document_structure
        show_sidebar = self.workspace.get_active_latex_document() and sidebar_visible_for_latex_docs
        preview_help_visible_for_latex_docs = self.workspace.show_preview or self.workspace.show_help
        show_preview_help = self.workspace.get_root_or_active_latex_document() and preview_help_visible_for_latex_docs
        # Pass-10: build_log 弹窗化后，初始显隐走 update_build_log_visibility
        # （present/close dialog），不再用 set_visible + paned position。
        show_build_log = self.workspace.get_root_or_active_latex_document() and self.workspace.get_show_build_log()

        sidebar_fraction = self.workspace.settings.get_value('window_state', 'sidebar_width_fraction')
        preview_fraction = self.workspace.settings.get_value('window_state', 'preview_width_fraction')

        # 一次性迁移：旧版用像素 position（= 编辑器列宽度），新版用 fraction（= 预览占比）。
        # 仅当存在有效旧 position 时估算 fraction = 1 - position/window_width，并落盘。
        legacy_pos = self.workspace.settings.data.get('window_state', {}).get('preview_paned_position', -1)
        saved_width = self.workspace.settings.get_value('window_state', 'width')
        if legacy_pos > 0 and saved_width > 0:
            preview_fraction = 1.0 - (legacy_pos / saved_width)
            preview_fraction = min(max(preview_fraction, 0.2), 0.8)
            self.workspace.settings.set_value('window_state', 'preview_width_fraction', preview_fraction)
            try: del(self.workspace.settings.data['window_state']['preview_paned_position'])
            except KeyError: pass

        # sidebar / preview 宽度均按 fraction（Adw.OverlaySplitView）
        if isinstance(sidebar_fraction, (int, float)) and 0.0 < sidebar_fraction <= 1.0:
            self.main_window.sidebar_split.set_sidebar_width_fraction(sidebar_fraction)
        if isinstance(preview_fraction, (int, float)) and 0.0 < preview_fraction <= 1.0:
            self.main_window.preview_split.set_sidebar_width_fraction(preview_fraction)
        # build_log_paned_position 设置项已废弃（弹窗尺寸由 Adw.Dialog 自管理）。

        if self.workspace.show_symbols: self.main_window.sidebar.set_visible_child_name('symbols')
        elif self.workspace.show_document_structure: self.main_window.sidebar.set_visible_child_name('document_structure')

        if self.workspace.show_preview: self.main_window.preview_help_stack.set_visible_child_name('preview')
        elif self.workspace.show_help: self.main_window.preview_help_stack.set_visible_child_name('help')

        # 初始显隐（首次无动画）。build_log 走 update_build_log_visibility
        # （present dialog 而非 set_visible 面板）。
        if show_build_log:
            self.update_build_log_visibility()
        self.main_window.sidebar_split.set_show_sidebar(show_sidebar)
        self.main_window.preview_split.set_show_sidebar(show_preview_help)

        # 拖动分隔条时实时持久化到 settings（仅更新内存 dict，pickle 在关闭时落盘）
        self.main_window.sidebar_split.connect('notify::sidebar-width-fraction', self.on_sidebar_width_changed)
        self.main_window.preview_split.connect('notify::sidebar-width-fraction', self.on_preview_width_changed)

        self.main_window.headerbar.sidebar_toggle.set_active(self.workspace.show_symbols or self.workspace.show_document_structure)
        self.main_window.headerbar.preview_help_toggle.set_active(self.workspace.show_preview or self.workspace.show_help)

    def on_sidebar_width_changed(self, split, pspec):
        # 去抖：拖动期间仅缓存最新值，idle 时一次性 set_value。
        # 避免 notify 每像素触发 set_value → settings_changed → 10+ 观察者回调。
        fraction = split.get_sidebar_width_fraction()
        if self._sidebar_width_idle_id is not None:
            GLib.Source.remove(self._sidebar_width_idle_id)
        self._sidebar_width_idle_id = GLib.idle_add(self._persist_sidebar_width, fraction)

    def _persist_sidebar_width(self, fraction):
        self._sidebar_width_idle_id = None
        self.workspace.settings.set_value('window_state', 'sidebar_width_fraction', fraction)
        return False

    def on_preview_width_changed(self, split, pspec):
        fraction = split.get_sidebar_width_fraction()
        if self._preview_width_idle_id is not None:
            GLib.Source.remove(self._preview_width_idle_id)
        self._preview_width_idle_id = GLib.idle_add(self._persist_preview_width, fraction)

    def _persist_preview_width(self, fraction):
        self._preview_width_idle_id = None
        self.workspace.settings.set_value('window_state', 'preview_width_fraction', fraction)
        return False


