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
# along with this program. If not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, Gdk
import json
import re

from setzer.app.service_locator import ServiceLocator
from setzer.keyboard_shortcuts import shortcut_tooltips
from setzer.keyboard_shortcuts.shortcut_accels import SETTINGS_KEY_TO_ACTION


# 修饰符标准顺序，与 setzer/settings/settings.py 中 keyboard_shortcuts
# 默认值书写顺序一致（<Control><Shift><Alt><Super>）。find_conflicting_action
# 用字符串相等判断冲突，因此 Gtk.accelerator_name 返回的任意顺序（如
# '<Shift><Control>s'）都必须归一化到该顺序才能正确匹配。
_CANONICAL_MOD_ORDER = ('Control', 'Shift', 'Alt', 'Super')

# 这些 keysym 只是修饰键本身（Ctrl / Shift / Alt / …），单独按下不应被当作
# 快捷键，而是等待主键。
_MODIFIER_KEYVAL_NAMES = {
    'Control_L', 'Control_R', 'Shift_L', 'Shift_R', 'Alt_L', 'Alt_R',
    'Super_L', 'Super_R', 'Meta_L', 'Meta_R', 'ISO_Level3_Shift',
    'ISO_Level5_Shift', 'Num_Lock', 'Caps_Lock', 'Scroll_Lock',
}


def _normalize_shortcut(raw):
    '''把 Gtk.accelerator_name 的输出重排成与默认值一致的标准顺序。'''
    mods = re.findall(r'<([^>]+)>', raw)
    key = re.sub(r'<[^>]+>', '', raw)
    ordered = [m for m in _CANONICAL_MOD_ORDER if m in mods]
    ordered += [m for m in mods if m not in _CANONICAL_MOD_ORDER]
    return ''.join('<%s>' % m for m in ordered) + key


class PageShortcuts(object):

    def __init__(self, preferences, settings):
        self.view = PageShortcutsView()
        self.preferences = preferences
        self.settings = settings
        self.main_window = ServiceLocator.get_main_window()
        self.shortcut_entries = dict()
        self.current_shortcut_button = None
        self.key_controller = None
        self._pending_assignment = None
        self._pending_import = None
        # title/description 字典懒加载缓存：首次调用 get_action_title/
        # get_action_description 时构建（此时 _() 已由 gettext 注入），
        # 后续直接读缓存，不再每次 add_shortcut_row 重建 ~100 项的字典。
        self._action_titles = None
        self._action_descriptions = None

    def init(self):
        self.load_shortcuts()
        self.view.reset_button.connect('clicked', self.on_reset_clicked)
        self.view.import_button.connect('clicked', self.on_import_clicked)
        self.view.export_button.connect('clicked', self.on_export_clicked)

    def load_shortcuts(self):
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = self.settings.defaults['keyboard_shortcuts']
        
        for action_name, shortcut in shortcuts.items():
            self.add_shortcut_row(action_name, shortcut)

    def add_shortcut_row(self, action_name, shortcut):
        row = Adw.ActionRow()
        row.set_title(self.get_action_title(action_name))
        row.set_subtitle(self.get_action_description(action_name))
        
        shortcut_button = Gtk.Button()
        shortcut_button.set_label(self.format_shortcut(shortcut))
        shortcut_button.set_valign(Gtk.Align.CENTER)
        shortcut_button.connect('clicked', self.on_shortcut_button_clicked, action_name)
        row.add_suffix(shortcut_button)
        
        self.shortcut_entries[action_name] = {
            'row': row,
            'button': shortcut_button,
            'shortcut': shortcut
        }
        
        self.view.shortcuts_group.add(row)

    def get_action_title(self, action_name):
        if self._action_titles is None:
            self._action_titles = {
                'new_document': _('New document'),
                'open_document': _('Open document'),
                'save': _('Save'),
                'save_as': _('Save as'),
                'print': _('Print'),
                'close_document': _('Close document'),
                'quit': _('Quit'),
                'show_shortcuts': _('Show keyboard shortcuts'),
                'show_open_docs': _('Show open documents'),
                'switch_document': _('Switch document'),
                'show_document_chooser': _('Show document chooser'),
                'zoom_in': _('Zoom in'),
                'zoom_out': _('Zoom out'),
                'reset_zoom': _('Reset zoom'),
                'find': _('Find'),
                'find_and_replace': _('Find and replace'),
                'find_next': _('Find next'),
                'find_previous': _('Find previous'),
                'help': _('Help'),
                'document_structure': _('Document structure'),
                'symbols': _('Symbols'),
                'save_and_build': _('Save and build'),
                'build': _('Build'),
                'forward_sync': _('Forward sync'),
                'build_log': _('Build log'),
                'preview': _('Preview'),
                'command_palette': _('Command palette'),
                'hamburger_menu': _('Hamburger menu'),
                'context_menu': _('Context menu'),
                'cut': _('Cut'),
                'copy': _('Copy'),
                'paste': _('Paste'),
                'undo': _('Undo'),
                'redo': _('Redo'),
                'select_all': _('Select all'),
                'toggle_comment': _('Toggle comment'),
                'delete_line': _('Delete line'),
                'duplicate_line': _('Duplicate line'),
                'move_line_up': _('Move line up'),
                'move_line_down': _('Move line down'),
                'new_line': _('New line'),
                'bold': _('Bold'),
                'italic': _('Italic'),
                'underline': _('Underline'),
                'typewriter': _('Typewriter'),
                'emphasized': _('Emphasized'),
                'quotation_marks': _('Quotation marks'),
                'list_item': _('List item'),
                'environment': _('Environment'),
                'inline_math': _('Inline math'),
                'display_math': _('Display math'),
                'equation': _('Equation'),
                'subscript': _('Subscript'),
                'superscript': _('Superscript'),
                'fraction': _('Fraction'),
                'left': _('Left'),
                'right': _('Right'),
                'show_preferences_dialog': _('Preferences'),
                'show_about_dialog': _('About NeoSetzer'),
                'insert_table_dialog': _('Insert table'),
                'insert_matrix_dialog': _('Insert matrix'),
                'close_all_documents': _('Close all documents'),
                'restore_session': _('Restore session'),
                'reopen_last_closed_document': _('Reopen last closed document'),
                'pin_tab': _('Pin tab'),
                'select_next_occurrence': _('Select next occurrence'),
                'select_all_occurrences': _('Select all occurrences'),
                'add_cursor_above': _('Add cursor above'),
                'add_cursor_below': _('Add cursor below'),
                'clear_multi_cursor': _('Clear multi-cursor'),
                'ai_add_annotation': _('Add AI annotation'),
                'ai_send_selection': _('Send selection to agent')
            }
        return self._action_titles.get(action_name, action_name)

    def get_action_description(self, action_name):
        if self._action_descriptions is None:
            self._action_descriptions = {
                'new_document': _('Create a new LaTeX document'),
                'open_document': _('Open an existing document'),
                'save': _('Save the current document'),
                'save_as': _('Save the active document with a new filename'),
                'print': _('Print the active document'),
                'close_document': _('Close the active document'),
                'quit': _('Quit the application'),
                'show_shortcuts': _('Show keyboard shortcuts dialog'),
                'show_open_docs': _('Show list of open documents'),
                'switch_document': _('Switch to another open document'),
                'show_document_chooser': _('Show document chooser popover'),
                'zoom_in': _('Zoom in the whole application'),
                'zoom_out': _('Zoom out the whole application'),
                'reset_zoom': _('Reset the application zoom to 100%'),
                'find': _('Find text in document'),
                'find_and_replace': _('Find and replace text'),
                'find_next': _('Find next match'),
                'find_previous': _('Find previous match'),
                'help': _('Toggle help panel'),
                'document_structure': _('Toggle document structure panel'),
                'symbols': _('Toggle symbols panel'),
                'save_and_build': _('Save and build PDF'),
                'build': _('Build PDF without saving'),
                'forward_sync': _('Sync PDF position to editor'),
                'build_log': _('Toggle build log panel'),
                'preview': _('Toggle preview panel'),
                'command_palette': _('Search and run application commands'),
                'hamburger_menu': _('Show hamburger menu'),
                'context_menu': _('Show context menu'),
                'cut': _('Cut selected text'),
                'copy': _('Copy selected text'),
                'paste': _('Paste from clipboard'),
                'undo': _('Undo last action'),
                'redo': _('Redo last action'),
                'select_all': _('Select all text'),
                'toggle_comment': _('Comment/uncomment lines'),
                'delete_line': _('Delete the current line'),
                'duplicate_line': _('Duplicate the current line'),
                'move_line_up': _('Move the current line up'),
                'move_line_down': _('Move the current line down'),
                'new_line': _('Insert new line'),
                'bold': _('Insert bold text'),
                'italic': _('Insert italic text'),
                'underline': _('Insert underlined text'),
                'typewriter': _('Insert typewriter text'),
                'emphasized': _('Insert emphasized text'),
                'quotation_marks': _('Insert quotation marks'),
                'list_item': _('Insert list item'),
                'environment': _('Insert environment'),
                'inline_math': _('Insert inline math'),
                'display_math': _('Insert display math'),
                'equation': _('Insert equation'),
                'subscript': _('Insert subscript'),
                'superscript': _('Insert superscript'),
                'fraction': _('Insert fraction'),
                'left': _('Insert \\left'),
                'right': _('Insert \\right'),
                'show_preferences_dialog': _('Open the preferences dialog'),
                'show_about_dialog': _('Show the about dialog'),
                'close_all_documents': _('Close all open documents'),
                'restore_session': _('Restore a previously saved session'),
                'reopen_last_closed_document': _('Reopen the most recently closed document'),
                'pin_tab': _('Pin or unpin the active document tab'),
                'select_next_occurrence': _('Select the next occurrence of the selected text or word (multi-cursor)'),
                'select_all_occurrences': _('Select all occurrences of the selected text or word (multi-cursor)'),
                'add_cursor_above': _('Add a cursor on the line above (multi-cursor)'),
                'add_cursor_below': _('Add a cursor on the line below (multi-cursor)'),
                'clear_multi_cursor': _('Clear all additional cursors (multi-cursor)'),
                'ai_add_annotation': _('Annotate the selected text for the AI agent'),
                'ai_send_selection': _('Send the selected text to the AI agent immediately')
            }
        return self._action_descriptions.get(action_name, '')

    def format_shortcut(self, shortcut):
        # 与 tooltip 共用同一渲染逻辑（shortcut_tooltips.format_shortcut），
        # 保证偏好页按钮标签和各控件 tooltip 显示完全一致；并且能把
        # '<Control>quotedbl' 这类 keysym 渲染成 'Ctrl+"' 而非 'CTRL + QUOTEDBL'。
        if not shortcut:
            return _('Not set')
        return shortcut_tooltips.format_shortcut(shortcut)

    def on_shortcut_button_clicked(self, button, action_name):
        if self.current_shortcut_button:
            self.cancel_shortcut_capture()

        self.current_shortcut_button = button
        self.current_action_name = action_name

        button.set_label(_('Press shortcut…'))
        button.add_css_class('suggested-action')
        # tooltip 补充 Escape 取消提示：group description 已提及，但用户
        # 点击按钮后视线聚焦在按钮上，可能看不到顶部的 group 描述。
        button.set_tooltip_text(_('Press desired key combination, or Escape to cancel'))

        # EventControllerKey 加到按钮本身（而非 main_window）：作用域限定在
        # 对话框内，避免用户切换到其他窗口时仍被拦截按键。按钮被点击后
        # 自动获得焦点，CAPTURE 阶段让控制器在按钮处理（如 Space 激活）前
        # 拿到 key-pressed 事件。
        self.key_controller = Gtk.EventControllerKey()
        self.key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.key_controller.connect('key-pressed', self.on_key_pressed)
        button.add_controller(self.key_controller)
        button.grab_focus()

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.cancel_shortcut_capture()
            return True

        shortcut = self.event_to_shortcut(keyval, state)
        if shortcut:
            # 重申自己当前的绑定不算冲突（用户只是确认了原键）。
            shortcuts = self.settings.get_value('keyboard_shortcuts', None)
            if shortcuts is None:
                shortcuts = self.settings.defaults['keyboard_shortcuts']
            if shortcut != shortcuts.get(self.current_action_name):
                hardcoded = self.find_hardcoded_conflict(shortcut)
                if hardcoded is not None:
                    # 固定功能不可夺走：提示换键，不落盘。
                    self._show_hardcoded_conflict_dialog(shortcut, hardcoded)
                    self.cancel_shortcut_capture()
                    return True
            conflicting_action = self.find_conflicting_action(self.current_action_name, shortcut)
            if conflicting_action is not None:
                # 有冲突：弹确认对话框，让用户决定是否从其他操作夺走此快捷键。
                # cancel_shortcut_capture 在对话框弹出后立即执行，但
                # _pending_assignment 已保存待分配信息，对话框回调中使用。
                self._show_conflict_dialog(self.current_action_name, shortcut, conflicting_action)
            else:
                self.set_shortcut(self.current_action_name, shortcut)
            self.cancel_shortcut_capture()
            return True

        return False

    def find_hardcoded_conflict(self, shortcut):
        """Return the display label of a fixed (non-rebindable) function bound
        to ``shortcut``, or ``None``.

        Ctrl+L（跳转行）、Ctrl+Y（重做别名）与 Insert（覆写切换）不在
        keyboard_shortcuts 设置里、无法重绑定，常规冲突检测看不到它们——
        不拦的话用户可以把可配置键绑上去，改了却不生效。F3 / Shift+F3 已作
        为 win.find-next / win.find-previous 的 accel 别名注册，
        find_conflicting_action 扫 application accel 时天然可见，无需硬列。
        Alt+Up/Down 不在此列：编辑态语义（移动行）本身就是可配置项，
        常规检测已覆盖；非编辑态的章节导航与编辑态互斥。"""
        labels = (
            ('<Control>l', _('Go to line')),
            ('<Control>y', _('Redo')),
            ('Insert', _('Toggle insert / overwrite')),
        )
        for hardcoded, label in labels:
            if shortcut == hardcoded:
                return label
        return None

    def _show_hardcoded_conflict_dialog(self, shortcut, label):
        dialog = Adw.AlertDialog(
            heading=_('Shortcut already in use'),
            body=_('“{shortcut}” is reserved for “{function}”, which cannot be rebound. Choose a different key combination.').format(
                shortcut=shortcut, function=label))
        dialog.add_response('close', _('Close'))
        dialog.set_default_response('close')
        dialog.set_close_response('close')
        dialog.choose(self.preferences.view, None, None)

    def find_conflicting_action(self, action_name, shortcut):
        '''Return the name of another action already bound to ``shortcut``,
        or ``None`` if there is no conflict.

        已迁移到 application accel 的键以 app.get_accels_for_action 的返回
        为权威来源（同时覆盖主绑定与 F3 之类的固定别名）；未迁移的键
        （document 层剪贴板键、LaTeX 插入键）回落到 settings 里的绑定值。'''
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = self.settings.defaults['keyboard_shortcuts']
        target = _normalize_shortcut(shortcut)
        app = self.main_window.app
        for other_action, other_shortcut in shortcuts.items():
            if other_action == action_name:
                continue
            effective = []
            detailed_action = SETTINGS_KEY_TO_ACTION.get(other_action)
            if detailed_action is not None:
                effective = app.get_accels_for_action(detailed_action)
            if not effective and other_shortcut:
                effective = [other_shortcut]
            for accel in effective:
                if accel and _normalize_shortcut(accel) == target:
                    return other_action
        return None

    def _show_conflict_dialog(self, action_name, shortcut, conflicting_action):
        self._pending_assignment = (action_name, shortcut, conflicting_action)
        other_title = self.get_action_title(conflicting_action)
        dialog = Adw.AlertDialog(
            heading=_('Shortcut already in use'),
            body=_('This shortcut is currently assigned to "{other}". '
                   'Reassigning it will remove the shortcut from that action.').format(other=other_title))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('reassign', _('Reassign'))
        dialog.set_response_appearance('reassign', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self.preferences.view, None, self.on_conflict_dialog_response)

    def on_conflict_dialog_response(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'reassign' and self._pending_assignment is not None:
            action_name, shortcut, conflicting_action = self._pending_assignment
            self.set_shortcut(conflicting_action, '')
            self.set_shortcut(action_name, shortcut)
        self._pending_assignment = None

    def event_to_shortcut(self, keyval, state):
        '''把一次按键事件转换成与 settings 中存储格式一致的快捷键串。

        必须用 Gtk.accelerator_name（返回 '<Control>c' / 'F5' 这类规范串），
        绝不能用 Gtk.accelerator_get_label（后者返回 'Ctrl+C' 这种含修饰符的
        展示标签）。旧实现把展示标签当键名拼接，得到的串形如
        '<Control><Ctrl+C>'，既无法被 Gtk.ShortcutTrigger.parse_string 解析，
        也和已存的 '<Control>c' 对不上——这导致 find_conflicting_action
        永远匹配不到冲突，即用户能静默制造重复绑定（偏好页“没有冲突检测”
        的真正根因）。'''
        # 单独按下修饰键（Ctrl / Shift / Alt / …）不算完成，等待主键。
        if Gdk.keyval_name(keyval) in _MODIFIER_KEYVAL_NAMES:
            return None

        # 忽略 CapsLock / NumLock 等锁存修饰符，它们不应构成快捷键的一部分。
        state &= Gtk.accelerator_get_default_mod_mask()

        raw = Gtk.accelerator_name(keyval, state)
        if not raw:
            return None

        # 无修饰符时，只有功能键（F1-F12）与特殊键（Tab/Return/...）允许单独
        # 绑定；单个可输入字符（字母/数字/标点）会被正常打字需要，拒绝。
        key = re.sub(r'<[^>]+>', '', raw)
        if state == 0 and key and len(key) == 1:
            return None

        return _normalize_shortcut(raw)

    def set_shortcut(self, action_name, shortcut):
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = dict(self.settings.defaults['keyboard_shortcuts'])

        shortcuts[action_name] = shortcut
        # 写设置即触发 settings_changed：ShortcutAccels 随之重注册
        # application accel、Shortcuts 重建 document 层控制器，无需在此
        # 手动同步（批量导入/重置也走同一路径）。
        self.settings.set_value('keyboard_shortcuts', None, shortcuts)

        if action_name in self.shortcut_entries:
            self.shortcut_entries[action_name]['shortcut'] = shortcut
            self.shortcut_entries[action_name]['button'].set_label(self.format_shortcut(shortcut))

    def cancel_shortcut_capture(self):
        if self.key_controller and self.current_shortcut_button:
            self.current_shortcut_button.remove_controller(self.key_controller)
        self.key_controller = None

        if self.current_shortcut_button:
            self.current_shortcut_button.remove_css_class('suggested-action')
            self.current_shortcut_button.set_tooltip_text('')
            action_name = self.current_action_name
            if action_name in self.shortcut_entries:
                shortcut = self.shortcut_entries[action_name]['shortcut']
                self.current_shortcut_button.set_label(self.format_shortcut(shortcut))

        self.current_shortcut_button = None
        self.current_action_name = None

    def _write_shortcuts(self, shortcuts):
        '''一次性写入整张键位表（重置/导入用）并刷新全部行。

        单次 settings_changed 触发一次 accel 重注册 + document 控制器重建；
        若逐键 set_value 会把这两步各重复 N 遍。'''
        self.settings.set_value('keyboard_shortcuts', None, shortcuts)
        for action_name, entry in self.shortcut_entries.items():
            shortcut = shortcuts.get(action_name, '')
            entry['shortcut'] = shortcut
            entry['button'].set_label(self.format_shortcut(shortcut))

    def on_reset_clicked(self, button):
        dialog = Adw.AlertDialog(
            heading=_('Reset to defaults?'))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('reset', _('Reset'))
        dialog.set_response_appearance('reset', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self.preferences.view, None, self.on_reset_confirmed)

    def on_reset_confirmed(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'reset':
            self._write_shortcuts(dict(self.settings.defaults['keyboard_shortcuts']))

    def on_import_clicked(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title(_('Import keyboard shortcuts'))
        
        filter_json = Gtk.FileFilter()
        filter_json.set_name(_('JSON files'))
        filter_json.add_pattern('*.json')
        
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_json)
        dialog.set_filters(filters)
        
        dialog.open(self.main_window, None, self.on_import_response)

    def on_import_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                with open(file.get_path(), 'r', encoding='utf-8') as f:
                    raw = json.load(f)

                # 仅保留已知 action；未知 action 跳过。导入串统一规范化为存储
                # 格式（也顺带校验：无法解析的串视为无效而跳过），保证后续冲突
                # 比较基于一致的字符串。
                imported = {}
                skipped_unknown = 0
                skipped_invalid = 0
                for action_name, shortcut in raw.items():
                    if action_name not in self.settings.defaults['keyboard_shortcuts']:
                        skipped_unknown += 1
                        continue
                    normalized = self._normalize_imported_shortcut(shortcut)
                    if normalized is None:
                        skipped_invalid += 1
                        continue
                    imported[action_name] = normalized

                # 合并“当前设置 + 导入覆盖”，检测重复绑定冲突。冲突可能来自
                # 导入文件内部（两个 action 同键），也可能来自导入项与“未参与
                # 导入的现有项”相撞。
                current = dict(self.settings.get_value('keyboard_shortcuts', None)
                               or self.settings.defaults['keyboard_shortcuts'])
                merged = dict(current)
                merged.update(imported)
                conflicts = self._find_conflicts_in_set(merged)

                if conflicts:
                    self._pending_import = (imported, skipped_unknown, skipped_invalid, conflicts)
                    self._show_import_conflict_dialog(conflicts)
                    return

                self._apply_import(imported, skipped_unknown, skipped_invalid, resolve=False)
        except Exception as e:
            self._show_toast(_('Failed to import shortcuts: {error}').format(error=str(e)))

    def _normalize_imported_shortcut(self, shortcut):
        '''把导入的快捷键串规范化为存储格式；无法解析则返回 None（无效）。'''
        if not shortcut:
            return ''  # 未分配
        _ok, keyval, mods = Gtk.accelerator_parse(shortcut)
        if keyval == 0:
            return None
        return _normalize_shortcut(Gtk.accelerator_name(keyval, mods))

    def _find_conflicts_in_set(self, shortcuts):
        '''返回 [(shortcut, [action, ...]), ...]，即被 >1 个 action 绑定的快捷键
        （未分配 '' 不计入）。'''
        by_shortcut = {}
        for action, shortcut in shortcuts.items():
            if not shortcut:
                continue
            by_shortcut.setdefault(shortcut, []).append(action)
        return [(sc, acts) for sc, acts in by_shortcut.items() if len(acts) > 1]

    def _resolve_conflicts(self, merged, imported):
        '''就地解决合并集里的重复绑定，保证写回后无冲突。

        规则：每个冲突组内保留一个“胜出项”，其余清空。胜出项优先取导入项，
        同一组内多个导入项则取合并顺序中最后出现的那一个。'''
        for _sc, acts in self._find_conflicts_in_set(merged):
            winner = None
            for action in acts:
                if action in imported:
                    winner = action  # 最后一个导入项胜出
            if winner is None:
                winner = acts[-1]
            for action in acts:
                if action != winner:
                    merged[action] = ''
        return merged

    def _apply_import(self, imported, skipped_unknown, skipped_invalid, resolve):
        current = dict(self.settings.get_value('keyboard_shortcuts', None)
                       or self.settings.defaults['keyboard_shortcuts'])
        merged = dict(current)
        merged.update(imported)
        if resolve:
            merged = self._resolve_conflicts(merged, imported)

        self._write_shortcuts(merged)

        if skipped_unknown and skipped_invalid:
            self._show_toast(_('Imported {n} shortcuts, skipped {m} unknown actions '
                               'and {k} invalid shortcuts.').format(
                n=len(imported), m=skipped_unknown, k=skipped_invalid))
        elif skipped_unknown:
            self._show_toast(_('Imported {n} shortcuts, skipped {m} unknown actions.').format(
                n=len(imported), m=skipped_unknown))
        elif skipped_invalid:
            self._show_toast(_('Imported {n} shortcuts, skipped {m} invalid shortcuts.').format(
                n=len(imported), m=skipped_invalid))
        else:
            self._show_toast(_('Imported {n} shortcuts').format(n=len(imported)))

    def _show_import_conflict_dialog(self, conflicts):
        details = '\n'.join(
            '• ' + self.format_shortcut(sc) + ': ' +
            ', '.join(self.get_action_title(a) for a in acts)
            for sc, acts in conflicts)
        body = _('The imported file conflicts with existing or other imported '
                 'shortcuts ({n} conflict(s)). Proceeding will assign each '
                 'conflicting shortcut to the imported action and clear it from '
                 'the other action(s).').format(n=len(conflicts))
        dialog = Adw.AlertDialog(
            heading=_('Shortcut conflicts in import'),
            body=body + '\n\n' + details)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('resolve', _('Import and resolve'))
        dialog.set_response_appearance('resolve', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self.preferences.view, None, self.on_import_conflict_response)

    def on_import_conflict_response(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'resolve' and self._pending_import is not None:
            imported, skipped_unknown, skipped_invalid, _conflicts = self._pending_import
            self._apply_import(imported, skipped_unknown, skipped_invalid, resolve=True)
        self._pending_import = None

    def on_export_clicked(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title(_('Export keyboard shortcuts'))

        filter_json = Gtk.FileFilter()
        filter_json.set_name(_('JSON files'))
        filter_json.add_pattern('*.json')

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_json)
        dialog.set_filters(filters)

        dialog.save(self.main_window, None, self.on_export_response)

    def on_export_response(self, dialog, result):
        try:
            file = dialog.save_finish(result)
            if file:
                shortcuts = self.settings.get_value('keyboard_shortcuts', None)
                if shortcuts is None:
                    shortcuts = self.settings.defaults['keyboard_shortcuts']

                with open(file.get_path(), 'w', encoding='utf-8') as f:
                    json.dump(shortcuts, f, indent=2)
                # 导出成功不发 toast：选择器关闭即确认（非必要不打扰）
        except Exception as e:
            self._show_toast(_('Failed to export shortcuts: {error}').format(error=str(e)))

    def _show_toast(self, message):
        '''显示 toast 通知（导入/导出结果反馈）。

        发到偏好对话框：主窗口被设置窗口盖住，发过去等于用户看不见。
        '''
        toast = Adw.Toast.new(message)
        toast.set_timeout(5)
        view = getattr(self.preferences, 'view', None)
        if view is not None and hasattr(view, 'add_toast'):
            view.add_toast(toast)
            return
        main_window = ServiceLocator.get_main_window()
        if main_window and hasattr(main_window, 'toast_overlay'):
            main_window.toast_overlay.add_toast(toast)


class PageShortcutsView(Adw.PreferencesPage):

    def __init__(self):
        Adw.PreferencesPage.__init__(self)
        self.set_title(_('Keyboard shortcuts'))
        self.set_icon_name('input-keyboard-symbolic')

        self.shortcuts_group = Adw.PreferencesGroup()
        self.shortcuts_group.set_title(_('Keyboard shortcuts'))
        self.shortcuts_group.set_description(_('Click a shortcut button to change it. Press Escape to cancel.'))
        self.add(self.shortcuts_group)

        group_actions = Adw.PreferencesGroup()
        group_actions.set_title(_('Actions'))
        self.add(group_actions)

        export_row = Adw.ActionRow()
        export_row.set_title(_('Export shortcuts'))
        export_row.set_subtitle(_('Save keyboard shortcuts to a JSON file'))
        self.export_button = Gtk.Button(label=_('Export'))
        self.export_button.set_valign(Gtk.Align.CENTER)
        export_row.add_suffix(self.export_button)
        group_actions.add(export_row)

        import_row = Adw.ActionRow()
        import_row.set_title(_('Import shortcuts'))
        import_row.set_subtitle(_('Load keyboard shortcuts from a JSON file'))
        self.import_button = Gtk.Button(label=_('Import'))
        self.import_button.set_valign(Gtk.Align.CENTER)
        import_row.add_suffix(self.import_button)
        group_actions.add(import_row)

        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)