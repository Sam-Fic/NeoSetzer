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


import os.path


class BuildLogDialogPresenter(object):
    '''同步 build_log.items → dialog view，按设置项过滤显示哪些类型。

    `autoshow_build_log` 设置项的语义在 Pass-10 扩展为「弹窗显示哪些类型」：
      - 'errors'          → 仅 Errors
      - 'errors_warnings' → Errors + Warnings（不含 Badboxes）
      - 'all'             → Errors + Warnings + Badboxes

    该常量同时被 BuildLogDialogController.on_copy_all_clicked 引用，
    保证 Copy All 与显示范围一致。
    '''

    TYPE_FILTER = {
        'errors': {'Error'},
        'errors_warnings': {'Error', 'Warning'},
        'all': {'Error', 'Warning', 'Badbox'},
    }

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
        self._updating_filters = False

    def set_search_text(self, text):
        self.search_text = text.lower()
        self._last_signature = None
        self.populate()

    def set_filter_values(self, file_filter, type_filter, line_min, line_max):
        '''设置过滤器值并触发重建。'''
        if self._updating_filters:
            return
        self.file_filter = file_filter
        self.type_filter = type_filter
        self.line_min = line_min
        self.line_max = line_max if line_max > 0 else 999999
        self._last_signature = None
        self.populate()

    def on_build_log_finished_adding(self, build_log, has_been_built):
        self._update_filter_dropdowns()
        self.populate()

    def populate(self):
        '''重建弹窗内容：清空所有 group，按设置项过滤后重新追加 items。

        优化：弹窗未打开时仅标记 dirty 跳过重建；已打开时若内容签名未变也跳过。
        '''
        if not self.build_log.is_open:
            self._dirty = True
            return

        autoshow = self.build_log.settings.get_value('preferences', 'autoshow_build_log')
        visible_types = self.TYPE_FILTER.get(autoshow, self.TYPE_FILTER['all'])

        document = self.build_log.document
        build_system = document.build_system if (document is not None and document.build_system is not None) else None
        has_been_built = bool(getattr(build_system, 'document_has_been_built', False)) if build_system is not None else False
        build_time = getattr(build_system, 'build_time', None) if build_system is not None else None

        # 签名覆盖影响展示的全部输入：文档、构建状态、耗时、搜索文本、过滤器、可见 items 元组。
        def _matches_search(it):
            if not self.search_text:
                return True
            description = (it[4] or '').lower()
            filename = (it[2] or '').lower()
            line_number = str(it[3]) if it[3] >= 0 else ''
            return (self.search_text in description
                    or self.search_text in filename
                    or self.search_text in line_number)

        def _matches_filters(it):
            # 文件过滤
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

        visible_items = tuple((it[0], it[2], it[3], it[4]) for it in self.build_log.items if it[0] in visible_types and _matches_search(it) and _matches_filters(it))
        signature = (id(document), has_been_built, build_time, self.search_text, self.file_filter, self.type_filter, self.line_min, self.line_max, visible_items)
        if signature == self._last_signature:
            self._dirty = False
            return
        self._last_signature = signature
        self._dirty = False

        self.view.clear_all()

        any_visible = False
        for item in self.build_log.items:
            item_type = item[0]
            if item_type not in visible_types:
                continue
            # 应用过滤器
            if not _matches_search(item) or not _matches_filters(item):
                continue
            # item 元组：item[0]=type, item[2]=filename, item[3]=line_number, item[4]=description
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
        '''更新过滤器下拉框的选项列表。'''
        self._updating_filters = True
        try:
            # 收集所有唯一文件名
            filenames = sorted(set(os.path.basename(it[2]) for it in self.build_log.items if it[2]))
            filenames.insert(0, _('All'))
            self.view.update_file_filter(filenames)

            # 收集错误类型选项
            type_options = [_('All'), _('Undefined reference'), _('Missing package'), _('Syntax error')]
            self.view.update_type_filter(type_options)
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
