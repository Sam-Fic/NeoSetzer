#!/usr/bin/env python3
# coding: utf-8

'''Preview-first project-wide search and replacement dialog.'''

import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from setzer.project.search_replace import ProjectSearchReplace


class ProjectSearchReplaceDialog:

    def __init__(self, main_window, workspace):
        self.main_window = main_window
        self.workspace = workspace
        self.plan = None
        self.search_service = None
        self._build_view()

    def present(self, document):
        if document is None or document.get_filename() is None:
            self._show_toast(_('Save a LaTeX document before searching its project.'))
            return
        self.search_service = ProjectSearchReplace(document.get_filename())
        self.plan = None
        self.preview_buffer.set_text('')
        self.apply_button.set_sensitive(False)
        self.dialog.present(self.main_window)

    def _build_view(self):
        self.dialog = Adw.Dialog()
        self.dialog.set_title(_('Search and Replace in Project'))
        self.dialog.set_content_width(720)
        self.dialog.set_content_height(560)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        form = Adw.PreferencesGroup()
        self.search_entry = Adw.EntryRow()
        self.search_entry.set_title(_('Find'))
        self.replace_entry = Adw.EntryRow()
        self.replace_entry.set_title(_('Replace with'))
        form.add(self.search_entry)
        form.add(self.replace_entry)
        self.case_sensitive = Adw.SwitchRow()
        self.case_sensitive.set_title(_('Match case'))
        self.whole_word = Adw.SwitchRow()
        self.whole_word.set_title(_('Match whole word'))
        self.regex = Adw.SwitchRow()
        self.regex.set_title(_('Regular expression'))
        form.add(self.case_sensitive)
        form.add(self.whole_word)
        form.add(self.regex)
        content.append(form)

        self.preview_buffer = Gtk.TextBuffer()
        preview = Gtk.TextView(buffer=self.preview_buffer)
        preview.set_editable(False)
        preview.set_cursor_visible(False)
        preview.set_monospace(True)
        preview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(preview)
        content.append(scrolled)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button.new_with_mnemonic(_('_Cancel'))
        cancel.connect('clicked', lambda *_: self.dialog.close())
        preview_button = Gtk.Button.new_with_mnemonic(_('_Preview'))
        preview_button.connect('clicked', self._on_preview)
        self.apply_button = Gtk.Button.new_with_mnemonic(_('_Apply replacements'))
        self.apply_button.add_css_class('destructive-action')
        self.apply_button.connect('clicked', self._on_apply)
        buttons.append(cancel)
        buttons.append(preview_button)
        buttons.append(self.apply_button)
        content.append(buttons)
        self.dialog.set_child(content)

    def _options(self):
        return {
            'case_sensitive': self.case_sensitive.get_active(),
            'whole_word': self.whole_word.get_active(),
            'regex': self.regex.get_active(),
        }

    def _modified_open_files(self):
        blocked = []
        for document in self.workspace.open_documents:
            if (document.get_filename() is not None
                    and document.source_buffer.get_modified()):
                blocked.append(document.get_filename())
        return tuple(blocked)

    def _on_preview(self, *_args):
        query = self.search_entry.get_text()
        replacement = self.replace_entry.get_text()
        options = self._options()
        blocked = self._modified_open_files()
        self.plan = self.search_service.create_replacement_plan(
            query, replacement, blocked_files=blocked, **options)
        matches = self.search_service.search(query, **options)
        lines = [
            _('Preview: {count} replacement(s) in {files} file(s).').format(
                count=self.plan.replacement_count, files=len(self.plan.files)),
        ]
        for match in matches[:250]:
            relative = os.path.relpath(match.filename,
                                       self.search_service.project_root)
            lines.append('{path}:{line}:{column}: {preview}'.format(
                path=relative, line=match.line, column=match.column + 1,
                preview=match.preview))
        if len(matches) > 250:
            lines.append(_('Only the first 250 matches are shown.'))
        if blocked:
            lines.append('')
            lines.append(_('Replacement is disabled because these open files have unsaved changes:'))
            lines.extend('• ' + os.path.basename(filename) for filename in blocked)
        self.preview_buffer.set_text('\n'.join(lines))
        self.apply_button.set_sensitive(bool(self.plan.files) and not blocked)

    def _on_apply(self, *_args):
        if self.plan is None or not self.plan.files or self.plan.blocked_files:
            return
        confirmation = Adw.AlertDialog.new(
            _('Apply project-wide replacements?'),
            _('This writes the reviewed replacement plan to {count} file(s).').format(
                count=len(self.plan.files)))
        confirmation.add_response('cancel', _('_Cancel'))
        confirmation.add_response('apply', _('_Apply replacements'))
        confirmation.set_response_appearance('apply',
                                              Adw.ResponseAppearance.DESTRUCTIVE)
        confirmation.set_default_response('cancel')
        confirmation.choose(self.dialog, None, self._on_confirmation)

    def _on_confirmation(self, dialog, result):
        try:
            response = dialog.choose_finish(result)
        except Exception:
            return
        if response != 'apply':
            return
        try:
            changed = self.plan.apply()
        except ValueError as error:
            self._show_toast(str(error))
            self.apply_button.set_sensitive(False)
            return
        self._reload_open_unchanged_documents(changed)
        self._show_toast(_('Applied replacements to {count} file(s).').format(
            count=len(changed)))
        self.dialog.close()

    def _reload_open_unchanged_documents(self, changed):
        changed = set(changed)
        self.workspace._loading_start()
        try:
            for document in self.workspace.open_documents:
                if (document.get_filename() in changed
                        and not document.source_buffer.get_modified()):
                    document.populate_from_filename()
        finally:
            self.workspace._loading_finish()

    def _show_toast(self, message):
        toast = Adw.Toast.new(message)
        toast.set_timeout(5)
        self.main_window.toast_overlay.add_toast(toast)
