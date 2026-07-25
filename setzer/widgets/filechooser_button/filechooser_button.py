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
from gi.repository import Gio
from gi.repository import GLib
from gi.repository import Pango

import os.path

from setzer.helpers.observable import Observable


class FilechooserButtonView(Gtk.Button):

    def __init__(self):
        Gtk.Button.__init__(self)

        self.button_widget = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.button_label = Gtk.Label(label=_('(None)'))
        self.button_label.set_ellipsize(Pango.EllipsizeMode.START)
        self.button_widget.append(Gtk.Image(icon_name='document-open-symbolic'))
        self.button_widget.append(self.button_label)
        self.set_child(self.button_widget)


class FilechooserButton(Observable):

    def __init__(self, parent_window):
        Observable.__init__(self)

        self.parent_window = parent_window
        self.default_folder = None
        self.filename = None
        self.filters = list()
        self.title = _('Choose File')

        self.view = FilechooserButtonView()

        self.view.connect('clicked', self.on_button_clicked)

    def reset(self):
        self.default_folder = None
        self.filename = None
        self.view.button_label.set_text(_('(None)'))
        self.view.set_tooltip_text('')

    def set_default_folder(self, folder):
        self.default_folder = folder

    def set_title(self, title):
        self.title = title

    def get_filename(self):
        return self.filename

    def add_filter(self, file_filter):
        self.filters.append(file_filter)

    def on_button_clicked(self, button):
        self.dialog = Gtk.FileDialog()
        self.dialog.set_modal(True)
        self.dialog.set_title(self.title)

        # GTK4 的 FileDialog 通过 set_filters(GListModel) 提供可选过滤器列表,
        # 通过 set_default_filter 指定默认项。原代码在循环里反复调用
        # set_default_filter,只有最后一次生效,其余过滤器从未被注册,
        # 用户在对话框中根本看不到它们。这里用 Gio.ListStore 把全部过滤器
        # 注册进去,并保留“最后一个为默认”的原有行为。
        if len(self.filters) > 0:
            store = Gio.ListStore.new(Gtk.FileFilter)
            for file_filter in self.filters:
                store.append(file_filter)
            self.dialog.set_filters(store)
            self.dialog.set_default_filter(self.filters[-1])

        if self.default_folder != None:
            self.dialog.set_current_folder(self.default_folder)

        self.dialog.open(self.parent_window, None, self.dialog_process_response)

    def dialog_process_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            # GTK4 FileDialog 用户取消/关闭对话框时 open_finish 抛 GLib.Error
            # (gtk-dialog-error-quark: "Dialog was dismissed")。这是正常流程，
            # 静默忽略——不应报错或提示用户。
            pass
        except Exception:
            # 非取消异常（权限不足/IO 错误等，理论上少见）。原实现 except Exception: pass
            # 会连同真实错误一起吞掉，用户点文件后无任何反馈不知是失败还是取消。
            # 打印 traceback 便于诊断；不弹窗——FilechooserButton 是底层组件，
            # 不应假设有 toast 容器，且调用方（如 include_bibtex_file 对话框）
            # 可能有自己的错误处理逻辑。
            import traceback
            traceback.print_exc()
        else:
            if file != None:
                self.filename = file.get_path()
                self.view.button_label.set_text(os.path.basename(self.filename))
                # tooltip 显示完整路径：basename 无法区分同名不同目录的文件
                # (如 project1/main.tex vs project2/main.tex)，tooltip 让用户
                # 悬停即可确认选的是哪个。
                self.view.set_tooltip_text(self.filename)
                self.add_change_code('file-set')


