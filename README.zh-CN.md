# Setzer

[English](README.md)

一款简单但功能完整的 LaTeX 编辑器，专为 GNU/Linux 桌面端编写，基于 Python 和 GTK。

> 这是 [Setzer](https://github.com/cvfosammmm/Setzer) 的一个 fork，原作者为 cvfosammmm。
> 原项目官网 <https://www.cvfosammmm.org/setzer/>，基于 GPL-3.0-or-later 许可证。
> 本 fork 维护于 <https://github.com/Sam-Fic/Setzer>。

![截图](data/screenshot.png)

Setzer 是用 Python 和 GTK 编写的 LaTeX 编辑器。如果你愿意尝试并提供反馈，我很开心——通过 GitHub 上的 issue 即可，无论是关于设计、代码架构、错误报告、功能请求等等。

## 安装

本 fork **未**发布在 Flathub 上。有两种获取方式：

1. **从源码构建**（见下文）——适用于任何 GNU/Linux 发行版，只要依赖可用。
2. **Debian 系软件包**——预编译的 `.deb` 包已发布在本 fork 的 [GitHub Releases](https://github.com/Sam-Fic/Setzer/releases) 中。请在那里查看最新构建版本。

## 使用 Gnome Builder 运行 Setzer

要使用 Gnome Builder 运行 Setzer，只需在启动屏幕上点击"克隆.."按钮，粘贴 URL（ https://github.com/Sam-Fic/Setzer.git ），再次点击"克隆"，等待下载完成后按下运行按钮。它会构建 Setzer 及其依赖项，然后启动它。

警告：这种方式构建 Setzer 可能需要很长时间。

## 在 Debian/Ubuntu 上运行 Setzer

我在 Ubuntu 上开发 Setzer，并在此基础上进行了测试。

1. 运行以下命令安装前置软件包：<br />
`apt-get install meson python3-gi gir1.2-gtk-4.0 gir1.2-gtksource-5 gir1.2-pango-1.0 gir1.2-poppler-0.18 gir1.2-webkit-6.0 gettext python3-cairo python3-gi-cairo python3-pexpect gir1.2-adw-1 python3-bibtexparser python3-willow python3-numpy gir1.2-xdp-1.0`

2. 从 GitHub 克隆 Setzer 仓库

3. 进入 Setzer 目录

4. 运行 meson：`meson setup builddir`<br />
注意：某些发行版可能不包含未从发行版软件包安装的 Python 模块的系统级安装。在这种情况下，你需要将 Setzer 安装到主目录中，使用 `meson setup builddir --prefix=~/.local`。

5. 使用以下命令安装 Setzer：`ninja install -C builddir`<br />
或本地运行：`./scripts/setzer.dev`

## 在应用内构建文档

要使用应用内构建功能，你需要安装 LaTeX 解释器。例如，如果你想使用 XeLaTeX 构建，在 Ubuntu 上可以这样安装：
`apt-get install texlive-xetex`

要指定构建命令，请打开"偏好设置"对话框，在"LaTeX 解释器"下选择你想要的命令。

## 联系方式

本 fork 的开发和讨论在 GitHub 上进行，地址为 [https://github.com/Sam-Fic/Setzer](https://github.com/Sam-Fic/Setzer "项目地址")。
关于原上游项目，请参见 [https://github.com/cvfosammmm/setzer](https://github.com/cvfosammmm/setzer)。

## 致谢

Setzer 从其他 LaTeX 编辑器中汲取了一些灵感。例如，侧边栏中的符号大多与 LaTeXila 中的相同，尽管我仍在不断更改/整理它们。自动补全建议大多与 Texmaker 相同。我从 Gnome Builder 取用了一些图标。语法高亮方案基于 GtkSourceView 中的 Tango 方案和 Gnome Builder 方案。

用户界面的部分设计参考了 [GNOME Text Editor](https://gitlab.gnome.org/GNOME/gnome-text-editor)。

## 许可证

Setzer 基于 GPL v3 或更高版本许可证发布。详见 COPYING 文件。
