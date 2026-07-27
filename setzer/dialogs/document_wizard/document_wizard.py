#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
from gi.repository import Gtk, Gio, Gdk, Adw

import setzer.dialogs.document_wizard.document_wizard_viewgtk as view
from setzer.dialogs.document_wizard.pages.page_document_class import DocumentClassPage
from setzer.dialogs.document_wizard.pages.page_article_settings import ArticleSettingsPage
from setzer.dialogs.document_wizard.pages.page_report_settings import ReportSettingsPage
from setzer.dialogs.document_wizard.pages.page_book_settings import BookSettingsPage
from setzer.dialogs.document_wizard.pages.page_letter_settings import LetterSettingsPage
from setzer.dialogs.document_wizard.pages.page_beamer_settings import BeamerSettingsPage
from setzer.dialogs.document_wizard.pages.page_general_settings import GeneralSettingsPage
from setzer.dialogs.document_wizard import page_map
from setzer.app.service_locator import ServiceLocator
from setzer.app.latex_db import LaTeXDB

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

        self.init_current_values()
        self.setup()

        self.presets = None
        self.current_page = -1
        self.load_presets()
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
        self.completed = True

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

        self.view.close()

    def init_current_values(self):
        # 默认纸张按 locale 选：美加墨用 US Letter，其余（绝大多数地区）
        # 用 A4。报告 #13 要求"按系统语言选默认纸张"。
        default_format = self._default_page_format()

        self.current_values['document_class'] = 'article'
        self.current_values['title'] = ''
        self.current_values['author'] = ''
        self.current_values['date'] = '\\today'
        self.current_values['languages'] = LaTeXDB.get_languages_dict()
        # Problem 5: 字体包选择。lmodern（默认，pdfLaTeX 推荐）、
        # fontspec（XeLaTeX/LuaLaTeX）、none（用户自行处理）。
        self.current_values['font_package'] = 'lmodern'
        self.current_values['packages'] = dict()
        self.current_values['packages']['ams'] = True
        self.current_values['packages']['graphicx'] = True
        self.current_values['packages']['color'] = True
        self.current_values['packages']['xcolor'] = True
        self.current_values['packages']['url'] = True
        self.current_values['packages']['theorem'] = False
        self.current_values['packages']['textcomp'] = True
        self.current_values['packages']['listings'] = False
        self.current_values['packages']['hyperref'] = False
        self.current_values['packages']['glossaries'] = False
        self.current_values['packages']['parskip'] = True
        self.current_values['article'] = dict()
        self.current_values['article']['page_format'] = default_format
        self.current_values['article']['font_size'] = 10
        self.current_values['article']['option_twocolumn'] = False
        self.current_values['article']['option_default_margins'] = True
        self.current_values['article']['margin_left'] = 3.5
        self.current_values['article']['margin_right'] = 3.5
        self.current_values['article']['margin_top'] = 3.5
        self.current_values['article']['margin_bottom'] = 3.5
        self.current_values['article']['is_landscape'] = False
        self.current_values['report'] = dict()
        self.current_values['report']['page_format'] = default_format
        self.current_values['report']['font_size'] = 10
        self.current_values['report']['option_twocolumn'] = False
        self.current_values['report']['option_default_margins'] = True
        self.current_values['report']['margin_left'] = 3.5
        self.current_values['report']['margin_right'] = 3.5
        self.current_values['report']['margin_top'] = 3.5
        self.current_values['report']['margin_bottom'] = 3.5
        self.current_values['report']['is_landscape'] = False
        self.current_values['book'] = dict()
        self.current_values['book']['page_format'] = default_format
        self.current_values['book']['font_size'] = 10
        self.current_values['book']['option_twocolumn'] = False
        self.current_values['book']['option_default_margins'] = True
        self.current_values['book']['margin_left'] = 3.5
        self.current_values['book']['margin_right'] = 3.5
        self.current_values['book']['margin_top'] = 3.5
        self.current_values['book']['margin_bottom'] = 3.5
        self.current_values['book']['is_landscape'] = False
        self.current_values['letter'] = dict()
        self.current_values['letter']['page_format'] = default_format
        self.current_values['letter']['font_size'] = 10
        self.current_values['letter']['option_twocolumn'] = False
        self.current_values['letter']['is_landscape'] = False
        self.current_values['letter']['option_default_margins'] = True
        self.current_values['letter']['margin_left'] = 3.5
        self.current_values['letter']['margin_right'] = 3.5
        self.current_values['letter']['margin_top'] = 3.5
        self.current_values['letter']['margin_bottom'] = 3.5
        self.current_values['beamer'] = dict()
        self.current_values['beamer']['theme'] = 'default'
        self.current_values['beamer']['option_show_navigation'] = True
        self.current_values['beamer']['option_top_align'] = True

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

        self.pages = list()
        self.pages.append(DocumentClassPage(self.current_values))
        self.pages.append(ArticleSettingsPage(self.current_values))
        self.pages.append(ReportSettingsPage(self.current_values))
        self.pages.append(BookSettingsPage(self.current_values))
        self.pages.append(LetterSettingsPage(self.current_values))
        self.pages.append(BeamerSettingsPage(self.current_values))
        self.pages.append(GeneralSettingsPage(self.current_values))
        for page in self.pages: self.view.page_stack.add_child(page.view)

        self.view.cancel_button.connect('clicked', self.on_cancel_button_clicked)
        self.view.create_button.connect('clicked', self.on_create_button_clicked)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self.on_keypress)
        self.view.add_controller(key_controller)
        for page in self.pages: page.observe_view()
        self.view.next_button.connect('clicked', self.goto_page_next)
        self.view.back_button.connect('clicked', self.goto_page_prev)

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
            # 类型校验：必须是 dict。None / 非 dict 一律回退到默认预设。
            if not isinstance(presets, dict):
                presets = None
            self.presets = presets

        for page in self.pages:
            page.load_presets(self.presets)

    def save_presets(self):
        # 直接存 dict（JSON 兼容），不再 pickle.dumps。
        # settings.set_value → settings.json 持久化时自动 JSON 序列化。
        self.settings.set_value('app_document_wizard', 'presets', self.current_values)

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
        if self.current_page != page_number:
            self.current_page = page_number
            self.view.page_stack.set_visible_child(self.pages[page_number].view)
            self.view.subtitle_label.set_text(self.pages[page_number].view.headerbar_subtitle)

            self.pages[page_number].on_activation()

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

    def _get_standard_document(self, doc_class, section_cmd, include_abstract):
        '''article / report / book 共享模板。
        三者仅文档类名、章节命令（\\section vs \\chapter）、是否含 abstract 不同。'''
        s = self.current_values[doc_class]
        options = (
            self.page_formats[s['page_format']] + ','
            + str(s['font_size']) + 'pt'
            + (',twocolumn' if s['option_twocolumn'] else '')
            + (',landscape' if s['is_landscape'] else '')
        )
        preamble = (
            '\\documentclass[' + options + ']{' + doc_class + '}\n'
            + self._get_geometry_line(doc_class)
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
        if include_abstract:
            body += '\\begin{abstract}\n\\end{abstract}\n\n'
        body += '\\' + section_cmd + '{}\n\n'
        return (preamble + body, '\n\n\\end{document}')

    # ---- templates ----

    def get_insert_text_article(self):
        return self._get_standard_document('article', 'section', include_abstract=True)

    def get_insert_text_report(self):
        return self._get_standard_document('report', 'chapter', include_abstract=True)

    def get_insert_text_book(self):
        return self._get_standard_document('book', 'chapter', include_abstract=False)

    def get_insert_text_letter(self):
        s = self.current_values['letter']
        options = (
            self.page_formats[s['page_format']] + ',' + str(s['font_size']) + 'pt'
            + (',twocolumn' if s['option_twocolumn'] else '')
            + (',landscape' if s['is_landscape'] else '')
        )
        preamble = (
            '\\documentclass[' + options + ']{letter}\n'
            + self._get_geometry_line('letter')
            + self._get_preamble_packages()
        )
        # Letter body 结构独特：address / date / signature + letter 环境
        title_line = ('\\\\~\\\\\\textbf{' + self.current_values['title'] + '}'
                      if len(self.current_values['title']) > 0 else '')
        body = (
            '\n\\address{' + _('Your name') + '\\\\' + _('Your address') + '\\\\' + _('Your phone number') + '}\n'
            '\\date{' + self.current_values['date'] + '}\n'
            '\\signature{' + self.current_values['author'] + '}\n\n'
            '\\begin{document}\n\n'
            '\\begin{letter}{' + _('Destination') + '\\\\' + _('Address of the destination') + '\\\\'
            + _('Phone number of the destination') + title_line + '}\n\n'
            '\\opening{' + _('Dear addressee,') + '}\n\n'
        )
        end = (
            '\n\n\\closing{' + _('Yours sincerely,') + '}\n\n'
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
        if self.current_values['packages']['ams']:
            text += '''\\usepackage{amsmath}
\\usepackage{amsfonts}
\\usepackage{amssymb}
\\usepackage{amsthm}
'''
        for package_name, do_insert in self.current_values['packages'].items():
            if package_name != 'ams' and do_insert:
                text += '\\usepackage{' + package_name + '}\n'
        return text

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


