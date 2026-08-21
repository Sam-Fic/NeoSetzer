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
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.app.service_locator import ServiceLocator


class KeyboardShortcutsDialog(object):

    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = ServiceLocator.get_settings()
        
        self.data = self.build_shortcuts_data()

    def build_shortcuts_data(self):
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = self.settings.defaults['keyboard_shortcuts']
        
        data = list()

        section = {'title': _('Documents'), 'items': list()}
        section['items'].append({'title': _('Create new document'), 'shortcut': shortcuts.get('new_document', '<ctrl>N')})
        section['items'].append({'title': _('Open a document'), 'shortcut': shortcuts.get('open_document', '<ctrl>O')})
        section['items'].append({'title': _('Show recent documents'), 'shortcut': shortcuts.get('show_document_chooser', '<ctrl><shift>O')})
        section['items'].append({'title': _('Show open documents'), 'shortcut': shortcuts.get('show_open_docs', '<ctrl>T')})
        section['items'].append({'title': _('Switch to the next open document'), 'shortcut': shortcuts.get('switch_document', '<ctrl>Tab')})
        section['items'].append({'title': _('Save the current document'), 'shortcut': shortcuts.get('save', '<ctrl>S')})
        section['items'].append({'title': _('Save the document with a new filename'), 'shortcut': shortcuts.get('save_as', '<ctrl><shift>S')})
        section['items'].append({'title': _('Close the current document'), 'shortcut': shortcuts.get('close_document', '<ctrl>W')})
        section['items'].append({'title': _('Close all open documents'), 'shortcut': shortcuts.get('close_all_documents', '<ctrl><shift>W')})
        section['items'].append({'title': _('Restore a previously saved session'), 'shortcut': shortcuts.get('restore_session', '<ctrl><shift>J')})
        # 重开最近关闭的文档：现为可配置项（默认 Ctrl+Shift+T，浏览器惯例），
        # 从设置读取以反映用户改键；与 show_open_docs (Ctrl+T) 仅差一个 Shift。
        section['items'].append({'title': _('Reopen the last closed document'), 'shortcut': shortcuts.get('reopen_last_closed_document', '<ctrl><shift>T')})
        data.append(section)

        section = {'title': _('Tools'), 'items': list()}
        section['items'].append({'title': _('Save and build .pdf-file from document'), 'shortcut': shortcuts.get('save_and_build', 'F5')})
        section['items'].append({'title': _('Build PDF without saving'), 'shortcut': shortcuts.get('build', 'F6')})
        section['items'].append({'title': _('Show current position in preview'), 'shortcut': shortcuts.get('forward_sync', 'F7')})
        data.append(section)

        section = {'title': _('Windows and Panels'), 'items': list()}
        section['items'].append({'title': _('Open the preferences dialog'), 'shortcut': shortcuts.get('show_preferences_dialog', '<ctrl>comma')})
        # show_about_dialog 默认未绑定快捷键（About 对话框菜单可达）；
        # 仅当用户在偏好页手动绑定后才在对话框里显示，避免空白行。
        about_shortcut = shortcuts.get('show_about_dialog')
        if about_shortcut:
            section['items'].append({'title': _('Show the about dialog'), 'shortcut': about_shortcut})
        section['items'].append({'title': _('Show help panel'), 'shortcut': shortcuts.get('help', 'F1')})
        section['items'].append({'title': _('Toggle fullscreen'), 'shortcut': shortcuts.get('fullscreen', 'F11')})
        section['items'].append({'title': _('Toggle document structure panel'), 'shortcut': shortcuts.get('document_structure', '<ctrl><shift>B')})
        # symbols 默认已改为 F8、build_log 已改为 F4（分别避免与 save_as、\\left
        # 冲突），fallback 必须与设置里的新默认值一致，否则老配置缺键时会显示错误。
        section['items'].append({'title': _('Toggle symbols panel'), 'shortcut': shortcuts.get('symbols', 'F8')})
        section['items'].append({'title': _('Toggle build log'), 'shortcut': shortcuts.get('build_log', 'F4')})
        section['items'].append({'title': _('Toggle preview panel'), 'shortcut': shortcuts.get('preview', '<ctrl><shift>P')})
        section['items'].append({'title': _('Open command palette'), 'shortcut': shortcuts.get('command_palette', '<ctrl>period')})
        section['items'].append({'title': _('Show global menu'), 'shortcut': shortcuts.get('hamburger_menu', 'F10')})
        section['items'].append({'title': _('Show context menu'), 'shortcut': shortcuts.get('context_menu', 'F12')})
        section['items'].append({'title': _('Show keyboard shortcuts'), 'shortcut': shortcuts.get('show_shortcuts', '<ctrl>question')})
        section['items'].append({'title': _('Close Application'), 'shortcut': shortcuts.get('quit', '<ctrl>Q')})
        data.append(section)

        section = {'title': _('Find and Replace'), 'items': list()}
        section['items'].append({'title': _('Find'), 'shortcut': shortcuts.get('find', '<ctrl>F')})
        section['items'].append({'title': _('Find the next match'), 'shortcut': shortcuts.get('find_next', '<ctrl>G')})
        section['items'].append({'title': _('Find the previous match'), 'shortcut': shortcuts.get('find_previous', '<ctrl><shift>G')})
        # F3 / Shift+F3 是查找下一个/上一个的额外别名（与 Ctrl+G / Ctrl+Shift+G 并存）。
        section['items'].append({'title': _('Find the next match'), 'shortcut': 'F3'})
        section['items'].append({'title': _('Find the previous match'), 'shortcut': '<Shift>F3'})
        section['items'].append({'title': _('Find and Replace'), 'shortcut': shortcuts.get('find_and_replace', '<ctrl>H')})
        data.append(section)

        section = {'title': _('Zoom'), 'items': list()}
        section['items'].append({'title': _('Zoom in'), 'shortcut': shortcuts.get('zoom_in', '<ctrl>plus')})
        section['items'].append({'title': _('Zoom out'), 'shortcut': shortcuts.get('zoom_out', '<ctrl>minus')})
        section['items'].append({'title': _('Reset zoom'), 'shortcut': shortcuts.get('reset_zoom', '<ctrl>0')})
        data.append(section)

        section = {'title': _('Copy and Paste'), 'items': list()}
        section['items'].append({'title': _('Copy selected text to clipboard'), 'shortcut': shortcuts.get('copy', '<ctrl>C')})
        section['items'].append({'title': _('Cut selected text to clipboard'), 'shortcut': shortcuts.get('cut', '<ctrl>X')})
        section['items'].append({'title': _('Paste text from clipboard'), 'shortcut': shortcuts.get('paste', '<ctrl>V')})
        data.append(section)

        section = {'title': _('Undo and Redo'), 'items': list()}
        section['items'].append({'title': _('Undo previous text edit'), 'shortcut': shortcuts.get('undo', '<ctrl>Z')})
        section['items'].append({'title': _('Redo previous text edit'), 'shortcut': shortcuts.get('redo', '<ctrl><shift>Z')})
        # Ctrl+Y 是"重做"的额外别名（与 Ctrl+Shift+Z 并存，二者均有效）。
        section['items'].append({'title': _('Redo previous text edit'), 'shortcut': '<ctrl>Y'})
        data.append(section)

        section = {'title': _('Selection'), 'items': list()}
        section['items'].append({'title': _('Select all text'), 'shortcut': shortcuts.get('select_all', '<ctrl>A')})
        data.append(section)

        section = {'title': _('Editing'), 'items': list()}
        section['items'].append({'title': _('Toggle insert / overwrite'), 'shortcut': 'Insert'})
        # move_line_up/down 是可配置快捷键，从设置读取以反映用户改键
        section['items'].append({'title': _('Move current line up'), 'shortcut': shortcuts.get('move_line_up', '<Alt>Up')})
        section['items'].append({'title': _('Move current line down'), 'shortcut': shortcuts.get('move_line_down', '<Alt>Down')})
        # 注：Alt+Up/Down 在编辑器外（如侧栏聚焦时）还用于章节导航（prev/next section），
        # 由 app 控制器在编辑态返回 False 后交由文档控制器处理，二者上下文互斥。
        # 删除原先「移动单词 / 增减数字」四项：它们在代码里从未注册，属于无效快捷键。
        # 跳转到行：硬编码 Ctrl+L（编辑器内跳转），不在设置里，写死加速器。
        section['items'].append({'title': _('Go to line'), 'shortcut': '<ctrl>L'})
        section['items'].append({'title': _('Delete current line'), 'shortcut': shortcuts.get('delete_line', '<ctrl><shift>K')})
        data.append(section)

        section = {'title': _('LaTeX Shortcuts'), 'items': list()}
        section['items'].append({'title': _('Comment / Uncomment current line(s)'), 'shortcut': shortcuts.get('toggle_comment', '<ctrl>slash')})
        section['items'].append({'title': _('New Line') + ' (\\\\)', 'shortcut': shortcuts.get('new_line', '<ctrl>Return')})
        section['items'].append({'title': _('Bold Text'), 'shortcut': shortcuts.get('bold', '<ctrl>B')})
        section['items'].append({'title': _('Italic Text'), 'shortcut': shortcuts.get('italic', '<ctrl>I')})
        section['items'].append({'title': _('Underlined Text'), 'shortcut': shortcuts.get('underline', '<ctrl>U')})
        # typewriter 默认已改为 Ctrl+Shift+Y（原 Ctrl+Shift+T 被重开标签页占用）
        section['items'].append({'title': _('Typewriter Text'), 'shortcut': shortcuts.get('typewriter', '<ctrl><shift>Y')})
        section['items'].append({'title': _('Emphasized Text'), 'shortcut': shortcuts.get('emphasized', '<ctrl><shift>E')})
        section['items'].append({'title': _('Quotation Marks'), 'shortcut': shortcuts.get('quotation_marks', '<ctrl>quotedbl')})
        section['items'].append({'title': _('List Item'), 'shortcut': shortcuts.get('list_item', '<ctrl><shift>I')})
        section['items'].append({'title': _('Environment'), 'shortcut': shortcuts.get('environment', '<ctrl>E')})
        data.append(section)

        section = {'title': _('Math Shortcuts'), 'items': list()}
        section['items'].append({'title': _('Inline Math Section'), 'shortcut': shortcuts.get('inline_math', '<ctrl>M')})
        section['items'].append({'title': _('Display Math Section'), 'shortcut': shortcuts.get('display_math', '<ctrl><shift>M')})
        section['items'].append({'title': _('Equation'), 'shortcut': shortcuts.get('equation', '<ctrl><shift>N')})
        section['items'].append({'title': _('Subscript'), 'shortcut': shortcuts.get('subscript', '<ctrl><shift>D')})
        section['items'].append({'title': _('Superscript'), 'shortcut': shortcuts.get('superscript', '<ctrl><shift>U')})
        section['items'].append({'title': _('Fraction'), 'shortcut': shortcuts.get('fraction', '<alt><shift>F')})
        section['items'].append({'title': '\\left', 'shortcut': shortcuts.get('left', '<ctrl><shift>L')})
        section['items'].append({'title': '\\right', 'shortcut': shortcuts.get('right', '<ctrl><shift>R')})
        data.append(section)

        return data

    def run(self):
        self.data = self.build_shortcuts_data()
        self.setup()
        self.view.present(self.main_window)

    def setup(self):
        dialog = Adw.ShortcutsDialog()
        for section in self.data:
            sec = Adw.ShortcutsSection(title=section['title'])
            for item in section['items']:
                shortcut = Adw.ShortcutsItem(title=item['title'], accelerator=item['shortcut'])
                sec.add(shortcut)
            dialog.add(sec)
        self.view = dialog
