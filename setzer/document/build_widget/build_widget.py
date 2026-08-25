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
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib

import setzer.document.build_widget.build_widget_viewgtk as build_widget_view
from setzer.helpers.observable import Observable
from setzer.keyboard_shortcuts import shortcut_tooltips
from setzer.app.service_locator import ServiceLocator
from setzer.dialogs.dialog_locator import DialogLocator
from setzer.app.color_manager import ColorManager
from setzer.settings.document_settings import DocumentSettings
from setzer.document.magic_comments import parse_magic_comments

import time
import os.path


# LaTeX 辅助文件扩展名列表（构建产物）。原代码在 set_clean_button_state 和
# on_clean_button_click 中各定义一份字面量列表（且略有不同），每次调用都
# 重新创建。提到模块级常量后：1) 避免重复创建列表；2) 统一两处定义。
# on_clean_button_click 额外需要 .xdv 和 .out.ps，合并到同一列表。
_CLEANUP_FILE_ENDINGS = [
    '.aux', '.blg', '.bbl', '.dvi', '.xdv', '.fdb_latexmk', '.fls',
    '.idx', '.ilg', '.ind', '.log', '.nav', '.out', '.snm', '.synctex.gz',
    '.toc', '.ist', '.glo', '.glg', '.acn', '.alg', '.gls', '.acr',
    '.bcf', '.run.xml', '.out.ps',
]

# 解释器键 -> 用户可见的引擎名（与文档菜单 Build Interpreter 子菜单一致）。
_INTERPRETER_DISPLAY = {
    'pdflatex': 'PDFLaTeX',
    'xelatex': 'XeLaTeX',
    'lualatex': 'LuaLaTeX',
    'tectonic': 'Tectonic',
}

class BuildWidget(Observable):

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document
        self.settings = ServiceLocator.get_settings()

        self.items = list()

        self.view = build_widget_view.BuildWidgetView()
        self.view.build_button.connect('clicked', self.on_build_button_click)
        self.view.clean_button.connect('clicked', self.on_clean_button_click)

        self.build_button_state = ('idle', int(time.time()*1000))
        self.set_clean_button_state()
        self.update_build_button()
        self.update_build_button_tooltip()

        self.document.connect('filename_change', self.on_filename_change)
        self.document.build_system.connect('build_state_change', self.on_build_state_change)
        self.document.build_system.connect('build_state', self.on_build_state)
        self.document.build_system.connect('build_stage', self.on_build_stage)
        # 每文档/全局解释器变化时刷新“保存并构建”按钮的 tooltip（含引擎名，见项 15）。
        self.document.build_system.connect('latex_interpreter_changed', self.update_build_button_tooltip)
        # 项目构建 profile 变化时（对话框保存）刷新 tooltip 显示生效配置。
        self.document.build_system.connect('project_profile_changed', self.update_build_button_tooltip)
        # 保存回调引用以便 shutdown 时断开 settings 单例连接。
        self._settings_callback = self.on_settings_changed
        self.settings.connect('settings_changed', self._settings_callback)

    def shutdown(self):
        '''文档关闭时由 Document.shutdown 调用。停止构建计时器,避免在构建
        进行中文档被关闭时计时器 timeout 永久泄漏。settings 连接由
        Document.shutdown 集中处理。'''
        self.view.stop_timer()

    def on_filename_change(self, document, filename=None):
        self.set_clean_button_state()

    def on_build_state_change(self, build_system, build_state):
        document = self.document
        if document.build_system.build_mode in ['build', 'build_and_forward_sync']:
            state = document.build_system.get_build_state()
            selfstate = self.build_button_state
            if state == 'idle' or state == '':
                build_button_state = ('idle', int(time.time()*1000))
            elif state == 'building_to_stop':
                build_button_state = ('stopping', int(time.time()*1000))
            else:
                build_button_state = ('building', int(time.time()*1000))

            if selfstate[0] != build_button_state[0]:
                self.build_button_state = build_button_state
                if build_button_state[0] == 'idle':
                    self.view.switch_to_idle()
                    self.view.build_button.set_sensitive(True)
                    self.update_build_button_tooltip()
                elif build_button_state[0] == 'stopping':
                    # 构建正在停止：按钮保持停止图标但不可点击，
                    # 防止用户在进程退出前重复点击。
                    self.view.build_button.set_sensitive(False)
                else:
                    self.view.clear_stage()
                    self.view.switch_to_building()
                    self.view.build_button.set_sensitive(True)
                    self.view.reset_timer()
                    self.view.start_timer()
        else:
            self.view.switch_to_idle()
            self.view.build_button.set_sensitive(True)
            self.update_build_button_tooltip()
            self.build_button_state = ('idle', int(time.time()*1000))
        self.set_clean_button_state()

    def on_build_stage(self, build_system, stage):
        # 仅在构建/building 态显示阶段；纯 forward/backward sync 模式按钮不在
        # building 态，阶段事件应被忽略，避免误显示。
        if self.build_button_state[0] != 'building':
            return
        job_name, index = stage
        self.view.set_stage(self._stage_label(job_name), index)

    def _stage_label(self, job_name):
        # 懒求值：_() 在 gettext.install 之后才可用，不能在模块顶层调用
        # （否则导入期 NameError）。首次调用（运行时）构建并缓存映射表。
        if not hasattr(self, '_stage_labels'):
            self._stage_labels = {
                'build_latex': _('LaTeX'),
                'build_bibtex': _('BibTeX'),
                'build_biber': _('Biber'),
                'build_makeindex': _('MakeIndex'),
                'build_glossaries': _('Glossaries'),
                'forward_sync': _('Sync'),
            }
        return self._stage_labels.get(job_name, job_name)

    def on_build_state(self, build_system, message):
        if message == '':
            self.show_message('')
        elif message == 'success':
            self.show_message(_('Success!'))
            self._show_toast(_('Build succeeded'))
        elif message == 'error':
            error_count = build_system.get_error_count()
            error_color_rgba = ColorManager.get_ui_color_string('error_color')

            # ngettext 处理单复数：英语 "1 error" / "N errors"，
            # 俄语等有 3 种复数形式（1 ошибка / 2 ошибки / 5 ошибок）。
            # 旧实现拆分 "Failed" + "errors" 分别翻译，无法处理语序差异和复数形式。
            # Pango markup：error_color_rgba 作为 XML 属性值不转义；译文作为元素
            # 文本内容必须转义（GLib.markup_escape_text）以防 < > & 破坏解析。
            errors_text = ngettext('{count} error', '{count} errors', error_count).format(count=error_count)
            failed_text = GLib.markup_escape_text(_('Failed'), -1)
            error_text = GLib.markup_escape_text(errors_text, -1)
            message = '<span color="' + error_color_rgba + '">' + failed_text + '</span> (' + error_text + ')!'
            self.show_message(message)
            self._show_toast(_('Build failed') + ' (' + errors_text + ')')

    def _show_toast(self, text):
        main_window = ServiceLocator.get_main_window()
        if hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(text)
            toast.set_timeout(3)
            main_window.toast_overlay.add_toast(toast)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if (section, item) == ('preferences', 'cleanup_build_files'):
            self.set_clean_button_state()
        elif item == 'latex_interpreter':
            # 全局默认引擎变化：刷新 tooltip 中的引擎名。
            self.update_build_button_tooltip()

    def show_message(self, message=''):
        self.view.stop_timer()
        self.view.switch_to_idle()
        self.update_build_button_tooltip()

    def on_build_button_click(self, button_object=None):
        if self.build_button_state[0] == 'building':
            document = self.document
            if document != None:
                self.document.build_system.stop_building()

    def set_clean_button_state(self):
        def get_clean_button_state(document):
            if document != None:
                if document.filename != None:
                    # 与 on_clean_button_click 一致用 os.path.splitext 去扩展名：
                    # 原 rsplit('/', 1) + rsplit('.', 1) 对无 '/' 的相对路径会
                    # IndexError，且与 on_clean_button_click 写法不统一。
                    filename_base = os.path.splitext(document.get_filename())[0]
                    for ending in _CLEANUP_FILE_ENDINGS:
                        if os.path.exists(filename_base + ending): return True
            return False

        if DocumentSettings.get_effective_value(self.document, self.settings, 'cleanup_build_files') == True:
            self.view.clean_button.set_visible(False)
        else:
            # 无构建产物时隐藏按钮（而非显示灰色不可点击按钮），减少视觉干扰。
            self.view.clean_button.set_visible(get_clean_button_state(self.document))

    def on_clean_button_click(self, button_object=None):
        document = self.document
        if self.document == None: return
        if self.document.filename == None: return

        filename_base = os.path.splitext(document.get_filename())[0]
        for ending in _CLEANUP_FILE_ENDINGS:
            try: os.remove(filename_base + ending)
            except FileNotFoundError: pass

        self.set_clean_button_state()

    def update_build_button(self):
        building_in_progress = not (self.document.build_system.get_build_state() in ['', 'idle'])
        if building_in_progress:
            self.view.switch_to_building()
        else:
            self.view.switch_to_idle()
            self.update_build_button_tooltip()
        self.view.build_button.set_sensitive(True)

    def _active_interpreter_display(self):
        '''当前文档实际使用的引擎显示名：Magic Comment 优先于已保存设置。'''
        magic = parse_magic_comments(self.document.get_all_text())
        interp = magic.program or DocumentSettings.get_effective_value(
            self.document, self.settings, 'latex_interpreter')
        return _INTERPRETER_DISPLAY.get(interp, interp)

    def update_build_button_tooltip(self, *args):
        # 构建中按钮是“停止”语义，tooltip 由 view.switch_to_building 设为
        # “Stop building”，此处不应覆盖；仅在空闲/消息态展示引擎名。
        if self.build_button_state[0] == 'building':
            return
        engine = self._active_interpreter_display()
        text = _('Save and build .pdf-file from document')
        if engine:
            text = text + '  ·  ' + engine
        # 显示当前项目生效的构建 profile 名（多发行物/arXiv/出版社/幻灯片等）。
        profile = self.document.build_system.get_active_profile_name()
        if profile:
            text = text + '  ·  [' + profile + ']'
        # 经注册处设置：快捷键后缀随设置实时渲染，用户改键后自动刷新
        shortcut_tooltips.set_tooltip(self.view.build_button, text, 'save_and_build')


