# 标签条拖拽排序失效 —— Bug 交接总结

> 状态：**未解决**，转交他人排查。当前 master 处于半成品状态，此功能**尚未修好**。

## 一、现象

在多文档模式下（≥2 个打开的文档），拖动 `Adw.TabBar` 上的标签页进行排序时，
**只触发"切换激活标签"，排序不生效**（"切换优先"）。

## 二、环境与架构

- 应用：NeoSetzer（Setzer 分叉），Python + PyGObject + GTK4 + libadwaita
- 标签条实现：v2 native 方案
  - `setzer/workspace/workspace_viewgtk.py`：`document_stack = Adw.TabView()` +
    `Adw.TabBar(view=document_stack, autohide=True)`
  - 控件树：
    ```
    document_stack_overlay (Gtk.Overlay)            ← 祖先
      └─ document_stack_wrapper (Gtk.Box VERTICAL)  ← child（set_margin_top(46)）
           ├─ document_tabs (Adw.TabBar)
           ├─ shortcutsbar
           └─ document_stack (Adw.TabView)
    overlay 层：headerbar.widget (valign=START), drop_highlight
    ```
- 全工程**没有任何自定义 GestureDrag / DragSource** 挂在 tab 区。
  `Adw.TabBar` 的拖拽排序完全依赖 libadwaita 原生实现
  （libadwaita 中 TabBar 拖拽排序走 `Gtk.DragSource` DnD + `Adw.TabView` 内置 reorder handler）。

## 三、已尝试的修复（全部失效）

| commit | 内容 | 结果 |
|---|---|---|
| `33e74a16` | `on_drag_accept` 对非文件拖放返回 False | 失效 |
| `7b634b42` | `_drag_has_files` 改用 `drop.get_formats()` 判断文件（正确方向） | 失效；且引入 `kwargs` 未定义 NameError |
| `877b74af` | 修复 `kwargs` bug（`_drag_has_files(self, target, drop=None)`） | 仍失效（用户实测"依旧无用"） |

## 四、关键代码位置

`setzer/workspace/workspace_viewgtk.py`：
- `_make_drop_target()` `:398-414` — 两个 CAPTURE 阶段 `Gtk.DropTarget`（文件拖放用），
  挂在 `welcome_overlay` 与 `document_stack_overlay` 上
- `_drag_has_files(target, drop=None)` `:609` — 判断当前拖放是否为文件
- `on_drag_enter` `:656` / `on_drag_motion` `:671` / `on_drag_accept` `:684` / `on_drop` `:694`
- headerbar 浮层 `add_overlay` `:244-245`；wrapper `set_margin_top` `:235` 与 `do_size_allocate` `:740-748`

`setzer/workspace/workspace_presenter.py`：
- `_on_tab_view_selected_page_changed` — TabView selected → `workspace.set_active_document`

## 五、给接手者的建议（别再盲猜）

1. **先做确定性诊断**，别继续改 DropTarget：
   在 `on_drag_enter` / `on_drag_accept` 里临时打日志（print 拖拽时的
   `drop.get_formats()` 内容），确认 TabBar 的拖拽排序到底**进不进**这个 handler。
   - 若**进**：看 `get_formats()` 返回什么——若不含 `Gio.File`，说明判定已放行，
     问题在别处（见下）。
   - 若**不进**：说明 DropTarget 假设根本不成立，TabBar 排序不是 DnD，或事件流不经过该 overlay。
2. **候选根因（未验证）**：
   - headerbar 浮层 `valign=START` + wrapper `margin_top(46)` 是否仍与 TabBar 重叠、
     遮挡或吃掉事件。
   - presenter 在 press 时同步 `set_active_document`（`_deferred_post_activate` 里可能有
     较重操作）打断正在进行的拖拽。
   - libadwaita 自身行为：拖未激活 tab 时 `drag-begin` 会先选中该 tab（"切换优先"可能是
     标准行为），真正问题是排序 handler 没接管 —— 需确认 Adw.TabView reorder 是否被破坏。
3. **临时排除法**：注释掉 `document_stack_overlay` 上的 DropTarget，看排序是否恢复。
   若恢复 → 确认是 DropTarget 劫持，再修判定；若不恢复 → 彻底排除 DropTarget，查别处。

## 六、其它已知情况

- 该 DropTarget 的职责是"拖文件进窗口打开"（`on_drop` 打开白名单文件），
  修复时不能破坏此功能。
- `pytest tests/python` 全套 552 项通过（该 GUI 交互无测试覆盖）。
