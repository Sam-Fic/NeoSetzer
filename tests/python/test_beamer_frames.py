#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.document.parser.beamer_frames import extract_beamer_frame_titles


class BeamerFramesTest(unittest.TestCase):

    def test_extracts_title_from_frame_environment(self):
        frames = extract_beamer_frame_titles(
            '\\begin{frame}{Introduction}\n'
            'Content\n'
            '\\end{frame}\n'
        )
        self.assertEqual([(frame.offset, frame.title) for frame in frames], [(0, 'Introduction')])

    def test_extracts_frametitle_inside_untitled_frame(self):
        text = '\\begin{frame}\n\\frametitle{Method}\n\\end{frame}\n'
        frames = extract_beamer_frame_titles(text)
        self.assertEqual([(frame.offset, frame.title) for frame in frames], [(text.index('\\frametitle'), 'Method')])

    def test_frame_environment_title_suppresses_later_frametitle_duplicate(self):
        frames = extract_beamer_frame_titles(
            '\\begin{frame}{Overview}\n'
            '\\frametitle{Alternative title}\n'
            '\\end{frame}\n'
        )
        self.assertEqual([frame.title for frame in frames], ['Overview'])

    def test_supports_overlay_options_and_nested_title_markup(self):
        frames = extract_beamer_frame_titles(
            '\\begin{frame}<2->[fragile]{A {\\emph{highlight}}}\n'
            '\\end{frame}\n'
        )
        self.assertEqual([frame.title for frame in frames], [r'A {\emph{highlight}}'])

    def test_ignores_untitled_frames_and_frametitles_outside_frames(self):
        frames = extract_beamer_frame_titles(
            '\\frametitle{Preamble title}\n'
            '\\begin{frame}\nContent\n\\end{frame}\n'
        )
        self.assertEqual(frames, [])


if __name__ == '__main__':
    unittest.main()
