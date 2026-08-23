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
from gi.repository import GLib
from gi.repository import Gtk
from gi.repository import Adw

from setzer.dialogs.document_wizard.pages.page import Page, PageView
from setzer.dialogs.document_wizard import page_map
from setzer.app.service_locator import ServiceLocator
import setzer.widgets.async_svg.async_svg as async_svg

import os


class DocumentClassPage(Page):

    def __init__(self, current_values):
        self.current_values = current_values
        self.view = DocumentClassPageView()

    def observe_view(self):
        def row_selected(box, row, user_data=None):
            if row is None or not hasattr(row, 'document_class'):
                return
            document_class = row.document_class
            self.current_values['document_class'] = document_class
            self.view.preview_container.set_visible_child_name(document_class)

        def template_selected(combo, pspec):
            # 索引 0 为"不加载"；其余对应已保存模板。
            selected = combo.get_selected()
            if selected == 0 or selected == Gtk.INVALID_LIST_POSITION:
                return
            name = self.view.template_names[selected - 1]
            if self.controller.apply_template(name):
                # apply_template 已刷新各页控件；跳到通用设置页并刷新预览。
                self.controller.goto_page(page_map.GENERAL_PAGE_INDEX)

        def document_template_selected(combo, pspec):
            selected = combo.get_selected()
            if selected == 0 or selected == Gtk.INVALID_LIST_POSITION:
                self.controller.select_document_template(None)
                self.view.set_document_template_preview(None)
                self.view.delete_document_template_button.set_sensitive(False)
                return
            identifier = self.view.document_template_ids[selected - 1]
            if self.controller.select_document_template(identifier):
                self.view.set_document_template_preview(
                    self.controller.get_selected_document_template_preview())
                self.view.delete_document_template_button.set_sensitive(True)
            else:
                self.view.document_templates_combo.set_selected(0)

        def delete_document_template(button):
            selected = self.view.document_templates_combo.get_selected()
            if selected <= 0 or selected == Gtk.INVALID_LIST_POSITION:
                return
            identifier = self.view.document_template_ids[selected - 1]
            dialog = Adw.AlertDialog(
                heading=_('Delete document template?'),
                body=_('The saved template source will be permanently removed.'))
            dialog.add_response('cancel', _('Cancel'))
            dialog.add_response('delete', _('Delete'))
            dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response('cancel')
            dialog.set_close_response('cancel')

            def on_response(dialog, response):
                if response == 'delete' and self.controller.delete_document_template(identifier):
                    self.refresh_document_templates()

            dialog.connect('response', on_response)
            dialog.present(self.view.get_root())

        for group_list in self.view.group_lists:
            group_list.connect('row-selected', row_selected)
        self.view.templates_combo.connect('notify::selected', template_selected)
        self.view.document_templates_combo.connect('notify::selected', document_template_selected)
        self.view.delete_document_template_button.connect('clicked', delete_document_template)

    def load_presets(self, presets):
        try:
            row = self.view.list_rows[presets['document_class']]
        except (TypeError, KeyError):
            row = self.view.list_rows[self.current_values['document_class']]
        row.get_parent().select_row(row)

    def refresh_document_templates(self):
        if getattr(self, 'controller', None) is None:
            return
        self.view.set_document_templates(self.controller.get_document_templates())
        self.view.set_document_template_preview(
            self.controller.get_selected_document_template_preview())
        self.view.delete_document_template_button.set_sensitive(False)

    def on_activation(self):
        # 进入文档类页时刷新向导预设与用户源模板下拉。
        if getattr(self, 'controller', None) is not None:
            self.view.set_templates(list(self.controller.get_templates().keys()))
            self.refresh_document_templates()
        # 此列表决定后续页面和生成结果，必须是键盘流程中的首个控件。
        self.view.list.grab_focus()


class DocumentClassPageView(PageView):

    def __init__(self):
        PageView.__init__(self)

        self.headerbar_subtitle = _('Step') + ' 1: ' + _('Choose a document class')

        self.list_rows = dict()
        self.group_lists = list()
        self.group_headings = list()
        self.class_groups_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6)
        class_groups = [
            (_('Common document classes'), [
                ('article', _('Article')), ('report', _('Report')),
                ('book', _('Book')), ('letter', _('Letter')),
                ('beamer', _('Beamer')),]),
            (_('Advanced KOMA-Script classes'), [
                ('scrartcl', _('KOMA-Script Article (scrartcl)')),
                ('scrreprt', _('KOMA-Script Report (scrreprt)')),
                ('scrbook', _('KOMA-Script Book (scrbook)')),
                ('scrlttr2', _('KOMA-Script Letter (scrlttr2)')),]),
        ]
        for group_title, classes in class_groups:
            # 分组标题是普通的标题标签，而不是塞进 ListBox 的行——这样它才真正
            # 是“标题”，不会跟列表项在滚动 / 样式上混在一起。
            heading = Gtk.Label(label=group_title)
            heading.set_xalign(0)
            heading.add_css_class('heading')
            heading.set_margin_top(12)
            heading.set_margin_bottom(6)

            group_list = Gtk.ListBox()
            group_list.set_selection_mode(Gtk.SelectionMode.BROWSE)
            group_list.set_size_request(348, -1)
            group_list.set_can_focus(True)
            group_list.add_css_class('boxed-list')
            for document_class, title in classes:
                row = Adw.ActionRow()
                row.set_title(title)
                row.document_class = document_class
                self.list_rows[document_class] = row
                group_list.append(row)
            group_list.set_vexpand(False)

            self.group_headings.append(heading)
            self.group_lists.append(group_list)
            self.class_groups_box.append(heading)
            self.class_groups_box.append(group_list)

        # 保留首个分组列表作为主要可聚焦控件，供键盘导航 / grab_focus 契约使用。
        self.list = self.group_lists[0]
        self.list.set_can_focus(True)

        self.preview_container = Gtk.Stack()
        self.preview_container.set_size_request(366, -1)
        self.preview_data = list()
        self.preview_data.append({'name': 'article', 'image': 'article1.svg', 'text': _('<b>Article:</b>  For articles in scientific journals, term papers, handouts, short reports, …\n\nThis class on its own is pretty simplistic and is often used as a starting point for more custom layouts.')})
        self.preview_data.append({'name': 'book', 'image': 'book1.svg', 'text': _('<b>Book:</b>  For actual books containing many chapters and sections.')})
        self.preview_data.append({'name': 'report', 'image': 'report1.svg', 'text': _('<b>Report:</b>  For longer reports and articles containing more than one chapter, small books, thesis.')})
        self.preview_data.append({'name': 'letter', 'image': 'letter1.svg', 'text': _('<b>Letter:</b>  For writing letters.')})
        self.preview_data.append({'name': 'beamer', 'image': 'beamer1.svg', 'text': _('<b>Beamer:</b>  A class for making presentation slides with LaTeX.\n\nThere are many predefined presentation styles.')})
        # KOMA-Script 类复用对应标准类缩略图，仅文案不同（报告 #4）。
        self.preview_data.append({'name': 'scrartcl', 'image': 'article1.svg', 'text': _('<b>Scrartcl:</b>  KOMA-Script replacement for the article class. Adds many customizations and sensible defaults.')})
        self.preview_data.append({'name': 'scrreprt', 'image': 'report1.svg', 'text': _('<b>Scrreprt:</b>  KOMA-Script replacement for the report class.')})
        self.preview_data.append({'name': 'scrbook', 'image': 'book1.svg', 'text': _('<b>Scrbook:</b>  KOMA-Script replacement for the book class.')})
        self.preview_data.append({'name': 'scrlttr2', 'image': 'letter1.svg', 'text': _('<b>Scrlttr2:</b>  KOMA-Script letter class with advanced features for address, date, and subject layout.')})
        for item in self.preview_data:
            image = async_svg.AsyncSvg(os.path.join(ServiceLocator.get_resources_path(), 'document_wizard', item['image']), 374, 262)
            image.set_margin_bottom(6)

            label = Gtk.Label()
            label.set_markup(item['text'])
            label.set_xalign(0)
            label.set_wrap(True)
            label.set_margin_start(6)
            label.set_margin_end(6)

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.append(image)
            box.append(label)

            self.preview_container.add_named(box, item['name'])

        # 模板库（报告 #5）：下拉加载已保存的命名模板。
        self.group_templates = Adw.PreferencesGroup()
        self.group_templates.set_title(_('Templates'))
        self.templates_combo = Adw.ComboRow()
        self.templates_combo.set_title(_('Load template'))
        self.templates_combo.set_model(Gtk.StringList())  # 占位，on_activation 填充
        self.template_names = list()
        self.group_templates.add(self.templates_combo)

        # #205：用户源模板是完整 LaTeX 快照，不能与上面的向导设置预设混淆。
        self.group_document_templates = Adw.PreferencesGroup()
        self.group_document_templates.set_title(_('Document templates'))
        self.document_templates_combo = Adw.ComboRow()
        self.document_templates_combo.set_title(_('Use document template'))
        self.document_templates_combo.set_model(Gtk.StringList())
        self.document_template_ids = list()
        self.group_document_templates.add(self.document_templates_combo)
        self.document_template_preview = Gtk.TextView()
        self.document_template_preview.set_editable(False)
        self.document_template_preview.set_cursor_visible(False)
        self.document_template_preview.set_monospace(True)
        self.document_template_preview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.document_template_preview.set_top_margin(8)
        self.document_template_preview.set_bottom_margin(8)
        self.document_template_preview.set_left_margin(8)
        self.document_template_preview.set_right_margin(8)
        self.document_template_preview.add_css_class('view')
        preview_scroller = Gtk.ScrolledWindow()
        preview_scroller.set_hexpand(True)
        preview_scroller.set_margin_top(6)
        preview_scroller.set_margin_bottom(6)
        preview_scroller.set_min_content_height(110)
        preview_scroller.set_max_content_height(220)
        preview_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        preview_scroller.add_css_class('preview-card')
        preview_scroller.set_overflow(Gtk.Overflow.HIDDEN)
        preview_scroller.set_child(self.document_template_preview)
        self.group_document_templates.add(preview_scroller)
        self.delete_document_template_button = Gtk.Button.new_with_mnemonic(
            _('Delete selected document template'))
        self.delete_document_template_button.add_css_class('destructive-action')
        self.delete_document_template_button.set_sensitive(False)
        self.delete_document_template_button.set_margin_start(12)
        self.delete_document_template_button.set_margin_end(12)
        self.delete_document_template_button.set_margin_bottom(12)
        self.group_document_templates.add(self.delete_document_template_button)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        inner.append(self.class_groups_box)
        inner.append(self.preview_container)
        self.content.append(inner)
        self.content.append(self.group_templates)
        self.content.append(self.group_document_templates)

        # 把内容放入滚动容器（ScrolledWindow + Clamp），这样 Step 1 的高度也被
        # 收进和其它步骤一致的 520px 对话框高度，内容超长时滚动而不是撑大窗口。
        # Clamp 最大宽 760 让并排的文档类列表（348）+ 预览（366）+ 间距 24
        # （共 738）刚好在 840px 对话框里并排，窄屏则收缩/滚动。
        self.append(self.wrap_content(
            self.content, maximum_size=760, tightening_threshold=520))

    def set_document_templates(self, templates):
        '''Populate the source-template chooser with validated store metadata.'''
        self.document_template_ids = [template.identifier for template in templates]
        model = Gtk.StringList()
        model.append(_('Use wizard settings'))
        for template in templates:
            model.append(template.name)
        self.document_templates_combo.set_model(model)
        self.document_templates_combo.set_selected(0)

    def set_document_template_preview(self, preview):
        self.document_template_preview.get_buffer().set_text(
            preview or _('Select a saved LaTeX source template to preview it here.'))

    def set_templates(self, names):
        '''用已保存模板名填充下拉；索引 0 为"不加载"。'''
        self.template_names = list(names)
        model = Gtk.StringList()
        model.append(_('None'))
        for name in self.template_names:
            model.append(name)
        self.templates_combo.set_model(model)
        self.templates_combo.set_selected(0)
