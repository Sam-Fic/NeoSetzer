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

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

# 缩放档位上下限与步长。与 PDF 预览的离散档位不同：片段是单张图，
# 倍数缩放的连续手感更合适。
_ZOOM_MIN = 0.25
_ZOOM_MAX = 4.0
_ZOOM_STEP = 1.25

# 首次显示某张图时的可用宽度回退值（视图尚未分配尺寸时）。
_FALLBACK_WIDTH = 280


class SnippetPreviewView(Gtk.Box):
    '''侧栏里的片段图视图（全应用共享一个，只有可见文档驱动它）。

    单个 ScrolledWindow + 居中 Picture，上面叠一层状态层，四种状态：

      - ready      ：正常显示贴图，无状态层；
      - loading    ：Spinner 半透明叠在**上一帧**上——绝不白屏；
      - no_picture ：无帧可显示（光标不在图内 / 功能未开启），显示提示；
      - failed     ：显示失败原因 + Retry（上一帧仍在，供对照）。

    视图不持有文档：presenter 用 set_source(preview) 指向当前目标，
    状态变化经 TikzPreview 的 'tikz_preview_state_changed' 推回来。
    '''

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.add_css_class('preview')

        self._source = None
        self._texture = None
        self._base_size = (0, 0)
        self._zoom = 1.0

        # 卡片外观与 PDF 预览一致（圆角 + 裁剪），两态在侧栏里视觉同源。
        self.card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card_box.set_hexpand(True)
        self.card_box.set_vexpand(True)
        self.card_box.add_css_class('preview-card')
        self.card_box.set_overflow(Gtk.Overflow.HIDDEN)
        self.append(self.card_box)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC,
                                        Gtk.PolicyType.AUTOMATIC)
        self.scrolled_window.set_hexpand(True)
        self.scrolled_window.set_vexpand(True)
        self.card_box.append(self.scrolled_window)

        self.overlay = Gtk.Overlay()
        self.overlay.set_hexpand(True)
        self.overlay.set_vexpand(True)
        self.scrolled_window.set_child(self.overlay)

        # 固定尺寸 Box 包住 Picture：缩放就是改 Box 的 size_request。
        # 图小于视口时靠居中对齐，大于视口时由 ScrolledWindow 滚动。
        self.picture_box = Gtk.Box()
        self.picture_box.set_halign(Gtk.Align.CENTER)
        self.picture_box.set_valign(Gtk.Align.CENTER)
        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        self.picture.set_can_target(False)
        self.picture_box.append(self.picture)
        self.overlay.set_child(self.picture_box)

        self.status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.status_box.set_halign(Gtk.Align.CENTER)
        self.status_box.set_valign(Gtk.Align.CENTER)
        self.status_box.add_css_class('snippet-preview-status')
        self.spinner = Gtk.Spinner()
        self.status_box.append(self.spinner)
        self.status_label = Gtk.Label()
        self.status_label.set_wrap(True)
        self.status_label.set_justify(Gtk.Justification.CENTER)
        self.status_label.set_max_width_chars(28)
        self.status_label.add_css_class('dim-label')
        self.status_box.append(self.status_label)
        self.retry_button = Gtk.Button(label=_('Retry'))
        self.retry_button.set_halign(Gtk.Align.CENTER)
        self.retry_button.add_css_class('pill')
        self.status_box.append(self.retry_button)
        self.overlay.add_overlay(self.status_box)

        self.set_state(None)

    # ---------- 数据源 ----------

    def set_source(self, preview):
        '''指向当前目标文档的 TikzPreview（None = 无目标）。'''
        if self._source is not None:
            self._source.disconnect('tikz_preview_state_changed',
                                    self._on_source_state_changed)
        self._source = preview
        if preview is None:
            self.set_state(None)
            return
        preview.connect('tikz_preview_state_changed', self._on_source_state_changed)
        self.set_state(preview.state)

    def _on_source_state_changed(self, preview):
        self.set_state(preview.state)

    # ---------- 状态 ----------

    def set_state(self, state):
        '''渲染一个 TikzPreviewState（None = 清空为无帧）。'''
        if state is None:
            self.show_texture(None)
            self._set_status(None, '', False)
            return
        if state.texture is not self._texture:
            self.show_texture(state.texture)
        if state.kind == 'loading':
            self._set_status('loading', '', False)
        elif state.kind == 'ready':
            self._set_status(None, '', False)
        elif state.kind == 'failed':
            self._set_status('failed', state.message, True)
        else:
            self._set_status('no_picture', state.message or _(
                'Put the cursor inside a tikzpicture to preview it here.'), False)

    def show_texture(self, texture):
        self._texture = texture
        self.picture.set_paintable(texture)
        if texture is None:
            self._base_size = (0, 0)
            self.picture_box.set_size_request(-1, -1)
            return
        base_size = (texture.get_width(), texture.get_height())
        if base_size != self._base_size:
            # 只在图本身尺寸变化时回到「适配宽度」：改一个坐标不该让
            # 用户刚调好的缩放跳掉。
            self._base_size = base_size
            self._zoom = self._fit_zoom()
        self._apply_zoom()

    def _set_status(self, icon, message, retry):
        self.spinner.set_visible(icon == 'loading')
        if icon == 'loading':
            self.spinner.start()
        else:
            self.spinner.stop()
        self.status_label.set_text(message)
        self.status_label.set_visible(bool(message))
        self.retry_button.set_visible(retry)
        self.status_box.set_visible(icon is not None or bool(message))
        # 纯文字/转圈时不吃指针事件：让滚轮照常落到 ScrolledWindow 上。
        self.status_box.set_can_target(retry)

    # ---------- 缩放 ----------

    def _fit_zoom(self):
        '''默认缩到面板宽度以内，绝不放大——大图先看全局，细节再放大。'''
        width, _height = self._base_size
        if not width:
            return 1.0
        available = self.get_width() - 24
        if available <= 0:
            available = _FALLBACK_WIDTH
        return max(_ZOOM_MIN, min(1.0, available / width))

    def set_zoom(self, zoom):
        '''设置缩放，返回是否真的生效（被上下限钳制且值未变时为 False）。'''
        zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, zoom))
        if abs(zoom - self._zoom) < 1e-6:
            return False
        self._zoom = zoom
        self._apply_zoom()
        return True

    def get_zoom(self):
        return self._zoom

    def has_frame(self):
        return self._texture is not None

    def zoom_in(self):
        return self.set_zoom(self._zoom * _ZOOM_STEP)

    def zoom_out(self):
        return self.set_zoom(self._zoom / _ZOOM_STEP)

    def _apply_zoom(self):
        width, height = self._base_size
        if not width or not height:
            return
        self.picture_box.set_size_request(max(1, round(width * self._zoom)),
                                          max(1, round(height * self._zoom)))
