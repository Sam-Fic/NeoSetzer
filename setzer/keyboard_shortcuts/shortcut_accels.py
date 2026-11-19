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
# along with <http://www.gnu.org/licenses/>

'''窗口级快捷键的 application accelerator 注册（set_accels_for_action）。

此前这些键注册在挂到主窗口的 Gtk.ShortcutController（CAPTURE 相位）上，
有三个长期问题，迁移一并解决：

1. 菜单项右侧的快捷键提示要靠每处手写 accel 字面量（汉堡菜单 13 处、
   上下文菜单 13 处），用户在偏好页改键后提示立即漂移；
2. popover 打开时 Shortcuts 会摘掉整个控制器（引用计数逻辑），导致
   弹出菜单里全局快捷键全部失效（issue 4.5）；
3. 偏好页冲突检测只能对比 settings 字典里的字符串，看不到 F3 / Ctrl+L
   这类固定别名，改绑可覆盖它们而不报冲突。

迁移的关键约束（均经实测验证，勿凭直觉改动）：

- application accel 在焦点控件的控制器**之前**处理：把剪贴板类键
  （cut/copy/paste/undo/redo/select_all）迁过来会抢走对话框与输入框的
  Ctrl+C/Ctrl+A（实测 app accel 命中时 Entry 的内建选区操作不触发）。
  这类键必须留在 source_view 上的 document 层控制器里，故不在下方映射表。
- application accel 不会逃进独立顶层窗口：偏好页/快捷键页是 Adw.Dialog
  （独立 toplevel），其中按键不会触发主窗口的 win.* 加速器（实测为 0），
  快捷键捕获不受干扰。
- 同一个键只能映射到一个 action（后注册者覆盖），因此改绑冲突必须由
  偏好页拦下——find_conflicting_action 基于 app.get_accels_for_action
  扫描本表注册的全部动作。
'''

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gio

from setzer.app.service_locator import ServiceLocator
from setzer.keyboard_shortcuts.shortcut_controller import ShortcutController
from setzer.popovers.popover_manager import PopoverManager


# settings 键 -> 窗口动作（'win.<name>'）。只收录迁移到 application accel 的
# 键；未收录的键（剪贴板/行操作/书签/多光标等）仍由 document、LaTeX 层
# 控制器处理，其绑定值在 settings 字典里，冲突检测直接读 settings。
SETTINGS_KEY_TO_ACTION = {
    'new_document': 'win.new-latex-document',
    'open_document': 'win.open-document-dialog',
    'save': 'win.save',
    'save_as': 'win.save-as',
    'print': 'win.print',
    'close_document': 'win.close-active-document',
    'reopen_last_closed_document': 'win.reopen-last-closed-document',
    'quit': 'win.quit',
    'show_shortcuts': 'win.show-shortcuts-dialog',
    'show_open_docs': 'win.show-open-docs',
    'switch_document': 'win.switch-document',
    'show_document_chooser': 'win.show-document-chooser',
    'zoom_in': 'win.zoom-in',
    'zoom_out': 'win.zoom-out',
    'reset_zoom': 'win.reset-zoom',
    'find': 'win.start-search',
    'select_all_occurrences': 'win.select-all-occurrences',
    'find_and_replace': 'win.start-search-and-replace',
    'find_next': 'win.find-next',
    'find_previous': 'win.find-previous',
    'help': 'win.show-help-panel',
    'document_structure': 'win.show-document-structure',
    'symbols': 'win.show-symbols',
    'save_and_build': 'win.save-and-build',
    'build': 'win.build',
    'forward_sync': 'win.forward-sync',
    'build_log': 'win.toggle-build-log',
    'preview': 'win.toggle-preview',
    'command_palette': 'win.show-command-palette',
    'hamburger_menu': 'win.show-hamburger-menu',
    'fullscreen': 'win.toggle-fullscreen',
    'show_preferences_dialog': 'win.show-preferences-dialog',
    'show_about_dialog': 'win.show-about-dialog',
    'insert_table_dialog': 'win.insert-table-dialog',
    'insert_matrix_dialog': 'win.insert-matrix-dialog',
    'close_all_documents': 'win.close-all-documents',
    'restore_session': 'win.restore-session',
    'pin_tab': 'win.pin-tab',
    'ai_add_annotation': 'win.ai-add-annotation',
    'ai_send_selection': 'win.ai-send-selection',
}

# 固定别名键：不来自设置、无法在偏好页改绑，作为 accel 数组的附加项并入
# 对应动作（文档快捷键窗口与冲突检测都能看到它们）。
ACTION_ALIASES = {
    'win.find-next': ['F3'],
    'win.find-previous': ['<Shift>F3'],
}

# 不属于任何 settings 键的固定加速器。
FIXED_ACCELS = {
    'win.go-to-line': ['<Control>l'],
}


class ShortcutAccels(object):

    def __init__(self):
        self.main_window = ServiceLocator.get_main_window()
        self.workspace = ServiceLocator.get_workspace()
        self.settings = ServiceLocator.get_settings()
        self.app = self.main_window.app

        self._create_shortcut_actions()
        self.load_shortcuts()
        # 改键/重置/导入最终都落到 settings.set_value('keyboard_shortcuts', …)，
        # 这里统一监听并整体重注册，页面侧无需（也不应）各自驱动更新。
        self.settings.connect('settings_changed', self.on_settings_changed)

    def _create_shortcut_actions(self):
        '''注册仅服务于快捷键、菜单没有入口的窗口动作。

        其余键复用 Actions 已注册的动作（win.save、win.find-next 等），
        与菜单项、命令面板共用同一实现。'''
        callbacks = {
            'show-open-docs': self.show_document_chooser,
            'show-document-chooser': self.show_document_chooser,
            'switch-document': self.switch_document,
            'toggle-build-log': self.toggle_build_log,
            'toggle-preview': self.toggle_preview,
            'show-document-structure': self.show_document_structure,
            'show-symbols': self.show_symbols,
            'show-hamburger-menu': self.show_hamburger_menu,
        }
        for name, callback in callbacks.items():
            if self.main_window.lookup_action(name) is not None:
                continue
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.main_window.add_action(action)

    def load_shortcuts(self):
        '''把 settings 里的全部窗口级键（含别名）注册为 application accel。

        空键（如 show_about_dialog 默认未绑定）会清空该动作的 accel 数组，
        表示「未分配」。'''
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = self.settings.defaults['keyboard_shortcuts']

        for settings_key, detailed_action in SETTINGS_KEY_TO_ACTION.items():
            trigger = shortcuts.get(settings_key, '')
            accels = [trigger] if trigger else []
            accels += ACTION_ALIASES.get(detailed_action, [])
            self.app.set_accels_for_action(detailed_action, accels)

        for detailed_action, accels in FIXED_ACCELS.items():
            if detailed_action in SETTINGS_KEY_TO_ACTION.values():
                continue
            self.app.set_accels_for_action(detailed_action, list(accels))

    def on_settings_changed(self, settings, parameter):
        section, _item, _value = parameter
        if section == 'keyboard_shortcuts':
            self.load_shortcuts()

    # ---- 仅快捷键使用的动作回调（迁自 ShortcutControllerApp） ----

    def show_document_chooser(self, action, parameter):
        if self.main_window.headerbar.center_button.get_sensitive():
            PopoverManager.get_popover('document_switcher').open()

    def switch_document(self, action, parameter):
        self.workspace.switch_to_earliest_open_document()

    def toggle_build_log(self, action, parameter):
        self.workspace.set_show_build_log(not self.workspace.get_show_build_log())

    def toggle_preview(self, action, parameter):
        toggle = self.main_window.headerbar.preview_help_toggle
        if toggle.get_sensitive():
            toggle.set_active(not toggle.get_active())

    def show_document_structure(self, action, parameter):
        toggle = self.main_window.headerbar.sidebar_toggle
        if not toggle.get_sensitive():
            return
        if not toggle.get_active():
            toggle.set_active(True)
            self.workspace.set_show_symbols_or_document_structure(False, True)
        elif self.workspace.show_document_structure:
            toggle.set_active(False)
        else:
            self.workspace.set_show_symbols_or_document_structure(False, True)

    def show_symbols(self, action, parameter):
        toggle = self.main_window.headerbar.sidebar_toggle
        if not toggle.get_sensitive():
            return
        if not toggle.get_active():
            toggle.set_active(True)
            self.workspace.set_show_symbols_or_document_structure(True, False)
        elif self.workspace.show_symbols:
            toggle.set_active(False)
        else:
            self.workspace.set_show_symbols_or_document_structure(True, False)

    def show_hamburger_menu(self, action, parameter):
        self.main_window.headerbar.menu_button.popup()


class SectionNavigationController(ShortcutController):
    '''章节导航：Alt+Up / Alt+Down 跳到上/下一段。

    这两个键不迁 application accel：在源码视图里 Alt+Up/Down 语义是移动行
    （可配置，由 document 层控制器处理），本控制器必须在编辑态返回 False
    把事件放行给它——application accel 无法「放行」，抢先命中会架空用户
    的移动行改绑。'''

    def __init__(self):
        ShortcutController.__init__(self)

        self.workspace = ServiceLocator.get_workspace()

        self.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        self.create_and_add_shortcut('<Alt>Up', self.shortcut_prev_section)
        self.create_and_add_shortcut('<Alt>Down', self.shortcut_next_section)

    def shortcut_prev_section(self):
        if self._is_editing():
            return False
        self._navigate_section(-1)
        return True

    def shortcut_next_section(self):
        if self._is_editing():
            return False
        self._navigate_section(1)
        return True

    def _is_editing(self):
        document = self.workspace.get_active_document()
        if document is None:
            return False
        return document.source_view.has_focus()

    def _navigate_section(self, direction):
        sidebar = getattr(self.workspace, 'sidebar', None)
        if sidebar is None:
            return
        if getattr(self.workspace, 'show_symbols', False):
            page = sidebar.symbols_page
        else:
            page = sidebar.document_structure_page
        if direction < 0:
            page.scroll_prev_section()
        else:
            page.scroll_next_section()
