#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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

from setzer.document.document import Document
import setzer.document.build_system.build_system as build_system
import setzer.document.build_widget.build_widget as build_widget
import setzer.document.preview.preview as preview
from setzer.helpers.observable import Observable
from setzer.helpers.persistence import (
    load_json, save_json, migrate_pickle_to_json,
    try_migrate_session_file_pickle,
)
import setzer.workspace.workspace_presenter as workspace_presenter
import setzer.workspace.workspace_controller as workspace_controller
import setzer.workspace.preview_panel.preview_panel as preview_panel
import setzer.workspace.help_panel.help_panel as help_panel
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

    def __init__(self):
        Observable.__init__(self)
        self.pathname = ServiceLocator.get_config_folder()

        self.open_documents = list()
        self.open_latex_documents = list()
        self.root_document = None
        self.recently_opened_documents = dict()

        self.active_document = None

        self.recently_opened_session_files = dict()
        self.session_file_opened = None

        self.settings = ServiceLocator.get_settings()

        self.show_build_log = self.settings.get_value('window_state', 'show_build_log')
        self.show_preview = self.settings.get_value('window_state', 'show_preview')
        self.show_help = self.settings.get_value('window_state', 'show_help')
        self.show_symbols = self.settings.get_value('window_state', 'show_symbols')
        self.show_document_structure = self.settings.get_value('window_state', 'show_document_structure')

    def init_workspace_controller(self):
        self.welcome_screen = welcome_screen.WelcomeScreen(self)
        self.sidebar = sidebar.Sidebar(self)
        self.actions = actions.Actions(self)
        self.shortcutsbar = shortcutsbar.Shortcutsbar(self)
        self.context_menu = context_menu.ContextMenu(self)
        self.presenter = workspace_presenter.WorkspacePresenter(self)
        self.headerbar = headerbar.Headerbar(self)
        self.preview_panel = preview_panel.PreviewPanel(self)
        self.help_panel = help_panel.HelpPanel(self)
        self.build_log = build_log.BuildLog(self)
        self.auto_build = auto_build.AutoBuild(self)
        self.auto_save = auto_save.AutoSave(self)
        self.controller = workspace_controller.WorkspaceController(self)

    def open_document_by_filename(self, filename):
        if filename == None: return None

        document_candidate = self.get_document_by_filename(filename)
        if document_candidate != None:
            self.set_active_document(document_candidate)
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
        DocumentSettings.load_document_state(document)
        self.add_change_code('new_document', document)
        self.update_recently_opened_document(document.get_filename(), notify=True)
        # 刷新 LaTeXDB 的 label/bibitem 数据库（事件驱动，替代原 3 秒轮询）。
        # 去抖：会话恢复连续打开 N 个文档时，N 次 schedule 仅触发 1 次
        # parse_included_files（idle 合并），避免 N 次全量 stat/read 扫描。
        LaTeXDB.schedule_parse_included_files()

    def remove_document(self, document):
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
        # _init_latex_features idle 回调。详见 Document.shutdown 文档。
        try:
            document.shutdown()
        except Exception as e:
            print(f'Warning: document.shutdown() failed: {e}')

        self.open_documents.remove(document)
        if document.is_latex_document():
            self.open_latex_documents.remove(document)
            try:
                document.build_system.shutdown()
            except Exception as e:
                print(f'Warning: document.build_system.shutdown() failed: {e}')
            # 释放预览渲染器的 50ms 轮询定时器（后台线程靠 is_active=False 空转，
            # 随进程退出）。避免关闭文档后定时器常驻泄漏。
            try:
                document.preview.page_renderer.shutdown()
            except Exception as e:
                print(f'Warning: document.preview.page_renderer.shutdown() failed: {e}')
        if self.active_document == document:
            candidate = self.get_last_active_document()
            if candidate == None:
                self.set_active_document(None)
            else:
                self.set_active_document(candidate)
        self.add_change_code('document_removed', document)
        # 文档列表已变，刷新 LaTeXDB（事件驱动，替代原 3 秒轮询）。
        # 去抖：连续关闭多个文档时合并为一次刷新。
        LaTeXDB.schedule_parse_included_files()

    def create_latex_document(self):
        document = Document('latex')
        # preview 的 presenter 在构造时即访问 document.build_system，
        # 故须先于 preview 创建 build_system / build_widget。
        document.build_system = build_system.BuildSystem(document)
        document.build_widget = build_widget.BuildWidget(document)
        document.preview = preview.Preview(document)
        # BuildSystem.__init__ 内原本在此连接 preview 的 pdf_changed 信号，
        # 因构造时 preview 尚不存在而推迟到此处（两者均已就绪）。
        document.preview.connect('pdf_changed', document.build_system.update_can_sync)
        return document

    def create_bibtex_document(self):
        document = Document('bibtex')
        return document

    def create_other_document(self):
        document = Document('other')
        return document

    def create_document_from_filename(self, filename, lazy=False):
        # 文件名可能短于 4 字符（极端但合法），[-4:] 会返回整个字符串，
        # endswith 在此情形下仍能正确比较，且语义更清晰。
        if filename.endswith('.tex'):
            document = self.create_latex_document()
        elif filename.endswith('.bib'):
            document = self.create_bibtex_document()
        elif filename.endswith('.cls') or filename.endswith('.sty'):
            document = self.create_other_document()
        else:
            return None
        document.set_filename(filename)
        if lazy:
            # 懒加载：构造文档（含 view 加入 Stack）但不读文件内容，
            # 由 schedule_lazy_load 调度 idle 后台读取。激活时同步加载。
            self.add_document(document)
            document.schedule_lazy_load()
            return document
        response = document.populate_from_filename()
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
        # 懒加载同步兜底：用户切换到尚未加载内容的文档时，取消其 idle 回调
        # 并立即读取文件内容。idle 后台加载虽已调度，但用户主动切换意味着
        # 要立即查看该文档——不能等 idle 排到它。
        if document is not None and document._content_pending:
            document._load_content_if_pending()

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

    def set_build_log(self):
        document = self.get_root_or_active_latex_document()
        if document != None:
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
        if notify:
            self.add_change_code('update_recently_opened_documents', self.recently_opened_documents)

    def clear_recently_opened_documents(self):
        '''Remove all entries from the recently-opened list and notify listeners.'''
        self.recently_opened_documents = dict()
        self.add_change_code('update_recently_opened_documents', self.recently_opened_documents)

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
            document = self.create_document_from_filename(item['filename'], lazy=not is_active)
            if document != None:
                self._restore_document_state(document, item, root_document_filename)
        for item in data['recently_opened_documents'].values():
            self.update_recently_opened_document(item['filename'], item['date'], notify=False)
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

    def load_documents_from_session_file(self, filename):
        # .stzs 是用户交换文件，可能不可信：先试 JSON（新格式），失败回退到
        # RestrictedUnpickler（仅允许 builtins 容器类型，阻断 RCE）。
        data, was_pickle = try_migrate_session_file_pickle(filename)
        if data is None:
            return
        try:
            root_document_filename = data['root_document_filename']
        except KeyError:
            root_document_filename = None
        active_filename = data.get('active_document_filename')
        for item in sorted(data['open_documents'].values(), key=lambda val: val['last_activated']):
            document = self.create_document_from_filename(item['filename'])
            if document is None:
                continue
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
        if 'scroll_offset' in item:
            document._restore_scroll_offset = item['scroll_offset']
        if 'folded_regions' in item:
            document.code_folding.set_initial_folded_regions(item['folded_regions'])
        if item['filename'] == root_document_filename:
            self.set_one_document_root(document)

    def _collect_open_documents_data(self):
        '''收集所有已命名文档的会话状态（文件名、最后激活时间、光标位置、
        滚动偏移、折叠区域）。save_to_disk（workspace.json）和 save_session
        （.stzs 文件）共用此逻辑，避免两处重复维护导致行为漂移。

        每个字段单独 try/except：某文档的 source_buffer 已销毁或 code_folding
        不可用时不应影响其他字段的收集——缺字段读取时按默认值恢复。
        '''
        open_documents = dict()
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
        return open_documents

    def save_to_disk(self):
        # 写入 workspace.json（原子替换）。旧 workspace.pickle 保留作备份。
        json_path = os.path.join(self.pathname, 'workspace.json')
        open_documents = self._collect_open_documents_data()
        data = {
            'open_documents': open_documents,
            'recently_opened_documents': self.recently_opened_documents,
            'recently_opened_session_files': self.recently_opened_session_files,
            'recent_help_searches': getattr(self, 'help_panel', None) and self.help_panel.search_results_blank
        }
        if self.active_document is not None:
            data['active_document_filename'] = self.active_document.get_filename()
        if self.root_document != None:
            data['root_document_filename'] = self.root_document.get_filename()
        try:
            save_json(json_path, data)
        except (OSError, TypeError, ValueError) as e:
            self._show_persistence_warning(_('Could not save workspace state: {error}').format(error=str(e)))
        else:
            self._persistence_warning_shown = False

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
        open_documents = self._collect_open_documents_data()
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

    def unset_root_document(self):
        for document in self.open_latex_documents:
            document.set_root_state(False, False)
            self.update_preview_visibility(document)
        self.root_document = None
        self.update_preview_visibility(self.active_document)
        self.add_change_code('root_state_change', 'no_root_document')
        self.set_build_log()

    def get_root_document(self):
        return self.root_document

    def get_active_latex_document(self):
        if self.get_active_document() == None:
            return None
        if self.active_document.is_latex_document():
            return self.active_document
        return None

    def get_root_or_active_latex_document(self):
        if self.get_active_document() == None:
            return None
        else:
            if self.root_document != None:
                return self.root_document
            elif self.active_document.is_latex_document():
                return self.active_document
            else:
                return None

    def update_preview_visibility(self, document):
        if document != None and document.is_latex_document():
            if document == self.root_document:
                document.preview.page_renderer.activate()
            elif document == self.active_document and self.root_document == None:
                document.preview.page_renderer.activate()
            else:
                document.preview.page_renderer.deactivate()

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
            self.settings.set_value('window_state', 'show_symbols', show_symbols)
            self.settings.set_value('window_state', 'show_document_structure', show_document_structure)
            self.add_change_code('set_show_symbols_or_document_structure')

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


