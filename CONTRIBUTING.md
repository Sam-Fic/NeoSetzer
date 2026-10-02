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

### 静态检查

项目用 [ruff](https://docs.astral.sh/ruff/) 做静态检查（只启用 pyflakes 规则集），配置在根目录 `ruff.toml`。提交前建议跑一次，要求零告警：

```bash
ruff check .
```

两点项目约定已写进配置，不是误报也不是需要绕开的告警：

- `_` / `ngettext` 翻译函数由 `setzer.in` 启动时注入 builtins（`builtins._ = trans.gettext`），各模块直接使用属正常写法，勿改成逐文件 import——那会破坏「界面语言偏好优先于系统语言」的机制；
- 各包 `__init__.py` 里的导入是有意转出口，供其他模块短路径 import。

### 翻译文件 diff 优化

`sync-po.sh` 会重排 po 条目并刷新行号引用，导致 `git diff` 展示数千行噪音。
仓库根目录的 `.gitconfig` 定义了 `po-diff` driver，以 `msgid` 块为边界切 hunk，
把 po 文件的 PR 视图变得可读。

启用（克隆仓库后跑一次）：

```bash
git config include.path ../.gitconfig
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
