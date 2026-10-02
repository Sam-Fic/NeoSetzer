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
from gi.repository import Gtk, GLib, Adw, Gio, Pango, GObject

import os

from setzer.dialogs.helpers.dialog_viewgtk import DialogView


class FileChooserButton(Gtk.Button):
    """普通按钮 + 自定义 file-set 信号（模拟旧 Gtk.FileChooserButton 接口，
    GTK4 已移除该控件，file-set 由文件对话框回调中手动 emit）。"""

    __gsignals__ = {
        'file-set': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }


# 样式档位表：(传给 LaTeX \\bibliographystyle 的名字, 界面标签)。
# 控制器的 self.styles / self.natbib_styles 由这里派生，避免两处漂移。
#
# 标签刻意不做翻译：这些是 BibTeX 的样式标识符（bst 文件名），本身没有
# 语义可译——各语言的旧 po 里它们的 msgstr 全部恒等于 msgid（Plain→Plain、
# Apalike→Apalike）。故不用 _() 包裹，也就不在 pot 里占条目，避免 9 条
# 永远恒等的翻译噪音。同类先例见 insert_table 的 'Booktabs'。
STYLE_OPTIONS = (
    ('plain', 'Plain'),
    ('abbrv', 'Abbrv'),
    ('alpha', 'Alpha'),
    ('apalike', 'Apalike'),
    ('ieeetr', 'iEEEtr'),
)
NATBIB_STYLE_OPTIONS = (
    ('plainnat', 'Plainnat'),
    ('abbrvnat', 'Abbrvnat'),
    ('unsrtnat', 'Unsrtnat'),
    ('achemso', 'Achemso'),
)


class IncludeBibTeXFileView(DialogView):

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)

        self.set_content_width(400)
        self.set_content_height(300)
        self.set_can_focus(False)
        self.headerbar.set_show_start_title_buttons(False)
        self.headerbar.set_show_end_title_buttons(False)
        self.headerbar.set_title_widget(Adw.WindowTitle(title=_('Include BibTeX file')))
        self.topbox.set_size_request(400, -1)

        self.cancel_button = Gtk.Button.new_with_mnemonic(_('_Cancel'))
        self.cancel_button.set_can_focus(False)
        self.headerbar.pack_start(self.cancel_button)

        self.include_button = Gtk.Button.new_with_mnemonic(_('_Include'))
        self.include_button.set_can_focus(False)
        self.include_button.add_css_class('suggested-action')
        self.headerbar.pack_end(self.include_button)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content.set_margin_start(18)
        self.content.set_margin_end(18)

        # 直接用 Adw.PreferencesGroup 提供原生设置界面外观。
        # 不用 Adw.PreferencesPage：它内部自带 Gtk.ScrolledWindow，
        # 放进对话框会造成嵌套滚动层级。
        self.bibtex_group = Adw.PreferencesGroup(title=_('Bibliography'))
        self.content.append(self.bibtex_group)

        # 创建 ActionRow
        self.action_row = Adw.ActionRow(title=_('BibTeX file to include'))
        self.bibtex_group.add(self.action_row)

        # 创建非扁平按钮（标准 Gtk.Button 默认就是非扁平的）
        self.file_chooser_button = FileChooserButton()
        self.file_chooser_button.remove_css_class('flat')

        # 按钮内容：图标 + 文本
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.file_chooser_button.set_child(button_box)

        folder_icon = Gtk.Image.new_from_icon_name('folder-symbolic')
        button_box.append(folder_icon)

        self.button_label = Gtk.Label(label=_('(None)'))
        self.button_label.set_ellipsize(Pango.EllipsizeMode.START)
        button_box.append(self.button_label)

        # 将按钮作为 suffix 嵌入 ActionRow
        self.action_row.add_suffix(self.file_chooser_button)

        # 关键：修改 suffix box 的对齐方式，使按钮高度自适应
        self._adjust_suffix_alignment()

        # 绑定按钮点击事件，打开文件选择器
        self.file_chooser_button.connect('clicked', self.on_button_clicked)

        # 保存对 main_window 的引用，以便文件选择器使用
        self.main_window_ref = self.main_window
        self.filename = None

        # 样式选择用 Adw.ToggleGroup 分段控件（libadwaita 的「Group with
        # Labels」）：档位全部平铺可见、单击即达，符合 HIG 对「少量互斥选项」
        # 的建议，比 Adw.ComboRow 少一次展开。
        #
        # 这里刻意不把 ToggleGroup 放进 Adw.ActionRow / boxed-list 卡片：
        # ToggleGroup 自带圆角背景，再套一层卡片就成了「框里再套一个框」。
        # 直接把 ToggleGroup 作为 PreferencesGroup 的子级即可——按 GIR 文档，
        # 非 Adw.PreferencesRow 的子级会被放到列表下方（不套卡片），
        # 于是只保留 PreferencesGroup 的段落标题（heading h4，和上方「Bibliography」
        # 卡片标题同款），分段控件只留自己那一层背景。
        self.style_group = Adw.PreferencesGroup(title=_('Standard Styles'))
        self.style_toggle_group = self._make_style_toggle_group(STYLE_OPTIONS)
        self.style_group.add(self.style_toggle_group)
        self.content.append(self.style_group)

        self.natbib_style_group = Adw.PreferencesGroup(title=_('Natbib Styles'))
        self.natbib_toggle_group = self._make_style_toggle_group(NATBIB_STYLE_OPTIONS)
        self.natbib_style_group.add(self.natbib_toggle_group)
        self.content.append(self.natbib_style_group)

        # 「natbib 样式」二进制选项：Adw.SwitchRow，外包 Adw.PreferencesGroup
        # 形成 boxed list（裸 SwitchRow 在列表外会渲染成无边框浮动行），
        # 与偏好设置页/文档向导的二进制选项行同款。
        natbib_group = Adw.PreferencesGroup()
        natbib_group.set_margin_top(18)
        self.natbib_option = Adw.SwitchRow(title=_('Show bibliography styles for the \'natbib\' package'))
        natbib_group.add(self.natbib_option)
        self.content.append(natbib_group)

        self.preview_stack_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.preview_stack_wrapper.set_margin_top(18)
        self.preview_stack_wrapper.set_margin_bottom(18)
        self.preview_stack_wrapper.set_halign(Gtk.Align.FILL)
        self.preview_stack_wrapper.set_valign(Gtk.Align.CENTER)
        self.preview_stack = Gtk.Stack()
        self.preview_stack_wrapper.append(self.preview_stack)
        self.content.append(self.preview_stack_wrapper)

        self.natbib_preview_stack_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.natbib_preview_stack_wrapper.set_margin_top(18)
        self.natbib_preview_stack_wrapper.set_margin_bottom(18)
        self.natbib_preview_stack_wrapper.set_halign(Gtk.Align.FILL)
        self.natbib_preview_stack_wrapper.set_valign(Gtk.Align.CENTER)
        self.natbib_preview_stack = Gtk.Stack()
        self.natbib_preview_stack_wrapper.append(self.natbib_preview_stack)
        self.content.append(self.natbib_preview_stack_wrapper)

        self.content.set_vexpand(True)
        self.topbox.append(self.content)

    def _make_style_toggle_group(self, options):
        """构建样式分段控件：每档一个带 label 的 Adw.Toggle。

        can_shrink=False 让每档保持自然宽度——宁可变宽也不把 "Apalike"
        省略成 "Apal…"。
        """
        group = Adw.ToggleGroup()
        group.set_valign(Gtk.Align.CENTER)
        # 撑满卡片行宽度并等分各档：空白让给档位，而不是全部挤在左侧。
        # can_shrink=False 保证标签永不省略（宁可变宽也不出现 "Apal…"）。
        group.set_hexpand(True)
        group.set_homogeneous(True)
        group.set_can_shrink(False)
        for style, label in options:
            # label 不翻译，理由见上方 STYLE_OPTIONS 的注释。这里若写成
            # _(label) 只会是「变量调用的翻译」，xgettext 不提取该 msgid，
            # 既拿不到翻译，又让人误以为这些串已进 pot。
            group.add(Adw.Toggle(name=style, label=label))
        return group

    def _adjust_suffix_alignment(self):
        """获取 ActionRow 内部的 suffix box，并设置其垂直对齐为居中，避免拉伸按钮"""
        # 防止按钮被拉伸：在按钮上设置不扩展
        self.file_chooser_button.set_hexpand(False)
        self.file_chooser_button.set_vexpand(False)

        # 同时设置按钮的垂直对齐为居中
        self.file_chooser_button.set_valign(Gtk.Align.CENTER)

    def set_file(self, filename):
        """设置选中的文件路径，更新按钮标签和提示文本"""
        self.filename = filename
        if filename:
            self.button_label.set_label(os.path.basename(filename))
            self.file_chooser_button.set_tooltip_text(filename)
        else:
            self.button_label.set_label(_('(None)'))
            self.file_chooser_button.set_tooltip_text('')

    def reset(self):
        """重置文件选择状态"""
        self.set_file(None)

    def add_filter(self, file_filter):
        """添加文件过滤器（兼容旧接口）"""
        if not hasattr(self, '_filters'):
            self._filters = []
        self._filters.append(file_filter)

    def on_button_clicked(self, button):
        """打开文件选择对话框"""
        dialog = Gtk.FileDialog()
        dialog.set_modal(True)
        dialog.set_title(_('Select a BibTeX File'))

        # 设置过滤器
        if hasattr(self, '_filters') and len(self._filters) > 0:
            store = Gio.ListStore.new(Gtk.FileFilter)
            for f in self._filters:
                store.append(f)
            dialog.set_filters(store)
            dialog.set_default_filter(self._filters[-1])

        dialog.open(self.main_window_ref, None, self._on_file_selected)

    def _on_file_selected(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            # 用户取消/关闭对话框
            pass
        except Exception:
            import traceback
            traceback.print_exc()
        else:
            if file is not None:
                self.filename = file.get_path()
                self.set_file(self.filename)
                # 触发 file-set 信号，通知外部
                self.file_chooser_button.emit('file-set')
