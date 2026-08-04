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

from gi.repository import GLib

from setzer.app.service_locator import ServiceLocator
from setzer.helpers.observable import Observable
from setzer.helpers.timer import timer


# 文档级符号正则：label/include/input/subfile/subimport/bibliography/
# addbibresource/todo/usepackage/bibitem。原实现在 on_insert_text 与
# on_text_deleted 两处各写一份相同字面量，每次按键都经
# ServiceLocator.get_regex_object(...) 哈希查表（compiled 对象虽被缓存，
# 但每次按键查表本身也是无谓开销）。提到模块级一次性查表，热路径只取
# 已编译对象直接 finditer。
_OTHER_SYMBOLS_REGEX_PATTERN = (r'\\(label|include|input|subfile|subimport|bibliography|addbibresource|todo)(?:\[[^\{\[]*\]){0,1}\{((?:\s|\w|\:|\.|,|\/|\\|\'|-|\"|\(|\))*)\}|\\(usepackage)(?:\[[^\{\[]*\]){0,1}\{((?:\s|\w|\:|,)*)\}|\\(bibitem)(?:\[.*\]){0,1}\{((?:\s|\w|\:)*)\}')

# 块级符号正则：换行 / \begin{} / \end{} / 章节命令。
_BLOCK_SYMBOLS_REGEX_PATTERN = r'\n|\\(begin|end)\{((?:\w|•|\*)+)\}|\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)(?:\*){0,1}\{([^\{]*)\}'


class ParserLaTeX(Observable):

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document
        self.text_length = 0
        self.number_of_lines = 0
        self.block_symbol_matches = {'begin_or_end': list(), 'others': list()}
        self.other_symbols = list()

        self.symbols = dict()
        self.symbols['bibitems'] = set()
        self.symbols['labels'] = set()
        self.symbols['labels_with_offset'] = list()
        self.symbols['todos'] = set()
        self.symbols['todos_with_offset'] = set()
        self.symbols['included_latex_files'] = set()
        self.symbols['bibliographies'] = set()
        self.symbols['packages'] = set()
        self.symbols['packages_detailed'] = dict()
        self.symbols['blocks'] = list()

        self.last_edit = None

        # 解析防抖：连续输入期间不跑增量解析，停止输入 ~DEBOUNCE_MS 后再刷新。
        # 打字流畅性的主要瓶颈不是增量解析本身（它只算修改区），而是每次按键
        # 都 emit 'finished_parsing' 触发的下游连锁——尤其 sticky_scroll 的 O(n²)
        # 重算（见 sticky_scroll.py 注释）。防抖把「每键一次」降到「每停一次」。
        self._parse_timer = None
        # 防抖窗口内最早编辑起点（行/偏移最小），_flush 时以它为界重算其后整段。
        self._pending_first = None
        self._DEBOUNCE_MS = 200

        # 模块加载时一次性解析的正则对象，避免热路径里每次 finditer 都查表。
        self._other_symbols_regex = ServiceLocator.get_regex_object(_OTHER_SYMBOLS_REGEX_PATTERN)
        self._block_symbols_regex = ServiceLocator.get_regex_object(_BLOCK_SYMBOLS_REGEX_PATTERN)

        self.document.source_buffer.connect('insert-text', self.on_insert_text)
        self.document.source_buffer.connect('delete-range', self.on_text_deleted)

    #@timer
    def on_text_deleted(self, buffer, start_iter, end_iter):
        self.last_edit = ('delete', start_iter, end_iter)
        self._schedule_parsing(start_iter.get_line(), start_iter.get_offset())

    #@timer
    def on_insert_text(self, buffer, location_iter, text, text_length):
        self.last_edit = ('insert', location_iter, text, text_length)
        self._schedule_parsing(location_iter.get_line(), location_iter.get_offset())

    def stop(self):
        '''文档关闭时取消挂起的防抖定时器，避免对已销毁的 buffer 触发解析。'''
        if self._parse_timer is not None:
            GLib.source_remove(self._parse_timer)
            self._parse_timer = None
        self._pending_first = None

    def _schedule_parsing(self, line_start, offset_start):
        '''防抖：记录窗口内最早编辑起点，重启动时器。连续输入（间隔 <
        _DEBOUNCE_MS）只保留最后一次定时器，停止输入后才真正解析一次。'''

        if self._pending_first is None or (line_start, offset_start) < self._pending_first:
            self._pending_first = (line_start, offset_start)
        if self._parse_timer is not None:
            GLib.source_remove(self._parse_timer)
        self._parse_timer = GLib.timeout_add(self._DEBOUNCE_MS, self._flush_parsing)

    def _flush_parsing(self):
        '''防抖到期：对当前 buffer 全文重算一次并统一 emit 一次
        'finished_parsing'。连续输入期间不发通知，停止输入后才跑一次全量
        解析——把「每键一次 O(全文档) 解析 + 下游 O(n²) 刷新」降到「每停一次」，
        这正是 GNOME Text Editor 等主流编辑器采用的策略。全量重算保证与
        initial_parse 完全一致的正确性，不依赖旧状态做增量合并（后者在多
        次编辑下坐标系易漂移出错）。'''

        self._parse_timer = None
        if self._pending_first is None:
            return
        self._pending_first = None

        buffer = self.document.source_buffer
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        self.initial_parse(text)

    def parse_for_blocks(self, text, line_start, offset_line_start):
        block_symbol_matches = {'begin_or_end': list(), 'others': list()}
        counter = line_start
        for match in self._block_symbols_regex.finditer(text):
            if match.group(1) != None:
                block_symbol_matches['begin_or_end'].append((match, counter, match.start() + offset_line_start))
            elif match.group(3) != None:
                block_symbol_matches['others'].append((match, counter, match.start() + offset_line_start))
                counter += len(match.group(0).splitlines()) - 1
            if match.group(0) == '\n':
                counter += 1
        return block_symbol_matches

    #@timer
    def parse_blocks(self):
        blocks = dict()

        add_preamble_folding = True
        end_document_offset = None
        end_document_line = None
        begin_document_offset = None
        begin_document_line = None
        blocks_list = list()
        for (match, line_number, offset) in self.block_symbol_matches['begin_or_end']:
            if line_number == 0:
                add_preamble_folding = False

            # group(2) 原本每轮调用 2-4 次（strip 比较 + dict key + append），
            # 缓存原始值与 strip 后值避免重复 C 边界调用和字符串操作。
            group2 = match.group(2)
            group2_stripped = group2.strip()
            if match.group(1) == 'begin':
                if group2_stripped == 'document':
                    begin_document_offset = offset
                    begin_document_line = line_number
                # setdefault 替代原 try/except KeyError 控制流：原实现依赖
                # 异常路径做 dict 初始化，每次新增 key 都付出异常构造开销。
                # setdefault 一次属性查找完成「取或建」语义。
                blocks.setdefault(group2, []).append([offset, None, line_number, None])
            else:
                if group2_stripped == 'document':
                    end_document_offset = offset
                    end_document_line = line_number
                # 原实现用 try/except KeyError + try/except IndexError 两层异常
                # 控制流。改用 dict.get（key 不存在返回 None）+ truthy 检查
                # （空列表为 falsy，自动覆盖「key 存在但列表已 pop 空」的分支）。
                blocks_begin = blocks.get(group2)
                if blocks_begin:
                    block_begin = blocks_begin.pop()
                    block_begin[1] = offset
                    block_begin[3] = line_number
                    block_begin.append(group2)
                    blocks_list.append(block_begin)

        # levels 定义先于 relevant_following_blocks：用 len(levels) 派生
        # 列表容量，避免「7 个 list()」与 levels 条目数耦合。新增层级
        # （如 subsubparagraph）时只改 levels 一处即可。
        levels = {'part': 0, 'chapter': 1, 'section': 2, 'subsection': 3, 'subsubsection': 4, 'paragraph': 5, 'subparagraph': 6}
        relevant_following_blocks = [list() for _ in range(len(levels))]
        for (match, line_number, offset) in reversed(self.block_symbol_matches['others']):
            if line_number == 0:
                add_preamble_folding = False

            # group(3) 原本在循环中调用 2 次（levels 查表 + append），
            # 缓存到局部变量避免重复 C 边界调用。
            group3 = match.group(3)
            level = levels[group3]
            block = [offset, None, line_number, None]

            if len(relevant_following_blocks[level]) >= 1:
                # - 1 to go one line up
                block[1] = relevant_following_blocks[level][-1][0] - 1
                block[3] = relevant_following_blocks[level][-1][2] - 1
            else:
                if end_document_offset != None and block[0] < end_document_offset:
                    # - 1 to go one line up
                    block[1] = end_document_offset - 1
                    block[3] = end_document_line - 1
                else:
                    block[1] = self.text_length
                    block[3] = self.number_of_lines

            block.append(group3)
            block.append(match.group(4))
            blocks_list.append(block)
            # range 上界用 len(levels) 替代硬编码 7：与 levels 字典长度
            # 绑定，未来新增层级无需同步修改此循环。
            for i in range(level, len(levels)):
                relevant_following_blocks[i].append(block)

        if add_preamble_folding and begin_document_offset and begin_document_line:
            blocks_list.append([0, begin_document_offset - 1, 0, begin_document_line - 1, 'preamble'])

        self.symbols['blocks'] = sorted(blocks_list, key=lambda block: block[0])

    #@timer
    def parse_symbols(self):
        labels = set()
        labels_with_offset = list()
        todos = set()
        todos_with_offset = list()
        included_latex_files = list()
        bibliographies = set()
        bibitems = set()
        packages = set()
        packages_detailed = dict()
        # 缓存 group() 结果：match.group(N) 每次调用都经 C 边界查正则 capture group，
        # 原实现在 if/elif 链中对 group(1) 调用 5+ 次、对 group(2).strip() 调用 2-3 次。
        # 该函数在每次按键（on_insert_text/on_text_deleted 末尾）都执行，对大文档
        # （数百 other_symbols）累计开销可观。缓存到局部变量后每项仅查一次。
        for match in self.other_symbols:
            offset = match[1]
            match = match[0]
            group1 = match.group(1)
            if group1 == 'label':
                label = match.group(2).strip()
                labels.add(label)
                labels_with_offset.append([label, offset])
            elif group1 == 'include' or group1 == 'input' or group1 == 'subfile' or group1 == 'subimport':
                filename = match.group(2).strip()
                if not filename.endswith('.tex'):
                    filename += '.tex'
                included_latex_files.append((filename, offset))
            elif group1 == 'bibliography':
                bibfiles = match.group(2).strip().split(',')
                for entry in bibfiles:
                    bibliographies.add(entry.strip() + '.bib')
            elif group1 == 'addbibresource':
                bibfiles = match.group(2).strip().split(',')
                for entry in bibfiles:
                    bibliographies.add(entry.strip())
            elif group1 == 'todo':
                todo = match.group(2).strip()
                todos.add(todo)
                todos_with_offset.append([todo, offset])
            elif match.group(3) == 'usepackage':
                package_name = match.group(4).strip()
                packages.add(package_name)
                if package_name not in packages_detailed:
                    packages_detailed[package_name] = []
                packages_detailed[package_name].append([offset, match])
            elif match.group(5) == 'bibitem':
                bibitems.add(match.group(6).strip())

        self.symbols['labels'] = labels
        self.symbols['labels_with_offset'] = labels_with_offset
        self.symbols['included_latex_files'] = included_latex_files
        self.symbols['todos'] = todos
        self.symbols['todos_with_offset'] = todos_with_offset
        self.symbols['bibliographies'] = bibliographies
        self.symbols['bibitems'] = bibitems
        self.symbols['packages'] = packages
        self.symbols['packages_detailed'] = packages_detailed

    def initial_parse(self, text):
        '''文档初次加载（set_text）后一次性全量解析。

        文档通过 Gtk.TextBuffer.set_text() 加载时不会逐段发射 insert-text 信号，
        而 on_insert_text/on_text_deleted 是 blocks/符号的唯一增量解析入口，导致
        打开后、首次编辑前 symbols['blocks'] 始终为空。这会让 sticky scroll、文档
        结构侧边栏、代码折叠在"刚打开还没改过字"的文档上完全不显示内容。

        此方法对全文跑一次完整解析路径（与编辑触发的增量解析一致），并触发
        finished_parsing，使所有依赖 symbols 的功能在文档打开后即可用。
        幂等：重复调用只会基于当前文本重新计算，不会重复叠加。
        '''
        matches = self.parse_for_blocks(text, 0, 0)
        self.block_symbol_matches = matches
        self.text_length = len(text)
        self.number_of_lines = text.count('\n') + 1
        self.parse_blocks()
        self.parse_symbols()
        self.add_change_code('finished_parsing')


