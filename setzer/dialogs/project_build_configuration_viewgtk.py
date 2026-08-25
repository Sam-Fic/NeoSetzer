#!/usr/bin/env python3
# coding: utf-8

'''GTK/Adw view for the project build configuration dialog.

Built imperatively so the controller can rebuild widgets (profile combo,
task list) at runtime. No free-form command entry exists — only the fixed set
of build settings and whitelisted task types are exposed.
'''

import os
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GObject


class ProjectBuildConfigurationView(Adw.Window):

    def __init__(self):
        super().__init__(modal=True, default_width=720, default_height=640)
        self.set_title(_('Project Build Configuration'))

        # ---- 头部：profile 管理 ----
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_start(18)
        header.set_margin_end(18)
        header.set_margin_top(12)
        header.set_margin_bottom(6)
        profile_label = Gtk.Label(label=_('Profile'))
        self.profile_combo = Gtk.ComboBoxText()
        self.profile_combo.set_hexpand(True)
        header.append(profile_label)
        header.append(self.profile_combo)

        self.add_button = Gtk.Button(label='+')
        self.add_button.set_tooltip_text(_('Add profile'))
        self.duplicate_button = Gtk.Button(label='⧉')
        self.duplicate_button.set_tooltip_text(_('Duplicate profile'))
        self.rename_button = Gtk.Button(label=_('Rename'))
        self.delete_button = Gtk.Button(label=_('Delete'))
        self.delete_button.add_css_class('destructive-action')
        self.active_button = Gtk.Button(label=_('Set active'))
        self.active_button.add_css_class('suggested-action')
        for b in (self.add_button, self.duplicate_button, self.rename_button,
                  self.delete_button, self.active_button):
            header.append(b)

        # ---- 设置字段 ----
        settings_group = Adw.PreferencesGroup()
        settings_group.set_title(_('Build settings'))

        root_row = Adw.ActionRow(title=_('Root document'))
        self.root_document_entry = Gtk.Entry(hexpand=True, placeholder_text=_('(main document)'))
        self.file_chooser_button = Gtk.Button(label=_('Browse…'))
        root_row.add_suffix(self.root_document_entry)
        root_row.add_suffix(self.file_chooser_button)
        root_row.set_activatable_widget(self.root_document_entry)
        settings_group.add(root_row)

        output_row = Adw.ActionRow(title=_('Output directory'))
        self.output_directory_entry = Gtk.Entry(hexpand=True, placeholder_text=_('(project directory)'))
        self.folder_chooser_button = Gtk.Button(label=_('Browse…'))
        output_row.add_suffix(self.output_directory_entry)
        output_row.add_suffix(self.folder_chooser_button)
        output_row.set_activatable_widget(self.output_directory_entry)
        settings_group.add(output_row)

        interp_row = Adw.ActionRow(title=_('Interpreter'))
        self.interpreter_combo = Gtk.ComboBoxText()
        for value, label in (('pdflatex', 'PDFLaTeX'), ('xelatex', 'XeLaTeX'),
                             ('lualatex', 'LuaLaTeX'), ('tectonic', 'Tectonic')):
            self.interpreter_combo.append(id=value, text=label)
        self.interpreter_combo.set_active_id('pdflatex')
        interp_row.add_suffix(self.interpreter_combo)
        interp_row.set_activatable_widget(self.interpreter_combo)
        settings_group.add(interp_row)

        bib_row = Adw.ActionRow(title=_('Bibliography backend'))
        self.bib_backend_combo = Gtk.ComboBoxText()
        for value, label in (('bibtex', 'BibTeX'), ('biber', 'Biber')):
            self.bib_backend_combo.append(id=value, text=label)
        self.bib_backend_combo.set_active_id('bibtex')
        bib_row.add_suffix(self.bib_backend_combo)
        bib_row.set_activatable_widget(self.bib_backend_combo)
        settings_group.add(bib_row)

        args_row = Adw.ActionRow(title=_('Additional arguments'))
        self.additional_arguments_entry = Gtk.Entry(hexpand=True,
            placeholder_text='-draftmode --shell-escape …')
        args_row.add_suffix(self.additional_arguments_entry)
        args_row.set_activatable_widget(self.additional_arguments_entry)
        settings_group.add(args_row)

        self.latexmk_switch = Gtk.Switch()
        latexmk_row = Adw.ActionRow(title=_('Runs LaTeX repeatedly (latexmk)'))
        latexmk_row.add_suffix(self.latexmk_switch)
        latexmk_row.set_activatable_widget(self.latexmk_switch)
        settings_group.add(latexmk_row)

        self.cleanup_switch = Gtk.Switch()
        cleanup_row = Adw.ActionRow(title=_('Clean up build files'))
        cleanup_row.add_suffix(self.cleanup_switch)
        cleanup_row.set_activatable_widget(self.cleanup_switch)
        settings_group.add(cleanup_row)

        self.shell_mode_switch = Gtk.Switch()
        shell_row = Adw.ActionRow(title=_('Allow shell escape'))
        shell_row.add_suffix(self.shell_mode_switch)
        shell_row.set_activatable_widget(self.shell_mode_switch)
        settings_group.add(shell_row)

        # ---- 任务序列 ----
        tasks_group = Adw.PreferencesGroup()
        tasks_group.set_title(_('Build tasks (in order)'))

        tasks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tasks_box.set_margin_start(18)
        tasks_box.set_margin_end(18)
        tasks_box.set_margin_bottom(6)
        self.tasks_list_box = Gtk.ListBox()
        self.tasks_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.tasks_list_box.add_css_class('boxed-list')
        tasks_box.append(self.tasks_list_box)

        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.task_add_combo = Gtk.ComboBoxText()
        self.task_add_combo.set_hexpand(True)
        self.task_add_button = Gtk.Button(label=_('Add task'))
        self.task_add_button.add_css_class('suggested-action')
        add_box.append(self.task_add_combo)
        add_box.append(self.task_add_button)
        tasks_box.append(add_box)
        tasks_group.add(tasks_box)

        # ---- 内容组装 ----
        prefs = Adw.PreferencesPage()
        prefs.add(settings_group)
        prefs.add(tasks_group)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(prefs)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.append(header)
        content_box.append(scrolled)

        # ---- 底部按钮 ----
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_margin_start(18)
        footer.set_margin_end(18)
        footer.set_margin_top(6)
        footer.set_margin_bottom(12)
        footer.set_halign(Gtk.Align.END)
        self.cancel_button = Gtk.Button(label=_('Cancel'))
        self.save_button = Gtk.Button(label=_('Save'))
        self.save_button.add_css_class('suggested-action')
        footer.append(self.cancel_button)
        footer.append(self.save_button)
        content_box.append(footer)

        self.set_content(content_box)

    # ---- 文件/文件夹选择（复用标准文件选择器，杜绝任意脚本） ----

    def run_file_chooser(self, callback, title, select_folder):
        dialog = Gtk.FileDialog(title=title)
        if select_folder:
            dialog.select_folder(self, None,
                lambda d, res: self._on_chooser_done(d, res, callback, True))
        else:
            dialog.open(self, None,
                lambda d, res: self._on_chooser_done(d, res, callback, False))

    def _on_chooser_done(self, dialog, result, callback, is_folder):
        try:
            if is_folder:
                file = dialog.select_folder_finish(result)
            else:
                file = dialog.open_finish(result)
        except Exception:
            return
        if file is not None:
            callback(file.get_path())

    def show_rename_dialog(self, current_name, callback):
        dialog = Adw.MessageDialog(transient_for=self,
                                   heading=_('Rename profile'))
        entry = Gtk.Entry(text=current_name)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('rename', _('Rename'))
        dialog.set_response_appearance('rename', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('rename')
        dialog.connect('response', lambda d, r: (
            callback(entry.get_text()) if r == 'rename' else None, d.close()))

    def show_confirm_delete(self, name, callback):
        dialog = Adw.MessageDialog(transient_for=self,
            heading=_('Delete profile?'),
            body=_('The profile «{}» will be removed.').format(name))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('delete', _('Delete'))
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect('response', lambda d, r: (
            callback() if r == 'delete' else None, d.close()))

    def show_message(self, text):
        dialog = Adw.MessageDialog(transient_for=self, heading=_('Notice'),
                                   body=text)
        dialog.add_response('ok', _('OK'))
        dialog.present()
