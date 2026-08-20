# NeoSetzer macOS 打包与发布

NeoSetzer 提供 **macOS Apple Silicon（ARM64）** 应用包。正式产物由 GitHub Actions 的 Apple Silicon macOS 运行器构建，文件名为 `setzer_<version>_macos_arm64.zip`。

## 使用发布包

解压 ZIP 后会得到 `Setzer.app`。应用包已包含 NeoSetzer 的 Python 代码、GTK4/libadwaita 运行时、资源、翻译，以及 **Adwaita symbolic 图标主题**，无需用户预装 Homebrew、Python 或 GTK。

当前发布包使用临时签名，**尚未经过 Apple Notarization**。首次启动若被 Gatekeeper 阻止，请在系统“隐私与安全性”中确认打开；确认来源可信后，也可以执行：

```bash
xattr -dr com.apple.quarantine Setzer.app
```

## 正式发布

macOS 包不单独创建 Release。请遵循 [Debian 发布流程](debian.md#正式发布流程)：同步 `meson.build` 与 `CHANGELOG.md`，然后推送匹配的 `v*` 标签。`build-macos.yml` 会生成 `.app`、压缩为 ZIP，并上传到与 Linux 和 Windows 资产相同的 GitHub Release。

工作流会验证应用入口可执行，并检查包内是否存在：

| 路径或资源 | 用途 |
|---|---|
| `Setzer.app` | macOS 应用包。 |
| `share/icons/Adwaita/index.theme` | GTK 主题索引。 |
| 代表性 symbolic SVG 图标 | 防止非 GNOME 平台图标回退失败。 |
| Python site-packages 与 NumPy | 保证应用和预览模块的启动依赖完整。 |

## 本地构建说明

macOS 打包涉及 GTK、PyGObject、libadwaita、Python 运行时和 PyInstaller 资源收集。为避免本机环境差异，**GitHub Actions 是当前唯一受支持的正式构建路径**。开发者如需修改打包逻辑，应直接更新 `.github/workflows/build-macos.yml`，并通过手动触发工作流验证生成的应用包。

## 支持范围与后续工作

| 项目 | 当前状态 |
|---|---|
| Apple Silicon（M1/M2/M3/M4） | 已支持并由 CI 验证。 |
| Intel Mac（x64） | 暂未提供发布包。 |
| Universal 双架构包 | 暂未提供。 |
| Developer ID 签名与 Notarization | 尚未配置；需提供 Apple Developer 凭据后单独接入。 |
