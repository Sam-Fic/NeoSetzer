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

from setzer.helpers.observable import Observable
from setzer.app.service_locator import ServiceLocator
from setzer.helpers.timer import timer


class CodeFolding(Observable):

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document
        self.source_buffer = self.document.source_buffer
        self.settings = ServiceLocator.get_settings()
        self.tag = self.source_buffer.create_tag('invisible_region', invisible=1)

        self.folding_regions = dict()
        self.folding_regions_by_line = dict()
        self.initial_folded_regions = None

        self.document.parser.connect('finished_parsing', self.on_parser_update)
        self.settings.connect('settings_changed', self.on_settings_changed)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if item == 'enable_code_folding' and value == False:
            for region in self.folding_regions.values():
                self.unfold(region)

    def on_parser_update(self, parser):
        # this method updates the dict of folding regions after the
        # main text changed and the parser has updated the blocks (potential
        # folding regions). the first step is to update the offsets of
        # previous folding regions (update their positions w.r.t. the
        # amount of text inserted or deleted). these updated positions
        # will be used in the algorithm further below.

        folding_regions = dict()
        if parser.last_edit[0] == 'insert':
            _, location_offset, text, text_length = parser.last_edit
            length = len(text)
            # location_offset 是 insert-text 信号触发时（插入前）的位置（记为 P）。
            # 旧 region 的偏移基于插入前的文档，取值范围 [0, old_doc_length]。
            # 正确的平移规则：
            #   index < P  → 保持原偏移（region 在插入点之前，不受影响）
            #   index >= P → 平移 +length（region 在插入点及之后，内容后移）
            # 因此 offset_start = P - 1（使 index <= offset_start 等价于 index < P），
            # offset_end = P（使 index >= offset_end 等价于 index >= P）。
            #
            # 原代码 offset_start = P + length - 1, offset_end = P + length 把
            # [P, P+length) 范围内的旧 region 当成"既不在前也不在后"而丢弃。
            # 单字符输入时该范围只含 P 本身，仅丢失插入点处的 region；但粘贴
            # 大段文本时 length 很，[P, min(P+length, old_doc_length)] 内的所有
            # 旧 region 都会被丢弃，导致这些区域的折叠状态丢失（被后续 unfold
            # 循环展开）。修复后所有旧 region 都能正确保留并平移。
            offset_start = location_offset - 1
            offset_end = location_offset
        elif parser.last_edit[0] == 'delete':
            _, start_offset, end_offset = parser.last_edit
            offset_start = start_offset
            offset_end = end_offset
            length = offset_start - offset_end
        for index, region in self.folding_regions.items():
            if index <= offset_start:
                folding_regions[index] = region
            elif index >= offset_end:
                folding_regions[index + length] = region

        # now update the folding regions w.r.t. the new parsing results.
        # if the offset of a region matches a previously included region,
        # that region is assumed to be the same as the previous one:
        # it will match if it's in the same place, after the above
        # relocations are taken into account.
        # this step is important, because we want to keep track of
        # which regions are folded, so we have to transfer that
        # state to the new regions, by identifying them with previous
        # ones.

        self.folding_regions = dict()
        self.folding_regions_by_line = dict()
        for block in parser.symbols['blocks']:
            if block[1] != None:
                # 不再按 starting_line 去重：同一行可能有多个嵌套 block（如
                # \section{...} \subsection{...} 写在同一行），它们有不同
                # offset_start 因此是独立的折叠区域。旧实现用 last_line 跳过
                # 同行第二个 block，导致该行嵌套折叠区域丢失。
                # folding_regions 按 offset_start 索引无冲突；folding_regions_by_line
                # 同行多个 block 时后者覆盖前者，gutter 点击该行时折叠最内层区域。
                if block[0] in folding_regions:
                    region = folding_regions[block[0]]
                    del(folding_regions[block[0]])
                else:
                    region = {'is_folded': False}
                region['offset_start'] = block[0]
                region['offset_end'] = block[1]
                region['starting_line'] = block[2]
                region['ending_line'] = block[3]
                self.folding_regions[block[0]] = region
                self.folding_regions_by_line[block[2]] = region

        # in a last step, the regions that are no longer
        # included, but were previously, are unfolded.

        for region in folding_regions.values():
            self.unfold(region)

        self.initial_folding()

    def get_region_by_line(self, line):
        if line in self.folding_regions_by_line:
            return self.folding_regions_by_line[line]
        return None

    def unfold_region_containing_line(self, line):
        '''展开包含指定行的所有已折叠区域（含嵌套祖先），使该行可见。
        用于错误跳转等场景：目标行若落在折叠区内会被隐藏，跳转前先展开。'''
        for region in self.folding_regions.values():
            if region['is_folded'] and region['starting_line'] < line <= region['ending_line']:
                self.unfold(region)

    def fold(self, region):
        region['is_folded'] = True
        self.hide_region(region)

    def unfold(self, region):
        region['is_folded'] = False
        self.show_region(region)

    def show_region(self, region):
        offset_start = region['offset_start']
        start_iter = self.source_buffer.get_iter_at_offset(offset_start)
        start_iter.forward_to_line_end()
        offset_end = region['offset_end']
        end_iter = self.source_buffer.get_iter_at_offset(offset_end)
        if not end_iter.ends_line():
            end_iter.forward_to_line_end()
        end_iter.forward_char()
        self.source_buffer.remove_tag(self.tag, start_iter, end_iter)
        for some_region in self.folding_regions.values():
            if some_region['is_folded']:
                if some_region['starting_line'] >= region['starting_line'] and some_region['ending_line'] <= region['ending_line']:
                    self.hide_region(some_region)
        self.add_change_code('folding_state_changed')

    def hide_region(self, region):
        offset_start = region['offset_start']
        start_iter = self.source_buffer.get_iter_at_offset(offset_start)
        start_iter.forward_to_line_end()
        offset_end = region['offset_end']
        end_iter = self.source_buffer.get_iter_at_offset(offset_end)
        if not end_iter.ends_line():
            end_iter.forward_to_line_end()
        end_iter.forward_char()
        self.source_buffer.apply_tag(self.tag, start_iter, end_iter)
        self.add_change_code('folding_state_changed')

    def fold_all(self):
        '''折叠所有有效的折叠区域。'''
        for region in self.folding_regions.values():
            self.fold(region)

    def unfold_all(self):
        '''展开所有折叠区域。'''
        for region in self.folding_regions.values():
            self.unfold(region)

    def _line_text(self, line):
        '''返回第 line 行（0-based）的文本内容（不含换行符）；越界返回 None。'''
        line_count = self.source_buffer.get_line_count()
        if line < 0 or line >= line_count:
            return None
        start = self.source_buffer.get_iter_at_line(line)[1]
        stop = start.copy()
        if not stop.ends_line():
            stop.forward_to_line_end()
        return self.source_buffer.get_text(start, stop, False)

    def get_folded_regions(self):
        folded_regions = list()
        for region in self.folding_regions.values():
            if region['is_folded']:
                start_line = region['starting_line']
                end_line = region['ending_line']
                # 除绝对行号外，额外保存「内容锚点」：起始/结束行文本 + 行跨度。
                # 行号在文档他处增删行后会发生偏移，仅按行号恢复会错位（甚至
                # 折叠到错误的区域）。内容锚点不依赖绝对位置，可正确恢复。
                # 旧格式（仅 starting_line/ending_line）仍被保留以兼容老状态文件。
                folded_regions.append({
                    'starting_line': start_line,
                    'ending_line': end_line,
                    'start_text': self._line_text(start_line),
                    'end_text': self._line_text(end_line),
                    'span': end_line - start_line,
                })
        return folded_regions

    def set_initial_folded_regions(self, folded_regions):
        if self.settings.get_value('preferences', 'enable_code_folding'):
            self.initial_folded_regions = folded_regions
            self.initial_folding()

    def initial_folding(self):
        if self.initial_folded_regions != None:
            for anchor in self.initial_folded_regions:
                region = self._find_region_by_anchor(anchor)
                if region != None:
                    self.fold(region)
        self.initial_folded_regions = None

    def _find_region_by_anchor(self, anchor):
        '''按保存的锚点定位应折叠的区域。

        新格式含内容锚点（start_text / end_text / span）：优先用起始行文本匹配，
        不依赖绝对行号，因而在文档他处增删行后仍能正确恢复。同名区域（如两个
        \\section{Introduction}）用结束行文本、再用行跨度进一步消歧。
        旧格式（仅 starting_line / ending_line）：回退到按行号匹配，兼容老状态文件。
        '''
        start_text = anchor.get('start_text')
        if start_text is not None:
            candidates = [r for r in self.folding_regions.values()
                          if self._line_text(r['starting_line']) == start_text]
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]
            # 多个起始行文本相同的区域：用结束行文本进一步区分。
            end_text = anchor.get('end_text')
            if end_text is not None:
                narrowed = [r for r in candidates
                            if self._line_text(r['ending_line']) == end_text]
                if narrowed:
                    candidates = narrowed
                    if len(candidates) == 1:
                        return candidates[0]
            # 仍不唯一：用行跨度（ending_line - starting_line）区分。
            span = anchor.get('span')
            if span is not None:
                narrowed = [r for r in candidates
                            if (r['ending_line'] - r['starting_line']) == span]
                if narrowed:
                    return narrowed[0]
            return candidates[0]
        # 旧格式：按行号匹配。
        region = self.get_region_by_line(anchor.get('starting_line'))
        if region != None and anchor.get('ending_line') == region['ending_line']:
            return region
        return None


