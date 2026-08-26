# Changelog

## v80 — 2026-08-27

### 修复

- **修复全套件运行时 5 个实时持久化测试的既有污染失败**：`test_settings_realtime_persistence.py` / `test_workspace_realtime_persistence.py` 假设 `conftest_stub` 的伪 GLib 生效，但 pytest 收集阶段字母序靠前的测试（test_cite_optional_arg、test_code_folding_programmatic_load、test_latex_db_error_flag、test_matrix_generator）会先加载真实 gi，使桩让位——被测代码经真实 `timeout_add` 登记的 source id 与测试重置的假 `_sources` 字典对不上，产生 `KeyError`。现两个测试文件不再依赖环境中 GLib 的身份：新增 `conftest_stub.make_glib_stub()` 工厂，setUp 构建独立桩实例并注入调用点（settings 经 `patch.object(settings_module, 'GLib', …)`；workspace 注入 AST 提取函数共享的 globals 字典），断言同步指向私有桩。全套件从 574 passed + 5 failed 恢复为 **579 passed**。

### 重构

- **Include BibTeX file 对话框标准化为 libadwaita 组件**：两个样式选择器由 linked `Gtk.ToggleButton` 组改为 `Adw.ComboRow`；natbib 选项由 `Gtk.CheckButton` 改为 `Adw.SwitchRow`（外包 `Adw.PreferencesGroup` boxed list）；标题栏裸 `Gtk.Label` 换成标准 `Adw.WindowTitle`。控制器信号相应迁移：`toggled` → `notify::selected` / `notify::active`（编程式 set 同样触发、且仅在值变化时触发，语义对齐）。顺带修复重构中样式预览图丢失的问题——`Gtk.Stack` 未填充任何 `Gtk.Picture` 子项，`set_visible_child_name()` 会告警且预览区空白，现于视图构造时按内部样式 id 顺序补齐（5 + 4 张）。AI Fix 发送前预览弹窗同步处理：「此项目不再提示」复选框 → `Adw.SwitchRow`（说明文字从 tooltip 移至标准 `subtitle` 槽位，属性更名 `dont_ask_check` → `dont_ask_switch`）。

- **文献管理器条目编辑表单标准化为 libadwaita 组件**：由手拼「`<b>`粗体 Label + `Gtk.Entry`」竖排堆叠 ×23 字段改为 `Adw.PreferencesGroup`（boxed list）+ `Adw.EntryRow`（原 placeholder 提示语转 tooltip 保留），与同文件 Insert Citations 区及偏好页行风格一致；多行自由字段编辑器按本文件 chips_row 惯例嵌入 `Adw.PreferencesRow`；底部 Cancel/Save 按钮改用标准 `Gtk.ActionBar`；删除失效的 `_labeled_widget()` 帮助方法。右键字段菜单的 target 判定从 `Gtk.Entry` 放宽到 `Gtk.Editable`（覆盖 EntryRow），并顺带修复两个存量 bug：`_field_replace_text` 误把区间终点当长度传给 `delete_text()`（选区起点非 0 时少删字符、残留中间文本）；无选区时 `Adw.EntryRow.get_selection_bounds()` 返回空元组导致解包崩溃（旧 `Gtk.Entry` 返回 `None`，现统一按真值判断）。

- **项目构建设置对话框全面标准化为 libadwaita 组件**：由 `Adw.Window` + 手拼头部/底部按钮条改为 `Adw.Dialog`（复用 `DialogView` 基类：`Adw.HeaderBar` + `Adw.ToolbarView`，Cancel/Save 入标题栏）；表单行改用 `Adw.EntryRow` / `Adw.SwitchRow` / `Adw.ComboRow`（新增 `TextComboRow`/`IdComboRow` 两个薄适配子类，保持控制器侧 ComboBoxText 风格 API 不变）；三个确认弹窗从已废弃的 `Adw.MessageDialog` 迁移到 `Adw.AlertDialog`。顺带修复未配置过的项目打开该对话框即崩溃的问题（`load_profiles()` 的全 None 占位 profile 触发 `tuple(None)`）。

### Improvements

- **refactor**: Standardize the Include BibTeX file dialog on libadwaita widgets — both style pickers move from linked `Gtk.ToggleButton` groups to `Adw.ComboRow`s; the natbib option becomes an `Adw.SwitchRow` inside an `Adw.PreferencesGroup` boxed list; the bare headerbar `Gtk.Label` title becomes a standard `Adw.WindowTitle`. Controller wiring migrates from `toggled` to `notify::selected` / `notify::active`, which fire on programmatic updates and only on actual value changes — semantics fully aligned. Also fixes preview pictures lost in the rework: the `Gtk.Stack`s had no `Gtk.Picture` children, so `set_visible_child_name()` warned and the preview area stayed blank; they are now populated at view construction in internal style-id order (5 + 4 images). The AI Fix send-preview dialog gets the same treatment: the "Don't ask again" check button becomes an `Adw.SwitchRow` (explanation moved from tooltip to the standard `subtitle` slot; attribute renamed `dont_ask_check` → `dont_ask_switch`).

- **fix**: Make the two realtime-persistence test files immune to GLib identity in the environment. They assumed the `conftest_stub` fake GLib was active, but during full-suite collection, alphabetically earlier tests (`test_cite_optional_arg`, `test_code_folding_programmatic_load`, `test_latex_db_error_flag`, `test_matrix_generator`) load the real gi first, so the stub stands down — production code then registered timers via real `timeout_add` while assertions looked into a freshly reset fake `_sources` dict, raising `KeyError`. Both files now build an isolated stub per test via the new `conftest_stub.make_glib_stub()` factory and inject it at the call site (`patch.object(settings_module, 'GLib', …)` for Settings; the AST-extracted methods' shared globals dict for Workspace), with assertions pointed at the private stub. Full suite goes from 574 passed + 5 failed to **579 passed**.

- **refactor**: Rebuild the bibliography manager's entry-edit form on standard libadwaita widgets — the hand-built stack of bold labels + `Gtk.Entry` (×23 fields) is now an `Adw.PreferencesGroup` boxed list of `Adw.EntryRow`s (former placeholder hints kept as tooltips), matching the Insert Citations section and the preferences pages; the multi-line extra-fields editor follows the file's existing chips-row convention inside an `Adw.PreferencesRow`; bottom Cancel/Save buttons move to a standard `Gtk.ActionBar`; the dead `_labeled_widget()` helper is removed. The field right-click menu now targets `Gtk.Editable` (covers EntryRow), which also surfaces two latent fixes: `_field_replace_text` passed an interval length where `delete_text()` expects an end offset (under-deleted when a selection didn't start at 0), and no-selection `get_selection_bounds()` on `Adw.EntryRow` returns `()` instead of `None`, which crashed unpacking (now handled by truthiness check).

- **refactor**: Rebuild the project build configuration dialog on standard libadwaita widgets — `Adw.Dialog` via the shared `DialogView` base (`Adw.HeaderBar` + `Adw.ToolbarView` with Cancel/Save in the titlebar), `Adw.EntryRow`/`Adw.SwitchRow`/`Adw.ComboRow` form rows (thin `TextComboRow`/`IdComboRow` adapters keep the controller's legacy combo API unchanged), and `Adw.AlertDialog` replacing the deprecated `Adw.MessageDialog`. Also fixes a crash when opening the dialog for projects without an existing `build.json` (all-None placeholder profile hit `tuple(None)`).

---

## v79 — 2026-08-25

### 主要改进

- **修复新建文档 / 打开文件 / 会话恢复卡顿数秒的真正元凶**：每个 LaTeX 文档构造时都会重复注册 16 个窗口级全局快捷键加速器（实测每次注册阻塞 20-90ms，合计 0.6-2s，会话恢复 N 个文档重复 N 遍）。现改为应用启动时一次性注册，键位偏好变更时重新注册并即时全局生效（旧实现中已打开文档从不跟随新键位）。实测：`add_document` 从 ~1540ms 降至 ~1ms；连续打开 7 个文件（含一个 380KB 文档）共 ~220ms。
- **大幅优化打开文件 / 新建文档 / 会话恢复的加载耗时**（此前大文档可达数秒）：
  - 消灭双重全文解析：程序化读盘时抑制 parser 防抖调度，`set_text` + 显式 `initial_parse` 只触发一次 `finished_parsing`，所有下游观察者（代码折叠、粘性滚动、结构侧栏等）不再各执行两遍。
  - 修复代码折叠展开风暴：解析更新后只对真正失配且处于折叠态的区域做展开操作，批量路径合并为一次 `folding_state_changed` 通知，消除 O(B²) 的逐区域全表扫描；程序化整篇载入（`last_edit=None`）显式走"原偏移匹配"分支——修复重载时用陈旧偏移平移导致折叠丢失/静默崩溃的问题，同内容重载现在完整保留折叠。
  - 新增回归测试 `test_code_folding_programmatic_load.py`（覆盖 last_edit=None 首开与同内容重载场景，headless 环境自动跳过）。
  - 粘性滚动章节可见性改为排序 + 栈式扫描一次预计算（含同行嵌套命令的平局语义），替代每章节沿父链全量回溯的 O(n²) 实现。
  - `.bib` 文献解析改用轻量正则提取条目 key，移除 bibtexparser 依赖：3000 条目实测从 ~4100ms 降至 <5ms，且不再在主线程 idle 中长时间冻结 UI。
  - 编码检测改用 chardet UniversalDetector 增量喂入、高置信度提前退出：检测结果与全量 detect 一致，超大非 UTF-8 文件实测 3MB 从 ~3200ms 降至 ~140ms。
  - 新增打开路径基准（benchmarks H5）：覆盖单次解析断言、打开端到端耗时随规模趋势、全折叠后重解析的折叠保留率。

### Improvements

- **perf**: Register LaTeX document-level global accels once at startup instead of per document (each registration blocked 20-90ms; ~0.6-2s per new/opened/restored document). `add_document` drops from ~1540ms to ~1ms; opening 7 files including a 380KB one takes ~220ms in total. Re-register on keybinding preference changes so all documents update immediately.
- **perf**: Eliminate double full-document parse on open/session-restore — suppress parser debounce scheduling during programmatic disk loads so `set_text` + explicit `initial_parse` emit exactly one `finished_parsing`; all downstream observers run once instead of twice.
- **perf**: Fix code-folding unfold storm — only actually-folded mismatched regions are unfolded, bulk operations batch a single `folding_state_changed`, removing the O(B²) rescan; programmatic whole-file loads (`last_edit=None`) now take an explicit offset-preserving match path, fixing silent crashes and fold-state loss on reload.
- **perf**: Precompute sticky-scroll section visibility with a sort + stack sweep (identical tie-breaking for same-line nesting), replacing the per-section O(n²) parent-chain walk.
- **perf**: Replace bibtexparser with a lightweight regex entry-key extractor (~4100ms → <5ms for 3000 entries) and drop the dependency from packaging, CI, and docs; also make `LaTeXDB.get_file_dict` reachable as a static method.
- **perf**: Feed chardet via UniversalDetector with early exit at high confidence — identical results to full detection, 3MB non-UTF-8 detection ~3200ms → ~140ms.
- **bench**: Add open-path benchmark (H5) covering single-parse assertion, end-to-end open scaling, and fold-state preservation across reparse.

---

## v78 — 2026-08-24

### 主要改进

- **新增矩阵创建对话框（#152）**：仿照插入表格对话框实现，提供 pmatrix、bmatrix、Bmatrix、vmatrix、Vmatrix、matrix、matrix* 共 7 种 amsmath/mathtools 矩阵环境；支持自定义行数/列数（1–20）、matrix* 对齐方式（居中/左/右）；空单元格渲染为编辑器占位符，插入后可通过 Tab 键依次填写。所需 amsmath / mathtools 包会自动添加到文档中。入口包括对象菜单、命令面板和可配置快捷键。
- **新增原生命令面板**：基于 GTK4 构建，支持 Ctrl+Period 快捷键唤起；可搜索全部菜单命令、显示最近使用和可用快捷键；通过键盘选择结果并直接执行。
- **新增 LaTeX 实时拼写检查与词表管理**：支持防抖式实时检查、拼写错误波浪线提示、替换建议、会话忽略和用户词典持久化；能够识别 LaTeX 命令、数学环境、引用与路径，避免误报。
- **新增原生 LaTeX 表格生成器**：提供完整的表格对话框，支持多行多列网格编辑、单元格合并与拆分、表格样式（Plain rules / Booktabs）、长表格（longtable）跨页支持、导入/粘贴 TSV/CSV 数据；插入后可自动添加所需宏包。
- **新增用户自定义 LaTeX 代码片段**：允许用户创建、编辑和删除自定义的 LaTeX 片段，在命令面板和符号面板中可搜索并插入。
- **新增文献管理器**：支持创建和管理 `.bib` 文件，提供条目类型选择、字段编辑和导入/导出功能。
- **新增文献格式化（#229）**：文献管理器对话框新增 "Format Bibliography" 按钮，可将 `.bib` 文件中的所有条目重写为统一风格——字段按规范顺序排列、等号对齐、缩进统一；注释、`@string`/`@preamble`、条目顺序及所有条目外内容逐字节保留；裸宏值（如 `month = jun`）保持原样不被加大括号。改写前需确认，文件在 Setzer 中打开时可通过撤销栈回退。
- **新增插入图片对话框**：支持从文件或剪贴板插入图片，保存常用设置，并为关键控件补充工具提示；未保存文档会先提示保存以获取目标目录。
- **增强文档向导**：改进文档类型页面布局与模板选择器；新增 scrlttr2 信头字段、KOMA 信函模板选项（#170）和 Beamer 主题搜索功能；支持窄窗口自适应布局；新增用户自定义文档模板；在文档创建前显示待确认信息。
- **增强文档大纲**：显示章节编号，支持附录和计数器，新增 Beamer 帧导航；修复嵌套章节标题解析和短文档块保护。
- **增强导航与粘性滚动**：为诊断信息目标（标签、引用、待办）保留上下文并居中显示；修复粘性滚动标题下方的上下文保留和阅读边距。
- **优化编辑器滚动、行号栏与粘性滚动**：以 FrameClock 驱动惯性滚动；改进行号栏离屏缓存、HiDPI/分数缩放下的文字与图标清晰度，以及粘性滚动的行高和绘制一致性。
- **新增直接打印功能**：替换打印对话框为直接调用 Gtk.PrintJob 打印，简化打印流程。
- **新增预览 PDF 外部监控**：当 PDF 文件在外部被修改时自动检测并更新预览。
- **支持 TeX 魔术注释**：允许通过魔术注释指定编译引擎和命令。
- **刷新首次运行教程**：重新设计初次运行引导界面，提供更好的入门体验。
- **新增交互式示例项目**：提供可写的示例项目副本和高级功能导图，方便新用户快速了解编辑器功能。
- **继续列表项输入**：按 Enter 时自动插入新 `\item`，无需手动输入。
- **改善常用交互与界面细节**：打开文件时默认定位到当前活动文档目录；优化自动补全背景、预览菜单、搜索展开图标和光标滚动行为。
- **修复构建、同步与文档处理稳定性问题**：修复正向/反向同步与构建中止冲突、非 LaTeX 文档导致的属性错误和构建挂起，以及文本编辑后的迭代器失效问题。
- **修复预览面板样式**：调整预览卡片宽度、圆角和间距以保持视觉一致性。
- **修复自动保存恢复**：在恢复的文件名中转义 Markup 字符，防止 XML 解析错误。
- **修复编辑器折叠**：调整折叠符号居中逻辑和图标尺寸。
- **修复命令面板样式**：使用扁平列表样式并修复空状态显示。
- **修复自动补全**：修复 preamble 过滤未生效问题并预计算命令基础名；修复补全弹窗背景透明问题。
- **修复插入图片对话框**：在粘贴图片前先提示保存未保存文档。
- **修复 LaTeX 数据库崩溃**：修复未初始化文件的解析崩溃。
- **建立跨平台打包与发布支持**：新增 Debian、Windows x64 和 macOS Apple Silicon 自动打包；Windows 与 macOS 包内置 Adwaita symbolic 图标主题，保证非 GNOME 平台上的图标一致性；同时加入持续集成测试。
- **重构偏好设置**：将 LaTeX 代码片段折叠到编辑器页面；将实验性功能设置移至编辑器页面。
- **重构构建日志对话框**：替换 GLib 排序为 Python 内置排序，添加错误/警告/badbox 类型过滤器，优化行激活跳转逻辑。
- **更新翻译与文档**：补全中文、德语、西班牙语及繁体中文翻译；更新 README 项目名称与仓库引用；更新版权声明。

### Improvements

- **feat**: Add Insert Matrix dialog (#152) — 7 amsmath/mathtools matrix environments (pmatrix, bmatrix, Bmatrix, vmatrix, Vmatrix, matrix, matrix*), adjustable rows/columns (1–20), column alignment for matrix*. Empty cells render as • placeholder for Tab navigation. Required packages added automatically.
- **feat**: Add native GTK4 command palette — Ctrl+Period to invoke, searchable across all menu commands, shows recently used and available shortcuts, keyboard-selectable results.
- **feat**: Add real-time LaTeX-aware spellchecking with debounced diagnostics, replacement suggestions, session ignore, persistent user dictionaries, and exclusions for commands, math, citations, and file paths.
- **feat**: Add native LaTeX table generator dialog — full grid editing, cell merges, plain/booktabs styles, longtable support, TSV/CSV import/paste.
- **feat**: Add user-defined LaTeX snippets — create, edit, and delete custom snippets searchable in the command palette and symbols panel.
- **feat**: Add bibliography manager for creating and managing .bib files with entry type selection, field editing, and import/export.
- **feat**: Add bibliography formatting (#229) — a "Format Bibliography" button in the manager dialog rewrites all entries in a .bib file to a canonical style: fields in a consistent order, aligned equals signs, uniform indentation. Comments, @string/@preamble, entry order, and everything outside entries are preserved byte-for-byte; bare macro values (e.g. month = jun) stay unbraced. Requires confirmation and remains undoable via the undo stack when the file is open in Setzer.
- **feat**: Add insert-image dialog with file and clipboard support, saved defaults, and improved control tooltips; prompt to save unsaved documents before pasting images.
- **feat**: Improve document wizard — better document-class page layout and template choosers, scrlttr2 letterhead fields, KOMA letter template options (#170), Beamer theme search, narrow-window adaptation, user document templates, and pending document creation confirmation.
- **feat**: Improve document outline — show section numbers, support appendices and counters, add Beamer frame navigation; fix nested section title parsing and guard against short blocks.
- **feat**: Improve navigation and sticky scrolling — preserve context for label, reference, and todo jumps with centering; fix sticky scroll reading margin and context below sticky headers.
- **perf**: Drive inertial scrolling with FrameClock; improve gutter off-screen caching, HiDPI/fractional-scaling text and icon rendering, and sticky-scroll line-height and drawing consistency.
- **feat**: Replace print dialog with direct Gtk.PrintJob printing.
- **feat**: Monitor externally changed PDFs for automatic preview updates.
- **feat**: Support TeX magic comments for specifying compile engine and command.
- **feat**: Refresh first-run tutorial with improved onboarding.
- **feat**: Add interactive example project with writable copies and advanced feature map.
- **feat**: Auto-continue LaTeX list items on Enter.
- **improvements**: Open the file chooser in the active document directory; refine autocomplete backgrounds, preview context menus, search disclosure icons, and cursor scrolling behavior.
- **fix**: Resolve forward/backward SyncTeX and build-cancellation conflicts, non-LaTeX document property errors and build hangs, and invalid iterators after text-buffer edits.
- **fix**: Match preview card width, rounded corners, and spacing.
- **fix**: Escape markup in autosave recovery file names.
- **fix**: Adjust gutter fold symbol centering and icon size; fix HiDPI/fractional-scaling line number and icon blurriness; fix gutter off-screen cache blank on upward scroll.
- **fix**: Use flat list style and fix empty state in command palette.
- **fix**: Fix preamble filtering not applying and precompute command base names in autocomplete; fix autocomplete popup background transparency.
- **fix**: Prompt to save unsaved documents before pasting images.
- **fix**: Fix LaTeX database parse crash on uninitialized files.
- **ci**: Add automated Debian, Windows x64, and macOS Apple Silicon packaging with continuous tests; bundle Adwaita symbolic icon theme for non-GNOME platforms.
- **refactor**: Fold LaTeX snippets into Editor page; move experimental feature settings into Editor page.
- **refactor**: Replace GLib sorting with Python built-in sorting in build log dialog; add error/warning/badbox type filter checkboxes; optimize row activation jump logic.
- **i18n/docs**: Complete all bundled translations (zh_CN, de, es, zh_TW); update README project name and repository references; update copyright notices.

---

## v77 — 2026-08-21

### 主要改进

- **更名为 NeoSetzer 并更新应用图标**：完成项目名称、仓库引用与版权信息迁移；重绘应用图标，简化图形结构并更新配色，使其在各类桌面环境中更清晰易辨。
- **新增 LaTeX 实时拼写检查与词表管理**：支持防抖式实时检查、拼写错误波浪线提示、替换建议、会话忽略和用户词典持久化；能够识别 LaTeX 命令、数学环境、引用与路径，避免误报。
- **新增插入图片对话框**：支持从文件或剪贴板插入图片，保存常用设置，并为关键控件补充工具提示。
- **补全 Ctrl+V 直接粘贴图片生成 figure（#439）**：编辑器内 Ctrl+V 探测到剪贴板图片时自动弹出插入对话框，默认包装完整 `figure` 环境并确保 `graphicx`；未保存文档会先提示保存以获取目标目录，避免插入崩溃。
- **优化编辑器滚动、行号栏与粘性滚动**：以 FrameClock 驱动惯性滚动；改进行号栏离屏缓存、HiDPI/分数缩放下的文字与图标清晰度，以及粘性滚动的行高和绘制一致性。
- **修复构建、同步与文档处理稳定性问题**：修复正向/反向同步与构建中止冲突、非 LaTeX 文档导致的属性错误和构建挂起，以及文本编辑后的迭代器失效问题。
- **改善常用交互与界面细节**：打开文件时默认定位到当前活动文档目录；优化自动补全背景、预览菜单、搜索展开图标和光标滚动行为。
- **建立跨平台打包与发布支持**：新增 Debian、Windows x64 和 macOS Apple Silicon 自动打包；Windows 与 macOS 包内置 Adwaita symbolic 图标主题，保证非 GNOME 平台上的图标一致性；同时加入持续集成测试。
- **更新翻译与文档**：同步中文、德语、西班牙语及繁体中文翻译，并更新项目说明与版权信息。

### Improvements

- **branding**: Rename the project to NeoSetzer; update repository references and copyright notices; redesign the application icon with a simplified structure and refreshed palette for clearer desktop presentation.
- **feat**: Add real-time LaTeX-aware spellchecking with debounced diagnostics, replacement suggestions, session ignore, persistent user dictionaries, and exclusions for commands, math, citations, and file paths.
- **feat**: Add an insert-image dialog with file and clipboard support, saved defaults, and improved control tooltips.
- **perf**: Drive inertial scrolling with FrameClock; improve gutter off-screen caching, HiDPI/fractional-scaling text and icon rendering, and sticky-scroll line-height and drawing consistency.
- **fix**: Resolve forward/backward SyncTeX and build-cancellation conflicts, non-LaTeX document property errors and build hangs, and invalid iterators after text-buffer edits.
- **improvements**: Open the file chooser in the active document directory; refine autocomplete backgrounds, preview context menus, search disclosure icons, and cursor scrolling behavior.
- **ci**: Add automated Debian, Windows x64, and macOS Apple Silicon packaging with continuous tests; bundle the Adwaita symbolic icon theme in Windows and macOS packages for consistent non-GNOME icon rendering.
- **i18n/docs**: Refresh Simplified Chinese, German, Spanish, and Traditional Chinese translations; update project documentation and copyright information.

---

## v76 — 2026-08-03

### 主要改进

- **重构构建日志对话框**：替换 GLib 排序为 Python 内置排序，添加错误/警告/badbox 类型过滤器，优化行激活跳转逻辑
- **新增主题快速切换器**：在汉堡菜单顶部添加主题切换控件，支持跟随系统/浅色/深色三种主题
- **重构自动补全弹窗**：替换 libadwaita boxed-list 为自定义样式，新增图标列与详情列，优化内边距与圆角
- **优化 gutter 绘制**：修复空白字符显示不生效、行号偏移与图标对齐问题，滚动时立即重绘消除帧滞后
- **重构弹窗与全屏逻辑**：统一弹窗调用方法，移除冗余轮询，新增全屏加载指示器，优化长操作用户体验
- **修复 Flatpak 配置**：修正 JSON 格式与安装路径配置
- **更新构建脚本**：规范 setzer.in 换行符，为安装脚本添加权限设置

### Improvements

- **refactor**: Replace GLib sorting with Python built-in sorting in build log dialog; add error/warning/badbox type filter checkboxes; optimize row activation jump logic
- **feat**: Add theme quick switcher to hamburger menu with system/light/dark modes
- **refactor**: Replace libadwaita boxed-list with custom CSS in autocomplete popup; add icon and detail columns; optimize padding and corner radius
- **fix**: Fix whitespace rendering not working, line number offset and icon alignment in gutter; immediate redraw during scroll to eliminate frame lag
- **refactor**: Unify popover call methods; remove redundant polling logic; add fullscreen loading indicator for long operations
- **fix**: Fix Flatpak manifest JSON formatting and libdir installation path
- **chore**: Normalize line endings in setzer.in; add install_mode permissions to setzer script

## v75 — 2026-07-31

### 主要改进

- **新增 Windows 平台原生支持**：移除 `pexpect` 依赖（Unix PTY 专用），改用跨平台 `subprocess` + 线程监控实现 LaTeX 构建进程管理；Windows 上自动设 `CREATE_NO_WINDOW` 避免弹出控制台窗口
- **修复路径解析跨平台兼容性**：将 6 处 `rsplit('/', 1)` 路径切分改为 `os.path.splitext(os.path.basename(...))`，修复 Windows 反斜杠路径下文件名提取失败；将 `+ '/' +` 字符串拼接改为 `os.path.join`；将 `BIBINPUTS` 环境变量分隔符从硬编码 `:` 改为 `os.pathsep`（Windows 用 `;`）
- **修复构建系统 GNOME 桌面集成的平台条件化**：`meson.build` 在 Windows 上跳过 `.desktop`/mime/metainfo/man 安装；新增 `setzer.bat.in` 启动器模板和 `scripts/setzer.dev.bat` 开发启动脚本
- **修复硬编码 Unix 路径**：`setzer.in` 中 `/usr/share/locale` 回退改为平台感知；`ai_fix/agent_runner.py` 中 `_which_on_host` 新增 Windows `Program Files` 路径探测，`TERMINAL_CHAIN` 新增 `wt`/`powershell`/`cmd` 终端支持
- **更新文档**：README（中英文）新增 Windows 安装/运行/打包章节、平台支持矩阵；移除已不再需要的 `python3-pexpect` 依赖声明

### Improvements

- **feat**: Add native Windows support — replace pexpect with cross-platform subprocess + thread-based process monitoring, add CREATE_NO_WINDOW flag on Windows to suppress console popups, add setzer.bat launcher and setzer.dev.bat dev script
- **fix**: Replace 6 `rsplit('/', 1)` path splits with `os.path.splitext(os.path.basename(...))` for Windows backslash path compatibility; replace `+ '/' +` string concatenation with `os.path.join`; replace hardcoded `:` BIBINPUTS separator with `os.pathsep`; make GNOME desktop integration (.desktop/mime/metainfo/man) conditional on non-Windows in meson.build; make hardcoded `/usr/share/locale` fallback platform-aware in setzer.in
- **feat**: Add Windows terminal support (wt/powershell/cmd) to TERMINAL_CHAIN in agent_runner.py; add Windows Program Files path probing in `_which_on_host`; replace `start_new_session=True` with `CREATE_NEW_PROCESS_GROUP` on Windows
- **docs**: Update README.md and README.zh-CN.md with Windows installation (MSYS2), running, and packaging sections; add platform support matrix; remove `python3-pexpect` from dependency list (no longer used)

---

## v74 — 2026-07-31

### 主要改进

- **重构构建系统与GTK4适配**：重构构建流程支持阶段追踪与UI展示，适配GTK4剪贴板API；修复构建误报成功、预览spinner卡死、子工具链错误未检测等问题
- **新增编译诊断高亮与会话恢复**：在编辑器边栏与整行背景显示编译错误/警告，支持悬停查看详情；会话恢复时校验诊断行号新鲜度避免错位
- **增强预览面板交互**：支持Ctrl+点击打开URI链接并添加悬停提示；新增逐页布局与页码徽章按钮，支持不等高页面精准跳转；修复深色模式页码徽章不可见、滚动条溢出圆角等问题
- **重构高亮与样式系统**：优化begin/end语法高亮透明度处理，避免污染缓存颜色；调整高亮颜色透明度防止过度饱和
- **新增撤销深度上限与构建日志按钮**：允许配置撤销步数上限避免撤销栈无界增长；新增标题栏构建日志副本按钮
- **新增文档语法高亮**：支持LaTeX \begin/\end语法高亮
- **修复符号页、侧边栏与编辑器问题**：移除收藏菜单项星号前缀；修复右键菜单与悬停预览冲突、侧边栏菜单无法点击、字体缩放锁死、文档切换样式残留等问题
- **更新文档、示例与翻译**：更新README与打包文档；调整示例文档日期排版与字体配置；更新截图资源与翻译文件

### Improvements

- **feat**: Add build stage tracking/UI display, build diagnostics highlighting with hover details and session restore freshness, Ctrl+click URI opening with toast, per-page layout with page indicator buttons, undo depth limit preference, headerbar build log toggle button, and LaTeX begin/end syntax highlight
- **fix**: Fix build false positive success, preview spinner stuck in building state, dark mode page badge invisible, scrollbar overflow rounded corners, symbol hover preview conflicting with context menu, sidebar context menu unclickable/open folder failed, font zoom percentage stuck, document switch style residue, and GTK4 API compatibility in multicursor/document controller
- **refactor**: Restructure build system with thread pool + idle callbacks; adapt GTK4 clipboard, multicursor, and document controller APIs; optimize highlight alpha and cache color handling
- **style**: Adjust highlight color alpha values to fix over-saturation
- **docs**: Update README and deb build docs for webkitgtk dependency; update example document date layout and remove monospace font config; add highlight alpha constant comments
- **chore**: Ignore .codebuddy directory; update translations; update screenshots

---

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