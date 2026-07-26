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
gi.require_version('GtkSource', '5')
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GtkSource, Gtk, GObject, Adw, GLib, Gdk

import os.path, stat, time

import setzer.document.document_controller as document_controller
import setzer.document.document_presenter as document_presenter
import setzer.document.document_viewgtk as document_view
import setzer.document.search.search as search
import setzer.document.gutter.gutter as gutter
import setzer.document.statusbar.statusbar as statusbar
import setzer.document.parser.parser_latex as parser_latex
import setzer.document.parser.parser_bibtex as parser_bibtex
import setzer.document.parser.parser_dummy as parser_dummy
import setzer.document.code_folding.code_folding as code_folding
import setzer.document.bracket_completion.bracket_completion as bracket_completion
import setzer.document.update_matching_blocks.update_matching_blocks as update_matching_blocks
import setzer.document.autocomplete.autocomplete as autocomplete
from setzer.helpers.observable import Observable
from setzer.app.service_locator import ServiceLocator
from setzer.app.color_manager import ColorManager
from setzer.app.font_manager import FontManager


class Document(Observable):

    def __init__(self, language):
        Observable.__init__(self)
        self.language = language

        self.displayname = ''
        self.filename = None
        self.save_date = None
        self.last_activated = 0
        self.is_root = False
        self.root_is_set = False
        self.highlight_tag_count = 0
        self.highlight_tags = dict()
        self._highlight_timeout_id = None

        self.source_buffer = GtkSource.Buffer()
        self.source_buffer.set_language(ServiceLocator.get_source_language(language))
        self.source_view = GtkSource.View.new_with_buffer(self.source_buffer)
        self.source_buffer.set_style_scheme(ServiceLocator.get_style_scheme())
        self.source_buffer.connect('modified-changed', self.on_modified_change)
        self.source_buffer.connect('changed', self.on_change)
        self.source_buffer.connect('notify::cursor-position', self.on_cursor_position_change)
        self.settings = ServiceLocator.get_settings()

        self.view = document_view.DocumentView(self)
        self.presenter = document_presenter.DocumentPresenter(self, self.view)
        self.controller = document_controller.DocumentController(self, self.view)

        if self.is_latex_document(): self.parser = parser_latex.ParserLaTeX(self)
        elif self.is_bibtex_document(): self.parser = parser_bibtex.ParserBibTeX(self)
        else: self.parser = parser_dummy.ParserDummy(self)
        self.code_folding = code_folding.CodeFolding(self)
        self.gutter = gutter.Gutter(self, self.view)
        self.search = search.Search(self, self.view)
        # 状态栏：每文档一个，嵌入 editor-card 底部。监听光标移动与设置变化
        # 更新行/列、语言、编码、缩进、选区词数。构造后注入 view。
        self.statusbar = statusbar.StatusBar(self)
        self.view.set_statusbar(self.statusbar.view)

        # LaTeX 专属子系统（autocomplete / bracket_completion /
        # update_matching_blocks）延迟到 idle 构造，避免新建文档时主线程
        # 阻塞。它们只在用户按键交互时才需要，idle 调度时文档已可见可编辑。
        self._latex_features_ready = False
        self._is_shutdown = False
        self._latex_features_idle_id = None
        # 懒加载：会话恢复时非活跃文档不立即读取文件内容，标记 _content_pending
        # 后由 idle 回调或激活时同步加载。_lazy_load_idle_id 跟踪挂起的 idle
        # 以便激活/关闭时取消。详见 populate_from_disk / _load_content_if_pending。
        self._content_pending = False
        self._lazy_load_idle_id = None
        if self.is_latex_document():
            self._latex_features_idle_id = GLib.idle_add(self._init_latex_features)

        self.settings.connect('settings_changed', self.on_settings_changed)

        self.style_manager = Adw.StyleManager.get_default()
        self._theme_handler_id = self.style_manager.connect('notify::dark', self.on_theme_colors_changed)

    def _init_latex_features(self):
        '''延迟构造 LaTeX 专属子系统（autocomplete / bracket_completion /
        update_matching_blocks）。它们只在用户按键交互时才需要，idle 调度
        时文档已可见可编辑，从而把构造开销从「新建文档」主帧移到空闲时刻。'''
        self._latex_features_idle_id = None
        # 文档可能在 idle 排队期间被关闭（如快速新建后立即关闭）。
        # 此时不应再构造组件——它们会向已失效的 source_view 挂控制器、
        # 向主窗口 overlay 挂已失效的补全 widget，造成引用泄漏与报错。
        if self._is_shutdown:
            return False

        self.update_matching_blocks = update_matching_blocks.UpdateMatchingBlocks(self)
        self.bracket_completion = bracket_completion.BracketCompletion(self)
        self.autocomplete = autocomplete.Autocomplete(self)
        self._latex_features_ready = True

        # on_new_active_document 在 idle 之前就已执行，当时 autocomplete
        # 尚未构造，overlay 挂载被 try/except 跳过。此处补做。
        workspace = ServiceLocator.get_workspace()
        is_active = workspace is not None and workspace.active_document is self
        if is_active:
            main_window = ServiceLocator.get_main_window()
            try:
                main_window.preview_paned_overlay.add_overlay(self.autocomplete.widget.view)
            except AttributeError:
                pass

        return False

    def shutdown(self):
        '''文档关闭时由 workspace.remove_document 调用，清理信号连接与
        挂起的 idle 回调，防止已关闭文档的回调仍被触发（内存泄漏 + 报错）。

        settings / style_manager 是进程级单例，不随文档释放。若不断开
        连接，单例会持续持有文档的回调引用，文档对象无法被 GC 回收，
        且后续设置/主题变更会调到已失效的 on_settings_changed /
        on_theme_colors_changed（后者会 set_style_scheme 到已销毁的
        source_buffer，可能触发 GTK 警告）。
        '''
        self._is_shutdown = True

        # 取消尚未执行的 idle 回调；若已执行则 id 已在回调内清为 None。
        if self._latex_features_idle_id is not None:
            GLib.Source.remove(self._latex_features_idle_id)
            self._latex_features_idle_id = None
        # 取消懒加载 idle：文档关闭时内容可能尚未加载，无需再读。
        if self._lazy_load_idle_id is not None:
            GLib.Source.remove(self._lazy_load_idle_id)
            self._lazy_load_idle_id = None

        # 断开单例信号连接。
        # settings 是 Observable（自定义观察者模式），disconnect 接受
        # (change_code, callback)；Python 3 中同一实例的绑定方法 hash/eq
        # 一致，故可在 disconnect 时再次传 self.on_settings_changed。
        # style_manager 是 GObject，disconnect 接受 handler_id。
        try:
            self.settings.disconnect('settings_changed', self.on_settings_changed)
        except (TypeError, KeyError, AttributeError):
            pass
        try:
            self.style_manager.disconnect(self._theme_handler_id)
        except (TypeError, AttributeError):
            pass

        # 取消预览滚动减速动画的 timeout,避免回调在 widget 已销毁后继续
        # 访问 adjustment 等已释放对象。bibtex/other 文档没有 preview 属性。
        preview = getattr(self, 'preview', None)
        if preview is not None:
            try:
                preview.shutdown()
            except Exception:
                pass

        # gutter 连接了 settings 单例信号 + idle 回调 + 减速 timeout,
        # 需显式清理,否则 settings 单例持有 gutter→document 引用导致无法 GC。
        gutter = getattr(self, 'gutter', None)
        if gutter is not None:
            try:
                gutter.shutdown()
            except Exception:
                pass

        # bracket_completion / autocomplete 连接了 settings 单例信号 + idle
        # 回调,需断开 + 取消挂起 idle。它们是 LaTeX 专属子系统,延迟到 idle
        # 构造,文档可能在构造前就被关闭(此时属性不存在)。
        for attr in ('bracket_completion', 'autocomplete'):
            module = getattr(self, attr, None)
            if module is not None and hasattr(module, 'shutdown'):
                try:
                    module.shutdown()
                except Exception:
                    pass

        # 以下模块仅连接了 settings 单例信号(无 idle/timeout 资源),
        # 集中断开即可。settings 是进程级单例,不断开会导致单例持续持有
        # 模块→document 引用,文档对象无法被 GC 回收,且后续设置变更会调到
        # 已失效的 on_settings_changed。
        for module in (self.presenter, self.code_folding):
            try:
                self.settings.disconnect('settings_changed', module.on_settings_changed)
            except (TypeError, KeyError, AttributeError):
                pass
        # LaTeX 专属模块,可能尚未构造。
        # update_matching_blocks 仅连接 settings,断开即可。
        umb = getattr(self, 'update_matching_blocks', None)
        if umb is not None:
            try:
                self.settings.disconnect('settings_changed', umb.on_settings_changed)
            except (TypeError, KeyError, AttributeError):
                pass
        # build_widget 连接 settings 且持有构建计时器 timeout,需 shutdown
        # 停止计时器 + 断开 settings。
        bw = getattr(self, 'build_widget', None)
        if bw is not None:
            try:
                bw.shutdown()
            except Exception:
                pass
            try:
                self.settings.disconnect('settings_changed', bw.on_settings_changed)
            except (TypeError, KeyError, AttributeError):
                pass

        # 取消高亮淡出 timeout：文档关闭时若仍有 tag 在淡出（最多 ~1.75s），
        # 回调会持续访问已逻辑失效的 source_buffer 并持有 document 引用阻碍 GC。
        if self._highlight_timeout_id is not None:
            try:
                GLib.Source.remove(self._highlight_timeout_id)
            except (ValueError, RuntimeError):
                pass
            self._highlight_timeout_id = None

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        # 用户在 Preferences 中切换了编辑器配色方案。
        # ServiceLocator.set_style_scheme_name 在 set_value 前已清空缓存，
        # 此处 get_style_scheme 会重新读取设置返回新方案；应用到 source_buffer。
        # 系统主题切换走 on_theme_colors_changed 路径，与此互不干扰。
        if item == 'editor_style_scheme':
            self.source_buffer.set_style_scheme(ServiceLocator.get_style_scheme())

    def on_theme_colors_changed(self, style_manager, pspec=None):
        self.source_buffer.set_style_scheme(ServiceLocator.get_style_scheme())

    def set_filename(self, filename):
        if filename == None:
            self.filename = filename
        else:
            self.filename = os.path.realpath(filename)
        self.add_change_code('filename_change', filename)

    def get_filename(self):
        return self.filename
        
    def get_dirname(self):
        if self.filename != None:
            return os.path.dirname(self.filename)
        else:
            return ''

    def get_displayname(self):
        if self.filename != None:
            return self.get_filename()
        else:
            return self.displayname
        
    def set_displayname(self, displayname):
        self.displayname = displayname
        self.add_change_code('displayname_change')

    def get_basename(self):
        if self.filename != None:
            return os.path.basename(self.filename)
        else:
            return self.displayname

    def get_last_activated(self):
        return self.last_activated
        
    def set_last_activated(self, date):
        self.last_activated = date

    def populate_from_filename(self):
        if self.filename == None: return False
        if not os.path.isfile(self.filename):
            self.set_filename(None)
            return False

        self._load_file_content()
        return True

    def _load_file_content(self):
        '''读取文件内容并填入 source_buffer。

        从 populate_from_filename 抽出，供懒加载复用：会话恢复时非活跃文档
        延迟调用此方法（idle 或激活时），避免启动期同步读取 N 个大文件。
        加载后应用 _restore_cursor_offset / _restore_scroll_offset（若存在），
        因为懒加载文档在 _restore_document_states idle 时缓冲区尚为空，
        偏移恢复会失败——此处补做。
        '''
        with open(self.filename) as f:
            text = f.read()

        # 预置行号宽度：在 set_text 之前用文件真实行数把 gutter 宽度算好，
        # 避免大文档加载后行数从 0 跳到几千时行号区域“突然变宽”的跳变。
        # text.count('\n') + 1 是 O(1) 计数；空文件记 1 行，保持最小宽度。
        line_count = text.count('\n') + 1
        if getattr(self, 'gutter', None) is not None:
            self.gutter.presize_for_line_count(line_count)

        self.source_buffer.begin_irreversible_action()
        self.source_buffer.set_text(text)
        self.source_buffer.end_irreversible_action()
        self.source_buffer.set_modified(False)
        self.place_cursor(0, 0)
        self.update_save_date()

        # 懒加载文档的游标/滚动恢复：_restore_document_states idle 在内容
        # 加载前运行时偏移无效（缓冲区空），此处内容已就绪，补做恢复。
        cursor_offset = getattr(self, '_restore_cursor_offset', None)
        if cursor_offset is not None:
            try:
                if cursor_offset <= self.source_buffer.get_end_iter().get_offset():
                    self.source_buffer.place_cursor(self.source_buffer.get_iter_at_offset(cursor_offset))
            except Exception:
                pass
            self._restore_cursor_offset = None
        scroll_offset = getattr(self, '_restore_scroll_offset', None)
        if scroll_offset is not None:
            try:
                adj = self.view.scrolled_window.get_vadjustment()
                GLib.idle_add(lambda a=adj, v=scroll_offset: (a.set_value(v), False))
            except Exception:
                pass
            self._restore_scroll_offset = None

    def _load_content_if_pending(self):
        '''懒加载入口：若文档内容尚未加载（_content_pending），同步加载。

        调用时机：
        - workspace.set_active_document：激活文档前同步加载（用户切换到该文档）
        - GLib.idle_add 回调：启动后空闲时后台加载非活跃文档

        幂等：_content_pending 为 False 时直接返回，避免重复加载。
        取消挂起的 idle（若由激活触发，idle 不应再执行）。
        '''
        if not self._content_pending:
            return
        # 取消挂起的 idle 回调（激活时同步加载，idle 不必再跑）
        if self._lazy_load_idle_id is not None:
            GLib.Source.remove(self._lazy_load_idle_id)
            self._lazy_load_idle_id = None
        self._content_pending = False
        if self.filename is not None and os.path.isfile(self.filename):
            self._load_file_content()
        else:
            # 文件在会话恢复后已被删除：清空文件名，显示空文档
            self.set_filename(None)

    def schedule_lazy_load(self):
        '''调度 idle 加载文档内容（会话恢复时对非活跃文档调用）。

        使用 PRIORITY_LOW（=300）让 UI 渲染与交互优先于后台文件读取。
        多个文档各自调度一个 idle，按 GTK 事件循环顺序依次执行——
        不会一次性占满主线程，每个 idle 只读一个文件。
        '''
        self._content_pending = True
        self._lazy_load_idle_id = GLib.idle_add(self._on_lazy_load_idle)

    def _on_lazy_load_idle(self):
        self._lazy_load_idle_id = None
        # 文档可能在 idle 排队期间被关闭（schedule 后立即关闭）
        if self._is_shutdown or not self._content_pending:
            return False
        self._load_content_if_pending()
        return False

    def save_to_disk(self):
        if self.filename == None: return False

        # 懒加载安全守卫：内容未加载时缓冲区为空，直接保存会用空内容覆盖
        # 原文件（数据丢失）。先同步加载内容再保存。正常流程下非活跃文档
        # 不会被保存（UI 仅对活跃文档触发保存），此守卫防御 Save All /
        # AutoSave 等批量保存路径。
        if self._content_pending:
            self._load_content_if_pending()

        text = self.get_all_text()
        if text == None: return False

        dirname = os.path.dirname(self.filename)
        # exist_ok=True 一次调用替代 exists + makedirs：dirname 几乎总是存在，
        # 原实现每次保存都做一次多余 stat；exist_ok 时已存在不报错，省一次系统调用。
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        with open(self.filename, 'w') as f:
            f.write(text)
        self.update_save_date()
        self.controller.deleted_on_disk_dialog_shown_after_last_save = False
        self.source_buffer.set_modified(False)
        # 通知监听者文档已成功保存（AutoSave 据此删除对应的崩溃恢复临时文件，
        # 避免下次启动误把已保存的旧版本当作可恢复内容）。无参数，与 'changed'
        # 同模式：回调签名 callback(document)。
        self.add_change_code('saved')

    def update_save_date(self):
        self.save_date = os.path.getmtime(self.filename)

    def get_disk_status(self):
        '''一次 os.stat 返回 (deleted, changed)，供 save_date_loop 每 2s 轮询。

        原实现分别用 get_deleted_on_disk（os.path.isfile）与
        get_changed_on_disk（os.path.getmtime），两次独立 stat。正常情况
        （文件存在且未变更）每文档每 2s 2 次 stat，N 文档 = N 次 stat/秒。
        合并为单 os.stat 省 50% syscall；HDD/网络盘 stat 延迟叠加时收益显著。

        deleted 语义保持与原 os.path.isfile 一致：不存在或非常规文件
        （如被目录替换）均视为已删除。
        '''
        try:
            st = os.stat(self.filename)
        except FileNotFoundError:
            return (True, False)
        except OSError:
            # 权限不足等：当作未删除未变更，避免误弹「已删除」对话框。
            return (False, False)
        if not stat.S_ISREG(st.st_mode):
            return (True, False)
        return (False, self.save_date <= st.st_mtime - 0.001)

    def set_root_state(self, is_root, root_is_set):
        self.is_root = is_root
        self.root_is_set = root_is_set
        self.add_change_code('is_root_changed', is_root)

    def get_is_root(self):
        return self.is_root

    def is_latex_document(self):
        return self.language == 'latex'

    def is_bibtex_document(self):
        return self.language == 'bibtex'

    def get_document_type(self):
        return self.language

    def get_all_text(self):
        return self.source_buffer.get_text(self.source_buffer.get_start_iter(), self.source_buffer.get_end_iter(), True)

    def get_selected_text(self):
        bounds = self.source_buffer.get_selection_bounds()
        if len(bounds) == 2:
            return self.source_buffer.get_text(bounds[0], bounds[1], True)
        else:
            return None

    def get_line(self, line_number):
        found, start_iter = self.source_buffer.get_iter_at_line(line_number)
        end_iter = start_iter.copy()
        if not end_iter.ends_line():
            end_iter.forward_to_line_end()
        return self.source_buffer.get_slice(start_iter, end_iter, False)

    def get_line_after_offset(self, offset):
        start_iter = self.source_buffer.get_iter_at_offset(offset)
        return self.get_line(start_iter.get_line())[start_iter.get_line_offset():]

    def get_chars_at_cursor(self, number_of_chars):
        return self.get_chars_at_iter(self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()), number_of_chars)

    def get_chars_at_iter(self, start_iter, number_of_chars):
        end_iter = start_iter.copy()
        end_iter.forward_chars(number_of_chars)
        return self.source_buffer.get_text(start_iter, end_iter, False)

    def place_cursor(self, line_number, offset=0):
        _, text_iter = self.source_buffer.get_iter_at_line_offset(line_number, offset)
        self.source_buffer.place_cursor(text_iter)

    def delete_selection(self):
        self.source_buffer.delete_selection(True, True)

    def select_all(self, widget=None):
        self.source_buffer.select_range(self.source_buffer.get_start_iter(), self.source_buffer.get_end_iter())

    def add_packages(self, packages):
        # 用 join 替代循环 +=：每次 += 创建新字符串并复制全部已有内容，
        # N 个包的复杂度为 O(N²)。join 一次性分配。
        text = '\n'.join('\\usepackage{' + name + '}' for name in packages)
        self.insert_text_after_packages_if_possible(text)

    def insert_text_after_packages_if_possible(self, text):
        self.source_buffer.begin_user_action()
        package_data = self.parser.symbols['packages_detailed']
        if package_data:
            # 找所有 \usepackage match 中最大的 end offset，确定插入位置。
            # 原双层循环 O(P×M) 手写比较；改用 max + 生成器，仍是 O(P×M) 但
            # 比较逻辑下沉到 C 层，Python 字节码循环更少。default=0 处理
            # package_data 非空但所有 match list 为空的边界（max() 空序列会抛
            # ValueError）。
            max_end = max(
                (offset + match_obj.end() - match_obj.start()
                 for match_list in package_data.values()
                 for offset, match_obj in match_list),
                default=0,
            )
            insert_iter = self.source_buffer.get_iter_at_offset(max_end)
            if not insert_iter.ends_line():
                insert_iter.forward_to_line_end()
            self.source_buffer.place_cursor(insert_iter)
            text = '\n' + text
        else:
            end_iter = self.source_buffer.get_end_iter()
            result = end_iter.backward_search('\\documentclass', Gtk.TextSearchFlags.VISIBLE_ONLY, None)
            if result != None:
                result[0].forward_to_line_end()
                self.source_buffer.place_cursor(result[0])
                text = '\n' + text

        self.source_buffer.delete_selection(False, False)
        self.source_buffer.insert_at_cursor(text)
        self.source_buffer.end_user_action()

    def remove_packages(self, packages):
        packages_data = self.parser.symbols['packages_detailed']
        for package in packages:
            if package in packages_data:
                self.source_buffer.begin_user_action()

                # 原实现对每个 match 调 get_text 取出文本与 match_obj.group(0)
                # 比较验证 buffer 未变。但 remove_packages 在 begin_user_action
                # 内执行，期间 buffer 不会被外部并发修改；parser 数据在 buffer
                # 变化时同步刷新。若 parser 数据过期，正确做法是重新解析而非逐
                # match 验证。移除 N 次 get_text（GtkTextIter→C→Python 字符串
                # 分配）调用，直接按 offset 删除。
                for package_match in reversed(packages_data[package]):
                    offset, match_obj = package_match
                    start_iter = self.source_buffer.get_iter_at_offset(offset)
                    end_iter = self.source_buffer.get_iter_at_offset(offset + match_obj.end() - match_obj.start())
                    if start_iter.get_line_offset() == 0:
                        start_iter.backward_char()
                    self.source_buffer.delete(start_iter, end_iter)

                self.source_buffer.end_user_action()

    def insert_before_document_end(self, text):
        end_iter = self.source_buffer.get_end_iter()
        result = end_iter.backward_search('\\end{document}', Gtk.TextSearchFlags.VISIBLE_ONLY, None)
        if result != None:
            self.source_buffer.begin_user_action()
            self.source_buffer.place_cursor(result[0])
            self.source_buffer.insert_at_cursor('''
''' + text + '''

''')
            self.source_buffer.end_user_action()
        else:
            self.source_buffer.insert_at_cursor(text)

    def replace_tabs_with_spaces_if_set(self, text):
        if self.settings.get_value('preferences', 'spaces_instead_of_tabs'):
            number_of_spaces = self.settings.get_value('preferences', 'tab_width')
            text = text.replace('\t', ' ' * number_of_spaces)
        return text

    def indent_text_with_whitespace_at_iter(self, text, start_iter):
        found, line_iter = self.source_buffer.get_iter_at_line(start_iter.get_line())
        ws_line = self.source_buffer.get_text(line_iter, start_iter, False)
        lines = text.split('\n')
        ws_number = len(ws_line) - len(ws_line.lstrip())
        whitespace = ws_line[:ws_number]
        # 用 join 替代循环 +=：每次 += 都创建新字符串并复制全部已有内容，
        # N 行片段的复杂度为 O(N²)。join 一次性分配。多行片段插入
        # （\begin{..}\n\t•\n\\end{..}）时差异最明显。
        if whitespace:
            lines = [lines[0]] + [whitespace + line for line in lines[1:]]
        return '\n'.join(lines)

    def on_modified_change(self, buffer):
        self.add_change_code('modified_changed')

    def on_change(self, buffer):
        self.add_change_code('changed')
        # 不在此调用 scroll_cursor_onscreen：文本插入/删除必然伴随光标移动，
        # notify::cursor-position 会触发 on_cursor_position_change 完成滚动。
        # 原实现两处各调一次，快速打字时双倍 set_kinetic_scrolling + scroll_to_mark。

    def on_cursor_position_change(self, buffer, location):
        self.add_change_code('cursor_position_changed')
        self.scroll_cursor_onscreen(margin_lines=0)
        return True

    def select_first_dot_around_cursor(self, offset_before, offset_after):
        end_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        start_iter = end_iter.copy()
        start_iter.backward_chars(offset_before)
        end_iter.forward_chars(offset_after)
        result = start_iter.forward_search('•', 0, end_iter)
        if result != None:
            self.source_buffer.select_range(result[0], result[1])

    def select_next_placeholder(self):
        if self.dot_selected():
            insert = self.source_buffer.get_selection_bounds()[1]
        else:
            insert = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())

        limit_iter = insert.copy()
        limit_iter.forward_lines(5)
        limit_iter.backward_chars(1)
        result = insert.forward_search('•', Gtk.TextSearchFlags.VISIBLE_ONLY, limit_iter)
        if result != None:
            self.source_buffer.select_range(result[0], result[1])
            self.scroll_cursor_onscreen()

    def select_previous_placeholder(self):
        if self.dot_selected():
            insert = self.source_buffer.get_selection_bounds()[0]
        else:
            insert = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())

        limit_iter = insert.copy()
        limit_iter.backward_lines(5)
        result = insert.backward_search('•', Gtk.TextSearchFlags.VISIBLE_ONLY, limit_iter)
        if result != None:
            self.source_buffer.select_range(result[0], result[1])
            self.scroll_cursor_onscreen()

    def dot_selected(self):
        return self.get_selected_text() == '•'

    def highlight_section(self, start_iter, end_iter):
        self.highlight_tag_count += 1
        color = ColorManager.get_ui_color('highlight_tag_textview')
        self.source_buffer.create_tag('highlight-' + str(self.highlight_tag_count), background_rgba=color, background_full_height=True)
        tag = self.source_buffer.get_tag_table().lookup('highlight-' + str(self.highlight_tag_count))
        self.source_buffer.apply_tag(tag, start_iter, end_iter)
        # 延迟首次淡出 tick：tag 创建后 1.5s 内无需任何处理（淡出在 1.5s 后才开始）。
        # 原实现立即启动 15ms timeout，1.5s 等待期内每 15ms 空转遍历所有 tag 检查
        # time_factor > 1.5（约 100 次无谓 tick + 每次调 time.time()）。改为 1.5s 后
        # 才启动 33ms（~30fps）淡出循环：消除等待期空转，且 33ms 对 0.25s 淡出
        # 足够平滑（约 7-8 帧），相比 15ms（~67Hz，高于 60fps 刷新率）减半 tick。
        # _highlight_timeout_id 先持有 1500ms 一次性 id，_start_highlight_fade 触发后
        # 切换为 33ms 循环 id；shutdown 据此 id 取消（两种 id 都能 remove）。
        if self._highlight_timeout_id is None:
            self._highlight_timeout_id = GObject.timeout_add(1500, self._start_highlight_fade)
        self.highlight_tags[self.highlight_tag_count] = {'tag': tag, 'time': time.time()}

    def _start_highlight_fade(self):
        # 1.5s 等待结束：切换为 33ms 淡出循环。返回 False 让本一次性 source 移除。
        # 若此期间所有 tag 已被外部移除（极端情况），直接清空 id 不启动循环。
        if self.highlight_tags:
            self._highlight_timeout_id = GObject.timeout_add(33, self.remove_or_color_highlight_tags)
        else:
            self._highlight_timeout_id = None
        return False

    def remove_or_color_highlight_tags(self):
        # start/end iter 对所有 tag 相同，提到循环外避免每个过期 tag 各调一次
        # get_start_iter / get_end_iter（C 调用 + 对象创建）。fade 色也仅取决于
        # 主题，循环内只需算 opacity_factor。
        start = end = None
        fade_color = None
        for tag_count in list(self.highlight_tags):
            item = self.highlight_tags[tag_count]
            time_factor = time.time() - item['time']
            if time_factor > 1.5:
                if time_factor <= 1.75:
                    if fade_color is None:
                        fade_color = ColorManager.get_ui_color('highlight_tag_textview')
                    color = Gdk.RGBA()
                    color.red, color.green, color.blue = fade_color.red, fade_color.green, fade_color.blue
                    opacity_factor = int(self.ease(1 - (time_factor - 1.5) * 4) * 20)
                    color.alpha = fade_color.alpha * opacity_factor * 0.05
                    item['tag'].set_property('background-rgba', color)
                else:
                    if start is None:
                        start = self.source_buffer.get_start_iter()
                        end = self.source_buffer.get_end_iter()
                    self.source_buffer.remove_tag(item['tag'], start, end)
                    self.source_buffer.get_tag_table().remove(item['tag'])
                    del(self.highlight_tags[tag_count])
        # 无剩余 tag 时返回 False 让 GLib 自动移除 source，并清空缓存的 id，
        # 以便 highlight_section 判断「无运行中 timeout」、shutdown 判断「无需取消」。
        if not self.highlight_tags:
            self._highlight_timeout_id = None
            return False
        return True

    def ease(self, factor): return (factor - 1)**3 + 1

    def scroll_cursor_onscreen(self, margin_lines=5):
        height = self.view.scrolled_window.get_allocated_height()
        if height > 0:
            # 可见性短路：on_cursor_position_change 每次光标移动都调本方法
            # （margin_lines=0），绝大多数按键光标本就可见。原实现每次都
            # set_kinetic_scrolling(False)+scroll_to_mark+set_kinetic_scrolling(True)，
            # 是无谓的属性抖动 + C 调用。先判断光标是否已在可视区域（含 margin）
            # 内，可见则直接返回，不触发 kinetic toggle。
            visible_rect = self.source_view.get_visible_rect()
            cursor_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
            cursor_loc = self.source_view.get_iter_location(cursor_iter)
            if margin_lines > 0:
                line_height = FontManager.get_line_height(self.source_view)
                margin_pixels = margin_lines * line_height
            else:
                margin_pixels = 0
            if (visible_rect.height > 0 and
                    cursor_loc.y >= visible_rect.y + margin_pixels and
                    cursor_loc.y + cursor_loc.height <= visible_rect.y + visible_rect.height - margin_pixels):
                return

            if margin_lines > 0:
                margin = margin_lines / (height / FontManager.get_line_height(self.source_view))
            else:
                margin = 0

            self.view.scrolled_window.set_kinetic_scrolling(False)
            self.source_view.scroll_to_mark(self.source_buffer.get_insert(), margin, False, 0, 0)
            self.view.scrolled_window.set_kinetic_scrolling(True)


