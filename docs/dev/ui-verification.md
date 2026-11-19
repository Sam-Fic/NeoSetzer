# UI 目检与端到端验证

单元测试之外的外观与交互确认流程，按环境二选一：CI 同构的 **Xvfb headless**（流程 A）或本机 **niri 桌面**直接终验（流程 B）。

| 章节 | 覆盖内容 |
|------|----------|
| 1 前置条件 | 启动入口、干净配置、深色模式 |
| 2 流程 A：Xvfb headless | 无显示截图与局限 |
| 3 流程 B：本机 niri 桌面 | 截图、程序化驱动、会话恢复 |
| 4 共同纪律 | 进程管理、自动化局限、像素级验证 |

## 1 前置条件

### 1.1 必须用 dev 启动器

GUI 验证一律用 `./scripts/dev/setzer.dev`：它把仓库源码注入 `sys.path` 并指向源码内资源（改 CSS 即时生效，无需 ninja）。其它入口会静默导错包：

- `/usr/lib/python3/dist-packages` 装有系统级旧版 Setzer；`python -I`（隔离模式）会忽略 `PYTHONPATH` 直接落到旧版；
- 仓库内 `build/setzer` 不注入 `sys.path`，同样落到系统旧版——表现为「对象没有某方法/属性」的诡异 AttributeError，且毫无报错。

临时脚本需在文件头部显式 `sys.path.insert(0, <仓库根>)` 再 import。详见 [CONTRIBUTING.md](../../CONTRIBUTING.md)「开发环境」。

### 1.2 干净配置目录

`XDG_CONFIG_HOME` 指向**全新的**临时目录——配置目录用一次就被会话态污染，复用会导致首次引导/会话恢复不可控。需要特定初始状态时预种子 `<config>/setzer/settings.json`（部分键安全，其余按 defaults 回退），例如：

```json
{"window_state": {"show_document_structure": true, "width": 1600, "height": 1000}}
```

### 1.3 深色模式

**禁止 `GTK_THEME=Adwaita-dark`**：libadwaita 应用会部分退回 GTK 自带主题，`.accent` 着色等样式表特性失效，截出来的颜色不可信。深色模式用（需要 dbus session）：

```bash
gsettings set org.gnome.desktop.interface color-scheme prefer-dark
```

## 2 流程 A：Xvfb headless

```bash
dbus-run-session -- bash -c '
  Xvfb :99 -screen 0 1400x900x24 & sleep 2
  export GDK_BACKEND=x11 DISPLAY=:99
  export XDG_CONFIG_HOME=/tmp/setzer-verify-config
  ./scripts/dev/setzer.dev >/tmp/verify-run.log 2>&1 &
  sleep 5
  # …交互/截图脚本…
'
```

截图：

```bash
shot() { xwd -root -silent > f.xwd && ffmpeg -y -loglevel error -i f.xwd f.png; }
```

| 要点 | 说明 |
|------|------|
| `GDK_BACKEND=x11` 必设 | 不设则 GTK4 选 Wayland 后端直接渲染到本机 niri 桌面，Xvfb 的 root 无任何窗口、截图全黑 |
| 无 WM | `xdotool windowactivate` 报错可忽略；合成点击不进窗口时改用绝对坐标 `xdotool mousemove x y; click 1` |
| XTEST 合成点击不可靠 | 对 GTK4 按钮行/列表行多次实测无效（hover 无高亮、`getmouselocation` 报 window:0）——截图验证可靠，**交互验证不可依赖** |
| 自动化点击不稳定 | 重开弹窗等操作多次不生效属自动化问题，别把自动化失败当应用回归 |
| 找窗口 | `xdotool search --onlyvisible --name "NeoSetzer"`（标题形如 `main.tex — NeoSetzer`） |

交互路径的验证策略：截图 + 代码推演为主，真实点击留给用户在自己的会话里确认。

## 3 流程 B：本机 niri 桌面

不用 Xvfb，直接在用户的 niri 会话里终验，三件套：

**1. 截图**（`screenshot-window` 拍的是**焦点窗口**，必须先 focus，否则拍到的是终端自己）：

```bash
niri msg action focus-window --id <N>
niri msg action screenshot-window --path /tmp/shot.png
# 窗口 id：niri msg windows | grep -B4 "org.cvfosammmm.Setzer"
```

**2. 程序化驱动 UI**（窗口级 GAction 全部可用，见 `shortcut_accels.py` 注册的 `win.*` 动作）：

```bash
gdbus call --session --dest org.cvfosammmm.Setzer \
  --object-path /org/cvfosammmm/Setzer/window/1 \
  --method org.gtk.Actions.Activate <action> "[]" "{}"
# 返回 () 即成功
```

**3. 会话恢复路径**：直接改 `~/.config/setzer/settings.json` 的 `window_state`（`show_git`、`sidebar_page` 等）再重启应用，比点按钮可靠。

## 4 共同纪律

### 4.1 杀进程与启动必须分开

`pkill -f "[s]etzer.dev"` 的方括号技巧挡不住**日志路径**里的子串匹配——`>/tmp/setzer-dev3.log` 中的 `setzer-dev3` 也能被 `[s]etzer\.dev` 正则匹配（`.` 通配 `-`），会把自己的 shell 杀掉、命令静默无输出。规避：日志路径避开 `setzer` 字样，或杀进程与启动分两次调用。

### 4.2 自动化失败 ≠ 应用回归

XTEST 点击失灵、弹窗重开不生效都是自动化层的问题；确认应用侧无 bug 后，不要为「点不动」反复改代码。

### 4.3 像素级验证优先于目测

背景色用 PIL 采样并与理论值对比（如 `alpha(@theme_selected_bg_color, 0.12)` 叠已知底色可精确算出期望 RGB），比肉眼可靠。

### 4.4 已知既有问题

传文件参数启动 + 已有会话恢复 → 文档重复出现在切换列表。属既有 bug，验证时注意别误判为新回归。

## 相关文档

- [gtk-pitfalls.md](gtk-pitfalls.md) — 无显示环境下的 SIGSEGV 守卫与属性探查
- [CONTRIBUTING.md](../../CONTRIBUTING.md) — 测试注册与运行方式
