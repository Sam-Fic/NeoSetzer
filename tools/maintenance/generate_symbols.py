#!/usr/bin/env python3
# coding: utf-8
"""Regenerate sidebar symbol SVG assets and their XML size metadata.

Run from any directory with: ``python3 tools/maintenance/generate_symbols.py``.
Requires XeLaTeX, Poppler GI bindings, pdf2svg, Inkscape, and the ``file`` utility.
"""

# Copyright (C) 2017-present Robert Griesel
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

import gi

gi.require_version('Poppler', '0.18')
from gi.repository import Poppler

import xml.etree.ElementTree as ET
import subprocess, os, os.path
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SYMBOLS_DIR = REPOSITORY_ROOT / 'data' / 'resources' / 'symbols'
WORK_DIR = Path(__file__).resolve().parent
os.chdir(WORK_DIR)

folders = [
            'arrows',
            'greek_letters',
            'operators',
            'misc_math',
            'misc_text',
            'relations'
          ]

def generate_tex(border_h, border_v):
        tex_file = '''\\documentclass[12pt, border={ ''' + str(border_h) + 'pt ' + str(border_v) + '''pt }]{standalone}\n
\\usepackage[T1]{fontenc}\n
'''

        try: tex_file += '\\usepackage{' + attrib['package'] + '}\n'
        except KeyError: pass

        tex_file += '\\begin{document}\n'
        
        try: is_math = attrib['math']
        except KeyError: is_math = '0'
        try: command = attrib['gencommand']
        except KeyError: command = attrib['command']
        
        if is_math == '1':
            tex_file += '\\ensuremath{' + command + '}\n'
        else:
            tex_file += command + '\n'

        tex_file += '\\end{document}\n'

        return tex_file
    

for folder in folders:
    xml_path = SYMBOLS_DIR / f'{folder}.xml'
    target_dir = SYMBOLS_DIR / folder / 'hicolor' / 'scalable' / 'actions'
    target_dir.mkdir(parents=True, exist_ok=True)
    tree = ET.parse(xml_path)
    root = tree.getroot()

    for child in root:
        attrib = child.attrib

        # make pdf
        tex_file = generate_tex(1, 1)
        with open('./temp.tex', 'w') as f: f.write(tex_file)
        arguments = ['xelatex', 'temp.tex']
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.wait()
        process.kill()

        # get pdf size
        document = Poppler.Document.new_from_file('file:' + os.path.dirname(os.path.realpath(__file__)) + '/temp.pdf')
        page = document.get_page(0)
        size = page.get_size()

        # compute borders to make the image square
        if size.width > size.height:
            border_h = 1
            border_v = 1 + 1.004 * (size.width - size.height) / 2
        else:
            border_h = 1 + (size.height - size.width) / 2
            border_v = 1

        # make pdf again with adapted borders
        tex_file = generate_tex(border_h, border_v)
        with open('./temp.tex', 'w') as f: f.write(tex_file)
        arguments = ['xelatex', 'temp.tex']
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.wait()
        process.kill()

        # make svg
        target_svg = target_dir / f"sidebar-{attrib['file'][:-4]}-symbolic.svg"
        arguments = ['pdf2svg', 'temp.pdf', str(target_svg)]
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.wait()

        # get image size
        arguments = ['inkscape', '--export-filename=temp.png', str(target_svg)]
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.wait()
        process.communicate()
        process.kill()

        arguments = ['file', 'temp.png']
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.wait()
        output = process.communicate()
        output = output[0].decode('utf8')

        width_match = re.search('PNG image data, ([0-9]+) x ([0-9]+),', output)

        process.kill()
        child.set('original_width', width_match.group(1))
        child.set('original_height', width_match.group(2))
        tree.write(xml_path)

        # delete helper files
        os.remove('temp.tex')
        os.remove('temp.log')
        os.remove('temp.aux')
        os.remove('temp.pdf')
        os.remove('temp.png')


