# NeoSetzer 发布流程

每次发布新版本时按以下步骤操作。所有步骤必须按顺序执行，版本号必须在 `meson.build`、`CHANGELOG.md` 和 Git 标签三处保持一致。

## 检查清单

### 1. 确认上一版本以来的变更

```bash
PREV_VERSION=$(git tag --sort=-creatordate | head -1 | sed 's/^v//')
echo "上一版本: ${PREV_VERSION}"
git log "v${PREV_VERSION}..HEAD" --oneline
```

浏览所有提交，排除 `ci:`、`chore:`（如 `.gitignore`）和 `docs:` 类提交，确定需要写入更新日志的功能、修复和重构。

### 2. 更新 CHANGELOG.md

在 `CHANGELOG.md` 顶部添加新版本条目，格式与历史条目一致：

```markdown
## vXX — YYYY-MM-DD

### 主要改进

- **新增/修复/重构/优化 描述**：详细说明（中文）。

### Improvements

- **feat/fix/refactor/perf**: Description (English).

---
```

**要求**：
- 中文使用 `**加粗前缀**：描述` 格式（如 `**新增矩阵创建对话框**：...`）
- 英文使用 `**类型**: 描述` 格式（如 `**feat**: Add ...`）
- 类型前缀：`feat` / `fix` / `perf` / `refactor` / `ci` / `i18n` / `docs` / `chore`
- 以 `---` 结尾

### 3. 更新 meson.build 版本号

```bash
# 将 version: 'XX' 中的数字加 1
sed -i "s/version: '[0-9]*'/version: '${NEW_VERSION}'/" meson.build
```

### 4. 提交并创建标签

```bash
git add meson.build CHANGELOG.md
git commit -m "release: version ${NEW_VERSION}"
git tag -a "v${NEW_VERSION}" -m "NeoSetzer ${NEW_VERSION}"
git push origin master "v${NEW_VERSION}"
```

### 5. 创建 GitHub Release

```bash
# 提取 CHANGELOG.md 中新版本条目作为 Release 正文
python3 -c "
with open('CHANGELOG.md') as f:
    content = f.read()
start = content.find('## v${NEW_VERSION}')
end = content.find('\n---\n', start)
print(content[start:end])
" > /tmp/release_notes.md
echo "---" >> /tmp/release_notes.md

gh release create "v${NEW_VERSION}" \
  --repo Sam-Fic/NeoSetzer \
  --title "NeoSetzer ${NEW_VERSION}" \
  --notes-file /tmp/release_notes.md
```

### 6. 验证 Actions 通过

```bash
gh run list --repo Sam-Fic/NeoSetzer --limit 5
```

确认以下 4 个 workflow 全部成功：
- Run unit tests
- Build Debian package
- Build Windows package
- Build macOS package

### 7. 修复失败（如需要）

**最常见的失败原因**：`meson.build` 版本号与 tag 不一致。

```bash
# 修正 meson.build 版本号
# 提交、更新标签、强制推送
git add meson.build
git commit -m "chore: bump version to ${NEW_VERSION} in meson.build"
git tag -d "v${NEW_VERSION}"
git tag "v${NEW_VERSION}"
git push origin +refs/tags/v${NEW_VERSION} master
```

重新检查 Actions 直到全部通过。

## 常见错误

| 问题 | 原因 | 处理 |
|---|---|---|
| CI 报 `test "$project_version" = "$tag_version"` 失败 | `meson.build` 版本号与 tag 不匹配 | 更新 `meson.build` 版本号，重新打 tag 并强制推送 |
| Release 资产未生成 | tag 未推送或 Actions 未触发 | 确认 `git push origin master "v${VERSION}"` 成功 |
| CHANGELOG.md 为空或不完整 | 未按格式填写 | 参考历史条目补充 |
