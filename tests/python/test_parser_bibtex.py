#!/usr/bin/env python3
# coding: utf-8

import ast
from pathlib import Path
import unittest

from setzer.document.bibtex.entry_store import BibTeXEntryStore


class _Observable:
    def __init__(self):
        pass


def _load_parser_class():
    source_path = Path(__file__).parents[2] / 'setzer/document/parser/parser_bibtex.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'), filename=str(source_path))
    parser_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ParserBibTeX')
    namespace = {
        'Observable': _Observable,
        'BibTeXEntryStore': BibTeXEntryStore,
    }
    exec(compile(ast.Module(body=[parser_class], type_ignores=[]), str(source_path), 'exec'), namespace)
    return namespace['ParserBibTeX']


ParserBibTeX = _load_parser_class()


class ParserBibTeXTest(unittest.TestCase):

    def test_initial_parse_indexes_safe_complex_entries(self):
        parser = object.__new__(ParserBibTeX)
        parser.symbols = {}
        text = (
            '@article{nested-key, title = {A {Nested} Title}}\n'
            '@book{second2026, title = "Quoted"}\n'
            '@string{name = "Ignored"}\n'
        )
        parser.initial_parse(text)
        self.assertEqual(parser.text, text)
        self.assertEqual(parser.symbols['bibitems'], {'nested-key', 'second2026'})

    def test_incomplete_entry_does_not_leak_nested_fake_key(self):
        parser = object.__new__(ParserBibTeX)
        parser.symbols = {}
        parser.parse_symbols('@article{safe, title = {Safe}}\n@book{unfinished, note = @article{fake, title = {No}}\n')
        self.assertEqual(parser.symbols['bibitems'], {'safe'})


if __name__ == '__main__':
    unittest.main()
