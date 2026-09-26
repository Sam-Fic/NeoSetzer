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

'''TikZ 片段实时预览（文档级控制器，仅 LaTeX 文档构造）。

光标停在 tikzpicture 内、停止输入后，把这一段连同 root 文档的 preamble
编译成单页 PDF 并渲染到右侧预览栏。编译/渲染/缓存复用共用的
SnippetEngine（见 snippet_engine.py），本类只做「何时做哪一段」的决策：

- **触发**挂在 parser 的 finished_parsing（200 ms 防抖之后）与光标移动上，
  再叠一层 tikz_preview_delay（默认 600 ms，下限 300 ms）——末键到发起
  编译约 0.8 s。挂在 changed 上会在每个按键重排计时器，且解析结果还没
  更新，取到的 tikzpicture 边界是旧的。
- **只服务于被展示的文档**：presenter 调 set_tracking(True/False)。
  后台标签页不排程、不起编译器。
- **图身份去重**：(start, 行数, 长度) 未变则直接复用上一帧，不清屏也不
  重编——光移动光标不该让图闪一下。
- **光标不在任何图内时不改变现状**：保留上一帧（用户多半只是在读正文），
  只有在无帧可显示时才给「把光标放进 tikzpicture」的提示。
- **失败冷却**：同一 (root 签名, 图起点) 连续失败两次即停手，直到换图、
  root preamble 改动或用户按 Retry（见 retry_policy.py）。

已知且有意接受的失败模式：图比纸面宽时会被墨迹裁剪；多页产物只渲染
第 1 页；图内 \\ref/\\label 显示为 ??；未保存文档没有 build_dir，
相对路径的 \\includegraphics 解析不了。
'''

import os
from typing import NamedTuple

from gi.repository import GObject, GLib

from setzer.helpers.observable import Observable
from setzer.app.service_locator import ServiceLocator
from setzer.settings.document_settings import DocumentSettings
from setzer.document.snippet_preview.retry_policy import RetryPolicy
from setzer.document.snippet_preview.root_context import resolve_root_context
from setzer.document.snippet_preview.snippet_wrapper import build_figure_document
from setzer.document.snippet_preview.tikz_region_finder import find_tikzpicture_at

# 本前端在引擎里的取消作用域标识（hover 公式预览用 'math'）。
TAG = 'tikz'

# 单张图的源文本上限：几万字符的图编译慢、贴图巨大，实时预览价值低。
_MAX_FIGURE_CHARS = 20000

_DEFAULT_DELAY_MS = 600
_MIN_DELAY_MS = 300


def _disabled_message():
    '''开关关闭时的提示。两条路径（改设置、下次评估）共用，避免一处带
    原因、一处只剩「把光标放进图里」的空提示。'''
    return _('TikZ Preview is turned off. Enable it in the Editor preferences.')


class TikzPreviewState(NamedTuple):
    '''预览栏要呈现的状态。kind ∈ no_picture / loading / ready / failed。'''
    kind: str        # 见下
    texture: object  # 最近一帧 Gdk.Texture；loading/failed 时保留上一帧
    message: str     # failed 的原因 / no_picture 的提示；其余为空

    # no_picture：无帧可显示（光标不在图内且还没编过任何图 / 功能关闭）
    # loading   ：正在编译，texture 若有则是上一帧，供 UI 半透明覆盖
    # ready     ：texture 是最新一帧
    # failed    ：编译失败，message 为首条错误，UI 给 Retry


class TikzPreview(Observable):

    def __init__(self, document, engine):
        Observable.__init__(self)
        self.document = document
        self.engine = engine
        self.settings = ServiceLocator.get_settings()

        self.state = TikzPreviewState('no_picture', None, '')

        self._tracking = False
        self._timer_id = None
        self._texture = None       # 最近一帧，跨图保留（loading 不白屏）
        self._shown_key = None     # 已展示/在飞的图身份 (start, 行数, 长度)
        self._request_id = None    # 在飞请求；None = 无
        self._retry_policy = RetryPolicy()

        self.settings.connect('settings_changed', self.on_settings_changed)
        self.document.parser.connect('finished_parsing', self.on_parser_finished)
        self.document.connect('cursor_position_changed', self.on_cursor_changed)

    # ---------- 外部接口 ----------

    def set_tracking(self, tracking):
        '''面板是否正在展示本文档的片段预览。

        调用方（presenter）须先让视图 set_source(self) 渲染当前 state，
        再置 True——置 True 时会立即评估一次，不经过防抖：切换模式/切标签
        是用户动作，不是打字。
        '''
        tracking = bool(tracking)
        if tracking == self._tracking:
            return
        self._tracking = tracking
        if tracking:
            self._evaluate()
        else:
            self._cancel_timer()
            self._cancel_inflight()

    def retry(self):
        '''面板上 Retry 按钮：清除冷却并立即重编当前图。'''
        self._retry_policy.reset()
        self._cancel_timer()
        self._evaluate(force=True)

    def shutdown(self):
        self._tracking = False
        self._cancel_timer()
        self._cancel_inflight()
        for observable, change_code, callback in (
                (self.settings, 'settings_changed', self.on_settings_changed),
                (self.document.parser, 'finished_parsing', self.on_parser_finished),
                (self.document, 'cursor_position_changed', self.on_cursor_changed)):
            try:
                observable.disconnect(change_code, callback)
            except (TypeError, KeyError, AttributeError, ValueError):
                pass

    # ---------- 信号 ----------

    def on_parser_finished(self, parser):
        self._schedule()

    def on_cursor_changed(self, document):
        # 打字也会移动光标：这里同样只重排防抖计时器，语义仍是「停下才编」。
        self._schedule()

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if section != 'preferences':
            return
        if item == 'tikz_preview_delay':
            return  # 下次排程自然读新值
        if item != 'tikz_live_preview':
            return
        if value:
            if self._tracking:
                self._evaluate()
            return
        self._cancel_timer()
        self._cancel_inflight()
        self._texture = None
        self._shown_key = None
        self._retry_policy = RetryPolicy()
        self._set_state('no_picture', None, _disabled_message())

    # ---------- 排程 ----------

    def _schedule(self):
        if not self._tracking:
            return
        self._cancel_timer()
        self._timer_id = GObject.timeout_add(self._delay_ms(), self._on_timer)

    def _on_timer(self):
        self._timer_id = None
        self._evaluate()
        return False

    def _cancel_timer(self):
        if self._timer_id is not None:
            GLib.Source.remove(self._timer_id)
            self._timer_id = None

    def _cancel_inflight(self):
        # 先注销 request_id 再取消：cancel_waiting 同步回调 None，若不先
        # 注销会被当成一次编译失败，白白推进冷却计数。
        self._request_id = None
        self.engine.cancel_waiting(TAG)

    def _delay_ms(self):
        delay = DocumentSettings.get_effective_value(
            self.document, self.settings, 'tikz_preview_delay')
        try:
            delay_ms = int(float(delay) * 1000)
        except (TypeError, ValueError):
            delay_ms = _DEFAULT_DELAY_MS
        return max(delay_ms, _MIN_DELAY_MS)

    # ---------- 决策 ----------

    def _evaluate(self, force=False):
        self._timer_id = None
        if not self._tracking:
            return

        if not DocumentSettings.get_effective_value(
                self.document, self.settings, 'tikz_live_preview'):
            self._cancel_inflight()
            self._texture = None
            self._shown_key = None
            self._set_state('no_picture', None, _disabled_message())
            return

        # 程序化读盘（打开/会话恢复/重载）不算用户编辑：此时 symbols 可能
        # 是上一份内容或空的，等 finished_parsing 与光标落位后再来。
        if getattr(self.document, '_loading_from_disk', False):
            return

        text = self._get_document_text()
        blocks = getattr(self.document.parser, 'symbols', {}).get('blocks') or []
        figure = find_tikzpicture_at(blocks, text, self._get_cursor_offset())

        if figure is None:
            self._shown_key = None
            if self._texture is not None:
                return  # 保留上一帧
            self._set_state('no_picture', None, '')
            return

        figure_key = (figure.start, figure.raw.count('\n'), len(figure.raw))
        if not force and figure_key == self._shown_key:
            return  # 同一张图且没改过：复用上一帧

        if not self.engine.is_engine_available():
            self._shown_key = figure_key
            self._set_state('failed', self._texture,
                            _('No LaTeX compiler was found for the snippet preview.'))
            return
        if len(figure.raw) > _MAX_FIGURE_CHARS:
            self._shown_key = figure_key
            self._set_state('failed', self._texture, _(
                'This figure is too large for the live preview.'))
            return

        context = resolve_root_context(
            self.document.get_filename(), text,
            open_sources=_OpenDocumentTexts())

        # 身份不含图内容：改图不算「换了一张图」（见 retry_policy 模块说明）。
        identity = (context.signature, figure.start)
        if not force and not self._retry_policy.should_attempt(identity):
            self._shown_key = figure_key
            self._set_state('failed', self._texture,
                            self._retry_policy.last_reason)
            return

        wrapped = build_figure_document(figure.raw, context.text)
        request_id = self.engine.request(
            wrapped, cwd=context.build_dir, callback=self._on_texture, tag=TAG)
        if request_id is None:
            self._shown_key = figure_key
            self._set_state('failed', self._texture,
                            _('No LaTeX compiler was found for the snippet preview.'))
            return

        self._request_id = request_id
        self._shown_key = figure_key
        self._set_state('loading', self._texture, '')

    def _on_texture(self, request_id, texture):
        if request_id != self._request_id:
            return  # 已被更新的请求或取消取代
        self._request_id = None
        if texture is not None:
            self._retry_policy.record_success()
            self._texture = texture
            self._set_state('ready', texture, '')
            return
        reason = self.engine.get_delivery_failure_reason()
        self._retry_policy.record_failure(reason)
        self._set_state('failed', self._texture, reason or _('Compilation failed.'))

    def _set_state(self, kind, texture, message=''):
        self.state = TikzPreviewState(kind, texture, message)
        self.add_change_code('tikz_preview_state_changed')

    # ---------- 文档访问 ----------

    def _get_document_text(self):
        buffer = self.document.source_buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def _get_cursor_offset(self):
        buffer = self.document.source_buffer
        return buffer.get_iter_at_mark(buffer.get_insert()).get_offset()


class _OpenDocumentTexts(object):
    '''open_sources 的惰性实现：只对命中 root 的那个文件取一次 buffer 文本。

    resolve_root_context 只做 ``in`` 与 ``[]`` 两次查询，所以没必要（也不
    该）在每次评估时把所有打开文档的全文都抽出来。
    '''

    def __getitem__(self, filename):
        document = self._find(filename)
        buffer = document.source_buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def __contains__(self, filename):
        return self._find(filename) is not None

    def _find(self, filename):
        workspace = ServiceLocator.get_workspace()
        documents = getattr(workspace, 'open_documents', None) or []
        target = os.path.normpath(filename)
        for document in documents:
            candidate = document.get_filename()
            if candidate and os.path.normpath(candidate) == target:
                return document
        return None
