# GTK4 / libadwaita 实测坑位

本文记录本项目在 GTK 4.22 / PyGObject / libadwaita 1.9 上实测踩过的 API 坑位：每条给出症状、正确写法与代码先例。「什么时候用哪种控件」的设计决策见 [ui-guidelines](../ui-guidelines.md)，本文只回答「怎么写才不会崩」。

| 章节 | 覆盖内容 |
|------|----------|
| 1 弹层与通知 | Banner、ToastOverlay |
| 2 行与分段控件 | ActionRow、Toggle / ToggleGroup、图标名 |
| 3 顶栏与容器 | HeaderBar reparent、ScrolledWindow 中的卡片 |
| 4 文本与绘制 | TextBuffer / TextTag、滚动判稳、离屏截图 |
| 5 事件与动画 | 手势、FrameClock、过渡动画、绑定可用性 |
| 6 无显示环境与测试 | headless SIGSEGV、守卫判据、属性探查 |

## 1 弹层与通知

### 1.1 Adw.Banner 没有内置关闭键

属性只有 `button-label` / `revealed` / `title` / `use-markup` 等，没有 close 或 `show-close-button`。想让用户能收掉，只能借 `button-label` 挂一个动作。

- `notify::revealed` 只在程序侧置 `False` 时发出——把它当「用户点了 ✕」永远不会由用户触发。
- 需要可关闭的持续提示，重新考虑用 Toast + 动作按钮（判据见 [ui-guidelines](../ui-guidelines.md) 第 7 章）。

### 1.2 ToastOverlay 包裹必须由内向外一次到位

`Adw.ToastOverlay.set_child()` 断言 child 无 parent，失败只打 CRITICAL、**静默拒绝**——界面渲染成空的 toast_overlay，headerbar/表单全丢，却没有任何异常可捕获。

正确嵌套顺序：

```python
content → toast_overlay → toolbar_view → dialog.set_child(...)
```

给已接线的弹窗「补包一层 ToastOverlay」时最容易踩：先把 `set_child(content)` 写在了 `toolbar_view.set_content(content)` 之后。

## 2 行与分段控件

### 2.1 Adw.PreferencesRow.activatable 默认 False

`Adw.ActionRow`（及受限行）不显式 `set_activatable(True)` 时，点击整行毫无反应，`activated` 信号永不发出。症状是「功能写好了但点不出来」，极难从代码看出。先例：[structure_widget.py](../../setzer/workspace/sidebar/document_structure_page/structure_widget.py)。

### 2.2 Adw.Toggle 是纯 GObject，不是 Widget

- 没有 `set_visible`——隐藏只能从 `ToggleGroup` 里 `remove`；置灰可用 `enabled` 属性。
- `Adw.ToggleGroup` 只有 `add` / `remove` / `remove_all`，**没有 `insert`**：往中间插档位只能 `remove_all` 后按序重建，普通 `add` 一律追加到末尾。动态档位通常只能接受「排在最后」。

### 2.3 动态增删档位必须按 name 定位

索引随档位出现/消失漂移（`set_active(2)` 原本指 help，多一档就指到别处）：

- `get_active()` 无选中返回 `G_MAXUINT`（判界写 `>= get_n_toggles()`，不要写 `< 0`）；
- `get_active_name()` 无选中返回 `None`；
- `set_active_name()` 对未知名字打 CRITICAL 并拒绝赋值——先用 `get_toggle_by_name(name) is None` 降级，再 `set_active_name`。

同步回调里用 `_syncing_*` 标志位压掉程序化触发的回环（设计规范见 [ui-guidelines](../ui-guidelines.md) 6.3）。

### 2.4 图标名先查主题

图标名不在 Adwaita 主题里时会退化成破图标（如 `changes-prevent-symbolic` 只存在于 breeze / HighContrast）。双栏/对照语义用 `view-dual-symbolic`（Adwaita `symbolic/actions`）。查法：

```bash
find /usr/share/icons/Adwaita -name '<name>-symbolic.svg'
```

## 3 顶栏与容器

### 3.1 Adw.HeaderBar.pack_end 是栈式插入，且断言 child 无父

后 `pack_end` 的在更左边。把自有控件 reparent 进标题栏必须先 `unparent()`，收回时同样用 `unparent()`——父是 headerbar 内部 end box，`headerbar.remove()` 无效。需要与侧栏左→右顺序一致时，**反向遍历** pack。先例：[agent_terminal_window.py](../../setzer/workspace/agent_terminal_window/agent_terminal_window.py)。

### 3.2 ScrolledWindow 里的 boxed-list 卡片

viewport 会把 child 拉伸到视口高度，背景画在 ListBox 上就会一路铺到底：

- 给 ListBox 设 `set_valign(Gtk.Align.START)` 才只占内容高度（scroller 仍 vexpand）。
- **不要**给装 boxed-list 的 ScrolledWindow 设 `set_overflow(Gtk.Overflow.HIDDEN)` 做「圆角卡片蒙版」：overflow 裁剪子级**全部绘制，包括 `box-shadow`**。
- 阴影被裁的真正原因：`Gtk.ScrolledWindow` 内部包一层 `Gtk.Viewport`，视口把子控件裁到自己的矩形（滚动实现必需，与 `set_overflow` 无关）。libadwaita 的卡片投影（`0 0 0 1px …3%, 0 1px 3px 1px …7%, 0 2px 6px 2px …3%`，左右外扩约 5px）整块落在视口外，只要卡片与视口**同宽**，左右/上边就会被切。
- 官方做法（`Adw.PreferencesPage`）：`preferencespage > scrolledwindow > viewport > clamp > box { margin: 24px 12px }`——**边距留在视口内部**，卡片比视口窄，投影落在视口内的空白里。自己搭滚动列表要照这个结构：margin 加在 ListBox / 内容容器上（viewport 内）；加在 scroller 外面完全无效，视口照样把卡片投影裁掉。
- 不要试图把投影搬到 scroller 上（`overflow` 裁剪、`box-shadow` 搬运、按溢出切类都试过）：溢出时滚动区上下边是视口**裁切内容**的位置，不是卡片边界，在那里画阴影等于谎报「卡片到这儿结束」。投影归 `list.boxed-list` 自己，滚动区不画任何阴影。
- 先例：Git 侧栏 [git_section_viewgtk.py](../../setzer/workspace/sidebar/git/git_section_viewgtk.py) 给两个列表加 `.git-card-list`（ListBox 左右 edge 边距，margin 在 viewport 内），同栏其它控件由 `.git-section-box > *` 补同样的边距，`GitPageView.content_box` 的横向 margin 归零——三者合起来仍是全侧栏 10px 边缘线。
- 代价：视口是方的，列表溢出后滚动的行会走到视口方角处（卡片 12px 圆角只在首尾生效）；卡片左右阴影完整，上下只在未溢出（卡片边界在视口内）时可见。

## 4 文本与绘制

### 4.1 get_iter_at_line* 返回 (ok, iter) 元组

`Gtk.TextBuffer.get_iter_at_line` / `get_iter_at_line_offset` / `get_iter_at_line_index` 的 PyGObject 绑定保留了 GTK 的 out 参数，返回 `(ok, iter)` 元组，不是迭代器本身——对返回值直接调 `copy()` / `forward_to_line_end()` 会 AttributeError，必须解包。老代码里若没解包，说明那条路径从没被执行过。

### 4.2 select_range 的光标落在第一个参数

`buffer.select_range(ins, bound)` 的 insert mark 落在**第一个**参数上——要让光标停在选区末尾须 `select_range(end, start)`。先例：[actions.py](../../setzer/workspace/actions/actions.py)。

### 4.3 TextTag 的 foreground 只认具体颜色

`'alpha(currentColor, 0.45)'` 这类 CSS 函数会被 GTK 打 WARNING 并忽略；写死 `#8a8a8a` 之类即可（`background` 支持 `rgba(...)`）。

### 4.4 scroll_to_mark 是渐进生效的，idle 判稳会假稳定

GTK4 `TextView.scroll_to_mark` 的对齐跨多帧逐步逼近（实测滚动值继续漂移 ~100ms / 54px）：

- 低优先级 `GLib.idle_add` 轮询不可靠：idle 在同一帧内可连跑多次，「连续两次采样相等」在漂移开始前就假稳定；按 idle 次数计的上限（如 attempt >= 120）几十 ms 就烧完。
- 正确做法：`widget.add_tick_callback`（真帧节拍）+ 判稳标准「锚点矩形（buffer→widget 坐标）连续 N 帧不变」，**矩形一变即清零计数**（清零制，不是固定帧数），外加目标落入可见区检查；重锚同理，矩形变就 `set_pointing_to` 并清零，连续 ~20 帧不变才停，3s 兜底。

先例：[actions.py](../../setzer/workspace/actions/actions.py) 的 `_ai_annotate_when_view_settled` / `_ai_reanchor_annotation_popover`。

### 4.5 离屏截图

```python
paintable = Gtk.WidgetPaintable.new(widget)
# snapshot → get_renderer().render_texture(node) → save_to_png(...)
```

注意离屏渲染时 `@card_bg_color` 等语义色取浅色值，别据此判断暗色主题观感。

## 5 事件与动画

### 5.1 手势与修饰键

- 同一手势组内 `GestureDrag` 的 `drag-begin` 先于 `GestureClick` 的 `pressed` 触发；两者可共存，按下时都会收到事件（先例：[gutter.py](../../setzer/document/gutter/gutter.py) 的拖动选行）。
- PyGObject 没有 `Gdk.Event.get_current_event()`；读修饰键用 `controller.get_current_event()` + `event.get_modifier_state()`。

### 5.2 FrameClock 处理器泄漏

`Gdk.FrameClock::after-paint` 处理器只收 `clock` 一个参数，且 `return False` **不会**断开连接（每帧重入，泄漏）。要自动注销用 `widget.add_tick_callback(cb)`（签名 `(widget, frame_clock)`）。

### 5.3 过渡动画统一走 animation_prefs

所有 Stack / Revealer 过渡经 [animation_prefs.py](../../setzer/helpers/animation_prefs.py) 的 `set_transition()` 设置，**禁止裸调 `set_transition_type`**——CI 的 AST 检查会拦截（[test_animation_prefs.py](../../tests/python/test_animation_prefs.py)），保证「减弱动画」偏好一处生效。

注意：`RevealerTransitionType.NONE` 收起时仍按子控件全高申请尺寸。需要完全收起：Stack 用 `NONE` + duration 0；Revealer 保留滑动 type + duration 0。

### 5.4 系统 GTK 版本号 ≠ PyGObject 绑定可用性

`GtkWidget.set_height_request`（GTK 4.12+）在当前 PyGObject 绑定上不存在（GTK 库 4.22 也没有），调用即 AttributeError。等价写法 `widget.set_size_request(-1, 72)`。**新 widget 方法用前先 `hasattr` 验证。**

## 6 无显示环境与测试

### 6.1 无显示时实例化 Python widget 子类会 SIGSEGV

GTK ≥ 4.14 的无显示环境下 `Gtk.init()` **成功**、`Gdk.Display.get_default()` 返回 `None`、`Gtk.is_initialized()` 为 True，但实例化任何 Python 定义的 GTK widget 子类（`class MyBox(Gtk.Box)`）会在 libgtk 内部 SIGSEGV（`g_object_new → g_type_create_instance` 链），不可捕获。纯 `Gtk.Button` 等非 Python 子类实例化不崩——容易误判成业务代码 bug。

- 测试守卫必须写成「`Gtk.init()` 成功 **且** `Gdk.Display.get_default() is not None`」（先例：[test_agent_terminal_headerbar.py](../../tests/python/test_agent_terminal_headerbar.py)）。CI 无显示器，这类用例跳过属预期。
- 本地复现 CI 行为：`env -u DISPLAY -u WAYLAND_DISPLAY meson test -C builddir`。

### 6.2 无显示环境可以探查 API

真构造 Banner / ToggleGroup 等 widget 需要显示；无显示环境可 `Adw.Banner.list_properties()` / `dir(Adw.ToggleGroup)` 列属性做 API 断言，不需要起显示。UI 目检的替代流程见 [ui-verification.md](ui-verification.md)。

## 相关文档

- [ui-guidelines.md](../ui-guidelines.md) — 设计规范（令牌、控件语义、打扰阶梯）
- [ui-verification.md](ui-verification.md) — UI 目检流程（Xvfb / niri）
- [CONTRIBUTING.md](../../CONTRIBUTING.md) — 测试注册与无显示约定
