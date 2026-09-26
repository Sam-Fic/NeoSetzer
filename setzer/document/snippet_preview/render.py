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

'''snippet PDF → 贴图渲染（公式 hover 预览与 TikZ 侧栏预览共用）。

贴图渲染：Poppler 整页渲染到 cairo ARGB32 surface（密度 RENDER_DENSITY
px/pt），numpy 按背景色裁掉页边白（ink bbox），经 GdkPixbuf 转为
Gdk.Texture。Pixbuf 可在工作线程构建，Texture 必须延迟到主线程创建
（GDK 对象非线程安全）。
'''

import cairo
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Poppler', '0.18')
from gi.repository import Gdk, GLib, Poppler

import numpy

# 渲染密度（像素/pt）：2x 保证 hidpi 下清晰，A4 页约 1190×1684 px。
RENDER_DENSITY = 2.0

# 裁剪后四周保留的白边（渲染像素）。
CROP_PADDING_PX = 8


def render_pdf_to_pixbuf(pdf_path, density=RENDER_DENSITY, padding_px=CROP_PADDING_PX):
    '''工作线程：单页 snippet PDF → 裁剪后的 GdkPixbuf（RGB，无 alpha）。

    只渲染第 1 页：snippet 包装文档刻意不带分页，多页只可能来自用户图比
    纸面还大的溢出情形，此时旧行为（math hover）就是取首页。
    '''
    pdf_document = Poppler.Document.new_from_file(GLib.filename_to_uri(pdf_path))
    if pdf_document.get_n_pages() < 1:
        return None
    page = pdf_document.get_page(0)
    page_width, page_height = page.get_size()
    surface_width = max(1, int(page_width * density))
    surface_height = max(1, int(page_height * density))
    surface = cairo.ImageSurface(cairo.Format.ARGB32, surface_width, surface_height)
    context = cairo.Context(surface)
    context.set_source_rgb(1, 1, 1)
    context.paint()
    context.scale(density, density)
    page.render(context)
    return crop_surface_to_pixbuf(
        surface, surface_width, surface_height, padding_px=padding_px)


def crop_surface_to_pixbuf(surface, surface_width, surface_height,
                           padding_px=CROP_PADDING_PX):
    '''按背景色裁掉页边白，返回裁剪区 GdkPixbuf；全白页返回 None。

    cairo ARGB32 在小端机器上字节序为 BGRA（premultiplied）；只需
    「与角落背景色不同」的判断，四通道一起比较即可，不解释语义。
    '''
    if surface.get_format() != cairo.Format.ARGB32:
        return None
    # stride 以字节计（行尾可能含对齐 padding）：每行 stride//4 个像素，
    # 有效的只有前 surface_width 个，padding 列不参与背景比对。
    stride = surface.get_stride()
    raw = numpy.frombuffer(surface.get_data(), dtype=numpy.uint8)
    raw = raw[:surface_height * stride].reshape(surface_height, stride // 4, 4)
    background = raw[0, 0].copy()
    ink = numpy.any(raw[:, :surface_width, :] != background, axis=2)
    rows = numpy.flatnonzero(ink.any(axis=1))
    cols = numpy.flatnonzero(ink.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    top = max(0, int(rows[0]) - padding_px)
    bottom = min(surface_height, int(rows[-1]) + 1 + padding_px)
    left = max(0, int(cols[0]) - padding_px)
    right = min(surface_width, int(cols[-1]) + 1 + padding_px)
    return Gdk.pixbuf_get_from_surface(
        surface, left, top, right - left, bottom - top)


def texture_from_pixbuf(pixbuf):
    '''主线程：GdkPixbuf → Gdk.Texture；失败返回 None。

    GDK 纹理只能在主线程创建，所以工作线程交付 Pixbuf、主线程交付
    Texture（见 snippet_engine 的交付路径）。
    '''
    if pixbuf is None:
        return None
    try:
        return Gdk.Texture.new_for_pixbuf(pixbuf)
    except Exception:
        return None
