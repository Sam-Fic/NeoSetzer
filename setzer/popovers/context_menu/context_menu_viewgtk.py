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

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gio, GLib

from setzer.ai_fix.availability import is_ai_agent_enabled
from setzer.app.service_locator import ServiceLocator
from setzer.app.ui_zoom import UIZoomManager
from setzer.app.latex_templates import WRAP_COMMANDS, WRAP_ENVIRONMENTS, INSERT_TEMPLATES


def _current_shortcuts():
    '''当前 keyboard_shortcuts 键位表（菜单 accel 提示的唯一来源）。'''
    settings = ServiceLocator.get_settings()
    shortcuts = settings.get_value('keyboard_shortcuts', None)
    if shortcuts is None:
        shortcuts = settings.defaults['keyboard_shortcuts']
    return shortcuts


def _action_item(label, detailed_action, accel=None):
    '''A Gio.MenuItem for an action, optionally with a parseable accel.

    The ``accel`` attribute is rendered natively by Gtk.PopoverMenu as the
    shortcut label on the right (e.g. ``<Control>z`` -> "Ctrl+Z").

    迁移到 application accel 的动作（forward-sync、AI 批注等）不要传
    ``accel``：GtkPopoverMenu 会自动依据 application accel 渲染提示，
    提示天然随改键更新。仅 document 层控制器持有的键（剪贴板、行操作等，
    实测迁走会劫持输入框的 Ctrl+C/Ctrl+A）需要显式传入——调用方应从
    settings 读取，不要写字面量。'''
    item = Gio.MenuItem.new(label, detailed_action)
    if accel is not None:
        item.set_attribute_value('accel', GLib.Variant('s', accel))
    return item


def _wrap_item(label, before, after):
    '''A Gio.MenuItem that invokes ``win.insert-before-after`` with a [before, after]
    parameter. When a range is selected the wrapped text becomes the payload;
    otherwise the ``•`` placeholder is left for the cursor (the calling action
    handles both cases).

    ``label`` is localised here (at menu-build time) because the template data
    stores raw English strings to avoid calling _() at module import time.'''
    item = Gio.MenuItem.new(_(label), 'win.insert-before-after')
    item.set_action_and_target_value('win.insert-before-after',
                                     GLib.Variant('as', [before, after]))
    return item


def _insert_item(label, template):
    '''A Gio.MenuItem that invokes ``win.insert-symbol`` with a template string.

    ``label`` is localised here (at menu-build time) — see _wrap_item for why.'''
    item = Gio.MenuItem.new(_(label), 'win.insert-symbol')
    item.set_action_and_target_value('win.insert-symbol',
                                     GLib.Variant('as', [template]))
    return item


def _build_submenu_from_list(items, builder_fn):
    '''Build a Gio.Menu submenu from a list of template dicts.

    Each dict is passed to ``builder_fn`` which returns a Gio.MenuItem.
    Returns the populated Gio.Menu.'''
    submenu = Gio.Menu()
    for entry in items:
        submenu.append_item(builder_fn(entry))
    return submenu


class ContextMenuView(Gtk.PopoverMenu):
    '''ShortcutsBar "more" popover (the F12 context menu).

    Built from a ``Gio.Menu`` model on a native ``Gtk.PopoverMenu`` — the same
    form as the hamburger menu — instead of the former hand-built
    ListBox-in-popover. Action items use real GAction targets and parseable
    accelerators. The LaTeX/BibTeX items (Toggle Comment / Show in Preview) live
    in a section rebuilt on active-document changes. The Zoom controls are a
    custom child widget (so the buttons can trigger actions without closing
    the popover and the reset label can be updated dynamically).
    '''

    def __init__(self, popover_manager):
        Gtk.PopoverMenu.__init__(self)
        self.set_size_request(288, -1)

        self.latex_section = Gio.Menu()
        # 拼写建议 section：右键点击拼写错误词时由 rebuild_spell_section()
        # 注入建议/忽略/加词典项，置于菜单最顶部（与主流编辑器一致）。
        # 空section不渲染任何内容（无分隔线），不影响 F12 菜单常态外观。
        self.spell_section = Gio.Menu()
        # AI 批注 section：有选区时由 rebuild_ai_section() 注入「添加批注 /
        # 直发选区」两项；无选区时为空（不渲染）。与 spell 同样不能泄漏进 F12。
        self.ai_section = Gio.Menu()
        # 暴露 model 引用供共享：编辑器右键菜单（workspace ContextMenu.popover_pointer）
        # 用同一份 Gio.Menu model，使两者样式与内容完全一致（LaTeX section 重建对两者同效）。
        self.model = self._build_model()
        self.set_menu_model(self.model)
        self.add_child(self._build_zoom_widget(), 'zoom-controls')
        self.connect('map', self.on_map)
        # 改键后原地重建带 accel 的四段，提示与新键位同步（model 引用不变，
        # 共享同一 model 的编辑器右键菜单同步生效）。
        ServiceLocator.get_settings().connect('settings_changed',
                                              self.on_settings_changed)

    def on_settings_changed(self, settings, parameter):
        section, _item, _value = parameter
        if section == 'keyboard_shortcuts':
            self._populate_action_sections()

    def _build_model(self):
        model = Gio.Menu()

        # 拼写建议在最顶部（默认空），AI 批注紧随其后（默认空）。
        model.prepend_section(None, self.ai_section)
        model.prepend_section(None, self.spell_section)

        # 编辑/剪贴板/选择/行操作四段单独持有引用：键位偏好变化时原地重建
        # （Gio.Menu 引用不变——编辑器右键菜单共享同一 model）。
        self.section_edit = Gio.Menu()
        self.section_clip = Gio.Menu()
        self.section_select = Gio.Menu()
        self.section_lines = Gio.Menu()
        model.append_section(None, self.section_edit)
        model.append_section(None, self.section_clip)
        model.append_section(None, self.section_select)
        model.append_section(None, self.section_lines)

        # 行操作：快捷键已存在（Alt+Up/Down、Alt+Shift+D、Tab/Shift+Tab），
        # 此前仅藏在线下，放进菜单提升可发现性。非 LaTeX 专属，对任何文档可用。
        # （section 内容由 _populate_action_sections() 填充。）

        # 折叠操作：Fold All / Unfold All。对任意文档类型可用，随代码折叠功能
        # 提供。原放在标题栏汉堡菜单，现移入 Shortcut Bar 的汉堡菜单（F12 上下文菜单），
        # 与行操作等编辑类功能同处一处，更易发现。
        section_folding = Gio.Menu()
        section_folding.append_item(_action_item(_('Fold All'), 'win.fold-all'))
        section_folding.append_item(_action_item(_('Unfold All'), 'win.unfold-all'))
        model.append_section(None, section_folding)

        # LaTeX/BibTeX section: rebuilt via rebuild_latex_section().
        model.append_section(None, self.latex_section)

        # Zoom controls as a custom child row.
        zoom_section = Gio.Menu()
        zoom_item = Gio.MenuItem()
        zoom_item.set_attribute_value('custom', GLib.Variant('s', 'zoom-controls'))
        zoom_section.append_item(zoom_item)
        model.append_section(None, zoom_section)

        self._populate_action_sections()
        return model

    def _populate_action_sections(self):
        '''（重）建编辑 / 剪贴板 / 选择 / 行操作四段。

        accel 提示从 keyboard_shortcuts 设置读取，与 ShortcutAccels 注册的
        application accel 同源——改键后提示不漂移。这些键留在 document 层
        控制器（迁到 application accel 会抢走对话框/输入框的 Ctrl+C、
        Ctrl+A，见 shortcut_accels 模块注释），popover 不会为它们自动渲染
        提示，故显式写 accel 属性；Tab/Shift+Tab 不可配置，用固定字面量。'''
        shortcuts = _current_shortcuts()

        self.section_edit.remove_all()
        self.section_edit.append_item(
            _action_item(_('Undo'), 'win.undo', shortcuts.get('undo') or None))
        self.section_edit.append_item(
            _action_item(_('Redo'), 'win.redo', shortcuts.get('redo') or None))

        self.section_clip.remove_all()
        self.section_clip.append_item(
            _action_item(_('Cut'), 'win.cut', shortcuts.get('cut') or None))
        self.section_clip.append_item(
            _action_item(_('Copy'), 'win.copy', shortcuts.get('copy') or None))
        self.section_clip.append_item(
            _action_item(_('Paste'), 'win.paste', shortcuts.get('paste') or None))
        self.section_clip.append_item(_action_item(_('Delete'), 'win.delete-selection'))

        self.section_select.remove_all()
        self.section_select.append_item(
            _action_item(_('Select All'), 'win.select-all',
                         shortcuts.get('select_all') or None))

        self.section_lines.remove_all()
        self.section_lines.append_item(
            _action_item(_('Duplicate Line'), 'win.duplicate-line',
                         shortcuts.get('duplicate_line') or None))
        self.section_lines.append_item(
            _action_item(_('Move Line Up'), 'win.move-line-up',
                         shortcuts.get('move_line_up') or None))
        self.section_lines.append_item(
            _action_item(_('Move Line Down'), 'win.move-line-down',
                         shortcuts.get('move_line_down') or None))
        self.section_lines.append_item(_action_item(_('Indent'), 'win.indent', '<Tab>'))
        self.section_lines.append_item(_action_item(_('Unindent'), 'win.outdent', '<Shift>Tab'))

    def _build_zoom_widget(self):
        box = Gtk.CenterBox()
        box.set_orientation(Gtk.Orientation.HORIZONTAL)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        zoom_label = Gtk.Label(label=_('Zoom'))
        box.set_start_widget(zoom_label)

        inner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        button_zoom_out = Gtk.Button()
        button_zoom_out.set_icon_name('value-decrease-symbolic')
        button_zoom_out.add_css_class('flat')
        button_zoom_out.set_action_name('win.zoom-out')
        inner_box.append(button_zoom_out)

        self.reset_zoom_button = Gtk.Button.new_with_label(UIZoomManager.get_zoom_percent())
        self.reset_zoom_button.add_css_class('flat')
        self.reset_zoom_button.set_action_name('win.reset-zoom')
        inner_box.append(self.reset_zoom_button)

        button_zoom_in = Gtk.Button()
        button_zoom_in.set_icon_name('value-increase-symbolic')
        button_zoom_in.add_css_class('flat')
        button_zoom_in.set_action_name('win.zoom-in')
        inner_box.append(button_zoom_in)

        box.set_end_widget(inner_box)
        return box

    def rebuild_spell_section(self, word, offset, suggestions):
        r'''Populate (or clear) the spell checking section.

        Called with a misspelled ``word`` at buffer ``offset`` before the
        right-click popover is shown: suggestion items come first (invoking
        ``win.spell-replace`` with ``[offset, word, suggestion]``), followed
        by "Ignore …" (``win.spell-ignore``) and "Add … to Dictionary"
        (``win.spell-add``).  Pass ``word=None`` to clear.

        The offset travels through the action target because the popover
        stays open without edits — offsets remain valid; the replace action
        re-verifies the text at the offset before substituting.
        '''
        self.spell_section.remove_all()
        if word is None:
            return

        for suggestion in suggestions:
            item = Gio.MenuItem.new(suggestion, 'win.spell-replace')
            item.set_action_and_target_value(
                'win.spell-replace',
                GLib.Variant('as', [str(offset), word, suggestion]))
            self.spell_section.append_item(item)

        item_ignore = Gio.MenuItem.new(
            _('Ignore “{word}”').format(word=word), 'win.spell-ignore')
        item_ignore.set_action_and_target_value(
            'win.spell-ignore', GLib.Variant('s', word))
        self.spell_section.append_item(item_ignore)

        item_add = Gio.MenuItem.new(
            _('Add “{word}” to Dictionary').format(word=word), 'win.spell-add')
        item_add.set_action_and_target_value(
            'win.spell-add', GLib.Variant('s', word))
        self.spell_section.append_item(item_add)

    def rebuild_ai_section(self, has_selection):
        '''Populate (or clear) the AI annotation section.

        有选区且 AI Agent 启用时显示两项：添加批注（落模型，稍后批量发）
        与直发选区（不落批注，立即发送）。快捷键可配置，不在此硬编码 accel。
        总开关关闭时 section 保持为空——右键菜单里不出现 AI 入口。
        '''
        self.ai_section.remove_all()
        if not has_selection or not is_ai_agent_enabled():
            return
        self.ai_section.append_item(
            _action_item(_('Add AI Annotation…'), 'win.ai-add-annotation'))
        self.ai_section.append_item(
            _action_item(_('Send Selection to Agent'), 'win.ai-send-selection'))

    def rebuild_latex_section(self, document, label=None):
        r'''Populate (or clear) the LaTeX/BibTeX section for the active document.

        "Toggle Comment" is shown for any document (LaTeX, BibTeX, etc.),
        while "Show in Preview" and the LaTeX-specific sub-menus are limited
        to LaTeX documents.  The three sub-menus (Wrap in Command / Wrap in
        Environment / Insert Template) provide quick access to common LaTeX
        constructs via the existing ``insert-before-after`` and ``insert-symbol``
        G-actions (registered in ``Actions``) — no new action plumbing needed.

        If ``label`` is provided (right-click on a \ref-like command), additional
        label-specific items are shown: Jump to Definition, Copy \ref{label},
        Copy \pageref{label}, Copy \autoref{label}, and Find All References.
        '''
        self.latex_section.remove_all()
        if document is None:
            return

        self.latex_section.append_item(
            _action_item(_('Toggle Comment'), 'win.toggle-comment',
                         _current_shortcuts().get('toggle_comment') or None))

        if document.is_latex_document():
            # -- Label context items (right-click on \ref{...}) -----------------
            if label is not None:
                label_section = Gio.Menu()
                label_section.append_item(
                    _action_item(_('Jump to Definition'), 'win.jump-to-definition::' + label))
                # PhiTeX 直查：label 指向内容的实际排版位置 / 裁剪图（watch
                # 的 where/png 子命令；其它引擎没有这条数据，不出现）。
                build_system = getattr(document, 'build_system', None)
                phitex_live = (build_system is not None
                               and build_system.is_phitex_live_build())
                if phitex_live:
                    label_section.append_item(
                        _action_item(_('Show in PDF'), 'win.show-label-in-pdf::' + label))
                label_section.append_item(
                    _action_item(_('Copy \\ref{' + label + '}'), 'win.copy-ref::' + label))
                label_section.append_item(
                    _action_item(_('Copy \\pageref{' + label + '}'), 'win.copy-pageref::' + label))
                label_section.append_item(
                    _action_item(_('Copy \\autoref{' + label + '}'), 'win.copy-autoref::' + label))
                if phitex_live:
                    label_section.append_item(
                        _action_item(_('Copy as PNG'), 'win.copy-label-png::' + label))
                label_section.append_item(
                    _action_item(_('Find All References'), 'win.find-all-refs::' + label))
                self.latex_section.append_section(None, label_section)

            # -- Wrap in Command sub-menu ---------------------------------------
            wrap_cmds_submenu = _build_submenu_from_list(
                WRAP_COMMANDS,
                lambda e: _wrap_item(e['label'], e['before'], e['after']))
            self.latex_section.append_submenu(
                _('Wrap in Command'), wrap_cmds_submenu)

            # -- Wrap in Environment sub-menu ----------------------------------
            wrap_envs_submenu = _build_submenu_from_list(
                WRAP_ENVIRONMENTS,
                lambda e: _wrap_item(e['label'], e['before'], e['after']))
            self.latex_section.append_submenu(
                _('Wrap in Environment'), wrap_envs_submenu)

            # -- Insert Template sub-menu --------------------------------------
            insert_submenu = _build_submenu_from_list(
                INSERT_TEMPLATES,
                lambda e: _insert_item(e['label'], e['template']))
            self.latex_section.append_submenu(
                _('Insert Template'), insert_submenu)

            # -- Show in Preview ------------------------------------------------
            # accel 不写：win.forward-sync 已是 application accel，提示由
            # popover 依实际键位自动渲染（默认 F7，改键后自动跟随）。
            self.latex_section.append_item(
                _action_item(_('Show in Preview'), 'win.forward-sync'))

            # -- Text transformations -----------------------------------------
            # Useful for both LaTeX prose and BibTeX field bodies; exposed
            # from the editor right-click menu so users do not need to open
            # the bibliography manager for one-shot edits.
            transform_section = Gio.Menu()
            transform_section.append_item(
                _action_item(_('Protect Upper-Case'),
                             'win.transform-text::protect'))
            transform_section.append_item(
                _action_item(_('Unicode → LaTeX'),
                             'win.transform-text::unicode-to-latex'))
            transform_section.append_item(
                _action_item(_('LaTeX → Unicode'),
                             'win.transform-text::latex-to-unicode'))
            self.latex_section.append_submenu(
                _('Transform Selection'), transform_section)

    def on_map(self, popover):
        popover.grab_focus()
