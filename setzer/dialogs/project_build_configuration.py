#!/usr/bin/env python3
# coding: utf-8

'''Dialog to manage per-project build configuration.

The configuration is organised as **named build profiles**. Each profile holds
a set of build settings (root document, output directory, interpreter, ...) and
an ordered list of *build tasks* (steps). One profile is *active* per project
and is used whenever any document in the project is built.

Only whitelisted, trusted build backends may appear as tasks — this dialog
never lets the user run an arbitrary command or shell script, honouring the
hard constraint that the editor must not execute arbitrary scripts.
'''

import os
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GObject

from setzer.project.build_configuration import (
    ProjectBuildConfiguration,
    task_type_label,
    ALLOWED_TASK_TYPES,
    TASK_TYPE_LATEX,
    TASK_TYPE_BIBTEX,
    TASK_TYPE_BIBER,
    TASK_TYPE_MAKEINDEX,
    TASK_TYPE_GLOSSARIES,
    DEFAULT_PROFILE_NAME,
)


class ProjectBuildConfigurationDialog():

    def __init__(self, main_window, document=None):
        self.main_window = main_window
        self.document = document if document is not None else main_window.close_popovers()
        if self.document == None: return

        configuration = ProjectBuildConfiguration.discover(self.document.filename)
        if configuration is None:
            # 文档不属于任何项目：以文档所在目录为项目根创建配置。
            configuration = ProjectBuildConfiguration(
                os.path.dirname(os.path.abspath(self.document.filename)))
        self.configuration = configuration

        profiles, active = configuration.load_profiles()
        # 深拷贝，避免编辑过程中误改磁盘缓存对象。
        self.profiles = [self._copy_profile(p) for p in profiles]
        self.active_name = active
        self.selected_name = active

        self.view = ProjectBuildConfigurationView()

        self.view.cancel_button.connect('clicked', self.on_cancel_button_clicked)
        self.view.save_button.connect('clicked', self.on_save_button_clicked)
        self.view.file_chooser_button.connect('clicked', self.on_file_chooser_button_clicked)
        self.view.folder_chooser_button.connect('clicked', self.on_folder_chooser_button_clicked)

        self._build_profile_ui()
        self._select_profile(self.selected_name)

        self.view.set_transient_for(self.main_window)
        self.view.show()

    def present(self):
        self.view.show()

    # ---- 数据工具 ---------------------------------------------------------

    @staticmethod
    def _empty_profile(name):
        return {
            'name': name,
            'root_document': None,
            'output_directory': None,
            'interpreter': None,
            'use_latexmk': True,
            'cleanup_build_files': True,
            'shell_mode': False,
            'bibliography_backend': 'bibtex',
            'additional_arguments': (),
            'tasks': [TASK_TYPE_LATEX],
        }

    @staticmethod
    def _copy_profile(profile):
        copy = dict(profile)
        copy['additional_arguments'] = tuple(profile.get('additional_arguments', ()))
        copy['tasks'] = list(profile.get('tasks', [TASK_TYPE_LATEX]))
        return copy

    def _find_profile(self, name):
        for profile in self.profiles:
            if profile['name'] == name:
                return profile
        return None

    def _selected_profile(self):
        return self._find_profile(self.selected_name)

    # ---- Profile 列表 UI --------------------------------------------------

    def _build_profile_ui(self):
        self.view.profile_combo.remove_all()
        for profile in self.profiles:
            self.view.profile_combo.append_text(profile['name'])
        self.view.profile_combo.connect('changed', self.on_profile_combo_changed)
        self.view.add_button.connect('clicked', self.on_add_profile)
        self.view.duplicate_button.connect('clicked', self.on_duplicate_profile)
        self.view.rename_button.connect('clicked', self.on_rename_profile)
        self.view.delete_button.connect('clicked', self.on_delete_profile)
        self.view.active_button.connect('clicked', self.on_set_active)

        self.view.task_add_combo.remove_all()
        for task_type in sorted(ALLOWED_TASK_TYPES):
            self.view.task_add_combo.append_text(task_type_label(task_type))
        self.view.task_add_combo.set_active(0)
        self.view.task_add_button.connect('clicked', self.on_add_task)

        self._refresh_active_button()

    def _refresh_active_button(self):
        is_active = self.selected_name == self.active_name
        self.view.active_button.set_sensitive(not is_active)
        self.view.active_button.set_label(
            _('Active') if is_active else _('Set active'))

    def _select_profile(self, name):
        # 切换前先把当前选中 profile 的 UI 改动写回，避免编辑 A 后切到 B 时
        # A 的未保存修改被无声丢弃（data-loss bug）。
        if name != self.selected_name and self._selected_profile() is not None:
            self._commit_selected_profile()
        self.selected_name = name
        index = 0
        for i, profile in enumerate(self.profiles):
            if profile['name'] == name:
                index = i
                break
        self.view.profile_combo.set_active(index)
        profile = self._selected_profile()
        if profile is None:
            return
        self.view.root_document_entry.set_text(profile.get('root_document') or '')
        self.view.output_directory_entry.set_text(profile.get('output_directory') or '')
        self.view.interpreter_combo.set_active_id(profile.get('interpreter') or 'pdflatex')
        self.view.additional_arguments_entry.set_text(
            ' '.join(profile.get('additional_arguments') or ()))
        self.view.latexmk_switch.set_active(bool(profile.get('use_latexmk', True)))
        self.view.cleanup_switch.set_active(bool(profile.get('cleanup_build_files', True)))
        self.view.shell_mode_switch.set_active(bool(profile.get('shell_mode', False)))
        self.view.bib_backend_combo.set_active_id(
            profile.get('bibliography_backend') or 'bibtex')
        self._refresh_tasks(profile)
        self._refresh_active_button()
        self._refresh_delete_sensitivity()

    def _refresh_delete_sensitivity(self):
        # 至少保留一个 profile。
        self.view.delete_button.set_sensitive(len(self.profiles) > 1)

    def _refresh_tasks(self, profile):
        list_box = self.view.tasks_list_box
        while list_box.get_first_child() is not None:
            list_box.remove(list_box.get_first_child())
        for position, task in enumerate(profile['tasks']):
            row = self._make_task_row(task, position, profile)
            list_box.append(row)

    def _make_task_row(self, task, position, profile):
        row = Adw.ActionRow()
        row.set_title(task_type_label(task))
        row.set_activatable(False)

        up = Gtk.Button(label='↑', valign=Gtk.Align.CENTER)
        up.set_tooltip_text(_('Move up'))
        up.connect('clicked', lambda b: self._move_task(profile, position, -1))
        down = Gtk.Button(label='↓', valign=Gtk.Align.CENTER)
        down.set_tooltip_text(_('Move down'))
        down.connect('clicked', lambda b: self._move_task(profile, position, 1))
        remove = Gtk.Button(label='✕', valign=Gtk.Align.CENTER)
        remove.add_css_class('destructive-action')
        remove.set_tooltip_text(_('Remove task'))
        remove.connect('clicked', lambda b: self._remove_task(profile, position))

        up.set_sensitive(position > 0)
        down.set_sensitive(position < len(profile['tasks']) - 1)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.append(up)
        box.append(down)
        box.append(remove)
        row.add_suffix(box)
        return row

    # ---- Profile 操作回调 -------------------------------------------------

    def on_profile_combo_changed(self, combo):
        text = combo.get_active_text()
        if text:
            self._select_profile(text)

    def on_add_profile(self, button):
        base = _('New profile')
        name = base
        index = 1
        existing = {p['name'] for p in self.profiles}
        while name in existing:
            index += 1
            name = f'{base} {index}'
        profile = self._empty_profile(name)
        profile['tasks'] = [TASK_TYPE_LATEX, TASK_TYPE_BIBTEX, TASK_TYPE_LATEX, TASK_TYPE_LATEX]
        self.profiles.append(profile)
        self.view.profile_combo.append_text(name)
        self._select_profile(name)
        self._refresh_delete_sensitivity()

    def on_duplicate_profile(self, button):
        source = self._selected_profile()
        if source is None:
            return
        base = source['name'] + ' ' + _('copy')
        name = base
        index = 1
        existing = {p['name'] for p in self.profiles}
        while name in existing:
            index += 1
            name = f'{base} {index}'
        copy = self._copy_profile(source)
        copy['name'] = name
        self.profiles.append(copy)
        self.view.profile_combo.append_text(name)
        self._select_profile(name)
        self._refresh_delete_sensitivity()

    def on_rename_profile(self, button):
        profile = self._selected_profile()
        if profile is None:
            return
        self.view.show_rename_dialog(profile['name'], self._do_rename)

    def _do_rename(self, new_name):
        new_name = (new_name or '').strip()
        if not new_name or new_name == self.selected_name:
            return
        existing = {p['name'] for p in self.profiles}
        if new_name in existing:
            self.view.show_message(_('A profile with this name already exists.'))
            return
        profile = self._selected_profile()
        old_name = profile['name']
        profile['name'] = new_name
        if self.active_name == old_name:
            self.active_name = new_name
        # 重建下拉列表文本。
        self.view.profile_combo.remove_all()
        for p in self.profiles:
            self.view.profile_combo.append_text(p['name'])
        self._select_profile(new_name)

    def on_delete_profile(self, button):
        if len(self.profiles) <= 1:
            return
        profile = self._selected_profile()
        if profile is None:
            return
        self.view.show_confirm_delete(profile['name'], self._do_delete)

    def _do_delete(self):
        name = self.selected_name
        self.profiles = [p for p in self.profiles if p['name'] != name]
        if self.active_name == name:
            self.active_name = self.profiles[0]['name']
        self.view.profile_combo.remove_all()
        for p in self.profiles:
            self.view.profile_combo.append_text(p['name'])
        self._select_profile(self.profiles[0]['name'])
        self._refresh_delete_sensitivity()

    def on_set_active(self, button):
        if self.selected_name != self.active_name:
            self.active_name = self.selected_name
            self._refresh_active_button()

    # ---- Task 操作回调 ----------------------------------------------------

    def on_add_task(self, button):
        profile = self._selected_profile()
        if profile is None:
            return
        label = self.view.task_add_combo.get_active_text()
        task = self._task_type_from_label(label)
        if task is None:
            return
        profile['tasks'].append(task)
        self._refresh_tasks(profile)

    def _task_type_from_label(self, label):
        for task_type in ALLOWED_TASK_TYPES:
            if task_type_label(task_type) == label:
                return task_type
        return None

    def _move_task(self, profile, position, direction):
        target = position + direction
        if 0 <= target < len(profile['tasks']):
            profile['tasks'][position], profile['tasks'][target] = (
                profile['tasks'][target], profile['tasks'][position])
            self._refresh_tasks(profile)

    def _remove_task(self, profile, position):
        if 0 <= position < len(profile['tasks']):
            profile['tasks'].pop(position)
            self._refresh_tasks(profile)

    # ---- 字段回调 ---------------------------------------------------------

    def on_file_chooser_button_clicked(self, button):
        def callback(filename):
            if filename != None:
                self.view.root_document_entry.set_text(filename)
        self.view.run_file_chooser(callback, _('Select root document'), False)

    def on_folder_chooser_button_clicked(self, button):
        def callback(foldername):
            if foldername != None:
                self.view.output_directory_entry.set_text(foldername)
        self.view.run_file_chooser(callback, _('Select output directory'), True)

    # ---- 保存 / 取消 ------------------------------------------------------

    def on_save_button_clicked(self, button):
        # 先把当前 UI 值写入选中 profile。
        self._commit_selected_profile()
        self.configuration.save_profiles(self.profiles, self.active_name)
        # 通知构建系统：生效 profile 可能已变更，刷新 tooltip 等。
        self.document.build_system.add_change_code('project_profile_changed')
        self.view.close()

    def on_cancel_button_clicked(self, button):
        self.view.close()

    def _commit_selected_profile(self):
        profile = self._selected_profile()
        if profile is None:
            return
        profile['root_document'] = self.view.root_document_entry.get_text().strip() or None
        profile['output_directory'] = self.view.output_directory_entry.get_text().strip() or None
        profile['interpreter'] = self.view.interpreter_combo.get_active_id()
        profile['use_latexmk'] = self.view.latexmk_switch.get_active()
        profile['cleanup_build_files'] = self.view.cleanup_switch.get_active()
        profile['shell_mode'] = self.view.shell_mode_switch.get_active()
        profile['bibliography_backend'] = self.view.bib_backend_combo.get_active_id()
        args_text = self.view.additional_arguments_entry.get_text().strip()
        profile['additional_arguments'] = tuple(args_text.split()) if args_text else ()


# 延迟导入以避免循环依赖（view 引用 controller 仅在回调中）。
from setzer.dialogs.project_build_configuration_viewgtk import (
    ProjectBuildConfigurationView)
