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
# along with this program. If not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk
import json
import os

from setzer.app.service_locator import ServiceLocator
from setzer.keyboard_shortcuts.shortcut_controller_app import ShortcutControllerApp


class PageShortcuts(object):

    def __init__(self, preferences, settings):
        self.view = PageShortcutsView()
        self.preferences = preferences
        self.settings = settings
        self.main_window = ServiceLocator.get_main_window()
        self.shortcut_entries = dict()
        self.current_shortcut_button = None
        self.key_controller = None
        self._batch_update = False
        self._pending_assignment = None
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
        shortcut_button.add_css_class('flat')
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
                'new_document': _('New Document'),
                'open_document': _('Open Document'),
                'save': _('Save'),
                'save_as': _('Save As'),
                'print': _('Print'),
                'close_document': _('Close Document'),
                'quit': _('Quit'),
                'show_shortcuts': _('Show Keyboard Shortcuts'),
                'show_open_docs': _('Show Open Documents'),
                'switch_document': _('Switch Document'),
                'show_document_chooser': _('Show Document Chooser'),
                'zoom_in': _('Zoom In'),
                'zoom_out': _('Zoom Out'),
                'reset_zoom': _('Reset Zoom'),
                'find': _('Find'),
                'find_and_replace': _('Find and Replace'),
                'find_next': _('Find Next'),
                'find_previous': _('Find Previous'),
                'help': _('Help'),
                'document_structure': _('Document Structure'),
                'symbols': _('Symbols'),
                'save_and_build': _('Save and Build'),
                'build': _('Build'),
                'forward_sync': _('Forward Sync'),
                'build_log': _('Build Log'),
                'preview': _('Preview'),
                'hamburger_menu': _('Hamburger Menu'),
                'context_menu': _('Context Menu'),
                'cut': _('Cut'),
                'copy': _('Copy'),
                'paste': _('Paste'),
                'undo': _('Undo'),
                'redo': _('Redo'),
                'select_all': _('Select All'),
                'toggle_comment': _('Toggle Comment'),
                'delete_line': _('Delete Line'),
                'duplicate_line': _('Duplicate Line'),
                'move_line_up': _('Move Line Up'),
                'move_line_down': _('Move Line Down'),
                'new_line': _('New Line'),
                'bold': _('Bold'),
                'italic': _('Italic'),
                'underline': _('Underline'),
                'typewriter': _('Typewriter'),
                'emphasized': _('Emphasized'),
                'quotation_marks': _('Quotation Marks'),
                'list_item': _('List Item'),
                'environment': _('Environment'),
                'inline_math': _('Inline Math'),
                'display_math': _('Display Math'),
                'equation': _('Equation'),
                'subscript': _('Subscript'),
                'superscript': _('Superscript'),
                'fraction': _('Fraction'),
                'left': _('Left'),
                'right': _('Right')
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
                'switch_document': _('Switch to the next open document'),
                'show_document_chooser': _('Show document chooser popover'),
                'zoom_in': _('Increase font size'),
                'zoom_out': _('Decrease font size'),
                'reset_zoom': _('Reset font size to default'),
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
                'right': _('Insert \\right')
            }
        return self._action_descriptions.get(action_name, '')

    def format_shortcut(self, shortcut):
        if not shortcut:
            return _('Not set')
        
        parts = shortcut.split('<')
        formatted_parts = []
        for part in parts:
            if not part:
                continue
            part = part.rstrip('>')
            if part == 'Control':
                formatted_parts.append('Ctrl')
            elif part == 'Shift':
                formatted_parts.append('Shift')
            elif part == 'Alt':
                formatted_parts.append('Alt')
            else:
                formatted_parts.append(part.upper())
        
        return ' + '.join(formatted_parts)

    def on_shortcut_button_clicked(self, button, action_name):
        if self.current_shortcut_button:
            self.cancel_shortcut_capture()

        self.current_shortcut_button = button
        self.current_action_name = action_name

        button.set_label(_('Press shortcut...'))
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
        if keyval == Gtk.KEY_Escape:
            self.cancel_shortcut_capture()
            return True

        shortcut = self.event_to_shortcut(keyval, state)
        if shortcut:
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

    def find_conflicting_action(self, action_name, shortcut):
        '''Return the name of another action already bound to ``shortcut``,
        or ``None`` if there is no conflict.'''
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = self.settings.defaults['keyboard_shortcuts']
        for other_action, other_shortcut in shortcuts.items():
            if other_action != action_name and other_shortcut == shortcut:
                return other_action
        return None

    def _show_conflict_dialog(self, action_name, shortcut, conflicting_action):
        self._pending_assignment = (action_name, shortcut, conflicting_action)
        other_title = self.get_action_title(conflicting_action)
        dialog = Adw.AlertDialog(
            heading=_('Shortcut Already in Use'),
            body=_('This shortcut is currently assigned to "{other}". '
                   'Reassigning it will remove the shortcut from that action.').format(other=other_title))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('reassign', _('Reassign'))
        dialog.set_response_appearance('reassign', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self.main_window, None, self.on_conflict_dialog_response)

    def on_conflict_dialog_response(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'reassign' and self._pending_assignment is not None:
            action_name, shortcut, conflicting_action = self._pending_assignment
            # 用 _batch_update 避免两次 set_shortcut 各自更新 controller。
            self._batch_update = True
            self.set_shortcut(conflicting_action, '')
            self.set_shortcut(action_name, shortcut)
            self._batch_update = False
            self.rebuild_shortcut_controllers()
        self._pending_assignment = None

    def event_to_shortcut(self, keyval, state):
        '''Convert a key-press event to a shortcut trigger string.

        注意：Gtk.accelerator_get_label 返回的键名依赖当前键盘布局——例如
        Ctrl+[ 在英文键盘返回 '['，但在德语键盘可能返回 'ü'。这是 GTK 的标准
        本地化行为：用户看到的是其键盘布局上实际按下的键。快捷键字符串存入
        settings 后，在不同布局的键盘上可能不匹配。这是已知的 GTK 限制，
        非本代码 bug；GTK4 的 Gtk.ShortcutTrigger.parse_string 对符号键有
        一定的布局无关匹配能力，但 accelerator_get_label 的输出仍受布局影响。'''
        parts = []
        
        if state & Gdk.ModifierType.CONTROL_MASK:
            parts.append('Control')
        if state & Gdk.ModifierType.SHIFT_MASK:
            parts.append('Shift')
        if state & Gdk.ModifierType.MOD1_MASK:
            parts.append('Alt')
        
        key_name = Gtk.accelerator_get_label(keyval, state)
        if key_name and key_name not in ['Control', 'Shift', 'Alt', '']:
            parts.append(key_name)
        
        if len(parts) < 2:
            return None
        
        return '<' + '><'.join(parts) + '>'

    def set_shortcut(self, action_name, shortcut):
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = dict(self.settings.defaults['keyboard_shortcuts'])

        shortcuts[action_name] = shortcut
        self.settings.set_value('keyboard_shortcuts', None, shortcuts)

        if action_name in self.shortcut_entries:
            self.shortcut_entries[action_name]['shortcut'] = shortcut
            self.shortcut_entries[action_name]['button'].set_label(self.format_shortcut(shortcut))

        if not self._batch_update:
            # 单个快捷键修改：只更新该 action 的 trigger，不重建整个 controller。
            # 重建会销毁所有已注册快捷键（含非配置的 F3/Shift+F3 等）并重新解析
            # 全部 trigger 字符串——单次修改的开销与重置全部相同，不合理。
            self.update_shortcut_controller(action_name, shortcut)

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

    def update_shortcut_controller(self, action_name, shortcut):
        '''Update a single action's trigger on the existing controller.
        Preferred over rebuild for single-key changes — O(1) vs O(n).'''
        shortcuts = ServiceLocator.get_shortcuts()
        if shortcuts and shortcuts.shortcut_controller_app:
            shortcuts.shortcut_controller_app.update_shortcut(action_name, shortcut or '')

    def rebuild_shortcut_controllers(self):
        '''Full rebuild — destroy and recreate the entire ShortcutControllerApp.
        Only for batch operations (reset all, import) where many shortcuts
        change at once. For single changes, use update_shortcut_controller.'''
        shortcuts = ServiceLocator.get_shortcuts()
        if shortcuts:
            old_controller = shortcuts.shortcut_controller_app
            new_controller = ShortcutControllerApp()
            self.main_window.remove_controller(old_controller)
            shortcuts.shortcut_controller_app = new_controller
            self.main_window.add_controller(new_controller)

    def on_reset_clicked(self, button):
        dialog = Adw.AlertDialog(
            heading=_('Reset to Defaults?'),
            body=_('All keyboard shortcuts will be restored to their default values.'))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('reset', _('Reset'))
        dialog.set_response_appearance('reset', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self.main_window, None, self.on_reset_confirmed)

    def on_reset_confirmed(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'reset':
            defaults = self.settings.defaults['keyboard_shortcuts']
            self._batch_update = True
            for action_name, shortcut in defaults.items():
                self.set_shortcut(action_name, shortcut)
            self._batch_update = False
            self.rebuild_shortcut_controllers()

    def on_import_clicked(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title(_('Import Keyboard Shortcuts'))
        
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
                with open(file.get_path(), 'r') as f:
                    shortcuts = json.load(f)

                imported = 0
                skipped = 0
                # _batch_update 避免 N 次 set_shortcut 各自更新 controller，
                # 全部写完后一次 rebuild_shortcut_controllers。
                self._batch_update = True
                for action_name, shortcut in shortcuts.items():
                    if action_name in self.settings.defaults['keyboard_shortcuts']:
                        self.set_shortcut(action_name, shortcut)
                        imported += 1
                    else:
                        skipped += 1
                self._batch_update = False
                self.rebuild_shortcut_controllers()

                if skipped > 0:
                    self._show_toast(
                        _('Imported {n} shortcuts, skipped {m} unknown actions').format(
                            n=imported, m=skipped))
                else:
                    self._show_toast(_('Imported {n} shortcuts').format(n=imported))
        except Exception as e:
            self._show_toast(_('Failed to import shortcuts: {error}').format(error=str(e)))

    def on_export_clicked(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title(_('Export Keyboard Shortcuts'))

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

                with open(file.get_path(), 'w') as f:
                    json.dump(shortcuts, f, indent=2)
                self._show_toast(_('Exported {n} shortcuts').format(n=len(shortcuts)))
        except Exception as e:
            self._show_toast(_('Failed to export shortcuts: {error}').format(error=str(e)))

    def _show_toast(self, message):
        '''显示 toast 通知（导入/导出结果反馈）。'''
        main_window = ServiceLocator.get_main_window()
        if main_window and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(message)
            toast.set_timeout(5)
            main_window.toast_overlay.add_toast(toast)


class PageShortcutsView(Adw.PreferencesPage):

    def __init__(self):
        Adw.PreferencesPage.__init__(self)
        self.set_title(_('Keyboard Shortcuts'))
        self.set_icon_name('input-keyboard-symbolic')

        self.shortcuts_group = Adw.PreferencesGroup()
        self.shortcuts_group.set_title(_('Keyboard Shortcuts'))
        self.shortcuts_group.set_description(_('Click a shortcut button to change it. Press Escape to cancel.'))
        self.add(self.shortcuts_group)

        group_actions = Adw.PreferencesGroup()
        group_actions.set_title(_('Actions'))
        self.add(group_actions)

        import_row = Adw.ActionRow()
        import_row.set_title(_('Import Shortcuts'))
        import_row.set_subtitle(_('Load keyboard shortcuts from a JSON file'))
        self.import_button = Gtk.Button(label=_('Import'))
        self.import_button.set_valign(Gtk.Align.CENTER)
        import_row.add_suffix(self.import_button)
        group_actions.add(import_row)

        export_row = Adw.ActionRow()
        export_row.set_title(_('Export Shortcuts'))
        export_row.set_subtitle(_('Save keyboard shortcuts to a JSON file'))
        self.export_button = Gtk.Button(label=_('Export'))
        self.export_button.set_valign(Gtk.Align.CENTER)
        export_row.add_suffix(self.export_button)
        group_actions.add(export_row)

        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)