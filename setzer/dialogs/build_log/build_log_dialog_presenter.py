#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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


import os.path
import sys

from gi.repository import GLib


def classify_warning_type(item_type, description):
    '''把一条日志项归类为稳定的「warning 类型」，供「忽略此类 warning」使用。

    返回 (key, label)：
      - key：跨构建稳定（**不含**行号 / 文件名 / 具体引用名等噪声），存入 settings。
      - label：给用户看的类型名，用于右键菜单「忽略 <label> 类警告」。

    粒度设计：同一底层原因触发的不同实例（不同行号、不同文件名、不同具体
    引用名）归为同一 key，从而「忽略」一次即屏蔽全部同类，正好解决用户在
    每次构建都看到同一类无意义 warning（如 font shape warning）的困扰。
    '''
    text = (description or '').strip()

    if item_type == 'Badbox':
        if text.startswith('Overfull'):
            return ('badbox:overfull', _('Overfull \\hbox'))
        if text.startswith('Underfull'):
            return ('badbox:underfull', _('Underfull \\hbox'))
        return ('badbox:other', _('Badbox'))

    if item_type == 'Error':
        # 错误默认不可忽略（误忽略会掩盖真实编译失败），但仍提供稳定分类能力。
        return ('error:other', _('Error'))

    # —— Warning ——（解析器已去掉 "LaTeX Warning: " 等前缀后的文本）
    if 'Font shape' in text:
        return ('warning:font_shape', _('Font shape'))
    if text.startswith('LaTeX Font') or 'Size substitut' in text:
        return ('warning:latex_font', _('LaTeX font'))
    if text.startswith('Package '):
        # "Package hyperref Warning: <msg>" → 按包名归类，忽略具体消息内容。
        name = text.split(':', 1)[0].replace('Package ', '', 1).split()[0].strip()
        return ('warning:package:' + name, _('Package "{name}"').format(name=name))
    if text.startswith('Citation'):
        return ('warning:citation', _('Undefined citation'))
    if text.startswith('Reference'):
        return ('warning:reference', _('Undefined reference'))
    if text.startswith('There were'):
        return ('warning:undefined', _('Undefined references'))
    return ('warning:other', _('Warning'))


class BuildLogDialogPresenter(object):
    '''同步 build_log.items → dialog view。

    「Automatically show build log」设置项（`autoshow_build_log`）只决定
    **何时自动弹出**日志弹窗（见 build_log.py 的 update_items/has_items），
    **不**用于过滤弹窗内显示的内容。弹窗里显示哪些类型完全由弹窗自身的
    筛选控件（文件 / 类型 / 行号 / 搜索）决定，因此这里始终展示全部三种
    类型，交由窗口内控件进一步筛选。
    '''

    # 弹窗始终展示全部日志类型，内容的筛选交给窗口内的筛选控件。
    ALL_TYPES = {'Error', 'Warning', 'Badbox'}

    def __init__(self, build_log, dialog_view):
        self.build_log = build_log
        self.view = dialog_view

        # build_log 是 Observable；build_log_finished_adding 在 update_items 末尾触发。
        self.build_log.connect('build_log_finished_adding', self.on_build_log_finished_adding)

        # 弹窗关闭时跳过昂贵的行重建（clear_all + 数百行 make_row）。
        self._dirty = True
        # 内容签名短路：切换到日志相同的文档或重编译产生相同日志时，签名未变则跳过。
        self._last_signature = None
        self.search_text = ''

        # 过滤器状态
        self.file_filter = None
        self.type_filter = None
        self.line_min = 0
        self.line_max = 999999
        self.visible_types_filter = {'Error', 'Warning', 'Badbox'}  # 用户选择显示的类型
        self._updating_filters = False

        # 初始化 group 折叠/展开状态（从 settings 读取）
        self._init_group_expanded_state()

    def set_search_text(self, text):
        self.search_text = text.lower()
        self._last_signature = None
        self.populate()

    def set_filter_values(self, file_filter, type_filter, line_min, line_max, visible_types=None):
        '''设置过滤器值并触发重建。'''
        if self._updating_filters:
            return
        self.file_filter = file_filter
        self.type_filter = type_filter
        self.line_min = line_min
        self.line_max = line_max if line_max > 0 else 999999
        if visible_types is not None:
            self.visible_types_filter = visible_types
        self._last_signature = None
        self.populate()

    def _init_group_expanded_state(self):
        '''从 settings 读取 group 折叠/展开状态并应用到视图。'''
        expanded_state = self.build_log.settings.get_value(
            'window_state', 'build_log_groups_expanded'
        )
        if not isinstance(expanded_state, dict):
            expanded_state = {}
        # 确保所有类型都有值（兼容旧版 settings 文件）
        default_state = {'Error': True, 'Warning': True, 'Badbox': True}
        for item_type in default_state:
            expanded = expanded_state.get(item_type, default_state[item_type])
            self.view.set_group_expanded(item_type, expanded)

    def on_group_toggle(self, item_type, expanded):
        '''用户切换 group 折叠/展开状态时调用，保存到 settings。'''
        expanded_state = self.build_log.settings.get_value(
            'window_state', 'build_log_groups_expanded'
        )
        if not isinstance(expanded_state, dict):
            expanded_state = {'Error': True, 'Warning': True, 'Badbox': True}
        expanded_state[item_type] = expanded
        self.build_log.settings.set_value(
            'window_state', 'build_log_groups_expanded', expanded_state
        )

    def on_build_log_finished_adding(self, build_log, has_been_built):
        self._update_filter_dropdowns()
        self.populate()

    def _get_visible_types(self):
        '''返回用户选择显示的日志类型。

        用户可以通过复选框选择是否显示 Error、Warning、Badbox 三种类型。
        默认情况下显示所有类型。
        '''
        return self.visible_types_filter

    def _matches_search(self, it):
        '''检查日志项是否匹配当前搜索文本。'''
        if not self.search_text:
            return True
        description = (it[4] or '').lower()
        filename = (it[2] or '').lower()
        line_number = str(it[3]) if it[3] >= 0 else ''
        return (self.search_text in description
                or self.search_text in filename
                or self.search_text in line_number)

    def _matches_filters(self, it):
        '''检查日志项是否匹配当前文件/类型/行号筛选器。'''
        # 文件过滤：如果 self.file_filter 为 None，或者等于 'All'（已翻译），则不应用过滤
        if self.file_filter and self.file_filter != _('All'):
            if it[2] is None or os.path.basename(it[2]) != self.file_filter:
                return False
        # 错误类型过滤
        if self.type_filter and self.type_filter != _('All'):
            desc = (it[4] or '').lower()
            item_type = it[0]
            if not self._matches_error_type(self.type_filter, desc, item_type):
                return False
        # 行号范围过滤
        if it[3] >= 0 and (it[3] < self.line_min or it[3] > self.line_max):
            return False
        return True

    def _is_ignored(self, it):
        '''某条日志项是否属于被用户「忽略此类 warning」屏蔽的类型。'''
        ignored = self.build_log.settings.get_value('preferences', 'ignored_warning_types') or []
        if not ignored:
            return False
        key, _ = classify_warning_type(it[0], it[4])
        return key in ignored

    def refresh(self):
        '''忽略列表等外部状态变化后，强制重建弹窗内容（绕过签名短路）。'''
        self._last_signature = None
        self.populate()

    def get_visible_items(self):
        '''返回当前在 Build Log 弹窗中可见的所有日志项（原始 item 元组）。

        可见性 = 全部类型（弹窗始终展示） + 搜索文本 + 文件/类型/行号筛选器
                 + 被「忽略此类 warning」屏蔽的类型。
        即用户在弹窗里「看到什么」就返回什么。供 AI Fix All 按钮使用，
        保证发送给 Agent 的内容与用户视线一致。
        '''
        visible_types = self._get_visible_types()
        return [it for it in self.build_log.items
                if it[0] in visible_types
                and self._matches_search(it)
                and self._matches_filters(it)
                and not self._is_ignored(it)]

    def populate(self):
        '''重建弹窗内容：清空所有 group，按设置项过滤后重新追加 items。

        优化：弹窗未打开时仅标记 dirty 跳过重建；已打开时若内容签名未变也跳过。
        '''
        if not self.build_log.is_open:
            self._dirty = True
            return

        visible_types = self._get_visible_types()

        document = self.build_log.document
        build_system = document.build_system if (document is not None and document.build_system is not None) else None
        has_been_built = bool(getattr(build_system, 'document_has_been_built', False)) if build_system is not None else False
        build_time = getattr(build_system, 'build_time', None) if build_system is not None else None

        # 签名覆盖影响展示的全部输入：文档、构建状态、耗时、搜索文本、过滤器、
        # 被忽略的类型、可见类型、可见 items 元组。忽略列表变化时务必触发重建，否则用户
        # 点「忽略」后日志不会刷新。
        ignored_keys = tuple(sorted(
            self.build_log.settings.get_value('preferences', 'ignored_warning_types') or []))
        visible_types_tuple = tuple(sorted(self.visible_types_filter))
        visible_items = tuple((it[0], it[2], it[3], it[4]) for it in self.build_log.items
                              if it[0] in visible_types
                              and self._matches_search(it)
                              and self._matches_filters(it)
                              and not self._is_ignored(it))
        signature = (id(document), has_been_built, build_time, self.search_text,
                     self.file_filter, self.type_filter, self.line_min, self.line_max,
                     visible_types_tuple, ignored_keys, visible_items)
        if signature == self._last_signature:
            self._dirty = False
            return
        self._last_signature = signature
        self._dirty = False

        self.view.clear_all()

        # 把当前搜索文本下发给各列表，供 make_row 对标题/副标题做命中加粗。
        for lst in self.view.lists.values():
            lst.search_text = self.search_text

        # 按阶段分隔日志：在类型 group 内，当连续 item 的 stage (item[1])
        # 发生变化时，插入一个 stage header 行（'LaTeX' 或 'BibTeX'）。
        # 主文档（LaTeX）的 item 连续出现时不重复插入 header。
        any_visible = False
        last_stage_by_type = {}
        for item in self.build_log.items:
            item_type = item[0]
            if item_type not in visible_types:
                continue
            # 应用过滤器
            if not self._matches_search(item) or not self._matches_filters(item):
                continue
            # 应用「忽略此类 warning」过滤
            if self._is_ignored(item):
                continue
            # item 元组：item[0]=type, item[1]=stage, item[2]=filename,
            #            item[3]=line_number, item[4]=description
            stage = item[1]
            if stage != last_stage_by_type.get(item_type):
                self.view.add_stage_header(item_type, stage)
                last_stage_by_type[item_type] = stage
            self.view.add_item(item_type, item[2], item[3], item[4])
            any_visible = True

        # group 显隐：仅显示「在 visible_types 中 且 有内容」的 group。
        for item_type, group in self.view.groups.items():
            has_content = self.view.lists[item_type].get_first_child() is not None
            group.set_visible(item_type in visible_types and has_content)

        # 全空时显示空状态页，有内容时隐藏
        self.view.empty_state.set_visible(not any_visible)
        self.view.page.set_visible(any_visible)

        # 滚动回顶
        scrolled = self.view.page.get_first_child()
        if scrolled is not None:
            scrolled.get_vadjustment().set_value(0)
            scrolled.get_hadjustment().set_value(0)

        # 更新 HeaderBar 标题为构建状态
        self._update_header_title(has_been_built_implicit=True)

    def _update_filter_dropdowns(self):
        '''更新过滤器下拉框的选项列表，并尽量保持用户的选择。'''
        # 保存当前的过滤器选择（在更新下拉框之前）
        saved_file_filter = self.file_filter
        saved_type_filter = self.type_filter
        saved_visible_types = self.visible_types_filter.copy()
        
        self._updating_filters = True
        try:
            # 收集所有唯一文件名
            if sys.platform == 'win32':
                filenames = sorted(set(os.path.basename(it[2]) for it in self.build_log.items if it[2]),
                                    key=lambda filename: filename.casefold())
            else:
                raw_filenames = set(os.path.basename(it[2]) for it in self.build_log.items if it[2])
                # 使用 Python 内置排序替代 GLib  collation，避免触发 GLib 断言警告
                filenames = sorted(raw_filenames, key=lambda f: GLib.utf8_make_valid(f, -1).casefold())
            filenames.insert(0, _('All'))
            self.view.update_file_filter(filenames)

            # 收集错误类型选项
            type_options = [_('All'), _('Undefined reference'), _('Missing package'), _('Syntax error')]
            self.view.update_type_filter(type_options)
            
            # 更新类型复选框的状态（确保与 visible_types_filter 同步）
            self.view.set_selected_types(self.visible_types_filter)
        finally:
            self._updating_filters = False
        
        # 尝试恢复用户的过滤器选择（如果可能的话）
        self._restore_filter_selection(saved_file_filter, saved_type_filter, saved_visible_types)
    
    def _find_combo_index(self, combo, target_text):
        '''在 Gtk.ComboBoxText 中查找指定文本的索引位置。
        
        GTK4 的 Gtk.ComboBoxText 没有 find_text 方法，需要手动遍历。
        '''
        model = combo.get_model()
        if model is None:
            return -1
        for i in range(model.get_n_items()):
            if model[i][0] == target_text:
                return i
        return -1

    def _restore_filter_selection(self, saved_file_filter, saved_type_filter, saved_visible_types=None):
        '''尝试恢复用户之前选择的过滤器。'''
        self._updating_filters = True
        try:
            # 恢复文件过滤器
            if saved_file_filter and saved_file_filter != _('All'):
                # 尝试找到之前选择的文件
                found_index = self._find_combo_index(self.view.file_filter_combo, saved_file_filter)
                if found_index >= 0:
                    self.view.file_filter_combo.set_active(found_index)
                else:
                    # 如果找不到，重置为 All
                    self.view.file_filter_combo.set_active(0)
                    self.file_filter = _('All')
            else:
                self.view.file_filter_combo.set_active(0)
                self.file_filter = _('All')
            
            # 恢复类型过滤器（下拉框）
            if saved_type_filter and saved_type_filter != _('All'):
                # 尝试找到之前选择的类型
                found_index = self._find_combo_index(self.view.type_filter_combo, saved_type_filter)
                if found_index >= 0:
                    self.view.type_filter_combo.set_active(found_index)
                else:
                    # 如果找不到，重置为 All
                    self.view.type_filter_combo.set_active(0)
                    self.type_filter = _('All')
            else:
                self.view.type_filter_combo.set_active(0)
                self.type_filter = _('All')
            
            # 恢复可见类型过滤器（复选框）
            if saved_visible_types is not None:
                self.visible_types_filter = saved_visible_types
                self.view.set_selected_types(self.visible_types_filter)
        finally:
            self._updating_filters = False

    def _matches_error_type(self, type_filter, description, item_type):
        '''检查日志项是否匹配指定的错误类型过滤。'''
        if type_filter == _('Undefined reference'):
            return 'undefined' in description and 'reference' in description
        elif type_filter == _('Missing package'):
            return 'missing' in description or 'not found' in description
        elif type_filter == _('Syntax error'):
            return 'syntax' in description
        elif type_filter == _('All'):
            return True
        return True

    def _update_header_title(self, has_been_built_implicit):
        '''根据构建结果更新 HeaderBar 副标题。

        原底部面板顶部显示「Building successful (1.23s, no warnings or badboxes).」
        等状态文本；弹窗化后改为 HeaderBar title=「Build Log」+ subtitle=状态文本。
        '''
        document = self.build_log.document
        if document is None or document.build_system is None:
            self.view.set_header_title(_('Build Log'), '')
            return

        build_system = document.build_system
        if not getattr(build_system, 'document_has_been_built', False):
            self.view.set_header_title(_('Build Log'), '')
            return

        num_errors = build_system.get_error_count()
        num_others = build_system.get_warning_count() + build_system.get_badbox_count()

        # 时间字符串
        if build_system.build_time is not None:
            time_string = '{:.2f}s'.format(build_system.build_time)
        else:
            time_string = ''

        # 状态文本（与原 BuildLogPresenter.set_header_data 保持一致）
        if num_errors == 0:
            status = _('Building successful')
        else:
            status = ngettext('Building failed with {amount} error',
                              'Building failed with {amount} errors',
                              num_errors).format(amount=str(num_errors))

        if num_others == 0:
            warnings_text = _('no warnings or badboxes')
        else:
            warnings_text = ngettext('{amount} warning or badbox',
                                     '{amount} warnings or badboxes',
                                     num_others).format(amount=str(num_others))

        subtitle_parts = []
        if time_string:
            subtitle_parts.append(time_string)
        subtitle_parts.append(status)
        subtitle_parts.append(warnings_text)
        subtitle = ' · '.join(subtitle_parts) if subtitle_parts else ''

        self.view.set_header_title(_('Build Log'), subtitle)
