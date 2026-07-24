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

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from gi.repository import Pango
import os.path
import pickle

from setzer.helpers.observable import Observable


class Settings(Observable):
    ''' Settings controller for saving application state. '''

    def __init__(self, pathname):
        Observable.__init__(self)

        self.pathname = pathname
    
        self.data = dict()
        self.defaults = dict()
        self.set_defaults()

        if not self.unpickle():
            self.data = self.defaults
            self.pickle()
            
    def set_defaults(self):
        self.defaults['window_state'] = dict()
        self.defaults['window_state']['width'] = 1020
        self.defaults['window_state']['height'] = 550
        self.defaults['window_state']['is_maximized'] = False
        # 窗口左上角坐标；-1 表示未持久化过，启动时由窗口管理器决定位置。
        self.defaults['window_state']['x'] = -1
        self.defaults['window_state']['y'] = -1
        self.defaults['window_state']['show_symbols'] = False
        self.defaults['window_state']['show_document_structure'] = False
        self.defaults['window_state']['sidebar_paned_position'] = -1
        self.defaults['window_state']['sidebar_width_fraction'] = 0.14
        self.defaults['window_state']['show_help'] = False
        self.defaults['window_state']['show_preview'] = False
        self.defaults['window_state']['show_build_log'] = False
        self.defaults['window_state']['preview_paned_position'] = -1
        # preview 宽度占比（Adw.OverlaySplitView）。旧版用像素 preview_paned_position，
        # 由 workspace_presenter.setup_paneds 一次性迁移到 fraction；此处保留旧键默认值
        # 仅为向后兼容，迁移后忽略。
        self.defaults['window_state']['preview_width_fraction'] = 0.5
        self.defaults['window_state']['notebook_paned_position'] = -1
        # Pass-10: build_log_paned_position 已废弃（build_log 改为 Adw.Dialog 弹窗，
        # 尺寸由 dialog 自管理）。旧 pickle 文件中若有该 key 不影响，只是不再读它。
        
        self.defaults['app_document_wizard'] = dict()
        self.defaults['app_document_wizard']['presets'] = None
        
        self.defaults['app_bibtex_wizard'] = dict()
        self.defaults['app_bibtex_wizard']['presets'] = None
        
        self.defaults['app_include_bibtex_file_dialog'] = dict()
        self.defaults['app_include_bibtex_file_dialog']['presets'] = None

        self.defaults['app_recent_symbols'] = {'symbols': []}
        self.defaults['app_favorite_symbols'] = {'symbols': []}

        self.defaults['preferences'] = dict()
        self.defaults['preferences']['cleanup_build_files'] = True
        self.defaults['preferences']['autoshow_build_log'] = 'errors_warnings'
        self.defaults['preferences']['latex_interpreter'] = 'xelatex'
        self.defaults['preferences']['use_latexmk'] = False
        self.defaults['preferences']['auto_build'] = False
        self.defaults['preferences']['auto_build_delay'] = 2
        self.defaults['preferences']['color_scheme'] = 'default'
        self.defaults['preferences']['app_theme_mode'] = 'system'
        self.defaults['preferences']['language'] = 'en'
        self.defaults['preferences']['recolor_pdf'] = False
        self.defaults['preferences']['spaces_instead_of_tabs'] = True
        self.defaults['preferences']['tab_width'] = 4
        self.defaults['preferences']['show_line_numbers'] = True
        self.defaults['preferences']['enable_code_folding'] = True
        self.defaults['preferences']['enable_line_wrapping'] = True
        self.defaults['preferences']['highlight_current_line'] = False
        self.defaults['preferences']['highlight_matching_brackets'] = True
        self.defaults['preferences']['build_option_system_commands'] = 'disable'
        self.defaults['preferences']['enable_autocomplete'] = True
        self.defaults['preferences']['enable_bracket_completion'] = True
        self.defaults['preferences']['bracket_selection'] = True
        self.defaults['preferences']['tab_jump_brackets'] = True
        self.defaults['preferences']['update_matching_blocks'] = True

        self.defaults['preferences']['use_system_font'] = True
        textview = Gtk.TextView()
        textview.set_monospace(True)
        font_string = textview.get_pango_context().get_font_description().to_string()
        self.defaults['preferences']['font_string'] = font_string

        self.defaults['keyboard_shortcuts'] = dict()
        self.defaults['keyboard_shortcuts']['new_document'] = '<Control>n'
        self.defaults['keyboard_shortcuts']['open_document'] = '<Control>o'
        self.defaults['keyboard_shortcuts']['save'] = '<Control>s'
        self.defaults['keyboard_shortcuts']['save_as'] = '<Control><Shift>s'
        self.defaults['keyboard_shortcuts']['close_document'] = '<Control>w'
        self.defaults['keyboard_shortcuts']['quit'] = '<Control>q'
        self.defaults['keyboard_shortcuts']['show_shortcuts'] = '<Control>question'
        self.defaults['keyboard_shortcuts']['show_open_docs'] = '<Control>t'
        self.defaults['keyboard_shortcuts']['switch_document'] = '<Control>Tab'
        self.defaults['keyboard_shortcuts']['show_document_chooser'] = '<Control><Shift>o'
        self.defaults['keyboard_shortcuts']['zoom_in'] = '<Control>plus'
        self.defaults['keyboard_shortcuts']['zoom_out'] = '<Control>minus'
        self.defaults['keyboard_shortcuts']['reset_zoom'] = '<Control>0'
        self.defaults['keyboard_shortcuts']['find'] = '<Control>f'
        self.defaults['keyboard_shortcuts']['find_and_replace'] = '<Control>h'
        self.defaults['keyboard_shortcuts']['find_next'] = '<Control>g'
        self.defaults['keyboard_shortcuts']['find_previous'] = '<Control><Shift>g'
        self.defaults['keyboard_shortcuts']['help'] = 'F1'
        self.defaults['keyboard_shortcuts']['document_structure'] = '<Control><Shift>b'
        self.defaults['keyboard_shortcuts']['symbols'] = '<Control><Shift>s'
        self.defaults['keyboard_shortcuts']['save_and_build'] = 'F5'
        self.defaults['keyboard_shortcuts']['build'] = 'F6'
        self.defaults['keyboard_shortcuts']['forward_sync'] = 'F7'
        self.defaults['keyboard_shortcuts']['build_log'] = '<Control><Shift>l'
        self.defaults['keyboard_shortcuts']['preview'] = '<Control><Shift>p'
        self.defaults['keyboard_shortcuts']['hamburger_menu'] = 'F10'
        self.defaults['keyboard_shortcuts']['context_menu'] = 'F12'
        self.defaults['keyboard_shortcuts']['cut'] = '<Control>x'
        self.defaults['keyboard_shortcuts']['copy'] = '<Control>c'
        self.defaults['keyboard_shortcuts']['paste'] = '<Control>v'
        self.defaults['keyboard_shortcuts']['undo'] = '<Control>z'
        self.defaults['keyboard_shortcuts']['redo'] = '<Control><Shift>z'
        self.defaults['keyboard_shortcuts']['select_all'] = '<Control>a'
        self.defaults['keyboard_shortcuts']['toggle_comment'] = '<Control>k'
        self.defaults['keyboard_shortcuts']['new_line'] = '<Control>Return'
        self.defaults['keyboard_shortcuts']['bold'] = '<Control>b'
        self.defaults['keyboard_shortcuts']['italic'] = '<Control>i'
        self.defaults['keyboard_shortcuts']['underline'] = '<Control>u'
        self.defaults['keyboard_shortcuts']['typewriter'] = '<Control><Shift>t'
        self.defaults['keyboard_shortcuts']['emphasized'] = '<Control><Shift>e'
        self.defaults['keyboard_shortcuts']['quotation_marks'] = '<Control>quotedbl'
        self.defaults['keyboard_shortcuts']['list_item'] = '<Control><Shift>i'
        self.defaults['keyboard_shortcuts']['environment'] = '<Control>e'
        self.defaults['keyboard_shortcuts']['inline_math'] = '<Control>m'
        self.defaults['keyboard_shortcuts']['display_math'] = '<Control><Shift>m'
        self.defaults['keyboard_shortcuts']['equation'] = '<Control><Shift>n'
        self.defaults['keyboard_shortcuts']['subscript'] = '<Control><Shift>d'
        self.defaults['keyboard_shortcuts']['superscript'] = '<Control><Shift>u'
        self.defaults['keyboard_shortcuts']['fraction'] = '<Alt><Shift>f'
        self.defaults['keyboard_shortcuts']['left'] = '<Control><Shift>l'
        self.defaults['keyboard_shortcuts']['right'] = '<Control><Shift>r'

    def get_value(self, section, item):
        if item is None:
            try: return self.data[section]
            except KeyError: return self.defaults.get(section, {})
        try: value = self.data[section][item]
        except KeyError:
            value = self.defaults[section][item]
            self.set_value(section, item, value)
        return value

    def set_value(self, section, item, value):
        try: section_dict = self.data[section]
        except KeyError:
            section_dict = dict()
            self.data[section] = section_dict
        if item is None:
            self.data[section] = value
        else:
            section_dict[item] = value
        self.add_change_code('settings_changed', (section, item, value))
        
    def unpickle(self):
        ''' Load settings from home folder. '''

        # create folder if it does not exist
        if not os.path.isdir(self.pathname):
            os.makedirs(self.pathname)

        try: filehandle = open(os.path.join(self.pathname, 'settings.pickle'), 'rb')
        except IOError: return False
        else:
            try:
                self.data = pickle.load(filehandle)
            except (EOFError, pickle.UnpicklingError, ValueError, AttributeError):
                # pickle 文件损坏或为空时，原代码写 `except EOFError: False`
                # ——这只是一条空表达式语句（求值 False 后丢弃），并非 return False。
                # 结果 self.data 保持为空 dict，unpickle 仍返回 True，__init__ 不
                # 走 defaults 恢复分支。改为 return False 让 __init__ 用 defaults
                # 重置并重新 pickle，确保损坏文件不会导致设置永久丢失。
                # 同时扩展异常覆盖：UnpicklingError/ValueError/AttributeError 也
                # 是 pickle 文件损坏的常见表现。
                return False

        return True
        
    def pickle(self):
        ''' Save settings in home folder. '''
        
        try: filehandle = open(os.path.join(self.pathname, 'settings.pickle'), 'wb')
        except IOError: return False
        else: pickle.dump(self.data, filehandle)

    def reset_preferences(self):
        '''Reset all preferences to default values.'''
        self.data['preferences'] = dict(self.defaults['preferences'])
        self.add_change_code('settings_changed', ('preferences', None, None))
        

