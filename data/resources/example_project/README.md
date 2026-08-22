# NeoSetzer Example Project

NeoSetzer created this **user-owned copy** of its bundled example project. You can safely edit, save, build, rename, move, or delete this folder; updating NeoSetzer will not overwrite your changes.

## Start here

Open **`main.tex`** and build it with NeoSetzer. The project intentionally uses **PDFLaTeX** as its baseline so that a typical TeX Live installation can build it without a font-specific engine. NeoSetzer also supports other configured build systems when they are installed.

The source contains short, runnable LaTeX examples alongside practical notes about NeoSetzer. The table of contents and the Document Structure sidebar are useful ways to explore it.

## Project layout

```text
example_project/
├── main.tex
├── README.md
├── references.bib
├── chapters/
│   ├── 01-getting-started.tex
│   ├── 02-writing-and-navigation.tex
│   ├── 03-project-workflows.tex
│   ├── 04-tables-and-data.tex
│   ├── 05-feature-atlas.tex
│   └── appendix-structure.tex
└── data/
    └── example-table.csv
```

`main.tex` is the project root and includes the files in `chapters/`. Each child chapter starts with a relative `% !TEX root = ../main.tex` Magic Comment, so opening the child in NeoSetzer still builds the root document. The root also demonstrates a supported `% !TEX program = pdflatex` directive.

`references.bib` provides a real BibTeX citation for the project. Build enough passes for your selected builder to resolve it; `latexmk` can manage those passes automatically when configured. `data/example-table.csv` is deliberately a practice input for NeoSetzer's **Insert Table** dialog. It is not read automatically by LaTeX: use **Paste TSV/CSV** or **Import CSV/TSV File** in the dialog, then inspect and edit the LaTeX it inserts.

## Safe experimentation

Use **Save and Build** in NeoSetzer after making a small edit. If you want to keep a clean starting point, create another example project from the welcome screen or first-run tutorial; NeoSetzer creates a new numbered directory instead of overwriting an existing copy.

Suggested experiments are embedded in the source: open a child chapter directly, inspect the nested heading and TODO in Document Structure, import the CSV grid, follow a cross-reference, then build the project again. The appendix also demonstrates ordinary article appendix numbering and a literal subsection counter change. Finally, use `05-feature-atlas.tex` as a task-oriented directory for NeoSetzer's command discovery, editing aids, sidebars, build and preview tools, preferences, recovery behaviour, and optional external tools.
