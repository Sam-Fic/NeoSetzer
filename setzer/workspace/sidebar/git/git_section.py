#!/usr/bin/env python3
# coding: utf-8

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

import builtins


def _(s):
    '''翻译函数。运行时委托 builtins._（由 setzer.in 注入 trans.gettext），
    未注入时回退到原字符串——便于开发/测试。'''
    fn = getattr(builtins, '_', None)
    return s if fn is None else fn(s)


import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk

import os
import threading

from setzer.app.service_locator import ServiceLocator
from setzer.document.build_system.phitex_session.phitex_session import PhitexSession
from setzer.helpers import phitex_compare
from setzer.vcs.git_repository import GitRepository, git_available
from setzer.workspace.sidebar.git.git_section_viewgtk import GitSectionView
from setzer.workspace.sidebar.git.diff_dialog import DiffWindow

# PDF diff（PhiTeX session compare）适用的文件扩展：与
# workspace.create_document_from_filename 的可编译集合一致。
LATEX_DIFF_EXTENSIONS = ('.tex', '.cls', '.sty', '.lco', '.loc', '.bib')

# session compare 首跑要 prime + 重排，宽裕上限同 AiDiff.COMPARE_TIMEOUT_S。
PDF_DIFF_TIMEOUT_S = 600


class GitSection(object):
    '''Git 侧栏面板控制器（#443）。VSCode Source Control 式交互：
    Changes 档内直接提交（message 输入 + 文件勾选，无需弹窗），文件行支持
    勾选纳入提交 / 点击看 diff / 行内 Discard；Commits 档展示线性历史。

    升级为独立面板页后，「不适用」不再隐藏整个分区，而是页内空状态：
    无已保存文档 → No Document；git 集成偏好关 / git 未安装 → Unavailable；
    文档已保存但不在 repo 内 → 「初始化仓库」入口（快捷初始化）。

    刷新时机（事件驱动，无轮询）：活动文档切换、文档保存、仓库状态信号
    （.git/index、.git/HEAD 的 Gio.FileMonitor 覆盖终端里的 commit/pull）。

    会话内记忆（不持久化）：commit message 按仓库根记忆（_messages），
    文件勾选状态按仓库根记忆（_checked），切换仓库/文档后恢复。
    '''

    def __init__(self, workspace):
        self.workspace = workspace
        self.settings = ServiceLocator.get_settings()
        self.document = None
        self.repo = None

        self.view = GitSectionView()
        self.view.button_pull.connect('clicked', self.on_pull_clicked)
        self.view.button_push.connect('clicked', self.on_push_clicked)
        self.view.button_commit.connect('clicked', self.on_commit_clicked)
        self.view.button_init.connect('clicked', self.on_init_clicked)
        self.view.message_text.get_buffer().connect('changed', self.on_message_changed)

        # Ctrl+Enter 在输入框内直接提交（VSCode 习惯键位）。
        key_controller = Gtk.EventControllerKey()
        self.view.message_text.add_controller(key_controller)
        key_controller.connect('key-pressed', self.on_message_key_pressed)

        self._messages = dict()   # repo root → 未提交的 commit message
        self._checked = dict()    # repo root → {path: 是否纳入提交}
        self._pdf_diff_running = False
        self._saved_document = None

        workspace.connect('new_active_document', self.on_new_active_document)
        self.settings.connect('settings_changed', self.on_settings_changed)
        self.set_document()

    def on_new_active_document(self, workspace, document):
        self.set_document()

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if section == 'preferences' and item in ('git_integration', 'git_sidebar_panel'):
            self.set_document()

    def set_document(self):
        document = self.workspace.get_active_document()

        # 'saved' 信号跟随活动文档：保存后刷新仓库状态
        if document is not self._saved_document:
            if self._saved_document is not None:
                try:
                    self._saved_document.disconnect('saved', self.on_document_saved)
                except KeyError:
                    pass
            self._saved_document = document
            if document is not None:
                document.connect('saved', self.on_document_saved)

        self.document = document

        repo = None
        if document is not None:
            repo = GitRepository.get_for_path(document.get_filename())
        if repo is not self.repo:
            if self.repo is not None:
                try:
                    self.repo.disconnect('state_changed', self.on_repo_state_changed)
                except KeyError:
                    pass
            self.repo = repo
            if self.repo is not None:
                self.repo.connect('state_changed', self.on_repo_state_changed)
                # 换仓库：恢复该仓库未提交的 message（会话内记忆）。
                self._load_message()

        self.update_view()

    def on_document_saved(self, document):
        if self.repo is not None:
            self.repo.refresh()

    def on_repo_state_changed(self, repo):
        if repo is self.repo:
            self.update_view()

    def _panel_enabled(self):
        return (self.settings.get_value('preferences', 'git_integration')
                and self.settings.get_value('preferences', 'git_sidebar_panel')
                and git_available())

    def _init_directory(self):
        '''返回可用于快捷初始化的目录；没有可初始化的目录时返回 None。

        只在文档已保存（有绝对路径）时提供：未命名文档没有所属目录，
        git init 出来的仓库也无处安放。'''
        if self.document is None:
            return None
        filename = self.document.get_filename()
        if filename is None:
            return None
        directory = os.path.dirname(os.path.realpath(filename))
        if not directory or not os.path.isdir(directory):
            return None
        return directory

    def update_view(self):
        '''按当前条件把页面切到对应状态。

        判据顺序即状态优先级：git 可执行 → 集成偏好开 → 有已保存文档 →
        有仓库；前三层不满足时分别给 Unavailable / No Document / Init
        空状态，而不是留白。'''
        if not git_available():
            self.view.show_unavailable(_('The git executable was not found in PATH.'))
            return
        if not self.settings.get_value('preferences', 'git_integration'):
            self.view.show_unavailable(_('Git integration is disabled in Preferences.'))
            return
        if self.document is None or self.document.get_filename() is None:
            self.view.show_no_document()
            return
        if self.repo is None:
            self.view.show_init()
            return
        state = self.repo.state
        # 刚连上仓库时状态还在后台线程里跑：留白等 state_changed，别把
        # 「尚未取到状态」误当成「没有仓库」而闪回初始化页。
        if state is None:
            return
        self.view.show_repository()

        # 分支 + ahead/behind
        self.view.label_branch.set_text(state['branch'] or _('(no commits yet)'))
        marks = []
        if state['ahead']:
            marks.append('↑ %d' % state['ahead'])
        if state['behind']:
            marks.append('↓ %d' % state['behind'])
        if marks:
            self.view.label_ahead_behind.set_text(' '.join(marks))
            self.view.label_ahead_behind.set_visible(True)
        elif state['upstream']:
            self.view.label_ahead_behind.set_text(_('synced'))
            self.view.label_ahead_behind.set_visible(True)
        else:
            self.view.label_ahead_behind.set_visible(False)

        # 推送只在「配了远程 + 有东西可推」时可用：本地领先远程，或还没建立
        # upstream（首次推送会自动 --set-upstream）。纯本地项目或已有 upstream
        # 且不领先 → 灰掉并说明原因，避免点了只得到 "Everything up-to-date"。
        has_remote = bool(state['remotes'])
        has_upstream = state['upstream'] is not None
        self.view.button_push.set_sensitive(
            has_remote and bool(state['branch'])
            and (bool(state['ahead']) or not has_upstream))
        self.view.button_push.set_tooltip_text(
            _('Push commits to the remote') if has_remote
            else _('No remote repository is set up for this project.'))

        # 拉取需要 upstream：没有它 git pull 只会吐 "no tracking information"。
        # 先把按钮灰掉，并指向「先推送一次」这条唯一出路。
        self.view.button_pull.set_sensitive(has_upstream)
        self.view.button_pull.set_tooltip_text(
            _('Update this branch from the remote (fast-forward only)')
            if has_upstream
            else _('Push this branch once to set its upstream, then you can pull.'))

        # 改动文件行（勾选状态跨刷新保留，新文件按 untracked 默认值）。
        self._populate_changes(state['changed_files'])
        self._update_commit_sensitivity()

        # 线性历史（Commits 档，切换档位时内容已就绪）。
        self.view.populate_history(state['history'])

    # —— Changes 档：文件行与提交 ————————————————————————

    def _populate_changes(self, changed_files):
        self.view.clear_file_rows()
        checked_map = self._checked.setdefault(self.repo.root, dict())
        for entry in changed_files:
            path = entry['path']
            default = not entry['untracked']
            row, check, open_button, discard = self.view.build_file_row(entry)
            check.set_active(checked_map.get(path, default))
            check.connect('toggled', self.on_file_check_toggled, entry)
            open_button.connect('clicked', self.on_open_file_clicked, entry)
            # 已删除（D）的文件磁盘上不存在：查看文件按钮灰掉。
            open_button.set_sensitive(
                os.path.exists(os.path.join(self.repo.root, path)))
            discard.connect('clicked', self.on_discard_clicked, entry)
            row.connect('activated', self.on_file_row_activated, entry)
            self.view.append_file_row(row)
        if not changed_files:
            self.view.set_clean()

    def on_file_check_toggled(self, check, entry):
        checked_map = self._checked.setdefault(self.repo.root, dict())
        checked_map[entry['path']] = check.get_active()
        self._update_commit_sensitivity()

    def _checked_paths(self):
        checked_map = self._checked.get(self.repo.root, dict())
        state = self.repo.state
        if state is None:
            return []
        result = []
        for entry in state['changed_files']:
            default = not entry['untracked']
            if checked_map.get(entry['path'], default):
                result.append(entry['path'])
        return result

    def _current_message(self):
        start = self.view.message_text.get_buffer().get_start_iter()
        end = self.view.message_text.get_buffer().get_end_iter()
        return self.view.message_text.get_buffer().get_text(start, end, False).strip()

    def _update_commit_sensitivity(self):
        self.view.button_commit.set_sensitive(
            bool(self._current_message()) and bool(self._checked_paths()))

    def on_message_changed(self, buffer):
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        text = buffer.get_text(start, end, False)
        self.view.set_message_placeholder_visible(not text)
        if self.repo is not None:
            self._messages[self.repo.root] = text
        self._update_commit_sensitivity()

    def _load_message(self):
        '''换仓库后恢复该仓库未提交的 message（无记忆则清空）。'''
        text = self._messages.get(self.repo.root, '') if self.repo is not None else ''
        buffer = self.view.message_text.get_buffer()
        buffer.set_text(text or '')
        self.view.set_message_placeholder_visible(not text)

    def on_message_key_pressed(self, controller, keyval, keycode, state):
        # Ctrl+Return / Ctrl+KP_Enter 提交；其余键交给输入框（Enter 换行，
        # 与 VSCode 的多行 message 输入一致）。
        if (keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
                and state & Gdk.ModifierType.CONTROL_MASK):
            self.on_commit_clicked(None)
            return True
        return False

    def on_commit_clicked(self, button):
        if not self._panel_enabled() or self.repo is None or self.repo.state is None:
            return
        message = self._current_message()
        files = self._checked_paths()
        if not message or not files:
            return
        if not self._is_trusted():
            self._request_trust(self.do_commit)
            return
        self.do_commit()

    def do_commit(self):
        message = self._current_message()
        files = self._checked_paths()
        if not message or not files:
            return
        self.show_error(None)
        # 先保存仓库内所有有未保存修改的文档，保证提交内容与编辑器一致。
        self._save_open_documents()
        self.view.button_commit.set_sensitive(False)
        self.repo.commit(files, message, self.on_commit_done)

    def on_commit_done(self, error):
        if error:
            self.show_error(error)
            self._update_commit_sensitivity()
            return
        # 成功：清空 message（含记忆）并刷新；Changes 清空、历史更新即确认。
        self._messages[self.repo.root] = ''
        self.view.message_text.get_buffer().set_text('')
        self.repo.refresh()

    def _save_open_documents(self):
        root_prefix = self.repo.root + os.sep
        for document in self.workspace.get_all_documents():
            filename = document.get_filename()
            if filename is None:
                continue
            if not filename.startswith(root_prefix):
                continue
            if document.source_buffer.get_modified():
                document.save_to_disk(show_toast=False)

    # —— 文件行：diff / 查看文件 / Discard ——————————————————

    def on_file_row_activated(self, row, entry):
        '''点击文件行：LaTeX 族文件优先走 PhiTeX PDF diff（工作区 vs HEAD
        的编译级 diff），条件不满足或失败兜底文本统一 diff。均只读，不需
        信任门。路径绑定进回调，弹窗标题/副标题用它。'''
        if entry['path'].lower().endswith(LATEX_DIFF_EXTENSIONS):
            if self._open_pdf_diff(entry):
                return
        self._show_text_diff(entry)

    def _show_text_diff(self, entry):
        '''并排文本 diff 窗口（非 LaTeX 文件，或 PDF diff 不可用时的兜底）。'''
        if self.repo is None:
            return
        self.repo.file_versions(entry['path'],
                                lambda error, old_text, new_text, path=entry['path']:
                                self.on_diff_done(error, old_text, new_text, path))

    def on_diff_done(self, error, old_text, new_text, path):
        if error:
            self.show_error(error)
            return
        window = DiffWindow(path, old_text, new_text,
                            ServiceLocator.get_main_window())
        window.present()

    def _pdf_diff_session(self, document):
        '''PDF diff 的前置条件检查，通过返回 PhitexSession，否则 None。

        条件：活动文档有 build_system + preview（可编译可交付）、PhiTeX
        ≥1.0.3（supports_compare）、watch session 存活、AI 审阅无在途
        compare（JOB-diff session 并发会互相污染）。'''
        build_system = getattr(document, 'build_system', None)
        if build_system is None or getattr(document, 'preview', None) is None:
            return None
        if not PhitexSession.supports_compare():
            return None
        ai_diff = getattr(build_system, 'ai_diff', None)
        if ai_diff is None or ai_diff.is_query_running():
            return None
        builder = build_system.builders.get('build_latex')
        session = getattr(builder, 'phitex_session', None) if builder else None
        if session is None or session.pid is None:
            return None
        return session

    def _open_pdf_diff(self, entry):
        '''尝试展示「工作区 vs HEAD」的编译级 PDF diff。

        返回 True 表示已接管（发起查询；结果失败时本方法自行兜底文本
        diff）；False 表示条件不满足，调用方走文本 diff。_pdf_diff_running
        只在主线程读写（发起/回调都经 GLib.idle_add），无需加锁。'''
        document = self.document
        if document is None:
            return False
        session = self._pdf_diff_session(document)
        if session is None:
            return False
        if self._pdf_diff_running:
            # 已有在途的 PDF diff 查询：不重复发起，也不兜底（等结果）。
            return True
        # 未保存的缓冲区不在磁盘态里，diff 会漏；先落盘（与提交同一纪律）。
        self._save_open_documents()
        # 解除 AI 审阅武装：防止后续自动构建触发的 AI compare 覆盖本次
        # git diff 结果（两者共用 preview 的 diff 槽位）。disarm 不打断
        # JOB-diff——紧接着的 git compare 正要用它。
        ai_diff = getattr(document.build_system, 'ai_diff', None)
        if ai_diff is not None:
            ai_diff.disarm()
        self._pdf_diff_running = True
        threading.Thread(target=self._pdf_diff_query,
                         args=(session, document, entry),
                         daemon=True).start()
        return True

    def _pdf_diff_query(self, session, document, entry):
        '''后台线程：`session compare HEAD --json`（子进程，秒级到分钟级）。'''
        try:
            returncode, stdout = session.compare('HEAD', timeout=PDF_DIFF_TIMEOUT_S)
        except Exception:
            returncode, stdout = 1, ''
        GLib.idle_add(self._on_pdf_diff_done, document, entry, returncode, stdout)
        return False

    def _on_pdf_diff_done(self, document, entry, returncode, stdout):
        self._pdf_diff_running = False
        payload = phitex_compare.parse_compare_json(stdout)
        failed = (payload is None
                  or phitex_compare.compare_failed(payload)
                  or payload.get('pdf') is None)
        changes = (payload.get('changes') or []) if payload is not None else []
        preview = getattr(document, 'preview', None)
        if (failed
                or not changes
                or preview is None
                or getattr(document, 'build_system', None) is None):
            # PDF diff 没排出来 / 排出来无可见差异：报一条说明并兜底文本 diff
            # （文本 diff 一定能呈现该文件的改动，哪怕是纯注释级的）。
            self.show_error(_('Could not render a PDF diff — showing the text diff '
                              'instead.'))
            self._show_text_diff(entry)
            return
        result = {
            'rectangles': phitex_compare.changes_to_rectangles(changes),
            'changes': changes,
            'pdf': payload.get('pdf'),
            'pages': payload.get('pages'),
            'change_count': len(changes),
            'errors': payload.get('errors') or [],
            'failed': False,
            'source': 'git',
        }
        preview.set_diff_result(result)
        presenter = getattr(preview, 'presenter', None)
        if presenter is not None:
            presenter.show_diff_view()

    def on_open_file_clicked(self, button, entry):
        '''行内「查看文件」：一律在 NeoSetzer 编辑器里打开（已打开则激活）。
        非 LaTeX/BibTeX 文件（.log 等）走 create_document_from_filename 的
        纯文本(other)兜底——基础文本编辑器已有，没必要跳去外部应用。'''
        if self.repo is None:
            return
        absolute = os.path.join(self.repo.root, entry['path'])
        if not os.path.exists(absolute):
            return
        document = self.workspace.get_document_by_filename(absolute)
        if document is not None:
            self.workspace.set_active_document(document)
            return
        document = self.workspace.create_document_from_filename(absolute)
        if document is not None:
            self.workspace.set_active_document(document)

    def on_discard_clicked(self, button, entry):
        '''行内 Discard：先确认（写操作 + 对 untracked 是直接删文件），
        再过信任门（hooks 风险），最后执行并同步打开中的文档缓冲区。'''
        if not self._panel_enabled() or self.repo is None or self.repo.state is None:
            return
        full_path = os.path.join(self.repo.root, entry['path'])
        dialog = Adw.AlertDialog()
        dialog.set_heading(_('Discard changes in %s?') % entry['path'])
        if entry['untracked']:
            dialog.set_body(_('%s\n\nThis untracked file will be deleted permanently.')
                            % full_path)
        else:
            dialog.set_body(_('%s\n\nAll uncommitted changes in this file will be lost.')
                            % full_path)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('discard', _('Discard'))
        dialog.set_response_appearance('discard', Adw.ResponseAppearance.DESTRUCTIVE)
        # Enter/Esc 默认走 Cancel：丢弃不可撤销，默认响应须安全侧。
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self.on_discard_confirmed, entry['path'])
        dialog.present(ServiceLocator.get_main_window())

    def on_discard_confirmed(self, dialog, response, path):
        if response != 'discard':
            return
        if not self._is_trusted():
            self._request_trust(self.do_discard, path)
            return
        self.do_discard(path)

    def do_discard(self, path):
        self.show_error(None)
        self.repo.discard(path,
                          lambda error, p=path: self.on_discard_done(error, p))

    def on_discard_done(self, error, path):
        if error:
            self.show_error(error)
            return
        self.repo.refresh()
        # 磁盘已被 git 还原：打开中的文档缓冲区同步重读。
        self._reload_document_for_path(path)
        self._toast(_('Changes discarded'))

    def _reload_document_for_path(self, path):
        '''discard 后同步打开中的文档：磁盘已被 git 还原，编辑器缓冲区必须
        跟着重读，否则显示的是已丢弃的内容且不标记修改，保存即复活。'''
        root_prefix = self.repo.root + os.sep
        target = os.path.realpath(os.path.join(self.repo.root, path))
        for document in self.workspace.get_all_documents():
            filename = document.get_filename()
            if filename is None:
                continue
            if not filename.startswith(root_prefix):
                continue
            if os.path.realpath(filename) == target:
                document.reload_from_disk()

    # —— 快捷初始化（文档所在目录还不是仓库）————————————————

    def on_init_clicked(self, button):
        if not self._panel_enabled() or self.repo is not None:
            return
        directory = self._init_directory()
        if directory is None:
            return
        # 首次写操作前的显式确认：git init 会在该目录写下 .git，选错目录
        # （比如家目录）代价很高，所以把完整路径摆出来让用户看一眼。
        dialog = Adw.AlertDialog()
        dialog.set_heading(_('Initialize Git Repository?'))
        dialog.set_body(_('A new repository will be created in:\n\n%s') % directory)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('init', _('Initialize'))
        dialog.set_response_appearance('init', Adw.ResponseAppearance.SUGGESTED)
        # Enter/Esc 默认走 Cancel：git init 是有后果的写操作，默认响应须安全侧。
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self.on_init_confirmed, directory)
        dialog.present(ServiceLocator.get_main_window())

    def on_init_confirmed(self, dialog, response, directory):
        if response != 'init':
            return
        self.view.button_init.set_sensitive(False)
        GitRepository.init_repository(directory, self.on_init_done)

    def on_init_done(self, error, repo):
        self.view.button_init.set_sensitive(True)
        if error:
            self._toast(error)
            return
        # 初始化成功不弹 toast：面板随即切到仓库视图，状态变化本身即确认。
        if repo is not None:
            # 刚建好的仓库由本应用创建，不可能有预置 hooks，直接记为已信任，
            # 免得紧接着的第一次提交又弹一次信任确认。
            self._trust_repository(repo.root)
            # 结构变化（从「无仓库」到「有仓库」）：重走 set_document 完成
            # 信号连接与视图刷新，避免在这里重复实现一遍绑定逻辑。
            self.repo = None
            self.set_document()

    def _toast(self, message):
        '''非模态结果反馈统一走主窗口 toast（ui-guidelines）。'''
        if not message:
            return
        try:
            main_window = ServiceLocator.get_main_window()
            if main_window is not None and hasattr(main_window, 'toast_overlay'):
                main_window.toast_overlay.add_toast(Adw.Toast.new(message))
        except Exception:
            pass  # toast 失败不影响主流程

    # —— 写操作（pull / push），按仓库显式信任 ———————————————

    def _is_trusted(self):
        return self.repo.root in self.settings.get_value('preferences', 'git_trusted_repos')

    def _request_trust(self, callback, *args):
        '''首次写操作前的仓库信任确认。git hooks 可执行任意命令，
        打开外来项目时这是真实风险，必须显式确认一次。'''
        dialog = Adw.AlertDialog()
        dialog.set_heading(_('Trust This Repository?'))
        dialog.set_body(_('Pull, commit and push run git commands in this repository, '
                          'which may execute its hooks:\n\n%s') % self.repo.root)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('trust', _('Trust and Continue'))
        dialog.set_response_appearance('trust', Adw.ResponseAppearance.SUGGESTED)
        # Enter/Esc 默认走 Cancel：信任仓库会放行 hooks 执行，默认须安全侧。
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self.on_trust_response, callback, args)
        dialog.present(ServiceLocator.get_main_window())

    def on_trust_response(self, dialog, response, callback, args):
        if response != 'trust':
            return
        self._trust_repository(self.repo.root)
        callback(*args)

    def _trust_repository(self, root):
        '''把仓库根路径写入信任列表（幂等）。'''
        if root is None:
            return
        trusted = list(self.settings.get_value('preferences', 'git_trusted_repos'))
        if root not in trusted:
            trusted.append(root)
            self.settings.set_value('preferences', 'git_trusted_repos', trusted)

    def on_pull_clicked(self, button):
        if not self._panel_enabled() or self.repo is None or self.repo.state is None:
            return
        if not self._is_trusted():
            self._request_trust(self.do_pull)
            return
        self.do_pull()

    def do_pull(self):
        state = self.repo.state
        # 脏工作区保护：--ff-only 会失败，先给出明确提示，不留困惑状态。
        if state['dirty']:
            self.show_error(_('Working tree has uncommitted changes. Commit them '
                              'first, or use a terminal to stash.'))
            return
        self.show_error(None)
        self.view.button_pull.set_sensitive(False)
        self.repo.pull(self.on_pull_done)

    def on_pull_done(self, error):
        self.view.button_pull.set_sensitive(True)
        if error:
            self.show_error(error)
        else:
            self.show_error(None)
            self.repo.refresh()

    def on_push_clicked(self, button):
        if not self._panel_enabled() or self.repo is None or self.repo.state is None:
            return
        if not self._is_trusted():
            self._request_trust(self.do_push)
            return
        self.do_push()

    def do_push(self):
        state = self.repo.state
        if state['branch'] is None:
            self.show_error(_('Nothing to push: this branch has no commits yet.'))
            return
        self.show_error(None)
        self.view.button_push.set_sensitive(False)
        self.repo.push(self.on_push_done)

    def on_push_done(self, error):
        if error:
            # 失败时 update_view 里重算的敏感度可能仍为 True，这里只需先恢复。
            self.show_error(error)
            self.view.button_push.set_sensitive(True)
        else:
            self.show_error(None)
            self.repo.refresh()

    def show_error(self, text):
        self.view.show_error(text)
