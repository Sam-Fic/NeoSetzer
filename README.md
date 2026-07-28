# Setzer

[简体中文](README.zh-CN.md)

Simple yet full-featured LaTeX editor for the GNU/Linux desktop, written in Python with GTK.

> This is a fork of [Setzer](https://github.com/cvfosammmm/Setzer) by cvfosammmm.
> The original project site is <https://www.cvfosammmm.org/setzer/>, licensed under GPL-3.0-or-later.
> This fork is maintained at <https://github.com/Sam-Fic/Setzer>.

![Screenshot](data/screenshot.png)

Setzer is a LaTeX editor written in Python with GTK. I'm happy if you give it a try and provide feedback via the issue tracker here on GitHub, be it about design, code architecture, bugs, feature requests, ...

## Installation

This fork is **not** published on Flathub. There are two ways to get it:

1. **Build from source** (see below) — works on any GNU/Linux distribution with the dependencies available.
2. **Debian/Ubuntu package** — Prebuilt `.deb` packages are published in the [GitHub Releases](https://github.com/Sam-Fic/Setzer/releases) of this fork. Check there for the latest build.

## Running Setzer with Gnome Builder

To run Setzer with Gnome Builder just click the "Clone.." button on the start screen, paste in the url (https://github.com/Sam-Fic/Setzer.git), click on "Clone" again, wait for it to download and hit the play button. It will build Setzer and its dependencies and then launch it.

Warning: Building Setzer this way may take a long time.

## Running Setzer on Debian/Ubuntu

I develop Setzer on Ubuntu and that's what I tested it with.

1. Run the following command to install prerequisite packages:<br />
`apt-get install meson python3-gi gir1.2-gtk-4.0 gir1.2-gtksource-5 gir1.2-pango-1.0 gir1.2-poppler-0.18 gir1.2-webkit-6.0 gettext python3-cairo python3-gi-cairo python3-pexpect gir1.2-adw-1 python3-bibtexparser python3-willow python3-numpy gir1.2-xdp-1.0`

2. Clone Setzer repository from GitHub

3. cd to Setzer folder

4. Run meson: `meson setup builddir`<br />
Note: Some distributions may not include systemwide installations of Python modules which aren't installed from distribution packages. In this case, you want to install Setzer in your home directory with `meson setup builddir --prefix=~/.local`.

5. Install Setzer with: `ninja install -C builddir`<br />
Or run it locally: `./scripts/setzer.dev`

## Building your documents from within the app

To build your documents from within the app you have to install a LaTeX interpreter. For example if you want to build with XeLaTeX, on Ubuntu this can be installed like so:
`apt-get install texlive-xetex`

To specify a build command open the "Preferences" dialog and choose the command you want to use under "LaTeX Interpreter".

## Getting in touch

Development and discussion for this fork take place on GitHub at [https://github.com/Sam-Fic/Setzer](https://github.com/Sam-Fic/Setzer "project url").
For the original upstream project, see [https://github.com/cvfosammmm/setzer](https://github.com/cvfosammmm/setzer).

## Acknowledgements

Setzer draws some inspiration from other LaTeX editors. For example the symbols in the sidebar are mostly the same as in LaTeXila, though I continue to change / reorganize them. The autocomplete suggestions are mostly the same as in Texmaker. I took some icons from Gnome Builder. Syntax highlighting schemes are based on the Tango scheme in GtkSourceView and the Gnome Builder Scheme.

Parts of the user interface are modeled after [GNOME Text Editor](https://gitlab.gnome.org/GNOME/gnome-text-editor).

## License

Setzer is licensed under GPL version 3 or later. See the COPYING file for details.
