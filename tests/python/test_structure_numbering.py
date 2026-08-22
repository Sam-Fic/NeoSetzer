#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.document.parser.structure_numbering import (
    SectioningCommand,
    SecnumDepthChange,
    calculate_structure_numbers,
    format_structure_title,
)


class StructureNumberingTest(unittest.TestCase):

    def test_article_sections_and_subsections_are_numbered(self):
        commands = (
            SectioningCommand(0, 'section'),
            SectioningCommand(20, 'subsection'),
            SectioningCommand(40, 'subsection'),
            SectioningCommand(60, 'section'),
        )

        self.assertEqual(calculate_structure_numbers(commands), {
            0: '1',
            20: '1.1',
            40: '1.2',
            60: '2',
        })

    def test_higher_level_resets_deeper_counters(self):
        commands = (
            SectioningCommand(0, 'chapter'),
            SectioningCommand(10, 'section'),
            SectioningCommand(20, 'subsection'),
            SectioningCommand(30, 'chapter'),
            SectioningCommand(40, 'section'),
        )

        self.assertEqual(calculate_structure_numbers(commands), {
            0: '1',
            10: '1.1',
            20: '1.1.1',
            30: '2',
            40: '2.1',
        })

    def test_starred_commands_do_not_number_or_change_later_counters(self):
        commands = (
            SectioningCommand(0, 'section'),
            SectioningCommand(10, 'subsection'),
            SectioningCommand(20, 'section', starred=True),
            SectioningCommand(30, 'subsection', starred=True),
            SectioningCommand(40, 'subsection'),
            SectioningCommand(50, 'section'),
        )

        self.assertEqual(calculate_structure_numbers(commands), {
            0: '1',
            10: '1.1',
            20: None,
            30: None,
            40: '1.2',
            50: '2',
        })

    def test_secnumdepth_hides_deeper_commands_without_advancing_counters(self):
        commands = (
            SectioningCommand(0, 'section'),
            SectioningCommand(20, 'subsection'),
            SectioningCommand(40, 'subsection'),
            SectioningCommand(60, 'section'),
            SectioningCommand(80, 'subsection'),
        )
        changes = (
            SecnumDepthChange(10, 1),
            SecnumDepthChange(70, 2),
        )

        self.assertEqual(calculate_structure_numbers(commands, changes), {
            0: '1',
            20: None,
            40: None,
            60: '2',
            80: '2.1',
        })

    def test_direct_subsection_avoids_leading_zero_for_navigation(self):
        self.assertEqual(calculate_structure_numbers((
            SectioningCommand(0, 'subsection'),
            SectioningCommand(10, 'subsubsection'),
        )), {0: '1', 10: '1.1'})

    def test_same_offset_secnumdepth_is_applied_before_sectioning_command(self):
        commands = (SectioningCommand(10, 'section'),)
        changes = (SecnumDepthChange(10, 0),)
        self.assertEqual(calculate_structure_numbers(commands, changes), {10: None})

    def test_structure_title_uses_number_only_when_present(self):
        self.assertEqual(format_structure_title('Scope', '1.2'), '1.2 Scope')
        self.assertEqual(format_structure_title('Acknowledgements', None), 'Acknowledgements')
        self.assertEqual(format_structure_title('Frame title', ''), 'Frame title')

    def test_invalid_commands_and_offsets_are_rejected(self):
        with self.assertRaises(ValueError):
            SectioningCommand(0, 'frame')
        with self.assertRaises(ValueError):
            SectioningCommand(-1, 'section')
        with self.assertRaises(ValueError):
            SecnumDepthChange(-1, 2)


if __name__ == '__main__':
    unittest.main()
