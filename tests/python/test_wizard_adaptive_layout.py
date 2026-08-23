#!/usr/bin/env python3
# coding: utf-8

"""Static contracts for the document wizard's responsive layout."""

import ast
import os
import unittest


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def method_source(relative_path, class_name, method_name='__init__'):
    path = os.path.join(REPO, relative_path)
    tree = ast.parse(open(path, encoding='utf-8').read())
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


class TestWizardAdaptiveLayout(unittest.TestCase):

    def test_dialog_gets_a_comfortable_minimum_size(self):
        '''对话框有明确的初始/最小内容尺寸，但不锁死为单一桌面尺寸。'''
        source = method_source(
            'setzer/dialogs/document_wizard/document_wizard_viewgtk.py',
            'DocumentWizardView')
        self.assertIn('self.set_content_width(840)', source)
        self.assertIn('self.set_content_height(900)', source)
        self.assertNotIn('topbox.set_size_request(', source)

    def test_template_actions_are_grouped_in_a_menu(self):
        source = method_source(
            'setzer/dialogs/document_wizard/document_wizard_viewgtk.py',
            'DocumentWizardView')
        self.assertIn('Gtk.MenuButton()', source)
        self.assertIn('template_actions_box.append(self.save_template_button)', source)
        self.assertIn(
            'template_actions_box.append(self.save_document_template_button)', source)
        self.assertIn('self.headerbar.pack_start(self.template_actions_button)', source)
        self.assertNotIn('self.headerbar.pack_start(self.save_template_button)', source)
        self.assertNotIn(
            'self.headerbar.pack_start(self.save_document_template_button)', source)

    def test_document_class_chooser_keeps_simple_two_column_layout(self):
        '''文档类选择页保持简单双栏布局（列表 + 预览并排）；用 wrap_content
        做滚动 + Clamp 包裹以收缩窗口高度到 520，窄屏走 Clamp 收缩而非
        BreakpointBin 断点（标题不再作为 ListBoxRow 塞进列表）。'''
        source = method_source(
            'setzer/dialogs/document_wizard/pages/page_document_class.py',
            'DocumentClassPageView')
        self.assertIn(
            'Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)',
            source)
        self.assertIn('inner.append(self.class_groups_box)', source)
        self.assertIn('inner.append(self.preview_container)', source)
        self.assertIn('self.content.append(inner)', source)
        self.assertIn('self.append(self.wrap_content(', source)
        self.assertNotIn('Adw.BreakpointBin()', source)
        self.assertNotIn('class_chooser_breakpoint_bin', source)
        
    def test_group_titles_are_plain_labels_not_list_rows(self):
        '''分组标题是普通 Gtk.Label，不能作为 Gtk.ListBoxRow 塞进列表。'''
        source = method_source(
        'setzer/dialogs/document_wizard/pages/page_document_class.py',
        'DocumentClassPageView')
        self.assertIn('heading = Gtk.Label(label=group_title)', source)
        self.assertIn("heading.add_css_class('heading')", source)
        self.assertIn('self.class_groups_box.append(heading)', source)
        self.assertNotIn('Gtk.ListBoxRow()', source)
        self.assertIn('inner.append(self.class_groups_box)', source)

    def test_template_combos_explain_their_empty_state(self):
        '''两个模板下拉必须用 tooltip 说明：只有先保存过才会有选项。'''
        source = method_source(
            'setzer/dialogs/document_wizard/pages/page_document_class.py',
            'DocumentClassPageView')
        self.assertIn('self.templates_combo.set_tooltip_text(', source)
        self.assertIn('“Save as Preset”', source)
        self.assertIn('otherwise only “None” is listed.', source)
        self.assertIn(
            'self.document_templates_combo.set_tooltip_text(', source)
        self.assertIn('“Save Document Template”', source)
        self.assertIn('otherwise only “Use wizard settings” is listed.', source)

if __name__ == '__main__':
    unittest.main()
