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

from setzer.helpers.file_io import read_text_with_encoding, write_text_with_encoding, detect_encoding

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
import setzer.document.bookmarks.bookmarks as bookmarks
import setzer.document.multicursor.multicursor as multicursor
import setzer.document.bracket_completion.bracket_completion as bracket_completion
import setzer.document.update_matching_blocks.update_matching_blocks as update_matching_blocks
import setzer.document.autocomplete.autocomplete as autocomplete
import setzer.document.begin_end_highlight.begin_end_highlight as begin_end_highlight
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
        # 记录原始文件的换行符格式（'\n' | '\r\n' | '\r'），用于保存时还原
        self.line_ending = '\n'
        # 每文档「最近使用」符号列表，结构同 favorites：[(category, command), ...]。
        # 由 DocumentSettings 随文档状态文件持久化（仅 filename 非空时落盘），
        # 区别于全局 app_recent_symbols——最近符号按文档区分，切文档即切换列表。
        self.recent_symbols = list()
        # 每文档「文档结构折叠」状态：已折叠的 section 节点 offset 集合。
        # 由 DocumentSettings 随文档状态文件持久化，切换文档或重启后恢复。
        self.collapsed_sections = set()
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

        # Undo 分组：合并连续打字为一个 undo 组，停顿 500ms 后开新组。
        self._undo_timeout_id = None
        self._undo_group_depth = 0
        # undo/redo 期间置 True，使 _ensure_undo_group 跳过——undo manager 修改
        # buffer 时触发的 insert-text/delete-range 不应再开新 user action 组。
        self._in_undo = False
        self.source_buffer.connect('insert-text', self._on_buffer_insert_text)
        self.source_buffer.connect('delete-range', self._on_buffer_delete_range)
        self.source_buffer.connect('undo', self._on_buffer_undo)
        self.source_buffer.connect('redo', self._on_buffer_redo)
        self.source_buffer.connect('paste-done', self._on_buffer_paste_done)

        self.view = document_view.DocumentView(self)
        self.presenter = document_presenter.DocumentPresenter(self, self.view)
        self.controller = document_controller.DocumentController(self, self.view)

        if self.is_latex_document(): self.parser = parser_latex.ParserLaTeX(self)
        elif self.is_bibtex_document(): self.parser = parser_bibtex.ParserBibTeX(self)
        else: self.parser = parser_dummy.ParserDummy(self)
        # BeginEndHighlight 仅对 LaTeX 文档构造：它依赖 parser 的
        # block_symbol_matches['begin_or_end']（latex parser 才会填充）；
        # 其它类型 parser 没有此字段，构造会 KeyError。DocumentController
        # 在 on_primary_buttonpress 中对非 LaTeX 文档已前置守卫不访问
        # begin_end_highlight（document_controller.py:191），与之对齐。
        if self.is_latex_document():
            self.begin_end_highlight = begin_end_highlight.BeginEndHighlight(self)
        self.code_folding = code_folding.CodeFolding(self)
        self.bookmarks = bookmarks.Bookmarks(self)
        self.gutter = gutter.Gutter(self, self.view)
        self.search = search.Search(self, self.view)
        self.multicursor = multicursor.MultiCursor(self)
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
        self.file_encoding = 'utf-8'
        self.has_bom = False
        # 程序化读盘期间（_load_file_content 的 set_text）置 True。set_text 会
        # 触发 buffer 'changed' 信号，但这是会话恢复/懒加载/文件打开，并非用户
        # 编辑——auto_build 据此跳过启动即重编。仅 auto_build 读取此标志，
        # parser 等其它 'changed' 观察者仍照常收到通知以初始化文档结构。
        self._loading_from_disk = False
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
        # 取消 undo 分组定时器，防止文档关闭后回调访问已释放的 buffer。
        if self._undo_timeout_id is not None:
            GLib.Source.remove(self._undo_timeout_id)
            self._undo_timeout_id = None

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

        # bookmarks 连接了 source_buffer 的 insert-text / delete-range 信号,
        # 需断开以防止已关闭文档的回调继续被触发。
        bookmarks = getattr(self, 'bookmarks', None)
        if bookmarks is not None:
            try:
                bookmarks.shutdown()
            except Exception:
                pass

        # multicursor 连接了 source_view 的 draw 信号和 source_buffer 的
        # cursor/insert/delete 信号，也挂载了 overlay，需清理以防止
        # 已关闭文档的回调继续触发和 UI 残留。
        mc = getattr(self, 'multicursor', None)
        if mc is not None:
            try:
                mc.shutdown()
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
        # begin_end_highlight 同样仅连接 settings 信号,但仅 LaTeX 文档构造。
        # 不断开会持有文档引用阻碍 GC,且后续设置变更会调到失效的 on_settings_changed。
        beh = getattr(self, 'begin_end_highlight', None)
        if beh is not None:
            try:
                self.settings.disconnect('settings_changed', beh.on_settings_changed)
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

    @staticmethod
    def _detect_line_ending(raw_bytes):
        '''检测文件的换行符格式。

        优先级：CRLF > CR > LF（CRLF 包含 CR 子串，需先判定）。
        空文件返回 LF。
        '''
        if b'\r\n' in raw_bytes:
            return '\r\n'
        if b'\r' in raw_bytes:
            return '\r'
        return '\n'

    @staticmethod
    def _strip_bom_bytes(raw_bytes):
        '''移除文件开头的 BOM 字节（如果存在）。

        支持 UTF-8 BOM (EF BB BF)、UTF-16 LE BOM (FF FE)、
        UTF-16 BE BOM (FE FF)。只移除 BOM 字节，不修改其他内容。
        '''
        if raw_bytes.startswith(b'\xef\xbb\xbf'):
            return raw_bytes[3:]
        if raw_bytes.startswith(b'\xff\xfe'):
            return raw_bytes[2:]
        if raw_bytes.startswith(b'\xfe\xff'):
            return raw_bytes[2:]
        return raw_bytes

    @staticmethod
    def _prepend_bom(encoded_bytes, encoding):
        '''根据编码类型在字节开头添加对应的 BOM。

        只在 has_bom=True 时调用，支持 utf-8/utf-16-le/utf-16-be。
        '''
        enc = encoding.lower().replace('-', '_')
        if enc in ('utf_8', 'utf8'):
            return b'\xef\xbb\xbf' + encoded_bytes
        if enc in ('utf_16_le', 'utf16_le', 'utf_16le'):
            return b'\xff\xfe' + encoded_bytes
        if enc in ('utf_16_be', 'utf16_be', 'utf_16be'):
            return b'\xfe\xff' + encoded_bytes
        return encoded_bytes

    def _load_file_content(self):
        '''读取文件内容并填入 source_buffer。

        从 populate_from_filename 抽出，供懒加载复用：会话恢复时非活跃文档
        延迟调用此方法（idle 或激活时），避免启动期同步读取 N 个大文件。
        加载后应用 _restore_cursor_offset / _restore_scroll_offset（若存在），
        因为懒加载文档在 _restore_document_states idle 时缓冲区尚为空，
        偏移恢复会失败——此处补做。

        同时检测原始换行符格式（CRLF / CR / LF）并缓存，保存时还原。
        GtkTextBuffer 内部统一用 LF，不保留原始换行符，需要外部记录。
        '''
        # 先用二进制读原始字节，检测换行符格式
        with open(self.filename, 'rb') as f:
            raw_bytes = f.read()
        self.line_ending = self._detect_line_ending(raw_bytes)

        # 先去掉 BOM（如果存在），再用正确的编码解码
        raw_bytes_no_bom = self._strip_bom_bytes(raw_bytes)

        # 检测编码并用正确的编码解码
        self.file_encoding, self.has_bom = detect_encoding(raw_bytes)
        text = raw_bytes_no_bom.decode(self.file_encoding, errors='replace')

        # 预置行号宽度：在 set_text 之前用文件真实行数把 gutter 宽度算好，
        # 避免大文档加载后行数从 0 跳到几千时行号区域“突然变宽”的跳变。
        # text.count('\n') + 1 是 O(1) 计数；空文件记 1 行，保持最小宽度。
        line_count = text.count('\n') + 1
        if getattr(self, 'gutter', None) is not None:
            self.gutter.presize_for_line_count(line_count)

        # 标记程序化读盘：set_text 会同步触发 buffer 'changed' → auto_build
        # 的 on_document_changed。此处的 changed 并非用户编辑（会话恢复/懒加载
        # /文件打开），auto_build 据此标志跳过，避免每次启动都白白重编一次。
        # 用 try/finally 保证异常路径也能复位，否则该文档后续真实编辑会被永久忽略。
        self._loading_from_disk = True
        try:
            self.source_buffer.begin_irreversible_action()
            self.source_buffer.set_text(text)
            self.source_buffer.end_irreversible_action()
            self.source_buffer.set_modified(False)
        finally:
            self._loading_from_disk = False
        self.place_cursor(0, 0)
        self.update_save_date()

        # 更新状态栏的编码显示（已在 __init__ 初始化，这里根据实际检测结果更新）
        if getattr(self, 'statusbar', None) is not None:
            self.statusbar.update_encoding_display()

        # 懒加载文档的游标/滚动恢复：_restore_document_states idle 在内容
        # 加载前运行时偏移无效（缓冲区空），此处内容已就绪，补做恢复。
        self.apply_restored_cursor()
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

    def save_to_disk(self, show_toast=True):
        if self.filename == None: return False

        # 懒加载安全守卫：内容未加载时缓冲区为空，直接保存会用空内容覆盖
        # 原文件（数据丢失）。先同步加载内容再保存。正常流程下非活跃文档
        # 不会被保存（UI 仅对活跃文档触发保存），此守卫防御 Save All /
        # AutoSave 等批量保存路径。
        if self._content_pending:
            self._load_content_if_pending()

        text = self.get_all_text()
        if text == None: return False

        # 将 GtkTextBuffer 中的 LF 转换回原始换行符格式
        # 先统一所有换行符为 LF（防御性处理），再转换为目标格式
        line_ending = getattr(self, 'line_ending', '\n')
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if line_ending != '\n':
            text = text.replace('\n', line_ending)

        try:
            dirname = os.path.dirname(self.filename)
            # exist_ok=True 一次调用替代 exists + makedirs：dirname 几乎总是存在，
            # 原实现每次保存都做一次多余 stat；exist_ok 时已存在不报错，省一次系统调用。
            if dirname:
                os.makedirs(dirname, exist_ok=True)

            # 使用二进制模式写入，避免 Python 在文本模式下额外转换换行符
            try:
                encoded = text.encode(self.file_encoding)
                # 如果原文件有 BOM，保存时保留 BOM 状态
                if self.has_bom:
                    encoded = self._prepend_bom(encoded, self.file_encoding)
            except (UnicodeEncodeError, LookupError):
                encoded = text.encode('utf-8', errors='replace')
                # BOM 状态不传递给 fallback 编码，保持安全
            with open(self.filename, 'wb') as f:
                f.write(encoded)
        except OSError as e:
            if show_toast:
                self._show_save_error_toast(str(e))
            return False

        self.update_save_date()
        self.controller.deleted_on_disk_dialog_shown_after_last_save = False
        self.source_buffer.set_modified(False)
        # 通知监听者文档已成功保存（AutoSave 据此删除对应的崩溃恢复临时文件，
        # 避免下次启动误把已保存的旧版本当作可恢复内容）。无参数，与 'changed'
        # 同模式：回调签名 callback(document)。
        self.add_change_code('saved')
        return True

    def _show_save_error_toast(self, error_msg):
        main_window = ServiceLocator.get_main_window()
        if main_window is not None and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(_('Could not save document: {error}').format(error=error_msg))
            toast.set_timeout(5)
            main_window.toast_overlay.add_toast(toast)

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

    # ref-like 命令的命令名集合：get_label_at_iter 在此集合中查找。
    _REF_COMMANDS = frozenset([
        'ref', 'eqref', 'autoref', 'pageref', 'nameref', 'vref',
        'cref', 'Cref', 'cite', 'citep', 'citet', 'citeauthor',
        'citeyear', 'citealt', 'citealp', 'citealt*', 'citealp*',
    ])

    def get_label_at_iter(self, iter_at_click):
        r'''检查 iter 是否在 \ref{...} 等引用命令的参数内，返回引用的 label 名。

        从 iter 所在行向前搜索反斜杠开头的命令名，若命令名属于
        _REF_COMMANDS 且 iter 在其后 {…} 大括号参数范围内，则返回
        去除空白后的参数文本；否则返回 None。
        '''
        if not self.is_latex_document():
            return None
        offset = iter_at_click.get_offset()
        line = iter_at_click.get_line()
        line_text = self.get_line(line)
        line_start_offset = self.source_buffer.get_iter_at_line(line)[1].get_offset()
        col = offset - line_start_offset
        if col < 0 or col > len(line_text):
            return None
        # 从光标位置向前查找最近的反斜杠
        search_text = line_text[:col + 1]
        backslash_pos = search_text.rfind('\\')
        if backslash_pos < 0:
            return None
        rest = line_text[backslash_pos:]
        # 匹配 \command{...} 模式
        import re
        match = re.match(r'\\([a-zA-Z]+)\*?\{([^}]*)\}', rest)
        if match is None:
            # 光标可能在 { 内但 } 尚未闭合
            match = re.match(r'\\([a-zA-Z]+)\*?\{([^}]*)$', rest)
        if match is None:
            return None
        command = match.group(1)
        if command not in self._REF_COMMANDS:
            return None
        label = match.group(2).strip()
        return label if label else None

    def get_label_at_cursor(self):
        r'''返回光标位置处的引用 label 名（若在 \ref{...} 内），否则 None。'''
        return self.get_label_at_iter(self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()))

    def get_chars_at_iter(self, start_iter, number_of_chars):
        end_iter = start_iter.copy()
        end_iter.forward_chars(number_of_chars)
        return self.source_buffer.get_text(start_iter, end_iter, False)

    def place_cursor(self, line_number, offset=0):
        _, text_iter = self.source_buffer.get_iter_at_line_offset(line_number, offset)
        self.source_buffer.place_cursor(text_iter)

    def apply_restored_cursor(self):
        '''应用会话恢复的光标位置及（可选）选区范围。
        由 _restore_document_states（idle，内容已加载）与 _load_file_content
        （懒加载内容就绪后补做）共同调用，避免两处重复维护导致行为漂移。
        仅恢复 insert 与 selection_bound 两个 mark：若无选区则折叠光标，
        否则按保存的两端分别落点，保留原有选择方向与范围。'''
        cursor_offset = getattr(self, '_restore_cursor_offset', None)
        if cursor_offset is None:
            return
        try:
            buf = self.source_buffer
            end_offset = buf.get_end_iter().get_offset()
            if 0 <= cursor_offset <= end_offset:
                insert_iter = buf.get_iter_at_offset(cursor_offset)
                sel_bound = getattr(self, '_restore_selection_bound_offset', None)
                if sel_bound is not None and 0 <= sel_bound <= end_offset and sel_bound != cursor_offset:
                    buf.move_mark(buf.get_insert(), insert_iter)
                    buf.move_mark(buf.get_selection_bound(), buf.get_iter_at_offset(sel_bound))
                else:
                    buf.place_cursor(insert_iter)
        except Exception:
            pass
        self._restore_cursor_offset = None
        self._restore_selection_bound_offset = None

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

    # ------------------------------------------------------------------
    # Undo 分组：合并连续打字为一个 undo 组，停顿 500ms 后开新组。
    # ------------------------------------------------------------------
    def _on_buffer_insert_text(self, buffer, location, text, length):
        self._ensure_undo_group()

    def _on_buffer_delete_range(self, buffer, start, end):
        self._ensure_undo_group()

    def _ensure_undo_group(self):
        if self._is_shutdown:
            return
        # 跳过程序化读盘（set_text）：在此期间调用 begin_user_action 会
        # 在 buffer 内部插入操作的信号处理中嵌套，导致 500ms 后
        # end_user_action 在不一致状态下段错误。
        if getattr(self, '_loading_from_disk', False):
            return
        # 撤销/重做期间 undo manager 会修改 buffer（delete-range / insert-text），
        # 此信号会回到这里。若此时 begin_user_action 开新组，会在 undo manager
        # 正在重组栈的过程中嵌套一个用户动作，破坏其内部状态；随后 500ms 定时器
        # 的 end_user_action 在该不一致状态下段错误。故 undo/redo 期间跳过。
        # _in_undo 在 _on_buffer_undo / _on_buffer_redo（undo 信号，RUN_LAST，
        # 先于默认 handler 即真正的撤销/重做执行）中置位，idle 清除。
        if getattr(self, '_in_undo', False):
            return
        if self._undo_group_depth == 0:
            self.source_buffer.begin_user_action()
            self._undo_group_depth = 1
        if self._undo_timeout_id is not None:
            GLib.Source.remove(self._undo_timeout_id)
        self._undo_timeout_id = GLib.timeout_add(500, self._on_undo_timeout)

    def _on_undo_timeout(self):
        # 定时器回调：source 由返回 False 自动移除，先清 id 再调 _close_undo_group，
        # 使后者不会重复 remove 一个正在派发的 source。
        self._undo_timeout_id = None
        self._close_undo_group()
        return False

    def _close_undo_group(self):
        if self._is_shutdown:
            return False
        if self._undo_group_depth > 0:
            self.source_buffer.end_user_action()
            self._undo_group_depth = 0
        # 必须移除 GLib source 再置 None：仅置 None 会留下孤儿 source，它稍后触发
        # 时会误关新开的 undo 组（_undo_group_depth 仍为 1），或在文档关闭后访问
        # 已释放的 buffer。定时器回调路径已在 _on_undo_timeout 清 id，此处为 None 跳过。
        if self._undo_timeout_id is not None:
            GLib.Source.remove(self._undo_timeout_id)
            self._undo_timeout_id = None
        return False

    def _on_buffer_undo(self, buffer):
        # undo 信号是 RUN_LAST：本回调先于默认 handler（真正的撤销）执行。
        # 置 _in_undo 使随后撤销操作修改 buffer 时触发的 _ensure_undo_group 跳过，
        # 避免在 undo manager 重组栈期间嵌套 begin_user_action。idle 清除。
        self._in_undo = True
        self._close_undo_group()
        GLib.idle_add(self._end_undo_redo)

    def _on_buffer_redo(self, buffer):
        self._in_undo = True
        self._close_undo_group()
        GLib.idle_add(self._end_undo_redo)

    def _end_undo_redo(self):
        self._in_undo = False
        return False

    def _on_buffer_paste_done(self, buffer, clipboard):
        self._close_undo_group()

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
        # 立即清除已有高亮：快速连续点击 PDF 不同位置时，旧高亮不应等自己的
        # 倒计时结束才消失，而应在新高亮出现的瞬间熄灭。
        if self.highlight_tags:
            buf_start = self.source_buffer.get_start_iter()
            buf_end = self.source_buffer.get_end_iter()
            for item in self.highlight_tags.values():
                self.source_buffer.remove_tag(item['tag'], buf_start, buf_end)
                self.source_buffer.get_tag_table().remove(item['tag'])
            self.highlight_tags.clear()
        if self._highlight_timeout_id is not None:
            try:
                GLib.Source.remove(self._highlight_timeout_id)
            except (ValueError, RuntimeError):
                pass
            self._highlight_timeout_id = None

        self.highlight_tag_count += 1
        # 复制并降 alpha:accent 全不透明(1.0)在编辑器里太浓,与
        # begin_end_match 保持同一浓度(0.20),「比行高亮浓厚一点点」。
        # get_ui_color 返回的是缓存引用,不能直接改 alpha。
        accent = ColorManager.get_ui_color('highlight_tag_textview')
        color = Gdk.RGBA(red=accent.red, green=accent.green, blue=accent.blue,
                         alpha=_HIGHLIGHT_SECTION_MAX_ALPHA)
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


