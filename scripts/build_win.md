# Setzer Windows 打包标准流程

> 本文档供 AI 编程助手在协助构建和发布 Setzer 的 Windows 版时参考。
> 对应 Debian 打包流程见 [build_deb.md](build_deb.md)。

本流程用于从源码构建 Setzer 的 Windows 便携版（zip 目录树），可选再封装为
Inno Setup 安装程序。Windows 没有标准包管理器，采用
**DESTDIR 安装 + 复制 MSYS2 运行时 DLL** 的方式制作可独立运行的目录树。

环境前提：已安装 [MSYS2](https://www.msys2.org/)，且后续命令均在
**MSYS2 MINGW64** 终端中执行（不是 MSYS 或 UCRT64 终端）。

---

## W1. 前置依赖

在 MSYS2 MINGW64 终端中安装构建与运行时依赖（一次性）：

```bash
pacman -S --needed \
  mingw-w64-x86_64-meson mingw-w64-x86_64-ninja \
  mingw-w64-x86_64-gtk4 mingw-w64-x86_64-libadwaita \
  mingw-w64-x86_64-gtksourceview5 \
  mingw-w64-x86_64-poppler \
  mingw-w64-x86_64-python mingw-w64-x86_64-python-cairo \
  mingw-w64-x86_64-python-gobject \
  mingw-w64-x86_64-python-pip \
  gettext
pip install bibtexparser numpy
```

---

## W2. 配置与安装到临时目录

```bash
cd /path/to/Setzer
meson setup builddir
rm -rf /c/setzer_pkg
DESTDIR=/c/setzer_pkg meson install -C builddir
```

安装产物布局（`/c/setzer_pkg` 即 `C:\setzer_pkg`）：

```
mingw64/bin/setzer.bat                              # Windows 启动器
mingw64/bin/setzer                                  # Python 入口脚本
mingw64/lib/python3.x/site-packages/setzer/         # 应用模块
mingw64/share/Setzer/resources/                     # 静态资源
mingw64/share/locale/...                            # 翻译
```

> Windows 上 meson 自动跳过 `.desktop`/mime/metainfo/man 安装
> （`data/meson.build` 中有平台条件判断）。

---

## W3. 打包运行时 DLL（制作便携版）

将 MSYS2 的 `mingw64/` 目录下的关键运行时文件复制到打包目录，使最终
用户无需安装 MSYS2 即可运行：

```bash
PKGROOT=/c/setzer_pkg/mingw64
MSYS2ROOT=/mingw64  # MSYS2 安装路径

# 复制 GTK4 / GLib / Poppler / Python 等运行时 DLL 和数据
mkdir -p "$PKGROOT/bin" "$PKGROOT/lib" "$PKGROOT/share"
cp -r "$MSYS2ROOT/bin/"*.dll "$PKGROOT/bin/"
cp -r "$MSYS2ROOT/lib/girepository-*" "$PKGROOT/lib/"
cp -r "$MSYS2ROOT/lib/python3."* "$PKGROOT/lib/"
cp -r "$MSYS2ROOT/share/glib-2.0" "$PKGROOT/share/"
cp -r "$MSYS2ROOT/share/icons" "$PKGROOT/share/"
cp -r "$MSYS2ROOT/share/locale" "$PKGROOT/share/" 2>/dev/null || true

# 清理 __pycache__（与 deb 打包同理，避免跨 Python 版本 stale pyc）
find "$PKGROOT" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$PKGROOT" -name "*.pyc" -delete 2>/dev/null || true
```

---

## W4. 打包为 zip

```bash
cd /c/setzer_pkg
zip -r /path/to/Setzer/setzer_${NEWVER}_windows_x64.zip mingw64
```

最终用户解压后运行 `mingw64\bin\setzer.bat` 即可。

---

## W5.（可选）制作 Inno Setup 安装程序

用 [Inno Setup](https://jrsoftware.org/isinfo.php) 封装便携目录树，
生成带快捷方式、文件关联、卸载器的 `.exe` 安装程序。示例 `.iss` 脚本
结构（将 `{#NEWVER}` 替换为实际版本号）：

```iss
[Setup]
AppName=Setzer
AppVersion={#NEWVER}
DefaultDirName={pf}\Setzer
DefaultGroupName=Setzer
OutputBaseFilename=setzer_{#NEWVER}_windows_x64_setup

[Files]
Source: "C:\setzer_pkg\mingw64\*"; DestDir: "{app}\mingw64"; Flags: recursesubdirs

[Icons]
Name: "{group}\Setzer"; Filename: "{app}\mingw64\bin\setzer.bat"
Name: "{group}\Uninstall Setzer"; Filename: "{uninstallexe}"
```

---

## W6. 发布

将 `.zip` 或 `.exe` 安装程序上传到 GitHub Release，与 `.deb` 包并列。
版本号 `NEWVER` 取自 `meson.build` 顶部的 `version:` 字段，需与 git tag、
`CHANGELOG.md` 保持一致（详见 [build_deb.md](build_deb.md) 第 7 节）。

---

## 常见问题

- **`meson setup` 报 `Program 'msgfmt' not found`** → 安装 `gettext`
  （见 W1）。
- **Windows 上运行报 `ModuleNotFoundError: No module named 'gi'`** →
  未在 MSYS2 MINGW64 终端中运行，或未安装
  `mingw-w64-x86_64-python-gobject`。便携版需确保 `mingw64\bin` 下的
  GTK4/GLib DLL 与 Python 在同一目录树。
- **Windows 上 LaTeX 构建无输出 / 弹出控制台窗口** → 确认 LaTeX 引擎
  （如 `xelatex.exe`）在 `PATH` 中；v75+ 已用 `CREATE_NO_WINDOW` 抑制
  控制台弹出，若仍出现说明运行的是旧版本。
