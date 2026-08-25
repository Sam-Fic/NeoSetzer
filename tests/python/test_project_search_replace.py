#!/usr/bin/env python3
# coding: utf-8

import os
import tempfile
import unittest

from setzer.project.search_replace import ProjectSearchReplace


class ProjectSearchReplaceTest(unittest.TestCase):

    def _write(self, root, relative, text):
        filename = os.path.join(root, relative)
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as file:
            file.write(text)
        return filename

    def test_searches_project_text_files_and_creates_preview(self):
        with tempfile.TemporaryDirectory() as project:
            main = self._write(project, 'main.tex', '\\input{chapter}\nTarget term\n')
            chapter = self._write(project, 'chapter.tex', 'target Term\n')
            self._write(project, 'notes.txt', 'target is not a project text extension')
            search = ProjectSearchReplace(main, project)
            matches = search.search('target')
            self.assertEqual([(os.path.basename(match.filename), match.line)
                              for match in matches], [('chapter.tex', 1), ('main.tex', 2)])
            plan = search.create_replacement_plan('target', 'replacement')
            self.assertEqual(plan.replacement_count, 2)
            self.assertEqual([os.path.basename(file.filename) for file in plan.files],
                             ['chapter.tex', 'main.tex'])
            changed = plan.apply()
            self.assertEqual(set(changed), {chapter, main})
            with open(chapter, encoding='utf-8') as file:
                self.assertIn('replacement', file.read())

    def test_respects_word_matching_and_open_buffer_blocks(self):
        with tempfile.TemporaryDirectory() as project:
            main = self._write(project, 'main.tex', 'cat category cat\n')
            search = ProjectSearchReplace(main, project)
            self.assertEqual(len(search.search('cat', whole_word=True)), 2)
            plan = search.create_replacement_plan('cat', 'dog',
                                                  whole_word=True,
                                                  blocked_files=(main,))
            self.assertEqual(plan.replacement_count, 0)
            self.assertEqual(plan.blocked_files, (main,))
            with self.assertRaises(ValueError):
                plan.apply()

    def test_replacement_refuses_changed_file_after_preview(self):
        with tempfile.TemporaryDirectory() as project:
            main = self._write(project, 'main.tex', 'alpha alpha\n')
            plan = ProjectSearchReplace(main, project).create_replacement_plan(
                'alpha', 'beta')
            self._write(project, 'main.tex', 'alpha changed after preview\n')
            with self.assertRaises(ValueError):
                plan.apply()
            with open(main, encoding='utf-8') as file:
                self.assertIn('changed after preview', file.read())

    def test_ignores_external_and_build_directories(self):
        with tempfile.TemporaryDirectory() as parent:
            project = os.path.join(parent, 'project')
            os.mkdir(project)
            main = self._write(project, 'main.tex', '\\input{../outside}\ntarget\n')
            self._write(project, 'build/generated.tex', 'target')
            outside = self._write(parent, 'outside.tex', 'target')
            matches = ProjectSearchReplace(main, project).search('target')
            self.assertEqual([match.filename for match in matches], [main])
            self.assertNotIn(outside, [match.filename for match in matches])


if __name__ == '__main__':
    unittest.main()
