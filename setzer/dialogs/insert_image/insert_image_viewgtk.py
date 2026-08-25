#!/usr/bin/env python3
# coding: utf-8

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
# along with this program at <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, Gio, Pango

from setzer.dialogs.helpers.dialog_viewgtk import DialogView


class InsertImageView(DialogView):

    # 浮动位置参数预设（含 float 包提供的 [H] 选项说明）
    # (key, msgid) — labels are translated at runtime in __init__,
    # because module-level _() calls are not allowed before gettext.install.
    PLACEMENT_OPTIONS = [
        ('htbp', 'htbp (here, top, bottom, page) — most flexible, default'),
        ('ht',   'ht (here, top) — common default'),
        ('h',    'h (here only)'),
        ('t',    't (top)'),
        ('b',    'b (bottom)'),
        ('p',    'p (separate float page)'),
        ('H',    'H (HERE, forced in place by float package)'),
        ('h!',   'h! (here, override restrictions with !)'),
    ]

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)
        self.set_content_width(560)
        self.set_content_height(680)

        # 工具栏
        self.headerbar.set_title_widget(Adw.WindowTitle(title=_('Insert Image')))
        self.headerbar.set_show_start_title_buttons(False)
        self.headerbar.set_show_end_title_buttons(False)

        self.cancel_button = Gtk.Button.new_with_mnemonic(_('_Cancel'))
        self.cancel_button.set_tooltip_text(_('Close the dialog without inserting anything'))
        self.headerbar.pack_start(self.cancel_button)

        self.save_defaults_button = Gtk.Button(icon_name='document-save-symbolic')
        self.save_defaults_button.set_tooltip_text(_('Store the current options to be used next time'))
        self.save_defaults_button.add_css_class('flat')
        self.save_defaults_button.set_can_focus(False)
        self.headerbar.pack_start(self.save_defaults_button)

        self.insert_button = Gtk.Button.new_with_mnemonic(_('_Insert'))
        self.insert_button.add_css_class('suggested-action')
        self.insert_button.set_sensitive(False)
        self.insert_button.set_tooltip_text(_('Generate the LaTeX code and insert it at the cursor'))
        self.headerbar.pack_end(self.insert_button)

        # 滚动内容
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_margin_top(14)
        content.set_margin_bottom(14)
        content.set_margin_start(14)
        content.set_margin_end(14)
        scrolled.set_child(content)
        self.topbox.append(scrolled)

        # ---- 来源组 ----
        src_group = Adw.PreferencesGroup()
        src_group.set_title(_('Image Source'))

        self.source_row = Adw.ComboRow()
        self.source_row.set_title(_('Source'))
        self.source_row.set_subtitle(_('Where the image comes from'))
        self.source_row.set_tooltip_text(_('Pick an image file or use the picture currently on the clipboard'))
        self.source_model = Gtk.StringList.new([_('From file…'), _('From clipboard (pasted)')])
        self.source_row.set_model(self.source_model)
        src_group.add(self.source_row)

        self.choose_row = Adw.ActionRow()
        self.choose_row.set_title(_('Image File'))
        self.choose_row.set_subtitle(_('No file chosen'))
        self.choose_row.set_tooltip_text(_('The selected image file, or "No file chosen" before selection'))
        self.choose_button = Gtk.Button.new_with_mnemonic(_('_Choose…'))
        self.choose_button.set_valign(Gtk.Align.CENTER)
        self.choose_button.set_tooltip_text(_('Open a file chooser to select an image'))
        self.choose_row.add_suffix(self.choose_button)
        src_group.add(self.choose_row)

        # 预览（仅在有图片时显示）
        self.preview = Gtk.Picture()
        self.preview.set_size_request(240, 160)
        self.preview.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.preview.add_css_class('card')
        self.preview.set_margin_top(6)
        self.preview.set_visible(False)
        preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        preview_box.set_halign(Gtk.Align.CENTER)
        preview_box.append(self.preview)
        src_group.add(preview_box)

        content.append(src_group)

        # ---- 文件保存组 ----
        save_group = Adw.PreferencesGroup()
        save_group.set_title(_('Save Location'))

        self.filename_row = Adw.EntryRow()
        self.filename_row.set_title(_('File name (without extension)'))
        self.filename_row.set_tooltip_text(_('Base name used when saving the image, without .png'))
        save_group.add(self.filename_row)

        self.subdir_row = Adw.EntryRow()
        self.subdir_row.set_title(_('Subfolder'))
        self.subdir_row.set_text('images')
        self.subdir_row.set_tooltip_text(_('Subfolder inside the document folder where the image is stored'))
        save_group.add(self.subdir_row)

        save_group.add(self._note(_('Image will be saved as PNG into "<document folder>/<subfolder>/<file name>.png".')))

        content.append(save_group)

        # ---- 排版组 ----
        layout_group = Adw.PreferencesGroup()
        layout_group.set_title(_('Layout'))

        self.placement_row = Adw.ComboRow()
        self.placement_row.set_title(_('Float placement'))
        self.placement_row.set_subtitle(_('e.g. [ht] — use "H" for forced in-place (needs float package)'))
        self.placement_row.set_tooltip_text(_('LaTeX float specifier controlling where the figure may be placed'))

        # 为下拉列表项设置自定义 factory，使每个选项都有完整文字的悬停气泡
        def _setup_list_item(factory, list_item):
            label = Gtk.Label()
            label.set_xalign(0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            list_item.set_child(label)

        def _bind_list_item(factory, list_item):
            child = list_item.get_child()
            if isinstance(child, Gtk.Label):
                text = list_item.get_item().get_string()
                child.set_label(text)
                child.set_tooltip_text(text)

        list_factory = Gtk.SignalListItemFactory()
        list_factory.connect('setup', _setup_list_item)
        list_factory.connect('bind', _bind_list_item)

        placement_labels = [_('htbp (here, top, bottom, page) — most flexible, default'),
                             _('ht (here, top) — common default'),
                             _('h (here only)'),
                             _('t (top)'),
                             _('b (bottom)'),
                             _('p (separate float page)'),
                             _('H (HERE, forced in place by float package)'),
                             _('h! (here, override restrictions with !)')]
        self.placement_row.set_model(Gtk.StringList.new(placement_labels))
        self.placement_row.set_list_factory(list_factory)
        self.placement_row.set_selected(1)  # 默认 ht
        layout_group.add(self.placement_row)

        self.scale_row = Adw.SpinRow()
        self.scale_row.set_title(_('Scale'))
        self.scale_row.set_subtitle(_('Relative size, 1.0 = original'))
        self.scale_row.set_tooltip_text(_('Multiplies the image size; 1.0 keeps the original dimensions'))
        self.scale_row.set_digits(2)
        adjustment_scale = Gtk.Adjustment(value=1.0, lower=0.05, upper=5.0, step_increment=0.05, page_increment=0.25)
        self.scale_row.set_adjustment(adjustment_scale)
        layout_group.add(self.scale_row)

        self.width_row = Adw.EntryRow()
        self.width_row.set_title(_('Width (optional, e.g. 0.8\\textwidth)'))
        self.width_row.set_tooltip_text(_('Override scaling with an explicit width such as 0.8\\textwidth'))
        layout_group.add(self.width_row)

        self.centered_switch = Adw.SwitchRow()
        self.centered_switch.set_title(_('Center the image'))
        self.centered_switch.set_subtitle(_('Wrap in \\begin{center}…\\end{center}'))
        self.centered_switch.set_tooltip_text(_('Center the image horizontally on the page'))
        self.centered_switch.set_active(True)
        layout_group.add(self.centered_switch)

        self.figure_switch = Adw.SwitchRow()
        self.figure_switch.set_title(_('Use figure environment'))
        self.figure_switch.set_subtitle(_('Floating wrapper with caption &amp; label'))
        self.figure_switch.set_tooltip_text(_('Wrap the image in a figure float with a caption and label'))
        self.figure_switch.set_active(True)
        layout_group.add(self.figure_switch)

        content.append(layout_group)

        # ---- 文本组 ----
        text_group = Adw.PreferencesGroup()
        text_group.set_title(_('Caption &amp; Label'))

        self.caption_row = Adw.EntryRow()
        self.caption_row.set_title(_('Caption'))
        self.caption_row.set_tooltip_text(_('Text shown below the image; leave empty for no caption'))
        self.caption_row.set_text(_('Caption'))
        text_group.add(self.caption_row)

        self.label_row = Adw.EntryRow()
        self.label_row.set_title(_('Label (for \\ref)'))
        self.label_row.set_tooltip_text(_('Identifier used with \\ref to cross-reference this figure'))
        self.label_row.set_text('fig:')
        text_group.add(self.label_row)

        text_group.add(self._note(_('Label is referenced with \\ref{label}. Leave "fig:" prefix or customize.')))

        content.append(text_group)

        # 内容已挂到 self.topbox（DialogView 的 ToolbarView 内容区）
        self._apply_row_tooltips()

    def _apply_row_tooltips(self):
        '''给所有 PreferenceRow 在缺少显式 tooltip 时，用其标题作为悬停气泡，
        以便标题被折叠/省略时仍能通过鼠标悬停查看完整文字。'''
        def walk(widget):
            if isinstance(widget, Adw.PreferencesRow) and not widget.get_tooltip_text():
                title = widget.get_title()
                if title:
                    widget.set_tooltip_text(title)
            child = widget.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()
        walk(self.topbox)

    def _note(self, text):
        label = Gtk.Label(label=text)
        label.set_wrap(True)
        label.set_xalign(0)
        label.add_css_class('dim-label')
        label.set_margin_top(4)
        label.set_margin_bottom(8)
        return label

    # 工具属性
    @property
    def source_is_clipboard(self):
        return self.source_row.get_selected() == 1

    def set_preview_texture(self, texture):
        if texture is not None:
            self.preview.set_paintable(texture)
            self.preview.set_visible(True)
            self.insert_button.set_sensitive(self._has_image())
        else:
            self.preview.set_paintable(None)

    def set_source_file(self, file_path):
        self.choose_row.set_subtitle(file_path)
        self.choose_button.set_label(_('Change…'))
        self.insert_button.set_sensitive(self._has_image())

    def set_filename(self, name):
        self.filename_row.set_text(name)

    def _has_image(self):
        # 有文件名即可插入；图片本身要么是文件要么是剪贴板纹理
        return len(self.filename_row.get_text().strip()) > 0

    def get_values(self):
        '''返回当前可持久化为默认值的选项。'''
        return {
            'subdir': self.subdir_row.get_text().strip() or 'images',
            'placement_index': self.placement_row.get_selected(),
            'scale': self.scale_row.get_value(),
            'width': self.width_row.get_text().strip(),
            'centered': self.centered_switch.get_active(),
            'figure': self.figure_switch.get_active(),
        }

    def apply_values(self, values):
        '''从已保存的默认值恢复选项（不影响文件名/图片来源等每次插入相关项）。'''
        if not isinstance(values, dict):
            return
        if 'subdir' in values:
            self.subdir_row.set_text(str(values['subdir']))
        if 'placement_index' in values:
            self.placement_row.set_selected(int(values['placement_index']))
        if 'scale' in values:
            self.scale_row.set_value(float(values['scale']))
        if 'width' in values:
            self.width_row.set_text(str(values['width']))
        if 'centered' in values:
            self.centered_switch.set_active(bool(values['centered']))
        if 'figure' in values:
            self.figure_switch.set_active(bool(values['figure']))
