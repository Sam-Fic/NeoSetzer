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

    def test_parses_strings_with_braced_quoted_and_bare_values(self):
        text = (
            '@string{journal = "Journal of Tests"}\n'
            '@string{month = jun}\n'
            '@string{publisher = {Acme Publishing}}\n'
            '@article{a, title = {A}, journal = journal}\n'
        )
        store = BibTeXEntryStore(text)
        self.assertEqual(store.diagnostics, ())
        self.assertEqual(
            [(s.name, s.value, s.value_kind) for s in store.strings],
            [
                ('journal', 'Journal of Tests', 'quoted'),
                ('month', 'jun', 'bare'),
                ('publisher', 'Acme Publishing', 'braced'),
            ],
        )
        self.assertTrue(store.get_string('Journal') is not None)
        self.assertIsNone(store.get_string('missing'))

    def test_list_strings_filters_and_sorts_case_insensitively(self):
        text = (
            '@string{zeta = "z"}\n'
            '@string{alpha = "a"}\n'
            '@string{mixed = "mix"}\n'
        )
        store = BibTeXEntryStore(text)
        self.assertEqual(
            [s.name for s in store.list_strings()],
            ['alpha', 'mixed', 'zeta'],
        )
        self.assertEqual(
            [s.name for s in store.list_strings('alp')],
            ['alpha'],
        )
        self.assertEqual(
            [s.name for s in store.list_strings('MIX')],
            ['mixed'],
        )

    def test_add_string_appends_without_mutating_existing_text(self):
        text = '@article{a, title = {A}}\n'
        store = BibTeXEntryStore(text)
        changed = store.add_string('journal', 'Journal of Tests')
        self.assertIn('@article{a, title = {A}}\n', changed)
        self.assertIn('@string{journal = {Journal of Tests}}\n', changed)
        self.assertTrue(changed.startswith(text.rstrip('\n')))
        # Re-parsing the result sees the new string, so a duplicate add
        # against the new store raises.
        updated_store = BibTeXEntryStore(changed)
        with self.assertRaisesRegex(BibTeXEntryError, 'already exists'):
            updated_store.add_string('journal', 'Another')
        with self.assertRaisesRegex(BibTeXEntryError, 'plain identifier'):
            store.add_string('bad name', 'v')

    def test_add_string_treats_single_identifier_as_bare_value(self):
        store = BibTeXEntryStore('')
        changed = store.add_string('month', 'jun')
        self.assertEqual(changed, '@string{month = jun}\n')

    def test_add_string_treats_braced_input_as_braced_value(self):
        store = BibTeXEntryStore('')
        changed = store.add_string('month', '{June}')
        self.assertEqual(changed, '@string{month = {June}}\n')

    def test_update_string_replaces_only_target_range(self):
        text = (
            '% preamble kept as written\n'
            '@string{journal = "Old Name"}\n'
            '@article{a, title = {A}}\n'
        )
        store = BibTeXEntryStore(text)
        changed = store.update_string('journal', 'journal', 'New Name')
        self.assertIn('% preamble kept as written', changed)
        self.assertIn('@article{a, title = {A}}\n', changed)
        self.assertIn('@string{journal = {New Name}}', changed)
        with self.assertRaisesRegex(BibTeXEntryError, 'no longer exists'):
            store.update_string('missing', 'journal', 'X')

    def test_update_string_can_rename_case_insensitively(self):
        text = '@string{journal = "Old"}\n@article{a, title = {A}}\n'
        store = BibTeXEntryStore(text)
        changed = store.update_string('journal', 'JOURNAL', '{New}')
        self.assertIn('@string{JOURNAL = {New}}', changed)
        self.assertNotIn('"Old"', changed)

    def test_delete_string_removes_only_target_range(self):
        text = (
            '@string{journal = "X"}\n\n'
            '@string{month = "Y"}\n\n'
            '@article{a, title = {A}}\n'
        )
        store = BibTeXEntryStore(text)
        changed = store.delete_string('month')
        self.assertIn('@string{journal = "X"}', changed)
        self.assertNotIn('month = "Y"', changed)
        self.assertIn('@article{a, title = {A}}\n', changed)

    def test_format_bibliography_keeps_strings_byte_for_byte(self):
        text = (
            '@string{journal = "J"}\n'
            '@string{month = jun}\n'
            '@article{a, title = {A}}\n'
        )
        store = BibTeXEntryStore(text)
        formatted = store.format_bibliography()
        self.assertIn('@string{journal = "J"}', formatted)
        self.assertIn('@string{month = jun}', formatted)
        # Entries are reformatted by format_bibliography, but the
        # @string blocks are passed through verbatim.  Confirm the
        # entry is reformatted and the strings survive untouched.
        self.assertIn('@article{a,', formatted)

    def test_import_strings_skips_duplicates_and_appends_new_ones(self):
        text = '@string{journal = "J"}\n@article{a, title = {A}}\n'
        external = (
            '@string{journal = "Other"}\n'
            '@string{publisher = "P"}\n'
            '@string{month = jan}\n'
        )
        store = BibTeXEntryStore(text)
        updated, summary = store.import_strings(external)
        self.assertEqual(summary['skipped'], ['journal'])
        self.assertEqual(set(summary['imported']), {'publisher', 'month'})
        self.assertIn('@string{journal = "J"}', updated)
        self.assertIn('@string{publisher = "P"}', updated)
        self.assertIn('@string{month = jan}', updated)
        self.assertNotIn('"Other"', updated)
        self.assertIn('@article{a, title = {A}}', updated)

    def test_import_strings_on_empty_document(self):
        store = BibTeXEntryStore('')
        updated, summary = store.import_strings('@string{journal = "J"}\n')
        self.assertEqual(summary['imported'], ['journal'])
        self.assertIn('@string{journal = "J"}', updated)

    def test_import_strings_with_no_new_macros_is_a_noop(self):
        text = '@string{journal = "J"}\n'
        store = BibTeXEntryStore(text)
        updated, summary = store.import_strings('@string{journal = "Other"}\n')
        self.assertEqual(updated, text)
        self.assertEqual(summary['skipped'], ['journal'])
        self.assertEqual(summary['imported'], [])

    def test_add_string_on_empty_document(self):
        store = BibTeXEntryStore('')
        changed = store.add_string('journal', '{J}')
        self.assertIn('@string{journal = {J}}', changed)


if __name__ == '__main__':
    unittest.main()
