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
# along with this program. If not, see <http://www.gnu.org/licenses/>.

'''Integrated, source-preserving BibTeX entry manager for Setzer.'''

from __future__ import annotations

import builtins
import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, Gtk

from setzer.app.latex_db import LaTeXDB
from setzer.dialogs.helpers.dialog_viewgtk import DialogView
from setzer.document.bibtex.entry_store import (
    BibTeXEntry,
    BibTeXEntryError,
    BibTeXEntryStore,
    BibTeXString,
)
from setzer.document.bibtex.file_session import (
    BibTeXExternalChangeError,
    BibTeXFileSession,
)
from setzer.document.bibtex.text_utils import (
    latex_to_unicode,
    protect_cases,
    unicode_to_latex,
)


def _(message: str) -> str:
    '''Look up a runtime gettext translation with a test-safe fallback.'''
    return getattr(builtins, '_', lambda value: value)(message)


FIELD_LABELS = {
    'author': _('Author'),
    'title': _('Title'),
    'year': _('Year'),
    'journal': _('Journal'),
    'booktitle': _('Book Title'),
    'publisher': _('Publisher'),
    'volume': _('Volume'),
    'number': _('Number'),
    'pages': _('Pages'),
    'doi': _('DOI'),
    'url': _('URL'),
    'editor': _('Editor'),
    'edition': _('Edition'),
    'address': _('Address'),
    'month': _('Month'),
    'note': _('Note'),
    'series': _('Series'),
    'institution': _('Institution'),
    'school': _('School'),
    'howpublished': _('How Published'),
    'keywords': _('Keywords'),
}


class BibliographyManagerDialog(DialogView):
    '''Browse, safely edit, and cite entries from project bibliography files.'''

    def __init__(self, main_window, workspace):
        DialogView.__init__(self, main_window)
        self.main_window = main_window
        self.workspace = workspace
        self.document = None
        self.sources = []
        self.selected_source = None
        self.file_session = None
        self.store = None
        self.loaded_text = ''
        self.selected_entry = None
        self.editing_key = None
        self.entry_rows = []
        # 引用插入辅助状态：用户从左侧列表勾选/取消勾选条目时维护这个
        # 集合；预览与最终插入均以此为唯一真源，避免在多选期间与
        # GTK ListBox 内部选择模型（仅追踪焦点行）发生分歧。重新加载
        # 文献、切换 source、刷新搜索时一并清空。
        self.cited_keys = set()

        self.set_title(_('Manage Bibliography'))
        self.set_content_width(1040)
        self.set_content_height(680)
        self._build_view()
        self.connect('closed', self._on_closed)

    def _build_view(self):
        self.banner = Adw.Banner()
        self.banner.set_revealed(False)
        self.banner.set_button_label(_('Reload'))
        self.banner.connect('button-clicked', self._on_banner_reload)
        self.topbox.append(self.banner)

        header = Gtk.Box(spacing=8)
        header.set_margin_top(12)
        header.set_margin_bottom(8)
        header.set_margin_start(18)
        header.set_margin_end(18)
        self.topbox.append(header)

        self.source_model = Gtk.StringList.new([])
        self.source_selector = Gtk.DropDown.new(self.source_model, None)
        self.source_selector.set_hexpand(True)
        self.source_selector.connect('notify::selected', self._on_source_selected)
        header.append(self.source_selector)

        self.open_button = Gtk.Button(label=_('Open BibTeX File…'))
        self.open_button.set_icon_name('document-open-symbolic')
        self.open_button.set_tooltip_text(_('Open an existing BibTeX file'))
        self.open_button.connect('clicked', self._on_open_file)
        header.append(self.open_button)

        self.add_button = Gtk.Button(label=_('Add Entry'))
        self.add_button.set_icon_name('list-add-symbolic')
        self.add_button.set_tooltip_text(_('Create a new bibliography entry'))
        self.add_button.add_css_class('suggested-action')
        self.add_button.connect('clicked', self._on_add_entry)
        header.append(self.add_button)

        self.format_button = Gtk.Button(label=_('Format Bibliography'))
        self.format_button.set_icon_name('format-justify-fill-symbolic')
        self.format_button.set_tooltip_text(
            _('Rewrite all entries with sorted fields and aligned values'))
        self.format_button.connect('clicked', self._on_format_bibliography)
        header.append(self.format_button)

        self.strings_button = Gtk.Button(label=_('Strings'))
        # 原写为 'edit-symbolic'——但 Adwaita 图标集中只有具体前缀的
        # edit-* 图标（edit-clear/edit-copy/edit-cut/...），裸 edit-
        # symbolic 不存在，按钮会渲染为空白方块。用 document-edit-
        # symbolic（项目内其他“Edit Entry”按钮已在用）保证图标可
        # 加载且语义一致。
        self.strings_button.set_icon_name('document-edit-symbolic')
        self.strings_button.set_tooltip_text(
            _('Browse, edit, and import @string macros'))
        self.strings_button.connect('clicked', self._on_open_strings_dialog)
        header.append(self.strings_button)

        self.paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        self.paned.set_wide_handle(True)
        self.paned.set_position(370)
        self.paned.set_vexpand(True)
        self.topbox.append(self.paned)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_margin_start(18)
        left.set_margin_bottom(18)
        left.set_margin_end(8)
        self.paned.set_start_child(left)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(_('Search citation key, title, author, or year'))
        self.search_entry.connect('search-changed', self._on_search_changed)
        left.append(self.search_entry)

        self.sort_keys = ('key', 'title', 'author', 'year')
        self.sort_model = Gtk.StringList.new([
            _('Sort by Citation Key'),
            _('Sort by Title'),
            _('Sort by Author'),
            _('Sort by Year'),
        ])
        self.sort_selector = Gtk.DropDown.new(self.sort_model, None)
        self.sort_selector.set_tooltip_text(_('Sort bibliography entries'))
        self.sort_selector.connect('notify::selected', self._on_sort_selected)
        left.append(self.sort_selector)

        self.entry_list = Gtk.ListBox()
        # 单选：点击行把该 key 加/移出 cited_keys 集合（购物车模型）。
        # 多选语义完全在插入侧栏表达（chips 区域可见、单击移除），
        # 避免在 GTK ListBox 上自绘复选框——ListBox 原生 selection 视觉
        # 干扰且与自定义 checked 状态叠加混乱。selected_entry 仍绑定
        # 当前激活行用于右侧详情面板。
        self.entry_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.entry_list.add_css_class('boxed-list')
        # 勾选 toggle 走自定义 button-press 事件控制器，而不是
        # row-activated（需双击/Enter/空格）或 row-selected（SINGLE
        # 模式下重复点同一行不重新触发）——两者都不能提供稳定的
        # “点一下勾上、再点一下取消”交互。row-selected 仅负责详情同步。
        self.entry_list.connect('row-selected', self._on_entry_selected)
        entry_scroller = Gtk.ScrolledWindow()
        entry_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        entry_scroller.set_vexpand(True)
        entry_scroller.set_child(self.entry_list)
        left.append(entry_scroller)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        right.set_margin_end(18)
        right.set_margin_bottom(18)
        right.set_margin_start(8)
        self.paned.set_end_child(right)

        # 插入侧栏：多选条目 + 命令 + 可选 locator + 实时预览。
        # 放在详情/编辑面板之上：不论用户是否已选中条目，插入操作始终
        # 可用。cited_keys 集合是预览与最终插入的唯一真源（左侧 ListBox
        # 切换搜索/排序/文件时被重建，直接读 GTK 选区会丢勾选）。
        self.insert_box = self._build_insert_box()
        right.append(self.insert_box)

        self.empty_page = Adw.StatusPage()
        self.empty_page.set_icon_name('library-symbolic')
        self.empty_page.set_title(_('Choose a BibTeX File'))
        self.empty_page.set_description(_('Choose a bibliography associated with the current document, or open a BibTeX file.'))
        self.empty_page.set_vexpand(True)
        right.append(self.empty_page)

        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.details_box.set_visible(False)
        right.append(self.details_box)

        self.details_title = Gtk.Label()
        self.details_title.add_css_class('title-2')
        self.details_title.set_halign(Gtk.Align.START)
        self.details_title.set_wrap(True)
        self.details_box.append(self.details_title)

        self.details_subtitle = Gtk.Label()
        self.details_subtitle.add_css_class('dim-label')
        self.details_subtitle.set_halign(Gtk.Align.START)
        self.details_subtitle.set_wrap(True)
        self.details_box.append(self.details_subtitle)

        self.details_view = Gtk.TextView()
        self.details_view.set_editable(False)
        self.details_view.set_cursor_visible(False)
        self.details_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.details_view.set_monospace(True)
        self.details_view.set_top_margin(8)
        self.details_view.set_bottom_margin(8)
        self.details_view.set_left_margin(8)
        self.details_view.set_right_margin(8)
        details_scroller = Gtk.ScrolledWindow()
        details_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        details_scroller.set_vexpand(True)
        details_scroller.set_child(self.details_view)
        details_scroller.add_css_class('preview-card')
        details_scroller.set_overflow(Gtk.Overflow.HIDDEN)
        self.details_box.append(details_scroller)

        actions = Gtk.Box(spacing=8)
        actions.set_halign(Gtk.Align.END)
        self.insert_button = Gtk.Button(label=_('Insert Citation'))
        self.insert_button.set_icon_name('insert-text-symbolic')
        self.insert_button.set_tooltip_text(_('Insert a \\cite command for this entry'))
        self.insert_button.connect('clicked', self._on_insert_citation)
        actions.append(self.insert_button)
        self.edit_button = Gtk.Button(label=_('Edit Entry'))
        self.edit_button.set_icon_name('document-edit-symbolic')
        self.edit_button.set_tooltip_text(_('Edit the selected bibliography entry'))
        self.edit_button.connect('clicked', self._on_edit_entry)
        actions.append(self.edit_button)
        self.delete_button = Gtk.Button(label=_('Delete Entry'))
        self.delete_button.set_icon_name('user-trash-symbolic')
        self.delete_button.set_tooltip_text(_('Remove the selected bibliography entry'))
        self.delete_button.add_css_class('destructive-action')
        self.delete_button.connect('clicked', self._on_delete_entry)
        actions.append(self.delete_button)
        self.details_box.append(actions)

        self.form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.form_box.set_visible(False)
        right.append(self.form_box)
        self._build_form()

    def _build_form(self):
        # 标准 Adwaita 编辑表单：Adw.PreferencesGroup（boxed list）+ Adw.EntryRow，
        # 与同文件 Insert Citations 区、偏好设置页、文档向导保持同一行风格；
        # 取代旧版手拼「<b>粗体 Label + Gtk.Entry」竖排堆叠。EntryRow 的 title
        # 即字段标签，原 placeholder 提示语转为 tooltip 保留。
        form_scroller = Gtk.ScrolledWindow()
        form_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        form_scroller.set_vexpand(True)
        self.form_box.append(form_scroller)

        group = Adw.PreferencesGroup()
        group.set_title(_('Entry Details'))
        form_scroller.set_child(group)

        self.type_entry = Adw.EntryRow()
        self.type_entry.set_title(_('Entry Type'))
        self.type_entry.set_tooltip_text(_('Entry type, for example article'))
        group.add(self.type_entry)
        self.key_entry = Adw.EntryRow()
        self.key_entry.set_title(_('Citation Key'))
        self.key_entry.set_tooltip_text(_('Unique citation key'))
        group.add(self.key_entry)

        # Shared field-popover used by the right-click menu on every
        # field row and the extra-fields TextView.  The menu is wired
        # after _build_view() returns, so we build it lazily here and
        # connect the gesture controller inside _build_form (the field
        # rows and extra_fields text view are still being appended
        # below).
        self._field_popover = self._build_field_popover()

        self.field_entries = {}
        for field in BibTeXEntryStore.common_fields():
            entry = Adw.EntryRow()
            entry.set_title(FIELD_LABELS[field])
            entry.set_tooltip_text(field)
            self.field_entries[field] = entry
            group.add(entry)
            self._attach_field_popover(entry)

        # 多行自由字段编辑器：libadwaita 没有标准多行 EntryRow，沿用本文件
        # chips_row 的惯例——嵌入一个 Adw.PreferencesRow，内层 box 边距
        # （6/6/12/12）与其余行对齐，标题用 .heading 样式类。
        extra_row = Adw.PreferencesRow()
        extra_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        extra_column.set_margin_top(6)
        extra_column.set_margin_bottom(6)
        extra_column.set_margin_start(12)
        extra_column.set_margin_end(12)
        extra_label = Gtk.Label(label=_('Additional Fields'))
        extra_label.set_halign(Gtk.Align.START)
        extra_label.add_css_class('heading')
        extra_column.append(extra_label)
        extra_hint = Gtk.Label(label=_('One field per line: name = value'))
        extra_hint.set_halign(Gtk.Align.START)
        extra_hint.add_css_class('dim-label')
        extra_hint.set_wrap(True)
        extra_column.append(extra_hint)
        self.extra_fields = Gtk.TextView()
        self.extra_fields.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.extra_fields.set_monospace(True)
        extra_scroller = Gtk.ScrolledWindow()
        extra_scroller.set_min_content_height(110)
        extra_scroller.set_child(self.extra_fields)
        extra_column.append(extra_scroller)
        extra_row.set_child(extra_column)
        group.add(extra_row)
        self._attach_field_popover(self.extra_fields)

        # 底部动作条：Gtk.ActionBar 是 GTK4 标准的「底部动作条」组合控件，
        # 自动获得顶部分隔线与底栏背景；Cancel 在左，主操作 Save 在右。
        actions = Gtk.ActionBar()
        cancel = Gtk.Button(label=_('Cancel'))
        cancel.connect('clicked', self._on_cancel_edit)
        actions.pack_start(cancel)
        save = Gtk.Button(label=_('Save Entry'))
        save.add_css_class('suggested-action')
        save.connect('clicked', self._on_save_entry)
        actions.pack_end(save)
        self.form_box.append(actions)

    def _build_insert_box(self):
        # 复用 LaTeXDB.dynamic_commands['citations'] 的白名单——这是补全
        # 也用的同一份，命令名/带星变体/可选参数形态都对齐。补全
        # 字典插入顺序有用户含义（cite 在末位当 fallback），照搬。
        commands = list(LaTeXDB.dynamic_commands.get('citations') or ['\\cite'])
        self.insert_command_options = commands
        self.insert_command_model = Gtk.StringList.new(commands)
        # 选择默认 \cite 与现有 _on_insert_citation 行为一致。
        self.insert_command_selected = '\\cite'

        # Adw.PreferencesGroup：Adwaita 标准“带标题/描述的分组容器”，
        # 替代手拼 card + Label + box。其 add() 接受任意 Gtk.Widget，
        # 包括 ActionRow 与我们的 chips 容器 WrapBox。
        group = Adw.PreferencesGroup()
        group.set_title(_('Insert Citations'))
        group.set_description(
            _(
                'Select entries on the left, choose a command, optionally add a '
                'locator, and insert. The preview shows the final LaTeX text.'
            ),
        )

        # 命令 + locator：两行 Adw.ActionRow，command_dropdown 作为
        # 第一个 row 的 suffix、locator_entry 作为第二个 row 的 widget。
        # ActionRow 是 Adwaita row 风格（与偏好页一致），不需手拼 box。
        command_row = Adw.ActionRow()
        command_row.set_title(_('Command'))
        self.insert_command_dropdown = Gtk.DropDown.new(self.insert_command_model, None)
        self.insert_command_dropdown.set_tooltip_text(_('Citation command to insert'))
        self.insert_command_dropdown.set_valign(Gtk.Align.CENTER)
        self.insert_command_dropdown.connect('notify::selected', self._on_insert_command_changed)
        command_row.add_suffix(self.insert_command_dropdown)
        command_row.set_activatable_widget(self.insert_command_dropdown)
        group.add(command_row)

        locator_row = Adw.ActionRow()
        locator_row.set_title(_('Locator'))
        locator_row.set_subtitle(_('Optional argument, e.g. p.~12 or ch.~3'))
        self.insert_locator_entry = Gtk.Entry()
        self.insert_locator_entry.set_placeholder_text('p.~12')
        self.insert_locator_entry.set_valign(Gtk.Align.CENTER)
        self.insert_locator_entry.set_tooltip_text(
            _('Optional argument placed in [brackets] after the command'),
        )
        self.insert_locator_entry.connect('changed', self._on_insert_locator_changed)
        locator_row.add_suffix(self.insert_locator_entry)
        locator_row.set_activatable_widget(self.insert_locator_entry)
        group.add(locator_row)

        # 已选 keys 区域（chips）：点击行后，key 以 chip 形式出现在
        # 插入侧栏，提示已勾选且可单击移除。嵌入外层 group 的一个
        # Adw.PreferencesRow（其 child 是 Adw.WrapBox）。
        chips_row = Adw.PreferencesRow()
        chips_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        chips_column.set_margin_top(6)
        chips_column.set_margin_bottom(6)
        chips_column.set_margin_start(12)
        chips_column.set_margin_end(12)
        chips_label = Gtk.Label(label=_('Selected Keys'))
        chips_label.set_halign(Gtk.Align.START)
        chips_label.add_css_class('heading')
        chips_column.append(chips_label)
        self.cited_chips_empty_label = Gtk.Label(
            label=_('No keys selected yet.'),
        )
        self.cited_chips_empty_label.set_halign(Gtk.Align.CENTER)
        self.cited_chips_empty_label.add_css_class('dim-label')
        chips_column.append(self.cited_chips_empty_label)
        # Adw.WrapBox：Adwaita 的“自动换行 box”，是 chips 的标准容器。
        # 取代手拼 Gtk.FlowBox；子项可以是任意 Gtk.Widget。
        # 注意：WrapBox 没有 set_spacing，只有 set_child_spacing（行内
        # 子项间距）与 set_line_spacing（行与行间距）。
        self.cited_chips_wrap = Adw.WrapBox()
        self.cited_chips_wrap.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.cited_chips_wrap.set_child_spacing(4)
        self.cited_chips_wrap.set_line_spacing(4)
        chips_column.append(self.cited_chips_wrap)
        chips_row.set_child(chips_column)
        group.add(chips_row)

        # 预览行：Adw.ActionRow 的 title 直接承载 monospace 预览文本
        # ——ActionRow 的 title 区域支持多行 wrap 与可选择，与偏好页
        # 中“长文本”行（描述/版本号）一致。
        self.insert_preview_row = Adw.ActionRow()
        self.insert_preview_row.set_title(_('Preview'))
        self.insert_preview = Gtk.Label()
        self.insert_preview.set_halign(Gtk.Align.START)
        self.insert_preview.set_wrap(True)
        self.insert_preview.set_selectable(True)
        self.insert_preview.set_xalign(0)
        self.insert_preview.add_css_class('monospace')
        self.insert_preview_row.add_suffix(self.insert_preview)
        group.add(self.insert_preview_row)

        # 动作行：Clear + Insert 作为同一 ActionRow 的两个 suffix。
        # Adwaita 偏好页普遍采用 ActionRow 容纳多个 suffix button 的
        # 布局，避免在 PreferencesGroup 之外再拼一个水平 box。
        actions_row = Adw.ActionRow()
        actions_row.set_title(_('Actions'))
        self.insert_clear_button = Gtk.Button(label=_('Clear Selection'))
        self.insert_clear_button.set_valign(Gtk.Align.CENTER)
        self.insert_clear_button.set_tooltip_text(_('Deselect all citation keys'))
        self.insert_clear_button.connect('clicked', self._on_insert_clear)
        actions_row.add_suffix(self.insert_clear_button)
        self.insert_button_multi = Gtk.Button(label=_('Insert Citations'))
        self.insert_button_multi.set_icon_name('insert-text-symbolic')
        self.insert_button_multi.set_valign(Gtk.Align.CENTER)
        self.insert_button_multi.add_css_class('suggested-action')
        self.insert_button_multi.set_tooltip_text(
            _('Insert the citation text at the cursor in the active LaTeX document'),
        )
        self.insert_button_multi.connect('clicked', self._on_insert_citation_multi)
        actions_row.add_suffix(self.insert_button_multi)
        group.add(actions_row)

        return group

    def run(self, document):
        '''Present the manager for the current LaTeX or BibTeX document.'''
        self.document = document
        self.search_entry.set_text('')
        # 每次打开都重置引文选择——关闭后再打开不应保留上次的
        # 勾选状态。命令与 locator 保留作为用户偏好。
        self.cited_keys.clear()
        if hasattr(self, 'insert_locator_entry'):
            self.insert_locator_entry.set_text('')
            self._update_insert_preview()
        self._populate_sources()
        Adw.Dialog.present(self, self.main_window)

    def _on_closed(self, dialog):
        self._reset_editing()
        self.document = None
        self.selected_source = None
        self.file_session = None
        self.store = None
        self.loaded_text = ''

    def _populate_sources(self):
        self.sources = self._collect_sources()
        self.source_model.splice(0, self.source_model.get_n_items(), [])
        for source in self.sources:
            self.source_model.append(source['label'])
        self.source_selector.set_sensitive(bool(self.sources))
        self.add_button.set_sensitive(bool(self.sources))
        self.format_button.set_sensitive(bool(self.sources))
        self.strings_button.set_sensitive(bool(self.sources))
        if self.sources:
            self.source_selector.set_selected(0)
            self._load_source(self.sources[0])
        else:
            self._clear_source()

    def _collect_sources(self):
        result = []
        seen = set()

        def add_source(path, label=None, document=None):
            normalized = os.path.realpath(path) if path else None
            identity = normalized or f'untitled:{id(document)}'
            if identity in seen:
                return
            seen.add(identity)
            result.append({
                'path': normalized,
                'label': label or (os.path.basename(normalized) if normalized else _('Untitled BibTeX File')),
                'document': document,
            })

        active = self.document
        if active is not None and active.is_bibtex_document():
            add_source(active.get_filename(), document=active)
        if active is not None and active.is_latex_document() and active.get_filename():
            base_directory = active.get_dirname()
            for filename in sorted(active.parser.symbols.get('bibliographies', set())):
                path = os.path.realpath(os.path.join(base_directory, filename))
                if os.path.isfile(path):
                    add_source(path)
        for document in self.workspace.open_documents:
            if document.is_bibtex_document():
                add_source(document.get_filename(), document=document)
        return result

    def _on_source_selected(self, selector, parameter):
        selected = selector.get_selected()
        if selected >= len(self.sources):
            return
        self._load_source(self.sources[selected])

    def _load_source(self, source):
        try:
            self.selected_source = source
            target_document = self._document_for_path(source.get('path')) or source.get('document')
            source['document'] = target_document
            self.file_session = None
            if target_document is not None:
                text = self._document_text(target_document)
            else:
                self.file_session = BibTeXFileSession(source['path'])
                text = self.file_session.text
            self.loaded_text = text
            self.store = BibTeXEntryStore(text)
            self._show_message('', False)
            if self.store.diagnostics:
                self._show_message('\n'.join(self.store.diagnostics), True)
            self._refresh_entry_rows()
            self.add_button.set_sensitive(True)
            self.format_button.set_sensitive(True)
            self.strings_button.set_sensitive(True)
        except (OSError, UnicodeError, BibTeXEntryError) as error:
            self._clear_source()
            self._show_message(str(error), True)

    def _clear_source(self):
        self.selected_source = None
        self.file_session = None
        self.store = None
        self.loaded_text = ''
        self.selected_entry = None
        self.cited_keys.clear()
        if hasattr(self, 'insert_preview'):
            self._update_insert_preview()
        self._clear_entry_rows()
        self._set_detail_visibility(False)
        self.empty_page.set_visible(True)
        self.add_button.set_sensitive(False)
        self.format_button.set_sensitive(False)
        self.strings_button.set_sensitive(False)

    def _refresh_entry_rows(self):
        self._clear_entry_rows()
        if self.store is None:
            # 清除 source 时连同 cited_keys 一起清空——旧 key 已无法
            # 定位，且对用户来说不残留幽灵预览是关键。
            self.cited_keys.clear()
            self._update_insert_preview()
            return
        # 重新加载 store 后 cited_keys 中可能含新 store 不存在的 key
        # （外部编辑 / 切换 source / 同步删除等）。用现有解析器验证
        # 后再丢弃，避免预览/插入引用不存在的 key 时 LaTeX 编译报
        # undefined citation。
        available = {entry.key for entry in self.store.entries}
        stale = self.cited_keys - available
        if stale:
            self.cited_keys -= stale
        sort_by = self.sort_keys[self.sort_selector.get_selected()]
        for entry in self.store.list_entries(self.search_entry.get_text(), sort_by=sort_by):
            row = Gtk.ListBoxRow()
            row.entry = entry
            # Adw.ActionRow：title/subtitle 自动以 Adwaita typography
            # 呈现，跟随主题明暗；add_prefix 是 ActionRow 标准的“装饰
            # 图标”槽位。复用不需手拼 Gtk.Box + 多个 Gtk.Label。
            action_row = Adw.ActionRow()
            action_row.set_title(entry.key)
            action_row.set_subtitle(self._entry_summary(entry))
            # 多选用 Gtk.CheckButton 作 prefix：Adwaita 偏好多选用
            # 复选框（不是开关）作列表项的勾选控件（GNOME Files、
            # Builder 的多选模式都是 CheckButton + ActionRow）。
            # CheckButton 的 toggled 信号是唯一 toggle 入口—不要
            # 另加行级 gesture click，避免重复 toggle。
            checkbox = Gtk.CheckButton()
            checkbox.set_active(entry.key in self.cited_keys)
            checkbox.set_valign(Gtk.Align.CENTER)
            checkbox.set_tooltip_text(_('Add this entry to the citation list'))
            handler_id = checkbox.connect('toggled', self._on_entry_check_toggled, entry.key)
            # 保存 handler_id 以供 _sync_row_checkboxes 临时 block
            # 避免 set_active 重入 _on_entry_check_toggled。
            checkbox._toggle_handler_id = handler_id
            action_row.add_prefix(checkbox)
            row.checkbox = checkbox
            row.set_child(action_row)
            # 行点击 → 详情同步：依赖 Gtk.ListBox 原生 row-selected
            # 信号（SINGLE 模式下点击行触发），不需要再加 GestureClick
            # 拦截——后者会吞下事件且与 CheckButton 事件传播冲突。
            # 已连接 row-selected → _on_entry_selected。
            self.entry_list.append(row)
            self.entry_rows.append(row)
        if self.entry_rows:
            self.entry_list.select_row(self.entry_rows[0])
        else:
            self.selected_entry = None
            self._set_detail_visibility(False)
            self.empty_page.set_visible(True)
            self.empty_page.set_title(_('No Bibliography Entries Found'))
            self.empty_page.set_description(_('Add an entry or adjust the search query.'))

    def _clear_entry_rows(self):
        child = self.entry_list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.entry_list.remove(child)
            child = following
        self.entry_rows = []

    @staticmethod
    def _entry_summary(entry: BibTeXEntry):
        parts = [entry.entry_type]
        if entry.get('title'):
            parts.append(entry.get('title'))
        if entry.get('author'):
            parts.append(entry.get('author'))
        if entry.get('year'):
            parts.append(entry.get('year'))
        return ' · '.join(parts)

    def _on_search_changed(self, entry):
        self._refresh_entry_rows()

    def _on_sort_selected(self, selector, parameter):
        if self.store is not None:
            self._refresh_entry_rows()

    def _on_entry_selected(self, listbox, row):
        # row-selected 仅负责详情面板同步。勾选 toggle 走
        # _on_entry_toggled（由行上的 button-press 事件控制器调用），
        # 因为 row-selected 在 SINGLE 模式下重复点击同一行不重新触发，
        # row-activated 则需双击/Enter/空格，均不能提供稳定的
        # "点一下勾上、再点一下取消"交互。
        if row is None:
            return
        self.selected_entry = row.entry
        self._reset_editing()
        self.empty_page.set_visible(False)
        self._set_detail_visibility(True)
        entry = self.selected_entry
        self.details_title.set_text(entry.get('title') or entry.key)
        self.details_subtitle.set_text(f'{entry.key} · {entry.entry_type}')
        details = [f'@{entry.entry_type}{{{entry.key},']
        details.extend(f'  {name} = {{{value}}}' for name, value in entry.fields)
        details.append('}')
        self.details_view.get_buffer().set_text('\n'.join(details))
        self.insert_button.set_sensitive(
            self.document is not None and self.document.is_latex_document()
        )

    def _on_entry_check_toggled(self, checkbox, key):
        # Gtk.CheckButton 的 toggled 信号：active=True 表示勾选。同步
        # 到 cited_keys 并刷新预览/chips。行级 gesture click 不再
        # toggle——避免同一行点一次 row-toggle 再点一次 checkbox-
        # toggle 重复动作。
        active = checkbox.get_active()
        if active and key not in self.cited_keys:
            self.cited_keys.add(key)
            self._update_insert_preview()
        elif not active and key in self.cited_keys:
            self.cited_keys.discard(key)
            self._update_insert_preview()

    def _set_detail_visibility(self, visible):
        self.details_box.set_visible(visible)
        self.form_box.set_visible(False)

    def _on_add_entry(self, button):
        if self.store is None:
            return
        self.selected_entry = None
        self.editing_key = None
        self._set_detail_visibility(False)
        self.empty_page.set_visible(False)
        self.form_box.set_visible(True)
        self.type_entry.set_text('article')
        self.key_entry.set_text('')
        for field in self.field_entries.values():
            field.set_text('')
        self.extra_fields.get_buffer().set_text('')
        self.key_entry.grab_focus()

    def _on_edit_entry(self, button):
        if self.selected_entry is None:
            return
        entry = self.selected_entry
        self.editing_key = entry.key
        self._set_detail_visibility(False)
        self.form_box.set_visible(True)
        self.type_entry.set_text(entry.entry_type)
        self.key_entry.set_text(entry.key)
        fields = entry.field_map
        for name, field in self.field_entries.items():
            field.set_text(fields.pop(name, ''))
        extra = '\n'.join(f'{name} = {value}' for name, value in fields.items())
        self.extra_fields.get_buffer().set_text(extra)
        self.key_entry.grab_focus()

    def _on_cancel_edit(self, button):
        self._reset_editing()
        if self.selected_entry is not None:
            self._set_detail_visibility(True)
        else:
            self.empty_page.set_visible(True)

    def _reset_editing(self):
        self.editing_key = None
        self.form_box.set_visible(False)

    def _on_save_entry(self, button):
        if self.store is None:
            return
        try:
            fields = self._collect_form_fields()
            if self.editing_key is None:
                updated_text = self.store.add_entry(
                    self.type_entry.get_text(), self.key_entry.get_text(), fields)
            else:
                updated_text = self.store.update_entry(
                    self.editing_key, self.type_entry.get_text(), self.key_entry.get_text(), fields)
            self._apply_text(updated_text)
            key = self.key_entry.get_text().strip()
            self._load_source(self.selected_source)
            for row in self.entry_rows:
                if row.entry.key == key:
                    self.entry_list.select_row(row)
                    break
        except (BibTeXEntryError, BibTeXExternalChangeError, OSError, UnicodeError) as error:
            self._show_message(str(error), True)

    def _collect_form_fields(self):
        fields = {name: entry.get_text() for name, entry in self.field_entries.items()}
        start, end = self.extra_fields.get_buffer().get_bounds()
        extra = self.extra_fields.get_buffer().get_text(start, end, True)
        for line in extra.splitlines():
            if not line.strip():
                continue
            if '=' not in line:
                raise BibTeXEntryError(_('Additional fields must use “name = value”'))
            name, value = line.split('=', 1)
            name = name.strip().lower()
            if name in fields and fields[name].strip():
                raise BibTeXEntryError(_('The field “{field}” occurs more than once').format(field=name))
            fields[name] = value.strip()
        return fields

    def _on_delete_entry(self, button):
        if self.selected_entry is None:
            return
        key = self.selected_entry.key
        confirmation = Adw.AlertDialog(
            heading=_('Delete BibTeX Entry?'),
            body=_('Delete “{key}” from this bibliography? This can be undone when the file is open in Setzer.').format(key=key),
        )
        confirmation.add_response('cancel', _('Cancel'))
        confirmation.add_response('delete', _('Delete'))
        confirmation.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        confirmation.set_default_response('cancel')
        confirmation.set_close_response('cancel')
        confirmation.connect('response', self._on_delete_response, key)
        confirmation.present(self)

    def _on_delete_response(self, dialog, response, key):
        if response != 'delete' or self.store is None:
            return
        try:
            self._apply_text(self.store.delete_entry(key))
            self._load_source(self.selected_source)
        except (BibTeXEntryError, BibTeXExternalChangeError, OSError, UnicodeError) as error:
            self._show_message(str(error), True)

    def _on_format_bibliography(self, button):
        if self.store is None:
            return
        confirmation = Adw.AlertDialog(
            heading=_('Format Bibliography?'),
            body=_('Rewrite every entry with fields in a consistent order and aligned values. Comments and everything outside entries stay unchanged. This can be undone when the file is open in Setzer.'),
        )
        confirmation.add_response('cancel', _('Cancel'))
        confirmation.add_response('format', _('Format'))
        confirmation.set_response_appearance('format', Adw.ResponseAppearance.SUGGESTED)
        confirmation.set_default_response('cancel')
        confirmation.set_close_response('cancel')
        confirmation.connect('response', self._on_format_response)
        confirmation.present(self)

    def _on_format_response(self, dialog, response):
        if response != 'format' or self.store is None:
            return
        try:
            formatted = self.store.format_bibliography()
            if formatted == self.loaded_text:
                self._show_message(_('The bibliography already uses the canonical entry style'), False)
                return
            selected_key = self.selected_entry.key if self.selected_entry is not None else None
            self._apply_text(formatted)
            self._load_source(self.selected_source)
            if selected_key is not None:
                for row in self.entry_rows:
                    if row.entry.key == selected_key:
                        self.entry_list.select_row(row)
                        break
        except (BibTeXEntryError, BibTeXExternalChangeError, OSError, UnicodeError) as error:
            self._show_message(str(error), True)

    def _on_insert_citation(self, button):
        if self.selected_entry is None or self.document is None or not self.document.is_latex_document():
            return
        # 把快速插入路径与新插入侧栏共享同一集合——用户多次点击
        # "Insert Citation" 时，预览始终反映"刚刚插入"的最新一次。
        self.cited_keys = {self.selected_entry.key}
        # 命令/预览与新侧栏保持一致：locator 为空时退化为 \cite{key}，
        # 与原行为字节级等价；其他命令 / locator 走 _on_insert_citation_multi。
        locator = self.insert_locator_entry.get_text().strip() if hasattr(self, 'insert_locator_entry') else ''
        command = getattr(self, 'insert_command_selected', '\\cite')
        text = self._build_citation_text(command, [self.selected_entry.key], locator)
        buffer = self.document.source_buffer
        buffer.begin_user_action()
        try:
            buffer.insert_at_cursor(text)
        finally:
            buffer.end_user_action()
        self.document.scroll_cursor_onscreen()
        LaTeXDB.schedule_parse_included_files()
        self._update_insert_preview()
        self._show_message(_('Citation inserted'), False)

    def _on_insert_citation_multi(self, button):
        r'''根据侧栏设置插入多选引用，复用现有 buffer 插入路径。

        - 使用 begin_user_action/end_user_action 让 Ctrl+Z 一次回退整
          个插入，避免与现有 _apply_text 的 undo 语义不一致。
        - 插入文本由 _build_citation_text 集中生成，与预览完全一致。
        - 插入成功后保留 cited_keys 与命令/locator 选择——用户常常需
          要在多位置引用同一组 key，立即重置反而打断工作流；提供
          “Clear Selection” 按钮供用户手动清空。
        '''
        if (
            self.document is None
            or not self.document.is_latex_document()
            or not self.cited_keys
        ):
            return
        text = self._build_citation_text(
            self.insert_command_selected,
            sorted(self.cited_keys),
            self.insert_locator_entry.get_text().strip(),
        )
        if not text:
            return
        buffer = self.document.source_buffer
        buffer.begin_user_action()
        try:
            buffer.insert_at_cursor(text)
        finally:
            buffer.end_user_action()
        self.document.scroll_cursor_onscreen()
        LaTeXDB.schedule_parse_included_files()
        count = len(self.cited_keys)
        # 单复数：手动分支避免引入 ngettext 包装（项目现有 _() 包装
        # 只查 builtins._，未绑定 ngettext）。两种语言模板都通过
        # 现有 gettext 工具提取。
        if count == 1:
            message = _('Inserted citation with 1 key')
        else:
            message = _('Inserted citation with {count} keys').format(count=count)
        self._show_message(message, False)

    def _build_citation_text(self, command, keys, locator):
        r'''构造稳定的、可预览的 \command[locator]{k1,k2,...} 文本。

        - 命令名以反斜杠开头；不带反斜杠时自动补上（防御性，避免预览
          与插入结果不一致）。
        - locator 去除首尾空白；空字符串时不带方括号——经典 \cite{...}
          形态最常见，显式空方括号 (\cite[]{...}) 易让 biblatex 误
          解析。
        - key 列表按字母序排序、去除前后空白；空条目被丢弃。已存在的
          反斜杠/花括号不做转义——BibTeX key 本身在 store 解析阶段就
          限制为不含空白/逗号/花括号/圆括号（entry_store._ENTRY_KEY），
          因此无需额外清洗。
        - 用 ', ' 而非 ',' 拼接键列表，与 Setzer 现有 cite 补全提议
          风格一致；与 LaTeX 编译器对多余空白的容忍度也更高。
        '''
        if not command.startswith('\\'):
            command = '\\' + command.lstrip()
        # locator 在函数内统一 strip 一次——所有调用方都期望"全空白
        # 即无 locator"，避免散落在调用方各处单独处理而漏掉一处。
        if isinstance(locator, str):
            locator = locator.strip()
        else:
            locator = ''
        cleaned = []
        seen = set()
        for key in keys:
            if not isinstance(key, str):
                continue
            stripped = key.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            cleaned.append(stripped)
        cleaned.sort()
        if not cleaned:
            return ''
        if locator:
            return f'{command}[{locator}]{{{", ".join(cleaned)}}}'
        return f'{command}{{{", ".join(cleaned)}}}'

    def _on_insert_command_changed(self, dropdown, parameter):
        selected = dropdown.get_selected()
        if 0 <= selected < len(self.insert_command_options):
            self.insert_command_selected = self.insert_command_options[selected]
        self._update_insert_preview()

    def _on_insert_locator_changed(self, entry):
        self._update_insert_preview()

    def _on_insert_clear(self, button):
        self.cited_keys.clear()
        self._update_insert_preview()

    def _update_insert_preview(self):
        r'''根据 cited_keys + 命令 + locator 刷新所有相关 UI。

        刷新三处可见状态以保证相互一致：
        - 预览标签的最终 LaTeX 文本；
        - 左侧列表每行开头勾选图标的显隐（cited_keys → 行可见性），
          按 key 字典序——但仅对当前可见行处理，避免隐藏搜索结果被
          反复操作；
        - 插入侧栏 chips 区域（已选 keys 列表+单击移除）。
        预览、插入按钮与清空按钮的可用性也在此处统一决定。
        '''
        if not hasattr(self, 'insert_preview'):
            return
        text = self._build_citation_text(
            self.insert_command_selected,
            sorted(self.cited_keys),
            self.insert_locator_entry.get_text().strip(),
        )
        if text:
            self.insert_preview.set_text(text)
            self.insert_preview.set_tooltip_text(text)
        else:
            self.insert_preview.set_text(
                _('No citation keys selected — click an entry to add it.'),
            )
            self.insert_preview.set_tooltip_text('')
        can_insert = (
            bool(self.cited_keys)
            and self.document is not None
            and self.document.is_latex_document()
        )
        self.insert_button_multi.set_sensitive(can_insert)
        self.insert_clear_button.set_sensitive(bool(self.cited_keys))
        self._sync_row_checkboxes()
        self._refresh_cited_chips()

    def _sync_row_checkboxes(self):
        r'''同步左侧列表每行 CheckButton 的 active 与 cited_keys。

        chips 移除/批量操作/外部更新 cited_keys 后，行 CheckButton
        必须同步——但要避免在 toggled 信号内递归触发。用 handler
        block 保护：临时断开 toggled 连接，set_active 不重入。
        '''
        for row in self.entry_rows:
            checkbox = getattr(row, 'checkbox', None)
            if checkbox is None:
                continue
            desired = row.entry.key in self.cited_keys
            if checkbox.get_active() == desired:
                continue
            # handler_block/unblock 防止 set_active 触发 _on_entry_
            # check_toggled 形成递归。
            handler_id = getattr(checkbox, '_toggle_handler_id', None)
            if handler_id is not None:
                checkbox.handler_block(handler_id)
            checkbox.set_active(desired)
            if handler_id is not None:
                checkbox.handler_unblock(handler_id)

    def _refresh_cited_chips(self):
        r'''重建插入侧栏 chips 区域（已选 keys 列表）。

        每个 chip 是 Gtk.Button 包裹 Adw.ButtonContent（Adwaita 推
        荐的“icon + label” button 内部布局），点击从 cited_keys 移除
        并刷新所有 UI。全量重建：chips 数量通常 0-10，重建成本低，
        状态一致性好于增量 diff。
        '''
        wrap = getattr(self, 'cited_chips_wrap', None)
        if wrap is None:
            return
        child = wrap.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            wrap.remove(child)
            child = following
        for key in sorted(self.cited_keys):
            chip = Gtk.Button()
            chip.set_has_frame(False)
            chip.add_css_class('chip')
            # Adw.ButtonContent 是 Adwaita 为“带 icon 的 button”提供
            # 的标准内部布局：set_icon_name + set_label + 可选
            # set_use_underline。替代手拼 Gtk.Box + Image + Label。
            content = Adw.ButtonContent()
            content.set_icon_name('object-select-symbolic')
            content.set_label(key)
            chip.set_child(content)
            chip.set_tooltip_text(
                _('Remove “{key}” from the citation list').format(key=key),
            )
            chip.connect('clicked', self._on_remove_chip, key)
            wrap.append(chip)
        # 列表空时显示提示文本，避免 chips 区域看起来“故障”。
        empty = getattr(self, 'cited_chips_empty_label', None)
        if empty is not None:
            empty.set_visible(not self.cited_keys)

    def _on_remove_chip(self, button, key):
        if key in self.cited_keys:
            self.cited_keys.discard(key)
            self._update_insert_preview()

    def _apply_text(self, text):
        target_document = self.selected_source.get('document') if self.selected_source else None
        if target_document is not None:
            current_text = self._document_text(target_document)
            if current_text != self.loaded_text:
                raise BibTeXExternalChangeError(_('The open BibTeX document changed. Reload before saving.'))
            buffer = target_document.source_buffer
            buffer.begin_user_action()
            try:
                buffer.set_text(text)
            finally:
                buffer.end_user_action()
            parser = getattr(target_document, 'parser', None)
            if parser is not None and hasattr(parser, 'parse_symbols'):
                parser.parse_symbols(text)
        elif self.file_session is not None:
            self.file_session.write_text(text)
        else:
            raise BibTeXEntryError(_('No BibTeX file is selected'))
        LaTeXDB.schedule_parse_included_files()

    @staticmethod
    def _document_text(document):
        start, end = document.source_buffer.get_bounds()
        return document.source_buffer.get_text(start, end, True)

    def _document_for_path(self, pathname):
        if not pathname:
            return None
        normalized = os.path.realpath(pathname)
        for document in self.workspace.open_documents:
            if document.is_bibtex_document() and document.get_filename() and \
                    os.path.realpath(document.get_filename()) == normalized:
                return document
        return None

    def _on_open_file(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title(_('Open BibTeX File'))
        filter = Gtk.FileFilter()
        filter.set_name(_('BibTeX Files'))
        filter.add_suffix('bib')
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter)
        dialog.set_filters(filters)
        dialog.open(self.main_window, None, self._on_open_file_finished)

    def _on_open_file_finished(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            path = file.get_path()
            if not path:
                return
            self._add_external_source(path)
        except Exception as error:
            # Gtk.DialogError.CANCELLED is normal and should remain silent.
            if 'cancel' not in str(error).lower():
                self._show_message(str(error), True)

    def _add_external_source(self, path):
        normalized = os.path.realpath(path)
        for index, source in enumerate(self.sources):
            if source.get('path') == normalized:
                self.source_selector.set_selected(index)
                return
        self.sources.append({
            'path': normalized,
            'label': os.path.basename(normalized),
            'document': self._document_for_path(normalized),
        })
        self.source_model.append(os.path.basename(normalized))
        self.source_selector.set_selected(len(self.sources) - 1)

    def _on_banner_reload(self, banner):
        if self.selected_source is not None:
            self._load_source(self.selected_source)

    def _show_message(self, message, warning):
        self.banner.set_title(message)
        self.banner.set_button_label(_('Reload') if warning else '')
        self.banner.set_revealed(bool(message))

    # --- Field right-click popover ------------------------------------------

    def _build_field_popover(self):
        '''Return a single shared popover for the field right-click menu.

        The popover exposes three text transformations that operate on
        the field widget the user right-clicked on.  The active widget
        is recorded on ``popover._field_target`` just before the popover
        pops up, so one popover instance can serve every field entry and
        the extra-fields TextView.
        '''
        popover = Gtk.Popover()
        popover.set_autohide(True)
        popover.set_has_arrow(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        protect_button = Gtk.Button(label=_('Protect Upper-Case'))
        protect_button.set_has_frame(False)
        protect_button.set_halign(Gtk.Align.FILL)
        protect_button.connect('clicked', self._on_field_protect_cases)
        box.append(protect_button)

        to_latex_button = Gtk.Button(label=_('Unicode → LaTeX'))
        to_latex_button.set_has_frame(False)
        to_latex_button.set_halign(Gtk.Align.FILL)
        to_latex_button.connect('clicked', self._on_field_unicode_to_latex)
        box.append(to_latex_button)

        to_unicode_button = Gtk.Button(label=_('LaTeX → Unicode'))
        to_unicode_button.set_has_frame(False)
        to_unicode_button.set_halign(Gtk.Align.FILL)
        to_unicode_button.connect('clicked', self._on_field_latex_to_unicode)
        box.append(to_unicode_button)

        popover.set_child(box)
        popover._field_target = None
        return popover

    def _attach_field_popover(self, widget):
        '''Bind a right-click gesture on ``widget`` to the shared popover.'''
        gesture = Gtk.GestureClick()
        gesture.set_button(Gdk.BUTTON_SECONDARY)
        gesture.connect('pressed', self._on_field_popup, widget)
        widget.add_controller(gesture)

    def _on_field_popup(self, gesture, n_press, x, y, widget):
        self._field_popover._field_target = widget
        self._field_popover.unparent()
        self._field_popover.set_parent(widget)
        rectangle = Gdk.Rectangle()
        rectangle.x = int(x)
        rectangle.y = int(y)
        rectangle.width = 1
        rectangle.height = 1
        self._field_popover.set_pointing_to(rectangle)
        self._field_popover.popup()

    def _field_selection(self):
        '''Return ``(widget, text, start, end)`` for the active field.'''
        if self.store is None:
            return None
        widget = getattr(self._field_popover, '_field_target', None)
        if widget is None:
            return None
        if isinstance(widget, Gtk.TextView):
            buffer = widget.get_buffer()
            if buffer.get_has_selection():
                start_iter, end_iter = buffer.get_selection_bounds()
                text = buffer.get_text(start_iter, end_iter, True)
                return widget, text, start_iter.get_offset(), end_iter.get_offset()
            start_iter, end_iter = buffer.get_bounds()
            text = buffer.get_text(start_iter, end_iter, True)
            return widget, text, start_iter.get_offset(), end_iter.get_offset()
        if isinstance(widget, Gtk.Editable):
            # Adw.EntryRow 实现了 GtkEditable 接口（Gtk.Entry 亦然），
            # 用 Editable 而非 Gtk.Entry 判定即可同时覆盖两类字段行。
            text = widget.get_text()
            selection = widget.get_selection_bounds()
            # Gtk.Entry 无选区时返回 None；Adw.EntryRow（GtkEditable 接口）
            # 返回空元组 ()——统一按真值判断。
            if selection:
                start, end = selection
                return widget, text[start:end], start, end
            return widget, text, 0, len(text)
        return None

    def _field_replace_text(self, widget, start, end, new_text):
        '''Replace ``[start, end)`` in ``widget`` with ``new_text``.'''
        if isinstance(widget, Gtk.TextView):
            buffer = widget.get_buffer()
            start_iter = buffer.get_iter_at_offset(start)
            end_iter = buffer.get_iter_at_offset(end)
            buffer.delete(start_iter, end_iter)
            buffer.insert(buffer.get_iter_at_offset(start), new_text)
            return
        if isinstance(widget, Gtk.Editable):
            # Editable.delete_text(start, end) 的第二个参数是结束位置
            # （不含），与 TextView 分支的 [start, end) 语义一致。
            widget.delete_text(start, end)
            widget.insert_text(new_text, start)
            widget.set_position(start + len(new_text))

    def _popdown_field_popover(self):
        if self._field_popover.get_visible():
            self._field_popover.popdown()

    def _apply_field_transform(self, transform):
        selection = self._field_selection()
        if selection is None:
            return
        widget, text, start, end = selection
        new_text = transform(text)
        if new_text == text:
            self._popdown_field_popover()
            return
        self._field_replace_text(widget, start, end, new_text)
        self._popdown_field_popover()

    def _on_field_protect_cases(self, button):
        self._apply_field_transform(protect_cases)

    def _on_field_unicode_to_latex(self, button):
        self._apply_field_transform(unicode_to_latex)

    def _on_field_latex_to_unicode(self, button):
        self._apply_field_transform(latex_to_unicode)

    # --- Strings sub-dialog -------------------------------------------------

    def _on_open_strings_dialog(self, button):
        if self.store is None:
            return
        _StringsDialog(self)


class _StringsDialog(DialogView):
    '''A small modal sub-dialog for browsing and editing ``@string`` blocks.

    Opens with an in-memory copy of every ``@string`` definition in the
    current source, rendered as one expander row per macro.  Additions,
    renames, revaluations and deletions stay local until the headerbar
    Save button writes them back to the bibliography in a single batch;
    closing with unsaved changes asks for confirmation.  Import writes
    through immediately and rebuilds the buffer, so it is blocked while
    unsaved edits exist.
    '''

    def __init__(self, parent):
        DialogView.__init__(self, parent.main_window)
        self.parent = parent
        # 编辑缓冲：每项 {'orig': 原名或 None（本次会话新增）, 'name': str,
        # 'value': str, 'orig_value': str, 'row': ExpanderRow,
        # 'name_entry'/'value_entry': EntryRow}。保存时与 orig/orig_value
        # 对比算出增删改，全部成功才一次性写回。
        self._items = []
        self._row_widgets = []
        self._original_names = []
        self._dirty = False
        self._build_view()
        self.refresh_from_store()
        # 必须传父窗口：present() 缺省父窗口时会呈现为独立顶层窗口，
        # 失去 transient 父子关系（不随父窗口居中、无模态遮罩等对话框效果）
        self.present(self.parent.main_window)

    def _build_view(self):
        self.set_title(_('Manage @string Macros'))
        self.set_content_width(560)
        self.set_content_height(520)
        # Adw.Dialog 无 set_modal：present() 展示时天然模态

        # HeaderBar：Close（start）| Import、Save（end，Save 为 suggested）
        # 隐藏窗口标题按钮：右上角的原生 ✕ 与左上 Close 重复
        self.headerbar.set_show_start_title_buttons(False)
        self.headerbar.set_show_end_title_buttons(False)
        close_button = Gtk.Button.new_with_mnemonic(_('_Close'))
        close_button.set_can_focus(False)
        close_button.connect('clicked', self._on_close_requested)
        self.headerbar.pack_start(close_button)

        self.save_button = Gtk.Button(label=_('Save'))
        self.save_button.set_can_focus(False)
        self.save_button.add_css_class('suggested-action')
        self.save_button.set_sensitive(False)
        self.save_button.set_tooltip_text(
            _('Apply all changes to the bibliography file'))
        self.save_button.connect('clicked', self._on_save)
        self.headerbar.pack_end(self.save_button)

        import_button = Gtk.Button.new_from_icon_name('document-open-symbolic')
        import_button.set_tooltip_text(
            _('Import @string macros from a .bib file'))
        import_button.connect('clicked', self._on_import_strings)
        self.headerbar.pack_end(import_button)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(6)
        content.set_margin_start(6)
        content.set_margin_end(6)
        content.set_vexpand(True)
        self.topbox.append(content)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(_('Search string name or value'))
        self.search_entry.connect('search-changed', self._rebuild_rows)
        content.append(self.search_entry)

        # Adw.PreferencesPage 是官方的偏好设置滚动容器（内部自带
        # ScrolledWindow + Clamp，展开行高度测量正确）。必须 vexpand(True)
        # 让它填满剩余空间——若为 False，页面高度=自然高度，窗口偏高时
        # 下方留白、窗口偏矮时内容被 ToolbarView 裁切且无滚动条。
        self.prefs_page = Adw.PreferencesPage()
        self.prefs_page.set_vexpand(True)
        content.append(self.prefs_page)

        self.group = Adw.PreferencesGroup(title=_('@string Macros'))
        self.prefs_page.add(self.group)

        add_button = Gtk.Button.new_from_icon_name('list-add-symbolic')
        add_button.set_tooltip_text(_('New String'))
        add_button.set_valign(Gtk.Align.CENTER)
        add_button.connect('clicked', self._on_add)
        self.group.set_header_suffix(add_button)

    def refresh_from_store(self):
        '''Rebuild the edit buffer from the parent manager's current text.'''
        self._items = []
        if self.parent.store is not None:
            self.parent.store = BibTeXEntryStore(self.parent.loaded_text)
            for string in self.parent.store.list_strings():
                self._items.append(self._make_item(string.name, string.value))
        self._original_names = [item['orig'] for item in self._items]
        self._dirty = False
        self.save_button.set_sensitive(False)
        self._rebuild_rows()

    @staticmethod
    def _make_item(name, value):
        return {
            'orig': name, 'name': name, 'value': value,
            'orig_value': value,
            'row': None, 'name_entry': None, 'value_entry': None,
        }

    def _visible_items(self):
        needle = self.search_entry.get_text().casefold().strip()
        if not needle:
            return list(self._items)
        return [item for item in self._items
                if needle in item['name'].casefold()
                or needle in item['value'].casefold()]

    def _rebuild_rows(self, *args):
        for row in self._row_widgets:
            self.group.remove(row)
        self._row_widgets = []
        for item in self._visible_items():
            row = self._make_row(item)
            self.group.add(row)
            self._row_widgets.append(row)
        if not self._row_widgets:
            empty = Adw.ActionRow(
                title=_('No @string macros'),
                subtitle=_('Use the + button to add one.'))
            self.group.add(empty)
            self._row_widgets.append(empty)

    def _make_row(self, item):
        row = Adw.ExpanderRow()
        item['row'] = row

        # 先 set_text 再 connect，避免重建行时误标 dirty
        name_entry = Adw.EntryRow(title=_('Name'))
        name_entry.set_text(item['name'])
        name_entry.connect('changed', self._on_name_changed, item)
        row.add_row(name_entry)
        item['name_entry'] = name_entry

        value_entry = Adw.EntryRow(title=_('Value'))
        value_entry.set_text(item['value'])
        value_entry.connect('changed', self._on_value_changed, item)
        row.add_row(value_entry)
        item['value_entry'] = value_entry

        delete_button = Gtk.Button.new_from_icon_name('user-trash-symbolic')
        delete_button.add_css_class('flat')
        delete_button.add_css_class('destructive-action')
        delete_button.set_valign(Gtk.Align.CENTER)
        delete_button.set_tooltip_text(_('Delete'))
        delete_button.connect('clicked', self._on_delete, item)
        row.add_suffix(delete_button)

        self._update_row_display(item)
        return row

    @staticmethod
    def _update_row_display(item):
        if item['row'] is None:
            return
        item['row'].set_title(item['name'].strip() or _('(untitled)'))
        item['row'].set_subtitle(item['value'].strip() or _('No value'))

    def _on_name_changed(self, entry, item):
        item['name'] = entry.get_text()
        self._mark_dirty()
        self._update_row_display(item)

    def _on_value_changed(self, entry, item):
        item['value'] = entry.get_text()
        self._mark_dirty()
        self._update_row_display(item)

    def _mark_dirty(self):
        self._dirty = True
        self.save_button.set_sensitive(True)

    def _on_add(self, button):
        item = self._make_item('', '')
        item['orig'] = None  # 标记为本次会话新增
        self._items.append(item)
        self._mark_dirty()
        if self.search_entry.get_text():
            self.search_entry.set_text('')  # 触发一次重建
        else:
            self._rebuild_rows()
        if item['row'] is not None:
            item['row'].set_expanded(True)
            item['name_entry'].grab_focus()

    def _on_delete(self, button, item):
        self._items.remove(item)
        self._mark_dirty()
        self._rebuild_rows()

    def _on_close_requested(self, button):
        if not self._dirty:
            self.close()
            return
        dialog = Adw.AlertDialog(
            heading=_('Discard unsaved changes?'),
            body=_('Changes to @string macros have not been saved yet.'))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('discard', _('Discard'))
        dialog.set_response_appearance(
            'discard', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_discard_response)
        dialog.present(self)

    def _on_discard_response(self, dialog, response):
        if response == 'discard':
            self.close()

    def _on_save(self, button):
        if self.parent.store is None:
            return
        for item in self._items:
            name = item['name'].strip()
            if not name:
                self._show_local_error(_('Enter a macro name first.'))
                return
            if not item['value'].strip():
                self._show_local_error(
                    _('Enter a value for the macro “{name}”.').format(name=name))
                return
        try:
            # store 的增删改方法不修改内部状态、只返回新文本，因此每个
            # 操作都基于最新文本新建 store；全部成功后才一次性写回。
            text = self.parent.loaded_text
            kept = {item['orig'].casefold() for item in self._items
                    if item['orig'] is not None}
            for orig in self._original_names:
                if orig.casefold() not in kept:
                    text = BibTeXEntryStore(text).delete_string(orig)
            for item in self._items:
                name = item['name'].strip()
                if item['orig'] is None:
                    text = BibTeXEntryStore(text).add_string(name, item['value'])
                elif name != item['orig'] or item['value'] != item['orig_value']:
                    text = BibTeXEntryStore(text).update_string(
                        item['orig'], name, item['value'])
        except (BibTeXEntryError, BibTeXExternalChangeError, OSError, UnicodeError) as error:
            self._show_local_error(str(error))
            return
        try:
            self.parent._apply_text(text)
        except (BibTeXEntryError, BibTeXExternalChangeError, OSError, UnicodeError) as error:
            self._show_local_error(str(error))
            return
        self.parent._load_source(self.parent.selected_source)
        self.refresh_from_store()

    def _on_import_strings(self, button):
        if self.parent.store is None:
            return
        if self._dirty:
            # 导入会立即写回并重建缓冲，会丢掉未保存的本地编辑
            self._show_local_error(
                _('Save or discard your changes before importing.'))
            return
        chooser = Gtk.FileDialog()
        chooser.set_title(_('Choose a BibTeX File to Import'))
        filter_ = Gtk.FileFilter()
        filter_.set_name(_('BibTeX Files'))
        filter_.add_suffix('bib')
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_)
        chooser.set_filters(filters)
        chooser.open(self.parent.main_window, None, self._on_import_file_finished)

    def _on_import_file_finished(self, chooser, result):
        try:
            file = chooser.open_finish(result)
        except Exception as error:
            if 'cancel' not in str(error).lower():
                self._show_local_error(str(error))
            return
        path = file.get_path()
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                source_text = handle.read()
        except (OSError, UnicodeError) as error:
            self._show_local_error(str(error))
            return
        try:
            updated, summary = self.parent.store.import_strings(source_text)
        except (BibTeXEntryError, BibTeXExternalChangeError, OSError, UnicodeError) as error:
            self._show_local_error(str(error))
            return
        if updated == self.parent.loaded_text:
            skipped = summary.get('skipped') or []
            errors = summary.get('errors') or []
            message = self._format_import_message(0, skipped, errors)
            self._show_local_error(message)
            return
        try:
            self.parent._apply_text(updated)
        except (BibTeXEntryError, BibTeXExternalChangeError, OSError, UnicodeError) as error:
            self._show_local_error(str(error))
            return
        self.parent._load_source(self.parent.selected_source)
        self.refresh_from_store()
        imported = summary.get('imported') or []
        skipped = summary.get('skipped') or []
        errors = summary.get('errors') or []
        self._show_local_info(self._format_import_message(
            len(imported), skipped, errors))

    @staticmethod
    def _format_import_message(imported_count, skipped, errors):
        parts = [_('Imported {count} new @string macros.').format(count=imported_count)]
        if skipped:
            parts.append(
                _('Skipped {count} duplicate names: {names}.').format(
                    count=len(skipped),
                    names=', '.join(skipped)))
        if errors:
            parts.append(
                _('Errors: {messages}').format(messages='; '.join(errors)))
        return ' '.join(parts)

    def _show_local_error(self, message):
        dialog = Adw.AlertDialog(
            heading=_('Cannot update @string macros'),
            body=message)
        dialog.add_response('ok', _('OK'))
        dialog.set_default_response('ok')
        dialog.present(self)

    def _show_local_info(self, message):
        dialog = Adw.AlertDialog(
            heading=_('@string import complete'),
            body=message)
        dialog.add_response('ok', _('OK'))
        dialog.set_default_response('ok')
        dialog.present(self)
