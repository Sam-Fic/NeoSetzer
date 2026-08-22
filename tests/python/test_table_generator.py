#!/usr/bin/env python3
# coding: utf-8

import ast
import os
from pathlib import Path
import unittest

from setzer.dialogs.insert_table.table_generator import (
    IMPORT_FORMAT_AUTO,
    IMPORT_FORMAT_CSV_COMMA,
    IMPORT_FORMAT_CSV_SEMICOLON,
    IMPORT_FORMAT_TSV,
    MAX_COLUMNS,
    CellMerge,
    ENVIRONMENT_LONGTABLE,
    MAX_CELL_MERGES,
    MAX_ROWS,
    STYLE_BOOKTABS,
    STYLE_PLAIN,
    TableImportError,
    TableSpec,
    merges_removed_by_resize,
    parse_table_text,
    replace_cell_merge,
    resize_cells,
    resize_merges,
)


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _method_calls(path, class_name, method_name):
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    class_node = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in class_node.body
                  if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return [node for node in ast.walk(method) if isinstance(node, ast.Call)]


class TableGeneratorTest(unittest.TestCase):

    def test_import_auto_prefers_tsv_and_normalizes_ragged_rows(self):
        imported = parse_table_text('Header A\tHeader B\nOne\tTwo\nThree\n', IMPORT_FORMAT_AUTO)

        self.assertEqual(imported.format_name, IMPORT_FORMAT_TSV)
        self.assertEqual(imported.rows, 3)
        self.assertEqual(imported.columns, 2)
        self.assertEqual(imported.cells, (
            ('Header A', 'Header B'),
            ('One', 'Two'),
            ('Three', ''),
        ))

    def test_import_csv_preserves_quotes_commas_and_embedded_newlines(self):
        imported = parse_table_text(
            'Name,Notes\n"Ada, Lovelace","First line\nSecond ""quoted"" line"\n',
            IMPORT_FORMAT_CSV_COMMA,
        )

        self.assertEqual(imported.cells, (
            ('Name', 'Notes'),
            ('Ada, Lovelace', 'First line\nSecond "quoted" line'),
        ))
        self.assertEqual(imported.format_name, IMPORT_FORMAT_CSV_COMMA)

    def test_import_supports_explicit_semicolon_and_trims_only_trailing_blank_records(self):
        imported = parse_table_text('A;B\n;\n\n', IMPORT_FORMAT_CSV_SEMICOLON)

        self.assertEqual(imported.cells, (('A', 'B'),))
        self.assertEqual(imported.rows, 1)
        self.assertEqual(imported.columns, 2)

    def test_import_rejects_empty_invalid_and_oversized_data(self):
        with self.assertRaises(TableImportError):
            parse_table_text('')
        with self.assertRaises(TableImportError):
            parse_table_text('"unterminated', IMPORT_FORMAT_CSV_COMMA)
        with self.assertRaises(TableImportError):
            parse_table_text('\n'.join('a' for index in range(MAX_ROWS + 1)))
        with self.assertRaises(TableImportError):
            parse_table_text('\t'.join(str(index) for index in range(MAX_COLUMNS + 1)), IMPORT_FORMAT_TSV)
        with self.assertRaises(TableImportError):
            parse_table_text('A,B', 'spreadsheet')

    def test_resize_cells_preserves_overlapping_values(self):
        cells = resize_cells((('A', 'B'), ('C', 'D')), 3, 3)
        self.assertEqual(cells, (
            ('A', 'B', ''),
            ('C', 'D', ''),
            ('', '', ''),
        ))
        self.assertEqual(resize_cells(cells, 1, 2), (('A', 'B'),))

    def test_default_spec_uses_left_then_centered_column_alignment(self):
        spec = TableSpec()
        self.assertEqual(spec.cells, (('', '', ''), ('', '', ''), ('', '', '')))
        self.assertEqual(spec.alignments, ('l', 'c', 'c'))
        self.assertEqual(spec.column_specification, 'lcc')

    def test_plain_table_with_metadata_renders_editable_latex(self):
        spec = TableSpec(
            rows=2,
            columns=2,
            cells=(('Method', 'Score'), ('Proposed', '$94.6\\%$')),
            alignments=('l', 'r'),
            caption='Results',
            label='tab:results',
        )

        self.assertEqual(spec.render(), '\n'.join((
            '\\begin{table}[htbp]',
            '\\centering',
            '\\caption{Results}',
            '\\label{tab:results}',
            '\\begin{tabular}{lr}',
            '\\hline',
            r'Method & Score \\',
            '\\hline',
            r'Proposed & $94.6\%$ \\',
            '\\hline',
            '\\end{tabular}',
            '\\end{table}',
        )))
        self.assertEqual(spec.required_packages, ())

    def test_booktabs_table_adds_only_header_midrule_and_package(self):
        spec = TableSpec(
            rows=3,
            columns=2,
            cells=(('Method', 'Score'), ('Baseline', '91.2\\%'), ('Proposed', '94.6\\%')),
            style=STYLE_BOOKTABS,
            header_row=True,
            use_table_environment=False,
        )

        self.assertEqual(spec.render(), '\n'.join((
            '\\begin{tabular}{lc}',
            '\\toprule',
            r'Method & Score \\',
            '\\midrule',
            r'Baseline & 91.2\% \\',
            r'Proposed & 94.6\% \\',
            '\\bottomrule',
            '\\end{tabular}',
        )))
        self.assertEqual(spec.required_packages, ('booktabs',))

    def test_longtable_with_booktabs_repeats_header_and_requests_packages(self):
        spec = TableSpec(
            rows=3,
            columns=2,
            cells=(('Method', 'Score'), ('Baseline', '91.2\\%'), ('Proposed', '94.6\\%')),
            style=STYLE_BOOKTABS,
            environment=ENVIRONMENT_LONGTABLE,
            use_table_environment=False,
            caption='Results',
            label='tab:results',
        )

        self.assertEqual(spec.render(), '\n'.join((
            '\\begin{longtable}{lc}',
            '\\caption{Results}\\label{tab:results} \\\\',
            '\\toprule',
            r'Method & Score \\',
            '\\midrule',
            '\\endfirsthead',
            '\\toprule',
            r'Method & Score \\',
            '\\midrule',
            '\\endhead',
            r'Baseline & 91.2\% \\',
            r'Proposed & 94.6\% \\',
            '\\bottomrule',
            '\\end{longtable}',
        )))
        self.assertEqual(spec.required_packages, ('booktabs', 'longtable'))
        self.assertTrue(spec.uses_repeated_header)

    def test_plain_longtable_can_skip_repeated_header(self):
        spec = TableSpec(
            rows=2,
            columns=1,
            cells=(('First',), ('Second',)),
            environment=ENVIRONMENT_LONGTABLE,
            use_table_environment=False,
            header_row=True,
            repeat_header=False,
        )

        self.assertEqual(spec.render(), '\n'.join((
            '\\begin{longtable}{l}',
            '\\hline',
            r'First \\',
            '\\hline',
            r'Second \\',
            '\\hline',
            '\\end{longtable}',
        )))
        self.assertNotIn('\\endfirsthead', spec.render())
        self.assertFalse(spec.uses_repeated_header)
        self.assertEqual(spec.required_packages, ('longtable',))

    def test_multicolumn_merges_anchor_content_and_skips_covered_cells(self):
        spec = TableSpec(
            rows=2,
            columns=3,
            cells=(('Methods', 'ignored', 'Score'), ('Baseline', 'N/A', '91.2\\%')),
            cell_merges=(CellMerge(0, 0, column_span=2),),
            use_table_environment=False,
        )

        self.assertEqual(spec.render(), '\n'.join((
            '\\begin{tabular}{lcc}',
            '\\hline',
            r'\multicolumn{2}{l}{Methods} & Score \\',
            '\\hline',
            r'Baseline & N/A & 91.2\% \\',
            '\\hline',
            '\\end{tabular}',
        )))
        self.assertEqual(spec.required_packages, ())

    def test_multirow_merges_add_package_and_avoid_blocked_rule_columns(self):
        spec = TableSpec(
            rows=3,
            columns=3,
            cells=(('Group', 'Method', 'Score'), ('ignored', 'Baseline', '91.2\\%'), ('Other', 'Proposed', '94.6\\%')),
            cell_merges=(CellMerge(0, 0, row_span=2, column_span=1),),
            use_table_environment=False,
        )

        self.assertEqual(spec.render(), '\n'.join((
            '\\begin{tabular}{lcc}',
            '\\hline',
            r'\multirow{2}{*}{Group} & Method & Score \\',
            '\\cline{2-3}',
            r' & Baseline & 91.2\% \\',
            '\\hline',
            r'Other & Proposed & 94.6\% \\',
            '\\hline',
            '\\end{tabular}',
        )))
        self.assertEqual(spec.required_packages, ('multirow',))

    def test_booktabs_multirow_uses_cmidrule_for_unblocked_columns(self):
        spec = TableSpec(
            rows=2,
            columns=3,
            cells=(('Group', 'Method', 'Score'), ('ignored', 'Baseline', '91.2\\%')),
            cell_merges=(CellMerge(0, 0, row_span=2, column_span=1),),
            style=STYLE_BOOKTABS,
            use_table_environment=False,
        )

        rendered = spec.render()
        self.assertIn('\\cmidrule(lr){2-3}', rendered)
        self.assertNotIn('\\midrule', rendered)
        self.assertEqual(spec.required_packages, ('booktabs', 'multirow'))

    def test_cell_merges_reject_overlap_bounds_and_repeated_header_rowspan(self):
        with self.assertRaises(ValueError):
            TableSpec(rows=2, columns=2, cell_merges=(CellMerge(0, 0, column_span=2), CellMerge(0, 1, row_span=2)))
        with self.assertRaises(ValueError):
            TableSpec(rows=2, columns=2, cell_merges=(CellMerge(1, 1, column_span=2),))
        with self.assertRaises(ValueError):
            TableSpec(rows=2, columns=2, cell_merges=(CellMerge(0, 0, 1, 1),))
        with self.assertRaises(ValueError):
            TableSpec(
                rows=3,
                columns=2,
                environment=ENVIRONMENT_LONGTABLE,
                use_table_environment=False,
                cell_merges=(CellMerge(0, 0, row_span=2),),
            )
        with self.assertRaises(ValueError):
            TableSpec(rows=5, columns=5, cell_merges=tuple(
                CellMerge(index // 4, (index % 4) * 2, column_span=2)
                for index in range(MAX_CELL_MERGES + 1)
            ))

    def test_resize_merges_drops_ranges_outside_new_dimensions(self):
        merges = (CellMerge(0, 0, column_span=2), CellMerge(1, 1, row_span=2))
        self.assertEqual(resize_merges(merges, 3, 3), merges)
        self.assertEqual(resize_merges(merges, 2, 3), (CellMerge(0, 0, column_span=2),))
        self.assertEqual(merges_removed_by_resize(merges, 2, 3), (CellMerge(1, 1, row_span=2),))

    def test_replace_cell_merge_validates_after_removing_edited_range(self):
        first = CellMerge(0, 0, column_span=2)
        second = CellMerge(1, 0, column_span=2)
        updated = replace_cell_merge((first, second), first, CellMerge(0, 1, column_span=2), 3, 3)

        self.assertEqual(updated, (CellMerge(0, 1, column_span=2), second))
        with self.assertRaises(ValueError):
            replace_cell_merge((first, second), first, CellMerge(1, 1, column_span=2), 3, 3)
        with self.assertRaises(ValueError):
            replace_cell_merge((first,), second, CellMerge(1, 0, column_span=2), 3, 3)

    def test_longtable_rejects_table_float_wrapper(self):
        with self.assertRaises(ValueError):
            TableSpec(environment=ENVIRONMENT_LONGTABLE)

    def test_forced_h_table_requests_float_package(self):
        forced_table = TableSpec(rows=1, columns=1, placement='H')
        forced_booktabs_table = TableSpec(rows=1, columns=1, placement='H', style=STYLE_BOOKTABS)
        bare_tabular = TableSpec(rows=1, columns=1, placement='H', use_table_environment=False)

        self.assertEqual(forced_table.required_packages, ('float',))
        self.assertEqual(forced_booktabs_table.required_packages, ('booktabs', 'float'))
        self.assertEqual(bare_tabular.required_packages, ())

    def test_booktabs_without_header_does_not_add_midrule(self):
        spec = TableSpec(
            rows=2,
            columns=1,
            cells=(('First',), ('Second',)),
            style=STYLE_BOOKTABS,
            header_row=False,
            use_table_environment=False,
        )
        self.assertNotIn('\\midrule', spec.render())

    def test_empty_caption_and_label_are_omitted(self):
        spec = TableSpec(rows=1, columns=1, caption='  ', label='  ')
        rendered = spec.render()
        self.assertNotIn('\\caption', rendered)
        self.assertNotIn('\\label', rendered)

    def test_invalid_dimensions_style_placement_and_alignment_are_rejected(self):
        with self.assertRaises(ValueError):
            TableSpec(rows=0)
        with self.assertRaises(ValueError):
            TableSpec(rows=MAX_ROWS + 1)
        with self.assertRaises(ValueError):
            TableSpec(columns=MAX_COLUMNS + 1)
        with self.assertRaises(ValueError):
            TableSpec(style='grid')
        with self.assertRaises(ValueError):
            TableSpec(environment='tabularx')
        with self.assertRaises(ValueError):
            TableSpec(placement='x')
        with self.assertRaises(ValueError):
            TableSpec(columns=2, alignments=('l',))
        with self.assertRaises(ValueError):
            TableSpec(alignments=('p', 'c', 'c'))

    def test_editor_integration_uses_one_table_dialog_action(self):
        actions_path = os.path.join(REPO, 'setzer/workspace/actions/actions.py')
        source = Path(actions_path).read_text(encoding='utf-8')
        self.assertIn("self.add_action('insert-table-dialog', self.start_insert_table_dialog, None)", source)
        self.assertIn("self.actions['insert-table-dialog'].set_enabled(document_active_is_latex)", source)
        start_calls = _method_calls(actions_path, 'Actions', 'start_insert_table_dialog')
        self.assertTrue(any(
            isinstance(call.func, ast.Attribute) and call.func.attr == 'open'
            and any(isinstance(argument, ast.Constant) and argument.value == 'insert_table'
                    for argument in ast.walk(call))
            for call in start_calls
        ))

        locator_source = Path(
            os.path.join(REPO, 'setzer/dialogs/dialog_locator.py')).read_text(encoding='utf-8')
        self.assertIn("dialogs['insert_table'] = InsertTableController(main_window)", locator_source)
        menu_source = Path(
            os.path.join(REPO, 'setzer/popovers/shortcutsbar/object_menu.py')).read_text(encoding='utf-8')
        self.assertIn("self.add_action_button('main', _('Table'), 'win.insert-table-dialog')", menu_source)
        shortcut_source = Path(
            os.path.join(REPO, 'setzer/keyboard_shortcuts/shortcut_controller_app.py')).read_text(encoding='utf-8')
        self.assertIn("self._register_configurable('insert_table_dialog'", shortcut_source)
        table_controller_source = Path(
            os.path.join(REPO, 'setzer/dialogs/insert_table/insert_table_controller.py')).read_text(encoding='utf-8')
        self.assertIn("self.view.copy_button.connect('clicked', self._on_copy)", table_controller_source)
        self.assertIn('parse_table_text', table_controller_source)
        self.assertIn("self.view.paste_data_button.connect('clicked', self._on_paste_data)", table_controller_source)
        self.assertIn("self.view.import_file_button.connect('clicked', self._on_import_file)", table_controller_source)
        self.assertIn("self.view.add_merge_button.connect('clicked', self._on_add_merge)", table_controller_source)
        self.assertIn('self.cell_merges = resize_merges(self.cell_merges, rows, columns)', table_controller_source)
        self.assertIn('replace_cell_merge', table_controller_source)
        self.assertIn('merges_removed_by_resize', table_controller_source)
        self.assertIn('cell_merges=self.cell_merges if cell_merges is None else cell_merges', table_controller_source)
        self.assertIn("self.document.add_packages(spec.required_packages)", table_controller_source)
        table_view_source = Path(
            os.path.join(REPO, 'setzer/dialogs/insert_table/insert_table_viewgtk.py')).read_text(encoding='utf-8')
        self.assertIn("self.merges_group.set_title(_('Merge Cells'))", table_view_source)
        self.assertIn("self.edit_merge_button = Gtk.Button.new_with_mnemonic(_('_Update Merge'))", table_view_source)
        self.assertIn("self.paste_data_button = Gtk.Button.new_with_mnemonic(_('_Paste TSV/CSV'))", table_view_source)
        self.assertIn("self.import_file_button = Gtk.Button.new_with_mnemonic(_('_Import CSV/TSV File'))", table_view_source)
        self.assertIn('def set_merge_coverage(self, merges):', table_view_source)
        self.assertIn("entry.set_sensitive(not covered)", table_view_source)

    def test_plain_style_constant_is_the_default(self):
        self.assertEqual(TableSpec().style, STYLE_PLAIN)


if __name__ == '__main__':
    unittest.main()
