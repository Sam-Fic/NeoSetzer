#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

import gi
gi.require_version('Adw', '1')
from gi.repository import GLib, Adw
import os
import os.path
import time
import uuid

from setzer.document.document import Document
from setzer.document.magic_comments import parse_magic_comments, resolve_root_filename
from setzer.project.build_configuration import ProjectBuildConfiguration
import setzer.document.build_system.build_system as build_system
import setzer.document.build_widget.build_widget as build_widget
import setzer.document.preview.preview as preview
from setzer.helpers.observable import Observable
from setzer.helpers.persistence import (
    atomic_write_text, load_json, save_json, migrate_pickle_to_json,
    try_migrate_session_file_pickle,
)
import setzer.workspace.workspace_presenter as workspace_presenter
import setzer.workspace.workspace_controller as workspace_controller
import setzer.workspace.preview_panel.preview_panel as preview_panel
import setzer.workspace.help_panel.help_panel as help_panel
import setzer.workspace.pdf_preview_window.pdf_preview_window as pdf_preview_window
import setzer.workspace.welcome_screen.welcome_screen as welcome_screen
import setzer.workspace.headerbar.headerbar as headerbar
import setzer.workspace.sidebar.sidebar as sidebar
import setzer.workspace.shortcutsbar.shortcutsbar as shortcutsbar
import setzer.workspace.build_log.build_log as build_log
import setzer.workspace.actions.actions as actions
import setzer.workspace.context_menu.context_menu as context_menu
import setzer.workspace.auto_build.auto_build as auto_build
import setzer.workspace.auto_save.auto_save as auto_save
from setzer.app.service_locator import ServiceLocator
from setzer.settings.document_settings import DocumentSettings
from setzer.app.latex_db import LaTeXDB


class Workspace(Observable):
    ''' A workspace contains a user's open documents. '''

    # 工作区变化常成批出现（会话恢复、连续切换文档、滚动），将其合并为
    # 一次原子 JSON 快照，既减少 I/O 又显著缩短异常退出时的状态丢失窗口。
    PERSISTENCE_DELAY_MS = 1000

    def __init__(self):
        Observable.__init__(self)
        self.pathname = ServiceLocator.get_config_folder()
        self._persistence_source_id = None
        self._document_state_handlers = dict()

        self.open_documents = list()
        self.open_latex_documents = list()
        self.root_document = None
        self.recently_opened_documents = dict()
        self.pinned_recent_documents = set()

        self.active_document = None

        # 已确认关闭的文档集合：程序批量关闭路径（如 hamburger 会话恢复
        # 的 discard_all / 单文档 discard）已让用户确认过，调 remove_document
        # 时传 confirmed=True 标记，WorkspacePresenter 的 close-page handler
        # 见到会直接移除而不再弹 confirm 对话框，避免重复确认。
        self._confirmed_closes = set()

        self.recently_opened_session_files = dict()
        self.session_file_opened = None

        self.settings = ServiceLocator.get_settings()

        self.show_build_log = self.settings.get_value('window_state', 'show_build_log')
        self.show_preview = self.settings.get_value('window_state', 'show_preview')
        self.show_help = self.settings.get_value('window_state', 'show_help')
        self.show_symbols = self.settings.get_value('window_state', 'show_symbols')
        self.show_document_structure = self.settings.get_value('window_state', 'show_document_structure')

        # 记忆上次侧栏面板（隐藏后再次显示时恢复）。与 show_*/show_document_structure
        # 解耦：后者在隐藏时被清成 False（仅用于驱动可见性），本属性持久化"上次选了哪个"。
        self.sidebar_page = self.settings.get_value('window_state', 'sidebar_page')
        if self.sidebar_page not in ('symbols', 'document_structure'):
            self.sidebar_page = 'symbols'

        # PDF 预览弹出独立窗口状态。不跨会话持久化（v1）：每次启动默认内嵌侧边栏，
        # 用户按需弹出。pdf_preview_window 懒创建（首次 pop_out 时构造），收回时
        # 仅隐藏不销毁，保留几何状态（位置/大小）便于再次弹出。
        # 注意：pop_out 不改 show_preview（避免污染持久化状态）——侧边栏收起由
        # update_preview_help_visibility 的 popped_out 分支处理（preview 不再算作
        # 需要展开侧栏的理由，因预览已在独立窗口）。这样退出时 show_preview 仍为
        # 原值，下次启动恢复正确。
        self.preview_popped_out = False
        self.pdf_preview_window = None
        self._loading_count = 0

        # Actions 必须在 Workspace 构造期就建立：DialogLocator.init_dialogs()
        # 在 activate() 中早于 init_workspace_controller() 调用，而命令面板等
        # 对话框构造时即依赖 self.actions（如 CommandCatalog(workspace.actions)）。
        # init_workspace_controller() 会复用此实例，不再重建。
        self.actions = actions.Actions(self)

    def _loading_start(self):
        '''开始加载：计数器+1并发射信号。'''
        self._loading_count += 1
        self.add_change_code('loading-started')

    def _loading_finish(self):
        '''结束加载：计数器-1并发射信号（计数器归零时才发）。'''
        if self._loading_count > 0:
            self._loading_count -= 1
        if self._loading_count == 0:
            self.add_change_code('loading-finished')

    def _on_document_loading(self, document, action):
        '''文档加载回调：由 document._load_file_content 调用。'''
        if action == 'start':
            self._loading_start()
        elif action == 'finish':
            self._loading_finish()

    def init_workspace_controller(self):
        # 缓存 main_window 引用：PdfPreviewWindow 等子组件通过 workspace.main_window
        # 访问（避免每处都 ServiceLocator.get_main_window()，且便于测试 mock）。
        self.main_window = ServiceLocator.get_main_window()
        self.welcome_screen = welcome_screen.WelcomeScreen(self)
        self.sidebar = sidebar.Sidebar(self)
        # self.actions 已在 __init__ 中建立，此处复用（不再 new 一份），
        # 避免覆盖掉 DialogLocator 等已持有引用的实例。
        self.shortcutsbar = shortcutsbar.ShortcutsBar(self)
        self.context_menu = context_menu.ContextMenu(self)
        self.presenter = workspace_presenter.WorkspacePresenter(self)
        self.headerbar = headerbar.HeaderBar(self)
        self.preview_panel = preview_panel.PreviewPanel(self)
        self.help_panel = help_panel.HelpPanel(self)
        self.build_log = build_log.BuildLog(self)
        self.auto_build = auto_build.AutoBuild(self)
        self.auto_save = auto_save.AutoSave(self)
        self.controller = workspace_controller.WorkspaceController(self)

    def open_document_by_filename_with_spinner(self, filename):
        '''用户触发的打开文档：先显示 spinner，延迟约一帧再执行实际打开。

        与直接调用 open_document_by_filename 的区别：
        - 显示 spinner 后用 timeout 延迟，确保 spinner 渲染出来再执行重操作
          （create_document_from_filename 会读盘 + 创建 GTK 组件，阻塞主线程）。
          16ms ≈ 60Hz 下一帧：spinner 能画出第一帧即可，不再固定多等 200ms。
        - fire-and-forget：不返回 document（用户交互场景无需同步拿返回值）。
        供对话框、最近文档、拖放、欢迎页等用户入口调用。编程式打开
        （sidebar include 跳转、build log 反向同步等）仍用同步的
        open_document_by_filename，因为它们需要立即拿到 document 引用。
        '''
        main_window = ServiceLocator.get_main_window()
        if main_window is not None and hasattr(main_window, 'show_loading_spinner'):
            main_window.show_loading_spinner()
            GLib.timeout_add(16, self._do_open_document_by_filename, filename)
        else:
            self.open_document_by_filename(filename)

    def _do_open_document_by_filename(self, filename):
        '''timeout 回调：spinner 已渲染后执行实际的文档打开。'''
        self.open_document_by_filename(filename)
        return False

    def open_document_by_filename(self, filename):
        if filename == None: return None

        document_candidate = self.get_document_by_filename(filename)
        if document_candidate != None:
            self.set_active_document(document_candidate)
            main_window = ServiceLocator.get_main_window()
            if main_window and hasattr(main_window, 'toast_overlay'):
                GLib.idle_add(self._do_show_toast, main_window,
                    _('{name} is already open').format(name=os.path.basename(filename)))
            return document_candidate
        else:
            document = self.create_document_from_filename(filename)
            if document != None:
                self.set_active_document(document)
            return document

    def switch_to_earliest_open_document(self):
        document = self.get_earliest_active_document()
        if document != None:
            self.set_active_document(document)

    def add_document(self, document):
        if document in self.open_documents: return False

        if document.get_filename() == None:
            increment = ServiceLocator.get_increment('untitled_documents_added')
            document.set_displayname(_('Untitled Document {number}').format(number=str(increment)))

        self.open_documents.append(document)
        if document.is_latex_document():
            self.open_latex_documents.append(document)
        self._watch_document_state(document)
        DocumentSettings.load_document_state(document)
        self.add_change_code('new_document', document)
        # 新打开的 LaTeX 文档若已有根文档日志（如会话恢复后），立即同步其
        # 错误/警告高亮，避免要等下一次编译才显示。
        if document.is_latex_document():
            document.update_build_diagnostics()
        self.update_recently_opened_document(document.get_filename(), notify=True)
        # 刷新 LaTeXDB 的 label/bibitem 数据库（事件驱动，替代原 3 秒轮询）。
        # 去抖：会话恢复连续打开 N 个文档时，N 次 schedule 仅触发 1 次
        # parse_included_files（idle 合并），避免 N 次全量 stat/read 扫描。
        LaTeXDB.schedule_parse_included_files()
        self.schedule_persistence()

    def remove_document(self, document, confirmed=False):
        if confirmed:
            self._confirmed_closes.add(document)
        self._unwatch_document_state(document)
        if document == self.root_document:
            self.unset_root_document()
        DocumentSettings.save_document_state(document)

        # 释放文档级常驻定时器，避免关闭后仍占主循环配额。
        # controller（save_date_loop 500ms）所有文档都有；
        # preview.page_renderer（rendered_pages_loop 50ms）仅 latex 文档有。
        # build_system 已改为事件驱动（worker 完成通过 GLib.idle_add 回调），
        # shutdown 仅清理 active_query 引用，无定时器需移除。
        try:
            document.controller.shutdown()
        except Exception as e:
            print(f'Warning: document.controller.shutdown() failed: {e}')

        # 断开 settings / style_manager 单例信号连接 + 取消挂起的
        # _init_deferred_features idle 回调。详见 Document.shutdown 文档。
        try:
            document.shutdown()
        except Exception as e:
            print(f'Warning: document.shutdown() failed: {e}')

        self.open_documents.remove(document)
        if document.is_latex_document():
            self.open_latex_documents.remove(document)
            # build_system / preview 可能尚未挂接（会话恢复的非活跃文档在
            # 激活前不建 toolchain），用 getattr 守卫避免 AttributeError。
            doc_build_system = getattr(document, 'build_system', None)
            if doc_build_system is not None:
                try:
                    doc_build_system.shutdown()
                except Exception as e:
                    print(f'Warning: document.build_system.shutdown() failed: {e}')
            # 释放预览渲染器的 50ms 轮询定时器（后台线程靠 is_active=False 空转，
            # 随进程退出）。避免关闭文档后定时器常驻泄漏。
            doc_preview = getattr(document, 'preview', None)
            if doc_preview is not None:
                try:
                    doc_preview.page_renderer.shutdown()
                except Exception as e:
                    print(f'Warning: document.preview.page_renderer.shutdown() failed: {e}')
        if self.active_document == document:
            candidate = self.get_last_active_document()
            if candidate == None:
                self.set_active_document(None)
            else:
                self.set_active_document(candidate)
        # 清理未命名文档的临时内容文件
        self._cleanup_untitled_content(document)
        self.add_change_code('document_removed', document)
        # 文档列表已变，刷新 LaTeXDB（事件驱动，替代原 3 秒轮询）。
        # 去抖：连续关闭多个文档时合并为一次刷新。
        LaTeXDB.schedule_parse_included_files()
        self.schedule_persistence()
        # 弹出状态下若已无 LaTeX 文档（全关 / 只剩 bibtex），自动收回独立窗口：
        # status page 无内容可显示，独立留着空窗口无意义。
        if self.preview_popped_out and self.get_root_or_active_latex_document() is None:
            self.pop_in_preview()

    def create_latex_document(self, attach_preview=True):
        '''创建 LaTeX 文档。

        attach_preview=True（默认）：立即挂接 BuildSystem / BuildWidget /
        Preview 工具链——新建文档、打开当前文件等「马上要用」的路径。
        attach_preview=False：只建轻量 Document，不建工具链——会话恢复的
        非活跃文档用；用户切到该文档时由 set_active_document 经
        _attach_latex_toolchain 补挂。Preview 会拉起整套 GTK 控件
        （布局器、页面渲染器、缩放管理器、右键菜单……），一次恢复 N 个
        文件就省下 N 套构造成本。
        '''
        document = Document('latex')
        # 设置加载回调，使文档读盘时能通知 workspace 显示/隐藏 spinner
        document._loading_callback = lambda action: self._on_document_loading(document, action)
        if attach_preview:
            self._attach_latex_toolchain(document)
        return document

    def _attach_latex_toolchain(self, document):
        '''为 LaTeX 文档挂接 BuildSystem / BuildWidget / Preview（幂等）。

        - 已挂接（document.preview 存在）或文档已 shutdown：直接返回。
        - 若有 _pending_document_data（DocumentSettings 在 toolchain 未就绪时
          暂存的 build log / PDF 状态），先应用再发通知，保证观察者在
          latex_toolchain_ready 时看到的是已恢复完整状态的文档。
        - 若文档已在 open_documents（会话恢复后补挂），发 latex_toolchain_ready
          让 preview panel / shortcutsbar 等补做 add_child 与信号连接；创建即
          挂接的场景不发（此时 new_document 信号自会携带完整文档）。
        '''
        if getattr(document, 'preview', None) is not None:
            return
        if getattr(document, '_is_shutdown', False):
            return

        # preview 的 presenter 在构造时即访问 document.build_system，
        # 故须先于 preview 创建 build_system / build_widget。
        document.build_system = build_system.BuildSystem(document)
        document.build_widget = build_widget.BuildWidget(document)
        document.preview = preview.Preview(document)
        # BuildSystem.__init__ 内原本在此连接 preview 的 pdf_changed 信号，
        # 因构造时 preview 尚不存在而推迟到此处（两者均已就绪）。
        document.preview.connect('pdf_changed', document.build_system.update_can_sync)

        pending = getattr(document, '_pending_document_data', None)
        if pending is not None:
            document._pending_document_data = None
            try:
                DocumentSettings.apply_pending_latex_state(document, pending)
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                # 收窄捕获范围：状态文件字段缺失/类型异常不应中断挂接流程，
                # 也不应吞掉编程错误（如 NameError）。
                print(f'Warning: could not apply pending latex state: {e}')

        if document in self.open_documents:
            self.add_change_code('latex_toolchain_ready', document)
            # 新挂接的工具链可能带有恢复出的编译日志（会话恢复路径），
            # 立即同步诊断高亮并刷新预览渲染器的激活状态。
            document.update_build_diagnostics()
            self.update_preview_visibility(document)

    def create_bibtex_document(self):
        document = Document('bibtex')
        document._loading_callback = lambda action: self._on_document_loading(document, action)
        return document

    def create_other_document(self):
        document = Document('other')
        document._loading_callback = lambda action: self._on_document_loading(document, action)
        return document

    def create_document_from_filename(self, filename, lazy=False, with_loading_indicator=True, attach_preview=True):
        # 文件名可能短于 4 字符（极端但合法），[-4:] 会返回整个字符串，
        # endswith 在此情形下仍能正确比较，且语义更清晰。
        if filename.endswith(('.tex', '.cls', '.sty', '.lco', '.loc')):
            # 类、样式及 KOMA Letter Option 文件都是 LaTeX 项目文件：以 LaTeX
            # 文档打开可让其 parser 参与项目侧栏、结构和跳转，而不会成为 root。
            document = self.create_latex_document(attach_preview=attach_preview)
        elif filename.endswith('.bib'):
            document = self.create_bibtex_document()
        else:
            # 兜底：用户通过"All Files"选择的非 TeX/BibTeX 文件（如 .txt/.md）
            # 作为纯文本(other)文档打开。
            document = self.create_other_document()
        document.set_filename(filename)
        if lazy:
            # 懒加载：构造文档（含 view 加入 Stack）但不读文件内容，
            # 由 schedule_lazy_load 调度 idle 后台读取。激活时同步加载。
            self.add_document(document)
            document.schedule_lazy_load()
            return document
        if with_loading_indicator:
            self._loading_start()
        try:
            response = document.populate_from_filename()
        finally:
            if with_loading_indicator:
                self._loading_finish()
        if response != False:
            self.add_document(document)
            return document
        else:
            return None

    def get_document_by_filename(self, filename):
        if filename == None: return None
        # normpath 涉及字符串复制与分隔符规整，提到循环外只算一次。
        # 原实现每次比较都重算 filename 与 document.filename 的 normpath，
        # N 个已打开文档时单次查找要做 2N+1 次 normpath。
        target = os.path.normpath(filename)
        for document in self.open_documents:
            doc_filename = document.get_filename()
            if doc_filename != None and os.path.normpath(doc_filename) == target:
                return document
        return None

    def get_active_document(self):
        return self.active_document

    def set_active_document(self, document):
        # 同一文档内跳转（如 Document Structure 点击）时，避免无谓发射
        # new_inactive_document / new_active_document 信号，防止 loading
        # spinner 全屏 overlay 显示/隐藏造成界面闪烁。
        if self.active_document is document:
            return

        if document is not None:
            # 激活前确保延迟构造的编辑器子系统（sticky_scroll / multicursor /
            # autocomplete 等）已就绪：激活信号会触发 shortcutsbar、presenter
            # 等观察者访问这些属性，不能等到 idle。
            document.ensure_editor_features()
            # LaTeX 工具链同理：preview panel / headerbar / build_log 都在
            # 激活信号路径上访问 document.preview / build_system。幂等，
            # 已挂接时立即返回。会话恢复的非活跃文档在此补挂工具链。
            if document.is_latex_document():
                self._attach_latex_toolchain(document)
            # 懒加载同步兜底：用户切换到尚未加载内容的文档时，取消其 idle 回调
            # 并立即读取文件内容。idle 后台加载虽已调度，但用户主动切换意味着
            # 要立即查看该文档——不能等 idle 排到它。
            if getattr(document, '_content_pending', False):
                self._loading_start()
                try:
                    document._load_content_if_pending()
                finally:
                    self._loading_finish()

        if self.active_document != None:
            self.add_change_code('new_inactive_document', self.active_document)
            previously_active_document = self.active_document
            self.active_document = document
            self.update_preview_visibility(previously_active_document)
        else:
            self.active_document = document

        if self.active_document != None:
            self.active_document.set_last_activated(time.time())
            self.update_preview_visibility(self.active_document)
            self.add_change_code('new_active_document', document)
            self.set_build_log()
        self.schedule_persistence()

    def set_build_log(self):
        document = self.get_root_or_active_latex_document()
        if document != None and hasattr(self, 'build_log'):
            self.build_log.set_document(document)

    def get_last_active_document(self):
        # max/min 是 O(n)，sorted 是 O(n log n)。仅取极值时无需排序。
        # 这两个方法在文档切换、关闭时被调用，文档数多时差异明显。
        try:
            return max(self.open_documents, key=lambda val: val.last_activated)
        except ValueError:
            return None

    def get_earliest_active_document(self):
        try:
            return min(self.open_documents, key=lambda val: val.last_activated)
        except ValueError:
            return None

    def _update_recently_opened(self, target_dict, filename, max_capacity,
                                change_code, date=None, notify=True):
        '''共享逻辑：检查文件存在 → 容量上限 evict → 更新字典 → 通知观察者。
        被 update_recently_opened_document 和 update_recently_opened_session_file
        共用，避免两处维护（容量阈值、evict 策略、change_code 名称）导致行为漂移。

        Args:
            target_dict: recently_opened_documents 或 recently_opened_session_files。
            max_capacity: 容量上限（document=50, session_file=15）。
            change_code: 通知观察者的 change code 名称。
            date: 条目日期；None 则取当前 time.time()。
            notify: 是否发射 change_code。批量恢复（populate_from_disk）传 False，
                由调用方在循环结束后统一发一次通知。
        '''
        if not isinstance(filename, str) or not os.path.isfile(filename):
            try:
                del(target_dict[filename])
            except KeyError:
                pass
        else:
            if date == None: date = time.time()
            # 容量上限触发时只删一个最旧条目；用 min O(n) 替代 sorted O(n log n)。
            # 统一为「insert 前 evict」（>= max），与 update_recently_opened_document
            # 原语义一致；session_file 原为「insert 后 evict」（> max），end-state
            # 容量相同，但统一后避免了「重新打开已存在文件时被 evict 的是刚插入
            # 的旧日期条目」的边界行为差异，语义更直观。
            if len(target_dict) >= max_capacity:
                oldest = min(target_dict.values(), key=lambda val: val['date'])
                del(target_dict[oldest['filename']])
            target_dict[filename] = {'filename': filename, 'date': date}
        if notify:
            self.add_change_code(change_code, target_dict)
            self.schedule_persistence()

    def update_recently_opened_document(self, filename, date=None, notify=True):
        self._update_recently_opened(
            self.recently_opened_documents, filename, 50,
            'update_recently_opened_documents', date, notify,
        )

    def remove_recently_opened_document(self, filename, notify=False):
        try:
            del(self.recently_opened_documents[filename])
        except KeyError:
            pass
        self.pinned_recent_documents.discard(filename)
        if notify:
            self.add_change_code('update_recently_opened_documents', self.recently_opened_documents)
            self.schedule_persistence()

    def clear_recently_opened_documents(self):
        '''Remove all entries from the recently-opened list and notify listeners.'''
        self.recently_opened_documents = dict()
        self.pinned_recent_documents = set()
        self.add_change_code('update_recently_opened_documents', self.recently_opened_documents)
        self.schedule_persistence()

    def toggle_pinned_recent_document(self, filename):
        '''置顶/取消置顶某个最近文档。变更通过 update_recently_opened_documents
        信号广播，使 welcome screen 重新排序并刷新行状态；持久化交由周期性
        save_to_disk（与 recently_opened_documents 同机制）。'''
        if filename in self.pinned_recent_documents:
            self.pinned_recent_documents.discard(filename)
        else:
            self.pinned_recent_documents.add(filename)
        self.add_change_code('update_recently_opened_documents', self.recently_opened_documents)
        self.schedule_persistence()

    def is_pinned_recent_document(self, filename):
        return filename in self.pinned_recent_documents

    def update_recently_opened_session_file(self, filename, date=None, notify=True):
        self._update_recently_opened(
            self.recently_opened_session_files, filename, 15,
            'update_recently_opened_session_files', date, notify,
        )

    def remove_recently_opened_session_file(self, filename):
        try:
            del(self.recently_opened_session_files[filename])
        except KeyError:
            pass

    def populate_from_disk(self):
        # 一次性迁移：旧 workspace.pickle → workspace.json。
        # workspace.pickle 是用户自己的配置文件（可信），用 load_pickle_trusted 迁移。
        # 旧 .pickle 文件保留作备份，不删除。
        json_path = os.path.join(self.pathname, 'workspace.json')
        pickle_path = os.path.join(self.pathname, 'workspace.pickle')
        migrate_pickle_to_json(json_path, pickle_path)
        data = load_json(json_path)
        if data is None:
            # 兼容：迁移失败或文件不存在时直接返回（保留原 EOFError 行为）
            self.add_change_code('update_recently_opened_documents', self.recently_opened_documents)
            self.add_change_code('update_recently_opened_session_files', self.recently_opened_session_files)
            return
        self._loading_start()
        try:
            try:
                root_document_filename = data['root_document_filename']
            except KeyError:
                root_document_filename = None
            active_filename = data.get('active_document_filename')
            # 懒加载：仅活跃文档同步读取文件内容，其余文档延迟到 idle 后台加载。
            # 启动时若有 N 个大 .tex 文件，原实现同步读 N 次（阻塞主线程），
            # 改为只读活跃文档 1 次，其余在 idle 中分批读取，主窗口更快可交互。
            # 排序保持原序（按 last_activated），活跃文档仍可能非末尾。
            for item in sorted(data['open_documents'].values(), key=lambda val: val['last_activated']):
                is_active = (item['filename'] == active_filename)
                # 非活跃文档：lazy 读盘 + 不挂 Preview/Build 工具链（激活时
                # set_active_document 补挂），把启动成本从 O(N) 降到 O(1)。
                document = self.create_document_from_filename(item['filename'], lazy=not is_active, with_loading_indicator=False, attach_preview=is_active)
                if document != None:
                    self._restore_document_state(document, item, root_document_filename)
            # 恢复未命名文档（临时内容文件）
            for item in sorted(data.get('untitled_documents', {}).values(), key=lambda val: val['last_activated']):
                is_active = (item.get('untitled_id') == active_filename)
                document = self._restore_untitled_document(item, attach_preview=is_active)
                if document is not None and is_active:
                    self._restore_active_filename = item.get('untitled_id')
            for item in data['recently_opened_documents'].values():
                self.update_recently_opened_document(item['filename'], item['date'], notify=False)
            try:
                self.pinned_recent_documents = set(data['pinned_recent_documents'])
            except KeyError:
                self.pinned_recent_documents = set()
            # update_recently_opened_document 已对不存在的文件调
            # remove_recently_opened_document（不添加到 dict），故无需再做一轮
            # stale 清理。原实现额外遍历 recently_opened_documents 逐个 os.path.isfile
            # 是冗余的二次 stat（上限 50 文件 × 2 = 100 次 stat）。recently_opened_documents
            # 在 __init__ 中初始化为空 dict，populate_from_disk 是启动时唯一填充点，
            # 不存在「加载前残留的过期条目」需要清理。
            try:
                self.help_panel.search_results_blank = data['recent_help_searches']
            except KeyError:
                pass
            try:
                recently_opened_session_files = data['recently_opened_session_files'].values()
            except KeyError:
                recently_opened_session_files = []
            for item in recently_opened_session_files:
                self.update_recently_opened_session_file(item['filename'], item['date'], notify=False)
            self._restore_active_filename = active_filename
            self.add_change_code('update_recently_opened_documents', self.recently_opened_documents)
            self.add_change_code('update_recently_opened_session_files', self.recently_opened_session_files)
        finally:
            self._loading_finish()

    def _restore_untitled_document(self, item, attach_preview=True):
        '''从 workspace.json 的 untitled_documents 条目恢复一个未命名文档。

        读取之前保存的临时内容文件，创建对应类型的文档并填充内容。
        如果临时文件不存在或读取失败，则静默跳过（不阻塞启动）。

        attach_preview：非活跃的 untitled 文档同样延迟挂接 Preview/Build
        工具链（与命名文档的会话恢复策略一致），激活时补挂。
        '''
        untitled_id = item.get('untitled_id')
        if not untitled_id:
            return None

        content = self._load_untitled_content(untitled_id)
        if content is None:
            return None

        doc_type = item.get('type', 'latex')
        if doc_type == 'latex':
            document = self.create_latex_document(attach_preview=attach_preview)
        elif doc_type == 'bibtex':
            document = self.create_bibtex_document()
        else:
            document = self.create_other_document()

        # 设置内容并重置修改标记，避免恢复后显示为"已修改"
        document.source_buffer.begin_irreversible_action()
        document.source_buffer.set_text(content)
        document.source_buffer.end_irreversible_action()
        document.source_buffer.set_modified(False)
        # 标记为未命名文档，记录 untitled_id 以便后续清理
        document._untitled_id = untitled_id
        document._untitled_content_saved = True

        # 先 add_document（会为未命名文档自动分配显示名），
        # 再恢复保存的显示名覆盖之
        self.add_document(document)
        displayname = item.get('displayname', document.get_displayname())
        document.set_displayname(displayname)
        self._restore_document_state(document, item, None)
        return document

    def _untitled_dir(self):
        '''返回存放未命名文档临时内容文件的目录路径。'''
        path = os.path.join(self.pathname, 'untitled')
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        return path

    def _untitled_content_path(self, untitled_id):
        '''返回指定 untitled_id 对应的临时内容文件路径。'''
        return os.path.join(self._untitled_dir(), f'{untitled_id}.content')

    def _save_untitled_content(self, document, untitled_id):
        '''将未命名文档的内容原子写入临时恢复文件。'''
        try:
            content = document.source_buffer.get_text(
                document.source_buffer.get_start_iter(),
                document.source_buffer.get_end_iter(),
                False
            )
            path = self._untitled_content_path(untitled_id)
            atomic_write_text(path, content)
            return True
        except Exception as e:
            print(f'Warning: could not save untitled content: {e}')
            return False

    def _load_untitled_content(self, untitled_id):
        '''从临时文件读取未命名文档的内容。'''
        try:
            path = self._untitled_content_path(untitled_id)
            if not os.path.isfile(path):
                return None
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f'Warning: could not load untitled content: {e}')
            return None

    def _cleanup_untitled_content(self, document):
        '''删除未命名文档的临时内容文件。'''
        untitled_id = getattr(document, '_untitled_id', None)
        if untitled_id:
            try:
                path = self._untitled_content_path(untitled_id)
                if os.path.isfile(path):
                    os.remove(path)
            except Exception as e:
                print(f'Warning: could not remove untitled content: {e}')

    def load_documents_from_session_file_with_spinner(self, filename):
        '''用户触发的 .stzs 会话文件打开：先显示 spinner，延迟约一帧再实际加载
        （16ms ≈ 60Hz 一帧，spinner 能画出第一帧即可）。'''
        main_window = ServiceLocator.get_main_window()
        if main_window is not None and hasattr(main_window, 'show_loading_spinner'):
            main_window.show_loading_spinner()
            GLib.timeout_add(16, self._do_load_documents_from_session_file, filename)
        else:
            self.load_documents_from_session_file(filename)

    def _do_load_documents_from_session_file(self, filename):
        '''timeout 回调：spinner 已渲染后执行实际的会话文件加载。'''
        success = self.load_documents_from_session_file(filename)
        if not success:
            # 加载失败（文件损坏/无可信结构）：load_documents_from_session_file
            # 未触发 _loading_start/finish，也不会 set_active_document，故
            # 显式 hide 兜底，避免 spinner 永久停留。
            main_window = ServiceLocator.get_main_window()
            if main_window is not None and hasattr(main_window, 'hide_loading_spinner'):
                main_window.hide_loading_spinner()
        return False

    def load_documents_from_session_file(self, filename):
        # .stzs 是用户交换文件，可能不可信：先试 JSON（新格式），失败回退到
        # RestrictedUnpickler（仅允许 builtins 容器类型，阻断 RCE）。
        data, was_pickle = try_migrate_session_file_pickle(filename)
        if data is None:
            # 文件既非合法 JSON 也非受限 pickle：原实现静默 return，用户无感知。
            # 改为弹 toast 告知是哪个文件加载失败（与 save_session 的失败反馈对称）。
            self._notify_session_load_error(filename)
            return False
        self._loading_start()
        try:
            try:
                root_document_filename = data['root_document_filename']
            except KeyError:
                root_document_filename = None
            active_filename = data.get('active_document_filename')
            opened_count = 0
            for item in sorted(data['open_documents'].values(), key=lambda val: val['last_activated']):
                is_active = (item['filename'] == active_filename)
                # 与 populate_from_disk 一致：非活跃文档 lazy 读盘且不挂
                # Preview/Build 工具链，激活时补挂；避免打开会话文件时为
                # 每个文档同步构造一整套 Preview GTK 控件。
                document = self.create_document_from_filename(item['filename'], lazy=not is_active, with_loading_indicator=False, attach_preview=is_active)
                if document is None:
                    continue
                opened_count += 1
                self._restore_document_state(document, item, root_document_filename)
            if len(self.open_documents) > 0:
                if active_filename:
                    target = next((d for d in self.open_documents if d.get_filename() == active_filename), None)
                    if target is not None:
                        self.set_active_document(target)
                    else:
                        self.set_active_document(self.open_documents[-1])
                else:
                    self.set_active_document(self.open_documents[-1])
            # 恢复窗口状态
            window_state = data.get('window_state')
            if window_state:
                self.show_symbols = window_state.get('show_symbols', self.show_symbols)
                self.show_document_structure = window_state.get('show_document_structure', self.show_document_structure)
                self.show_preview = window_state.get('show_preview', self.show_preview)
                self.show_help = window_state.get('show_help', self.show_help)
                self.show_build_log = window_state.get('show_build_log', self.show_build_log)
            self.session_file_opened = filename
            self.update_recently_opened_session_file(filename, notify=True)
            # 结构合法但没有任何文档成功打开（引用的 .tex/.bib 可能已被移动或删除）：
            # 仍视为加载成功（不回滚），但提示用户，避免"静默清空工作区"。
            if opened_count == 0 and len(data['open_documents']) > 0:
                self._notify_session_load_error(
                    filename,
                    _('Session loaded, but no documents could be opened (files may have been moved or deleted): {name}').format(name=os.path.basename(filename))
                )
            return True
        except (KeyError, TypeError, ValueError, AttributeError):
            # 结构残缺（缺 open_documents / data 非 dict 等）视为加载失败，提示用户。
            # 原实现会抛未捕获异常直接崩溃。
            self._notify_session_load_error(filename)
            return False
        finally:
            self._loading_finish()

    def _notify_session_load_error(self, filename, message=None):
        '''session 文件解析/结构失败，或加载后无任何文档打开时弹 toast 告知用户。'''
        main_window = ServiceLocator.get_main_window()
        if main_window and hasattr(main_window, 'toast_overlay'):
            if message is None:
                message = _('Could not open session: {name}').format(name=os.path.basename(filename))
            GLib.idle_add(self._do_show_toast, main_window, message)

    def _restore_document_state(self, document, item, root_document_filename):
        '''从会话数据项恢复单个文档的状态：last_activated / cursor_offset /
        scroll_offset / folded_regions / root 标记。populate_from_disk
        （workspace.json）和 load_documents_from_session_file（.stzs）共用
        此逻辑，避免两处重复维护导致行为漂移。

        与 _collect_open_documents_data 对称：前者负责「保存时收集」，
        本方法负责「加载时恢复」。

        Args:
            document: 已通过 create_document_from_filename 构造的文档对象。
            item: 会话数据项 dict（含 last_activated、cursor_offset 等可选字段）。
            root_document_filename: 会话记录的 root 文档名；None 表示无 root。
                document 的 filename 与之相等时调 set_one_document_root。
        '''
        document.set_last_activated(item['last_activated'])
        if 'cursor_offset' in item:
            document._restore_cursor_offset = item['cursor_offset']
        if 'selection_bound_offset' in item:
            document._restore_selection_bound_offset = item['selection_bound_offset']
        if 'scroll_offset' in item:
            document._restore_scroll_offset = item['scroll_offset']
        if 'folded_regions' in item:
            document.code_folding.set_initial_folded_regions(item['folded_regions'])
        # 未命名文档的 item 中无 'filename' 键（只有 'untitled_id'），
        # 且未命名文档不可能被设为 root，故跳过 root 匹配检查。
        if 'filename' in item and item['filename'] == root_document_filename:
            # 根文档即使非活跃也必须挂接 Build/Preview 工具链：headerbar
            # （build 按钮）、build_log、预览面板都以「根文档优先」取对象，
            # 而这些访问不经 set_active_document。先挂接再设 root，保证
            # root_state_change 的观察者（preview_panel.set_preview_document
            # 等）看到的是带 preview 的完整文档。
            self._attach_latex_toolchain(document)
            self.set_one_document_root(document)

    def _collect_open_documents_data(self):
        '''收集所有已命名文档的会话状态（文件名、最后激活时间、光标位置、
        滚动偏移、折叠区域）。save_to_disk（workspace.json）和 save_session
        （.stzs 文件）共用此逻辑，避免两处重复维护导致行为漂移。

        每个字段单独 try/except：某文档的 source_buffer 已销毁或 code_folding
        不可用时不应影响其他字段的收集——缺字段读取时按默认值恢复。

        未命名文档（filename == None）也会被收集到 untitled_documents 中，
        其内容保存到临时文件，以便下次启动时恢复。
        '''
        open_documents = dict()
        untitled_documents = dict()
        for document in self.open_documents:
            filename = document.get_filename()
            if filename != None:
                doc_data = {
                    'filename': filename,
                    'last_activated': document.get_last_activated()
                }
                try:
                    cursor_offset = document.source_buffer.get_property('cursor-position')
                    doc_data['cursor_offset'] = cursor_offset
                    if document.source_buffer.get_has_selection():
                        doc_data['selection_bound_offset'] = document.source_buffer.get_iter_at_mark(
                            document.source_buffer.get_selection_bound()).get_offset()
                except Exception:
                    pass
                try:
                    scroll_offset = document.view.scrolled_window.get_vadjustment().get_value()
                    doc_data['scroll_offset'] = scroll_offset
                except Exception:
                    pass
                try:
                    folded_regions = document.code_folding.get_folded_regions()
                    if folded_regions:
                        doc_data['folded_regions'] = folded_regions
                except Exception:
                    pass
                open_documents[filename] = doc_data
            else:
                # 未命名文档：生成唯一 ID，保存内容到临时文件
                untitled_id = getattr(document, '_untitled_id', None)
                if untitled_id is None:
                    untitled_id = str(uuid.uuid4())
                    document._untitled_id = untitled_id
                # 保存内容到临时文件
                self._save_untitled_content(document, untitled_id)
                document._untitled_content_saved = True

                doc_data = {
                    'untitled_id': untitled_id,
                    'last_activated': document.get_last_activated(),
                    'displayname': document.get_displayname(),
                    'type': document.language,
                }
                try:
                    cursor_offset = document.source_buffer.get_property('cursor-position')
                    doc_data['cursor_offset'] = cursor_offset
                    if document.source_buffer.get_has_selection():
                        doc_data['selection_bound_offset'] = document.source_buffer.get_iter_at_mark(
                            document.source_buffer.get_selection_bound()).get_offset()
                except Exception:
                    pass
                try:
                    scroll_offset = document.view.scrolled_window.get_vadjustment().get_value()
                    doc_data['scroll_offset'] = scroll_offset
                except Exception:
                    pass
                try:
                    folded_regions = document.code_folding.get_folded_regions()
                    if folded_regions:
                        doc_data['folded_regions'] = folded_regions
                except Exception:
                    pass
                untitled_documents[untitled_id] = doc_data
        return open_documents, untitled_documents

    def schedule_persistence(self):
        '''在短暂空闲后保存工作区快照；连续状态变更只保留最后一次写入。'''
        self._cancel_scheduled_persistence()
        self._persistence_source_id = GLib.timeout_add(
            self.PERSISTENCE_DELAY_MS, self._flush_scheduled_persistence)

    def _cancel_scheduled_persistence(self):
        source_id = self._persistence_source_id
        self._persistence_source_id = None
        if source_id is not None:
            try:
                GLib.Source.remove(source_id)
            except (ValueError, RuntimeError):
                pass

    def _flush_scheduled_persistence(self):
        self._persistence_source_id = None
        self.save_to_disk()
        return False

    def flush_persistence(self):
        '''取消挂起的去抖保存并立即写入，供正常退出路径调用。'''
        self._cancel_scheduled_persistence()
        return self.save_to_disk()

    def _watch_document_state(self, document):
        '''监听会话恢复所需的低成本视图状态，不监听正文内容。'''
        handlers = []
        try:
            handlers.append((document.source_buffer,
                             document.source_buffer.connect(
                                 'notify::cursor-position',
                                 self._on_document_state_changed)))
        except (AttributeError, TypeError):
            pass
        try:
            adjustment = document.view.scrolled_window.get_vadjustment()
            handlers.append((adjustment, adjustment.connect(
                'value-changed', self._on_document_state_changed)))
        except (AttributeError, TypeError):
            pass
        self._document_state_handlers[document] = handlers

    def _unwatch_document_state(self, document):
        for target, handler_id in self._document_state_handlers.pop(document, []):
            try:
                target.disconnect(handler_id)
            except (TypeError, ValueError, RuntimeError):
                pass

    def _on_document_state_changed(self, *args):
        self.schedule_persistence()

    def save_to_disk(self):
        # 写入 workspace.json（原子替换）。旧 workspace.pickle 保留作备份。
        self._cancel_scheduled_persistence()
        json_path = os.path.join(self.pathname, 'workspace.json')
        open_documents, untitled_documents = self._collect_open_documents_data()
        data = {
            'open_documents': open_documents,
            'untitled_documents': untitled_documents,
            'recently_opened_documents': self.recently_opened_documents,
            'recently_opened_session_files': self.recently_opened_session_files,
            'pinned_recent_documents': list(self.pinned_recent_documents),
            'recent_help_searches': getattr(self, 'help_panel', None) and self.help_panel.search_results_blank
        }
        if self.active_document is not None:
            data['active_document_filename'] = self.active_document.get_filename()
            # 如果活动文档是未命名文档，用 untitled_id 作为 active_document_filename
            if self.active_document.get_filename() is None:
                untitled_id = getattr(self.active_document, '_untitled_id', None)
                if untitled_id:
                    data['active_document_filename'] = untitled_id
        if self.root_document != None:
            data['root_document_filename'] = self.root_document.get_filename()
        try:
            save_json(json_path, data)
        except (OSError, TypeError, ValueError) as e:
            self._show_persistence_warning(_('Could not save workspace state: {error}').format(error=str(e)))
            return False
        else:
            self._persistence_warning_shown = False
            return True

    _persistence_warning_shown = False

    def _show_persistence_warning(self, message):
        '''工作区状态写入失败时弹 toast 通知用户。用 _persistence_warning_shown
        标志避免周期性保存持续失败时反复弹 toast——仅首次失败提示一次。'''
        if self._persistence_warning_shown:
            return
        self._persistence_warning_shown = True
        main_window = ServiceLocator.get_main_window()
        if main_window and hasattr(main_window, 'toast_overlay'):
            GLib.idle_add(self._do_show_toast, main_window, message)

    def _do_show_toast(self, main_window, message):
        toast = Adw.Toast.new(message)
        toast.set_timeout(5)
        main_window.toast_overlay.add_toast(toast)
        return False

    def save_session(self, session_filename):
        # 写入 .stzs（JSON 格式）。读取时支持 JSON 与受限 pickle 双路径
        # （load_documents_from_session_file），旧版 Setzer 创建的 .stzs 仍可打开。
        # 注意：save_session 不保存未命名文档（它们没有文件名，无法在另一台机器上恢复）。
        open_documents, _ = self._collect_open_documents_data()
        data = {'open_documents': open_documents}
        if self.active_document is not None:
            data['active_document_filename'] = self.active_document.get_filename()
        if self.root_document != None:
            data['root_document_filename'] = self.root_document.get_filename()
        data['window_state'] = {
            'show_symbols': self.show_symbols,
            'show_document_structure': self.show_document_structure,
            'show_preview': self.show_preview,
            'show_help': self.show_help,
            'show_build_log': self.show_build_log,
        }
        # save_json 可能因磁盘满/权限失败。捕获并返回 False，让调用方提示用户。
        try:
            save_json(session_filename, data)
        except (OSError, TypeError, ValueError):
            return False
        self.session_file_opened = session_filename
        self.update_recently_opened_session_file(session_filename, notify=True)
        return True

    def get_unsaved_documents(self):
        unsaved_documents = list()
        for document in self.open_documents:
            if document.source_buffer.get_modified():
                unsaved_documents.append(document)
        return unsaved_documents

    def get_all_documents(self):
        return self.open_documents.copy()

    def set_one_document_root(self, root_document):
        if root_document.is_latex_document():
            self.root_document = root_document
            for document in self.open_latex_documents:
                if document == root_document:
                    document.set_root_state(True, True)
                else:
                    document.set_root_state(False, True)
                self.update_preview_visibility(document)
            self.add_change_code('root_state_change', 'one_document')
            self.set_build_log()
            self.schedule_persistence()
            # 根文档切换后，重新分发编译诊断高亮（例如会话恢复后根文档已带日志）。
            self.distribute_build_diagnostics()

    def distribute_build_diagnostics(self):
        '''把所有打开的 LaTeX 文档的诊断高亮对齐到根文档当前编译日志。'''
        for document in self.open_latex_documents:
            document.update_build_diagnostics()

    def unset_root_document(self):
        for document in self.open_latex_documents:
            document.set_root_state(False, False)
            self.update_preview_visibility(document)
        self.root_document = None
        self.update_preview_visibility(self.active_document)
        self.add_change_code('root_state_change', 'no_root_document')
        self.set_build_log()
        self.schedule_persistence()

    def get_root_document(self):
        return self.root_document

    def get_active_latex_document(self):
        if self.get_active_document() == None:
            return None
        if self.active_document.is_latex_document():
            return self.active_document
        return None

    def get_root_or_active_latex_document(self):
        """Return the explicit project root or active LaTeX document without side effects."""
        if self.get_active_document() == None:
            return None
        if self.root_document != None:
            return self.root_document
        if self.active_document.is_latex_document():
            return self.active_document
        return None

    def _get_project_root_filename(self, document):
        if document is None or document.get_filename() is None:
            return None
        configuration = ProjectBuildConfiguration.discover(document.get_filename())
        if configuration is None:
            return None
        root_document = configuration.load().get('root_document')
        root_filename = configuration.effective_path(root_document)
        if root_filename and os.path.isfile(root_filename):
            return root_filename
        return None

    def _open_build_root_if_needed(self, root_filename, active_document):
        if root_filename is None or root_filename == os.path.abspath(
                active_document.get_filename()):
            return active_document
        for candidate in self.open_latex_documents:
            if candidate.get_filename() == root_filename:
                self._attach_latex_toolchain(candidate)
                return candidate
        root_document = self.open_document_by_filename(root_filename)
        if root_document is not None and root_document.is_latex_document():
            self.set_active_document(active_document)
            return root_document
        return active_document

    def get_magic_root_or_active_latex_document(self):
        """Resolve the active file's valid Magic Comment root for a build only.

        This is deliberately separate from ``get_root_or_active_latex_document``:
        the latter is also used by UI sensitivity and preview queries, where
        opening a referenced root file would be an unexpected side effect.
        """
        document = self.get_root_or_active_latex_document()
        if document is None or self.root_document is not None:
            return document

        active_document = self.active_document
        project_root_filename = self._get_project_root_filename(active_document)
        if project_root_filename is not None:
            return self._open_build_root_if_needed(
                project_root_filename, active_document)

        magic = parse_magic_comments(active_document.get_all_text())
        root_filename = resolve_root_filename(active_document.get_filename(), magic.root)
        return self._open_build_root_if_needed(root_filename, active_document)

    def update_preview_visibility(self, document):
        if document != None and document.is_latex_document():
            # preview 可能尚未挂接（会话恢复的非活跃文档），无渲染器则无
            # 激活/停用可言，直接跳过——挂接时 _attach_latex_toolchain 会
            # 再调一次本方法补齐状态。
            doc_preview = getattr(document, 'preview', None)
            if doc_preview is None:
                return
            if document == self.root_document:
                doc_preview.page_renderer.activate()
            elif document == self.active_document and self.root_document == None:
                doc_preview.page_renderer.activate()
            else:
                doc_preview.page_renderer.deactivate()

    def is_preview_popped_out(self):
        return self.preview_popped_out

    def pop_out_preview(self):
        '''把 PDF 预览从侧边栏弹出为独立窗口。

        整体 reparent main_window.preview_panel 到 PdfPreviewWindow：
        - 工具栏（缩放/页码/recolor/external viewer）随 panel 一起进入独立窗口
        - stack 内含所有文档的 preview.view，切文档时独立窗口自动跟随
        - 模型↔view 引用不变，SyncTeX 双向跳转继续工作

        侧边栏只保留帮助页面（无 status page、无 switch button）：preview_panel
        搬走后 stack 切到 'help'。右侧栏收起一次——pop_out 时将 show_preview 置
        False（保存原值），update_preview_help_visibility 的 popped_out 分支不再
        把 show_preview 当作展开理由，仅 help 可展开侧栏。用户可开关 help 来
        展开/收起侧栏。pop_in 时恢复原 show_preview 值。
        '''
        if self.preview_popped_out:
            return
        # 必须有 LaTeX 文档才有预览可弹。
        if self.get_root_or_active_latex_document() is None:
            return
        if self.main_window is None:
            return

        # 懒创建独立窗口。
        if self.pdf_preview_window is None:
            self.pdf_preview_window = pdf_preview_window.PdfPreviewWindow(self)

        # reparent preview_panel：从 preview_help_stack 取出，放进独立窗口。
        panel = self.main_window.preview_panel
        stack = self.main_window.preview_help_stack
        current_visible = stack.get_visible_child_name()
        try:
            stack.remove(panel)
        except Exception:
            pass
        # 侧边栏切到 help：预览已弹出，侧栏只保留帮助（无 status page）。
        # 仅当用户当前在看 preview 时才切（若已在看 help 则不动）。
        if current_visible == 'preview':
            stack.set_visible_child_name('help')
        # panel 进入独立窗口（连接 sync 信号 + 更新标题）。
        self.pdf_preview_window.set_panel(panel)

        self.preview_popped_out = True
        # 隐藏 panel 内的 switch_button（预览/帮助互切）：help 留在侧边栏，
        # 独立窗口里这个按钮无意义。pop_in 时恢复。
        try:
            panel.switch_button.set_visible(False)
        except AttributeError:
            pass

        # 主动收起侧栏一次（pop_out 的视觉反馈）：无论 show_preview / show_help
        # 当前状态如何，都收起侧栏。保存原值，pop_in 时恢复。
        # 用户可手动重新展开侧栏（toggle 会设 show_help=True 显示帮助页面）。
        self._show_preview_before_popout = self.show_preview
        self._show_help_before_popout = self.show_help
        if self.show_preview or self.show_help:
            self.set_show_preview_or_help(False, False)
        else:
            self.presenter.update_preview_help_visibility(False)

        self.pdf_preview_window.present()
        self.add_change_code('preview_pop_state_changed', True)

    def pop_in_preview(self):
        '''把 PDF 预览从独立窗口收回侧边栏。'''
        if not self.preview_popped_out:
            return
        if self.main_window is None or self.pdf_preview_window is None:
            self.preview_popped_out = False
            return

        # 从独立窗口取回 panel。
        panel = self.pdf_preview_window.take_panel()
        if panel is None:
            panel = self.main_window.preview_panel

        # reparent 回 preview_help_stack 的 'preview' 槽位。
        stack = self.main_window.preview_help_stack
        try:
            stack.add_named(panel, 'preview')
        except Exception:
            pass
        stack.set_visible_child_name('preview')

        # 恢复 switch_button 可见。
        try:
            panel.switch_button.set_visible(True)
        except (AttributeError, RuntimeError):
            pass

        self.preview_popped_out = False

        # 隐藏独立窗口（保留对象以便下次弹出复用几何状态）。
        self.pdf_preview_window.set_visible(False)

        # 恢复 pop_out 时保存的 show_preview / show_help 值：pop_out 将两者
        # 都置 False 以主动收起侧栏，现在预览回到侧栏，需恢复原值。
        self.main_window.preview_panel.presenter._sync_switch_icons()
        saved_preview = getattr(self, '_show_preview_before_popout', None)
        saved_help = getattr(self, '_show_help_before_popout', None)
        self._show_preview_before_popout = None
        self._show_help_before_popout = None
        target_preview = saved_preview if saved_preview is not None else self.show_preview
        target_help = saved_help if saved_help is not None else self.show_help
        if target_preview != self.show_preview or target_help != self.show_help:
            self.set_show_preview_or_help(target_preview, target_help)
        else:
            self.presenter.update_preview_help_visibility(False)

        # 在所有 reparent 和布局操作完成后，延迟更新缩放。
        # 确保从独立窗口回到侧边栏后，fit 模式的缩放能正确适应新宽度。
        self.pdf_preview_window.schedule_zoom_update()

        self.add_change_code('preview_pop_state_changed', False)

    def set_show_preview_or_help(self, show_preview, show_help):
        if show_preview != self.show_preview or show_help != self.show_help:
            self.show_preview = show_preview
            self.show_help = show_help
            self.settings.set_value('window_state', 'show_preview', show_preview)
            self.settings.set_value('window_state', 'show_help', show_help)
            self.add_change_code('set_show_preview_or_help')

    def set_show_symbols_or_document_structure(self, show_symbols, show_document_structure):
        if show_symbols != self.show_symbols or show_document_structure != self.show_document_structure:
            self.show_symbols = show_symbols
            self.show_document_structure = show_document_structure
            # 同步"上次选中面板"，供隐藏后恢复
            if show_symbols:
                self.set_sidebar_page('symbols')
            elif show_document_structure:
                self.set_sidebar_page('document_structure')
            self.settings.set_value('window_state', 'show_symbols', show_symbols)
            self.settings.set_value('window_state', 'show_document_structure', show_document_structure)
            self.add_change_code('set_show_symbols_or_document_structure')

    def set_sidebar_page(self, page):
        '''记忆侧栏当前选中的面板，隐藏后再次显示时恢复。'''
        if page not in ('symbols', 'document_structure'):
            return
        if page != self.sidebar_page:
            self.sidebar_page = page
            self.settings.set_value('window_state', 'sidebar_page', page)

    def set_show_sidebar(self, show):
        if not show:
            self.set_show_symbols_or_document_structure(False, False)
        else:
            if not self.show_symbols and not self.show_document_structure:
                # 之前未选中任何面板（隐藏过）：恢复上次记忆的面板，而非硬编码 Symbols
                if self.sidebar_page == 'document_structure':
                    self.set_show_symbols_or_document_structure(False, True)
                else:
                    self.set_show_symbols_or_document_structure(True, False)
            else:
                self.set_show_symbols_or_document_structure(self.show_symbols, self.show_document_structure)

    def set_show_build_log(self, show_build_log):
        if show_build_log != self.show_build_log:
            self.show_build_log = show_build_log
            self.settings.set_value('window_state', 'show_build_log', show_build_log)
            self.add_change_code('show_build_log_state_change', show_build_log)

    def get_show_build_log(self):
        if self.show_build_log != None:
            return self.show_build_log
        else:
            return False
