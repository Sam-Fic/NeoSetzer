# UI 规范

本文适用于所有 NeoSetzer UI 改动。与 GNOME HIG / libadwaita 默认样式冲突时以官方规范为准；确实需要偏离时，在改动说明中给出理由。

| 章节 | 覆盖内容 |
|------|----------|
| 1 设计原则（强制） | 决策优先级、少写自定义 |
| 2 设计令牌 | 不透明度、间距 |
| 3 排版与色彩 | 字号、颜色层级、图标、描边、高对比度 |
| 4 控件规范 | 输入框、窗口失焦、无障碍名称、选择控件语义 |
| 5 布局与容器 | 通用布局、空状态、文本密集区入口 |
| 6 弹窗与浮层 | 弹窗、HeaderBar 按钮、分段控件、偏好行 |
| 7 轻反馈与打断 | 打扰阶梯、节流与静默 |
| 8 工程约定 | 翻译调用时机、动画、文案 |

## 1 设计原则（强制）

- 所有 UI 改动遵循 GNOME HIG 与 Adwaita：优先复用 libadwaita 标准样式类和语义色，尽量少写自定义样式。
- 取值优先级（从高到低）：组件默认值 → 语义色 / 标准样式类 → 设计令牌 → 自定义 CSS（必须注释说明前三级为何不适用）。

## 2 设计令牌

### 2.1 不透明度

可复用的颜色与不透明度只在 [style_gtk.css](../data/resources/style_gtk.css) 顶部定义一次；CSS 规则和 Python 代码都引用名字，不写裸数值。

| 类别 | 令牌 | 数值 | 用途 |
|------|------|------|------|
| 表面 / 状态 | `fill` | 0.06 | 输入框填充 |
| 表面 / 状态 | `overlay-accent` | 0.10 | 强调色提示浮层（拖放高亮） |
| 表面 / 状态 | `overlay-error` | 0.10 | 错误色提示浮层（拖放拒绝） |
| 表面 / 状态 | `accent-subtle` | 0.12 | 当前项（列表无焦点） |
| 表面 / 状态 | `accent-selected` | 0.25 | 选中项 |
| 表面 / 状态 | `focus-ring` | 0.60 | 输入框聚焦内环 |
| 阴影 | `soft` | 0.03 | 环境层 |
| 阴影 | `medium` | 0.07 | 中间层 |
| 阴影 | `edge` | 0.12 | 浮层描边层 |
| 阴影 | `strong` | 0.16 | 浮层主层 |
| 阴影 | `deep` | 0.25 | 徽标等深投影 |

令牌按**颜色角色**组织而非按 alpha 值去重：同值不同底色（如 `overlay-accent` 选中色 / `overlay-error` 错误色，均 0.10）是不同角色，不合并；次要文字的 dim 档（0.55）走 `--setzer-opacity-dim` + `.dimmed`，不占颜色令牌。

- 颜色优先 libadwaita 语义色（`@window_bg_color` 等）；半透明表面与状态色只用 `@setzer_*` 令牌，例如 `@setzer_input_fill`、`@setzer_focus_ring`、`@setzer_accent_selected`、`@setzer_shadow_strong`。
- 新增半透明样式时先复用现有令牌；确需新档位时在令牌区扩展并同步本表，不在使用处写 `alpha(..., 0.xx)` 或 `opacity: 0.xx`。
- 0/1 的 `opacity` 不属于半透明档位：仅用于显隐/淡入淡出动画（先例：搜索历史删除按钮 hover 淡入、拖放时欢迎页 `.dnd-blank` 淡出），不受上条约束。
- Python 侧（Cairo 绘制）使用 `ColorManager.OPACITY_DIM` 等常量，与 CSS 层级保持一致。

### 2.2 间距

统一使用六档标度（定义在 [style_gtk.css](../data/resources/style_gtk.css) 的 `--setzer-spacing-*`）：

| 档位 | 值 | 用途 |
|------|----|------|
| xs | 4px | 图标与文字微间隙、紧凑控件内部 |
| edge | 10px | 侧栏内容边缘线：工具栏/搜索框/列表行/内容卡片的水平边缘（Python 侧 `set_margin_*` 写 10 并注释 `--setzer-spacing-edge`） |
| sm | 6px | 弹层/菜单内部段间距、工具栏按钮间距、行内间隙 |
| md | 12px | 对话框内容外边距、卡片间距、内容块间距、列间距 |
| lg | 18px | 页面内大分区间距 |
| xl | 24px | 页面级外边距（欢迎页、引导页） |

- 对话框（`Adw.Dialog`）内容外边距统一 12。
- 弹层/菜单的外围留白由 Adw 自带 `popover > contents` 内边距提供，不要再给子容器叠加 `set_margin_*()`；内部段间距用 6，承载表单或卡片的弹层可用 12。
- 侧栏所有页面的内容水平边缘统一压到 edge 档 10px，与 `.sidebar-toolbar` / `.sidebar-search-bar` 同一条线；libadwaita 默认值与此冲突时覆写（`.navigation-sidebar` 行 6px、preferences clamp 12px 均已覆写，见 style_gtk.css 注释）。
- `Gtk.Box` 的 spacing 无法用 CSS 设置，Python 中只写 4/6/10/12/18/24，并在注释中标注对应档位。
- 布局间距只取上表档位。1–3px 及个别光学对齐值（如标题栏 47px 基线的 7px、pill 按钮水平内边距 10px——该值按圆角半径推导，与 edge 档无关、色块留白 9px）必须写注释说明对齐锚点；不得无注释地出现 7/8/9/11/13/14/16/20 等非标值。
- 同一界面内等价区块使用同一档位；嵌套容器不要重复叠加外边距，优先由父级统一控制。

## 3 排版与色彩

### 3.1 字号

只使用 libadwaita / GNOME HIG 的标准文字样式，不写 `font-size` / `font-weight` 数值。

| 场景 | 样式类 |
|------|--------|
| 正文 | `body` |
| 分组 / 窗口标题 | `heading` |
| 附属小字 | `caption` |
| 小号加粗 | `caption-heading` |
| 展示型标题 | `title-3` / `title-4` |
| 超大欢迎语 | `large-title` |
| 等宽文本 | `monospace` |
| 数字对齐 | `numeric` |

组件内部已自带排版，不重复覆盖：

| 组件 | 已有排版 |
|------|----------|
| `Adw.PreferencesGroup` 标题 | `heading` |
| `Adw.StatusPage`（`.compact`）标题 / 描述 | `title-2` / `body` |
| `Adw.ActionRow` 副标题 | 最小号 + dim |

- 禁止新增裸 `font-size` / `font-weight`；唯一例外是非文字字形尺寸（如 `.sidebar-open-dot` 的圆点），必须注释说明。
- 编辑器内容字体（用户可配置的文档/等宽字体）属于内容渲染，不受本节约束。

### 3.2 颜色层级

| 用途 | 语义色 |
|------|--------|
| 窗口 / 侧栏 / 对话框 | `@window_bg_color` |
| 编辑器 / 预览内容区 | `@view_bg_color` |
| 卡片 | `@card_bg_color` |
| 浮层 / 悬浮控件 | `@popover_bg_color` |

- 不新增自定义表面色。
- 次要文字只用 `dimmed`（55% 前景，libadwaita 1.7 起替代废弃的 `dim-label`）这一级；不再出现 0.6 / 0.85 / 0.9 等临时透明度或自造灰字。
- 分隔线、描边统一用 `@borders`；禁止 `#e3e3e3`、`rgba(128,128,128,…)`、`alpha(currentColor,…)` 这类固定灰值。
- 强调色与语义色（错误、警告、diff 红绿、批注高亮）不属于灰阶层级，可保留；但不要为浅色/深色主题各写一套固定灰值，主题自适应交给语义色。
- Cairo / 自定义绘制的浮层同样遵守：文字最多「正常 + 55%」两级，描边复用 `@borders`，不再叠加多套描边色。
- 改动颜色后需确认浅色/深色两套主题下的可读性与对比度。

### 3.3 图标

尺寸只用 HIG 允许的 16 / 32 / 64 / 128：

| 尺寸 | 用途 |
|------|------|
| 16 | 行内图标、按钮图标 |
| 32 | 需要更大视觉重量的独立图标 |
| 64 | 空状态等占位图形（紧凑侧栏；替代 48） |
| 128 | 超大展示场景 |

- `Adw.StatusPage` 官方默认 96，紧凑侧栏可降到 64，但不要用 48。
- 不使用 14 / 18 / 28 / 48 等中间尺寸（`set_pixel_size()` 与 `-gtk-icon-size` 都算），避免高 DPI 下模糊。
- UI 图标一律用 symbolic 图标，颜色交给语义色，不写死。
- 自定义 symbolic 图标从 `~/icon-library`（GNOME icon-library 克隆的 icon-dev-kit 集合）挑选，复制到 `data/resources/icons/hicolor/scalable/actions/`（gresource 自动打包）；缺图标先向用户确认来源，不自造。
- 符号面板网格中的符号字形（按原始宽高等比渲染）与悬停放大预览属内容渲染，不受本节尺寸档约束；悬停预览常量仍对齐档位取值（先例：`PREVIEW_PIXEL_SIZE = 64`）。

### 3.4 描边

默认不画描边：层级优先用表面底色差、间距和组件自带投影表达。只允许以下语义描边：

| 场景 | 做法 |
|------|------|
| 焦点 / 选中态 | 输入框聚焦内环、配色方案选中环 |
| 状态反馈 | 拖放高亮、错误色 |
| 浮在不可控内容上的控件 | PDF 页边、页码 pill、主题色块——去掉轮廓会与背景融合 |
| 边界兜底 | 确实无法用底色差区分时 |

- 不给卡片、列表、输入框勾 1px `@borders`；浮层用阴影而不是描边。
- PDF 页面边缘的 3px 纸边属于内容渲染，不受本节约束。

### 3.5 高对比度

- 自定义表面必须能在 `prefers-contrast: more` 下辨认边界；「去线」只针对普通主题。
- 兜底统一用 inset `box-shadow`（`inset 0 0 0 1px @borders` / `inset 0 -1px @borders`），不用 `border`，避免改变 47px 工具栏等精确高度。
- 高对比度下聚焦环提升为不透明 `@theme_selected_bg_color`；相关规则集中在 [style_gtk.css](../data/resources/style_gtk.css) 末尾与 [text_input_card.py](../setzer/helpers/text_input_card.py) 的 `_CSS`。

## 4 控件规范

### 4.1 输入框

| 类型 | 做法 |
|------|------|
| 原生 `Gtk.Entry` / `Gtk.SearchEntry` / `Gtk.SpinButton` / `Adw.EntryRow` | 沿用 Adwaita 内建外观，不覆盖背景，避免与高位主题、高对比模式冲突 |
| 自绘输入框 | 不画常驻描边；填充用 `@setzer_input_fill`（6% 前景色，浅色主题下略深、深色主题下略亮），只有聚焦时才用 `@setzer_focus_ring` 画 2px 内环 |

自绘输入框清单：

| 场景 | 位置 |
|------|------|
| 多行文本输入卡片（AI prompt、批注、只读代码预览） | [text_input_card.py](../setzer/helpers/text_input_card.py) |
| 预览页码框 | `.preview-page-entry` |
| Git commit message | `.git-commit-message` |

- 禁止给输入框单独指定其他灰底（固定 hex、自造灰值、按明暗主题各写一套）；需要边界时优先加深填充，而不是加描边。

### 4.2 窗口失焦（backdrop）

失焦视觉全部交给 libadwaita 的 `:backdrop`，全应用不写自定义 `:backdrop` 规则。

| 区域 | 失焦行为（官方） |
|------|------------------|
| `Adw.HeaderBar` / `Adw.ToolbarView` 顶栏 | 内容 `opacity: 0.5`，底色切到 backdrop 变体 |
| `.sidebar-pane` | 背景切到 `sidebar_backdrop_color` |
| 非 `.inline` 的 tabbar、searchbar、actionbar、banner | 按官方规则变暗 |
| `tabbar.inline` | 明确不变暗 |
| 内容区、编辑器卡片、快捷工具栏、列表选中态、输入框聚焦环 | 保持原样（官方行为） |

- 内容区与选中态不变暗是官方行为，不要额外补「变暗」。
- 主窗口标题栏是透明的：`headerbar { background-color: transparent }`。两个主题下 `headerbar_backdrop_color` 恰好等于 `window_bg_color`，因此背景过渡不可见，失焦只剩内容 50% 变暗。改标题栏底色时必须同时确认失焦态。
- 新增窗口优先用 `Adw.HeaderBar` + `Adw.ToolbarView`；自绘顶栏不会被自动变暗，也不要把普通内容伪装成 headerbar 来获得失焦效果。

### 4.3 无障碍名称

- 纯图标按钮除 tooltip 外必须显式给出无障碍名称：`widget.update_property([Gtk.AccessibleProperty.LABEL], [_('…')])`，字符串复用同一 tooltip 文案，避免新增 msgid。
- 自绘可点击控件（如文档结构展开器）必须保证同一操作有键盘路径（方向键展开/收起），不能只依赖鼠标手势。
- 工具图标开关（`Gtk.ToggleButton`）同样属于图标按钮，必须给出无障碍名称，并用按下态表达「开」。
- 按钮不得用 `set_can_focus(False)` 关闭键盘可达；要避免点击抢焦点用 `set_focus_on_click(False)`，两者作用不同（先例：构建日志弹窗按钮显式 `set_can_focus(True)`）。

### 4.4 选择控件语义

| 场景 | 控件 | 约束 |
|------|------|------|
| 立即生效的二元设置 | `Adw.SwitchRow`（列表/弹窗内）、`Gtk.Switch`（工具栏） | 切换即生效，无确认步骤 |
| 2–5 个互斥可见选项 | 单选组：`Gtk.CheckButton` + `set_group()`，或 `Adw.ComboRow` | 选项一屏可见时优先可见单选 |
| 6 个及以上互斥选项 | `Adw.ComboRow` | 避免一长串单选行 |
| 视图 / 面板 / 模式切换 | `Adw.ToggleGroup` | 恒有一档激活，规则见 6.3 |
| 工具栏独立开关 | `Gtk.ToggleButton`（flat + symbolic 图标） | 配 tooltip 与无障碍 LABEL，见 4.3 |
| 多选列表 | 行前缀 `Gtk.CheckButton`（`Adw.ActionRow.add_prefix`） | 行内不要改用开关 |
| 单选 / 主从列表 | `Gtk.ListBox` 的 `SelectionMode.SINGLE` / `BROWSE` | 不再叠复选框 |
| 纯展示列表 | `SelectionMode.NONE` | 行内不放交互控件 |
| 菜单中的独立开关项 | 布尔 `Gio.SimpleAction`（stateful） | GTK 自动渲染为对勾，不要手写符号 |
| 菜单中的互斥项 | 非布尔 stateful action + target | GTK 自动渲染为单选圆点 |
| 一次性动作 | 普通 action | 不加任何选中指示符 |

禁止：

- 用开关表达多选，或用开关表达需要确认后才生效的选项（对话框例外见下）。
- 用互斥的 `Gtk.ToggleButton` 组替代 `Adw.ToggleGroup`。
- 在菜单里用两条文案相反的普通项表达同一开关（如 Pin / Unpin），应改为单一勾选项。
- 手动给菜单项拼 `✓`、`•` 等字符；选中态交给 stateful action。
- 在同一个列表行里同时放复选框和开关表达同一种选择。

例外（保持现状，改动前先更新本节）：

| 场景 | 位置 | 理由 |
|------|------|------|
| 确认式对话框（Insert / Save / Apply）中的二元选项仍用 `Adw.SwitchRow` | `insert_image`、`insert_table`、`document_wizard`、`project_build_configuration`、`document_properties`、`include_bibtex_file` | HIG 理论建议「需确认才生效」用勾选框；项目统一使用 Adw 行组件，与偏好页样式一致 |
| 预览「适合宽度」用 `Gtk.ToggleButton` 表示四档 fit 模式中的一档 | [preview_panel_viewgtk.py](../setzer/workspace/preview_panel/preview_panel_viewgtk.py) | 常用档位快捷入口 + 缩放菜单完整档位；presenter 仅在 `fit_to_width` 时置按下 |
| 构建日志过滤器用 `Gtk.ToggleButton` + 手写 popover | [build_log_dialog_viewgtk.py](../setzer/dialogs/build_log/build_log_dialog_viewgtk.py) | 规避 `Gtk.MenuButton` 在 `Adw.Dialog` 内无法二次点击关闭的已知问题 |
| 主题选择器用 `Gtk.CheckButton` + `set_group()` 模拟单选并 CSS 重绘为色块 | [theme_selector.py](../setzer/popovers/theme_selector/theme_selector.py) | 自定义主题色块需要 CheckButton 的选中态；已设 `set_focus_on_click(False)` |

## 5 布局与容器

### 5.1 通用布局

- 圆角、间距、按钮尺寸使用组件默认值，不手写固定数值；同一界面内等价区块的间距保持一致。
- 自绘输入类控件的圆角**逐处对齐相邻组件**（Adwaita 默认 button/entry 9px、boxed-list 卡片 12px），不做全局统一覆盖；主题默认值不显式覆盖。
- 纯说明文字不要包在卡片/带底框的容器里，避免制造可交互的错觉。
- 文本密集区不引入带边框的普通按钮；轻量入口用 flat 按钮 + `dimmed`。
- 按钮尺寸与交互反馈遵循 Adwaita 默认规范，不手工硬编码尺寸或关闭原生点击动画。

### 5.2 空状态

面板/列表无内容时用 `Adw.StatusPage`（icon + 标题，可选说明文字），不留白板。

### 5.3 文本密集区的轻量入口

状态栏等文本密集区域的可点击入口用 flat `Gtk.Button` 包 `caption` + `dimmed` 的子 label（先例：状态栏构建结果指示），CSS 上收敛按钮内边距与相邻 label 的视觉密度一致（见 `.editor-statusbar > button`），不引入带边框的普通按钮。

## 6 弹窗与浮层

### 6.1 弹窗组件

使用 `Adw.Dialog`、`Adw.PreferencesDialog` 等弹窗组件时，禁止同时保留系统默认右上角关闭按钮（右上角 X）和手动添加的 Close/Cancel 按钮。创建弹窗时必须调用 `set_show_end_title_buttons(False)` 禁用默认关闭按钮，仅保留单一手动关闭入口。

例外：无手动关闭按钮的弹窗不调用本 API——默认 X 就是唯一的关闭入口，禁用它会移除唯一的入口（先例：构建日志、Add / Remove Packages）。

### 6.2 HeaderBar 按钮布局

| 位置 | 控件 | 约束 |
|------|------|------|
| 左侧 | Cancel / Close | 文字按钮，最多 2 个文字按钮中的 1 个 |
| 中间 | 次要操作 | 统一降级为 flat 图标按钮并配套 tooltip |
| 右侧 | 主确认按钮 | 带 `suggested-action` 样式 |

### 6.3 分段控件（Adw.ToggleGroup）

面板/模式切换统一用 `Adw.ToggleGroup` 分段控件（先例：预览/帮助工具栏、侧栏面板切换），不用互斥 `Gtk.ToggleButton` 组。

- 恒有一档激活。`get_active()` 是 guint 索引，未选中时返回 `G_MAXUINT` 而非负数，兜底判断必须写 `active >= group.get_n_toggles()`，不要写 `active < 0`（永不成立）。
- 程序化 `set_active` 触发的回调，用 `_syncing_*` 标志位压掉，防止同步循环。

### 6.4 偏好项行组件

偏好设置页统一用 Adw 行组件，不用裸 `Gtk.Switch` / `Gtk.ComboBox` 拼装：

| 设置类型 | 行组件 |
|----------|--------|
| 开关 | `Adw.SwitchRow` |
| 单选 / 下拉 | `Adw.ComboRow` |
| 数值 | `Adw.SpinRow` |
| 文本输入 | `Adw.EntryRow` |

确认式对话框（Insert / Save / Apply）同样使用上表行组件；这是 4.4 登记的例外。

## 7 轻反馈与打断（非必要不打扰）

打扰阶梯（从轻到重）：

| 层级 | 组件 | 适用 |
|------|------|------|
| 最轻 | 状态栏 / 行内标记 | 界面本身已能表达结果 |
| 轻 | `Adw.Banner` | 需要持续可见但不必打断的提示 |
| 中 | `Adw.Toast` | 非模态的结果反馈（复制成功、已加入信任目录等） |
| 重 | `Adw.AlertDialog` | 需要用户做出决定的场景，遵守 6.1 |
| 最重 | 模态弹窗 | 不可逆 / 有后果的操作确认、需要立即处置的错误 |

- 反馈默认落在能表达清楚的最轻一档。
- **Banner 与 Toast 的判据**：提示消失后用户会不会失去关键状态或唯一入口？会 → Banner（先例：外部 PDF 变更提醒——提示没了用户就不知道自己在看旧 PDF）；不会、入口已由持久控件表达（如分段档位与结果同生同灭）→ Toast + 动作按钮（[toast.py](../setzer/helpers/toast.py) 支持 `button_label` / `on_button_clicked`）。
- **编程式打开文档一律静默**（synctex 跳转、侧栏 include、结构树、build log、AI 映射等）：`open_document_by_filename` 保持默认 `notify_if_already_open=False`，只有用户显式发起的打开（文件选择器、最近文档）才传 True。
- **例行成功不提示**：界面本身已可见的结果不再另发 toast——保存后标题/标签已变化、导出完成即文件选择器关闭、面板切换本身可见、构建结果有按钮区常驻的 Success!/Failed(N)! 文字。
- **重复事件必须收敛**：同一原因反复触发的提示走 [toast.py](../setzer/helpers/toast.py) 的 key 节流（先例：预览失败 8s、换行环绕提示）；自动构建循环内的成功/失败结果一律静默（输入中间态不是事件），只有用户主动构建的失败才发 toast；一次性告警用 one-shot 标志位（先例：`auto_build._untitled_warned`、`workspace._persistence_warning_shown`）。
- **打断的边界**：只有不可逆/有后果的操作确认、需要用户做选择、需要立即处置的错误才允许模态打断；被动结果（打印失败、保存失败等）走 toast，异常弹窗须节流防连环堆叠（先例：`exception_handler` 30s 窗口）。

## 8 工程约定

### 8.1 翻译调用的时机

`_` / `ngettext` 由 setzer.in 启动时注入 builtins。类体在模块导入时求值，过早调用 `_` 会 NameError——翻译调用必须推迟到 `_build_ui()` 等运行时路径（先例：theme_selector 的 THEME_MODES 存原文）。gi-free 且需单测的模块，用 builtins 委托 + 回退的包装函数（先例：[build_status_text.py](../setzer/helpers/build_status_text.py)，其复数包装 `_ngettext` 已在 po/meson.build 声明 keyword）。

- **丢弃式解包会破坏注入**：任何模块里写 `_, x = ...` 会把 str 写进模块全局 `_`、遮蔽 builtins 查找，此后该模块所有 `_(…)` 报 `TypeError: 'str' object is not callable`。丢弃项必须用具名变量接（任何名为 `_` 的赋值都会中招）。

### 8.2 动画

所有 Stack / Revealer 过渡统一经 [animation_prefs.py](../setzer/helpers/animation_prefs.py) 的 `set_transition()` 设置，**禁止裸调 `set_transition_type`**——CI 的 AST 检查会拦截（[test_animation_prefs.py](../tests/python/test_animation_prefs.py)），保证「减弱动画」偏好一处生效。

- `RevealerTransitionType.NONE` 收起时仍按子控件全高申请尺寸；需要完全收起用 Stack `NONE` + duration 0，Revealer 保留滑动 type + duration 0。

### 8.3 界面文案

- 省略号统一用 `…`，不用三个英文句点 `...`。
