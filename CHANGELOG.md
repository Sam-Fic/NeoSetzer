# Changelog

## v71 — 2026-07-29

### 主要改进

- **新增 PDF 预览分离窗口**：支持独立窗口查看 PDF 预览，提升多任务编辑体验
- **增强文档向导功能**：为文档向导添加新配置选项和流程改进
- **集成 AI 代码修复**：新增构建日志行内和批量 AI 修复功能，提升开发效率
- **添加周期快照与会话恢复**：支持自动保存会话状态，崩溃后可恢复工作状态
- **改进构建按钮与预览缩放体验**：优化构建流程和预览交互操作
- **调整注释快捷键默认值**：注释/取消注释快捷键默认由 Ctrl+K 改为更通用的 Ctrl+/（仍可在偏好设置中自定义）

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
