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
gi.require_version('GtkSource', '5')
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GtkSource
from gi.repository import GLib
from gi.repository import Adw

import re
import os, os.path
import xml.etree.ElementTree as ET

import setzer.settings.settings as settingscontroller


class ServiceLocator():

    main_window = None
    workspace = None
    settings = None
    shortcuts = None
    setzer_version = None
    resources_path = None
    app_icons_path = None
    increments = dict()
    regexes = dict()
    source_language_manager = None
    source_style_scheme_manager = None
    # style_scheme 缓存：get_style_scheme 在每个文档创建时被调用设置 source_buffer
    # 配色。原实现每次都 Adw.StyleManager.get_default().get_dark() + get_scheme(name)
    # 两次 C 调用。深浅色仅在主题切换时变化，缓存命中时省去查找；notify::dark
    # 触发时清空缓存。首次构建时连接失效信号（仅连一次）。
    _style_scheme = None
    _style_scheme_handler_connected = False

    def set_main_window(main_window):
        ServiceLocator.main_window = main_window

    def get_main_window():
        return ServiceLocator.main_window

    def set_workspace(workspace):
        ServiceLocator.workspace = workspace

    def get_workspace():
        return ServiceLocator.workspace

    def set_shortcuts(shortcuts):
        ServiceLocator.shortcuts = shortcuts

    def get_shortcuts():
        return ServiceLocator.shortcuts

    def get_increment(key):
        if key not in ServiceLocator.increments:
            ServiceLocator.increments[key] = 0
        ServiceLocator.increments[key] += 1
        return ServiceLocator.increments[key]

    def get_regex_object(pattern):
        if pattern in ServiceLocator.regexes:
            return ServiceLocator.regexes[pattern]
        else:
            regex = re.compile(pattern)
            ServiceLocator.regexes[pattern] = regex
            return regex

    def get_settings():
        if ServiceLocator.settings == None:
            ServiceLocator.settings = settingscontroller.Settings(ServiceLocator.get_config_folder())
        return ServiceLocator.settings

    def get_config_folder():
        return os.path.join(GLib.get_user_config_dir(), 'setzer')

    def set_setzer_version(setzer_version):
        ServiceLocator.setzer_version = setzer_version

    def get_setzer_version():
        return ServiceLocator.setzer_version

    def set_resources_path(resources_path):
        ServiceLocator.resources_path = resources_path

    def get_resources_path():
        return ServiceLocator.resources_path

    def set_app_icons_path(app_icons_path):
        ServiceLocator.app_icons_path = app_icons_path

    def get_app_icons_path():
        return ServiceLocator.app_icons_path

    def get_source_language_manager():
        if ServiceLocator.source_language_manager == None:
            ServiceLocator.source_language_manager = GtkSource.LanguageManager()
            path = os.path.join(ServiceLocator.get_resources_path(), 'language-specs')
            ServiceLocator.source_language_manager.set_search_path((path,))
        return ServiceLocator.source_language_manager

    def get_source_style_scheme_manager():
        if ServiceLocator.source_style_scheme_manager == None:
            ServiceLocator.source_style_scheme_manager = GtkSource.StyleSchemeManager()
            path1 = os.path.join(ServiceLocator.get_resources_path(), 'themes')
            if not os.path.isdir(os.path.join(ServiceLocator.get_config_folder(), 'themes')):
                os.mkdir(os.path.join(ServiceLocator.get_config_folder(), 'themes'))
            path2 = os.path.join(ServiceLocator.get_config_folder(), 'themes')
            ServiceLocator.source_style_scheme_manager.set_search_path((path1, path2))
        return ServiceLocator.source_style_scheme_manager

    def get_source_language(language):
        source_language_manager = ServiceLocator.get_source_language_manager()
        if language == 'bibtex': return source_language_manager.get_language('bibtex')
        else: return source_language_manager.get_language('latex')

    def get_style_scheme():
        # 编辑器配色跟随应用深浅色（Preferences 中不再提供独立选择）
        if ServiceLocator._style_scheme is None:
            try:
                dark = Adw.StyleManager.get_default().get_dark()
            except Exception:
                dark = False
            name = 'default-dark' if dark else 'default'
            ServiceLocator._style_scheme = ServiceLocator.get_source_style_scheme_manager().get_scheme(name)
            # 首次构建时连接 notify::dark 失效缓存（仅连一次）。
            if not ServiceLocator._style_scheme_handler_connected:
                try:
                    Adw.StyleManager.get_default().connect('notify::dark', ServiceLocator._invalidate_style_scheme)
                except Exception:
                    pass
                ServiceLocator._style_scheme_handler_connected = True
        return ServiceLocator._style_scheme

    def _invalidate_style_scheme(*args):
        ServiceLocator._style_scheme = None


