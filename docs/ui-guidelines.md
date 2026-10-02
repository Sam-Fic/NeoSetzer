# UI 规范

## 弹窗组件

在使用 Adw.Dialog、Adw.PreferencesDialog 等弹窗组件时，禁止同时保留系统默认右上角关闭按钮（右上角 X）和手动添加的 Close/Cancel 按钮。创建弹窗时必须调用 `set_show_end_title_buttons(False)` 禁用默认关闭按钮，仅保留单一手动关闭入口。

### HeaderBar 按钮布局

- 文字按钮最多设置 2 个：左侧 Cancel/Close + 右侧带 `suggested-action` 样式的主确认按钮
- 中间的次要操作统一降级为 `flat` 图标按钮并配套 tooltip 提示

## 分段控件（Adw.ToggleGroup）

面板/模式切换统一用 Adw.ToggleGroup 分段控件（先例：预览/帮助工具栏、侧栏面板切换），不用互斥 Gtk.ToggleButton 组。约定：

- 恒有一档激活。`get_active()` 是 guint 索引，未选中时返回 `G_MAXUINT` 而非负数，兜底判断必须写 `active >= group.get_n_toggles()`，不要写 `active < 0`（永不成立）。
- 程序化 `set_active` 触发的回调，用 `_syncing_*` 标志位压掉，防止同步循环。

## 轻反馈与打断

- 非模态的结果反馈（复制成功、已加入信任目录等）统一用 Adw.Toast，不弹对话框。
- 需要用户做出决定的场景才用 Adw.AlertDialog，并遵守上方弹窗组件规则。

## 偏好项行组件

偏好设置页统一用 Adw 行组件：开关用 Adw.SwitchRow、单选/下拉用 Adw.ComboRow、数值用 Adw.SpinRow、文本输入用 Adw.EntryRow，不用裸 Gtk.Switch / Gtk.ComboBox 拼装。

## 空状态

面板/列表无内容时用 Adw.StatusPage（icon + 标题，可选说明文字），不留白板。

## 文本密集区的轻量入口

状态栏等文本密集区域的可点击入口用 flat Gtk.Button 包 caption + dim-label 的子 label（先例：状态栏构建结果指示），CSS 上收敛按钮内边距与相邻 label 的视觉密度一致（见 `.editor-statusbar > button`），不引入带边框的普通按钮。

## 翻译调用的时机

`_` / `ngettext` 由 setzer.in 启动时注入 builtins。类体在模块导入时求值，过早调用 `_` 会 NameError——翻译调用必须推迟到 `_build_ui()` 等运行时路径（先例：theme_selector 的 THEME_MODES 存原文）。gi-free 且需单测的模块，用 builtins 委托 + 回退的包装函数（先例：`setzer/helpers/build_status_text.py`，其复数包装 `_ngettext` 已在 po/meson.build 声明 keyword）。
