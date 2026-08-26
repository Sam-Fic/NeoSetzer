#!/usr/bin/env python3
# coding: utf-8

'''GTK/Adw view for the project build configuration dialog.

Standard libadwaita building blocks throughout: ``Adw.Dialog`` (via the
shared ``DialogView`` base: HeaderBar + ToolbarView), an
``Adw.PreferencesPage`` with boxed-list ``Adw.PreferencesGroup`` groups,
``Adw.EntryRow`` / ``Adw.SwitchRow`` form rows and ``Adw.ComboRow``
selectors. Built imperatively so the controller can rebuild widgets
(profile combo, task list) at runtime. No free-form command entry exists —
only the fixed set of build settings and whitelisted task types are exposed.

The two ``Adw.ComboRow`` subclasses below exist solely to adapt the legacy
``Gtk.ComboBoxText`` convenience API (append_text/set_active/get_active_id,
'changed' signal) onto standard combo rows, keeping the presenter/controller
code unchanged while rendering native Adwaita UI.
'''

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GObject

from setzer.dialogs.helpers.dialog_viewgtk import DialogView


class TextComboRow(Adw.ComboRow):
    '''``Adw.ComboRow`` with a ``Gtk.ComboBoxText``-compatible convenience API.

    Backed by a ``Gtk.StringList``; exposes remove_all/append_text/
    set_active/get_active/get_active_text and re-emits selection changes as
    the legacy 'changed' signal.
    '''

    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self.model = Gtk.StringList()
        self.set_model(self.model)
        self.connect('notify::selected', self.on_selection_changed)

    def on_selection_changed(self, *args):
        self.emit('changed')

    def remove_all(self):
        self.model.splice(0, self.model.get_n_items(), [])

    def append_text(self, text):
        self.model.append(text)

    def set_active(self, index):
        if index is None or index < 0:
            self.set_selected(Gtk.INVALID_LIST_POSITION)
        else:
            self.set_selected(index)

    def get_active(self):
        selected = self.get_selected()
        return -1 if selected == Gtk.INVALID_LIST_POSITION else selected

    def get_active_text(self):
        index = self.get_active()
        if index < 0:
            return None
        return self.model.get_string(index)


class IdComboRow(Adw.ComboRow):
    '''``Adw.ComboRow`` over a fixed (value, label) option list.

    Exposes the legacy ``Gtk.ComboBoxText`` ID API (set_active_id /
    get_active_id) so callers keep working with semantic values instead of
    indices.
    '''

    def __init__(self, options):
        super().__init__()
        self.options = list(options)
        self.model = Gtk.StringList.new([label for _, label in self.options])
        self.set_model(self.model)

    def set_active_id(self, value):
        for index, (option_value, _) in enumerate(self.options):
            if option_value == value:
                self.set_selected(index)
                return

    def get_active_id(self):
        selected = self.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION or selected >= len(self.options):
            return None
        return self.options[selected][0]


class TextDropDown(Gtk.DropDown):
    '''``Gtk.DropDown`` with a ``Gtk.ComboBoxText``-compatible convenience API.

    The native GTK4 dropdown button (selected string + chevron) for use
    outside preference boxed-lists, where an untitled ``Adw.ComboRow``
    renders as a bare floating row. Backed by a ``Gtk.StringList``;
    exposes remove_all/append_text/set_active/get_active_text.

    Note: ``Gtk.DropDown`` auto-selects the first item once the model is
    non-empty (its internal ``GtkSingleSelection`` has autoselect enabled),
    so unlike ``Gtk.ComboBoxText`` there is always a valid selection after
    populate — the controller's trailing ``set_active(0)`` stays harmless.
    '''

    def __init__(self):
        super().__init__()
        self.model = Gtk.StringList()
        self.set_model(self.model)

    def remove_all(self):
        self.model.splice(0, self.model.get_n_items(), [])

    def append_text(self, text):
        self.model.append(text)

    def set_active(self, index):
        if index is None or index < 0:
            self.set_selected(Gtk.INVALID_LIST_POSITION)
        else:
            self.set_selected(index)

    def get_active(self):
        selected = self.get_selected()
        return -1 if selected == Gtk.INVALID_LIST_POSITION else selected

    def get_active_text(self):
        index = self.get_active()
        if index < 0:
            return None
        return self.model.get_string(index)


class ProjectBuildConfigurationView(DialogView):

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)
        self.set_title(_('Project Build Configuration'))
        self.set_content_width(680)
        self.set_content_height(560)

        self.headerbar.set_title_widget(Adw.WindowTitle(
            title=_('Project Build Configuration')))
        # 隐藏 Adw.Dialog 自动塞进 HeaderBar 两端的「窗口控制」按钮（关闭 X 等），
        # 由我们自己的 Cancel/Save 提供等价的取消/确认操作，避免重复。
        # 与 document_properties_viewgtk 同款。
        self.headerbar.set_show_start_title_buttons(False)
        self.headerbar.set_show_end_title_buttons(False)

        # ---- HeaderBar 动作：Cancel（左） / Save（右，建议操作） ----
        self.cancel_button = Gtk.Button(label=_('Cancel'))
        self.headerbar.pack_start(self.cancel_button)

        self.save_button = Gtk.Button(label=_('Save'))
        self.save_button.add_css_class('suggested-action')
        self.headerbar.pack_end(self.save_button)

        # ---- 内容：单一 PreferencesPage（自带滚动与限宽） ----
        prefs = Adw.PreferencesPage()
        prefs.set_vexpand(True)

        # ---- Profile 管理 ----
        profiles_group = Adw.PreferencesGroup()
        profiles_group.set_title(_('Build Profiles'))

        self.profile_combo = TextComboRow()
        self.profile_combo.set_title(_('Profile'))
        profiles_group.add(self.profile_combo)

        # Profile 管理按钮行：新增 / 复制 / 重命名 / 删除 / 设为激活。
        profile_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        profile_buttons.set_halign(Gtk.Align.END)
        profile_buttons.set_margin_top(6)
        self.add_button = Gtk.Button(label=_('Add profile'))
        self.duplicate_button = Gtk.Button(label=_('Duplicate profile'))
        self.rename_button = Gtk.Button(label=_('Rename'))
        self.delete_button = Gtk.Button(label=_('Delete'))
        self.delete_button.add_css_class('destructive-action')
        self.active_button = Gtk.Button(label=_('Set active'))
        self.active_button.add_css_class('suggested-action')
        for b in (self.add_button, self.duplicate_button, self.rename_button,
                  self.delete_button, self.active_button):
            profile_buttons.append(b)
        profiles_group.add(profile_buttons)

        # ---- 构建设置 ----
        settings_group = Adw.PreferencesGroup()
        settings_group.set_title(_('Build settings'))

        self.root_document_entry = Adw.EntryRow(title=_('Root document'))
        self.file_chooser_button = Gtk.Button(label=_('Browse…'))
        self.file_chooser_button.set_valign(Gtk.Align.CENTER)
        self.root_document_entry.add_suffix(self.file_chooser_button)
        settings_group.add(self.root_document_entry)

        self.output_directory_entry = Adw.EntryRow(title=_('Output directory'))
        self.folder_chooser_button = Gtk.Button(label=_('Browse…'))
        self.folder_chooser_button.set_valign(Gtk.Align.CENTER)
        self.output_directory_entry.add_suffix(self.folder_chooser_button)
        settings_group.add(self.output_directory_entry)

        self.interpreter_combo = IdComboRow((
            ('pdflatex', 'PDFLaTeX'),
            ('xelatex', 'XeLaTeX'),
            ('lualatex', 'LuaLaTeX'),
            ('tectonic', 'Tectonic'),
        ))
        self.interpreter_combo.set_title(_('Interpreter'))
        settings_group.add(self.interpreter_combo)

        self.bib_backend_combo = IdComboRow((
            ('bibtex', 'BibTeX'),
            ('biber', 'Biber'),
        ))
        self.bib_backend_combo.set_title(_('Bibliography backend'))
        settings_group.add(self.bib_backend_combo)

        self.additional_arguments_entry = Adw.EntryRow(title=_('Additional arguments'))
        self.additional_arguments_entry.set_tooltip_text('-draftmode --shell-escape …')
        settings_group.add(self.additional_arguments_entry)

        self.latexmk_switch = Adw.SwitchRow(title=_('Runs LaTeX repeatedly (latexmk)'))
        settings_group.add(self.latexmk_switch)

        self.cleanup_switch = Adw.SwitchRow(title=_('Clean up build files'))
        settings_group.add(self.cleanup_switch)

        self.shell_mode_switch = Adw.SwitchRow(title=_('Allow shell escape'))
        settings_group.add(self.shell_mode_switch)

        # ---- 任务序列 ----
        tasks_group = Adw.PreferencesGroup()
        tasks_group.set_title(_('Build tasks (in order)'))

        tasks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.tasks_list_box = Gtk.ListBox()
        self.tasks_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.tasks_list_box.add_css_class('boxed-list')
        tasks_box.append(self.tasks_list_box)

        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # 原生 GTK4 下拉按钮（Gtk.DropDown）：裸 Adw.ComboRow 在列表外
        # 只会渲染成无标题的浮动行，观感不是下拉控件。
        self.task_add_combo = TextDropDown()
        self.task_add_combo.set_hexpand(True)
        self.task_add_button = Gtk.Button(label=_('Add task'))
        self.task_add_button.add_css_class('suggested-action')
        add_box.append(self.task_add_combo)
        add_box.append(self.task_add_button)
        tasks_box.append(add_box)
        tasks_group.add(tasks_box)

        prefs.add(profiles_group)
        prefs.add(settings_group)
        prefs.add(tasks_group)
        self.topbox.append(prefs)

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
        dialog = Adw.AlertDialog(heading=_('Rename profile'))
        entry = Gtk.Entry(text=current_name)
        entry.set_activates_default(True)
        entry.connect('realize', lambda e: e.select_region(0, -1))
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('rename', _('Rename'))
        dialog.set_response_appearance('rename', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('rename')
        dialog.set_close_response('cancel')
        dialog.choose(self, None, lambda d, res: self._on_rename_response(
            d, res, entry, callback))

    def _on_rename_response(self, dialog, result, entry, callback):
        try:
            response = dialog.choose_finish(result)
        except Exception:
            return
        if response == 'rename':
            callback(entry.get_text())

    def show_confirm_delete(self, name, callback):
        dialog = Adw.AlertDialog(
            heading=_('Delete profile?'),
            body=_('The profile «{}» will be removed.').format(name))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('delete', _('Delete'))
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response('cancel')
        dialog.choose(self, None, lambda d, res: self._on_confirm_delete_response(
            d, res, callback))

    def _on_confirm_delete_response(self, dialog, result, callback):
        try:
            response = dialog.choose_finish(result)
        except Exception:
            return
        if response == 'delete':
            callback()

    def show_message(self, text):
        dialog = Adw.AlertDialog(heading=_('Notice'), body=text)
        dialog.add_response('ok', _('OK'))
        dialog.present(self)
