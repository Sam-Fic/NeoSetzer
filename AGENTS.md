# AGENTS.MD

本文件是 AI 协作索引。遇到对应场景时，先查阅指向的文档再动手。

| 场景 | 查阅文档 |
|------|----------|
| 改动了可翻译字符串（`_(...)` 文本、`data/resources/` 下 XML） | [po/README.md](po/README.md) |
| po 大面积格式 diff / blame 被归一化污染 | [po/README.md](po/README.md)「规范形态与归一化」 |
| 创建或修改 UI / 设计（弹窗、颜色、间距、组件等） | [docs/ui-guidelines.md](docs/ui-guidelines.md) |
| 写 GTK / libadwaita 代码（API 坑位、崩溃、静默失败） | [docs/dev/gtk-pitfalls.md](docs/dev/gtk-pitfalls.md) |
| UI 目检 / 端到端验证（Xvfb、niri 桌面、截图） | [docs/dev/ui-verification.md](docs/dev/ui-verification.md) |
| 发版（版本号、CHANGELOG、tag） | [docs/RELEASE.md](docs/RELEASE.md) |
| 打包（Debian / Windows / macOS） | [docs/packaging/](docs/packaging/) |
| 运行测试（`meson test`、`pytest`） | [CONTRIBUTING.md](CONTRIBUTING.md)「代码贡献」 |
| 静态检查（ruff，`_`/`ngettext` 注入约定） | [CONTRIBUTING.md](CONTRIBUTING.md)「静态检查」 + [ruff.toml](ruff.toml) |
| 构建系统配置（meson.build 结构） | [meson.build](meson.build) + [po/meson.build](po/meson.build) |
| CI workflow（test / build-deb / build-windows / build-macos / promote-artifact） | [.github/workflows/](.github/workflows/) |
| 示例项目结构与 Magic Comment | [data/resources/example_project/README.md](data/resources/example_project/README.md) |

## 协作纪律

- **验证类 grep 禁止 `head` 截断**：确认「X 是否还有调用者」用 `grep -rc` 或全量输出；删代码前必须看到真零（`grep -c` 为 0），不能拿被截断的非空输出当结论——同名/近名符号会先占满输出窗口。
- **丢弃项必须具名**：禁止 `_, x = ...` 式解包。`_` 是 builtins 注入的翻译函数，元组丢弃项会把 str 写进模块全局遮蔽它，此后该模块的 `_(...)` 全部报 TypeError（详见 [docs/ui-guidelines.md](docs/ui-guidelines.md) 8.1）。
- **多行提交信息写 `/tmp/msg_*.txt` 后 `git commit -F`**：`-m` 里的 `\n` 会留字面量。
- **纯格式变更单独成 commit**：归一化、批量重排（po、格式化等）不与语义改动混提；此类提交登记进 [.git-blame-ignore-revs](.git-blame-ignore-revs)，GitHub blame 自动跳过，本地生效需 `git config blame.ignoreRevsFile .git-blame-ignore-revs`（po 详见 [po/README.md](po/README.md)「规范形态与归一化」）。
