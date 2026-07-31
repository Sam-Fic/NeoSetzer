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
  mingw-w64-x86_64-python-numpy \
  mingw-w64-x86_64-python-pip \
  gettext zip
pip install --break-system-packages bibtexparser
```

> **numpy 必须用 pacman 装，不能用 pip。** MSYS2 的 MinGW Python 平台 tag 是
> `mingw_x86_64_msvcrt_gnu`，与 PyPI 上的 `win_amd64` wheel 不匹配，
> `pip install numpy` 会报 `Could not find a version that satisfies the
> requirement numpy (from versions: none)`。bibtexparser 是纯 Python 包，
> 走 pip 没问题。
>
> `zip` 属于 msys 子系统（包名就是 `zip`，没有 `mingw-w64-x86_64-` 前缀），
> W4 打包时要用。

---

## W2. 配置与安装到临时目录

```bash
cd /path/to/Setzer
meson setup builddir          # 已存在则用 meson setup --reconfigure builddir
rm -rf /c/setzer_pkg
DESTDIR=/c/setzer_pkg meson install -C builddir
```

**注意 DESTDIR 的路径拼接方式**：meson 会在 DESTDIR 下重建完整的 prefix 路径
（仅去掉盘符）。MSYS2 装在 `C:\msys64` 时 prefix 是 `C:/msys64/mingw64`，
所以产物落在 `/c/setzer_pkg/`**`msys64/mingw64`**`/`，而不是
`/c/setzer_pkg/mingw64/`。后续步骤统一用 `$PKGROOT` 指代它：

```bash
PKGROOT=/c/setzer_pkg/msys64/mingw64
# MSYS2 不在 C:\msys64 时用这行自动定位：
# PKGROOT=$(dirname "$(dirname "$(find /c/setzer_pkg -name setzer.bat | head -1)")")
```

安装产物布局：

```
$PKGROOT/bin/setzer.bat                              # Windows 启动器
$PKGROOT/bin/setzer                                  # Python 入口脚本
$PKGROOT/lib/python3.x/site-packages/setzer/         # 应用模块
$PKGROOT/share/Setzer/resources/                     # 静态资源
$PKGROOT/share/locale/...                            # 翻译
```

> Windows 上 meson 自动跳过 `.desktop`/mime/metainfo/man 安装
> （`data/meson.build` 中有平台条件判断）。

---

## W3. 打包运行时 DLL（制作便携版）

将 MSYS2 的 `mingw64/` 目录下的关键运行时文件复制到打包目录，使最终
用户无需安装 MSYS2 即可运行：

```bash
PKGROOT=/c/setzer_pkg/msys64/mingw64   # 见 W2
MSYS2ROOT=/mingw64                     # MSYS2 安装路径

mkdir -p "$PKGROOT/bin" "$PKGROOT/lib" "$PKGROOT/share"

# 运行时 DLL
cp "$MSYS2ROOT/bin/"*.dll "$PKGROOT/bin/"

# 可执行文件：setzer.bat 调的是 %~dp0python.exe，即与 .bat 同目录的 python，
# 必须一起打包，否则便携版根本起不来。gspawn 助手供 GLib 派生子进程使用。
cp "$MSYS2ROOT/bin/python.exe" "$PKGROOT/bin/"
cp "$MSYS2ROOT/bin/pythonw.exe" "$PKGROOT/bin/" 2>/dev/null || true
cp "$MSYS2ROOT/bin/gdbus.exe" "$PKGROOT/bin/" 2>/dev/null || true
cp "$MSYS2ROOT/bin/gspawn-win64-helper.exe" "$PKGROOT/bin/" 2>/dev/null || true
cp "$MSYS2ROOT/bin/gspawn-win64-helper-console.exe" "$PKGROOT/bin/" 2>/dev/null || true

# 库与数据
cp -r "$MSYS2ROOT/lib/girepository-1.0" "$PKGROOT/lib/"
cp -r "$MSYS2ROOT/lib/python3."*        "$PKGROOT/lib/"
cp -r "$MSYS2ROOT/lib/gdk-pixbuf-2.0"   "$PKGROOT/lib/"   # SVG/PNG 图标加载器
cp -r "$MSYS2ROOT/share/glib-2.0"       "$PKGROOT/share/" # 含 gschemas.compiled
cp -r "$MSYS2ROOT/share/icons"          "$PKGROOT/share/"
cp -r "$MSYS2ROOT/share/gtksourceview-5" "$PKGROOT/share/" 2>/dev/null || true
cp -r "$MSYS2ROOT/share/locale"         "$PKGROOT/share/" 2>/dev/null || true

# 清理 __pycache__（与 deb 打包同理，避免跨 Python 版本 stale pyc）
find "$PKGROOT" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$PKGROOT" -name "*.pyc" -delete 2>/dev/null || true
```

`cp -r $MSYS2ROOT/lib/python3.*` 是**合并**进已存在的
`$PKGROOT/lib/python3.x/`，不会覆盖掉 W2 装进 `site-packages/setzer/` 的应用
模块 —— 但顺序不能颠倒（必须先 W2 再 W3）。

清理后整棵树约 300 MB。

> **资源路径为什么能便携**：`setzer.in` 里的 resources_path /
> app_icons_path / localedir_path 三个占位符是 meson 在构建期按 prefix 写死的
> 绝对路径。`setzer.in` 的 `portable_path()` 会在 Windows 上按启动脚本自身
> 位置反推 `<root>`（脚本在 `<root>/bin/`，资源在 `<root>/share/`），推导结果
> 存在就优先用它，因此解压到任意目录都能找到资源；推导不到才回退到构建期
> 路径（开发模式 `setzer_dev.py` 走的就是回退分支）。
> 若删掉这段逻辑，便携版会刷一堆
> `Could not load commands XML .../resources\latexdb\...` 且符号面板、
> 主题、翻译全部失效。

---

## W4. 打包为 zip

zip 里要保留 `mingw64/` 这一层目录，所以要 `cd` 到它的**父目录**
（即 `$PKGROOT/..`），而不是 `/c/setzer_pkg`：

```bash
NEWVER=$(grep -m1 "version:" /path/to/Setzer/meson.build | sed "s/.*'\(.*\)'.*/\1/")
cd "$PKGROOT/.."          # = /c/setzer_pkg/msys64
zip -qr "/path/to/Setzer/setzer_${NEWVER}_windows_x64.zip" mingw64
```

最终用户解压到任意目录后运行 `mingw64\bin\setzer.bat` 即可。

**验收自测**（务必在“干净 PATH”下做，否则会误用宿主机的 MSYS2）：

```powershell
$pkg = 'C:\setzer_pkg\msys64\mingw64'
$env:PATH = 'C:\Windows\System32;C:\Windows'   # 故意不含 MSYS2
& "$pkg\bin\setzer.bat"
```

启动后 stderr 不应出现 `Could not load commands XML` /
`ModuleNotFoundError`。

---

## W5.（可选）制作 Inno Setup 安装程序

用 [Inno Setup](https://jrsoftware.org/isinfo.php) 封装便携目录树，
生成带快捷方式、文件关联、卸载器的 `.exe` 安装程序。示例 `.iss` 脚本
结构（将 `{#NEWVER}` 替换为实际版本号）：

```iss
[Setup]
AppName=Setzer
AppVersion={#NEWVER}
DefaultDirName={autopf}\Setzer
DefaultGroupName=Setzer
OutputBaseFilename=setzer_{#NEWVER}_windows_x64_setup
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
; 注意源路径含 msys64 这一层（见 W2）；createallsubdirs 保证空目录也建出来
Source: "C:\setzer_pkg\msys64\mingw64\*"; DestDir: "{app}\mingw64"; \
  Flags: recursesubdirs createallsubdirs ignoreversion

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

- **`pip install numpy` 报 `from versions: none`** → MSYS2 的 MinGW Python
  用不了 PyPI 的 `win_amd64` wheel，改用
  `pacman -S mingw-w64-x86_64-python-numpy`（见 W1）。
- **`meson setup` 报 `Program 'msgfmt' not found`** → 安装 `gettext`
  （见 W1）。
- **`zip -r ... mingw64` 报 `name not matched: mingw64`** → `cd` 错了目录，
  应该在 `$PKGROOT/..`（`/c/setzer_pkg/msys64`）下执行（见 W4）。
- **双击 `setzer.bat` 一闪而过 / 提示找不到 python** → W3 只拷了 `*.dll`
  没拷 `python.exe`。`setzer.bat` 用的是 `%~dp0python.exe`（同目录的
  python），必须一并打包。
- **便携版启动后刷 `Could not load commands XML ...`、符号面板空白** →
  资源路径回退到了构建期的绝对路径。检查 `$PKGROOT/share/Setzer/resources`
  是否存在，以及 `setzer.in` 的 `portable_path()` 是否还在（见 W3 末尾）。
- **Windows 上运行报 `ModuleNotFoundError: No module named 'gi'`** →
  未在 MSYS2 MINGW64 终端中运行，或未安装
  `mingw-w64-x86_64-python-gobject`。便携版需确保 `mingw64\bin` 下的
  GTK4/GLib DLL 与 Python 在同一目录树。
- **Windows 上 LaTeX 构建无输出 / 弹出控制台窗口** → 确认 LaTeX 引擎
  （如 `xelatex.exe`）在 `PATH` 中；已用 `CREATE_NO_WINDOW` 抑制控制台弹出，
  若仍出现说明运行的是旧版本。
