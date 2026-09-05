# AGENTS.MD

本文件是 AI 协作索引。遇到对应场景时，先查阅指向的文档再动手。

| 场景 | 查阅文档 |
|------|----------|
| 改动了可翻译字符串（`_(...)` 文本、`data/resources/` 下 XML） | [po/README.md](po/README.md) |
| 创建或修改弹窗组件（Adw.Dialog 等） | [docs/ui-guidelines.md](docs/ui-guidelines.md) |
| 发版（版本号、CHANGELOG、tag） | [docs/RELEASE.md](docs/RELEASE.md) |
| 打包（Debian / Windows / macOS） | [docs/packaging/](docs/packaging/) |
| 运行测试（`meson test`、`pytest`） | [CONTRIBUTING.md](CONTRIBUTING.md)「代码贡献」 |
| 构建系统配置（meson.build 结构） | [meson.build](meson.build) + [po/meson.build](po/meson.build) |
| CI workflow（test / build-deb / build-windows / build-macos） | [.github/workflows/](.github/workflows/) |
| 示例项目结构与 Magic Comment | [data/resources/example_project/README.md](data/resources/example_project/README.md) |
| 已知未解决问题 | [docs/known-issues/](docs/known-issues/) |
