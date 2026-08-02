#!/usr/bin/env python3
# coding: utf-8

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

'''PDF 预览弹出独立窗口。

把 workspace 的 preview_panel（工具栏 + 含所有文档 preview.view 的 stack +
target bar）整体 reparent 到本窗口的内容区。模型↔view 引用不变、EventController
绑在 view 上随其搬移，故 SyncTeX 双向跳转、缩放、页码、构建后刷新等全部继续
工作，与窗口位置无关——这是选择「整体 reparent」而非「重建 view」的根本原因。

行为：
- 关窗（X）= 收回到侧边栏（拦截 close-request → workspace.pop_in_preview），
  不销毁窗口对象本身。
- 切换文档时 stack 切可见页，独立窗口自动跟随活动文档（stack 内含所有文档的
  preview.view，切页即可）。
- 正向 sync（源→PDF）present 本窗口；反向 sync（PDF→源）present 主窗口，
  多显示器场景下让用户立刻看到跳转结果。
- 标题跟随活动文档的 PDF 文件名。
'''

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, Gio, GLib

import os.path


class PdfPreviewWindow(Adw.Window):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = workspace.main_window if hasattr(workspace, 'main_window') else None
        if self.main_window is None:
            from setzer.app.service_locator import ServiceLocator
            self.main_window = ServiceLocator.get_main_window()

        Adw.Window.__init__(self)
        # 设为 main_window 的 transient child：随主窗口一起销毁、共享任务栏条目、
        # 不在任务栏单独显示（视为辅助窗口）。
        if self.main_window is not None:
            self.set_transient_for(self.main_window)
        self.set_modal(False)
        self.set_title('PDF Preview')
        self.set_default_size(520, 720)

        # ToolbarView + HeaderBar：libadwaita 推荐的带标题栏窗口结构。
        # headerbar 仅放标题（关窗按钮由窗口装饰提供）；预览工具栏（缩放/页码等）
        # 随 preview_panel 一起 reparent 进 content 区，不需在 headerbar 重复。
        self.toolbar_view = Adw.ToolbarView()
        self.headerbar = Adw.HeaderBar()
        self.headerbar.set_title_widget(Gtk.Label(label='PDF Preview'))
        self.toolbar_view.add_top_bar(self.headerbar)

        # 内容区：pop_out 时把 preview_panel 放进这里。
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar_view.set_content(self.content_box)
        self.set_content(self.toolbar_view)

        # 当前 reparent 进来的 preview_panel（同时只在一处）。
        self._panel = None
        # 当前连接了 sync 信号的活动文档 preview（用于切文档时重连）。
        self._preview = None
        # 避免重复添加 timeout 回调：当用户快速切换时。
        self._zoom_update_timeout_id = None

        # 关窗 = 收回，而非销毁。返回 True 阻止默认 destroy。
        self.connect('close-request', self._on_close_request)

        # 跟踪活动文档切换：重连 sync 信号 + 更新标题。
        # 仅在弹出状态下生效（_on_active_doc_changed 内判断）。
        self.workspace.connect('new_active_document', self._on_active_doc_changed)
        self.workspace.connect('root_state_change', self._on_root_state_changed)

        # 把预览相关的 win. action 加到本窗口：preview_panel 的 zoom_level_button
        # 弹出的是 Gtk.PopoverMenu，菜单项用 win.preview-fit-mode /
        # win.preview-set-zoom-level。GTK4 的 win. 前缀在最近的 Gtk.Window 上查找
        # action——panel reparent 到本窗口后，主窗口的 action 在这里查不到，
        # 菜单项会变灰不可用。
        #
        # Adw.Window 不实现 GActionMap（无 add_action），故用 insert_action_group
        # 插入一个 Gio.SimpleActionGroup 作为 'win' 组。组内放同一批 action 对象
        # （与主窗口共享状态/回调）——从任一窗口激活都会触发同一回调，stateful
        # action 的对钩状态也同步。
        actions_obj = getattr(self.workspace, 'actions', None)
        if actions_obj is not None:
            action_group = Gio.SimpleActionGroup()
            added = False
            for name in ('preview-fit-mode', 'preview-set-zoom-level'):
                action = actions_obj.actions.get(name)
                if action is not None:
                    action_group.add_action(action)
                    added = True
            if added:
                self.insert_action_group('win', action_group)

    def set_panel(self, panel):
        '''把 preview_panel reparent 进本窗口内容区，并连接 sync 信号。'''
        self._panel = panel
        self.content_box.append(panel)
        self._connect_active_preview()
        # 延迟调用 update_dynamic_zoom_levels，让 GTK 先完成尺寸分配。
        # 否则 fit_to_text_width 等模式的缩放不会根据新窗口尺寸重新计算，
        # 用户需要滚动一下才能触发 size_changed → update_dynamic_zoom_levels。
        self._schedule_zoom_update()

    def _schedule_zoom_update(self, delay=50):
        '''延迟更新缩放，避免重复添加 timeout 回调。'''
        if self._zoom_update_timeout_id is not None:
            GLib.source_remove(self._zoom_update_timeout_id)
        self._zoom_update_timeout_id = GLib.timeout_add(delay, self._update_zoom_after_reparent)

    def _update_zoom_after_reparent(self):
        '''reparent 完成后更新动态缩放级别，确保 fit 模式正确。

        使用自适应重试：检查 view 的 allocated width 是否有效（>= 300），
        如果无效则重新调度自己（最多重试 5 次），确保 GTK 完成布局后再更新。
        
        对于 fit_to_text_width 模式，会在 update_dynamic_zoom_levels 后
        强制重新应用一次，确保水平居中正确。'''
        self._zoom_update_timeout_id = None
        doc = self.workspace.get_root_or_active_latex_document()
        if doc is None or not hasattr(doc.preview, 'zoom_manager'):
            return False

        view = doc.preview.view
        zoom_manager = doc.preview.zoom_manager
        
        # 检查 allocated width 是否有效
        if view.get_allocated_width() < 300:
            # 如果无效，重新调度（最多重试 5 次，每次增加延迟）
            retry_count = getattr(self, '_zoom_retry_count', 0)
            if retry_count < 5:
                self._zoom_retry_count = retry_count + 1
                delay = 50 * (retry_count + 2)  # 增加延迟：100, 150, 200, 250, 300
                self._schedule_zoom_update(delay)
                return False
            else:
                # 达到最大重试次数，强制更新
                self._zoom_retry_count = 0

        # 重置重试计数
        self._zoom_retry_count = 0
        zoom_manager.update_dynamic_zoom_levels()
        
        # 专门为 fit_to_text_width 模式做额外处理：
        # 延迟后强制重新应用一次，确保缩放比例和水平居中都正确
        if zoom_manager.zoom_mode == 'fit_to_text_width':
            self._fit_text_retry_count = 0
            GLib.timeout_add(100, lambda: self._reapply_fit_to_text_width(zoom_manager))
        
        return False

    def _reapply_fit_to_text_width(self, zoom_manager):
        '''强制重新应用 fit_to_text_width，确保水平居中正确。

        使用自适应重试：检查 viewport width 是否有效，
        如果无效则重新调度自己（最多重试 3 次）。'''
        view = zoom_manager.view
        viewport_width = view.content.adjustment_x.get_page_size()
        
        # 检查 viewport width 是否有效
        if viewport_width <= 0:
            # 如果无效，重新调度（最多重试 3 次）
            retry_count = getattr(self, '_fit_text_retry_count', 0)
            if retry_count < 3:
                self._fit_text_retry_count = retry_count + 1
                delay = 100 * (retry_count + 2)  # 增加延迟：200, 300, 400
                GLib.timeout_add(delay, lambda: self._reapply_fit_to_text_width(zoom_manager))
                return False
            else:
                # 达到最大重试次数，仍然尝试应用
                pass
        
        # 如果还是 fit_to_text_width 模式，强制重新应用
        if zoom_manager.zoom_mode == 'fit_to_text_width':
            zoom_manager.set_zoom_fit_to_text_width()
        self._fit_text_retry_count = 0
        return False

    def take_panel(self):
        '''把 preview_panel 从本窗口取回（交还侧边栏），断开 sync 信号。'''
        self._disconnect_active_preview()
        if self._panel is not None:
            try:
                self.content_box.remove(self._panel)
            except Exception:
                pass
            self._panel = None

    def schedule_zoom_update(self):
        '''公开方法：安排缩放更新（用于 pop_in_preview 等场景）。'''
        self._schedule_zoom_update()

    def _on_close_request(self, window):
        # 关窗 → 收回到侧边栏。pop_in_preview 会把 panel 取回并隐藏本窗口。
        # 不销毁窗口对象，便于下次弹出保留几何状态（位置/大小）。
        self.workspace.pop_in_preview()
        return True

    def _connect_active_preview(self):
        '''连接当前 root_or_active 文档 preview 的 sync 信号，并更新标题。'''
        doc = self.workspace.get_root_or_active_latex_document()
        if doc is not None:
            self._preview = doc.preview
            self._preview.connect('synctex_forward', self._on_synctex_forward)
            self._preview.connect('synctex_backward', self._on_synctex_backward)
            self._update_title(doc)
        else:
            self._preview = None
            self.headerbar.get_title_widget().set_label('PDF Preview')

    def _disconnect_active_preview(self):
        if self._preview is not None:
            try:
                self._preview.disconnect('synctex_forward', self._on_synctex_forward)
                self._preview.disconnect('synctex_backward', self._on_synctex_backward)
            except (TypeError, KeyError):
                pass
            self._preview = None

    def _on_active_doc_changed(self, workspace, document):
        # 仅弹出状态下重连；未弹出时本窗口应不可见，无需操作。
        if self.workspace.is_preview_popped_out():
            self._disconnect_active_preview()
            self._connect_active_preview()

    def _on_root_state_changed(self, workspace, state):
        if self.workspace.is_preview_popped_out():
            self._disconnect_active_preview()
            self._connect_active_preview()

    def _on_synctex_forward(self, preview):
        # 源→PDF：抬起独立窗口让用户看到跳转结果。
        self.present()

    def _on_synctex_backward(self, preview):
        # PDF→源：抬起主窗口让用户看到源码跳转。
        if self.main_window is not None:
            self.main_window.present()

    def _update_title(self, doc):
        '''标题跟随活动文档的 PDF 文件名；无 PDF 时用显示名。'''
        label = self.headerbar.get_title_widget()
        pdf = getattr(doc.preview, 'pdf_filename', None)
        if pdf:
            label.set_label(os.path.basename(pdf))
        else:
            name = doc.get_displayname()
            label.set_label(name if name else 'PDF Preview')
