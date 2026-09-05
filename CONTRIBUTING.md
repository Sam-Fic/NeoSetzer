# 贡献指南

感谢你对 NeoSetzer 的关注！本文档说明参与开发的基本流程。

## 翻译贡献

NeoSetzer 使用 gettext 进行国际化，翻译文件位于 `po/` 目录，当前支持 7 种语言：de、es、fr、it、pt_BR、zh_CN、zh_TW。

改动可翻译字符串（`_(...)` 文本、`data/resources/` 下 XML）后，必须用 `po/sync-po.sh` 同步 po 文件并提交前运行 `--check`。禁止手动运行 `msgmerge`。详细流程见 [po/README.md](po/README.md)。

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
