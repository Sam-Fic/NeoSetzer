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
gi.require_version('GtkSource', '5')
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GtkSource
from gi.repository import GLib
from gi.repository import Adw

import re
import os, os.path
import warnings
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
        # 早期访问检测：main_window 在 app bootstrap 早期由 set_main_window
        # 注入。若调用方在此之前访问，原实现静默返回 None，调用方随后的
        # `.something` 会抛 AttributeError，但栈帧看不出根因是初始化顺序。
        # 这里发出 RuntimeWarning（含调用位置）帮助定位 init-order bug；
        # 仍返回 None 以保持原行为，避免在生产环境把"顺序问题"变成"崩溃"。
        if ServiceLocator.main_window is None:
            warnings.warn(
                'ServiceLocator.get_main_window() returned None — called before '
                'set_main_window(). This is an initialization-order bug; the '
                'caller will likely raise AttributeError next.',
                RuntimeWarning, stacklevel=2)
        return ServiceLocator.main_window

    def set_workspace(workspace):
        ServiceLocator.workspace = workspace

    def get_workspace():
        # 同 get_main_window：workspace 在 bootstrap 早期注入，此前访问返回
        # None 并发出 RuntimeWarning，便于定位 init-order bug。
        if ServiceLocator.workspace is None:
            warnings.warn(
                'ServiceLocator.get_workspace() returned None — called before '
                'set_workspace(). This is an initialization-order bug; the '
                'caller will likely raise AttributeError next.',
                RuntimeWarning, stacklevel=2)
        return ServiceLocator.workspace

    def is_initialized():
        '''检查核心服务（main_window、workspace）是否已注入。

        在 bootstrap 早期、不确定服务是否就绪时可用于守卫访问，避免静默
        拿到 None 后再下游崩溃。settings 有懒初始化故不需检查；其余服务
        （shortcuts/version/paths）通常在更早期就绪，按需单独判断即可。

        返回 True 当且仅当 main_window 与 workspace 均非 None。
        '''
        return (ServiceLocator.main_window is not None
                and ServiceLocator.workspace is not None)

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
            resources_path = ServiceLocator.get_resources_path()
            if resources_path:
                path = os.path.join(resources_path, 'language-specs')
                ServiceLocator.source_language_manager.set_search_path((path,))
        return ServiceLocator.source_language_manager

    def get_source_style_scheme_manager():
        if ServiceLocator.source_style_scheme_manager == None:
            ServiceLocator.source_style_scheme_manager = GtkSource.StyleSchemeManager()
            resources_path = ServiceLocator.get_resources_path()
            if resources_path:
                path1 = os.path.join(resources_path, 'themes')
            else:
                path1 = None
            if not os.path.isdir(os.path.join(ServiceLocator.get_config_folder(), 'themes')):
                os.mkdir(os.path.join(ServiceLocator.get_config_folder(), 'themes'))
            path2 = os.path.join(ServiceLocator.get_config_folder(), 'themes')
            # 应用自定义路径在前，确保 Setzer 的 default / default-dark 优先于
            # 同名系统方案；同时保留系统默认搜索路径，使用户在 Appearance 偏好
            # 中可选择 Adwaita / Solarized / Oblivion 等 GtkSource 内置方案。
            default_paths = ServiceLocator.source_style_scheme_manager.get_search_path()
            if path1:
                combined_paths = (path1, path2) + tuple(p for p in default_paths if p not in (path1, path2))
            else:
                combined_paths = (path2,) + tuple(p for p in default_paths if p != path2)
            ServiceLocator.source_style_scheme_manager.set_search_path(combined_paths)
        return ServiceLocator.source_style_scheme_manager

    def get_source_language(language):
        source_language_manager = ServiceLocator.get_source_language_manager()
        if language == 'bibtex': return source_language_manager.get_language('bibtex')
        else: return source_language_manager.get_language('latex')

    def get_style_scheme():
        # 编辑器配色优先使用用户在 Preferences 中显式选择的方案（editor_style_scheme）；
        # 为空时跟随应用深浅色主题（default / default-dark）。
        # _style_scheme 缓存命中时省去 settings 读取 + get_scheme 查找；
        # notify::dark 触发 _invalidate_style_scheme 清空缓存，下次重新计算。
        # 用户切换方案时 set_style_scheme_name 也会清空缓存并 set_value，
        # settings_changed 信号驱动已打开文档 on_settings_changed 重新应用。
        if ServiceLocator._style_scheme is None:
            scheme_name = ServiceLocator.get_settings().get_value('preferences', 'editor_style_scheme')
            if scheme_name is None or scheme_name == '':
                # 未设置：跟随系统主题
                dark = ServiceLocator._get_dark()
                scheme_name = 'default-dark' if dark else 'default'
            scheme = ServiceLocator.get_source_style_scheme_manager().get_scheme(scheme_name)
            if scheme is None:
                # 方案 ID 不存在（用户手动改 settings.json 或方案文件被删除）：
                # 回退到跟随系统主题，避免 set_style_scheme(None) 报错。
                dark = ServiceLocator._get_dark()
                scheme = ServiceLocator.get_source_style_scheme_manager().get_scheme(
                    'default-dark' if dark else 'default')
            ServiceLocator._style_scheme = scheme
            # 首次构建时连接 notify::dark 失效缓存（仅连一次）。
            # 即使用户选择了固定方案，系统主题变化时 _invalidate_style_scheme
            # 仍会清空缓存，下次 get_style_scheme 重新读取用户设置返回同一方案——
            # 略有冗余但无害，且 document.on_theme_colors_changed 据此重应用。
            if not ServiceLocator._style_scheme_handler_connected:
                try:
                    Adw.StyleManager.get_default().connect('notify::dark', ServiceLocator._invalidate_style_scheme)
                except Exception:
                    pass
                ServiceLocator._style_scheme_handler_connected = True
        return ServiceLocator._style_scheme

    def _get_dark():
        '''返回当前是否深色主题。Adw.StyleManager 不可用时回退到 light。

        get_default() 在非 GNOME / headless 测试环境可能返回 None，此时
        .get_dark() 抛 AttributeError。精确捕获 AttributeError 而非 bare
        except Exception——避免吞掉其他真正的 bug（如 GLib 初始化异常），
        同时打印诊断信息到 stderr：原实现静默回退 light，配色不匹配时
        难以排查。connect notify::dark 处仍保留 except Exception，因为
        connect 失败的根因多样且仅影响缓存失效（非功能性）。
        '''
        try:
            return Adw.StyleManager.get_default().get_dark()
        except AttributeError:
            import sys
            print('[ServiceLocator] Adw.StyleManager.get_default() unavailable, falling back to light theme.', file=sys.stderr)
            return False

    def set_style_scheme_name(name):
        '''用户在偏好设置中选择编辑器配色方案。

        先清空缓存使下次 get_style_scheme 重新读取设置；再 set_value 触发
        settings_changed，已打开文档的 on_settings_changed 会重新调用
        set_style_scheme(get_style_scheme()) 应用新方案。name 为空字符串
        表示恢复跟随系统主题。
        '''
        ServiceLocator._style_scheme = None
        ServiceLocator.get_settings().set_value('preferences', 'editor_style_scheme', name)

    def _invalidate_style_scheme(*args):
        ServiceLocator._style_scheme = None


