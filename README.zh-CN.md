# NeoSetzer

<div align="center">
  <img src="data/org.cvfosammmm.Setzer.svg" alt="NeoSetzer" width="128" height="128">
</div>

[English](README.md)

---

一款简单但功能完整的 LaTeX 编辑器，支持 Linux、Windows 和 macOS，基于 Python 和 GTK 编写。（Setzer 的一个 fork。）

> 这是 [Setzer](https://github.com/cvfosammmm/Setzer) 的一个 fork，原作者为 cvfosammmm。
> 原项目官网 <https://www.cvfosammmm.org/setzer/>，基于 GPL-3.0-or-later 许可证。
> 本 fork 维护于 <https://github.com/Sam-Fic/NeoSetzer>。

![截图](data/screenshot.png)

NeoSetzer 是用 Python 和 GTK 编写的 LaTeX 编辑器。欢迎通过本仓库的 GitHub Issue 提供关于设计、代码架构、错误报告和功能请求的反馈。

在原项目的基础上，迁移大量组件至 Libadwaita，现代且美观。

全面优化的 UI/UX 设计，希望你能感受到我打磨的各处细节: )

## 平台支持

NeoSetzer 在 **Linux**、**Windows** 和 **macOS** 上使用同一套源码运行，无需平台分支或单独构建。

| 平台 | 状态 | 运行时栈 |
|------|------|----------|
| Linux（Debian/Ubuntu 24.04+、Fedora、Arch 等） | 完全支持 | 系统 GTK4 + libadwaita |
| Windows 10/11（x86_64） | 通过 MSYS2 支持 | 便携式 MSYS2 mingw-w64 GTK4 栈 |
| macOS Apple Silicon（ARM64） | 已支持 | 自包含 `.app` 发布包 |
| WSL（Windows Subsystem for Linux） | 作为 Linux 应用支持 | WSL 内的 Linux 发行版 |

> **关于 WebKitGTK：** 帮助面板内置浏览器（WebKitGTK 6.0）在 MSYS2 的 `mingw64` 中并未打包，因此无法在 Windows 上安装。当其不可用时 NeoSetzer 在运行时会自动探测（`HAS_WEBKIT`）并降级——搜索功能仍可用，仅应用内 HTML 渲染不可用。这是设计行为，不影响 LaTeX 编辑和 PDF 预览。

## 安装

本 fork **未**发布在 Flathub 上。获取方式：

1. **Windows 便携 zip**——本 fork 的 [GitHub Releases](https://github.com/Sam-Fic/NeoSetzer/releases) 中的 `setzer_<版本号>_windows_x64.zip`。解压到任意目录后运行 `mingw64\bin\setzer.bat` 即可，无需 MSYS2、无需安装程序。详见[使用便携 zip](#使用便携-zip)。
2. **Debian 系软件包**——预编译的 `.deb` 包已发布在本 fork 的 [GitHub Releases](https://github.com/Sam-Fic/NeoSetzer/releases) 中。请在那里查看最新构建版本。
3. **macOS Apple Silicon 应用包**——下载 `setzer_<版本号>_macos_arm64.zip` 并解压 `Setzer.app`；当前 Gatekeeper 与签名状态见 [macOS 打包说明](docs/packaging/macos.md)。
4. **从源码构建**（见下文）——适用于任何 Linux 发行版或 Windows（通过 MSYS2），只要依赖可用。

## 使用 GNOME Builder 运行 NeoSetzer

要使用 GNOME Builder 运行 NeoSetzer，只需在启动屏幕上点击“克隆”按钮，粘贴 `https://github.com/Sam-Fic/NeoSetzer.git`，等待克隆完成后按下运行按钮。它会构建 NeoSetzer 及其依赖项，然后启动应用。

警告：这种方式构建 NeoSetzer 可能需要较长时间。

## 在 Debian/Ubuntu 上运行 NeoSetzer

NeoSetzer 在 Ubuntu 上开发和测试。

> **支持的发行版：** NeoSetzer 需要 WebKitGTK 6.0（`gir1.2-webkit-6.0`），该包在 **Ubuntu 24.04（Noble）及以上**、**Debian 13（trixie）及以上** 中可用。在更旧的系统（如 Ubuntu 22.04、Debian 12）上不存在 `gir1.2-webkit-6.0` 软件包，因此对应的 `.deb` 无法安装。如果你使用的是较旧的发行版，请按下面的方式从源码构建——GTK4/WebKit 绑定是在运行时解析的。

1. 运行以下命令安装前置软件包：

   ```bash
   # 在 Linux 终端中执行
   apt-get install meson ninja-build python3-gi gir1.2-gtk-4.0 gir1.2-gtksource-5 gir1.2-pango-1.0 gir1.2-poppler-0.18 gir1.2-webkit-6.0 gettext python3-cairo python3-gi-cairo gir1.2-adw-1 python3-bibtexparser python3-numpy gir1.2-xdp-1.0
   ```

   > 注：`gir1.2-xdp-1.0` 仅用于 Linux/Flatpak 检测（即 libportal 的 GIR）。Windows 上不需要它（见下文说明）。

2. 从 GitHub 克隆 NeoSetzer 仓库：

   ```bash
   # 在 Linux 终端中执行
   git clone https://github.com/Sam-Fic/NeoSetzer.git
   ```

3. 进入 NeoSetzer 目录：

   ```bash
   # 在 Linux 终端中执行
   cd NeoSetzer
   ```

4. 运行 meson：

   ```bash
   # 在 Linux 终端中执行
   meson setup builddir
   ```

   > 注意：某些发行版可能不包含未从发行版软件包安装的 Python 模块的系统级安装。在这种情况下，你需要将 NeoSetzer 安装到主目录中，使用 `meson setup builddir --prefix=~/.local`。

5. 使用以下命令安装 NeoSetzer：

   ```bash
   # 在 Linux 终端中执行
   ninja install -C builddir
   ```

   或本地运行：

   ```bash
   # 在 Linux 终端中执行
   ./scripts/dev/setzer.dev
   ```

## 在 Windows 上运行 NeoSetzer

NeoSetzer 原生支持 Windows。GTK4 运行时栈由 **MSYS2** 提供（这是 Windows 上获取最新 GTK4 / libadwaita / GtkSourceView 5 / Poppler 二进制文件的唯一可靠来源）。

有两种使用方式：

- **便携 zip** —— 最省事，无需安装 MSYS2。见下文。
- **从源码构建** —— 适合开发或需要修改 NeoSetzer 的场景。见第 1 步及以后。

### 使用便携 zip

`setzer_<版本号>_windows_x64.zip` 是一个自包含的构建产物（约 128 MB，解压后约 349 MB），已打包 Python、GTK4 以及全部运行时依赖。无需安装任何东西，也不会写注册表。

1. 从 [GitHub Releases](https://github.com/Sam-Fic/NeoSetzer/releases) 页面下载 `setzer_<版本号>_windows_x64.zip`。

2. 解压到**任意目录**——U 盘、`D:\Apps\NeoSetzer`、桌面都可以，没有固定的安装路径要求。

   > 在资源管理器里右键 →「全部解压缩」即可。也可以用 PowerShell：
   >
   > ```powershell
   > Expand-Archive setzer_74_windows_x64.zip -DestinationPath D:\Apps\NeoSetzer
   > ```

3. 运行解压目录下的 **`mingw64\bin\setzer.bat`**——在资源管理器里双击，或从 cmd / PowerShell 调用。

   > **PowerShell 注意：** 运行时**不要加引号**，或者在前面加调用运算符（`& "…\setzer.bat"`）。只加引号的路径会被当作字符串回显，不会执行。

4. 想固定到任务栏或开始菜单，给 `setzer.bat` 创建一个快捷方式即可（右键 →「发送到」→「桌面快捷方式」）。

卸载时直接删除该文件夹即可。个人设置保存在 `%LOCALAPPDATA%\setzer`（即 `C:\Users\<你的用户名>\AppData\Local\setzer`），跨版本保留——想彻底清理的话把这个目录一并删掉。

> **还需要装 MSYS2 吗？** 不需要，所有依赖都已打包。另外**不要**把解压出来的 `mingw64\bin` 加进系统 `PATH`——如果你机器上同时装了 MSYS2，两套 GTK4 运行时会互相遮蔽，引发很难排查的 DLL 报错。
>
> **LaTeX 仍需单独安装。** zip 里只有编辑器，不含 LaTeX 发行版，参见下文「在 Windows 上安装 LaTeX 发行版」。

### 第 1 步 — 安装 MSYS2

从 <https://www.msys2.org/> 下载并安装 MSYS2。打开 **MSYS2 MINGW64** 终端（不是默认的 `ucrt64`/`clang64` 终端，除非你知道自己在做什么——`mingw64` 是经过测试的配置）。

### 第 2 步 — 安装依赖

在 MSYS2 MINGW64 终端中：

```bash
# 在 MSYS2 MINGW64 终端中执行
pacman -S --needed \
  mingw-w64-x86_64-meson mingw-w64-x86_64-ninja \
  mingw-w64-x86_64-gtk4 mingw-w64-x86_64-libadwaita \
  mingw-w64-x86_64-gtksourceview5 \
  mingw-w64-x86_64-poppler \
  mingw-w64-x86_64-python mingw-w64-x86_64-python-cairo \
  mingw-w64-x86_64-python-gobject \
  mingw-w64-x86_64-python-pip \
  mingw-w64-x86_64-python-numpy \
  gettext
```

> **不需要 `libportal`：** MSYS2 中**不存在** `mingw-w64-x86_64-libportal` 这个包——libportal 只在 `msys` 子系统中打包，不提供 `mingw64` 版本。NeoSetzer 仅在 Flatpak 检测时用到它（`Xdp`），且已被 `try/except` 包裹，因此在 Windows 上直接省略即可。

然后安装 pacman 未提供的 **纯 Python** 库。MSYS2 的 Python 是 externally-managed（PEP 668），必须加 `--break-system-packages` 参数：

```bash
# 在 MSYS2 MINGW64 终端中执行
python -m pip install --break-system-packages bibtexparser
```

> **`numpy` 要用 pacman 安装，而不是 pip。** MSYS2 的 MinGW Python 平台标签是 `mingw_x86_64_msvcrt_gnu`，与上游 `win_amd64` 的 wheel（包括 numpy 的）**不匹配**——`pip install numpy` 会回退到源码编译，要么失败、要么编译很久。凡是带 C 扩展的包（如 `numpy`、`scipy`、`pillow` 等）都应通过 pacman 以 `mingw-w64-x86_64-python-<name>` 安装。`bibtexparser` 是纯 Python，用 pip 即可。
>
> **务必使用 MinGW 的 Python，而不是 MSYS 的。** 请确认 `python` 解析到 `/mingw64/bin/python`（`python -c "import sys; print(sys.platform)"` 应输出 `win32`）。如果 `pip`/`python` 指向的是 `msys` 解释器，PyGObject 与通过 pacman 安装的 `numpy` 都会找不到。建议显式使用 `python -m pip …`。

### 第 3 步 — 克隆并配置

```bash
# 在 MSYS2 MINGW64 终端中执行
git clone https://github.com/Sam-Fic/NeoSetzer.git
cd NeoSetzer
meson setup builddir
```

### 第 4 步 — 运行（开发模式）

```bash
# 在 cmd / PowerShell 中执行（无需 MSYS2）
scripts\dev\setzer.dev.bat
```

`scripts\dev\setzer.dev.bat` 是推荐的 Windows 启动方式——它是一个轻量包装脚本：把源码根目录加入 `PYTHONPATH`（`setzer` 包并未安装到 `site-packages`），并把 `mingw64\bin` 加到 `PATH` 前面以便找到正确的 Python 与 GTK4 / libadwaita 的 DLL，然后运行 meson 生成的 `builddir\setzer_dev.py`。它可以从 cmd、PowerShell，或在资源管理器里双击运行，**无需 MSYS2 终端**。

> **PowerShell 注意：** 运行时**不要加引号**。加引号的路径（`"scripts\dev\setzer.dev.bat"`）会被当成字符串只回显、不会执行。

跨平台替代方式（在 MSYS2 MINGW64 终端里运行）——本 fork 正是用这一方式验证过构建：

```bash
# 在 MSYS2 MINGW64 终端中执行
python scripts/dev/setzer.dev
```

> 直接 `python builddir\setzer_dev.py` 在没有设置 `PYTHONPATH` 时会报 `ModuleNotFoundError: No module named 'setzer'`，请优先使用上面的 `.bat` 或 `python scripts/dev/setzer.dev`。

### 第 5 步 — 安装（可选）

```bash
# 在 MSYS2 MINGW64 终端中执行
ninja install -C builddir
```

这会将 `setzer.bat`（和 Python `setzer` 脚本）安装到 MSYS2 的 `bin/` 目录。安装后，你可以从任何 MSYS2 终端运行 `setzer` 启动，或将 `<MSYS2>\mingw64\bin` 加入系统 `PATH` 后从 cmd / PowerShell / Windows Terminal 运行 `setzer.bat`。

### 在 Windows 上安装 LaTeX 发行版

要在应用内构建文档，请安装以下之一：

- [MiKTeX](https://miktex.org/)（Windows 原生，推荐）
- [TeX Live](https://www.tug.org/texlive/)（跨平台）
- [Tectonic](https://tectonic-typesetting.github.io/)（单文件，自动下载依赖）

确保你选择的 LaTeX 引擎（`pdflatex`、`xelatex`、`lualatex` 或 `tectonic`）在 `PATH` 中，然后在"偏好设置"对话框的"LaTeX 解释器"下选择它。

## 在应用内构建文档

要使用应用内构建功能，你需要安装 LaTeX 解释器。例如，如果你想使用 XeLaTeX 构建，在 Ubuntu 上可以这样安装：
`apt-get install texlive-xetex`

要指定构建命令，请打开"偏好设置"对话框，在"LaTeX 解释器"下选择你想要的命令。

## 打包

### Debian/Ubuntu（`.deb`）

Debian 软件包与发布流程见 [docs/packaging/debian.md](docs/packaging/debian.md)。

### Windows（便携 zip / 安装程序）

Windows 便携包流程见 [docs/packaging/windows.md](docs/packaging/windows.md)，macOS 应用包流程见 [docs/packaging/macos.md](docs/packaging/macos.md)。

## 联系方式

本 fork 的开发和讨论在 GitHub 上进行，地址为 [https://github.com/Sam-Fic/NeoSetzer](https://github.com/Sam-Fic/NeoSetzer "项目地址")。
关于原上游项目，请参见 [https://github.com/cvfosammmm/setzer](https://github.com/cvfosammmm/setzer)。

## 致谢

NeoSetzer 从其他 LaTeX 编辑器中汲取了一些灵感。例如，侧边栏中的符号大多与 LaTeXila 中的相同，尽管我仍在不断更改/整理它们。自动补全建议大多与 Texmaker 相同。我从 Gnome Builder 取用了一些图标。语法高亮方案基于 GtkSourceView 中的 Tango 方案和 Gnome Builder 方案。

用户界面的部分设计参考了 [GNOME Text Editor](https://gitlab.gnome.org/GNOME/gnome-text-editor)。

## 许可证

NeoSetzer 基于 GPL v3 或更高版本许可证发布。详见 COPYING 文件。
