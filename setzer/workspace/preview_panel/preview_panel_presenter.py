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

from setzer.app.service_locator import ServiceLocator
from setzer.helpers import toast
from gi.repository import GLib


class PreviewPanelPresenter(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.view = self.main_window.preview_panel
        self.stack = self.main_window.preview_panel.stack
        self.document = None
        self._label_update_timeout_id = None

        # 当前已接线信号的 preview 对象。不能从 self.document 反查：目标是
        # 随时可变的，反查会 disconnect 到从未连接的对象，或在切换瞬间漏断
        # 旧对象两者都炸。
        self._connected_preview = None
        # 片段模式下正被跟踪的 TikzPreview（同时最多一个文档起编译器）。
        self._tracked_tikz = None
        # 'pdf'（整篇）| 'snippet'（片段图）。会话级状态，不落设置文件。
        self.mode = 'pdf'
        self._syncing_mode_switch = False
        # 帮助面板分段控件由 _sync_help_switch 回写时的抑制标志。
        self._syncing_help_switch = False

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('root_state_change', self.on_root_state_change)
        # Preview 可能晚于 new_document 才挂接（会话恢复的非活跃文档在激活
        # 时才建工具链）：latex_toolchain_ready 时补做 stack 挂载。
        self.workspace.connect('latex_toolchain_ready', self.on_latex_toolchain_ready)
        # 弹出/收回时刷新 detach 按钮可见性（popped_out 时隐藏——独立窗口已 detached）。
        self.workspace.connect('preview_pop_state_changed', self.on_preview_pop_state_changed)

        self.view.page_spin.connect('activate', self._on_page_spin_activate)
        self.view.fit_width_button.connect('clicked', self._on_fit_width_clicked)

        # 模式/面板切换（整篇 PDF / TikZ 片段图 / 帮助面板）与片段视图的 Retry。
        self.view.mode_switch.connect('notify::active', self._on_mode_switch_changed)
        self.view.snippet_view.retry_button.connect('clicked', self._on_snippet_retry_clicked)

        # 帮助面板工具栏的分段控件（与预览工具栏相同的三档）：点 pdf/snippet
        # 档切回预览（并顺带设好预览内部模式），help 档保持现状。
        self.main_window.help_panel.mode_switch.connect(
            'notify::active', self._on_help_switch_changed)

        # 反向挂到 view，便于其它地方（如 workspace_presenter 切换同步）
        # 通过 main_window.preview_panel.presenter 访问。
        self.view.presenter = self

        # 按实际显示的面板同步一次分段控件（图标档始终展示"目标面板"）。
        self._sync_switch_icons()
        self._sync_mode_switch()
        self._sync_mode_action_state()
        self.update_label()
        self.update_buttons()

        self.view.recolor_pdf_toggle.set_active(self.workspace.settings.get_value('preferences', 'recolor_pdf'))
        self.view.magnifier_toggle.set_active(self.workspace.settings.get_value('preferences', 'use_magnifier'))
        self.workspace.settings.connect('settings_changed', self.on_settings_changed)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'recolor_pdf':
            self.view.recolor_pdf_toggle.set_active(value)
        elif item == 'use_magnifier':
            self.view.magnifier_toggle.set_active(value)

    def on_new_document(self, workspace, document):
        if document.is_latex_document():
            # 工具链可能尚未挂接（会话恢复的非活跃轻量文档）：无 preview 则
            # 跳过，等 latex_toolchain_ready 再补挂 stack。
            doc_preview = getattr(document, 'preview', None)
            if doc_preview is not None:
                self.stack.add_child(doc_preview.view)

    def on_latex_toolchain_ready(self, workspace, document):
        '''延迟挂接的工具链就绪：补做 new_document 时被跳过的 stack 挂载。

        若该文档正是当前展示目标（如会话恢复时根文档在 set_active_document
        中补挂），还需刷新可见 child 与信号连接——set_preview_document 统一
        处理。'''
        if not document.is_latex_document():
            return
        doc_preview = getattr(document, 'preview', None)
        if doc_preview is not None and doc_preview.view.get_parent() is None:
            self.stack.add_child(doc_preview.view)
        if self.document is document:
            self.set_preview_document()

    def on_document_removed(self, workspace, document):
        if document.is_latex_document():
            # preview 可能不存在（从未激活过的轻量文档）或 view 从未加入
            # stack：两种情况都无需（也不能）从 stack 移除。
            doc_preview = getattr(document, 'preview', None)
            if doc_preview is not None and doc_preview.view.get_parent() is not None:
                self.stack.remove(doc_preview.view)
        # 被跟踪文档正在关闭：TikzPreview.shutdown 会自行清理，这里只丢引用，
        # 避免之后对已关闭对象调 set_tracking。
        if getattr(document, 'tikz_preview', None) is self._tracked_tikz:
            self._tracked_tikz = None

    def on_new_active_document(self, workspace, document):
        self.set_preview_document()

    def on_root_state_change(self, workspace, root_state):
        self.set_preview_document()

    def on_preview_pop_state_changed(self, workspace, popped_out):
        # 弹出后 preview_panel 搬进独立窗口，detach 按钮在那里无意义（收回走窗口 X）；
        # 收回后恢复可见。update_buttons 也会顺带刷新按钮敏感状态。
        self.view.detach_button.set_visible(not popped_out)
        # 弹出时禁用分段控件的 help 档（侧栏只显示 help，"切到帮助"无意义）
        # 并隐藏帮助面板的 switch 按钮；收回后恢复。
        self._sync_pop_state()
        self.update_buttons()

    def _preview_target(self):
        '''当前模式该展示的文档。

        PDF 模式取预览目标（显式 root / magic root / 活跃文档）：整篇论文
        的产物挂在 root 上，跳转子文件后仍展示 root 的 PDF。片段模式
        取「正在编辑的文件」——图在章节文件里，跟着 root 会让用户在图里
        打字却看不到反应（root 打开时 get_root_or_active 恒返回 root）。'''
        if self.mode == 'snippet':
            document = self.workspace.get_active_latex_document()
            if document is not None:
                return document
        return self.workspace.get_preview_target_document()

    def set_preview_document(self):
        # 切文档 / 切模式都要先停止对旧目标的片段跟踪：只有被展示的文档
        # 该起编译器。
        self._stop_snippet_tracking()

        if self._connected_preview is not None:
            self._connected_preview.disconnect('pdf_changed', self.on_pdf_changed)
            self._connected_preview.disconnect('position_changed', self.on_position_changed)
            self._connected_preview.disconnect('layout_changed', self.on_layout_changed)
            self._connected_preview.disconnect('pdf_stale_changed', self.on_pdf_stale_changed)
            self._connected_preview.disconnect('diff_state_changed', self.on_diff_state_changed)
            self._connected_preview.zoom_manager.disconnect('zoom_level_changed', self.on_zoom_level_changed)
            self._connected_preview.zoom_manager.disconnect('zoom_clamped', self.on_zoom_clamped)
            self._connected_preview.zoom_manager.disconnect('zoom_mode_changed', self.on_zoom_mode_changed)
            self._connected_preview = None

        document = self._preview_target()
        self._ensure_preview_toolchain(document)
        self.document = document

        if self.mode == 'snippet':
            self._show_snippet_document()
            return

        # 工具链仍未挂接（非 LaTeX 文档 / 已 shutdown）：无预览可展示。
        doc_preview = getattr(self.document, 'preview', None) if self.document != None else None
        if self.document == None or doc_preview is None:
            self.stack.set_visible_child(self.view.empty_placeholder)
            self.view.set_diff_toggle_visible(False)
            self.update_label()
            self.update_buttons()
            self._detach_target_bar()
        else:
            view = self.document.preview.view
            # 必须确保 view 已在 stack 里：set_visible_child 对非子控件只发
            # GTK 警告、不切换可见页，面板会停在别的文档的 PDF 或全空。
            if view.get_parent() is None:
                self.stack.add_child(view)
            self.stack.set_visible_child(view)
            # diff 档跟着目标文档走：切到没有 diff 的文档时档位消失，模式
            # 若还停在 diff 就必须落回 pdf（否则档位灭了但内容还是 diff PDF
            # —— 且那个 PDF 已随文档切换被替换掉了）。
            if self.mode == 'diff' and doc_preview.diff_result is None:
                self.mode = 'pdf'
                self._sync_mode_switch()
            self.view.set_diff_toggle_visible(doc_preview.diff_result is not None)
            self.update_label()
            self.update_buttons()
            self.update_zoom_level()
            self._sync_zoom_action_state()
            # 拦截重入造成的一次双连：目标文档恰是上一个目标时，
            # _ensure_preview_toolchain 里的 latex_toolchain_ready 会同步再走
            # 一遍本方法并完成连接，回到这里就没必要再连一次（否则每个信号
            # 都会被处理两遍）。
            if self._connected_preview is not doc_preview:
                doc_preview.connect('pdf_changed', self.on_pdf_changed)
                doc_preview.connect('position_changed', self.on_position_changed)
                doc_preview.connect('layout_changed', self.on_layout_changed)
                doc_preview.connect('pdf_stale_changed', self.on_pdf_stale_changed)
                doc_preview.connect('diff_state_changed', self.on_diff_state_changed)
                doc_preview.zoom_manager.connect('zoom_level_changed', self.on_zoom_level_changed)
                doc_preview.zoom_manager.connect('zoom_clamped', self.on_zoom_clamped)
                doc_preview.zoom_manager.connect('zoom_mode_changed', self.on_zoom_mode_changed)
                self._connected_preview = doc_preview
                self._attach_target_bar(view)

    def on_diff_state_changed(self, preview):
        '''diff 结果出现/消失：维护档位可见性，并在必要时从 diff 档落回。

        三种情形分开处理，别混成一条「不在 diff 视图就回 pdf」：
        * 结果没了（用户手改、文档重置）→ 档位移除 + 落回 pdf；
        * 结果还在但视图被顶回正式 PDF（新一轮构建到达时 preview 会自动
          干净地落回）→ 档位保留、模式落回 pdf，用户可以再切回去；
        * 正常在 diff 视图里（show_diff_pdf 自己发的这条）→ 什么都不做。
        '''
        available = preview.diff_result is not None
        self.view.set_diff_toggle_visible(available)
        if self.mode != 'diff':
            return
        if not available:
            self.set_mode('pdf')
        elif not preview.showing_diff_pdf:
            self.mode = 'pdf'
            self._sync_mode_switch()

    def _ensure_preview_toolchain(self, document):
        '''预览目标没有工具链（也就没有 PreviewView）时补挂一次。

        会话恢复的非活跃文档是轻量的（attach_preview=False），工具链要等它
        被激活才建。而预览目标未必是活跃文档：多文件工程里 root 才是目标，
        用户只点开了某个章节文件 —— 此时目标文档没有 PreviewView，stack 只能
        显示空白占位，哪怕 root 的 PDF 早就在磁盘上。切到该章节即补挂。

        `_attach_latex_toolchain` 经 latex_toolchain_ready 把 view 加进 stack
        （见 on_latex_toolchain_ready）。通常在切换路径上 self.document 还是旧
        值，那里的「目标即本文档就重走一遍」分支不会触发；但若目标没变（仅因
        root_state_change 重跑），会同步重入一次 set_preview_document，那条
        路径的连接由 `_connected_preview` 身份判定去重。'''
        if document is None or not document.is_latex_document():
            return
        if getattr(document, 'preview', None) is not None:
            return
        self.workspace._attach_latex_toolchain(document)
        # 「从未编译过的文档自动收起预览栏」的判据在挂接前一律为真（没有
        # build_system / preview）。若这次显隐评估排在本方法之前（root_state_change
        # 路径是同步的），侧栏已经被收起，而工具链就绪信号只补连 build_state、
        # 不动侧栏，就会一直收着。idle 延迟到当前信号链结束再重估，避免在
        # 布局分配期间操作 OverlaySplitView。
        workspace_presenter = getattr(self.workspace, 'presenter', None)
        if workspace_presenter is not None:
            GLib.idle_add(workspace_presenter.update_preview_help_visibility, False)

    # ---------- 模式（整篇 PDF / TikZ 片段图） ----------

    def get_mode(self):
        return self.mode

    def toggle_mode(self):
        self.set_mode('pdf' if self.mode == 'snippet' else 'snippet')

    def set_mode(self, mode):
        '''切换侧栏的展示对象（模式是会话级的，不持久化）。

        'diff' 比 'pdf'/'snippet' 窄一档：它不换 stack 子页（与 'pdf' 共用
        同一个 PDF 视图），只是让那个 Preview 以覆盖方式加载 diff 排版 PDF，
        所以**不**走 set_preview_document（那会重挂工具链、切 stack 子页）。
        缺 diff 结果时静默落回 pdf——档位理论上不该出现，但信号时序里可能
        先到一步（例如结果刚被清、档位还没被移除）。
        '''
        if mode == 'diff':
            if not self._diff_available():
                mode = 'pdf'
            else:
                # 档位可能还没建起来（面板刚连上这份文档、或结果是在没有
                # 面板连接时送达的）：先补上，否则下面的 _sync_mode_switch
                # 找不到 diff 档会把它降级回 pdf。
                self.view.set_diff_toggle_visible(True)
        if mode == self.mode:
            return
        previous = self.mode
        self.mode = mode
        self._sync_mode_switch()
        if mode == 'diff':
            preview = self._target_preview()
            if preview is not None:
                preview.show_diff_pdf()
            if preview is None or not preview.showing_diff_pdf:
                # diff PDF 打不开（被 phitex clean 掉/损坏）：落回 pdf，
                # 别让亮着的档位说谎。
                self.mode = 'pdf'
                self._sync_mode_switch()
            self._sync_mode_action_state()
            self.workspace.actions.update_actions()
            return
        if previous == 'diff':
            # 离开 diff 视图：切回正式 PDF。diff **结果**保留——用户只是
            # 去看别的内容，不是放弃审阅；放弃走横幅上的 Dismiss。
            preview = self._target_preview()
            if preview is not None:
                preview.show_main_pdf()
        self.set_preview_document()
        self._sync_mode_action_state()
        self.workspace.actions.update_actions()

    def _diff_available(self):
        '''当前预览目标是否有一份可切换查看的 diff 结果。'''
        preview = self._target_preview()
        return preview is not None and preview.diff_result is not None

    def _target_preview(self):
        '''预览目标文档的 Preview（多文件工程里是 root 的那份）。

        刻意用 get_preview_target_document 而非 self.document：片段模式下
        self.document 是正在编辑的子文件，而 diff 挂在产出整篇 PDF 的那份
        文档上（与 'pdf' 模式同一目标）。'''
        document = self.workspace.get_preview_target_document()
        return getattr(document, 'preview', None) if document is not None else None

    def _sync_mode_switch(self):
        '''把 self.mode 落到分段控件上（按**档位名**定位，不按索引）。

        name 定位是刚需：diff 档按需增删，索引会随它漂移。set_active_name
        对未知名字会打 CRITICAL 并拒绝赋值，所以档位不存在时先降级到 pdf
        ——这条同时兜住"结果已清但档位还没被移除"的短暂窗口。
        '''
        name = self.mode
        if self.view.mode_switch.get_toggle_by_name(name) is None:
            name = 'pdf'
            self.mode = 'pdf'
        self._syncing_mode_switch = True
        try:
            self.view.mode_switch.set_active_name(name)
        finally:
            self._syncing_mode_switch = False

    def _on_mode_switch_changed(self, group, parameter):
        if self._syncing_mode_switch:
            return
        name = group.get_active_name()
        if name is None:
            # 全灭兜底：get_active_name() 无选中时返回 None（get_active() 则
            # 返回 G_MAXUINT）。ToggleGroup 允许全灭，拨回当前档，恒有一档亮。
            self._sync_mode_switch()
            return
        if name == 'help':
            # help 档：整个右侧边栏切到帮助面板（合并的原 switch to help）。
            self._sync_mode_switch()
            self.workspace.set_show_preview_or_help(False, True)
            return
        self.set_mode(name)

    def _sync_mode_action_state(self):
        action = self.workspace.actions.actions.get('toggle-preview-snippet-mode')
        if action is not None:
            action.set_state(GLib.Variant.new_boolean(self.mode == 'snippet'))

    def _show_snippet_document(self):
        '''片段模式的可见子与跟踪目标（目标是 self.document，即正在编辑的
        文件，由 _preview_target 按模式解析）。'''
        tikz = getattr(self.document, 'tikz_preview', None) if self.document != None else None
        self._detach_target_bar()
        if tikz is None:
            self.view.snippet_view.set_source(None)
            self.stack.set_visible_child(self.view.empty_placeholder)
        else:
            # 先让视图渲染当前 state，再开跟踪——set_tracking(True) 会立即
            # 评估一次，评估结果经信号推回视图。
            self.view.snippet_view.set_source(tikz)
            tikz.connect('tikz_preview_state_changed', self.on_snippet_state_changed)
            self._tracked_tikz = tikz
            tikz.set_tracking(True)
            self.stack.set_visible_child(self.view.snippet_view)
        self.update_label()
        self.update_buttons()

    def _stop_snippet_tracking(self):
        if self._tracked_tikz is not None:
            self._tracked_tikz.set_tracking(False)
            self._tracked_tikz.disconnect('tikz_preview_state_changed', self.on_snippet_state_changed)
            self._tracked_tikz = None

    def on_snippet_state_changed(self, tikz):
        '''帧的有无决定 zoom± 的敏感状态（画面本身由片段视图自渲染）。'''
        has_frame = tikz.state.texture is not None
        self.view.zoom_in_button.set_sensitive(has_frame)
        self.view.zoom_out_button.set_sensitive(has_frame)

    def _on_snippet_retry_clicked(self, button):
        if self._tracked_tikz is not None:
            self._tracked_tikz.retry()

    def zoom_in(self):
        '''工具栏 zoom+ 的公共入口：片段模式路由到片段视图。'''
        if self.mode == 'snippet':
            if not self.view.snippet_view.zoom_in():
                self._show_zoom_toast('in')
            return
        document = self.workspace.get_preview_target_document()
        doc_preview = getattr(document, 'preview', None) if document is not None else None
        if doc_preview is not None:
            doc_preview.zoom_manager.zoom_in()

    def zoom_out(self):
        if self.mode == 'snippet':
            if not self.view.snippet_view.zoom_out():
                self._show_zoom_toast('out')
            return
        document = self.workspace.get_preview_target_document()
        doc_preview = getattr(document, 'preview', None) if document is not None else None
        if doc_preview is not None:
            doc_preview.zoom_manager.zoom_out()

    def on_pdf_changed(self, preview):
        self.update_label()
        self.update_buttons()
        self._sync_zoom_action_state()

    def on_position_changed(self, preview):
        self.update_label_debounced()

    def on_layout_changed(self, preview):
        self.update_label()

    def on_pdf_stale_changed(self, preview):
        # 构建失败未产出 PDF 时在页码区显示红色提示；下次构建成功（set_pdf_filename
        # 清除 stale）时隐藏。由 preview.set_pdf_is_stale 触发。
        self.update_label()
        self.update_buttons()

    def on_zoom_level_changed(self, preview):
        self.update_label()
        self.update_buttons()
        self.update_zoom_level()
        self._sync_zoom_action_state()

    def on_zoom_mode_changed(self, zoom_manager):
        self._sync_fit_width_button()

    def _sync_fit_width_button(self):
        '''fit_to_width 模式（本按钮点击后进入的模式）下按钮呈按下（checked）
        态；text-width / height 两个 fit 模式不亮，与弹窗菜单里的独立档位一致。'''
        if self.mode == 'snippet':
            return
        doc_preview = getattr(self.document, 'preview', None) if self.document != None else None
        if doc_preview is None:
            self.view.fit_width_button.set_active(False)
            return
        self.view.fit_width_button.set_active(doc_preview.zoom_manager.zoom_mode == 'fit_to_width')

    def _sync_zoom_action_state(self):
        '''同步两个有状态 action 的 state，使弹窗里「当前缩放档位」或「当前 fit
        模式」前自动显示对钩（GTK 标准做法），二者互斥：
        - fit 模式激活时，fit 项高亮、数值项不亮；
        - 手动缩放（数值档位）时，对应数值项高亮、fit 项不亮。

        片段模式下两个 action 都被禁用（弹窗与命令面板都够不着），保持原
        state 即可，切回 PDF 时由 set_preview_document 重同步。'''
        if self.mode == 'snippet':
            return
        zoom_action = self.workspace.actions.actions.get('preview-set-zoom-level')
        fit_action = self.workspace.actions.actions.get('preview-fit-mode')
        if self.document is None:
            return
        zoom_manager = getattr(self.document, 'preview', None)
        if zoom_manager is None:
            return
        zoom_manager = zoom_manager.zoom_manager
        zoom_level = zoom_manager.get_zoom_level()
        mode = zoom_manager.zoom_mode

        # 与弹窗菜单一致的离散缩放档位。
        levels = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0]
        mode_map = {
            'fit_to_width': 'width',
            'fit_to_text_width': 'text-width',
            'fit_to_height': 'height',
        }

        if mode in mode_map:
            if fit_action is not None:
                fit_action.set_state(GLib.Variant('s', mode_map[mode]))
            # fit 模式下不亮任何数值项：把数值 state 设为不在档位中的哨兵值。
            if zoom_action is not None and zoom_level is not None:
                zoom_action.set_state(GLib.Variant('d', -1.0))
        else:
            if fit_action is not None:
                fit_action.set_state(GLib.Variant('s', 'none'))
            current = -1.0
            if zoom_level is not None:
                for level in levels:
                    if abs(zoom_level - level) < 1e-6:
                        current = level
                        break
            if zoom_action is not None:
                zoom_action.set_state(GLib.Variant('d', current))

    def on_zoom_clamped(self, zoom_manager, direction):
        self._show_zoom_toast(direction)

    def _show_zoom_toast(self, direction):
        if direction == 'in':
            message = _('Maximum zoom level reached')
        else:
            message = _('Minimum zoom level reached')
        # 滚轮/捏合到顶后继续操作会一直命中同一句：节流，别每帧弹一次。
        toast.show(message, timeout=2)

    def update_label_debounced(self):
        '''滚动时 position_changed 高频触发（每帧）。页码标签只需秒级精度，
        用 150ms debounce 合并连续滚动事件，避免对 500 页 PDF 频繁调用
        get_n_pages / get_page_by_offset。'''
        if self._label_update_timeout_id is not None:
            GLib.source_remove(self._label_update_timeout_id)
        self._label_update_timeout_id = GLib.timeout_add(150, self._do_update_label)

    def _do_update_label(self):
        self._label_update_timeout_id = None
        self.update_label()
        return False

    def update_label(self):
        if self.mode == 'snippet':
            # 片段模式没有页码；编译状态与失败原因显示在片段视图自身的
            # 状态层（叠在图上，窄侧栏也看得见），工具栏不再占位。
            self.view.page_spin.set_visible(False)
            self.view.paging_of_label.set_visible(False)
            self.view.stale_label.set_text('')
            return
        if self.document == None:
            self.view.page_spin.set_visible(False)
            self.view.paging_of_label.set_visible(False)
            self.view.stale_label.set_text('')
        else:
            preview = getattr(self.document, 'preview', None)
            if preview is None:
                # 工具链未挂接：与「无文档」同样显示占位。
                self.view.page_spin.set_visible(False)
                self.view.paging_of_label.set_visible(False)
                self.view.stale_label.set_text('')
                return
            if preview.pdf_is_stale and not self._hide_stale_banner(self.document):
                self.view.stale_label.set_text(_('Build failed — showing the previous PDF'))
            else:
                self.view.stale_label.set_text('')
            self.view.page_spin.set_visible(True)
            self.view.paging_of_label.set_visible(True)
            if preview.poppler_document != None:
                total = preview.poppler_document.get_n_pages()
                if preview.layout != None:
                    offset = preview.view.content.scrolling_offset_y
                    current = preview.layout.get_page_by_offset(offset)
                else:
                    current = 1
                self.view.page_spin.set_range(1, max(total, 1))
                self.view.page_spin.set_value(current)
                self.view.paging_of_label.set_text(_('of {total}').format(total=total))
            else:
                self.view.paging_of_label.set_text(_('No preview'))

    @staticmethod
    def _hide_stale_banner(document):
        '''PhiTeX 常驻自动构建下不显示"构建失败，显示的是上一版 PDF"横幅。

        这种模式里自动编译被强制打开、每 150ms 一轮，用户敲到半个命令就是
        一次失败：横幅会在"失败/恢复"之间每秒闪合好几次，正好挡在页码区。
        错误本身已有行内波浪线、gutter 色条与按钮区失败计数三处表达，这条
        横幅在这种节奏下只剩噪音。手动构建（F5）与其它引擎照旧显示。
        '''
        build_system = getattr(document, 'build_system', None)
        if build_system is None:
            return False
        try:
            return (bool(getattr(build_system, 'is_auto_build', False))
                    and bool(build_system.is_phitex_live_build()))
        except Exception:
            return False

    def _sync_target_document(self):
        '''目标文档变了就走 set_preview_document 的完整重接线路径。

        update_buttons 被「文档切换」之外的路径频繁调用（构建状态、弹出
        状态、设置变化），这些调用也可能顺带把目标文档推进一格：只重算
        按钮可见性会漏接信号。目标按模式解析（见 _preview_target）。'''
        if self._preview_target() is not self.document:
            self.set_preview_document()

    def update_buttons(self):
        self._sync_target_document()
        doc_preview = getattr(self.document, 'preview', None) if self.document != None else None
        has_pdf = doc_preview is not None and doc_preview.poppler_document != None
        snippet_mode = self.mode == 'snippet'

        self.view.toolbar.set_visible(True)
        # 片段模式下这些按钮的语义都是「对整篇 PDF 操作」：一并隐藏，
        # 只留两档切换、zoom±、detach 与切到 Help。
        self.view.external_viewer_button.set_visible(not snippet_mode)
        self.view.recolor_pdf_toggle.set_visible(not snippet_mode)
        self.view.magnifier_toggle.set_visible(not snippet_mode)
        self.view.zoom_out_button.set_visible(True)
        self.view.fit_width_button.set_visible(not snippet_mode)
        self.view.zoom_level_button.set_visible(not snippet_mode)
        self.view.zoom_in_button.set_visible(True)
        # 弹出状态下隐藏 detach 按钮（preview_panel 已在独立窗口内，收回走窗口 X）。
        self.view.detach_button.set_visible(not self.workspace.is_preview_popped_out())
        # 同步分段控件 help 档与帮助面板 switch 按钮的弹出态可见性。
        self._sync_pop_state()

        if snippet_mode:
            # 有帧才谈得上缩放；上下限由视图钳制并弹 toast 提示。
            has_frame = self.view.snippet_view.has_frame()
            self.view.zoom_in_button.set_sensitive(has_frame)
            self.view.zoom_out_button.set_sensitive(has_frame)
            self.view.page_spin.set_sensitive(False)
            self._sync_fit_width_button()
            return

        self.view.external_viewer_button.set_sensitive(has_pdf)
        self.view.recolor_pdf_toggle.set_sensitive(has_pdf)
        self.view.magnifier_toggle.set_sensitive(has_pdf)
        self.view.zoom_out_button.set_sensitive(has_pdf)
        self.view.fit_width_button.set_sensitive(has_pdf)
        self.view.zoom_level_button.set_sensitive(has_pdf)
        self.view.zoom_in_button.set_sensitive(has_pdf)
        self.view.page_spin.set_sensitive(has_pdf)

        if has_pdf:
            zoom_level = doc_preview.zoom_manager.get_zoom_level()
            self.view.zoom_in_button.set_sensitive(zoom_level != None and zoom_level < 4)
            self.view.zoom_out_button.set_sensitive(zoom_level != None and zoom_level > 0.25)

        self._sync_fit_width_button()

    def update_zoom_level(self):
        doc_preview = getattr(self.document, 'preview', None) if self.document != None else None
        if doc_preview is None:
            return
        zoom_level = doc_preview.zoom_manager.get_zoom_level()

        if zoom_level != None:
            self.view.zoom_level_label.set_text('{0:.1f}%'.format(zoom_level * 100))

    def _on_page_spin_activate(self, spin_button):
        if self.mode == 'snippet':
            return
        page_number = int(spin_button.get_value())
        preview = getattr(self.document, 'preview', None) if self.document != None else None
        if preview is None:
            return
        if preview.layout == None or preview.poppler_document == None:
            return
        total = preview.poppler_document.get_n_pages()
        page_number = max(1, min(page_number, total))
        content = preview.view.content
        # per-page：用 get_page_top 取第 N 页顶部（已含 vertical_padding）。
        y = preview.layout.get_page_top(page_number - 1)
        if y is None:
            return
        preview.scroll_to_position(content.scrolling_offset_x, y)

    def _on_fit_width_clicked(self, button):
        if self.mode == 'snippet':
            return
        if self.document == None:
            return
        doc_preview = getattr(self.document, 'preview', None)
        if doc_preview is None:
            return
        doc_preview.zoom_manager.set_zoom_fit_to_width_auto_offset()
        # 已处于 fit_to_width 时模式未变、不发 zoom_mode_changed，而 ToggleButton
        # 的 clicked 默认处理已把 active 翻成 False，故此处无条件回写一次。
        self._sync_fit_width_button()

    def _sync_switch_icons(self):
        '''按当前显示的面板，同步两侧的分段控件与帮助面板 switch 档。

        - 预览在侧栏显示时：预览工具栏 group 停在当前模式档（pdf/snippet），
          help 档可用；帮助面板 group 同步亮同一模式档（此时它不在屏幕上，
          只是为了切回时状态正确）。
        - 帮助面板在侧栏显示时：两个 group 的 help 档亮起；预览 group 的
          help 档禁用（点击无意义——已在 Help）；帮助面板 group 全档可用，
          点 pdf/snippet 即切回预览。
        '''
        visible_name = self.main_window.preview_help_stack.get_visible_child_name()
        showing_help = visible_name != 'preview'
        self._sync_help_switch(showing_help)
        self._sync_pop_state()

    def _sync_help_switch(self, showing_help):
        '''同步帮助面板的分段控件：帮助面板显示时亮 help 档，否则亮当前
        预览模式档。set_active_name 触发的 notify 回调由 _syncing_help_switch
        压掉，不会反向触发切换。

        按 name 定位（与预览那组一致）：帮助面板这组固定三档、没有 diff 档，
        所以 diff 模式归到 pdf 档——diff 视图本来就是整篇 PDF 的另一种加载。'''
        self._syncing_help_switch = True
        try:
            if showing_help:
                name = 'help'
            else:
                name = 'pdf' if self.mode in ('pdf', 'diff') else 'snippet'
            self.main_window.help_panel.mode_switch.set_active_name(name)
        finally:
            self._syncing_help_switch = False

    def _on_help_switch_changed(self, group, parameter):
        '''帮助面板分段控件的档位变化。

        help 档：已在此面板，同步回亮即可。pdf/snippet 档：把整个右侧边栏
        切回预览，并设好预览内部模式。若这次变化是 _sync_help_switch 自己
        写出的（_syncing_help_switch），直接忽略。'''
        if self._syncing_help_switch:
            return
        name = group.get_active_name()
        if name is None or name == 'help':
            # 全灭兜底（get_active_name 无选中返回 None）与 help 档同一处理：
            # 拨回 help 档，恒有一档亮着。
            self._sync_help_switch(True)
            return
        # 切回预览并选中对应内部模式；set_mode 内部会 _sync_mode_switch。
        self.set_mode('pdf' if name == 'pdf' else 'snippet')
        self.workspace.set_show_preview_or_help(True, False)

    def _sync_pop_state(self):
        '''预览弹出/收回后的两个分段控件同步。

        弹出时：预览 group 的 help 档禁用（侧栏只显示 help，"切到帮助"
        无意义），帮助面板 group 整体禁用（侧栏上根本没有帮助面板，无面板
        可切）。收回时恢复。'''
        popped_out = self.workspace.is_preview_popped_out()
        self.view.help_toggle.set_enabled(not popped_out)
        self.main_window.help_panel.mode_switch.set_sensitive(not popped_out)

    def _attach_target_bar(self, preview_view):
        revealer = preview_view.target_label_revealer
        parent = revealer.get_parent()
        if parent is not None:
            parent.remove(revealer)
        self.view.target_bar_placeholder.append(revealer)

    def _detach_target_bar(self):
        placeholder = self.view.target_bar_placeholder
        child = placeholder.get_first_child()
        if child is not None:
            placeholder.remove(child)
