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
# along with this program at <http://www.gnu.org/licenses/>

import os
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, Gio, GLib

from setzer.dialogs.insert_image.insert_image_viewgtk import InsertImageView
from setzer.app.service_locator import ServiceLocator


class InsertImageController():
    '''处理"插入图片"对话框：从文件或剪贴板选取图片，落盘并生成 LaTeX 代码。'''

    SETTINGS_SECTION = 'app_insert_image_dialog'

    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = ServiceLocator.get_settings()
        self.view = InsertImageView(main_window)
        self.clipboard_texture = None   # 当粘贴流程传入的纹理
        self.source_file_path = None    # 从文件选择的路径
        self._connect_signals()

    def _connect_signals(self):
        self.view.cancel_button.connect('clicked', self._on_cancel)
        self.view.insert_button.connect('clicked', self._on_insert)
        self.view.save_defaults_button.connect('clicked', self._on_save_defaults)
        self.view.choose_button.connect('clicked', self._on_choose_file)
        self.view.source_row.connect('notify::selected', self._on_source_changed)
        self.view.filename_row.connect('changed', self._on_filename_changed)

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def open(self, document, texture=None):
        '''打开对话框。

        document: 当前激活文档（用于定位 images/ 与插入代码）
        texture:  若为粘贴流程，传入 Gdk.Texture（图片已就绪），
                  此时来源默认设为"剪贴板"且不可选文件。
        '''
        self.document = document
        self.source_file_path = None
        self.view.set_source_file(_('No file chosen'))
        self.view.choose_button.set_label(_('_Choose…'))
        self.view.set_preview_texture(None)
        self.view.filename_row.set_text('')
        self.view.label_row.set_text('fig:')
        self.view.caption_row.set_text(_('Caption'))
        self.view.scale_row.set_value(1.0)
        self.view.width_row.set_text('')
        # 恢复上次保存的默认值
        try:
            saved = self.settings.get_value(self.SETTINGS_SECTION, 'defaults')
        except KeyError:
            saved = None
        self.view.apply_values(saved)

        if texture is not None:
            self.clipboard_texture = texture
            self.view.source_row.set_selected(1)  # 剪贴板
            self.view.set_preview_texture(texture)
            self.view.choose_row.set_sensitive(False)
            self.view.source_row.set_sensitive(False)
            # 默认文件名 figureN
            self.view.set_filename(self._default_name())
            self.view.insert_button.set_sensitive(True)
        else:
            self.clipboard_texture = None
            self.view.source_row.set_selected(0)  # 从文件
            self.view.choose_row.set_sensitive(True)
            self.view.source_row.set_sensitive(True)
            self.view.insert_button.set_sensitive(False)

        self.view.present(self.main_window)

    # ------------------------------------------------------------------
    # 信号处理
    # ------------------------------------------------------------------
    def _on_source_changed(self, row, pspec):
        is_clip = self.view.source_is_clipboard
        self.view.choose_row.set_sensitive(not is_clip)
        if is_clip:
            self.view.set_preview_texture(self.clipboard_texture)
            self.view.choose_button.set_label(_('_Choose…'))
            if self.clipboard_texture is not None and not self.view.filename_row.get_text():
                self.view.set_filename(self._default_name())
        else:
            self.view.set_preview_texture(None)
            self.source_file_path = None
            self.view.set_source_file(_('No file chosen'))

    def _on_filename_changed(self, row):
        self.view.insert_button.set_sensitive(self.view._has_image())

    def _on_choose_file(self, button):
        dialog = Gtk.FileChooserDialog(
            title=_('Select Image'),
            transient_for=self.main_window,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(_('_Cancel'), Gtk.ResponseType.CANCEL,
                           _('_Open'), Gtk.ResponseType.ACCEPT)
        img_filter = Gtk.FileFilter()
        img_filter.set_name(_('Images'))
        for mime in ['image/png', 'image/jpeg', 'image/gif', 'image/bmp', 'image/svg+xml', 'image/webp']:
            img_filter.add_mime_type(mime)
        dialog.add_filter(img_filter)
        dialog.connect('response', self._on_file_chosen)
        dialog.present()

    def _on_file_chosen(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file is not None:
                path = file.get_path()
                self.source_file_path = path
                self.view.set_source_file(os.path.basename(path))
                # 用文件名（去扩展名）作为默认保存名
                base = os.path.splitext(os.path.basename(path))[0]
                if not self.view.filename_row.get_text():
                    self.view.set_filename(base)
                # 预览
                try:
                    texture = Gdk.Texture.new_from_file(file)
                    self.view.set_preview_texture(texture)
                except Exception:
                    self.view.set_preview_texture(None)
        dialog.destroy()

    def _on_cancel(self, button):
        self.view.close()

    def _on_insert(self, button):
        self._do_insert()

    def _on_save_defaults(self, button):
        self.settings.set_value(self.SETTINGS_SECTION, 'defaults', self.view.get_values())
        self.settings.pickle()
        # 视觉反馈：短暂改为 "Saved"
        original = self.view.save_defaults_button.get_label()
        self.view.save_defaults_button.set_label(_('Saved'))
        GLib.timeout_add(1200, self._restore_save_button_label, original)

    def _restore_save_button_label(self, label):
        self.view.save_defaults_button.set_label(label)
        return False

    # ------------------------------------------------------------------
    # 核心逻辑
    # ------------------------------------------------------------------
    def _default_name(self):
        folder = self._images_folder(create=False)
        if folder is None:
            return 'figure1'
        existing = [f for f in os.listdir(folder)] if os.path.isdir(folder) else []
        n = 1
        while f'figure{n}.png' in existing:
            n += 1
        return f'figure{n}'

    def _document_folder(self):
        folder = self.document.get_dirname()
        if folder is None:
            return None
        return folder

    def _images_folder(self, create=True):
        folder = self._document_folder()
        if folder is None:
            return None
        sub = self.view.subdir_row.get_text().strip() or 'images'
        images_dir = os.path.join(folder, sub)
        if create and not os.path.isdir(images_dir):
            os.makedirs(images_dir, exist_ok=True)
        return images_dir

    def _ensure_graphicx(self):
        # 通过 document 的 add_packages 机制确保 graphicx 已加载
        try:
            self.document.add_packages(['graphicx'])
            # 若使用 [H]，还需要 float 包
            if self._selected_placement() == 'H':
                self.document.add_packages(['float'])
        except Exception:
            pass

    def _selected_placement(self):
        selected = self.view.placement_row.get_selected()
        return InsertImageView.PLACEMENT_OPTIONS[selected][0]

    def _build_latex(self, relative_path):
        scale = self.view.scale_row.get_value()
        width = self.view.width_row.get_text().strip()
        caption = self.view.caption_row.get_text().strip()
        label = self.view.label_row.get_text().strip()

        # 构造 includegraphics 选项
        opts = []
        if width:
            opts.append(f'width={width}')
        else:
            opts.append(f'scale={scale:.2f}')
        opt_str = '[' + ','.join(opts) + ']' if opts else ''

        inner = f'\\includegraphics{opt_str}{{{relative_path}}}'

        use_figure = self.view.figure_switch.get_active()
        centered = self.view.centered_switch.get_active()

        body = inner
        if centered:
            body = '\\begin{center}\n\t' + inner + '\n\\end{center}'
        if caption:
            body += f'\n\\caption{{{caption}}}'
        if label:
            body += f'\n\\label{{{label}}}'

        if use_figure:
            placement = self._selected_placement()
            tex = f'\\begin{{figure}}[{placement}]\n\t{body}\n\\end{{figure}}'
        else:
            tex = body
        return tex

    def _do_insert(self):
        folder = self._document_folder()
        if folder is None:
            # 文档尚未保存：先要求保存
            from setzer.dialogs.dialog_locator import DialogLocator
            DialogLocator.get_dialog('save_document').run(
                self.document, self._after_save_then_insert, {'document_save_action': 'save'})
            return

        images_dir = self._images_folder(create=True)
        name = self.view.filename_row.get_text().strip()
        if not name:
            return
        name = self._sanitize(name)
        png_path = os.path.join(images_dir, name + '.png')

        # 决定纹理来源
        if self.view.source_is_clipboard:
            texture = self.clipboard_texture
            if texture is None:
                return
            texture.save_to_png(png_path)
        else:
            src = self.source_file_path
            if src is None or not os.path.isfile(src):
                return
            self._copy_file(src, png_path)

        relative_path = os.path.join(self.view.subdir_row.get_text().strip() or 'images', name + '.png')
        relative_path = relative_path.replace(os.sep, '/')

        self._ensure_graphicx()
        tex = self._build_latex(relative_path)

        # 插入到光标处
        self.document.insert_symbol_at_cursor(tex)
        self.view.close()

    def _after_save_then_insert(self, arguments):
        # 文档未保存时先保存；保存完成后剪贴板纹理已丢失，
        # 改为从文件选择图片重新打开对话框。
        self.open(self.document, texture=None)

    def _copy_file(self, src, dst):
        import shutil
        shutil.copyfile(src, dst)

    def _sanitize(self, name):
        # 清洗非法文件名字符（保留字母数字 _ -）
        keep = []
        for ch in name:
            if ch.isalnum() or ch in ('_', '-'):
                keep.append(ch)
            else:
                keep.append('_')
        return ''.join(keep)
