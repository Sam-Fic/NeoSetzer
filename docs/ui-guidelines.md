# UI 规范

## 弹窗组件

在使用 Adw.Dialog、Adw.PreferencesDialog 等弹窗组件时，禁止同时保留系统默认右上角关闭按钮（右上角 X）和手动添加的 Close/Cancel 按钮。创建弹窗时必须调用 `set_show_end_title_buttons(False)` 禁用默认关闭按钮，仅保留单一手动关闭入口。

### HeaderBar 按钮布局

- 文字按钮最多设置 2 个：左侧 Cancel/Close + 右侧带 `suggested-action` 样式的主确认按钮
- 中间的次要操作统一降级为 `flat` 图标按钮并配套 tooltip 提示
