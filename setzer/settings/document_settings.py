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

from setzer.app.service_locator import ServiceLocator
from setzer.helpers.persistence import (
    load_json, save_json, migrate_pickle_to_json,
)
from setzer.helpers.document_state_paths import (
    state_paths, legacy_state_paths,
)


class DocumentSettings():

    def load_document_state(document):
        if document.filename == None: return

        config_folder = ServiceLocator.get_config_folder()
        json_path, _ = state_paths(document.filename, config_folder)

        # 非 LaTeX 文档只加载书签状态
        if not document.is_latex_document():
            document_data = load_json(json_path)
            if document_data is None:
                return
            DocumentSettings.update_general_state(document, document_data)
            return

        # 以下为 LaTeX 文档的完整状态加载
        # 一次性迁移：新名文件不存在时，查找旧 base64 名文件并迁移过来。
        # - 旧 .json 已存在（上一版本已迁移到 JSON 但仍用 base64 名）→ 重命名
        # - 旧 .pickle 已存在（更早版本）→ pickle→json 迁移并写到新名
        # - 都不存在 → 全新用户，无状态可加载
        # 迁移失败（如权限问题）静默回退到无状态加载，不阻塞文档打开。
        if not os.path.exists(json_path):
            legacy_json, legacy_pickle = legacy_state_paths(document.filename, config_folder)
            if os.path.exists(legacy_json):
                try:
                    os.rename(legacy_json, json_path)
                except OSError:
                    pass
            elif os.path.exists(legacy_pickle):
                migrate_pickle_to_json(json_path, legacy_pickle)
        else:
            # 新名 .json 已存在，但可能同时存在未被清理的旧 .pickle。
            # migrate_pickle_to_json 在 .json 已存在时是 no-op，安全调用。
            _, legacy_pickle = legacy_state_paths(document.filename, config_folder)
            migrate_pickle_to_json(json_path, legacy_pickle)

        document_data = load_json(json_path)
        if document_data is None:
            return
        DocumentSettings.update_document(document, document_data)

    def update_general_state(document, document_data):
        """Restore general (non-LaTeX-specific) document state."""
        # restore bookmarks (general editor feature for all document types)
        bookmark_lines = document_data.get('bookmarks', [])
        if bookmark_lines:
            document.bookmarks.load_bookmarks_from_data(bookmark_lines)
        # restore recent symbols
        document.recent_symbols = list(document_data.get('recent_symbols', []))

    def update_document(document, document_data):
        # 最近符号按文档区分：优先取状态文件中的 recent_symbols，缺失则保持默认空列表。
        # 放在最前，确保即便后续因 save_date 或 PDF 缺失等提前 return，最近符号仍被恢复。
        document.recent_symbols = list(document_data.get('recent_symbols', []))

        # restore bookmarks (general editor feature for all document types)
        bookmark_lines = document_data.get('bookmarks', [])
        if bookmark_lines:
            document.bookmarks.load_bookmarks_from_data(bookmark_lines)

        # 恢复文档结构折叠状态：list → set。放在 save_date 检查之前，
        # 确保即便后续因文件修改等提前 return，折叠状态也已恢复。
        document.collapsed_sections = set(document_data.get('structure_collapsed', []))

        # save_date 可能为 None（极端情况：文档状态在文件已被删除后保存）。
        # None <= number 在 Python 3 中抛 TypeError，用 is None 守卫跳过比较，
        # 直接恢复其余状态（折叠区域等不依赖 save_date）。
        if document_data.get('save_date') is not None:
            try:
                if document_data['save_date'] <= os.stat(document.filename).st_mtime - 0.001: return
            except OSError:
                pass

        document.code_folding.set_initial_folded_regions(document_data['folded_regions'])
        document.build_system.build_log_data = document_data['build_log_data']
        document.build_system.document_has_been_built = document_data['has_been_built']
        document.build_system.build_time = document_data['build_time']
        document.build_system.latex_interpreter = document_data.get('latex_interpreter')
        document.build_system.has_synctex_file = document_data['has_synctex_file']
        document.build_system.update_can_sync()

        pdf_filename = document_data['pdf_filename']
        pdf_date = document_data['pdf_date']
        xoffset = document_data['xoffset']
        yoffset = document_data['yoffset']
        zoom_level = document_data['zoom_level']

        if pdf_filename == None: return
        # 原: os.path.isfile(pdf_filename) + os.path.getmtime(pdf_filename) 两次
        # stat。改用单次 os.stat：FileNotFoundError 即文件不存在；st_mtime 兼用。
        try:
            pdf_st = os.stat(pdf_filename)
        except FileNotFoundError:
            return

        # pdf_filename 不依赖 PDF 内容，始终恢复。缩放恢复改为按“模式”进行：
        # 新版本保存了 zoom_mode（fit_to_width / fit_to_text_width / fit_to_height /
        # manual），fit 模式的具体级别依赖布局与视口，留待首帧布局建立后由
        # update_dynamic_zoom_levels 按模式推导并（fit_to_text_width 时）居中；
        # 旧版本无 zoom_mode，则沿用旧行为直接恢复保存的精确级别（记为 manual）。
        # 横向居中（fit_to_text_width）因此可在重启后复现，且重编译/缩放窗口后
        # 也始终正确。
        document.preview.set_pdf_filename(pdf_filename)

        manager = document.preview.zoom_manager
        zoom_mode = document_data.get('zoom_mode')
        valid_modes = ('fit_to_width', 'fit_to_text_width', 'fit_to_height', 'manual')
        if zoom_mode in valid_modes:
            manager.zoom_mode = zoom_mode
            if zoom_mode == 'manual':
                manager.set_zoom_level(zoom_level)
        else:
            manager.zoom_mode = 'manual'
            manager.set_zoom_level(zoom_level)

        # 仅当磁盘上的 PDF 与状态保存时是同一个文件（mtime 在 1 秒容差内匹配）
        # 才恢复滚动位置。PDF 重建后页数/尺寸可能变化，旧 (xoffset, yoffset)
        # 会指到错位的地方。滚动位置暂存到 _restore_pending，待首帧布局就绪后
        # 在 on_layout_changed 中应用（fit_to_text_width 仅恢复垂直位置，水平由居中
        # 决定），避免恢复一个依赖旧视口宽度的绝对水平偏移。
        if pdf_date is not None and abs(pdf_st.st_mtime - pdf_date) <= 1:
            manager._restore_pending = (xoffset, yoffset, manager.zoom_mode)
            # 若布局此刻已就绪（如内存中已有该文档），先用 update_dynamic_zoom_levels
            # 按恢复的 zoom_mode 重新推导级别并（fit_to_text_width 时）居中，再
            # on_layout_changed 应用暂存的滚动位置；否则交给首帧 layout_changed。
            if document.preview.layout is not None:
                manager.update_dynamic_zoom_levels()
                manager.on_layout_changed()

    def save_document_state(document):
        if document.filename == None: return

        document_data = dict()
        # 保存书签 (所有文档类型)
        document_data['bookmarks'] = document.bookmarks.get_data_for_persistence()
        document_data['recent_symbols'] = list(getattr(document, 'recent_symbols', []))

        # 非 LaTeX 文档只保存书签和最近符号
        if not document.is_latex_document():
            config_folder = ServiceLocator.get_config_folder()
            json_path, _ = state_paths(document.filename, config_folder)
            try:
                save_json(json_path, document_data)
            except (OSError, TypeError, ValueError):
                pass
            return

        # LaTeX 文档保存完整状态
        document_data['save_date'] = document.save_date
        document_data['folded_regions'] = document.code_folding.get_folded_regions()
        document_data['build_log_data'] = document.build_system.build_log_data
        document_data['has_been_built'] = document.build_system.document_has_been_built
        document_data['build_time'] = document.build_system.build_time
        document_data['latex_interpreter'] = document.build_system.latex_interpreter
        document_data['has_synctex_file'] = document.build_system.has_synctex_file

        document_data['pdf_filename'] = document.preview.pdf_filename
        document_data['pdf_date'] = document.preview.get_pdf_date()
        document_data['xoffset'] = document.preview.view.content.scrolling_offset_x
        document_data['yoffset'] = document.preview.view.content.scrolling_offset_y
        document_data['zoom_level'] = document.preview.zoom_manager.zoom_level
        document_data['zoom_mode'] = document.preview.zoom_manager.zoom_mode
        # 文档结构折叠状态：set → list 便于 JSON 序列化。
        document_data['structure_collapsed'] = list(document.collapsed_sections)

        # 文件名走 state_paths（hash+basename 可读方案）。save_document_state
        # 入口已守卫 document.filename != None，此处不会收到 None。
        config_folder = ServiceLocator.get_config_folder()
        json_path, _ = state_paths(document.filename, config_folder)
        try:
            save_json(json_path, document_data)
        except (OSError, TypeError, ValueError):
            pass
