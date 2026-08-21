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
        else:
            self._migrate_conflicting_shortcut_defaults()

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
        self.defaults['window_state']['show_symbols'] = False
        self.defaults['window_state']['show_document_structure'] = False
        # 侧栏当前选中的面板（symbols / document_structure），隐藏后再次显示时恢复，
        # 避免每次都回退到 Symbols 面板。与 show_symbols/show_document_structure 解耦：
        # 后者在隐藏时被清成 False 用于驱动可见性，本键专门记忆"上次选了哪个面板"。
        self.defaults['window_state']['sidebar_page'] = 'symbols'
        self.defaults['window_state']['sidebar_paned_position'] = -1
        self.defaults['window_state']['sidebar_width_fraction'] = 0.20
        self.defaults['window_state']['show_help'] = False
        self.defaults['window_state']['show_preview'] = False
        self.defaults['window_state']['show_build_log'] = False
        # todos 侧栏：是否显示所有文档的 todos。True=全部文档, False=仅当前文档。
        self.defaults['window_state']['todos_show_all_documents'] = False
        self.defaults['window_state']['preview_paned_position'] = -1
        # preview 宽度占比（Adw.OverlaySplitView）。旧版用像素 preview_paned_position，
        # 由 workspace_presenter.setup_paneds 一次性迁移到 fraction；此处保留旧键默认值
        # 仅为向后兼容，迁移后忽略。
        self.defaults['window_state']['preview_width_fraction'] = 0.5
        self.defaults['window_state']['notebook_paned_position'] = -1
        # Pass-10: build_log_paned_position 已废弃（build_log 改为 Adw.Dialog 弹窗，
        # 尺寸由 dialog 自管理）。旧 pickle 文件中若有该 key 不影响，只是不再读它。
        # 构建日志弹窗内各 group 的展开/折叠状态。True=展开, False=折叠。
        self.defaults['window_state']['build_log_groups_expanded'] = {
            'Error': True, 'Warning': True, 'Badbox': True
        }

        self.defaults['app_document_wizard'] = dict()
        self.defaults['app_document_wizard']['presets'] = None
        # 命名模板库（报告 #5）：name → current_values blob。
        self.defaults['app_document_wizard']['templates'] = dict()

        self.defaults['app_bibtex_wizard'] = dict()
        self.defaults['app_bibtex_wizard']['presets'] = None

        self.defaults['app_include_bibtex_file_dialog'] = dict()
        self.defaults['app_include_bibtex_file_dialog']['presets'] = None

        # 注：app_recent_symbols 已移除——最近符号改为按文档区分（见
        # setzer/document/document.py 的 document.recent_symbols 与
        # setzer/settings/document_settings.py 的 per-document 持久化）。
        self.defaults['app_favorite_symbols'] = {'symbols': []}

        self.defaults['preferences'] = dict()
        self.defaults['preferences']['cleanup_build_files'] = True
        self.defaults['preferences']['autoshow_build_log'] = 'errors_warnings'
        self.defaults['preferences']['latex_interpreter'] = 'xelatex'
        # 启动行为：'last_session' 恢复上次会话；'empty' 启动空白工作区（见 ③）。
        self.defaults['preferences']['on_startup'] = 'last_session'
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
        # 撤销栈深度上限（GtkSource.Buffer 的 max-undo-levels）。0 = 不限。
        # 默认 200 与 GtkSourceView 内置上限一致，避免超大文档撤销栈无限增长；
        # 用户可在此调小以节省内存，或设为 0 关闭限制。
        self.defaults['preferences']['max_undo_levels'] = 200
        self.defaults['preferences']['show_line_numbers'] = True
        self.defaults['preferences']['show_right_margin'] = True
        self.defaults['preferences']['right_margin_position'] = 80
        self.defaults['preferences']['show_shortcuts_bar'] = True
        # 行距（像素）：每行之间的额外垂直间距。均分到 pixels_above_lines /
        # pixels_below_lines 使文本在行 slot 中竖直居中；pixels_inside_wrap
        # 设为完整值使自动换行续行间距与段落间一致。get_line_yrange().height
        # 自动包含这些间距，gutter 行号间距随之同步。
        self.defaults['preferences']['line_spacing'] = 0
        self.defaults['preferences']['enable_code_folding'] = True
        self.defaults['preferences']['enable_sticky_scroll'] = True
        self.defaults['preferences']['enable_line_wrapping'] = True
        self.defaults['preferences']['highlight_current_line'] = True
        self.defaults['preferences']['highlight_matching_brackets'] = True
        self.defaults['preferences']['highlight_matching_begin_end'] = True
        # 拼写检查（pyenchant 后端，缺库时偏好页置灰、功能整体停用）。
        # 仅检查 LaTeX 文档；数学/命令/verbatim 等语法区域自动跳过。
        # spellchecking_language 是 enchant/hunspell 词典 tag，系统无该
        # 词典时运行时回退 en_US → 首个可用语言。
        self.defaults['preferences']['spellchecking_enabled'] = False
        self.defaults['preferences']['spellchecking_language'] = 'en_US'
        # 行尾/空白可见性：调试缩进问题时有用。
        # show_line_endings: 在行尾显示 ¶ 符号。
        self.defaults['preferences']['show_line_endings'] = False
        # show_whitespace: 显示空白字符（空格 · Tab →）。
        self.defaults['preferences']['show_whitespace'] = False
        self.defaults['preferences']['build_option_system_commands'] = 'disable'
        self.defaults['preferences']['enable_autocomplete'] = True
        self.defaults['preferences']['enable_bracket_completion'] = True
        self.defaults['preferences']['bracket_selection'] = True
        self.defaults['preferences']['tab_jump_brackets'] = True
        # 手动触发补全的快捷键（GTK 加速器字符串）。默认 Ctrl+Space；
        # 在 CJK 输入法环境下可能与输入法开关冲突，可在「偏好 → 自动补全」中改绑。
        self.defaults['preferences']['autocomplete_manual_trigger'] = '<Control>space'
        # 补全弹窗内的导航键（GTK 加速器字符串），全部可在「偏好 → 自动补全」中改绑
        # （报告 #6 的遗留项：把上/下一条、上一页/下一页、接受、取消登记为可配置项，
        # 让补全弹窗的键盘交互可被用户发现与重映射）。
        self.defaults['preferences']['autocomplete_previous'] = 'Up'
        self.defaults['preferences']['autocomplete_next'] = 'Down'
        self.defaults['preferences']['autocomplete_previous_page'] = 'Page_Up'
        self.defaults['preferences']['autocomplete_next_page'] = 'Page_Down'
        self.defaults['preferences']['autocomplete_accept'] = 'Return'
        self.defaults['preferences']['autocomplete_cancel'] = 'Escape'
        self.defaults['preferences']['update_matching_blocks'] = True
        # 环境自动补：输入 \begin{ 时自动插入配对的 \end{}（含内容占位符）。默认关闭，避免干扰可选参数环境。
        self.defaults['preferences']['enable_environment_autocomplete'] = False
        # 自动保存（崩溃恢复模式）：定时把缓冲区内容写入
        # ~/.config/setzer/autosave/<hash>.tex，应用崩溃后下次启动弹恢复对话框。
        # 默认开启，间隔 60 秒（与 VS Code 默认 files.autoSave=off 不同；Setzer
        # 选默认开是因为 LaTeX 写作场景中崩溃恢复价值高于磁盘写入开销）。
        self.defaults['preferences']['auto_save_enabled'] = True
        self.defaults['preferences']['auto_save_delay'] = 60
        # 当检测到外部程序修改磁盘文件时，是否自动静默重载。
        # 仅对本地 buffer 无未保存修改的文档生效；有未保存修改时回退到对话框。
        self.defaults['preferences']['auto_reload_on_external_change'] = True
        # 默认编码：新建文档的初始编码，也是无法自动检测编码时的回退。
        self.defaults['preferences']['default_encoding'] = 'utf-8'
        # 默认行尾格式：新建文档的初始换行符。可选 '\n'（LF）、'\r\n'（CRLF）、'\r'（CR）。
        self.defaults['preferences']['default_line_ending'] = '\n'
        # PDF 预览默认缩放模式：'fit_to_width'（适应宽度）、
        # 'fit_to_text_width'（适应文字宽度）、
        # 'fit_to_height'（适应高度）、'manual'（手动缩放，100% 起步）。
        self.defaults['preferences']['preview_zoom'] = 'fit_to_width'
        # 首次运行引导（welcome dialog）：应用真正首次启动时弹一次，
        # 列出 Setzer 的核心功能要点。first_run_tutorial_shown 置 True 后不再
        # 自动弹；偏好页的“再次显示首次引导”按钮可随时手动重看。
        self.defaults['preferences']['first_run_tutorial_shown'] = False

        # —— AI 修复集成（build log → 外部 Agent CLI）——
        # 设计见 .trae/documents/ai-fix-agent-integration.md。
        # 信任目录列表里的 cwd 直接跳过预览弹窗，发送即确认；
        # 依赖上方 auto_reload_on_external_change 在 Agent 修复后自动重载文件。
        self.defaults['preferences']['ai_fix_enabled'] = True
        # 当前激活的工具 name（指向 ai_fix_tools 列表中的 name 字段）。
        self.defaults['preferences']['ai_fix_active_tool'] = 'opencode'
        # 终端命令：留空走自动检测链；用户可填 'xterm' / 'gnome-terminal' 等。
        self.defaults['preferences']['ai_fix_terminal_cmd'] = ''
        # 已信任目录列表：勾「此项目不再提示」后追加；按项目=按文档目录。
        # Preferences 页可手动移除以撤销信任。
        self.defaults['preferences']['ai_fix_trusted_dirs'] = []
        # Agent 工具列表（内置 5 个 + 用户自定义）。每个元素结构见
        # setzer/ai_fix/presets.py。default_tools() 返回深拷贝避免污染常量。
        from setzer.ai_fix.presets import default_tools
        self.defaults['preferences']['ai_fix_tools'] = default_tools()
        # 「忽略此类 warning」：存放被用户右键忽略的 warning/badbox 类型 key
        #（跨构建稳定，与具体行号/文件名无关）。见
        # setzer/dialogs/build_log/build_log_dialog_presenter.classify_warning_type。
        self.defaults['preferences']['ignored_warning_types'] = []

        self.defaults['preferences']['use_system_font'] = True
        textview = Gtk.TextView()
        textview.set_monospace(True)
        font_string = textview.get_pango_context().get_font_description().to_string()
        self.defaults['preferences']['font_string'] = font_string
        # 编辑器字号缩放倍率（1.0 = 默认）：与 font_string 分离，使 system font 模式
        # 下的缩放偏好也能跨重启持久化。
        self.defaults['preferences']['editor_font_zoom_level'] = 1.0

        # 搜索/替换历史记录：全局共享（跨文档），最多 15 条，去重，最新在前。
        self.defaults['search_history'] = {'find': [], 'replace': []}

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
        # symbols 不能用 <Control><Shift>s：与 save_as 相同，而 save_as 在
        # ShortcutControllerApp 中先注册，symbols 永远不会触发。F8 空闲且
        # 与 F5/F6/F7（构建组）、F10-F12（界面组）同风格。
        self.defaults['keyboard_shortcuts']['symbols'] = 'F8'
        self.defaults['keyboard_shortcuts']['save_and_build'] = 'F5'
        self.defaults['keyboard_shortcuts']['build'] = 'F6'
        self.defaults['keyboard_shortcuts']['forward_sync'] = 'F7'
        # build_log 不能用 <Control><Shift>l：与 left（插入 \left）相同，
        # 且 app 控制器在 CAPTURE 阶段消费事件，\left 永远不触发。F4 空闲。
        self.defaults['keyboard_shortcuts']['build_log'] = 'F4'
        self.defaults['keyboard_shortcuts']['preview'] = '<Control><Shift>p'
        # 命令面板不使用 Ctrl+Shift+P（预览）或 Ctrl+Shift+K（删除行）；
        # Ctrl+. 在现有默认快捷键中空闲，且仍可在 Preferences 中改绑。
        self.defaults['keyboard_shortcuts']['command_palette'] = '<Control>period'
        self.defaults['keyboard_shortcuts']['hamburger_menu'] = 'F10'
        self.defaults['keyboard_shortcuts']['fullscreen'] = 'F11'
        self.defaults['keyboard_shortcuts']['context_menu'] = 'F12'
        self.defaults['keyboard_shortcuts']['show_preferences_dialog'] = '<Control>comma'
        # show_about_dialog 不设默认快捷键：About 对话框在各平台均无专属快捷键，
        # 标准入口是「帮助 ▸ 关于」；且 Ctrl+Shift+H 恰是 Ctrl+H（查找替换）的 Shift
        # 变体，易被误触、易误解。动作仍可通过菜单触发，也仍可在偏好设置里手动绑定。
        self.defaults['keyboard_shortcuts']['show_about_dialog'] = ''
        self.defaults['keyboard_shortcuts']['close_all_documents'] = '<Control><Shift>w'
        self.defaults['keyboard_shortcuts']['restore_session'] = '<Control><Shift>j'
        # reopen_last_closed_document：默认 Ctrl+Shift+T（浏览器式"重开标签页"惯例）。
        # 此前为硬编码、用户无法改绑；现提升为可配置项，纳入偏好设置的快捷键编辑器。
        self.defaults['keyboard_shortcuts']['reopen_last_closed_document'] = '<Control><Shift>t'
        self.defaults['keyboard_shortcuts']['cut'] = '<Control>x'
        self.defaults['keyboard_shortcuts']['copy'] = '<Control>c'
        self.defaults['keyboard_shortcuts']['paste'] = '<Control>v'
        self.defaults['keyboard_shortcuts']['undo'] = '<Control>z'
        self.defaults['keyboard_shortcuts']['redo'] = '<Control><Shift>z'
        self.defaults['keyboard_shortcuts']['select_all'] = '<Control>a'
        self.defaults['keyboard_shortcuts']['delete_line'] = '<Control><Shift>k'
        self.defaults['keyboard_shortcuts']['toggle_comment'] = '<Control>slash'
        self.defaults['keyboard_shortcuts']['duplicate_line'] = '<Alt><Shift>d'
        self.defaults['keyboard_shortcuts']['move_line_up'] = '<Alt>Up'
        self.defaults['keyboard_shortcuts']['move_line_down'] = '<Alt>Down'
        self.defaults['keyboard_shortcuts']['new_line'] = '<Control>Return'
        self.defaults['keyboard_shortcuts']['bold'] = '<Control>b'
        self.defaults['keyboard_shortcuts']['italic'] = '<Control>i'
        self.defaults['keyboard_shortcuts']['underline'] = '<Control>u'
        # typewriter 不能用 <Control><Shift>t：该键被 reopen_last_closed_document
        # 占用（浏览器式"重开标签页"惯例，硬编码于 shortcut_controller_app.py）。
        # 改用 <Control><Shift>y（Ctrl+Y 单键是 redo，加 Shift 后空闲）。
        self.defaults['keyboard_shortcuts']['typewriter'] = '<Control><Shift>y'
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
        self.defaults['keyboard_shortcuts']['toggle_bookmark'] = '<Control>f2'
        self.defaults['keyboard_shortcuts']['next_bookmark'] = 'f2'
        self.defaults['keyboard_shortcuts']['previous_bookmark'] = '<Control><Shift>f2'
        # Multi-cursor shortcuts (VS Code-style)
        self.defaults['keyboard_shortcuts']['select_next_occurrence'] = '<Control>d'
        self.defaults['keyboard_shortcuts']['select_all_occurrences'] = '<Control><Shift>l'
        self.defaults['keyboard_shortcuts']['add_cursor_above'] = '<Control><Alt>Up'
        self.defaults['keyboard_shortcuts']['add_cursor_below'] = '<Control><Alt>Down'
        self.defaults['keyboard_shortcuts']['clear_multi_cursor'] = 'Escape'

        # Experimental features (multi-cursor toggles)
        self.defaults['preferences']['experimental_features'] = False
        self.defaults['preferences']['experimental_multicursor'] = False
        self.defaults['preferences']['experimental_alt_click'] = False
        self.defaults['preferences']['experimental_alt_drag'] = False
        self.defaults['preferences']['experimental_select_next'] = False
        self.defaults['preferences']['experimental_select_all'] = False
        self.defaults['preferences']['experimental_add_above'] = False
        self.defaults['preferences']['experimental_add_below'] = False
        self.defaults['preferences']['experimental_escape_clear'] = False
        self.defaults['preferences']['experimental_multiedit'] = False

    def _migrate_conflicting_shortcut_defaults(self):
        '''把已持久化配置中仍等于"旧冲突默认值"的快捷键迁到新默认值。

        首次运行时 defaults 会被整体写入 settings.json，因此仅改 defaults
        无法修复老用户的配置——它们仍保存着冲突的旧默认值：
        - symbols    = <Control><Shift>s（被 save_as 抢占，从未生效）
        - typewriter = <Control><Shift>t（被硬编码的 reopen 标签页抢占）
        - build_log  = <Control><Shift>l（在 CAPTURE 阶段抢占 left 的 \\left）
        只有当保存值仍等于旧默认值时才改写（说明用户从未主动改过该键，
        或改了也因冲突从未生效）；用户自定义的其他值一律保留。
        '''
        shortcuts = self.data.get('keyboard_shortcuts')
        if not isinstance(shortcuts, dict):
            return
        migrations = {
            'symbols': ('<Control><Shift>s', 'F8'),
            'typewriter': ('<Control><Shift>t', '<Control><Shift>y'),
            'build_log': ('<Control><Shift>l', 'F4'),
        }
        changed = False
        for action, (old_default, new_default) in migrations.items():
            if shortcuts.get(action) == old_default:
                shortcuts[action] = new_default
                changed = True
        # 补齐新增的可配置快捷键（如 reopen_last_closed_document）：老用户的已保存
        # 配置中没有这些键，用默认值补上，使其出现在偏好设置编辑器并参与冲突检测、
        # 导入/导出。仅补全缺失键，绝不动用户已自定义的值。
        for action, default in self.defaults['keyboard_shortcuts'].items():
            if action not in shortcuts:
                shortcuts[action] = default
                changed = True
        if changed:
            self.pickle()

    def add_to_search_history(self, field, text, max_items=15):
        """Add text to search history ('find' or 'replace'). Dupes moved to top."""
        if not text or not text.strip():
            return
        history = self.get_value('search_history', None)
        items = list(history.get(field, []))
        if text in items:
            items.remove(text)
        items.insert(0, text)
        self.set_value('search_history', field, items[:max_items])

    def get_search_history(self, field):
        """Return search history list for 'find' or 'replace'."""
        history = self.get_value('search_history', None)
        return list(history.get(field, []))

    def clear_search_history(self, field):
        """Clear search history for 'find' or 'replace'."""
        self.set_value('search_history', field, [])

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
