#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.document.bibtex.entry_store import (
    BibTeXEntryError,
    BibTeXEntryStore,
    render_entry,
)


class BibTeXEntryStoreTest(unittest.TestCase):

    def setUp(self):
        self.text = (
            '% Keep this heading exactly as written.\n'
            '@string{journal = "Journal of Tests"}\n\n'
            '@article{doe2024,\n'
            '  author = {Doe, Jane and Roe, Richard},\n'
            '  title = {A {Nested} \\emph{LaTeX} Title},\n'
            '  journal = journal,\n'
            '  year = {2024},\n'
            '  note = "A quoted value with \\"quote\\""\n'
            '}\n\n'
            '@book{smith2020,\n'
            '  title = {Book Title},\n'
            '  year = {2020}\n'
            '}\n'
            '% Keep this footer exactly as written.\n'
        )

    def test_lists_and_searches_structured_entries(self):
        store = BibTeXEntryStore(self.text)
        self.assertEqual(store.diagnostics, ())
        self.assertEqual([entry.key for entry in store.entries], ['doe2024', 'smith2020'])
        entry = store.get_entry('doe2024')
        self.assertEqual(entry.entry_type, 'article')
        self.assertEqual(entry.get('title'), r'A {Nested} \emph{LaTeX} Title')
        self.assertEqual(entry.get('note'), 'A quoted value with \\"quote\\"')
        self.assertEqual([entry.key for entry in store.list_entries('RICHARD')], ['doe2024'])
        self.assertEqual([entry.key for entry in store.list_entries('book')], ['smith2020'])

    def test_search_supports_deterministic_field_sorting(self):
        store = BibTeXEntryStore(self.text)
        self.assertEqual([entry.key for entry in store.list_entries(sort_by='key')], ['doe2024', 'smith2020'])
        self.assertEqual([entry.key for entry in store.list_entries(sort_by='title')], ['doe2024', 'smith2020'])
        self.assertEqual([entry.key for entry in store.list_entries(sort_by='year')], ['smith2020', 'doe2024'])
        with self.assertRaises(ValueError):
            store.list_entries(sort_by='unsupported')

    def test_update_replaces_only_target_entry_range(self):
        store = BibTeXEntryStore(self.text)
        changed = store.update_entry(
            'doe2024', 'article', 'doe2025',
            {'author': 'Doe, Jane', 'title': 'Revised', 'year': '2025'},
        )
        self.assertIn('% Keep this heading exactly as written.\n@string{journal = "Journal of Tests"}', changed)
        self.assertIn('@book{smith2020,\n  title = {Book Title},\n  year = {2020}\n}', changed)
        self.assertIn('% Keep this footer exactly as written.', changed)
        self.assertNotIn('doe2024', changed)
        self.assertIn('@article{doe2025,\n  author = {Doe, Jane},\n  title = {Revised},\n  year = {2025}\n}', changed)

    def test_delete_removes_only_selected_entry_and_adjacent_separator(self):
        changed = BibTeXEntryStore(self.text).delete_entry('doe2024')
        self.assertNotIn('doe2024', changed)
        self.assertIn('@string{journal = "Journal of Tests"}', changed)
        self.assertIn('@book{smith2020,', changed)
        self.assertIn('% Keep this footer exactly as written.', changed)

    def test_add_keeps_existing_text_and_rejects_duplicate_key(self):
        store = BibTeXEntryStore(self.text)
        changed = store.add_entry('misc', 'new-key', {'title': 'New item', 'note': 'Draft'})
        self.assertTrue(changed.startswith(self.text.rstrip('\n')))
        self.assertIn('@misc{new-key,\n  title = {New item},\n  note = {Draft}\n}', changed)
        with self.assertRaisesRegex(BibTeXEntryError, 'already exists'):
            store.add_entry('article', 'doe2024', {'title': 'Duplicate'})

    def test_validates_keys_types_fields_and_missing_entries(self):
        store = BibTeXEntryStore(self.text)
        for key in ('', 'two words', 'with,comma', 'with{brace}'):
            with self.assertRaises(BibTeXEntryError):
                store.add_entry('article', key, {'title': 'Invalid'})
        with self.assertRaises(BibTeXEntryError):
            store.add_entry('bad type', 'valid', {'title': 'Invalid'})
        with self.assertRaises(BibTeXEntryError):
            store.add_entry('article', 'valid', {'bad field': 'Invalid'})
        with self.assertRaisesRegex(BibTeXEntryError, 'no longer exists'):
            store.delete_entry('missing')

    def test_parenthesized_entries_and_crlf_are_supported(self):
        text = '@misc(test,\r\n  title = "Quoted"\r\n)\r\n'
        store = BibTeXEntryStore(text)
        self.assertEqual(store.entries[0].key, 'test')
        changed = store.update_entry('test', 'misc', 'test2', {'title': 'Updated'})
        self.assertIn('\r\n', changed)
        self.assertIn('@misc{test2,\r\n  title = {Updated}\r\n}', changed)

    def test_malformed_entries_report_diagnostics_without_unsafe_edit(self):
        text = ('@article{safe, title = {Safe}}\n'
                '@book{unfinished, title = {No end @article{not-real, title = {Fake}}\n')
        store = BibTeXEntryStore(text)
        self.assertEqual([entry.key for entry in store.entries], ['safe'])
        self.assertTrue(store.diagnostics)
        with self.assertRaisesRegex(BibTeXEntryError, 'no longer exists'):
            store.update_entry('unfinished', 'book', 'unfinished', {'title': 'No'})

    def test_duplicate_keys_are_reported_and_update_refuses_collision(self):
        text = '@article{same, title = {One}}\n@book{same, title = {Two}}\n'
        store = BibTeXEntryStore(text)
        self.assertTrue(any('more than once' in diagnostic for diagnostic in store.diagnostics))
        with self.assertRaisesRegex(BibTeXEntryError, 'ambiguous'):
            store.update_entry('same', 'article', 'same', {'title': 'Changed'})

    def test_render_entry_omits_empty_values_and_supports_custom_fields(self):
        self.assertEqual(
            render_entry('custom-type', 'item', {'title': 'Title', 'empty': '', 'extra': 'Value'}),
            '@custom-type{item,\n  title = {Title},\n  extra = {Value}\n}',
        )
        self.assertEqual(render_entry('misc', 'solo', {}), '@misc{solo}')

    def test_format_rewrites_entries_and_preserves_everything_else(self):
        store = BibTeXEntryStore(self.text)
        formatted = store.format_bibliography()
        expected = (
            '% Keep this heading exactly as written.\n'
            '@string{journal = "Journal of Tests"}\n\n'
            '@article{doe2024,\n'
            '  author  = {Doe, Jane and Roe, Richard},\n'
            '  title   = {A {Nested} \\emph{LaTeX} Title},\n'
            '  year    = {2024},\n'
            '  journal = journal,\n'
            '  note    = {A quoted value with \\"quote\\"}\n'
            '}\n\n'
            '@book{smith2020,\n'
            '  title = {Book Title},\n'
            '  year  = {2020}\n'
            '}\n'
            '% Keep this footer exactly as written.\n'
        )
        self.assertEqual(formatted, expected)

    def test_format_is_idempotent(self):
        store = BibTeXEntryStore(self.text)
        self.assertEqual(store.format_bibliography(), store.format_bibliography())

    def test_format_keeps_unparsable_blocks_byte_for_byte(self):
        text = ('@article{safe, title = {Safe}}\n'
                '@book{unfinished, title = {No end @article{not-real, title = {Fake}}\n')
        store = BibTeXEntryStore(text)
        formatted = store.format_bibliography()
        self.assertTrue(formatted.startswith('@article{safe,\n  title = {Safe}\n}\n'))
        self.assertIn(
            '@book{unfinished, title = {No end @article{not-real, title = {Fake}}\n',
            formatted,
        )

    def test_format_preserves_bare_macro_values_and_crlf(self):
        text = '@techreport(iea2019,\r\n  institution = "IEA",\r\n  month = jun,\r\n  volume = 12,\r\n)\r\n'
        store = BibTeXEntryStore(text)
        self.assertEqual(store.diagnostics, ())
        self.assertEqual(store.format_bibliography(),
                         '@techreport{iea2019,\r\n'
                         '  volume      = 12,\r\n'
                         '  month       = jun,\r\n'
                         '  institution = {IEA}\r\n'
                         '}\r\n')

    def test_format_renders_fieldless_entries_on_one_line(self):
        store = BibTeXEntryStore('@misc{solo,}\n')
        self.assertEqual([entry.key for entry in store.entries], ['solo'])
        self.assertEqual(store.format_bibliography(), '@misc{solo}\n')


if __name__ == '__main__':
    unittest.main()
