#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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

import setzer.dialogs.include_bibtex_file.include_bibtex_file_viewgtk as view
from setzer.app.service_locator import ServiceLocator

# pickle 仅用于迁移期兼容旧 settings（presets 字段旧版是 bytes）。
# settings.json 迁移完成后 presets 是 dict，isinstance(bytes) 分支不再触发。
import pickle
import os


class IncludeBibTeXFile(object):

    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = ServiceLocator.get_settings()
        self.styles = ['plain', 'abbrv', 'alpha', 'apalike', 'ieeetr']
        self.style_names = ['Plain', 'Abbrv', 'Alpha', 'Apalike', 'iEEEtr']
        self.natbib_styles = ['plainnat', 'abbrvnat', 'unsrtnat', 'achemso']
        self.natbib_style_names = ['Plainnat', 'Abbrvnat', 'Unsrtnat', 'Achemso']
        self.current_values = dict()

    def run(self, document):
        self.document = document

        self.init_current_values()
        self.view = view.IncludeBibTeXFileView(self.main_window)
        self.view.cancel_button.connect('clicked', self.on_cancel_button_clicked)
        self.view.include_button.connect('clicked', self.on_include_button_clicked)
        self.setup()

        # set_active(True) 在状态由默认 False 变为 True 时已会发射 'toggled'
        # 信号,从而触发 on_style_chosen / on_natbib_style_chosen 完成预览栈的
        # 可见子项初始化,无需再手动调用 toggled()(原代码重复触发会导致
        # 过渡方向判定为 SLIDE_RIGHT,产生轻微视觉瑕疵)。
        self.view.style_buttons[self.current_values['style']].set_active(True)
        self.view.natbib_style_buttons[self.current_values['natbib_style']].set_active(True)
        self.view.natbib_option.set_active(self.current_values['natbib_toggle'])
        self.update_style_chooser_visibility()

        self.view.include_button.set_sensitive(False)
        self.view.file_chooser_button.reset()

        self.view.present(self.main_window)

    def on_cancel_button_clicked(self, button):
        self.view.close()

    def on_include_button_clicked(self, button):
        self.insert_template()

        self.view.close()

    def init_current_values(self):
        self.current_values['style'] = 'plain'
        self.current_values['natbib_style'] = 'plainnat'
        self.current_values['natbib_toggle'] = False
        self.current_values['filename'] = ''
        presets = self.settings.get_value('app_include_bibtex_file_dialog', 'presets')
        # 迁移期兼容：旧版 presets 是 pickle.dumps 的 bytes；settings.json
        # 迁移完成后 _migrate_presets_bytes 已解为 dict。
        if isinstance(presets, (bytes, bytearray)):
            try:
                presets = pickle.loads(presets)
            except (pickle.UnpicklingError, EOFError, ValueError,
                    AttributeError, TypeError):
                presets = None
        if isinstance(presets, dict):
            try:
                style = presets['style']
                if style in self.styles:
                    self.current_values['style'] = style
            except KeyError: pass
            try:
                style = presets['natbib_style']
                if style in self.natbib_styles:
                    self.current_values['natbib_style'] = style
            except KeyError: pass
            try: self.current_values['natbib_toggle'] = presets['natbib_toggle']
            except KeyError: pass

    def setup(self):
        file_filter1 = Gtk.FileFilter()
        file_filter1.add_pattern('*.bib')
        file_filter1.set_name(_('BibTeX Files'))
        self.view.file_chooser_button.add_filter(file_filter1)

        first_button = None
        for name in self.style_names:
            style = name.lower()
            self.view.style_buttons[style] = Gtk.ToggleButton()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            box.append(Gtk.Label(label=name))
            box.set_margin_end(6)
            box.set_margin_start(4)
            self.view.style_buttons[style].set_child(box)
            if first_button != None:
                self.view.style_buttons[style].set_group(first_button)
            self.view.style_switcher.append(self.view.style_buttons[style])
            self.view.style_buttons[style].connect('toggled', self.on_style_chosen, style)
            if first_button == None: first_button = self.view.style_buttons[style]

            image = Gtk.Picture.new_for_filename(os.path.join(ServiceLocator.get_resources_path(), 'bibliography_styles', style + '.png'))
            image.set_can_shrink(False)
            self.view.preview_stack.add_named(image, style)

        first_button = None
        for name in self.natbib_style_names:
            style = name.lower()
            self.view.natbib_style_buttons[style] = Gtk.ToggleButton()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            box.append(Gtk.Label(label=name))
            box.set_margin_end(6)
            box.set_margin_start(4)
            self.view.natbib_style_buttons[style].set_child(box)
            if first_button != None:
                self.view.natbib_style_buttons[style].set_group(first_button)
            self.view.natbib_style_switcher.append(self.view.natbib_style_buttons[style])
            self.view.natbib_style_buttons[style].connect('toggled', self.on_natbib_style_chosen, style)
            if first_button == None: first_button = self.view.natbib_style_buttons[style]

            image = Gtk.Picture.new_for_filename(os.path.join(ServiceLocator.get_resources_path(), 'bibliography_styles', style + '.png'))
            image.set_can_shrink(False)
            self.view.natbib_preview_stack.add_named(image, style)

        self.view.file_chooser_button.connect('file-set', self.on_file_chosen)
        self.view.natbib_option.connect('toggled', self.on_natbib_toggled)

    def on_file_chosen(self, widget=None):
        self.view.include_button.set_sensitive(True)
        self.current_values['filename'] = self.view.file_chooser_button.get_filename()

    def on_natbib_toggled(self, togglebutton):
        self.update_style_chooser_visibility()

    def update_style_chooser_visibility(self):
        self.current_values['natbib_toggle'] = self.view.natbib_option.get_active()
        self.view.preview_stack_wrapper.set_visible(not self.view.natbib_option.get_active())
        self.view.style_switcher.set_visible(not self.view.natbib_option.get_active())
        self.view.natbib_preview_stack_wrapper.set_visible(self.view.natbib_option.get_active())
        self.view.natbib_style_switcher.set_visible(self.view.natbib_option.get_active())

    def on_natbib_style_chosen(self, button, style):
        if self.natbib_styles.index(style) > self.natbib_styles.index(self.current_values['natbib_style']):
            self.view.natbib_preview_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        else:
            self.view.natbib_preview_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
        self.view.natbib_preview_stack.set_visible_child_name(style)
        self.current_values['natbib_style'] = style

    def on_style_chosen(self, button, style):
        if self.styles.index(style) > self.styles.index(self.current_values['style']):
            self.view.preview_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        else:
            self.view.preview_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
        self.view.preview_stack.set_visible_child_name(style)
        self.current_values['style'] = style

    def get_display_filename(self):
        filename = self.current_values['filename']
        if filename:
            return os.path.splitext(os.path.basename(filename))[0]
        else:
            return ''

    def get_style(self):
        if self.current_values['natbib_toggle']:
            return self.current_values['natbib_style']
        else:
            return self.current_values['style']

    def insert_template(self):
        # 直接存 dict（JSON 兼容），不再 pickle.dumps。
        self.settings.set_value('app_include_bibtex_file_dialog', 'presets', self.current_values)

        self.document.insert_before_document_end('''\\bibliographystyle{''' + self.get_style() + '''}
\\bibliography{''' + self.get_display_filename() + '''}''')
        self.document.scroll_cursor_onscreen()


