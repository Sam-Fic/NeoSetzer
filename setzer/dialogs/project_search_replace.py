#!/usr/bin/env python3
# coding: utf-8

'''Preview-first project-wide search and replacement dialog.'''

import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from setzer.helpers.text_input_card import create_text_input_card
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
        # 宽度 720 为 §6.5 档位外特例：双栏编辑器（搜索结果 + 预览），
        # 拉宽是设计意图，不进档位。
        self.dialog.set_content_width(720)
        self.dialog.set_content_height(560)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # 自建 HeaderBar（docs/ui-guidelines.md 规则#1）：悬浮态 Adw.Dialog 会
        # 自带右上角关闭按钮，必须禁掉，仅保留手动 Cancel 单一关闭入口。
        # 规则#2：文字按钮最多 2 个（Cancel / Apply），Preview 降级为 flat
        # 图标按钮 + tooltip；Apply 是不可逆的全局替换，用 destructive-action。
        headerbar = Adw.HeaderBar()
        headerbar.set_show_end_title_buttons(False)
        headerbar.set_title_widget(Adw.WindowTitle(
            title=_('Search and Replace in Project')))
        cancel_button = Gtk.Button.new_with_mnemonic(_('_Cancel'))
        cancel_button.connect('clicked', lambda *_: self.dialog.close())
        headerbar.pack_start(cancel_button)
        self.preview_button = Gtk.Button()
        self.preview_button.set_icon_name('view-paged-symbolic')
        self.preview_button.add_css_class('flat')
        self.preview_button.set_tooltip_text(_('Preview replacements'))
        self.preview_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Preview replacements')])
        self.preview_button.connect('clicked', self._on_preview)
        headerbar.pack_end(self.preview_button)
        self.apply_button = Gtk.Button.new_with_mnemonic(_('_Apply replacements'))
        self.apply_button.add_css_class('destructive-action')
        self.apply_button.connect('clicked', self._on_apply)
        headerbar.pack_end(self.apply_button)
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
        content.append(create_text_input_card(
            preview, hexpand=True, vexpand=True, text_margin=12))

        # 应用失败要留在弹窗里改条件重试，提示必须落在弹窗自身；发到主窗口
        # 会藏在这个模态弹窗背后。用 ToastOverlay 包一层是最小改动。
        # 包裹顺序：content → toast_overlay → toolbar_view → dialog，
        # 否则 content 先挂上 toolbar_view，set_child 会因已有 parent 被拒。
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(content)
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(headerbar)
        toolbar_view.set_content(self.toast_overlay)
        self.dialog.set_child(toolbar_view)

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
        # 弹窗打开时用它的 overlay；未打开（如"先保存文档"那条）才回退主窗口，
        # 否则 toast 加到一个不在窗口里的 overlay 上什么都不会显示。
        if self.dialog.get_mapped():
            self.toast_overlay.add_toast(toast)
        elif hasattr(self.main_window, 'toast_overlay'):
            self.main_window.toast_overlay.add_toast(toast)
