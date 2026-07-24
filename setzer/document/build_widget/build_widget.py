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

import setzer.document.build_widget.build_widget_viewgtk as build_widget_view
from setzer.helpers.observable import Observable
from setzer.app.service_locator import ServiceLocator
from setzer.dialogs.dialog_locator import DialogLocator
from setzer.app.color_manager import ColorManager

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

        self.document.connect('filename_change', self.on_filename_change)
        self.document.build_system.connect('build_state_change', self.on_build_state_change)
        self.document.build_system.connect('build_state', self.on_build_state)
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
            else:
                build_button_state = ('building', int(time.time()*1000))

            if selfstate[0] != build_button_state[0]:
                self.build_button_state = build_button_state
                if build_button_state[0] == 'idle':
                    self.view.switch_to_idle()
                    self.view.build_button.set_sensitive(True)
                else:
                    self.view.switch_to_building()
                    self.view.build_button.set_sensitive(True)
                    self.view.reset_timer()
                    self.view.start_timer()
        else:
            self.view.switch_to_idle()
            self.view.build_button.set_sensitive(True)
            self.build_button_state = ('idle', int(time.time()*1000))
            self.view.hide_result_now()
        self.set_clean_button_state()

    def on_build_state(self, build_system, message):
        if message == '':
            self.show_message('')
        elif message == 'success':
            self.show_message(_('Success!'))
        elif message == 'error':
            error_count = build_system.get_error_count()
            error_color_rgba = ColorManager.get_ui_color_string('error_color')

            message = '<span color="' + error_color_rgba + '">' + _('Failed') + '</span> '
            if error_count == 1:
                message += '(' + _('1 error') + ')!'
            else:
                message += '(' + str(error_count) + ' ' + _('errors') + ')!'
            self.show_message(message)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if (section, item) == ('preferences', 'cleanup_build_files'):
            self.set_clean_button_state()

    def show_message(self, message=''):
        self.view.stop_timer()
        self.view.switch_to_idle()
        self.view.show_result(message)
        if self.view.get_parent() != None:
            self.view.hide_result(1600)

    def on_build_button_click(self, button_object=None):
        if self.build_button_state[0] == 'building':
            document = self.document
            if document != None:
                if document.filename != None:
                    self.document.build_system.stop_building()

    def set_clean_button_state(self):
        def get_clean_button_state(document):
            if document != None:
                if document.filename != None:
                    pathname = document.get_filename().rsplit('/', 1)
                    for ending in _CLEANUP_FILE_ENDINGS:
                        filename = pathname[0] + '/' + pathname[1].rsplit('.', 1)[0] + ending
                        if os.path.exists(filename): return True
            return False

        if self.settings.get_value('preferences', 'cleanup_build_files') == True:
            self.view.clean_button.set_visible(False)
        else:
            self.view.clean_button.set_visible(True)
            self.view.clean_button.set_sensitive(get_clean_button_state(self.document))

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
        self.view.build_button.set_sensitive(True)


