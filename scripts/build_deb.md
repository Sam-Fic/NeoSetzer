# Setzer Debian 打包标准流程

> 本文档供 AI 编程助手在协助构建和发布 Setzer 的 `.deb` 包时参考。
> **AI 应自动化完成全部流程**：版本号更新、CHANGELOG 更新、commit、tag、push、deb 构建、GitHub Release 创建，无需用户手动执行任何步骤。

本流程用于从源码构建 Setzer 的本地 `.deb` 包。采用 **meson 安装到临时
DESTDIR + `dpkg-deb --build`** 的手工打包方式（不依赖 `debuild`/`dh`，
适合本地构建与分发）。

环境实测：meson 1.10.1、Python 3.14、dpkg-deb 可用、GTK4/Adwaita 运行时齐全。

---

## 1. 前置依赖

构建期（本机编译/翻译需要）：

```bash
sudo apt-get install -y meson ninja-build gettext
```

运行时（打进 `Depends`，目标机安装 deb 时由 apt 自动解决）：

- `python3`、`python3-gi`、`python3-cairo`
- `gir1.2-gtk-4.0`、`gir1.2-adw-1`、`gir1.2-gtksource-5`、`gir1.2-pango-1.0`
- `gir1.2-poppler-0.18`、`gir1.2-webkit-6.0`、`gir1.2-xdp-1.0`
- `Recommends: texlive`（实际编译 LaTeX 文档所需，非编辑器运行必需）

> WebKit / Xdp 的 gir 版本随发行版变化（如 `gir1.2-webkit2-4.1` 或
> `gir1.2-webkit-6.0`），写 `Depends` 时按本机 `dpkg -l | grep gir1.2-webkit`
> 实际结果填写。

---

## 2. 配置与编译安装

```bash
# 回到仓库根目录
cd /path/to/Setzer

# 清理可能存在的旧 build 目录
rm -rf builddir

# 以 /usr 为前缀配置（决定 dist-packages 等标准安装路径）
meson setup builddir --prefix=/usr

# 安装到临时 root（DESTDIR 仅影响安装目的地，不改前缀）
# 注意：务必先清掉上一次 DESTDIR，否则会混入旧版 __pycache__（见下方"Stale .pyc 陷阱"）。
rm -rf /tmp/setzer_deb_root
DESTDIR=/tmp/setzer_deb_root meson install -C builddir
```

> ### ⚠️ Stale `.pyc` 陷阱（实测踩过）
>
> meson 的 `pycompile` 是**增量编译**：若目标目录已存在 `.pyc`，且 meson 认为它
> "比 `.py` 新"就跳过重编。如果你在本机**之前装过更旧的 Setzer**（无论 `dpkg -i`
> 还是 `meson install`），旧版留下的 `.pyc` 其 mtime 可能晚于本次复制进来的
> `.py`，导致运行时 Python 直接信任陈旧字节码——表现为"明明代码最新、deb 装好后
> 某功能（如预览区圆角卡片）却缺失"。
>
> **症状排查**：`ls -la` 对比某模块的 `.py` 与同名 `.pyc`，若 `.pyc` 的 mtime
> 比 `.py` 新，且反编译 `.pyc` 找不到新增的代码（如
> `add_css_class('preview-card')`），即为此问题。
>
> **打包侧根治**：每次 `meson install` 前 `rm -rf /tmp/setzer_deb_root`（上方已含）。
>
> **安装侧根治**：`dpkg -i` 之后清掉系统里的 stale pyc，否则本机仍跑旧字节码：
> ```bash
> sudo find /usr/lib/python3/dist-packages/setzer -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
> ```
> 或安装时直接不打包 `.pyc`，由目标机首次运行时自行编译。


安装产物布局（均在 `/tmp/setzer_deb_root` 下）：

```
usr/bin/setzer                                  # 入口脚本 (#!/usr/bin/python3)
usr/lib/python3/dist-packages/setzer/          # Python 模块
usr/share/applications/...desktop
usr/share/icons/hicolor/scalable/apps/...svg
usr/share/mime/packages/...mime.xml
usr/share/metainfo/...metainfo.xml
usr/share/man/man1/setzer.1
usr/share/locale/...                            # 翻译 (.mo)
usr/share/Setzer/resources/                    # 静态资源
```

> **关于测试子目录**：当前 `tests/python/meson.build` 的 `test()` 在新版
> meson 下会因参数类型校验报错，使 `meson setup` 整体失败（仅影响测试注册，
> 不影响安装产物）。打包时临时注释 `tests/meson.build` 末尾的 `subdir('python')`
> 即可让 configure 通过，打包完成后**务必还原该行**，保持工作区干净。

---

## 3. 编写 DEBIAN/control

```bash
mkdir -p /tmp/setzer_deb_root/DEBIAN
cat > /tmp/setzer_deb_root/DEBIAN/control <<'EOF'
Package: setzer
Version: 67
Section: editors
Priority: optional
Architecture: amd64
Depends: python3:any, python3-gi, python3-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-gtksource-5, gir1.2-pango-1.0, gir1.2-poppler-0.18, gir1.2-webkit-6.0, gir1.2-xdp-1.0
Recommends: texlive
Maintainer: Setzer Packaging <local@build>
Description: LaTeX editor for the GNOME desktop
 Setzer is a LaTeX editor with a modern GTK4/libadwaita interface.
 It features live PDF preview, autocomplete, code folding, a document
 structure outline, and an integrated build system supporting pdflatex,
 xelatex, lualatex and tectonic.
EOF
```

- `Version` 取自 `meson.build` 顶部的 `version:` 字段。
- `Architecture` 用 `dpkg --print-architecture` 取得（通常为 `amd64`）。
- 升级版本时同步修改 `Version` 与 `meson.build`。

---

## 4. 构建 deb 包

```bash
cd /tmp
dpkg-deb --build --root-owner-group setzer_deb_root /path/to/Setzer/setzer_67_amd64.deb
```

`--root-owner-group` 把所有文件属主归为 `root`，符合 deb 规范。
产物文件名约定：`setzer_<version>_<arch>.deb`。

---

## 5. 校验与安装

```bash
# 查看元信息与依赖
dpkg-deb -I setzer_67_amd64.deb

# 查看文件清单（确认关键路径）
dpkg-deb -c setzer_67_amd64.deb | grep -E "usr/bin/setzer|dist-packages/setzer$"

# 安装
sudo dpkg -i setzer_67_amd64.deb
sudo apt-get install -f   # 自动补全缺失依赖

# 清掉本机可能残留的旧版 stale .pyc（见第 2 步 Stale .pyc 陷阱），
# 否则即使装了新 deb，Python 仍可能信任旧字节码导致功能缺失。
sudo find /usr/lib/python3/dist-packages/setzer -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null

setzer                   # 启动验证
```

---

## 6. 清理

```bash
rm -rf /tmp/setzer_deb_root builddir
```

> 打包产物 `*.deb` 已被仓库 `.gitignore` 忽略，勿提交进版本库。

---

## 7. 发布新版本

每次发布新版本按以下步骤：

### 7.1 变更日志格式（强制）

每次发版前必须在仓库根目录更新 `CHANGELOG.md`，新增一条记录，格式如下（中文和英文日志均需填写，冒号使用全角 `：` 和半角 `:`）：

```markdown
## v{NEWVER} — {YYYY-MM-DD}

### 主要改进

- **简洁描述**：详细内容
- **简洁描述**：详细内容

### Improvements

- **Brief description**: Detailed content
- **Brief description**: Detailed content
```

- 必须包含 `主要改进` / `Improvements` 两个小节（无变更时写 `无`）
- 中文条目格式：`**简洁描述**：详细内容`（冒号为全角 `：`）
- 英文条目格式：`**Brief description**: Detailed content`（冒号为半角 `:`）
- 日期为发布当日，格式 `YYYY-MM-DD`
- 版本号与 `meson.build` 的 `version:` 字段、git tag 保持一致

**版本历史示例**：

| 版本 | 包含的提交（从旧到新） | 变更内容摘要 |
| ------ | -------------------------- | -------------------------------------------------- |
| v67   | `a1b2c3d`                  | 初始版本发布                                       |
| v68   | `e4f5g6h`, `i7j8k9l`     | 添加新功能、重构界面                               |
| v69   | `m0n1o2p`, `q3r4s5t`     | 优化构建流程、改进错误处理                       |

> 💡 当 Git 提交记录中没有明确的版本标记时，查看最近 20-50 条提交历史，结合 `meson.build` 的版本变更来确认版本边界。

### 7.2 发布步骤

```bash
# 1. 自动获取当前版本并加一（从 meson.build 读取）
CURVER=$(grep -oP 'version:\s*\K\d+' meson.build)
NEWVER=$((CURVER + 1))

# 2. 通过 Git 历史确认变更范围（基于上一版本 tag 与当前 HEAD 的差异）
#    查看自上一版本以来的所有提交
git log v${CURVER}..HEAD --oneline
#    查看详细变更内容（用于总结更新日志）
git log v${CURVER}..HEAD --stat --name-only

# 3. 更新 meson.build 中的 version 字段
sed -i "s/version: *${CURVER}/version: ${NEWVER}/" meson.build

# 4. 更新 CHANGELOG.md（参见上方格式要求）
#    根据第 2 步的 git log 输出，编辑仓库根目录的 CHANGELOG.md，新增 ## v${NEWVER} 条目

# 5. 提交变更并打 tag（tag 格式与 version 一致）
git add meson.build
git commit -m "release: v${NEWVER}"
git tag v${NEWVER}
git push && git push --tags

# 6. 构建 deb 包
rm -rf /tmp/setzer_deb_root builddir
meson setup builddir --prefix=/usr
DESTDIR=/tmp/setzer_deb_root meson install -C builddir
mkdir -p /tmp/setzer_deb_root/DEBIAN
cat > /tmp/setzer_deb_root/DEBIAN/control <<EOF
Package: setzer
Version: ${NEWVER}
Section: editors
Priority: optional
Architecture: \$(dpkg --print-architecture)
Depends: python3:any, python3-gi, python3-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-gtksource-5, gir1.2-pango-1.0, gir1.2-poppler-0.18, gir1.2-webkit-6.0, gir1.2-xdp-1.0
Recommends: texlive
Maintainer: Setzer Packaging <local@build>
Description: LaTeX editor for the GNOME desktop
 Setzer is a LaTeX editor with a modern GTK4/libadwaita interface.
 It features live PDF preview, autocomplete, code folding, a document
 structure outline, and an integrated build system supporting pdflatex,
 xelatex, lualatex and tectonic.
EOF
cd /tmp
dpkg-deb --build --root-owner-group setzer_deb_root /path/to/Setzer/setzer_${NEWVER}_amd64.deb

# 7. 验证构建结果
dpkg-deb -I setzer_${NEWVER}_amd64.deb
dpkg-deb -c setzer_${NEWVER}_amd64.deb | grep -E "usr/bin/setzer|dist-packages/setzer$"
```

### 7.3 创建 Release

创建 Release 并上传 `.deb` 包（注意格式按照模板写，中英文日志都有冒号）：

```bash
gh release create v${NEWVER} /path/to/Setzer/setzer_${NEWVER}_amd64.deb \
  --title "Setzer v${NEWVER}" \
  --notes "$(cat <<'EOF'
### 主要改进

- **简洁描述**：详细内容
- **简洁描述**：详细内容

### Improvements

- **Brief description**: Detailed content
- **Brief description**: Detailed content
EOF
)"
```

- `gh` 需预先 [`auth login`](https://cli.github.com/manual/gh_auth_login)（推荐 `git` 协议），无交互时可设 `GH_TOKEN` 环境变量。
- Release notes 内容应与 `CHANGELOG.md` 保持一致。

---

## 快速参考命令

```bash
# ──────────────────────────────────────
# 完整的版本发布流程（AI 全自动执行）
# ──────────────────────────────────────
# 1. git log v${CURVER}..HEAD 查看变更范围
# 2. 编辑 meson.build 更新版本号
# 3. 编辑 CHANGELOG.md 添加发布记录（双栏格式）
# 4. git add meson.build && git commit -m "release: v${NEWVER}" && git tag v${NEWVER} && git push && git push --tags
# 5. 构建 deb 包（见第 2 步"配置与编译安装"和第 4 步"构建 deb 包"）
# 6. dpkg-deb -I setzer_${NEWVER}_amd64.deb 验证元信息
# 7. gh release create v${NEWVER} --title "Setzer v${NEWVER}" --notes "$(sed -n '/^## v${NEWVER}/,/^## /p' CHANGELOG.md | head -n -1)" setzer_${NEWVER}_amd64.deb

# ──────────────────────────────────────
# 本地验证安装（快速开发测试）
# ──────────────────────────────────────
sudo dpkg -i setzer_${NEWVER}_amd64.deb
sudo apt-get install -f
sudo find /usr/lib/python3/dist-packages/setzer -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
setzer
```

---

## 常见问题

- **`meson setup` 报 `Program 'msgfmt' not found`** → 安装 `gettext`。
- **`meson setup` 因 `tests/python` 的 `test()` 报错中断** → 见第 2 步关于
  临时注释 `subdir('python')` 的说明，打包后还原。
- **安装后运行报 `ModuleNotFoundError: setzer`** → 确认 meson 配置用了
  `--prefix=/usr`；非 `/usr` 前缀会把模块装到 `/usr/local`，系统 Python
  默认不在其搜索路径内。
- **PDF 预览空白 / cairo 报错** → 入口脚本已 `import cairo` 在 `import gi`
  之前，属运行时环境缺失（缺 `python3-cairo` / `gir1.2-poppler`），非打包问题。