# Setzer

[简体中文](README.zh-CN.md)

Simple yet full-featured LaTeX editor for Linux and Windows, written in Python with GTK.

> This is a fork of [Setzer](https://github.com/cvfosammmm/Setzer) by cvfosammmm.
> The original project site is <https://www.cvfosammmm.org/setzer/>, licensed under GPL-3.0-or-later.
> This fork is maintained at <https://github.com/Sam-Fic/Setzer>.

![Screenshot](data/screenshot.png)

Setzer is a LaTeX editor written in Python with GTK. I'm happy if you give it a try and provide feedback via the issue tracker here on GitHub, be it about design, code architecture, bugs, feature requests, ...

## Platform Support

Setzer runs on both **Linux** and **Windows** from a single codebase. No platform fork or separate build is required.

| Platform | Status | Runtime stack |
|----------|--------|---------------|
| Linux (Debian/Ubuntu 24.04+, Fedora, Arch, ...) | Fully supported | System GTK4 + libadwaita |
| Windows 10/11 (x86_64) | Supported via MSYS2 | MSYS2 mingw-w64 GTK4 stack |
| WSL (Windows Subsystem for Linux) | Supported as Linux app | Linux distribution inside WSL |

> **WebKitGTK note:** The optional help-panel browser (WebKitGTK 6.0) is hard to obtain on Windows. When it is unavailable Setzer automatically falls back — search still works, only in-app HTML rendering is disabled. This is by design and does not affect LaTeX editing or PDF preview.

## Installation

This fork is **not** published on Flathub. Ways to get it:

1. **Build from source** (see below) — works on any Linux distribution or Windows (via MSYS2) with the dependencies available.
2. **Debian/Ubuntu package** — Prebuilt `.deb` packages are published in the [GitHub Releases](https://github.com/Sam-Fic/Setzer/releases) of this fork. Check there for the latest build.

## Running Setzer with Gnome Builder

To run Setzer with Gnome Builder just click the "Clone.." button on the start screen, paste in the url (https://github.com/Sam-Fic/Setzer.git), click on "Clone" again, wait for it to download and hit the play button. It will build Setzer and its dependencies and then launch it.

Warning: Building Setzer this way may take a long time.

## Running Setzer on Debian/Ubuntu

I develop Setzer on Ubuntu and that's what I tested it with.

> **Supported distributions:** Setzer requires WebKitGTK 6.0 (gir1.2-webkit-6.0), which is available on **Ubuntu 24.04 (Noble) or newer** and **Debian 13 (trixie) or newer**. On older releases (e.g. Ubuntu 22.04, Debian 12) the `gir1.2-webkit-6.0` package does not exist and the `.deb` cannot be installed there. If you are on an older distribution, build from source as described below — the GTK4/WebKit bindings are resolved at runtime.

1. Run the following command to install prerequisite packages:<br />
`apt-get install meson ninja-build python3-gi gir1.2-gtk-4.0 gir1.2-gtksource-5 gir1.2-pango-1.0 gir1.2-poppler-0.18 gir1.2-webkit-6.0 gettext python3-cairo python3-gi-cairo gir1.2-adw-1 python3-bibtexparser python3-numpy gir1.2-xdp-1.0`

2. Clone Setzer repository from GitHub

3. cd to Setzer folder

4. Run meson: `meson setup builddir`<br />
Note: Some distributions may not include systemwide installations of Python modules which aren't installed from distribution packages. In this case, you want to install Setzer in your home directory with `meson setup builddir --prefix=~/.local`.

5. Install Setzer with: `ninja install -C builddir`<br />
Or run it locally: `./scripts/setzer.dev`

## Running Setzer on Windows

Setzer supports Windows natively. The GTK4 runtime stack is provided by **MSYS2** (the only reliable source of up-to-date GTK4 / libadwaita / GtkSourceView 5 / Poppler binaries on Windows).

### Step 1 — Install MSYS2

Download and install MSYS2 from <https://www.msys2.org/>. Open the **MSYS2 MINGW64** shell (not the default `ucrt64`/`clang64` shell unless you know what you are doing — `mingw64` is the tested configuration).

### Step 2 — Install dependencies

In the MSYS2 MINGW64 shell:

```bash
pacman -S --needed \
  mingw-w64-x86_64-meson mingw-w64-x86_64-ninja \
  mingw-w64-x86_64-gtk4 mingw-w64-x86_64-libadwaita \
  mingw-w64-x86_64-gtksourceview5 \
  mingw-w64-x86_64-poppler \
  mingw-w64-x86_64-libportal \
  mingw-w64-x86_64-python mingw-w64-x86_64-python-cairo \
  mingw-w64-x86_64-python-gobject \
  mingw-w64-x86_64-python-pip \
  gettext
```

Then install the Python libraries that are not packaged by pacman. MSYS2's Python is externally managed (PEP 668), so the `--break-system-packages` flag is required:

```bash
pip install --break-system-packages bibtexparser numpy
```

### Step 3 — Clone and configure

```bash
git clone https://github.com/Sam-Fic/Setzer.git
cd Setzer
meson setup builddir
```

### Step 4 — Run (development mode)

```bash
scripts\setzer.dev.bat
```

`scripts\setzer.dev.bat` is the recommended Windows launcher — a thin wrapper that adds the source tree to `PYTHONPATH` (the `setzer` package is not installed into `site-packages`) and prepends `mingw64\bin` to `PATH` so the correct Python and the GTK4 / libadwaita DLLs are found, then runs the meson-built `builddir\setzer_dev.py`. It works from cmd, PowerShell, or a double-click in File Explorer, with no MSYS2 shell required.

> **PowerShell note:** run it *without* quotes. A quoted path (`"scripts\setzer.dev.bat"`) is treated as a string and is only echoed, not executed.

Cross-platform alternative (run from the MSYS2 MINGW64 shell):

```bash
python scripts/setzer.dev
```

> A bare `python builddir\setzer_dev.py` fails with `ModuleNotFoundError: No module named 'setzer'` unless `PYTHONPATH` is set first — prefer the `.bat` or `python scripts/setzer.dev`.

### Step 5 — Install (optional)

```bash
ninja install -C builddir
```

This installs `setzer.bat` (and the Python `setzer` script) into the MSYS2 `bin/` directory. After installation you can launch Setzer by running `setzer` from any MSYS2 shell, or by adding `<MSYS2>\mingw64\bin` to your system `PATH` and running `setzer.bat` from cmd / PowerShell / Windows Terminal.

### Installing a LaTeX distribution on Windows

To build documents from within the app, install one of:

- [MiKTeX](https://miktex.org/) (Windows-native, recommended)
- [TeX Live](https://www.tug.org/texlive/) (cross-platform)
- [Tectonic](https://tectonic-typesetting.github.io/) (single-binary, automatic dependency download)

Make sure the LaTeX engine you choose (`pdflatex`, `xelatex`, `lualatex`, or `tectonic`) is on your `PATH`, then pick it in the "Preferences" dialog under "LaTeX Interpreter".

## Building your documents from within the app

To build your documents from within the app you have to install a LaTeX interpreter. For example if you want to build with XeLaTeX, on Ubuntu this can be installed like so:
`apt-get install texlive-xetex`

To specify a build command open the "Preferences" dialog and choose the command you want to use under "LaTeX Interpreter".

## Packaging

### Debian/Ubuntu (`.deb`)

See [scripts/build_deb.md](scripts/build_deb.md) for the full Debian packaging workflow (version bump, CHANGELOG, build, release).

### Windows (portable zip / installer)

See [scripts/build_win.md](scripts/build_win.md) for the full Windows packaging workflow (dependency install, DESTDIR install, runtime DLL bundling, zip / Inno Setup installer, release).

## Getting in touch

Development and discussion for this fork take place on GitHub at [https://github.com/Sam-Fic/Setzer](https://github.com/Sam-Fic/Setzer "project url").
For the original upstream project, see [https://github.com/cvfosammmm/setzer](https://github.com/cvfosammmm/setzer).

## Acknowledgements

Setzer draws some inspiration from other LaTeX editors. For example the symbols in the sidebar are mostly the same as in LaTeXila, though I continue to change / reorganize them. The autocomplete suggestions are mostly the same as in Texmaker. I took some icons from Gnome Builder. Syntax highlighting schemes are based on the Tango scheme in GtkSourceView and the Gnome Builder Scheme.

Parts of the user interface are modeled after [GNOME Text Editor](https://gitlab.gnome.org/GNOME/gnome-text-editor).

## License

Setzer is licensed under GPL version 3 or later. See the COPYING file for details.
