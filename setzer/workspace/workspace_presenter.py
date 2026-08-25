#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026-present Sam-Fic
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

from gi.repository import Gio, GLib, Adw

from setzer.app.service_locator import ServiceLocator
from setzer.app.font_manager import FontManager

import os.path


class WorkspacePresenter(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.settings = ServiceLocator.get_settings()

        # 文档 <-> Adw.TabPage 双向映射。
        # document_stack 现在是 Adw.TabView（viewgtk 处构造），
        # 替换原来的 Gtk.Stack。它原生提供：标签条、滚动/拖拽排序、
        # 中键关闭、Ctrl+Tab 循环、关闭确认协议（close-page signal）、
        # 自带 .tabbar 样式 + 暗色主题。
        # 真理之源仍是 workspace.open_documents / active_document：
        # 标签条只是观察者，presenter 维护的双向映射是它们之间的桥。
        self._doc_to_page = dict()
        self._page_to_doc = dict()
        # 选中文档变更的抑制计数器：set_selected_page / close_page_finish
        # 等内部动作会发 selected-page::notify，若不抑制会无限循环
        # workspace.set_active_document → presenter.on_new_active_document
        # → set_selected_page → notify → set_active_document。
        self._selecting = 0

        # 拖动分隔条时 notify::sidebar-width-fraction 每像素触发一次。
        # 原实现每帧调 set_value → add_change_code('settings_changed') →
        # 通知全部 ~10 个 settings 观察者做字符串比较。去抖后合并为拖动
        # 停止后一次 idle 落盘，消除拖动期间的级联通知。
        self._sidebar_width_idle_id = None
        self._preview_width_idle_id = None
        # _deferred_post_activate 的 50ms timeout id。快速切换文档时取消
        # 旧 timeout，避免对已非 active 的文档状态做 sidebar/preview 显隐。
        self._dpa_timeout_id = None
        # 延迟文档视图激活：set_visible_child 和 focus 延后到 _deferred_post_activate
        # 执行，让 mode_stack 切换和 OverlaySplitView 首轮布局先完成。
        self._pending_document_view = None

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('new_inactive_document', self.on_new_inactive_document)
        self.workspace.connect('root_state_change', self.on_root_state_change)
        # Adw.TabView 的双向同步：
        # - 用户点击标签条切页 → selected-page::notify → workspace.set_active_document
        # - 用户点关闭按钮/中键 → close-page signal → workspace.remove_document
        # 这两条是「UI → workspace」方向；workspace → UI 已由 on_new_*
        # 覆盖。双向通过 _selecting 计数器抑制反馈循环。
        self.main_window.document_stack.connect('notify::selected-page', self._on_tab_view_selected_page_changed)
        self.main_window.document_stack.connect('close-page', self._on_tab_view_close_page)
        # BuildSystem 可能晚于 new_document 才挂接（会话恢复的非活跃文档在
        # 激活时才建工具链）：latex_toolchain_ready 时补连 build_state。
        self.workspace.connect('latex_toolchain_ready', self.on_latex_toolchain_ready)
        self.workspace.connect('set_show_symbols_or_document_structure', self.on_set_show_symbols_or_document_structure)
        self.workspace.connect('set_show_preview_or_help', self.on_set_show_preview_or_help)
        self.workspace.connect('show_build_log_state_change', self.on_show_build_log_state_change)
        self.settings.connect('settings_changed', self.on_settings_changed)

        self.main_window.mode_stack.set_visible_child_name('welcome_screen')
        # 初始默认欢迎页模式：headerbar 迁到 welcome_overlay，使欢迎页也能显示菜单与
        # 标题（open/create 按钮已在欢迎页正文，详见 reparent_headerbar 注释）。
        self.main_window.reparent_headerbar(to_welcome=True)
        self.update_font()
        self.update_colors()
        self.update_shortcuts_bar_visibility()
        self.update_tab_bar_visibility()
        self.setup_paneds()

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item in ['font_string', 'use_system_font']:
            self.update_font()

        if item == 'color_scheme':
            self.update_colors()

        if item == 'show_shortcuts_bar':
            self.update_shortcuts_bar_visibility()

        if item == 'show_tab_bar':
            self.update_tab_bar_visibility()

    def on_new_document(self, workspace, document):
        # 把文档视图挂到 Adw.TabView（替换原 Gtk.Stack）：
        # 标签条 / 拖拽排序 / 中键关闭 / Ctrl+Tab 循环全部由 Adw 原生提供。
        # add_page 立即执行：adw 的 child 容器是 Bin-like，只在切到该 page 时
        # 才 realize 子 widget；这与原来 Gtk.Stack + 延迟 add_child 的延迟
        # realize 行为等价，但额外获得 native 标签条。
        # parent 取根文档的 page（root_state_change 时已存在），无根文档则 None
        # —— 全部并排，无层级缩进。
        root_doc = self.workspace.get_root_document()
        parent_page = self._doc_to_page.get(root_doc) if root_doc is not None else None
        page = self.main_window.document_stack.add_page(
            document.view, parent=parent_page)
        # 双向映射：set_active_document / set_selected_page 都从映射回查。
        self._doc_to_page[document] = page
        self._page_to_doc[page] = document
        # 把 page 的 title / tooltip / icon 接到 document 的 displayname /
        # filename / 文件类型图标。脏点用 Adw.TabPage 内的 indicator-icon
        # (AdwTabView 自带「+」溢出菜单) 与 page.title 后缀的「•」组合：
        # 标题文字由我们控制，indicator-icon 给 root/build 状态。
        self._refresh_page_label(document)
        # 订阅 document 信号：displayname / filename 变化 → 刷 page.title；
        # modified-changed → 切「•」与 indicator-icon；is_root 变化 → 刷
        # page.parent（root 文档的 page 是其它 page 的 parent）。
        document.connect('displayname_change', self._on_document_displayname_changed)
        document.connect('filename_change', self._on_document_displayname_changed)
        # 文档关闭 / 已存盘提示要监听 modified-changed 走的是 GtkSource.Buffer
        # 的 modified-changed signal（与 document.py 内部同名 change_code 重名）。
        # 实际订阅由 document 通过 Observable 的 change code 'modified_changed'
        # 暴露，避免耦合具体 widget 实现。
        document.connect('modified_changed', self._on_document_modified_changed)
        document.connect('is_root_changed', self._on_document_is_root_changed)
        # 挂钩 build 完成事件：首次编译成功后，若用户已开启预览
        # （show_preview=True），之前因「从未编译」而被抑制的预览侧栏
        # 需要重新评估显隐——此时文档已有 PDF，预览有内容可展示。
        if document.is_latex_document():
            # 工具链可能尚未挂接（会话恢复的非活跃轻量文档）：无
            # build_system 则跳过，等 latex_toolchain_ready 再补连。
            # 两个入口互斥：创建即挂接时 new_document 能看到 build_system、
            # latex_toolchain_ready 不会发出（挂接幂等）；延迟挂接时
            # new_document 看不到 build_system、由 ready 补连。不会双连。
            build_system = getattr(document, 'build_system', None)
            if build_system is not None:
                document.build_system.connect('build_state', self.on_build_state)

    def on_latex_toolchain_ready(self, workspace, document):
        '''延迟挂接的工具链就绪：补连 build_state（编译成功后重估预览显隐）。'''
        if not document.is_latex_document():
            return
        build_system = getattr(document, 'build_system', None)
        if build_system is not None:
            build_system.connect('build_state', self.on_build_state)

    def on_build_state(self, build_system, message):
        # 编译成功后重新评估预览侧栏显隐。用 idle 延迟到当前 build_state
        # 通知链完成之后，避免在 build 回调中同步操作 OverlaySplitView。
        if message == 'success':
            GLib.idle_add(self.update_preview_help_visibility)

    def on_document_removed(self, workspace, document):
        # 从 Adw.TabView 拆 page：close_page_finish(page, confirm=False) 立即
        # 移除（已通过 workspace 内部的 confirm 校验，adw 不要再问）。
        # Adw.TabView 没有 remove_page 公开方法，只能走 close_page / finish 协议。
        page = self._doc_to_page.pop(document, None)
        if page is not None:
            self._page_to_doc.pop(page, None)
            # 抑制 selected-page::notify（拆掉当前选中 page 时会发空页），
            # 避免触发 workspace.set_active_document(None) 引发循环。
            self._selecting += 1
            try:
                self.main_window.document_stack.close_page_finish(page, False)
            finally:
                self._selecting -= 1

        if self.workspace.active_document == None:
            self.main_window.mode_stack.set_visible_child_name('welcome_screen')
            # 回到欢迎页：headerbar 迁到 welcome_overlay，保持菜单/标题可见。
            self.main_window.reparent_headerbar(to_welcome=True)

    def on_new_active_document(self, workspace, document):
        # 快速切换文档时取消上一次尚未触发的 _deferred_post_activate，
        # 避免对旧文档状态做 sidebar/preview 显隐（新文档会重新调度）。
        if self._dpa_timeout_id is not None:
            GLib.Source.remove(self._dpa_timeout_id)
            self._dpa_timeout_id = None

        # 切换文档时显示 spinner，让用户知道应用正在加载。延迟模式切换和
        # 文档视图添加到 idle/timeout 回调，让 spinner 在首帧渲染出来。
        current_mode = self.main_window.mode_stack.get_visible_child_name()
        self.main_window.show_loading_spinner()
        # 把新活跃文档对应的 page 同步到 Adw.TabView 的 selected。
        # 抑制 selected-page::notify 回调（它会再次调 workspace.set_active_document）。
        page = self._doc_to_page.get(document)
        if page is not None:
            self._selecting += 1
            try:
                self.main_window.document_stack.set_selected_page(page)
            finally:
                self._selecting -= 1
        if current_mode == 'welcome_screen':
            self._pending_document_view = document.view
            GLib.idle_add(self._do_activate_from_welcome)
        else:
            self._pending_document_view = document.view
            self._dpa_timeout_id = GLib.timeout_add(50, self._deferred_post_activate)

    def _do_activate_from_welcome(self):
        '''首次从欢迎页切换到文档模式的延迟执行。
        由 on_new_active_document 通过 idle_add 调度，确保 spinner 已在首帧渲染。'''
        self.main_window.mode_stack.set_visible_child_name('documents')
        self.main_window.reparent_headerbar(to_welcome=False)
        self._dpa_timeout_id = GLib.timeout_add(50, self._deferred_post_activate)
        return False

    def _deferred_post_activate(self):
        '''on_new_active_document 中延迟到首轮 size_allocate 之后的后续更新。
        由 GLib.timeout_add(50, ...) 触发，避免在 OverlaySplitView 首次分配
        期间调 set_show_sidebar 导致负尺寸分配。'''
        self._dpa_timeout_id = None
        # 延迟激活文档视图：此时 mode_stack 和 OverlaySplitView 的首轮布局
        # 已完成，GtkSource.View 渲染不再与它们竞争资源。
        if self._pending_document_view is not None:
            view = self._pending_document_view
            self._pending_document_view = None
            # Adw.TabView 已在 on_new_document 时把 view add_page 进栈；
            # 这里只需要把选中态同步到 tab view（_deferred_post_activate
            # 在 on_new_active_document 调 set_selected_page 之后才跑；
            # 即便如此此处再保险一次，幂等）。
            page = self._doc_to_page.get(self.workspace.get_active_document())
            if page is not None:
                self._selecting += 1
                try:
                    self.main_window.document_stack.set_selected_page(page)
                finally:
                    self._selecting -= 1
            # 激活文档后，若 autocomplete 已就绪（如切换回已有文档），挂载 overlay。
            # 首次新建文档时 autocomplete 由 _init_deferred_features 的 idle 回调处理。
            document = self.workspace.get_active_document()
            if document is not None and document.is_latex_document():
                autocomplete = getattr(document, 'autocomplete', None)
                if autocomplete is not None and autocomplete.widget is not None:
                    self.main_window.preview_paned_overlay.add_overlay(autocomplete.widget.view)
            # 用 idle 延迟聚焦，确保 set_selected_page 后 GtkSource.View
            # 已完成 realize 可接受焦点。
            GLib.idle_add(self._deferred_focus)
        self.update_sidebar_visibility(False)
        self.refresh_build_log_if_open()
        self.update_preview_help_visibility(False)
        # 首次激活完成后隐藏 loading spinner
        self.main_window.hide_loading_spinner()
        return False

    def _deferred_focus(self):
        '''在 _deferred_post_activate 之后延迟聚焦文档视图，确保
        GtkSource.View 已完成 realize 可接受焦点。'''
        active_document = self.workspace.get_active_document()
        if active_document is not None:
            active_document.view.source_view.grab_focus()
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
        show_preview = self.workspace.show_preview
        # 预览已弹出独立窗口时，侧栏只显示帮助（无 status page）。
        # show_preview 被忽略——预览在独立窗口，侧栏仅保留 help。
        if self.workspace.is_preview_popped_out():
            show_preview = False

        if show_preview:
            self.main_window.preview_help_stack.set_visible_child_name('preview')
            self.focus_active_document()
        elif self.workspace.show_help:
            self.main_window.preview_help_stack.set_visible_child_name('help')
            # HelpPanelView 用 content / search_content_box 两个互斥容器（非 Stack），
            # 搜索页可见时清空搜索框并聚焦。
            if self.main_window.help_panel.search_content_box.get_visible():
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
            # 仅当 is_open=False（权威状态）时 present。不使用 get_visible()：
            # Adw.Dialog.close() 异步，unmapped 中间态下 get_visible() 仍返回
            # True，用它判断会错误阻止正常的第二次 present。
            if not build_log.is_open:
                # present 前刷新内容：确保打开的是当前文档的最新 build_log。
                # populate 会处理 document 为 None 的情况（显示 empty_label）。
                if build_log.view.presenter is not None:
                    build_log.view.presenter.populate()
                build_log.view.present()
                build_log.on_present()
        else:
            # 仅当 is_open=True 时 close。这是消除「第二次 Esc 失效」的关键：
            # Esc 关闭走原生 closed 信号 → on_dialog_closed 先把 is_open 设 False，
            # 再 set_show_build_log(False) 触发本函数 → 此时 is_open 已是 False，
            # 不会重复调用 close()。否则会对一个已 unmapped（不在屏上）的 dialog
            # 再调一次 close()，触发 Adwaita "not presented" critical 并损坏 dialog
            # 内建 Escape shortcut 状态，导致下次 present 后 Esc 彻底失效。
            if build_log.is_open:
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
        if self.workspace.is_preview_popped_out():
            # 预览已 detach 到独立窗口：侧栏只显示帮助（无 status page、无 switch button）。
            # show_preview 不再算展开理由——预览在独立窗口，侧栏仅保留 help。
            # pop_out 时已将 show_preview 置 False（自动收起一次），用户可开关 help 来展开侧栏。
            preview_help_visible = show_help and target_doc is not None
        else:
            preview_help_visible = (show_preview or show_help) and target_doc is not None
            # 新建/从未编译过的文档没有 PDF，预览侧栏只会显示空白占位（"No preview
            # available"）。自动展开（文档激活时）默认抑制，避免无意义地占据屏幕空间。
            # 一旦文档首次编译成功（document_has_been_built=True）或重新打开时磁盘上
            # 已有 PDF（poppler_document 非 None），恢复正常显隐
            # （on_build_state 在编译成功后回调本方法重新评估）。
            # 用户手动点预览按钮时传 suppress_unbuilt=False，始终展开（显示占位提示
            # 用户去编译）。help 侧栏与编译无关，始终尊重用户设置，不受此抑制影响。
            if suppress_unbuilt and preview_help_visible and show_preview and not show_help:
                # target_doc 可能是未挂接工具链的根文档（会话恢复后从未激活）：
                # 无 build_system / preview 视同「从未编译且无 PDF」，抑制自动展开。
                doc_build_system = getattr(target_doc, 'build_system', None)
                doc_preview = getattr(target_doc, 'preview', None)
                if doc_build_system is None or doc_preview is None or (
                        not doc_build_system.document_has_been_built
                        and doc_preview.poppler_document is None):
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
            base_font_string = FontManager.default_font_string
        else:
            base_font_string = self.settings.get_value('preferences', 'font_string')
        # 记录干净基准字号（不含缩放），供 FontManager 计算缩放百分比使用。
        # 该值只在字体偏好变化时经此处更新，缩放动作不会改写它。
        FontManager.base_font_string = base_font_string
        # 加载保存的缩放倍率：默认 1.0（无缩放）。应用到基准字号上得到最终字号。
        # 这样即使 use_system_font=True，用户的缩放偏好也能跨重启保持。
        saved_zoom = self.settings.get_value('preferences', 'editor_font_zoom_level')
        FontManager.saved_zoom_level = saved_zoom
        if saved_zoom != 1.0:
            FontManager.font_string = FontManager.apply_zoom_to_font(base_font_string, saved_zoom)
        else:
            FontManager.font_string = base_font_string
        FontManager.propagate_font_setting()
        # 编辑器字体变化（含缩放）后，帮助文档 WebView 也用同一字体，需同步
        # 重新注入其 CSS（update_colors 仅颜色，不含字体，故单独刷新）。
        try: self.workspace.help_panel.update_colors()
        except AttributeError: pass

    def update_colors(self):
        # 自定义主题系统已移除，改为跟随系统 Libadwaita 调色板。
        # 自绘控件通过 ColorManager 的内置色回退取色。
        try: self.workspace.help_panel.update_colors()
        except AttributeError: pass

    def update_shortcuts_bar_visibility(self):
        show = self.settings.get_value('preferences', 'show_shortcuts_bar')
        self.main_window.shortcutsbar.set_visible(show)

    def update_tab_bar_visibility(self):
        '''按 show_tab_bar 偏好设置 Adw.TabBar 的可见性。

        两条路径：
        - 用户关闭：set_autohide(False) + set_visible(False) — 强制隐藏，
          1+ 个文档都不显示。
        - 用户打开：set_autohide(True) — 把显隐决定权交回 adw 自身（1 文档
          时自动隐藏、≥2 显示）。

        为什么不用 set_visible(True) 显式打开：autohide=True 时 adw 内部
        会根据页数自动 set_visible；外部再 set_visible(True) 会被 adw 的
        后续自动调整覆盖，反而引入闪烁。直接 set_autohide(True) 让 adw
        自己管，与文档数变化同步最干净。
        '''
        show = self.settings.get_value('preferences', 'show_tab_bar')
        tab_bar = self.main_window.document_tabs
        if show:
            tab_bar.set_autohide(True)
            # 不调 set_visible，让 autohide 自己根据页数决定。
        else:
            tab_bar.set_autohide(False)
            tab_bar.set_visible(False)

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



    # ---- Adw.TabView ↔ Workspace 双向同步 ----

    def _on_tab_view_selected_page_changed(self, tab_view, pspec):
        '''用户点击标签条触发的 selected-page::notify。
        抑制 _selecting 计数（presenter / actions 自己改的 set_selected_page 不应回环）。
        '''
        if self._selecting > 0:
            return
        page = tab_view.get_selected_page()
        if page is None:
            return
        document = self._page_to_doc.get(page)
        if document is None:
            return
        if document is self.workspace.get_active_document():
            return
        # 调 workspace.set_active_document 会触发 new_active_document signal
        # → on_new_active_document → set_selected_page 同一 page（_selecting
        # 抑制 notify 不会再发）。
        self.workspace.set_active_document(document)

    def _on_tab_view_close_page(self, tab_view, page):
        '''用户点关闭按钮 / 中键标签 / Alt+W 触发。
        委派给 actions.close_document(document)，统一走 push_closed_document
        + modified 检查 + confirm 对话框。
        '''
        document = self._page_to_doc.get(page)
        if document is None:
            # 找不到对应文档：放行让 adw 走默认 close（不会无限挂起页面）。
            tab_view.close_page_finish(page, True)
            return
        # 抑制 selected-page::notify（拆掉当前选中 page 时 adw 会发空 selected）。
        self._selecting += 1
        try:
            self.workspace.actions.close_document(document)
        finally:
            self._selecting -= 1

    def _refresh_page_label(self, document):
        '''把 document 的 displayname / modified 状态推到 Adw.TabPage.title。
        Adw.TabView 自带的 tabbar 渲染 page.title 为可见文字；modified 状态
        在 title 末尾追加「•」前缀（gedit 风格），保留 Adw 暗主题。
        '''
        page = self._doc_to_page.get(document)
        if page is None:
            return
        title = self._get_page_title(document)
        page.set_title(title)
        filename = document.get_filename()
        if filename is not None:
            page.set_tooltip(filename)
        # 根文档星标：用 page 的 indicator-activatable 标；indicator-icon 留
        # 空白图（default-icon 是 set_default_icon 设的统一图标）。这里更
        # 优雅的做法是 root 文档 page 改用 set_loading=False + needs_attention
        # 组合，但 gedit 风格里根文档没有特殊视觉（标题栏的星标已足够）。
        # 暂不附加 indicator。

    def _get_page_title(self, document):
        '''构造标签条显示文字。
        规则：displayname（包含未保存「•」前缀时本身已是「• File」），按
        gedit 风格保留原 displayname。modified 状态由 document.displayname
        已经反映（在 document.py 中加 '•' 前缀），不需要这里再加。
        '''
        return document.get_displayname()

    def _on_document_displayname_changed(self, document, value=None):
        self._refresh_page_label(document)

    def _on_document_modified_changed(self, document, value=None):
        # document.displayname 已经包含 dirty 状态前缀（document.py 处理），
        # 这里只刷 label 即可。如果未来想用 indicator-icon 显示独立 dirty
        # 标记，可在此切 page.set_indicator_activatable。
        self._refresh_page_label(document)

    def _on_document_is_root_changed(self, document, value=None):
        # 根文档变化：document.view 不会从 tab view 移走，但其它 page 的
        # parent 指针要刷（root 的 page 是它们的 parent）。简单实现：遍历
        # 所有 page 重 set parent。打开多个文档时只对根变化瞬间有效。
        if not self.workspace.open_documents:
            return
        root_doc = self.workspace.get_root_document()
        root_page = self._doc_to_page.get(root_doc) if root_doc is not None else None
        for doc, page in self._doc_to_page.items():
            if root_page is not None and doc is not root_doc:
                # adw.TabPage.parent 是只读，不能 set；只能拆掉重 add。
                # 这里用 hack：跳过 parent 重设（视觉差异不大：root 变化
                # 时其它标签不会自动重排层级）。需要的话后续可全量 rebuild。
                pass
