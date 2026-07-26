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
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, Adw
from gi.repository import GtkSource

from setzer.app.service_locator import ServiceLocator


# 编辑器配色方案预览样例：用 LaTeX 代码（而非 Markdown），因为 Setzer 是
# LaTeX 编辑器。样例刻意覆盖 latex.lang 的多种高亮样式——注释、文档/宏包
# 命令、章节、文本格式命令、verbatim、环境、行内/独立公式、数学命令、
# 上下标、特殊字符——以便配色方案预览能充分展示各语法元素的配色。
LATEX_PREVIEW_TEXT = (
    '% Sample LaTeX document\n'
    '\\documentclass{article}\n'
    '\\usepackage{amsmath}\n'
    '\\section{Introduction}\n'
    'Text \\textbf{bold}, \\emph{italic} and\n'
    '\\verb|code| inline.\n'
    '\\begin{equation}\n'
    '  E = mc^2 + \\alpha_i\n'
    '\\end{equation}\n'
)

# 预览样例表（按语言 ID 索引）；当前仅 LaTeX 一项，预留扩展。
LANG_PREVIEWS = {
    'latex': LATEX_PREVIEW_TEXT,
}


class PageEditor(object):

    def __init__(self, preferences, settings):
        self.view = PageEditorView()
        self.preferences = preferences
        self.settings = settings
        self._flowbox_previews = []  # (GtkSource.StyleSchemePreview, scheme_id_or_'')
        # 用户点击网格 tile 时（on_scheme_activated）会写回设置并同步触发
        # settings_changed；为避免该回调重建整个网格造成闪烁，临时屏蔽重建。
        self._suppress_scheme_rebuild = False

    def init(self):
        self.view.reset_button.connect('clicked', self.on_reset_clicked)

        # 编辑器配色方案（复刻 gnome-text-editor Appearance 分组）：
        # 顶部 Markdown 预览 + 方案平铺网格。仅列与当前 Adw 主题同深浅的方案。
        self.view.scheme_flowbox.connect('child-activated', self.on_scheme_activated)
        self.populate_scheme_flowbox()
        self.setup_preview_buffer()
        # 系统主题（或「System」模式下 OS 深浅）变化时重建网格候选与预览配色。
        try:
            Adw.StyleManager.get_default().connect('notify::dark',
                lambda *a: (self.populate_scheme_flowbox(), self.apply_preview_scheme()))
        except Exception:
            pass
        # 外部写回 editor_style_scheme（如 Appearance 页「Reset to Defaults」）
        # 时重建网格并刷新预览配色，保持 Editor 页网格与实际设置同步。
        self.settings.connect('settings_changed', self.on_settings_changed)

        self.view.option_spaces_instead_of_tabs.set_active(self.settings.get_value('preferences', 'spaces_instead_of_tabs'))
        self.view.option_spaces_instead_of_tabs.connect('notify::active', self.on_switch_toggled, 'spaces_instead_of_tabs')

        self.view.tab_width_spinbutton.set_property('value', self.settings.get_value('preferences', 'tab_width'))
        self.view.tab_width_spinbutton.connect('notify::value', self.preferences.spin_button_changed, 'tab_width')

        self.view.option_show_line_numbers.set_active(self.settings.get_value('preferences', 'show_line_numbers'))
        self.view.option_show_line_numbers.connect('notify::active', self.on_switch_toggled, 'show_line_numbers')

        self.view.option_line_wrapping.set_active(self.settings.get_value('preferences', 'enable_line_wrapping'))
        self.view.option_line_wrapping.connect('notify::active', self.on_switch_toggled, 'enable_line_wrapping')

        self.view.option_code_folding.set_active(self.settings.get_value('preferences', 'enable_code_folding'))
        self.view.option_code_folding.connect('notify::active', self.on_switch_toggled, 'enable_code_folding')

        self.view.option_highlight_current_line.set_active(self.settings.get_value('preferences', 'highlight_current_line'))
        self.view.option_highlight_current_line.connect('notify::active', self.on_switch_toggled, 'highlight_current_line')

        self.view.option_highlight_matching_brackets.set_active(self.settings.get_value('preferences', 'highlight_matching_brackets'))
        self.view.option_highlight_matching_brackets.connect('notify::active', self.on_switch_toggled, 'highlight_matching_brackets')

        self.view.option_auto_save_enabled.set_active(self.settings.get_value('preferences', 'auto_save_enabled'))
        self.view.option_auto_save_enabled.connect('notify::active', self.on_switch_toggled, 'auto_save_enabled')

        self.view.option_auto_reload_on_external_change.set_active(
            self.settings.get_value('preferences', 'auto_reload_on_external_change'))
        self.view.option_auto_reload_on_external_change.connect(
            'notify::active', self.on_switch_toggled, 'auto_reload_on_external_change')

        self.view.auto_save_delay_row.set_property('value', self.settings.get_value('preferences', 'auto_save_delay'))
        self.view.auto_save_delay_row.connect('notify::value', self.preferences.spin_button_changed, 'auto_save_delay')


    def on_switch_toggled(self, switch, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, switch.get_active())

    # ---- editor color scheme grid (复刻 gnome-text-editor) ----
    def _current_dark(self):
        '''当前 Adw 是否为深色主题，用于过滤方案网格。'''
        try:
            return Adw.StyleManager.get_default().get_dark()
        except AttributeError:
            return False

    def populate_scheme_flowbox(self):
        '''重建编辑器配色方案网格（列出全部方案，不过滤深浅）。

        首项为「Follow system theme」合成 tile（scheme_id=''），其余为
        StyleSchemeManager 中的所有真实方案——**不**按当前 Adw 深浅主题过滤，
        以便用户可在深色应用下选用浅色编辑器配色（或反之），恢复旧版 Setzer
        「任意组合 app 主题与编辑器配色」的能力。

        说明：gnome-text-editor 参考项目会按当前主题只列同深浅方案；但 Setzer
        用户习惯能独立选择，故这里列出全部。重建前清空旧 children。
        '''
        while True:
            child = self.view.scheme_flowbox.get_first_child()
            if child is None:
                break
            self.view.scheme_flowbox.remove(child)
        self._flowbox_previews = []

        current_scheme = self.settings.get_value('preferences', 'editor_style_scheme')
        dark = self._current_dark()

        # 1) Follow system theme —— 用当前系统默认方案渲染预览 tile。
        system_scheme_id = 'default-dark' if dark else 'default'
        system_scheme = ServiceLocator.get_source_style_scheme_manager().get_scheme(system_scheme_id)
        if system_scheme is not None:
            preview = GtkSource.StyleSchemePreview.new(system_scheme)
            self._add_flowbox_child(preview, '')

        # 2) 全部真实方案（不过滤深浅，允许用户任意组合 app 与编辑器配色）。
        scheme_manager = ServiceLocator.get_source_style_scheme_manager()
        for scheme_id in scheme_manager.get_scheme_ids():
            if scheme_id in ('default', 'default-dark'):
                continue
            scheme = scheme_manager.get_scheme(scheme_id)
            if scheme is None:
                continue
            preview = GtkSource.StyleSchemePreview.new(scheme)
            self._add_flowbox_child(preview, scheme_id)

        self.update_scheme_selection(current_scheme)

    def _add_flowbox_child(self, preview, scheme_id):
        preview.set_selected(False)
        self.view.scheme_flowbox.insert(preview, -1)
        self._flowbox_previews.append((preview, scheme_id))

    def update_scheme_selection(self, current_scheme):
        '''高亮与 current_scheme 匹配的 tile（'' 匹配 Follow system 项）。
        仅对状态实际变化的 tile 调用 set_selected，避免不必要的重绘闪烁。'''
        for preview, scheme_id in self._flowbox_previews:
            selected = (scheme_id == current_scheme)
            if preview.get_selected() != selected:
                preview.set_selected(selected)

    def on_scheme_activated(self, flowbox, child):
        # child 是 Gtk.FlowBoxChild，其单子为 StyleSchemePreview。
        preview = child.get_child()
        scheme_id = ''
        for p, sid in self._flowbox_previews:
            if p is preview:
                scheme_id = sid
                break
        # set_style_scheme_name 会同步触发 settings_changed -> on_settings_changed，
        # 该回调默认会重建整个网格（remove+insert 所有 tile），造成切换闪烁。
        # 激活场景下无需重建，仅更新选中态与预览配色即可，故临时屏蔽重建。
        self._suppress_scheme_rebuild = True
        try:
            # 写回设置（清空缓存 + set_value），驱动已打开文档重应用配色。
            ServiceLocator.set_style_scheme_name(scheme_id)
        finally:
            self._suppress_scheme_rebuild = False
        self.update_scheme_selection(scheme_id)
        self.apply_preview_scheme()

    def setup_preview_buffer(self):
        '''初始化 LaTeX 预览缓冲区：语法高亮 + 文本 + 选中第 3 行（同参考）。'''
        buffer_ = GtkSource.Buffer()
        lang_manager = GtkSource.LanguageManager()
        lang = lang_manager.get_language('latex')
        buffer_.set_language(lang)
        buffer_.set_highlight_syntax(True)
        text = LANG_PREVIEWS.get('latex', LATEX_PREVIEW_TEXT)
        buffer_.set_text(text)
        self.view.preview_source_view.set_buffer(buffer_)
        # get_iter_at_line 的 PyGObject 绑定返回 (found, iter) 元组，取 [1]。
        if buffer_.get_line_count() >= 3:
            start = buffer_.get_iter_at_line(2)[1]
            end = buffer_.get_iter_at_line(3)[1]
            buffer_.select_range(start, end)
        self.apply_preview_scheme()

    def apply_preview_scheme(self):
        '''把当前生效的编辑器配色应用到预览缓冲区。'''
        scheme = ServiceLocator.get_style_scheme()
        if scheme is not None:
            self.view.preview_source_view.get_buffer().set_style_scheme(scheme)

    def on_settings_changed(self, settings, parameter):
        # settings_changed 参数为 (section, item, value) 元组。
        # 仅当编辑器配色方案被外部写回时重建网格（如 Appearance 页重置）。
        # 忽略其它设置变更，避免无谓重建。
        # 用户点击网格 tile 时已在 on_scheme_activated 中临时屏蔽重建，
        # 此处仅在外部写回（如 Appearance 页「Reset to Defaults」）时重建。
        if self._suppress_scheme_rebuild:
            return
        section, item, value = parameter
        if section == 'preferences' and item == 'editor_style_scheme':
            self.populate_scheme_flowbox()
            self.apply_preview_scheme()

    def on_reset_clicked(self, button):
        defaults = self.settings.defaults['preferences']
        # editor_style_scheme 默认 '' = 跟随系统主题；写回后重建网格并刷新预览。
        ServiceLocator.set_style_scheme_name('')
        self.populate_scheme_flowbox()
        self.apply_preview_scheme()
        self.view.option_spaces_instead_of_tabs.set_active(defaults['spaces_instead_of_tabs'])
        self.view.tab_width_spinbutton.set_property('value', defaults['tab_width'])
        self.view.option_show_line_numbers.set_active(defaults['show_line_numbers'])
        self.view.option_line_wrapping.set_active(defaults['enable_line_wrapping'])
        self.view.option_code_folding.set_active(defaults['enable_code_folding'])
        self.view.option_highlight_current_line.set_active(defaults['highlight_current_line'])
        self.view.option_highlight_matching_brackets.set_active(defaults['highlight_matching_brackets'])
        self.view.option_auto_save_enabled.set_active(defaults['auto_save_enabled'])
        self.view.auto_save_delay_row.set_property('value', defaults['auto_save_delay'])
        self.view.option_auto_reload_on_external_change.set_active(
            defaults['auto_reload_on_external_change'])


class PageEditorView(Adw.PreferencesPage):

    def __init__(self):
        Adw.PreferencesPage.__init__(self)
        self.set_title(_('Editor'))
        self.set_icon_name('accessories-text-editor-symbolic')

        # Appearance 分组（复刻 gnome-text-editor）：顶部 Markdown 预览 +
        # 下方 Gtk.FlowBox 的编辑器配色方案平铺网格。置于 Editor 页最前，
        # 与参考项目一致——配色选择属于编辑器外观设置。
        group_appearance = Adw.PreferencesGroup()
        group_appearance.set_title(_('Appearance'))
        self.add(group_appearance)

        # Markdown 预览：只读、等宽、显示行号、card 类（参考项目 preview+card），
        # 并显式设置与参考一致的边距（上下 8、左右 12，底部额外 24 与网格分隔）。
        self.preview_source_view = GtkSource.View()
        self.preview_source_view.set_editable(False)
        self.preview_source_view.set_cursor_visible(False)
        self.preview_source_view.set_monospace(True)
        self.preview_source_view.set_show_line_numbers(True)
        self.preview_source_view.set_top_margin(8)
        self.preview_source_view.set_bottom_margin(8)
        self.preview_source_view.set_left_margin(12)
        self.preview_source_view.set_right_margin(12)
        self.preview_source_view.set_margin_bottom(24)
        self.preview_source_view.add_css_class('card')
        self.preview_source_view.add_css_class('scheme-preview')
        self.preview_source_view.add_css_class('preview')
        self.preview_source_view.set_size_request(-1, 140)
        group_appearance.add(self.preview_source_view)

        # 方案平铺网格：每行最多 4 个，不可框选（点击直接应用）。
        self.scheme_flowbox = Gtk.FlowBox()
        self.scheme_flowbox.add_css_class('scheme-flowbox')
        self.scheme_flowbox.set_hexpand(True)
        self.scheme_flowbox.set_column_spacing(12)
        self.scheme_flowbox.set_row_spacing(12)
        self.scheme_flowbox.set_max_children_per_line(4)
        self.scheme_flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        group_appearance.add(self.scheme_flowbox)

        group_tab_stops = Adw.PreferencesGroup()
        group_tab_stops.set_title(_('Tab Stops'))
        self.add(group_tab_stops)

        self.option_spaces_instead_of_tabs = Adw.SwitchRow()
        self.option_spaces_instead_of_tabs.set_title(_('Insert spaces instead of tabs'))
        group_tab_stops.add(self.option_spaces_instead_of_tabs)

        self.tab_width_row = Adw.SpinRow()
        self.tab_width_row.set_title(_('Tab Width'))
        adjustment = Gtk.Adjustment(value=1, lower=1, upper=8, step_increment=1)
        self.tab_width_row.set_adjustment(adjustment)
        group_tab_stops.add(self.tab_width_row)
        self.tab_width_spinbutton = self.tab_width_row

        group_line_numbers = Adw.PreferencesGroup()
        group_line_numbers.set_title(_('Line Numbers'))
        self.add(group_line_numbers)

        self.option_show_line_numbers = Adw.SwitchRow()
        self.option_show_line_numbers.set_title(_('Show line numbers'))
        group_line_numbers.add(self.option_show_line_numbers)

        group_line_wrapping = Adw.PreferencesGroup()
        group_line_wrapping.set_title(_('Line Wrapping'))
        self.add(group_line_wrapping)

        self.option_line_wrapping = Adw.SwitchRow()
        self.option_line_wrapping.set_title(_('Enable line wrapping'))
        group_line_wrapping.add(self.option_line_wrapping)

        group_code_folding = Adw.PreferencesGroup()
        group_code_folding.set_title(_('Code Folding'))
        self.add(group_code_folding)

        self.option_code_folding = Adw.SwitchRow()
        self.option_code_folding.set_title(_('Enable code folding'))
        group_code_folding.add(self.option_code_folding)

        group_highlighting = Adw.PreferencesGroup()
        group_highlighting.set_title(_('Highlighting'))
        self.add(group_highlighting)

        self.option_highlight_current_line = Adw.SwitchRow()
        self.option_highlight_current_line.set_title(_('Highlight current line'))
        group_highlighting.add(self.option_highlight_current_line)

        self.option_highlight_matching_brackets = Adw.SwitchRow()
        self.option_highlight_matching_brackets.set_title(_('Highlight matching brackets'))
        group_highlighting.add(self.option_highlight_matching_brackets)

        # 自动保存（崩溃恢复）：把缓冲区内容定时写入临时文件，
        # 应用崩溃后下次启动可恢复未保存的编辑。
        group_auto_save = Adw.PreferencesGroup()
        group_auto_save.set_title(_('Auto Save'))
        self.add(group_auto_save)

        self.option_auto_save_enabled = Adw.SwitchRow()
        self.option_auto_save_enabled.set_title(_('Enable auto save (crash recovery)'))
        self.option_auto_save_enabled.set_subtitle(_('Periodically save buffer to a temp file for crash recovery'))
        group_auto_save.add(self.option_auto_save_enabled)

        self.auto_save_delay_row = Adw.SpinRow()
        self.auto_save_delay_row.set_title(_('Auto save interval (seconds)'))
        adjustment_auto_save = Gtk.Adjustment(value=60, lower=10, upper=600, step_increment=5)
        self.auto_save_delay_row.set_adjustment(adjustment_auto_save)
        group_auto_save.add(self.auto_save_delay_row)

        # 外部变更：自动静默重载磁盘上被其他程序修改的文件。
        group_external_changes = Adw.PreferencesGroup()
        group_external_changes.set_title(_('External Changes'))
        self.add(group_external_changes)

        self.option_auto_reload_on_external_change = Adw.SwitchRow()
        self.option_auto_reload_on_external_change.set_title(_('Automatically reload changed files'))
        self.option_auto_reload_on_external_change.set_subtitle(
            _('Reload files modified by external applications without prompting'))
        group_external_changes.add(self.option_auto_reload_on_external_change)

        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        group_reset.add(self.reset_button)
