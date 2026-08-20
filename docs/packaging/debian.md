# NeoSetzer Debian 打包与发布

NeoSetzer 的 Debian x64 安装包由 GitHub Actions 在 **Ubuntu 24.04** 上构建。正式发布时，以标签触发的自动流程为准；本地打包仅用于开发调试或验证。

## 发布产物

正式标签构建会生成并上传以下 Linux 资产：

| 文件 | 适用范围 | 说明 |
|---|---|---|
| `setzer_<version>_amd64.deb` | Debian/Ubuntu x64 | 使用 `dpkg-deb` 构建的安装包。 |

当前包依赖 WebKitGTK 6，因此目标系统应提供 `gir1.2-webkit-6.0`；Ubuntu 24.04+ 和 Debian 13+ 是经过工作流验证的目标环境。

## 正式发布流程

发布前必须使 `meson.build`、`CHANGELOG.md` 和 Git 标签使用同一版本号。`CHANGELOG.md` 应包含中文 `主要改进` 与英文 `Improvements` 两个小节，并与 GitHub Release 正文保持一致。

```bash
# 1. 确认上一标签以来的变更
CURRENT_VERSION=$(sed -n "s/^[[:space:]]*version:[[:space:]]*'\([^']*\)'.*/\1/p" meson.build)
git log "v${CURRENT_VERSION}..HEAD" --oneline

# 2. 更新 meson.build 的版本号与 CHANGELOG.md
#    例如：76 -> 77；确保新增 ## v77 条目。

# 3. 提交版本资料并创建匹配标签
NEW_VERSION=77
git add meson.build CHANGELOG.md
git commit -m "release: version ${NEW_VERSION}"
git tag -a "v${NEW_VERSION}" -m "NeoSetzer ${NEW_VERSION}"
git push origin master "v${NEW_VERSION}"
```

推送 `v*` 标签后，Debian、Windows 与 macOS 工作流会并行构建，并把各自制品附加到同一 GitHub Release。不要手动创建仅包含 `.deb` 的 Release；这样会导致跨平台资产和标签源码不一致。

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
| 运行期缺少 GTK、Adwaita 或 WebKit typelib | 使用工作流 `control` 中的依赖清单，或在目标系统安装对应发行版软件包。 |
| 安装后行为像旧版本 | 清理目标机中的旧 `__pycache__`；正式包不携带 Python 字节码。 |
| 发布文件未进入 Release | 先确认标签版本和 `meson.build` 一致，再查看对应 Actions 运行。 |
