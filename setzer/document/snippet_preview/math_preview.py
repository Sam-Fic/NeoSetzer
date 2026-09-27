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

'''公式 hover 预览前端（文档级，仅 LaTeX 文档构造）。

只负责公式特有的一段：按 offset 从 parser 的 symbols['math_regions']
查出数学区域、长度上限判断、包上文档 preamble，然后编译/渲染/缓存全部
交给共用的 SnippetEngine（见 snippet_engine.py）。

失效管理：buffer 变更即让挂起请求以 None 收尾（弹窗层据此收起），
设置开关即时生效。取消只针对本前端的 tag，不会牵连 TikZ 侧栏预览在飞的
请求（反之亦然）。
'''

from setzer.helpers.observable import Observable
from setzer.app.service_locator import ServiceLocator
from setzer.document.snippet_preview.math_region_finder import find_region_at
from setzer.document.snippet_preview.snippet_wrapper import build_snippet_document

# 本前端在引擎里的取消作用域标识。
TAG = 'math'

# hover 贴图的渲染密度（px/pt）。显示端以 texture/2 呈现（见
# math_preview_popover），故 4.0 密度 = 公式以文档字号的 2 倍大小弹出，
# 且在 1x/2x 显示器上都有 ≥2 渲染像素/设备像素的清晰度余量。
# TikZ 侧栏不传 density，仍走引擎默认 RENDER_DENSITY=2.0，互不影响。
HOVER_RENDER_DENSITY = 4.0

# 单个公式的源文本上限：超长环境（数百行的 align 块）编译慢、贴图巨大，
# hover 预览价值低，直接跳过。
_MAX_REGION_CHARS = 3000

# 驻留时长（ms）：指针在数学区域内停留这么久才弹窗——避免划过公式时
# 连环弹窗。由 document_controller 使用。
DWELL_MS = 450


class MathPreview(Observable):

    def __init__(self, document, engine):
        Observable.__init__(self)
        self.document = document
        self.engine = engine
        self.settings = ServiceLocator.get_settings()
        self.settings.connect('settings_changed', self.on_settings_changed)
        self.document.source_buffer.connect('changed', self.on_buffer_changed)

    # ---------- 查询 ----------

    def get_region_at(self, offset):
        '''offset 所在数学区域（MathRegion）；不在任何区域内返回 None。'''
        symbols = getattr(self.document.parser, 'symbols', {})
        regions = symbols.get('math_regions')
        if not regions:
            return None
        return find_region_at(regions, offset)

    def is_engine_available(self):
        return self.engine.is_engine_available()

    def request(self, offset, callback):
        '''请求 offset 所在公式的贴图（契约见 SnippetEngine.request）。'''
        region = self.get_region_at(offset)
        if region is None:
            return None
        if region.end - region.start > _MAX_REGION_CHARS:
            return None
        wrapped = build_snippet_document(region.raw, self._get_document_text())
        return self.engine.request(wrapped, callback=callback, tag=TAG,
                                   density=HOVER_RENDER_DENSITY)

    # ---------- 信号 ----------

    def on_buffer_changed(self, buffer):
        self.engine.cancel_waiting(TAG)
        self.add_change_code('math_preview_invalidated')

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if section == 'preferences' and item == 'math_hover_preview' and not value:
            self.engine.cancel_waiting(TAG)
            self.add_change_code('math_preview_invalidated')

    def shutdown(self):
        '''文档关闭清理：断开单例信号、作废挂起请求。

        引擎与其临时目录由 Document 统一关闭（两个前端共用一份）。
        '''
        self.engine.cancel_waiting(TAG)
        try:
            self.settings.disconnect('settings_changed', self.on_settings_changed)
        except (TypeError, KeyError, AttributeError):
            pass
        try:
            self.document.source_buffer.disconnect('changed', self.on_buffer_changed)
        except (TypeError, KeyError, AttributeError):
            pass

    def _get_document_text(self):
        buffer = self.document.source_buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
