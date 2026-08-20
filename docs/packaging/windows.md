# NeoSetzer Windows 打包与发布

NeoSetzer 提供 **Windows x64 便携版**。正式包由 GitHub Actions 的 MSYS2 MINGW64 环境构建，文件名为 `setzer_<version>_windows_x64.zip`。

## 使用发布包

下载并解压 ZIP 后，运行：

```text
mingw64\bin\setzer.bat
```

便携包包含 Python、GTK4、libadwaita、运行时 DLL、GObject typelib、GLib schema、翻译、应用资源和 **Adwaita symbolic 图标主题**。最终用户无需预先安装 MSYS2。

## 正式发布

Windows 包不单独创建 Release。请遵循 [Debian 发布流程](debian.md#正式发布流程)：更新版本号和 `CHANGELOG.md`，然后推送与 `meson.build` 匹配的 `v*` 标签。`build-windows.yml` 会自动构建并把 ZIP 上传到同一 GitHub Release。

工作流会检查以下条件：

| 检查 | 目的 |
|---|---|
| 标签与 `meson.build` 版本一致 | 防止发布资产与源码版本不匹配。 |
| `setzer.bat` 和 Python 入口存在 | 确保解压后可启动。 |
| Adwaita 主题元数据和 symbolic 图标存在 | 防止非 GNOME 环境出现缺失图标。 |
| 压缩包内容可解压 | 防止上传损坏的便携包。 |

## 本地构建（开发验证）

本地打包应在 **MSYS2 MINGW64** 环境中执行，而不是 MSYS 或 UCRT64 环境。

```bash
pacman -S --needed \
  mingw-w64-x86_64-meson mingw-w64-x86_64-ninja \
  mingw-w64-x86_64-gtk4 mingw-w64-x86_64-libadwaita \
  mingw-w64-x86_64-gtksourceview5 mingw-w64-x86_64-poppler \
  mingw-w64-x86_64-python mingw-w64-x86_64-python-cairo \
  mingw-w64-x86_64-python-gobject mingw-w64-x86_64-python-numpy \
  mingw-w64-x86_64-unzip gettext zip
python -m pip install --break-system-packages bibtexparser

meson setup builddir
meson compile -C builddir
```

然后以 `.github/workflows/build-windows.yml` 为唯一参考完成 `DESTDIR` 安装、MSYS2 运行时复制、Adwaita 主题复制与 ZIP 校验。工作流包含已经验证过的动态 `DESTDIR` 路径处理；不要重新引入硬编码的 `C:\msys64` 路径假设。

## 常见问题

| 现象 | 处理方式 |
|---|---|
| `pip install numpy` 找不到可用版本 | 使用 `pacman` 安装 `mingw-w64-x86_64-python-numpy`，不要从 PyPI 安装 NumPy。 |
| 双击后无法找到 Python 或 GTK DLL | 使用完整的便携 ZIP，确认运行的是 `mingw64\bin\setzer.bat`。 |
| 图标缺失 | 确认包内存在 `mingw64/share/icons/Adwaita/index.theme`；该检查已在 CI 中强制执行。 |
| 资产未进入 Release | 检查标签与项目版本是否一致，并查看 Windows 打包工作流日志。 |
