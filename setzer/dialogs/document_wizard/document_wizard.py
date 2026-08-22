#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
from gi.repository import Gtk, Gio, Gdk, Adw, GLib

import copy

import setzer.dialogs.document_wizard.document_wizard_viewgtk as view
from setzer.dialogs.document_wizard.pages.page_document_class import DocumentClassPage
from setzer.dialogs.document_wizard.pages.page_standard_settings import StandardSettingsPage
from setzer.dialogs.document_wizard.pages.page_letter_settings import LetterSettingsPage
from setzer.dialogs.document_wizard.pages.page_beamer_settings import BeamerSettingsPage
from setzer.dialogs.document_wizard.pages.page_general_settings import GeneralSettingsPage
from setzer.dialogs.document_wizard import page_map
from setzer.app.service_locator import ServiceLocator
from setzer.app.latex_db import LaTeXDB
from setzer.dialogs.document_wizard.user_document_templates import (
    TemplateStoreError,
    UserDocumentTemplateStore,
)
from setzer.dialogs.document_wizard.wizard_state import (
    build_default_wizard_state,
    normalise_wizard_state,
)

# KOMA-Script 文档类复用对应标准类的设置页与 current_values 键
# （scrartcl/article、scrreprt/report、scrbook/book、scrlttr2/letter）。键用于查设置，
# 实际类名（用于生成 \documentclass{...}）仍是 KOMA 名。
KOMA_CLASS_TO_STANDARD = {
    'scrartcl': 'article',
    'scrreprt': 'report',
    'scrbook': 'book',
    'scrlttr2': 'letter',
}

# pickle 仅用于 load_presets 中兼容旧 settings 数据（迁移期：旧版用
# pickle.dumps(current_values) 存为 bytes）。settings.json 迁移完成后
# presets 字段是 dict，此处 isinstance(bytes) 分支不再触发。后续可移除。
import pickle
import os


# on_keypress 每次按键都跑，模块级预计算避免每次 C 查表。
_KEYVAL_RETURN = Gdk.keyval_from_name('Return')
_KEYVAL_ESCAPE = Gdk.keyval_from_name('Escape')
_KEYVAL_LEFT = Gdk.keyval_from_name('Left')
_KEYVAL_RIGHT = Gdk.keyval_from_name('Right')


class DocumentWizard(object):

    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = ServiceLocator.get_settings()
        self.current_values = dict()
        self.page_formats = {'US Letter': 'letterpaper', 'US Legal': 'legalpaper', 'A4': 'a4paper', 'A5': 'a5paper', 'B5': 'b5paper'}

    def run(self, document):
        self.document = document
        self.completed = False
        self.selected_document_template_id = None

        self.init_current_values()
        # 先读预设（load_presets 现只读 settings → self.presets，不再遍历页面），
        # 再 setup：setup → _ensure_page_built(0) 会用 self.presets 初始化首页控件。
        # 顺序倒置后，第 0 页在 setup 内即应用预设，无需 run 末尾再补一遍。
        self.presets = None
        self.load_presets()
        self.setup()
        self.view.save_document_template_button.set_visible(
            document.is_latex_document())

        self.current_page = -1
        self.goto_page(page_map.DOCUMENT_CLASS_PAGE_INDEX)

        self.view.present(self.main_window)

    def on_cancel_button_clicked(self, button):
        self.view.close()

    def on_create_button_clicked(self, button):
        # 创建前校验（如通用设置页的空标题），不合法则提示并中止。
        ok, message = self._validate_current_page()
        if not ok:
            self._show_validation_error(message)
            return
        self.save_presets()

        if self.selected_document_template_id is not None:
            try:
                template_start = self.get_document_template_store().load(
                    self.selected_document_template_id)
            except TemplateStoreError as error:
                self._show_validation_error(str(error))
                return
            template_end = ''
        else:
            document_class = self.current_values['document_class']
            # 用 getattr 替代 eval:既避免任意代码执行风险(若 presets 被篡改,
            # document_class 可能是任意字符串),也使方法不存在时抛出更清晰的
            # AttributeError 而非 SyntaxError/NameError。
            try:
                get_insert_text = getattr(self, 'get_insert_text_' + document_class)
            except AttributeError:
                return
            template_start, template_end = get_insert_text()
        self.insert_template(template_start, template_end)
        self.completed = True

        self.view.close()

    def _build_default_wizard_state(self):
        """Return a complete schema-versioned state for this wizard session."""
        return build_default_wizard_state(
            self._default_page_format(), LaTeXDB.get_languages_dict())

    def init_current_values(self):
        self.current_values = self._build_default_wizard_state()

    def _normalise_current_values(self):
        """Fill missing historical fields without changing valid user choices."""
        self.current_values = normalise_wizard_state(
            self.current_values, self._build_default_wizard_state())
        return self.current_values

    def _normalise_letter_settings(self):
        """Compatibility wrapper for direct Letter template generation.

        Template generators can be called by tests or extensions without a full
        wizard run. Normalising the complete state keeps those callers as safe
        as restored presets while preserving their valid values.
        """
        return self._normalise_current_values()['letter']

    def _default_page_format(self):
        '''按 locale 选默认纸张：美加墨用 US Letter，其余用 A4（报告 #13）。'''
        import locale
        try:
            loc = locale.getlocale(locale.LC_CTYPE)
            territory = (loc[0] or '').split('_')[-1].upper() if loc and loc[0] else ''
        except Exception:
            territory = ''
        return 'US Letter' if territory in ('US', 'CA', 'MX') else 'A4'

    def setup(self):
        self.view = view.DocumentWizardView(self.main_window)

        # 懒构造：首屏只建第 0 页（文档类选择页）。3 个文档类设置页
        # （standard/letter/beamer）每次会话只访问其中 1 个（由 document_class
        # 决定），其余必然白建；General 页也只在最后才访问。启动时只付 1 页的
        # 代价，其余页在 goto_page 首次进入时由 _ensure_page_built 按需构造。
        self._page_factories = [
            lambda: DocumentClassPage(self.current_values),
            lambda: StandardSettingsPage(self.current_values),
            lambda: LetterSettingsPage(self.current_values),
            lambda: BeamerSettingsPage(self.current_values),
            lambda: GeneralSettingsPage(self.current_values),
        ]
        self.pages = [None] * len(self._page_factories)
        self._ensure_page_built(page_map.DOCUMENT_CLASS_PAGE_INDEX)

        self.view.cancel_button.connect('clicked', self.on_cancel_button_clicked)
        self.view.create_button.connect('clicked', self.on_create_button_clicked)
        self.view.save_template_button.connect('clicked', self.open_save_template_dialog)
        self.view.save_document_template_button.connect(
            'clicked', self.open_save_document_template_dialog)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self.on_keypress)
        self.view.add_controller(key_controller)
        self.view.next_button.connect('clicked', self.goto_page_next)
        self.view.back_button.connect('clicked', self.goto_page_prev)

    def _ensure_page_built(self, page_number, apply_presets=True):
        '''首次访问某页时按需构造，并加入 page_stack。

        顺序仿原 setup()：先 add_child，再 observe_view（连信号），再 load_presets
        （set_* 会触发信号回写 current_values）。懒构造下该调用发生在首次
        goto_page 进入该页时，而非启动时。

        apply_presets=False 仅用于 apply_template 的强制建页路径：跳过用
        self.presets 初始化，避免保存的预设经信号回写污染 current_values
        （随后由 apply_template 统一用模板值刷新）。其余路径保持 True，确保
        控件与默认值/预设同步——即使 self.presets 为 None 也要调 load_presets：
        各页 load_presets 对 None 走 TypeError 回退到 current_values 默认值，
        负责把控件初始化到默认状态（如 page_format_combo 默认 index 0='US Letter'，
        而 current_values 按 locale 可能是 'A4'；General 页语言下拉、包开关、
        Beamer 主题选择都依赖此调用初始化）。
        '''
        if self.pages[page_number] is not None:
            return
        page = self._page_factories[page_number]()
        self.view.page_stack.add_child(page.view)
        page.controller = self
        page.observe_view()
        if apply_presets:
            page.load_presets(self.presets)
        self.pages[page_number] = page

    def load_presets(self):
        if self.presets == None:
            presets = self.settings.get_value('app_document_wizard', 'presets')
            # 迁移期兼容：旧版 settings 中 presets 是 pickle.dumps(current_values)
            # 的 bytes；settings.json 迁移完成后 _migrate_presets_bytes 已把它
            # 解为 dict，此处 isinstance(bytes) 分支不再触发。
            # 保留 bytes 解包以兼容：用户从旧版直接升级且 settings.pickle
            # 损坏导致迁移跳过时，presets 仍是 bytes。
            if isinstance(presets, (bytes, bytearray)):
                try:
                    presets = pickle.loads(presets)
                except (pickle.UnpicklingError, EOFError, ValueError,
                        AttributeError, TypeError):
                    presets = None
            # 历史预设可缺少后来加入的嵌套字段。正常化只补齐合法默认值，
            # 保留已保存的用户选择，并避免页面/生成器直接索引缺失键。
            self.presets = (
                normalise_wizard_state(presets, self._build_default_wizard_state())
                if isinstance(presets, dict) else None)

        # 不再在此遍历 self.pages 调用 page.load_presets：懒构造下每页在
        # _ensure_page_built 首次进入时各自应用 self.presets。此处若遍历，
        # 一则在 run() 中 setup() 之前访问尚未存在的 self.pages（首跑
        # AttributeError），二则二次开向导时会访问上一轮已销毁的旧页面。
        # apply_template 刷新页面走 page.load_presets(self.current_values)。

    def save_presets(self):
        # 直接存 dict（JSON 兼容），不再 pickle.dumps。
        # settings.set_value → settings.json 持久化时自动 JSON 序列化。
        self.settings.set_value('app_document_wizard', 'presets', self.current_values)

    # ---- 命名模板 / 模板库（报告 #5） ---------------------------------
    def get_templates(self):
        templates = self.settings.get_value('app_document_wizard', 'templates')
        return templates if isinstance(templates, dict) else dict()

    def save_template(self, name):
        name = (name or '').strip()
        if not name:
            return False
        templates = self.get_templates()
        templates[name] = copy.deepcopy(self.current_values)
        self.settings.set_value('app_document_wizard', 'templates', templates)
        return True

    def apply_template(self, name):
        blob = self.get_templates().get(name)
        if not isinstance(blob, dict):
            return False
        # 历史命名预设是用户数据。深拷贝后按当前 schema 正常化，
        # 只补齐缺失或无效值，避免新字段使旧预设失效。
        self.current_values = normalise_wizard_state(
            copy.deepcopy(blob), self._build_default_wizard_state())
        # 强制建完所有页，但 apply_presets=False：避免 _ensure_page_built 内的
        # load_presets(self.presets) 用「保存的预设」经 set_* 信号回写污染
        # current_values（模板值会被预设值覆盖）。随后统一用 current_values
        # （=模板）刷新所有页控件。apply_template 由 DocumentClassPage 的模板
        # 下拉触发，会随后 goto_page(GENERAL)，故 General 页也会在此一并建好。
        for i in range(len(self._page_factories)):
            self._ensure_page_built(i, apply_presets=False)
        for page in self.pages:
            page.load_presets(self.current_values)
        return True

    # ---- 用户 LaTeX 源模板（上游 #205） -------------------------------
    def get_document_template_store(self):
        '''Return the private XDG data store for immutable source snapshots.'''
        return UserDocumentTemplateStore(os.path.join(
            GLib.get_user_data_dir(), 'org.cvfosammmm.Setzer'))

    def get_document_templates(self):
        try:
            return self.get_document_template_store().list_templates()
        except TemplateStoreError:
            return []

    def select_document_template(self, identifier):
        if identifier is None:
            self.selected_document_template_id = None
            return True
        try:
            self.get_document_template_store().load(identifier)
        except TemplateStoreError:
            return False
        self.selected_document_template_id = identifier
        return True

    def get_selected_document_template_preview(self):
        '''Return the immutable source snapshot for a read-only wizard preview.'''
        if self.selected_document_template_id is None:
            return None
        try:
            return self.get_document_template_store().load(
                self.selected_document_template_id)
        except TemplateStoreError:
            return None

    def delete_document_template(self, identifier):
        try:
            deleted = self.get_document_template_store().delete(identifier)
        except TemplateStoreError:
            return False
        if deleted and self.selected_document_template_id == identifier:
            self.selected_document_template_id = None
        return deleted

    def open_save_document_template_dialog(self, button=None):
        source = self.document.get_all_text()
        dialog = Adw.AlertDialog(
            heading=_('Save document template'),
            body=_('Save a snapshot of the current LaTeX source for future documents.'))
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('save', _('Save'))
        dialog.set_default_response('save')
        dialog.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response('cancel')

        def on_response(dialog, response):
            if response != 'save':
                return
            try:
                self.get_document_template_store().save(entry.get_text(), source)
            except TemplateStoreError as error:
                self._show_validation_error(str(error))
                return
            document_class_page = self.pages[page_map.DOCUMENT_CLASS_PAGE_INDEX]
            document_class_page.refresh_document_templates()

        dialog.connect('response', on_response)
        dialog.present(self.main_window)

    def open_save_template_dialog(self):
        '''弹出对话框，将当前设置另存为命名模板（报告 #5）。'''
        dialog = Adw.AlertDialog(
            heading=_('Save as template'),
            body=_('Enter a name for the new template.'))
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('save', _('Save'))
        dialog.set_default_response('save')
        dialog.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response('cancel')

        def on_response(dialog, response):
            if response == 'save':
                self.save_template(entry.get_text())
        dialog.choose(self.main_window, None, on_response)

    def goto_page_next(self, button=None, data=None):
        # 页面流转集中在 page_map.next_page：避免本方法散落 0/1-5/6 魔法数字，
        # 新增/删除页面只改 page_map.py 一处。返回 None 表示无下一页
        # （如 document_class 非法时留在原页），与原 if/elif 链行为一致。
        # 前进前先校验当前页，避免基于非法输入（如空标题）继续。
        ok, message = self._validate_current_page()
        if not ok:
            self._show_validation_error(message)
            return
        next_idx = page_map.next_page(self.current_page, self.current_values['document_class'])
        if next_idx is not None:
            self.goto_page(next_idx)

    def goto_page_prev(self, button=None, data=None):
        prev_idx = page_map.prev_page(self.current_page, self.current_values['document_class'])
        if prev_idx is not None:
            self.goto_page(prev_idx)

    def _validate_current_page(self):
        '''进入下一步 / 创建前校验当前页。

        返回 (ok, message)：ok 为 False 时调用方应阻止前进并提示用户。
        边距等数值范围已由对应 SpinRow 的下限/上限约束（如 0–5cm），
        因此这里只校验语义上仍可能非法的输入（当前为通用设置页的空标题）。
        新增页面级校验只需在此按 page_index 扩展。
        '''
        if self.current_page == page_map.GENERAL_PAGE_INDEX:
            title = self.current_values.get('title', '').strip()
            if not title:
                return (False, _('Please enter a document title before creating the '
                                  'document. Otherwise the generated \\title{} will be empty.'))
        return (True, '')

    def _show_validation_error(self, message):
        dialog = Adw.AlertDialog(
            heading=_('Cannot continue'),
            body=message)
        dialog.add_response('ok', _('OK'))
        dialog.set_default_response('ok')
        dialog.set_close_response('ok')
        dialog.choose(self.main_window, None, None)

    def goto_page(self, page_number):
        # 懒构造：进入页面前确保该页已建（首屏只建了第 0 页）。已建则 no-op。
        self._ensure_page_built(page_number)
        if self.current_page != page_number:
            self.current_page = page_number
            self.view.page_stack.set_visible_child(self.pages[page_number].view)
            self.view.subtitle_label.set_text(self.pages[page_number].view.headerbar_subtitle)

            self.pages[page_number].on_activation()

            # 进入通用设置页时刷新 \\documentclass 选项预览（报告 #3）。
            if page_number == page_map.GENERAL_PAGE_INDEX:
                self.pages[page_number].view.preview_label.set_text(
                    self.get_documentclass_preview())

            # 按钮可见性也走 page_map 谓词，避免 0 / 6 硬编码：
            #   back  : 非首页（!= DOCUMENT_CLASS_PAGE_INDEX）
            #   next  : 未到 GeneralSettings（< GENERAL_PAGE_INDEX）
            #   create: 已到 GeneralSettings（>= GENERAL_PAGE_INDEX）
            self.view.back_button.set_visible(page_number != page_map.DOCUMENT_CLASS_PAGE_INDEX)
            self.view.next_button.set_visible(page_map.is_before_general(page_number))
            self.view.create_button.set_visible(page_map.is_at_or_after_general(page_number))

    def on_keypress(self, controller, keyval, keycode, state, data=None):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        # Esc 取消对话框（与标题栏 Cancel 按钮等价）。
        if keyval == _KEYVAL_ESCAPE:
            self.on_cancel_button_clicked(self.view.cancel_button)
            return True

        if keyval == _KEYVAL_RETURN:
            if state & modifiers == 0:
                # 回车在 GeneralSettings 之前 → 前进；在/之后 → 创建。
                # 用谓词替代 ``current_page in range(0, 6)`` / ``== 6``。
                if page_map.is_before_general(self.current_page):
                    self.goto_page_next()
                    return True
                elif page_map.is_at_or_after_general(self.current_page):
                    self.on_create_button_clicked(self.view.create_button)
                    return True
            return False

        # Alt+Left / Alt+Right 在步骤间前后跳转（受 page_map 守卫约束）。
        if state & modifiers == Gdk.ModifierType.ALT_MASK:
            if keyval == _KEYVAL_LEFT:
                self.goto_page_prev()
                return True
            elif keyval == _KEYVAL_RIGHT:
                self.goto_page_next()
                return True
        return False

    '''
    *** templates
    '''
    
    # ---- template helpers (Problem 2: 提取公共逻辑，消除 90% 重复) ----

    def _get_font_package_line(self):
        '''Problem 5: 根据用户选择返回字体包 \\usepackage 行。
        lmodern（默认，pdfLaTeX）、fontspec（XeLaTeX/LuaLaTeX）、none。'''
        choice = self.current_values.get('font_package', 'lmodern')
        if choice == 'lmodern':
            return '\\usepackage{lmodern}\n'
        elif choice == 'fontspec':
            return '\\usepackage{fontspec}\n'
        return ''

    def _get_preamble_packages(self):
        '''生成 fontenc + inputenc + babel + 字体包 + 其他包的公共 preamble。
        被 5 个 get_insert_text_* 方法共享。返回值以最后一个包的 \\n 结尾。'''
        return (
            '\\usepackage[T1]{fontenc}\n'
            '\\usepackage[utf8]{inputenc}\n'
            '\\usepackage[' + next(iter(self.current_values['languages'])) + ']{babel}\n'
            + self._get_font_package_line()
            + self.get_insert_packages()
        )

    def _get_geometry_line(self, doc_class):
        '''生成 geometry 包行（非默认边距时），否则返回空字符串。
        注意：返回值无尾部 \\n——与原实现一致，geometry 与 fontenc 在同一行。'''
        s = self.current_values[doc_class]
        if s['option_default_margins']:
            return ''
        return ('\\usepackage[top={}cm, bottom={}cm, left={}cm, right={}cm]{{geometry}}'
                .format(s['margin_top'], s['margin_bottom'], s['margin_left'], s['margin_right']))

    def _build_class_options(self, settings_key):
        '''构造 \\documentclass[...] 的 options 字符串（不含花括号与类名）。
        settings_key 是 current_values 中的键（KOMA 类复用对应标准类键）。'''
        if settings_key == 'letter':
            s = self._normalise_letter_settings()
        else:
            s = self.current_values[settings_key]
        size = s['font_size']
        # 非标准字号（非 10/11/12）需 extsizes 包；documentclass 用 10pt 作基。
        base = size if size in (10, 11, 12) else 10
        return (
            self.page_formats[s['page_format']] + ',' + str(base) + 'pt'
            + (',twocolumn' if s['option_twocolumn'] else '')
            + (',landscape' if s['is_landscape'] else '')
        )

    def _get_extsizes_line(self, settings_key):
        '''非标准字号时插入 extsizes 包（报告 #1 要求），否则返回空字符串。'''
        size = self.current_values[settings_key]['font_size']
        if size not in (10, 11, 12):
            return '\\usepackage[' + str(size) + 'pt]{extsizes}\n'
        return ''

    def _get_standard_document(self, settings_key, class_name=None):
        '''article / report / book 及对应 KOMA 类共享模板。
        settings_key 用于查 current_values；class_name 为实际输出类名
        （KOMA 类如 scrartcl 与标准类 article 共享设置但类名不同）。
        章节层级由 current_values['sectioning'] 控制。'''
        class_name = class_name or settings_key
        sectioning = self.current_values.get('sectioning', 'section')
        options = self._build_class_options(settings_key)
        preamble = (
            '\\documentclass[' + options + ']{' + class_name + '}\n'
            + self._get_extsizes_line(settings_key)
            + self._get_geometry_line(settings_key)
            + self._get_preamble_packages()
        )
        body = (
            '\n\\title{' + self.current_values['title'] + '}\n'
            '\\author{' + self.current_values['author'] + '}\n'
            '\\date{' + self.current_values['date'] + '}\n\n'
            '\\begin{document}\n\n'
            '\\maketitle\n'
            '\\tableofcontents\n\n'
        )
        # 章节层级：section 带 abstract；chapter 和 none 不带
        if sectioning == 'section':
            body += '\\begin{abstract}\n\\end{abstract}\n\n'
        if sectioning != 'none':
            body += '\\' + sectioning + '{}\n\n'
        return (preamble + body, '\n\n\\end{document}')

    # ---- templates ----

    def get_insert_text_article(self):
        return self._get_standard_document('article')

    def get_insert_text_report(self):
        return self._get_standard_document('report')

    def get_insert_text_book(self):
        return self._get_standard_document('book')

    # KOMA-Script 类：复用 article/report/book 的设置，但输出类名不同（#4）。
    def get_insert_text_scrartcl(self):
        return self._get_standard_document('article', class_name='scrartcl')

    def get_insert_text_scrreprt(self):
        return self._get_standard_document('report', class_name='scrreprt')

    def get_insert_text_scrbook(self):
        return self._get_standard_document('book', class_name='scrbook')

    def get_insert_text_letter(self):
        return self._get_insert_text_letter('letter')

    def get_insert_text_scrlttr2(self):
        return self._get_insert_text_letter('scrlttr2')

    def _get_insert_text_letter(self, class_name):
        letter = self._normalise_letter_settings()
        options = self._build_class_options('letter')
        sender_name = letter.get('sender_name', '') or _('Your name')
        sender_address = letter.get('sender_address', '') or _('Your address')
        sender_phone = letter.get('sender_phone', '') or _('Your phone number')
        recipient_name = letter.get('recipient_name', '') or _('Destination')
        recipient_address = letter.get('recipient_address', '') or _('Address of the destination')
        recipient_phone = letter.get('recipient_phone', '') or _('Phone number of the destination')
        signature = letter.get('signature', '') or self.current_values['author'] or _('Your name')
        opening = letter.get('opening', '') or _('Dear addressee,')
        closing = letter.get('closing', '') or _('Yours sincerely,')

        # 统一把多行地址转换为 LaTeX 的显式换行；用户可在向导的地址字段中
        # 使用换行，生成的地址块仍会同时适用于标准 letter 与 scrlttr2。
        def latex_lines(value):
            return value.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\\\')

        recipient_lines = [recipient_name]
        if recipient_address:
            recipient_lines.append(recipient_address)
        if recipient_phone:
            recipient_lines.append(recipient_phone)
        recipient_arg = '\\\\'.join(latex_lines(item) for item in recipient_lines)

        preamble = (
            '\\documentclass[' + options + ']{' + class_name + '}\n'
            + self._get_extsizes_line('letter')
            + self._get_geometry_line('letter')
            + self._get_preamble_packages()
        )

        if class_name == 'scrlttr2':
            # #170: scrlttr2 是向导中的高级信件选项。以下 KOMA 选项使寄件人
            # 与收件人地址块适配带窗口的 DL 信封，并加入折痕标记；寄件人信息
            # 使用 KOMA 变量而不是旧的 letter 宏，从而可由 scrlttr2 正确排版。
            koma_options = (
                '\\KOMAoptions{\n'
                '\tfromalign=left,\n'
                '\tfromrule=afteraddress,\n'
                '\tfromphone=true,\n'
                + '\taddrfield=' + ('true' if letter['option_window_address'] else 'false') + ',\n'
                + '\tfoldmarks=' + ('true' if letter['option_foldmarks'] else 'false') + ',\n'
                + '\tbackaddress=' + ('true' if letter['option_backaddress'] else 'false') + '\n'
                + '}\n\n'
            )
            subject_line = ('\\setkomavar{subject}{' + self.current_values['title'] + '}\n'
                            if self.current_values['title'] else '')
            body = (
                '\n' + koma_options
                + '\\setkomavar{fromname}{' + latex_lines(sender_name) + '}\n'
                + '\\setkomavar{fromaddress}{' + latex_lines(sender_address) + '}\n'
                + '\\setkomavar{fromphone}{' + latex_lines(sender_phone) + '}\n'
                + '\\setkomavar{signature}{' + latex_lines(signature) + '}\n'
                + '\\setkomavar{date}{' + self.current_values['date'] + '}\n'
                + subject_line
                + '\\begin{document}\n\n'
                + '\\begin{letter}{' + recipient_arg + '}\n\n'
                + '\\opening{' + opening + '}\n\n'
            )
            end = (
                '\n\n\\closing{' + closing + '}\n\n'
                '%\\cc{' + _('Other destination') + '}\n'
                '%\\ps{' + _('PS: PostScriptum') + '}\n'
                '%\\encl{' + _('Enclosures') + '}\n\n'
                '\\end{letter}\n'
                '\\end{document}'
            )
            return (preamble + body, end)

        # 标准 letter 类的传统命令保留原有行为。
        title_line = ('\\\\~\\\\\\textbf{' + self.current_values['title'] + '}'
                      if len(self.current_values['title']) > 0 else '')
        body = (
            '\n\\address{' + latex_lines(sender_name) + '\\\\' + latex_lines(sender_address) + '\\\\' + latex_lines(sender_phone) + '}\n'
            '\\date{' + self.current_values['date'] + '}\n'
            '\\signature{' + latex_lines(signature) + '}\n\n'
            '\\begin{document}\n\n'
            '\\begin{letter}{' + recipient_arg + title_line + '}\n\n'
            '\\opening{' + opening + '}\n\n'
        )
        end = (
            '\n\n\\closing{' + closing + '}\n\n'
            '%\\cc{' + _('Other destination') + '}\n'
            '%\\ps{' + _('PS: PostScriptum') + '}\n'
            '%\\encl{' + _('Enclosures') + '}\n\n'
            '\\end{letter}\n'
            '\\end{document}'
        )
        return (preamble + body, end)

    def get_insert_text_beamer(self):
        theme = self.current_values['beamer']['theme']
        top_align = '[t]' if self.current_values['beamer']['option_top_align'] else ''
        show_navigation = '\n\n\\beamertemplatenavigationsymbolsempty' if not self.current_values['beamer']['option_show_navigation'] else ''

        preamble = (
            '\\documentclass' + top_align + '{beamer}\n'
            + self._get_preamble_packages()
        )
        body = (
            '\\usetheme{' + theme + '}' + show_navigation + '\n\n'
            '\\title{' + self.current_values['title'] + '}\n'
            '\\author{' + self.current_values['author'] + '}\n'
            '\\date{' + self.current_values['date'] + '}\n\n'
            '\\begin{document}\n\n'
            '\\begin{frame}\n'
            '\t\\titlepage\n'
            '\\end{frame}\n\n'
        )
        return (preamble + body, '\n\n\\end{document}')

    def get_insert_packages(self):
        text = ''
        # 类型感知包过滤：beamer 内置图形支持，无需 graphicx；letter 通常不需要 amsmath
        doc_class = self.current_values.get('document_class', 'article')
        skip_ams = doc_class in ('letter', 'scrlttr2')
        skip_graphicx = doc_class in ('beamer',)

        if self.current_values['packages']['ams'] and not skip_ams:
            text += '''\\usepackage{amsmath}
\\usepackage{amsfonts}
\\usepackage{amssymb}
\\usepackage{amsthm}
'''
        for package_name, do_insert in self.current_values['packages'].items():
            if package_name == 'ams':
                continue
            if package_name == 'graphicx' and skip_graphicx:
                continue
            if do_insert:
                text += '\\usepackage{' + package_name + '}\n'
        # 用户自定义包（报告 #2）：逗号分隔，逐个插入。
        for name in self.current_values.get('custom_packages', '').split(','):
            name = name.strip()
            if name:
                text += '\\usepackage{' + name + '}\n'
        return text

    def _settings_key(self, doc_class):
        '''document_class 字符串 → current_values 中的设置键。
        KOMA 类（scrartcl 等）复用对应标准类的键。'''
        return KOMA_CLASS_TO_STANDARD.get(doc_class, doc_class)

    def get_documentclass_preview(self):
        '''实时预览将生成的 \\documentclass 行（报告 #3）。'''
        doc_class = self.current_values['document_class']
        if doc_class in ('beamer',):
            return '\\documentclass{' + doc_class + '}'
        if doc_class == 'letter':
            options = self._build_class_options('letter')
            return '\\documentclass[' + options + ']{letter}'
        if doc_class == 'scrlttr2':
            return '\\documentclass{' + doc_class + '}'
        options = self._build_class_options(self._settings_key(doc_class))
        return '\\documentclass[' + options + ']{' + doc_class + '}'

    def insert_template(self, template_start, template_end):
        buffer = self.document.source_buffer
        buffer.begin_user_action()

        bounds = buffer.get_bounds()
        text = buffer.get_text(bounds[0], bounds[1], True)
        line_count_before_insert = buffer.get_line_count()

        # replace tabs with spaces, if set in preferences
        if self.settings.get_value('preferences', 'spaces_instead_of_tabs'):
            number_of_spaces = self.settings.get_value('preferences', 'tab_width')
            template_start = template_start.replace('\t', ' ' * number_of_spaces)
            template_end = template_end.replace('\t', ' ' * number_of_spaces)

        bounds = buffer.get_bounds()
        buffer.insert(bounds[0], template_start)
        bounds = buffer.get_bounds()
        buffer.insert(bounds[1], template_end)

        bounds = buffer.get_bounds()
        bounds[0].forward_chars(len(template_start))
        buffer.place_cursor(bounds[0])

        buffer.end_user_action()
        buffer.begin_user_action()

        if len(text.strip()) > 0:
            note = _('''% NOTE: The content of your document has been commented out
% by the wizard. Just do a CTRL+Z (undo) to put it back in
% or remove the "%" before each line you want to keep.
% You can remove this note as well.
% 
''')
            note_len = len(note)
            note_number_of_lines = note.count('\n')
            offset = buffer.get_iter_at_mark(buffer.get_insert()).get_line()
            iter_found, offset_iter = buffer.get_iter_at_line(offset)
            buffer.insert(offset_iter, note)

            for line_number in range(offset + note_number_of_lines, line_count_before_insert + offset + note_number_of_lines):
                iter_found, offset_iter = buffer.get_iter_at_line(line_number)
                buffer.insert(offset_iter, '% ')
            insert_iter = buffer.get_iter_at_mark(buffer.get_insert())
            insert_iter.backward_chars(note_len + 2)
            buffer.place_cursor(insert_iter)

        buffer.end_user_action()


