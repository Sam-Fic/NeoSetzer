#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
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
# along with this program. If not, see <https://www.gnu.org/licenses/>.

'''LaTeX template definitions for context menu quick-insert/wrap actions.

Each entry is a dict with:
  - label:  raw English label string (localisation is applied at menu-build time)
  - cmd:    the LaTeX command (without backslash), or 'environment' for env wrappers
  - before: string inserted before selection / cursor
  - after:  string inserted after selection / cursor
  - mode:   'wrap' for insert-before-after, 'insert' for insert-symbol (template)

The '•' placeholder in before/after marks the text-insertion point when there is
no selection (consumed by insert-before-after as the caret target).

Note: this module intentionally does NOT call _() at module level.  gettext.install()
runs late in the app startup sequence (inside Application.activate) and imports
happens before that point; calling _() would crash with NameError.  Labels are
localised lazily when the menu items are built in rebuild_latex_section.
'''

# ---------------------------------------------------------------------------
# Wrap-in-command definitions — common LaTeX text-formatting commands
# ---------------------------------------------------------------------------

WRAP_COMMANDS = [
    {
        'label': 'Bold (\\textbf)',
        'cmd': 'textbf',
        'before': r'\textbf{',
        'after': '}',
        'mode': 'wrap',
    },
    {
        'label': 'Italic (\\textit)',
        'cmd': 'textit',
        'before': r'\textit{',
        'after': '}',
        'mode': 'wrap',
    },
    {
        'label': 'Underline (\\underline)',
        'cmd': 'underline',
        'before': r'\underline{',
        'after': '}',
        'mode': 'wrap',
    },
    {
        'label': 'Emphasis (\\emph)',
        'cmd': 'emph',
        'before': r'\emph{',
        'after': '}',
        'mode': 'wrap',
    },
    {
        'label': 'Sans Serif (\\textsf)',
        'cmd': 'textsf',
        'before': r'\textsf{',
        'after': '}',
        'mode': 'wrap',
    },
    {
        'label': 'Typewriter (\\texttt)',
        'cmd': 'texttt',
        'before': r'\texttt{',
        'after': '}',
        'mode': 'wrap',
    },
    {
        'label': 'Small Caps (\\textsc)',
        'cmd': 'textsc',
        'before': r'\textsc{',
        'after': '}',
        'mode': 'wrap',
    },
    {
        'label': 'Slanted (\\textsl)',
        'cmd': 'textsl',
        'before': r'\textsl{',
        'after': '}',
        'mode': 'wrap',
    },
]


# ---------------------------------------------------------------------------
# Wrap-in-environment definitions — common LaTeX environments
# ---------------------------------------------------------------------------

WRAP_ENVIRONMENTS = [
    # --- Generic environment ---
    {
        'label': 'Environment (\\begin{•} \\end{•})',
        'cmd': 'environment',
        'before': r'\begin{•}' + '\n\t',
        'after': '\n' + r'\end{•}',
        'mode': 'wrap',
    },
    {
        'label': 'Verbatim',
        'cmd': 'verbatim',
        'before': r'\begin{verbatim}' + '\n\t',
        'after': '\n' + r'\end{verbatim}',
        'mode': 'wrap',
    },

    # --- List environments ---
    {
        'label': 'Bulleted List (itemize)',
        'cmd': 'itemize',
        'before': r'\begin{itemize}' + '\n\t',
        'after': '\n' + r'\end{itemize}',
        'mode': 'wrap',
    },
    {
        'label': 'Numbered List (enumerate)',
        'cmd': 'enumerate',
        'before': r'\begin{enumerate}' + '\n\t',
        'after': '\n' + r'\end{enumerate}',
        'mode': 'wrap',
    },
    {
        'label': 'Description List (description)',
        'cmd': 'description',
        'before': r'\begin{description}' + '\n\t',
        'after': '\n' + r'\end{description}',
        'mode': 'wrap',
    },

    # --- Quote environments ---
    {
        'label': 'Quote (quote)',
        'cmd': 'quote',
        'before': r'\begin{quote}' + '\n\t',
        'after': '\n' + r'\end{quote}',
        'mode': 'wrap',
    },
    {
        'label': 'Quotation (quotation)',
        'cmd': 'quotation',
        'before': r'\begin{quotation}' + '\n\t',
        'after': '\n' + r'\end{quotation}',
        'mode': 'wrap',
    },

    # --- Alignment environments ---
    {
        'label': 'Centered (center)',
        'cmd': 'center',
        'before': r'\begin{center}' + '\n\t',
        'after': '\n' + r'\end{center}',
        'mode': 'wrap',
    },
    {
        'label': 'Left-aligned (flushleft)',
        'cmd': 'flushleft',
        'before': r'\begin{flushleft}' + '\n\t',
        'after': '\n' + r'\end{flushleft}',
        'mode': 'wrap',
    },
    {
        'label': 'Right-aligned (flushright)',
        'cmd': 'flushright',
        'before': r'\begin{flushright}' + '\n\t',
        'after': '\n' + r'\end{flushright}',
        'mode': 'wrap',
    },

    # --- Math environments ---
    {
        'label': 'Equation (equation)',
        'cmd': 'equation',
        'before': r'\begin{equation}' + '\n\t',
        'after': '\n' + r'\end{equation}',
        'mode': 'wrap',
    },
    {
        'label': 'Equation* (equation*)',
        'cmd': 'equation*',
        'before': r'\begin{equation*}' + '\n\t',
        'after': '\n' + r'\end{equation*}',
        'mode': 'wrap',
    },
    {
        'label': 'Align (align)',
        'cmd': 'align',
        'before': r'\begin{align}' + '\n\t',
        'after': '\n' + r'\end{align}',
        'mode': 'wrap',
    },
    {
        'label': 'Gather (gather)',
        'cmd': 'gather',
        'before': r'\begin{gather}' + '\n\t',
        'after': '\n' + r'\end{gather}',
        'mode': 'wrap',
    },
]


# ---------------------------------------------------------------------------
# Insert-template definitions — full LaTeX templates for common constructs
# ---------------------------------------------------------------------------

INSERT_TEMPLATES = [
    # --- Sectioning ---
    {
        'label': 'Part',
        'cmd': 'part',
        'template': r'\part{•}',
        'mode': 'insert',
    },
    {
        'label': 'Chapter',
        'cmd': 'chapter',
        'template': r'\chapter{•}',
        'mode': 'insert',
    },
    {
        'label': 'Section',
        'cmd': 'section',
        'template': r'\section{•}',
        'mode': 'insert',
    },
    {
        'label': 'Subsection',
        'cmd': 'subsection',
        'template': r'\subsection{•}',
        'mode': 'insert',
    },
    {
        'label': 'Paragraph',
        'cmd': 'paragraph',
        'template': r'\paragraph{•}',
        'mode': 'insert',
    },

    # --- Inline constructs ---
    {
        'label': 'Inline Math ($...$)',
        'cmd': 'inline_math',
        'template': r'$ • $',
        'mode': 'insert',
    },
    {
        'label': r'Display Math (\[...\])',
        'cmd': 'display_math',
        'template': r'\[ • \]',
        'mode': 'insert',
    },
    {
        'label': r'Footnote (\footnote)',
        'cmd': 'footnote',
        'template': r'\footnote{•}',
        'mode': 'insert',
    },
    {
        'label': r'Label (\label)',
        'cmd': 'label',
        'template': r'\label{•}',
        'mode': 'insert',
    },
    {
        'label': r'Reference (\ref)',
        'cmd': 'ref',
        'template': r'\ref{•}',
        'mode': 'insert',
    },
    {
        'label': r'Hyperlink (\href)',
        'cmd': 'href',
        'template': r'\href{•}{•}',
        'mode': 'insert',
    },

    # --- Graphics / Float ---
    {
        'label': 'Figure (with caption)',
        'cmd': 'figure',
        'template': (
            r'\begin{figure}[htbp]' + '\n'
            r'\centering' + '\n'
            r'\includegraphics[width=0.8\textwidth]{•}' + '\n'
            r'\caption{•}' + '\n'
            r'\label{fig:•}' + '\n'
            r'\end{figure}'
        ),
        'mode': 'insert',
    },
    {
        'label': 'Simple Figure',
        'cmd': 'figure_simple',
        'template': (
            r'\begin{figure}[htbp]' + '\n'
            r'\centering' + '\n'
            r'\includegraphics{•}' + '\n'
            r'\end{figure}'
        ),
        'mode': 'insert',
    },
    {
        'label': 'Table (with caption)',
        'cmd': 'table',
        'template': (
            r'\begin{table}[htbp]' + '\n'
            r'\centering' + '\n'
            r'\caption{•}' + '\n'
            r'\label{tab:•}' + '\n'
            r'\begin{tabular}{•}' + '\n'
            r'• & • \\' + '\n'
            r'\end{tabular}' + '\n'
            r'\end{table}'
        ),
        'mode': 'insert',
    },

    # --- Document Info ---
    {
        'label': r'Title (\title)',
        'cmd': 'title',
        'template': r'\title{•}',
        'mode': 'insert',
    },
    {
        'label': r'Author (\author)',
        'cmd': 'author',
        'template': r'\author{•}',
        'mode': 'insert',
    },
    {
        'label': r'Date (\date)',
        'cmd': 'date',
        'template': r'\date{•}',
        'mode': 'insert',
    },
    {
        'label': r'Table of Contents (\tableofcontents)',
        'cmd': 'toc',
        'template': r'\tableofcontents',
        'mode': 'insert',
    },
    {
        'label': r'Make Title (\maketitle)',
        'cmd': 'maketitle',
        'template': r'\maketitle',
        'mode': 'insert',
    },
]
