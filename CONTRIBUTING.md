# 贡献指南

感谢你对 NeoSetzer 的关注！本文档说明参与开发的基本流程。

## 翻译贡献

NeoSetzer 使用 gettext 进行国际化，翻译文件位于 `po/` 目录，当前支持 7 种语言：de、es、fr、it、pt_BR、zh_CN、zh_TW。

### 更新翻译

当源代码新增或修改了可翻译字符串后，需要同步 `.po` 文件。**请使用 `po/sync-po.sh` 脚本，不要手动运行 `msgmerge`。**

```bash
# 1. 生成最新的 pot 模板
meson setup --wipe builddir --prefix=/tmp/usr
ninja -C builddir setzer-pot
xgettext data/resources/latexdb/*/*.xml data/resources/document_wizard/languages.xml \
  -o po/setzer.pot --from-code=UTF-8 --join-existing --its=po/setzer.its

# 2. 同步全部 po 文件（清理 obsolete、零 fuzzy、稳定排序）
./po/sync-po.sh

# 3. 在 .po 文件中补译新增的未译条目

# 4. 提交前校验
./po/sync-po.sh --check
```

也可以只同步单个语言：

```bash
./po/sync-po.sh es
./po/sync-po.sh --check es
```

### 为什么不用 `msgmerge -U`？

`msgmerge` 默认开启 fuzzy matching，会将不相关的旧翻译错配到新 msgid 上，产生大量难以 review 的 diff 噪音。`sync-po.sh` 使用 `--no-fuzzy-matching` 杜绝错配，`--sort-by-file` 保持条目按源码位置稳定排序，并自动清理 obsolete 条目。

### 提交前检查清单

- [ ] `./po/sync-po.sh --check` 全部通过
- [ ] 不提交 `po/setzer.pot`（模板文件由 CI 生成）
- [ ] 翻译为人工完成，不使用机器翻译

## 代码贡献

### 开发环境

```bash
meson setup builddir --prefix=/usr
ninja -C builddir
./scripts/dev/setzer.dev
```

### 提交规范

提交信息使用 Conventional Commits 格式：

```
type(scope): 简短描述

可选的详细说明。
```

常用 type：`feat`（新功能）、`fix`（修复）、`i18n`（翻译）、`refactor`（重构）、`docs`（文档）。

### CI

所有 push 和 PR 会自动运行 [Run unit tests](.github/workflows/test.yml)，包括单元测试和翻译文件校验。PR 在 CI 通过后方可合入。

## 问题反馈

请通过 [NeoSetzer Issues](https://github.com/Sam-Fic/NeoSetzer/issues) 报告 bug 或提出功能建议。
