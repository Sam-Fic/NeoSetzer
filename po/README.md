# po 目录说明

本文件记录 po 目录的文件清单和翻译维护流程。代码贡献规范见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 文件清单

| 文件 | 用途 |
|------|------|
| `LINGUAS` | 支持的语言代码列表，每行一个 |
| `POTFILES` | 含可翻译字符串的源文件清单，由 `generate-potfiles.sh` 生成 |
| `setzer.pot` | 翻译模板，由 meson + xgettext 生成，已纳入版本控制供 CI 校验 |
| `setzer.its` | ITS 规则，让 xgettext 提取 XML 资源文件中的可翻译字符串 |
| `*.po` | 各语言的翻译文件 |
| `sync-po.sh` | po 同步与校验脚本 |
| `generate-potfiles.sh` | 重新生成 POTFILES 清单 |

`setzer` 是 gettext 域名和资源命名前缀，保留该名称以保持既有安装和翻译文件兼容性。

## 更新翻译

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
- [ ] `po/setzer.pot` 已同步至最新（如改动了可翻译字符串）
- [ ] 翻译为人工完成，不使用机器翻译

## 新增语言

将 `lang` 替换为语言代码：

```bash
meson setup --wipe builddir --prefix=/tmp/usr
ninja -C builddir setzer-pot
xgettext data/resources/latexdb/*/*.xml data/resources/document_wizard/languages.xml \
  -o po/setzer.pot --from-code=UTF-8 --join-existing --its=po/setzer.its
cp po/setzer.pot po/lang.po
```

翻译 `po/lang.po` 后，将语言代码加入 `LINGUAS`，再运行 `./po/sync-po.sh --check` 确认通过。

## 测试翻译

Meson 的翻译测试需要先执行临时安装：

```bash
DESTDIR=/tmp/neosetzer-i18n meson install -C builddir
LANGUAGE=lang ./scripts/dev/setzer.dev
```

将 `lang` 替换为要验证的语言代码。完成后可删除 `/tmp/neosetzer-i18n`。

## .po 元数据模板

新建或重置 `.po` 文件头部时可使用以下模板：

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

不要在 `.po` 文件开头新增版权行，以保持历史版权策略一致。
