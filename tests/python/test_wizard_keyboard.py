#!/usr/bin/env python3
# coding: utf-8

"""Static regression checks for keyboard-reachable wizard selection lists.

The production pages require GTK and a display server, so this test validates the
source-level contract that makes their standard ListBox keyboard behaviour
available: the lists stay focusable and each page hands focus to its primary
selection control on activation.
"""

import ast
import os
import unittest


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def load_class(path, class_name):
    tree = ast.parse(open(path, encoding='utf-8').read())
    return next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name)


def calls_method(function, receiver_attribute, method_name, required_value=None):
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != method_name:
            continue
        receiver = node.func.value
        is_direct_self_attribute = (
            isinstance(receiver, ast.Attribute)
            and isinstance(receiver.value, ast.Name)
            and receiver.value.id == 'self'
            and receiver.attr == receiver_attribute)
        is_view_attribute = (
            isinstance(receiver, ast.Attribute)
            and isinstance(receiver.value, ast.Attribute)
            and isinstance(receiver.value.value, ast.Name)
            and receiver.value.value.id == 'self'
            and receiver.value.attr == 'view'
            and receiver.attr == receiver_attribute)
        if not (is_direct_self_attribute or is_view_attribute):
            continue
        if required_value is None:
            return True
        if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == required_value:
            return True
    return False


class TestWizardKeyboardFocusContracts(unittest.TestCase):

    def assert_focus_contract(self, filename, page_class, view_class, primary_control):
        path = os.path.join(REPO, 'setzer', 'dialogs', 'document_wizard', 'pages', filename)
        page = load_class(path, page_class)
        view = load_class(path, view_class)
        activation = next(node for node in page.body
                          if isinstance(node, ast.FunctionDef) and node.name == 'on_activation')
        constructor = next(node for node in view.body
                           if isinstance(node, ast.FunctionDef) and node.name == '__init__')

        self.assertTrue(
            calls_method(activation, primary_control, 'grab_focus'),
            f'{page_class}.on_activation must focus {primary_control}')
        self.assertTrue(
            calls_method(constructor, primary_control, 'set_can_focus', True),
            f'{view_class} must keep {primary_control} focusable')
        self.assertFalse(
            calls_method(constructor, primary_control, 'set_can_focus', False),
            f'{view_class} must not disable {primary_control} keyboard focus')

    def test_document_class_list_is_keyboard_reachable(self):
        self.assert_focus_contract(
            'page_document_class.py', 'DocumentClassPage',
            'DocumentClassPageView', 'list')

    def test_beamer_theme_list_is_keyboard_reachable(self):
        self.assert_focus_contract(
            'page_beamer_settings.py', 'BeamerSettingsPage',
            'BeamerSettingsPageView', 'themes_list')


if __name__ == '__main__':
    unittest.main()
