# Changelog

## v73 — 2026-07-30

### 主要改进

- **重构构建流程与剪贴板适配**：改用线程池+idle回调替换轮询模式，新增构建中止提示；适配GTK4剪贴板API
- **修复预览滚动与布局问题**：添加预览窗口垂直边距，修复页面贴边问题；调整PDF预览边框和背景配色逻辑
- **修复撤销重做与构建日志问题**：修复undo组异常和日志表头样式；修复重载文件后未触发自动构建的问题
- **新增撤销深度上限偏好**：允许用户配置 GtkSource.Buffer 的撤销步数上限（默认 200，0 为不限），避免超大文档撤销栈无界增长
- **修复GTK4控件兼容性**：修复打印对话框和文档属性对话框的GTK4 API调用
- **修复滚动事件重复处理**：消费滚动事件避免ScrolledWindow重复执行平移操作
- **新增文档属性构建选项覆盖开关**：统一控制单个文档的构建设置是否使用全局默认值
- **新增首次运行教程打开示例文档按钮**：匹配欢迎页面入口
- **AI修复提示词增强**：在提示词中包含LaTeX引擎信息
- **更新示例文档**：新增AI相关功能说明，优化代码块显示样式
- **更新翻译文件**

### Improvements

- **refactor**: Restructure build system with thread pool + idle callbacks; adapt GTK4 clipboard API
- **fix**: Add vertical padding to document preview layout; adjust PDF preview border and background color logic
- **fix**: Fix undo group exception and build log header style; fix auto-build not triggering after file reload
- **feat**: Add undo depth limit preference (default 200, 0 = unlimited) to cap GtkSource.Buffer's undo stack for large documents
- **fix**: Fix GTK4 control API compatibility in print and document properties dialogs
- **fix**: Consume scroll events to prevent duplicate handling in ScrolledWindow
- **feat**: Add build option override master switch in document properties
- **feat**: Add open example document button in first-run tutorial
- **feat**: Include LaTeX engine info in AI fix prompts
- **docs**: Update example document with AI feature descriptions
- **chore(i18n)**: Update translations

---

## v72 — 2026-07-30

### 主要改进

- **新增全局异常处理器**：覆盖主线程(sys.excepthook)、子线程(threading.excepthook)与 GLib 回调，避免未捕获异常导致静默崩溃
- **新增首次运行教程**：首次启动时自动展示引导界面，帮助新用户快速上手
- **新增导出 PDF 功能**：支持将构建后的 PDF 另存到其他位置（仅副本，不修改源文件关联）
- **新增书签系统**：支持切换、跳转（上一个/下一个）和清除书签，提升长文档导航效率
- **新增代码折叠操作**：支持一键折叠/展开全部代码块
- **新增缩进控制**：支持增加/减少缩进（Indent/Outdent）
- **新增多光标编辑**：支持选择下一个/所有匹配项、添加上下光标等高级编辑能力
- **增强预览面板交互**：支持右键菜单进行旋转、复制文本/图片、保存图片、PDF 内搜索、显示源码、重新着色、打印等操作
- **增强标签页右键菜单**：支持跳转到定义、复制引用标签、查找所有引用
- **新增全屏模式切换**：通过动作切换全屏状态
- **重构构建系统**：简化架构，移除冗余查询循环与轮询逻辑；修复构建日志无法持久化的问题（恢复 parse_result 方法）
- **构建日志增加阶段标识**：区分 LaTeX 与 BibTeX 阶段的错误/警告/坏盒子条目
- **修正窗口状态保存**：改为保存 surface 实际宽高而非默认尺寸，确保窗口大小变更后能被正确恢复
- **修复日期时间本地化**：显式设置 LC_TIME，使时间戳（如日志、文件属性）跟随界面语言本地化
- **完善全屏编辑器顶部间距逻辑**：修复全屏隐藏快捷键栏时编辑器顶边紧贴窗口的问题
- **修复帮助面板搜索框问题**：清空搜索框时不再显示旧结果
- **更新翻译文件**：修复多项翻译条目，统一引号格式，补充缺失翻译

### Improvements

- **feat**: Add global exception handler covering main thread, threading, and GLib callbacks
- **feat**: Add first-run tutorial shown on first application launch
- **feat**: Add "Export PDF As..." action to save built PDF to a different location
- **feat**: Add bookmark system with toggle, navigation (next/previous), and clear actions
- **feat**: Add code folding actions (fold all / unfold all)
- **feat**: Add indent/outdent actions
- **feat**: Add multi-cursor editing (select next/all occurrences, add cursor above/below)
- **feat**: Add preview panel context menu (rotate, copy text/image, save image, search PDF, show source, recolor, print)
- **feat**: Add label context menu for references (jump-to-definition, copy-ref, find-all-refs)
- **feat**: Add toggle-fullscreen action
- **refactor**: Restructure build system architecture; restore parse_result to fix build log persistence
- **feat**: Show build log stage (LaTeX/BibTeX) per item
- **fix**: Save actual surface width/height instead of default size for window state
- **fix**: Set LC_TIME locale for localized date/time format in logs/file properties
- **fix**: Refine fullscreen editor top spacing logic when shortcut bar is hidden
- **fix**: Fix help panel search box retaining stale results when cleared
- **chore(i18n)**: Update translations, unify quotation marks, fix python-brace-format placeholders, add missing entries

---

## v71 — 2026-07-29

### 主要改进

- **新增 PDF 预览分离窗口**：支持独立窗口查看 PDF 预览，提升多任务编辑体验
- **增强文档向导功能**：为文档向导添加新配置选项和流程改进
- **集成 AI 代码修复**：新增构建日志行内和批量 AI 修复功能，提升开发效率
- **添加周期快照与会话恢复**：支持自动保存会话状态，崩溃后可恢复工作状态
- **改进构建按钮与预览缩放体验**：优化构建流程和预览交互操作
- **调整注释快捷键默认值**：注释/取消注释快捷键默认由 Ctrl+K 改为更通用的 Ctrl+/（仍可在偏好设置中自定义）
- **新增环境自动补 \\end{}（默认关闭）**：输入 \\begin{ 时自动补出配对 \\end{}（含内容占位符，可用 Tab 跳转）；因可选参数环境存在限制，默认不启用，偏好设置中可手动开启

### Improvements

- **feat**: Add PDF preview detached window feature
- **feat**: Enhance document wizard functionality and add new configuration options
- **feat**: Integrate AI code fix feature with inline and batch build log fix support
- **feat**: Add periodic snapshot and session restore with crash recovery
- **feat**: Improve build button and preview zoom interaction experience
- **change**: Set comment/uncomment default shortcut to Ctrl+/ instead of Ctrl+K (still customizable)

---

## v70 — 2026-07-27

### 主要改进

- **实现 PDF 点击精确定位到源码字符**：在 PDF 预览中点击即可跳转到对应源码位置
- **合并搜索/替换工具栏**：将搜索与替换功能整合到统一工具栏，新增保留大小写选项
- **重构偏好设置配色模块**：优化配色方案选择逻辑，恢复自由组合主题能力
- **优化面板切换逻辑与 UI 细节体验**：改进面板切换动画和界面细节

### Improvements

- **feat**: Implement PDF click-to-source character navigation
- **refactor**: Merge search/replace toolbar with case-sensitive option
- **refactor**: Restructure preferences color scheme selection logic and restore theme combination support
- **refactor**: Optimize panel switching logic and UI detail experience

---

## v69 — 2026-07-26

### 主要改进

- **重构侧边栏和预览帮助面板的交互逻辑**：提升侧边栏和帮助面板的操作流畅度
- **添加 PDF 页面跳转输入框和优化预览工具栏**：在预览面板中快速跳转到指定页
- **合并编辑器设置到外观页，新增自动重载配置**：简化设置界面，新增配置自动重载
- **修复深色模式下搜索栏边框问题**：优化搜索栏视觉表现

### Improvements

- **refactor**: Refactor sidebar and preview help panel interaction logic for smoother operation
- **feat**: Add PDF page jump input box and optimize preview toolbar
- **refactor**: Merge editor settings into appearance page with auto-reload configuration
- **fix**: Fix search bar border issue under dark mode

---

## v68 — 2026-07-25

### 主要改进

- **新增 Debian 打包文档**：添加完整的 `.deb` 包构建流程说明
- **添加代码折叠行号符号**：在行号 gutter 中显示换行符号标识
- **优化欢迎页与页眉栏样式**：改进空状态视图和顶部工具栏视觉效果
- **重构行号渲染逻辑**：移除行号垂直偏移配置，统一行号与折叠图标渲染

### Improvements

- **docs**: Add Debian packaging build documentation
- **feat(add)**: Add newline symbol indicator in gutter for wrapped lines
- **refactor(style)**: Optimize welcome page and header bar styles with fixed-height empty state
- **refactor(gutter)**: Remove vertical offset config and unify line number and fold icon rendering