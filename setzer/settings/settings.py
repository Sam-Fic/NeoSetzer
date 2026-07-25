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
import os
import os.path
import pickle

from setzer.helpers.observable import Observable
from setzer.helpers.persistence import (
    load_json, save_json, migrate_pickle_to_json,
)


class Settings(Observable):
    ''' Settings controller for saving application state. '''

    def __init__(self, pathname):
        Observable.__init__(self)

        self.pathname = pathname
        # JSON 是首选持久化格式；旧 settings.pickle 一次性迁移到 settings.json。
        # 保留 .pickle 文件作为备份，不删除（用户可手动清理）。
        self._json_path = os.path.join(self.pathname, 'settings.json')
        self._pickle_path = os.path.join(self.pathname, 'settings.pickle')

        self.data = dict()
        self.defaults = dict()
        self.set_defaults()

        # 一次性迁移：旧 settings.pickle → settings.json。
        # migrate_value 解包嵌套 pickle bytes（三个 wizard 的 presets 字段
        # 旧实现存的是 pickle.dumps(current_values) 的 bytes）。
        migrate_pickle_to_json(self._json_path, self._pickle_path,
                               migrate_value=self._migrate_presets_bytes)

        if not self.unpickle():
            self.data = self.defaults
            self.pickle()

    @staticmethod
    def _migrate_presets_bytes(data):
        '''settings 数据中 presets 字段从 pickle bytes 迁移为 dict。

        旧实现：document_wizard/include_bibtex_file/include_latex_file 的
        save_presets 调 pickle.dumps(current_values) 后存入 settings.data，
        再随 settings 整体 pickle 到 settings.pickle —— 双重 pickle。
        迁移到 JSON 前必须先解内层 bytes，否则 json.dump 遇 bytes 抛
        TypeError。三个 section 一并处理（未使用 wizard 时 presets 为 None，
        isinstance 检查跳过）。
        '''
        for section in ('app_document_wizard', 'app_bibtex_wizard',
                        'app_include_bibtex_file_dialog'):
            section_dict = data.get(section)
            if not isinstance(section_dict, dict):
                continue
            v = section_dict.get('presets')
            if isinstance(v, (bytes, bytearray)):
                try:
                    data[section]['presets'] = pickle.loads(v)
                except (pickle.UnpicklingError, EOFError, ValueError,
                        AttributeError, TypeError):
                    data[section]['presets'] = None
        return data

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
        self.defaults['window_state']['sidebar_width_fraction'] = 0.20
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
        # 自动构建报错时是否自动弹出构建日志。手动构建（F5/F6）始终遵循
        # autoshow_build_log；此开关仅影响自动构建路径——用户打字途中触发
        # 自动构建，文档可能尚未输完导致报错，频繁弹窗打扰写作。默认开启
        # 保持与原行为一致，用户可在 Build System 偏好中关闭。
        self.defaults['preferences']['auto_build_autoshow_errors'] = True
        self.defaults['preferences']['color_scheme'] = 'default'
        # 编辑器（GtkSourceView）配色方案 ID。空字符串 = 跟随应用深浅色主题
        # （由 ServiceLocator.get_style_scheme 根据 Adw.StyleManager.get_dark
        # 选择 default / default-dark）。非空时使用 GtkSource.StyleSchemeManager
        # 中对应的方案 ID（如 'default-dark'、'oblivion' 等），不再随系统主题
        # 切换变化。用户可在 Appearance 偏好中选择。
        self.defaults['preferences']['editor_style_scheme'] = ''
        self.defaults['preferences']['app_theme_mode'] = 'system'
        self.defaults['preferences']['language'] = 'en'
        self.defaults['preferences']['recolor_pdf'] = False
        self.defaults['preferences']['spaces_instead_of_tabs'] = True
        self.defaults['preferences']['tab_width'] = 4
        self.defaults['preferences']['show_line_numbers'] = True
        # 行距（像素）：每行文本下方额外添加的垂直间距。通过 GtkSourceView
        # 的 pixels_below_lines 实现，get_line_yrange().height 会自动包含
        # 这部分，gutter 行号间距随之同步，无需 gutter 侧额外处理。
        self.defaults['preferences']['line_spacing'] = 0
        self.defaults['preferences']['enable_code_folding'] = True
        self.defaults['preferences']['enable_line_wrapping'] = True
        self.defaults['preferences']['highlight_current_line'] = True
        self.defaults['preferences']['highlight_matching_brackets'] = True
        self.defaults['preferences']['build_option_system_commands'] = 'disable'
        self.defaults['preferences']['enable_autocomplete'] = True
        self.defaults['preferences']['enable_bracket_completion'] = True
        self.defaults['preferences']['bracket_selection'] = True
        self.defaults['preferences']['tab_jump_brackets'] = True
        self.defaults['preferences']['update_matching_blocks'] = True
        # 自动保存（崩溃恢复模式）：定时把缓冲区内容写入
        # ~/.config/setzer/autosave/<hash>.tex，应用崩溃后下次启动弹恢复对话框。
        # 默认开启，间隔 60 秒（与 VS Code 默认 files.autoSave=off 不同；Setzer
        # 选默认开是因为 LaTeX 写作场景中崩溃恢复价值高于磁盘写入开销）。
        self.defaults['preferences']['auto_save_enabled'] = True
        self.defaults['preferences']['auto_save_delay'] = 60
        # 当检测到外部程序修改磁盘文件时，是否自动静默重载。
        # 仅对本地 buffer 无未保存修改的文档生效；有未保存修改时回退到对话框。
        self.defaults['preferences']['auto_reload_on_external_change'] = True

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
        self.defaults['keyboard_shortcuts']['print'] = '<Control>p'
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
        self.defaults['keyboard_shortcuts']['delete_line'] = '<Control><Shift>k'
        self.defaults['keyboard_shortcuts']['toggle_comment'] = '<Control>k'
        self.defaults['keyboard_shortcuts']['duplicate_line'] = '<Alt><Shift>d'
        self.defaults['keyboard_shortcuts']['move_line_up'] = '<Alt>Up'
        self.defaults['keyboard_shortcuts']['move_line_down'] = '<Alt>Down'
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
        # 读操作不应有写副作用。原实现读缺失键时调 set_value 把默认值写回
        # self.data 并广播 settings_changed，导致首次启动/升级新增设置项时每个
        # 观察者首次 get_value 该键都触发一次假通知（FontManager 重算字体、
        # CodeFolding 重应用折叠、Gutter 重绘等数十处无谓响应）。此处直接返回
        # 默认值，不写回 data、不广播。默认值仅在用户显式 set_value 时进入 data
        # 并被 pickle 持久化；未持久化的键每次 get_value 都回退到 defaults。
        if item is None:
            return self.data.get(section, self.defaults.get(section, {}))
        try:
            return self.data[section][item]
        except KeyError:
            return self.defaults[section][item]

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
        ''' Load settings from home folder.

        优先读 settings.json；旧 settings.pickle 已在 __init__ 中通过
        migrate_pickle_to_json 一次性迁移到 JSON。保留方法名以兼容
        setzer.in 入口中 `self.settings.pickle()` 的调用。
        '''
        # create folder if it does not exist
        if not os.path.isdir(self.pathname):
            os.makedirs(self.pathname)

        data = load_json(self._json_path)
        if data is None:
            return False
        # 防御性：JSON 顶层必须是 dict（与 self.data 结构一致）
        if not isinstance(data, dict):
            return False
        self.data = data
        return True

    def pickle(self):
        ''' Save settings in home folder.

        写入 settings.json（原子替换）。保留方法名以兼容 setzer.in 入口
        中 `self.settings.pickle()` 的调用。
        '''
        try:
            save_json(self._json_path, self.data)
        except (OSError, TypeError, ValueError):
            return False
        return True

    def reset_preferences(self):
        '''Reset all preferences to default values.'''
        self.data['preferences'] = dict(self.defaults['preferences'])
        self.add_change_code('settings_changed', ('preferences', None, None))
