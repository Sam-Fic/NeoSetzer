# NeoSetzer Debian 打包与发布

NeoSetzer 的 Debian x64 安装包由 GitHub Actions 在 **Ubuntu 24.04** 上构建。正式发布时，以标签触发的自动流程为准；本地打包仅用于开发调试或验证。

## 发布产物

正式标签构建会生成并上传以下 Linux 资产：

| 文件 | 适用范围 | 说明 |
|---|---|---|
| `setzer_<version>_amd64.deb` | Debian/Ubuntu x64 | 使用 `dpkg-deb` 构建的安装包。 |

当前包通过 `Depends` 使用发行版提供的运行时；**Ubuntu 26.04+** 和 **Debian 14+** 是经过工作流验证的目标环境（冒烟容器为 Ubuntu 26.04）。

## 依赖模型

`.deb` 只包含 NeoSetzer 自身的 Python 源码、资源与翻译（解压约 15 MB，压缩后约 4 MB）。GTK、libadwaita、Python 运行时与 TeX 都由发行版提供，由 apt 按 `Depends` 安装。包内**不**捆绑 GTK：Linux 上捆绑系统库会与宿主库冲突，需要自包含交付时应使用 Flatpak。

`control` 中的版本约束与代码实际使用的 API 对应：

| 依赖 | 约束 | 原因 |
|---|---|---|
| `python3` | `>= 3.10` | 源码使用 `X \| None` 类型注解 |
| `gir1.2-gtk-4.0` | `>= 4.10` | `Gtk.FileDialog`、`Gtk.FontDialogButton` |
| `gir1.2-adw-1` | `>= 1.9` | `Adw.Dialog`、`Adw.PreferencesDialog`、`Adw.AlertDialog`，以及 `Adw.Spinner`（1.6）、`Adw.ToggleGroup`（1.7）、`Adw.ShortcutsDialog`（1.8）、`Adw.Sidebar`（1.9） |
| `gir1.2-gtksource-5` | `>= 5.4` | `GtkSource.StyleSchemePreview` |

版本不足时 apt 会直接拒绝安装，不会出现「装上了但启动即崩」。更旧的发行版（Ubuntu 24.04 自带 libadwaita 1.5、Debian 13 自带 1.7 等）没有满足约束的软件包，属于预期的不支持范围。

`gir1.2-webkit-6.0` 是 **Recommends**，只用于帮助面板的内嵌浏览器。缺失时应用正常启动，帮助页改用系统浏览器打开。

## AI 内嵌终端依赖（VTE）

侧栏内嵌 Agent 终端依赖 `Vte-3.91` typelib（GTK4 绑定；注意 `Vte-2.91` 是 GTK3 绑定，与本应用的 GTK4 不兼容），已作为 **Recommends**（`gir1.2-vte-3.91`）写入工作流 `control` 元数据。它是可选依赖：缺失时应用正常启动，AI 发送自动回退到外部终端 + 剪贴板，不阻塞主流程。

若后续引入 Flatpak 打包：需核实 `org.gnome.Platform` 运行时是否携带 `Vte-3.91` typelib（GNOME 运行时通常包含）；缺失时在 manifest 中追加 `vte` 模块（gtk4 分支）。仓库根的 `org.cvfosammmm.Setzer.json` 即当前 Flatpak manifest。

## 正式发布

发布流程（版本号、CHANGELOG、tag、GitHub Release）见 [docs/RELEASE.md](../RELEASE.md)。推送 `v*` 标签后，Debian、Windows 与 macOS 工作流会并行构建，并把各自制品附加到同一 GitHub Release。不要手动创建仅包含 `.deb` 的 Release；这样会导致跨平台资产和标签源码不一致。

## CI 冒烟测试

打包完成后，`build-deb.yml` 会在干净的 `ubuntu:24.04` 容器里用 `--no-install-recommends` 安装刚生成的 `.deb`（只依赖 `Depends`，等价于最小系统），然后：

1. 执行 `setzer --version`，验证全部 GI typelib 与 Python 模块可导入；
2. 用 `xvfb-run` + `dbus-run-session` 启动 GUI，确认 12 秒内进程没有退出。

任一步失败都不会上传资产或发布 Release。本地验证时，把工作流该步骤的 heredoc 脚本原样保存后，用同一条 `docker run` 命令运行即可。

## 本地 Debian 构建

```bash
sudo apt-get update
sudo apt-get install --yes meson ninja-build gettext dpkg-dev

cd /path/to/NeoSetzer
rm -rf builddir package-root
meson setup builddir --prefix=/usr
meson compile -C builddir

DESTDIR="$PWD/package-root" meson install -C builddir
find package-root -type d -name '__pycache__' -prune -exec rm -rf {} +
find package-root -type f -name '*.pyc' -delete
```

随后按 `.github/workflows/build-deb.yml` 中的 `control` 元数据创建 `package-root/DEBIAN/control`，并执行：

```bash
dpkg-deb --build --root-owner-group package-root "setzer_<version>_amd64.deb"
dpkg-deb -I "setzer_<version>_amd64.deb"
dpkg-deb -c "setzer_<version>_amd64.deb"
```

> 构建产物已由 `.gitignore` 排除。请不要提交 `.deb` 文件、`package-root/`、`builddir/`、`__pycache__/` 或 `.pyc` 文件。

## 故障排查

| 现象 | 处理方式 |
|---|---|
| `msgfmt` 不存在 | 安装 `gettext`。 |
| `apt install` 报依赖不满足 | 目标系统低于包要求（GTK 4.10 / libadwaita 1.9），属于预期行为；升级到 Ubuntu 26.04+ 或 Debian 14+。 |
| 绕过依赖强制安装后启动报 `Adw.Dialog` / `Gtk.FileDialog` 不存在 | 系统 GTK 或 libadwaita 版本不足。不要用 `--force-*` 安装，改用 Flatpak 或升级系统。 |
| 帮助面板显示占位文案、导航按钮全灰 | 缺少 `gir1.2-webkit-6.0`（Recommends 可被跳过安装）。需要内嵌浏览器时安装该包并重启应用。 |
| AI 发送打开的是外部终端而非侧栏内嵌终端 | 目标系统缺少 `gir1.2-vte-3.91`（Recommends 可被跳过安装）。安装该包后重启应用即可。 |
| 安装后行为像旧版本 | 清理目标机中的旧 `__pycache__`；正式包不携带 Python 字节码。 |
| 发布文件未进入 Release | 先确认标签版本和 `meson.build` 一致，再查看对应 Actions 运行。 |
