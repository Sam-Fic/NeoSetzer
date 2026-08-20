# NeoSetzer 国际化维护

NeoSetzer 当前未配置独立的托管翻译平台。请直接在本仓库提交翻译更新，并通过 [NeoSetzer Issues](https://github.com/Sam-Fic/NeoSetzer/issues) 报告翻译问题。

## 新增语言

将 `lang` 替换为语言代码：

```bash
meson setup --wipe builddir --prefix=/tmp/usr
ninja -C builddir setzer-pot
xgettext data/resources/latexdb/*/*.xml data/resources/document_wizard/languages.xml \
  -o po/setzer.pot --from-code=UTF-8 --join-existing --its=po/setzer.its
cp po/setzer.pot po/lang.po
```

翻译 `po/lang.po` 后，将语言代码加入 `po/LINGUAS`。

## 更新现有翻译

```bash
meson setup --wipe builddir --prefix=/tmp/usr
ninja -C builddir setzer-update-po
xgettext data/resources/latexdb/*/*.xml data/resources/document_wizard/languages.xml \
  -o po/setzer.pot --from-code=UTF-8 --join-existing --its=po/setzer.its
msgmerge -U po/lang.po po/setzer.pot
```

随后处理 `lang.po` 中标记为 `#, fuzzy` 的条目。`setzer` 是 gettext 域名和资源命名前缀，保留该名称以保持既有安装和翻译文件兼容性。

## 测试翻译

Meson 的翻译测试需要先执行临时安装：

```bash
DESTDIR=/tmp/neosetzer-i18n meson install -C builddir
LANGUAGE=lang ./scripts/dev/setzer.dev
```

将 `lang` 替换为要验证的语言代码。完成后可删除 `/tmp/neosetzer-i18n`。

## 提交前检查

| 检查项 | 要求 |
|---|---|
| 改动范围 | 只提交实际翻译过的 `.po` 文件、`LINGUAS` 和必要的源字符串清单更新。 |
| 模板文件 | 不提交生成的 `po/setzer.pot`。 |
| 格式验证 | 再次运行更新命令；除时间戳外不应产生意外差异。 |
| 编译验证 | 执行 `ninja -C builddir setzer-gmo`，确认 `.mo` 可正常生成。 |
| 版权信息 | 不要在 `.po` 文件开头新增版权行，以保持历史版权策略一致。 |

`.po` 文件的元数据可使用以下模板：

```po
msgid ""
msgstr ""
"Project-Id-Version: NeoSetzer\n"
"Report-Msgid-Bugs-To: https://github.com/Sam-Fic/NeoSetzer/issues\n"
"POT-Creation-Date: YEAR-MO-DA HO:MI+ZONE\n"
"PO-Revision-Date: YEAR-MO-DA HO:MI+ZONE\n"
"Last-Translator: FULL NAME <EMAIL@ADDRESS>\n"
"Language-Team: LANGUAGE\n"
"Language: lang\n"
"MIME-Version: 1.0\n"
"Content-Type: text/plain; charset=UTF-8\n"
"Content-Transfer-Encoding: 8bit\n"
```
