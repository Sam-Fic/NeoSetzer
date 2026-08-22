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

    def test_dialog_does_not_lock_to_a_single_desktop_size(self):
        source = method_source(
            'setzer/dialogs/document_wizard/document_wizard_viewgtk.py',
            'DocumentWizardView')
        self.assertNotIn('set_content_width(', source)
        self.assertNotIn('set_content_height(', source)
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

    def test_document_class_chooser_uses_a_narrow_window_breakpoint(self):
        source = method_source(
            'setzer/dialogs/document_wizard/pages/page_document_class.py',
            'DocumentClassPageView')
        self.assertIn('Adw.BreakpointBin()', source)
        self.assertIn("Adw.BreakpointCondition.parse('max-width: 620px')", source)
        self.assertIn("self.class_chooser, 'orientation', Gtk.Orientation.VERTICAL", source)
        self.assertIn("self.list, 'width-request', -1", source)
        self.assertIn("self.preview_container, 'width-request', -1", source)
        self.assertIn('self.wrap_content(', source)


if __name__ == '__main__':
    unittest.main()
