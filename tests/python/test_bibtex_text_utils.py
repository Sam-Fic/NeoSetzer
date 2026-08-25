#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.document.bibtex.text_utils import (
    GREEK_LETTERS,
    LATIN_ACCENTS,
    SPECIAL_CHARS,
    latex_to_unicode,
    protect_cases,
    unicode_to_latex,
)


class ProtectCasesTest(unittest.TestCase):

    def test_wraps_words_containing_capital_letters(self):
        self.assertEqual(protect_cases('NASA mission'), '{NASA} mission')
        self.assertEqual(protect_cases('LaTeX2e engine'), '{LaTeX2e} engine')

    def test_leaves_lowercase_only_text_unchanged(self):
        self.assertEqual(protect_cases('plain text'), 'plain text')
        self.assertEqual(protect_cases('because of'), 'because of')

    def test_is_idempotent(self):
        once = protect_cases('NASA and LaTeX')
        twice = protect_cases(once)
        self.assertEqual(once, twice)

    def test_does_not_double_wrap_existing_protection(self):
        self.assertEqual(protect_cases('{NASA}'), '{NASA}')
        self.assertEqual(protect_cases('a {NASA} b'), 'a {NASA} b')

    def test_does_not_protect_inside_existing_braces(self):
        # A value that already contains a nested brace should not be
        # re-wrapped, even when the inner text has capitals.
        self.assertEqual(
            protect_cases('{A Nested {NASA} story}'),
            '{A Nested {NASA} story}',
        )

    def test_skips_words_inside_quoted_regions(self):
        # "quoted NASA" should keep the capitals bare: the quotes
        # already protect against BibTeX lowercasing.
        self.assertEqual(protect_cases('"quoted NASA"'), '"quoted NASA"')

    def test_empty_string(self):
        self.assertEqual(protect_cases(''), '')


class UnicodeToLatexTest(unittest.TestCase):

    def test_replaces_latin_accents(self):
        self.assertIn(unicode_to_latex('é'), "\\'{e}")
        self.assertIn(unicode_to_latex('ü'), '\\"{u}')
        self.assertIn(unicode_to_latex('ñ'), '\\~{n}')

    def test_replaces_greek_letters(self):
        self.assertEqual(unicode_to_latex('α'), '\\alpha')
        self.assertEqual(unicode_to_latex('Ω'), '\\Omega')

    def test_replaces_special_characters(self):
        self.assertEqual(unicode_to_latex('&'), '\\&')
        self.assertEqual(unicode_to_latex('50%'), '50\\%')
        self.assertEqual(unicode_to_latex('a_b'), 'a\\_b')
        self.assertEqual(unicode_to_latex('a{b'), 'a\\{b')

    def test_passes_through_plain_ascii(self):
        self.assertEqual(unicode_to_latex('Hello world'), 'Hello world')

    def test_handles_mixed_content(self):
        self.assertEqual(
            unicode_to_latex('Café α 50%'),
            "Caf\\'{e} \\alpha 50\\%",
        )

    def test_empty_string(self):
        self.assertEqual(unicode_to_latex(''), '')

    def test_latin_table_covers_lower_and_upper_case(self):
        sources = {source for source, _ in LATIN_ACCENTS}
        self.assertIn('é', sources)
        self.assertIn('É', sources)
        self.assertIn('ñ', sources)

    def test_greek_table_covers_lower_and_upper_case(self):
        sources = {source for source, _ in GREEK_LETTERS}
        self.assertIn('α', sources)
        self.assertIn('Ω', sources)
        self.assertIn('π', sources)

    def test_special_table_includes_common_escapes(self):
        targets = {target for _, target in SPECIAL_CHARS}
        self.assertIn('\\&', targets)
        self.assertIn('\\%', targets)
        self.assertIn('\\$', targets)


class LatexToUnicodeTest(unittest.TestCase):

    def test_replaces_accent_commands(self):
        self.assertEqual(latex_to_unicode("\\'{e}"), 'é')
        self.assertEqual(latex_to_unicode('\\"{u}'), 'ü')
        self.assertEqual(latex_to_unicode('\\~{n}'), 'ñ')

    def test_replaces_greek_commands(self):
        self.assertEqual(latex_to_unicode('\\alpha'), 'α')
        self.assertEqual(latex_to_unicode('\\Omega'), 'Ω')

    def test_replaces_special_escapes(self):
        self.assertEqual(latex_to_unicode('\\&'), '&')
        self.assertEqual(latex_to_unicode('50\\%'), '50%')
        self.assertEqual(latex_to_unicode('a\\_b'), 'a_b')

    def test_passes_through_unknown_commands(self):
        self.assertEqual(latex_to_unicode('\\emph{foo}'), '\\emph{foo}')
        self.assertEqual(latex_to_unicode('\\frac{a}{b}'), '\\frac{a}{b}')

    def test_handles_ligatures(self):
        self.assertEqual(latex_to_unicode('\\ss{}'), 'ß')
        self.assertEqual(latex_to_unicode('\\ae{}'), 'æ')
        self.assertEqual(latex_to_unicode('\\OE{}'), 'Œ')

    def test_handles_mixed_content(self):
        self.assertEqual(
            latex_to_unicode("Caf\\'{e} \\alpha 50\\%"),
            'Café α 50%',
        )

    def test_empty_string(self):
        self.assertEqual(latex_to_unicode(''), '')


class RoundtripTest(unittest.TestCase):

    def test_roundtrip_is_stable(self):
        samples = [
            'Hello é',
            'Café Français',
            'α β γ',
            'Über naïve',
            '50% off',
            'a_b',
            'a{b}c',
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                forward = unicode_to_latex(sample)
                back = latex_to_unicode(forward)
                self.assertEqual(
                    back, sample,
                    msg=f'forward={forward!r} back={back!r}',
                )

    def test_roundtrip_for_brace_protected_text(self):
        # Once a word is brace-protected, the unicode form roundtrips
        # back to the protected form, never the bare lowercase.
        protected = protect_cases('NASA LaTeX')
        self.assertIn('{NASA}', protected)
        # The protected form survives both directions.
        self.assertEqual(latex_to_unicode(protected), protected)
        # Forward to LaTeX leaves the braces intact (no special chars
        # in the body), so the inverse also leaves them.
        forward = unicode_to_latex(protected)
        self.assertEqual(latex_to_unicode(forward), protected)


if __name__ == '__main__':
    unittest.main()
