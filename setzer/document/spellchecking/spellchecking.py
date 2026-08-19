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

'''LaTeX 文档的实时拼写检查。

后端是 pyenchant（enchant C 库的 Python 绑定，使用系统 hunspell 词典）；
未安装时功能优雅降级（偏好页置灰、右键无建议、不产生任何标记）。

LaTeX 感知的跳过策略（两层）：
1. GtkSourceView context class——自带的 latex.lang 已为数学（$…$、\\[…\\]、
   equation 等）、命令名（\\textbf）、verbatim/lstlisting、环境名、
   documentclass/usepackage/include 参数标注了 ``no-spell-check`` class，
   通过 ``GtkSource.Buffer.iter_has_context_class()`` 查询，语法引擎保证
   与高亮结果一致（含跨行数学环境）。
2. 补充正则——latex.lang 未覆盖的「引用/标签/文件路径」类参数
   （\\label{fig:intro}、\\cite{...}、\\includegraphics{...} 等）是标识符
   而非自然语言，检查只会误报，用 _ARG_SKIP_RE 按行跳过。

检查调度：buffer 变更后防抖 400ms 触发一次全文检查；检查按 300 行分片
在 idle 中执行，避免大文档单帧卡顿。新一轮检查通过 generation 计数使
在途分片立即作废。错误标记用 Pango.Underline.ERROR 波浪线 + 主题
error 色（深浅色主题切换时同步更新）。
'''

import os
import re

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Gdk, Pango, Adw

from setzer.app.service_locator import ServiceLocator
from setzer.app.color_manager import ColorManager

# pyenchant 可选依赖：缺库时 ENCHANT_AVAILABLE=False，本模块全部入口
# 变为 no-op，偏好页据此置灰。
try:
    import enchant
    ENCHANT_AVAILABLE = True
except ImportError:
    enchant = None
    ENCHANT_AVAILABLE = False


# 拉丁字母词（Latin-1 变音符号 + Latin Extended-A/B，覆盖欧洲语言与拼音
# 声调字符），支持 - ' ’ 连接的复合词（well-known、l'hôtel）。刻意不含
# CJK/西里尔等非拉丁脚本——它们在拉丁词典里必然「拼错」，会把中文文档
# 整段标红；非拉丁文本直接不参与检查。
_LATIN = r"A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F"
_WORD_RE = re.compile(r"[{}]+(?:['’-][{}]+)*".format(_LATIN, _LATIN))

# latex.lang 的 no-spell-check class 未覆盖的标识符类参数（见模块注释）。
_ARG_SKIP_RE = re.compile(
    r"\\(?:label|ref|eqref|pageref|autoref|nameref|vref|cref|Cref|labelcref|cpageref"
    r"|cite|citep|citet|citeauthor|citeyear|citealt|citealp|nocite"
    r"|parencite|textcite|footcite|includegraphics|graphicspath"
    r"|input|include|includeonly|bibliography|addbibresource|bibliographystyle"
    r"|lstinputlisting)\*?\s*(?:\[[^\]\n]*\]\s*)*\{[^{}\n]*\}")

# 单次 idle 分片检查的行数：兼顾单帧耗时（大文档不卡 UI）与总完成时间。
_CHUNK_LINES = 300
# 用户停止输入后的防抖间隔（毫秒）。
_DEBOUNCE_MS = 400
# 右键建议列表最多显示条数。
_MAX_SUGGESTIONS = 5
# 短于此长度的词不检查（单字母噪声大，且 i/a 等本就多为合法词）。
_MIN_WORD_LENGTH = 2

# 错误标记 tag 名（每文档 buffer 的 tag table 中仅一份）。
_TAG_NAME = 'spellcheck-error'

# 常用词典 tag 的显示名（endonym，找不到映射时直接显示 tag 原文）。
_LANGUAGE_NAMES = {
    'en_US': 'English (United States)',
    'en_GB': 'English (United Kingdom)',
    'en_AU': 'English (Australia)',
    'en_CA': 'English (Canada)',
    'en_ZA': 'English (South Africa)',
    'de_DE': 'Deutsch (Deutschland)',
    'de_AT': 'Deutsch (Österreich)',
    'de_CH': 'Deutsch (Schweiz)',
    'fr_FR': 'Français (France)',
    'fr_CA': 'Français (Canada)',
    'es_ES': 'Español (España)',
    'es_MX': 'Español (México)',
    'es_AR': 'Español (Argentina)',
    'it_IT': 'Italiano (Italia)',
    'pt_BR': 'Português (Brasil)',
    'pt_PT': 'Português (Portugal)',
    'nl_NL': 'Nederlands (Nederland)',
    'nl_BE': 'Nederlands (België)',
    'sv_SE': 'Svenska (Sverige)',
    'da_DK': 'Dansk (Danmark)',
    'nb_NO': 'Norsk bokmål (Norge)',
    'nn_NO': 'Norsk nynorsk (Norge)',
    'fi_FI': 'Suomi (Suomi)',
    'is_IS': 'Íslenska (Ísland)',
    'pl_PL': 'Polski (Polska)',
    'cs_CZ': 'Čeština (Česko)',
    'sk_SK': 'Slovenčina (Slovensko)',
    'hu_HU': 'Magyar (Magyarország)',
    'ro_RO': 'Română (România)',
    'bg_BG': 'Български (България)',
    'el_GR': 'Ελληνικά (Ελλάδα)',
    'ru_RU': 'Русский (Россия)',
    'uk_UA': 'Українська (Україна)',
    'sr_RS': 'Српски (Србија)',
    'hr_HR': 'Hrvatski (Hrvatska)',
    'sl_SI': 'Slovenščina (Slovenija)',
    'tr_TR': 'Türkçe (Türkiye)',
    'he_IL': 'עברית (ישראל)',
    'ar': 'العربية',
    'id_ID': 'Bahasa Indonesia',
    'ms_MY': 'Bahasa Melayu',
    'vi_VN': 'Tiếng Việt',
    'th_TH': 'ไทย (ไทย)',
}


class SpellChecker(object):
    '''每文档一个实例（仅 LaTeX 文档构造，见 Document.__init__）。

    词典对象按语言进程级共享（hunspell 词典加载昂贵）；「忽略」词表为
    会话级（跨文档生效、不落盘）；「加入词典」写入用户词表文件
    ~/.config/setzer/spellchecking_pwl.txt（enchant DictWithPWL 自管）。
    '''

    # lang tag -> enchant.DictWithPWL（进程级缓存）
    _dict_cache = dict()
    # 本会话「忽略」的词（小写），跨文档生效，不落盘
    _session_ignored = set()

    def __init__(self, document):
        self.document = document
        self.buffer = document.source_buffer
        self.view = document.view.source_view
        self.settings = ServiceLocator.get_settings()

        self.enabled = False
        self.dict = None
        self._is_shutdown = False
        self._debounce_id = None
        self._chunk_idle_id = None
        self._next_line = 0
        # generation 计数：schedule_recheck 递增，在途分片发现代数不符即退出，
        # 使「停顿后再次输入」能立刻废弃上一轮未完成的检查。
        self._generation = 0

        # 错误下划线 tag：波浪线（Pango.Underline.ERROR）+ 主题 error 色。
        tag = self.buffer.get_tag_table().lookup(_TAG_NAME)
        if tag is None:
            tag = self.buffer.create_tag(_TAG_NAME)
        self._apply_tag_color(tag)
        self.error_tag = tag

        # buffer 每次变更（含 set_text 载入）→ 防抖后全文检查。
        self.buffer.connect('changed', self.on_buffer_changed)
        self.settings.connect('settings_changed', self.on_settings_changed)
        # 深浅色主题切换时刷新波浪线颜色。
        self._theme_handler_id = Adw.StyleManager.get_default().connect(
            'notify::dark', self.on_theme_changed)

        # 应用当前设置；构造时文档通常尚无内容，空跑一次开销可忽略。
        self._apply_settings()

    # ------------------------------------------------------------------
    # 词典与语言
    # ------------------------------------------------------------------

    @staticmethod
    def is_available():
        return ENCHANT_AVAILABLE

    @staticmethod
    def available_languages():
        '''返回系统可用的基础词典 tag 列表（过滤 variant/accents 派生 tag）。'''
        if not ENCHANT_AVAILABLE:
            return []
        try:
            tags = [tag for tag in enchant.list_languages() if '-' not in tag]
        except Exception:
            return []
        return sorted(tags)

    @staticmethod
    def language_display_name(tag):
        return _LANGUAGE_NAMES.get(tag, tag)

    @classmethod
    def _get_dict(cls, lang):
        '''取（或创建并缓存）指定语言的词典。语言不可用时回退 en_US → 首个可用。'''
        if not ENCHANT_AVAILABLE:
            return None
        available = cls.available_languages()
        if lang not in available:
            lang = 'en_US' if 'en_US' in available else (available[0] if available else None)
        if lang is None:
            return None
        dict_obj = cls._dict_cache.get(lang)
        if dict_obj is None:
            pwl_path = os.path.join(ServiceLocator.get_config_folder(),
                                    'spellchecking_pwl.txt')
            try:
                dict_obj = enchant.DictWithPWL(lang, pwl_path)
            except Exception:
                return None
            cls._dict_cache[lang] = dict_obj
        return dict_obj

    # ------------------------------------------------------------------
    # 设置响应
    # ------------------------------------------------------------------

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if item in ('spellchecking_enabled', 'spellchecking_language'):
            self._apply_settings()

    def _apply_settings(self):
        enabled = ENCHANT_AVAILABLE and bool(
            self.settings.get_value('preferences', 'spellchecking_enabled'))
        new_dict = self._get_dict(
            self.settings.get_value('preferences', 'spellchecking_language')) \
            if enabled else None
        if enabled and new_dict is None:
            enabled = False

        if enabled == self.enabled and new_dict is self.dict:
            return
        self.enabled, self.dict = enabled, new_dict

        if self.enabled:
            self.schedule_recheck()
        else:
            self._cancel_work()
            self._remove_all_tags()

    # ------------------------------------------------------------------
    # 检查调度：防抖 + 分片 idle
    # ------------------------------------------------------------------

    def on_buffer_changed(self, buffer):
        if not self.enabled or self._is_shutdown:
            return
        if self._debounce_id is not None:
            GLib.Source.remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(_DEBOUNCE_MS, self._on_debounce_timeout)

    def _on_debounce_timeout(self):
        self._debounce_id = None
        self.schedule_recheck()
        return False

    def schedule_recheck(self):
        '''立即调度一次全文拼写检查（忽略/加词典后由外部调用）。'''
        if self._is_shutdown or not self.enabled:
            return
        self._generation += 1
        self._cancel_chunk_idle()
        self._next_line = 0
        self._chunk_idle_id = GLib.idle_add(self._recheck_chunk, self._generation)

    def _recheck_chunk(self, generation):
        # 本 source 即将结束；若还有剩余行，用新 source 续期（链式新 source
        # 而非返回 True 常驻，使 shutdown 只需移除最后一个挂起的 id）。
        self._chunk_idle_id = None
        if self._is_shutdown or not self.enabled:
            return False
        if generation != self._generation:
            return False

        line_count = self.buffer.get_line_count()
        start_line = self._next_line
        if start_line >= line_count:
            return False
        end_line = min(start_line + _CHUNK_LINES, line_count)
        self._check_line_range(start_line, end_line)
        self._next_line = end_line
        if end_line < line_count:
            self._chunk_idle_id = GLib.idle_add(self._recheck_chunk, generation)
        return False

    def _check_line_range(self, start_line, end_line):
        '''检查 [start_line, end_line) 行：先整体清除旧标记，再逐词标记。'''
        buf = self.buffer
        start_iter = buf.get_iter_at_line(start_line)[1]
        end_iter = buf.get_iter_at_line(end_line - 1)[1].copy()
        if not end_iter.ends_line():
            end_iter.forward_to_line_end()
        buf.remove_tag(self.error_tag, start_iter, end_iter)

        for line_number in range(start_line, end_line):
            line_text = self.document.get_line(line_number)
            if len(line_text) < _MIN_WORD_LENGTH:
                continue
            line_start = buf.get_iter_at_line(line_number)[1].get_offset()
            skip_ranges = [m.span() for m in _ARG_SKIP_RE.finditer(line_text)]
            for match in _WORD_RE.finditer(line_text):
                s, e = match.span()
                # 与标识符类参数区间有交集 → 跳过。
                if any(s < r_end and r_start < e for r_start, r_end in skip_ranges):
                    continue
                word = match.group(0)
                if len(word) < _MIN_WORD_LENGTH:
                    continue
                # TextIter 的 offset / get_line_offset() 是**字符**偏移，与 re
                # 的 span（字符索引）一致，可直接相加定位（已实测确认）。
                # 首尾任一位置落在 no-spell-check 上下文（命令/数学/verbatim/
                # 环境名等，由 latex.lang 语法引擎判定，跨行环境同样正确）
                # → 跳过。首尾都查，覆盖词跨上下文边界的情形。
                start_word = buf.get_iter_at_offset(line_start + s)
                end_word = buf.get_iter_at_offset(line_start + e - 1)
                if buf.iter_has_context_class(start_word, 'no-spell-check'):
                    continue
                if buf.iter_has_context_class(end_word, 'no-spell-check'):
                    continue
                # 复合词（well-known、l'hôtel）整体查不到时按分隔符拆分逐段
                # 检查，只标记真正出错的段。
                for rel_start, rel_end in self._misspelled_spans(word):
                    buf.apply_tag(
                        self.error_tag,
                        buf.get_iter_at_offset(line_start + s + rel_start),
                        buf.get_iter_at_offset(line_start + s + rel_end))

    # ------------------------------------------------------------------
    # 词级查询与修改（右键菜单 / 动作回调使用）
    # ------------------------------------------------------------------

    def is_misspelled(self, word):
        return bool(self._misspelled_spans(word))

    def _misspelled_spans(self, word):
        '''返回 word 内拼写错误片段的相对区间列表（空列表 = 拼写正确）。

        先整体查词；查不到再按分隔符（- ' ’）拆分逐段检查，只标记真正
        出错的段——整体与分段都查不到才标记整个词（无分隔符的普通词
        天然退化为这种情况）。
        '''
        if self.dict is None:
            return []
        if word.lower() in SpellChecker._session_ignored:
            return []
        try:
            if self.dict.check(word):
                return []
        except Exception:
            return []
        spans = []
        pos = 0
        for part in re.split(r"[-'’]", word):
            if part and len(part) >= _MIN_WORD_LENGTH:
                if part.lower() not in SpellChecker._session_ignored:
                    try:
                        part_ok = self.dict.check(part)
                    except Exception:
                        part_ok = True
                    if not part_ok:
                        spans.append((pos, pos + len(part)))
            pos += len(part) + 1
        return spans

    def suggest(self, word):
        if self.dict is None:
            return []
        try:
            return [s for s in self.dict.suggest(word) if s][:_MAX_SUGGESTIONS]
        except Exception:
            return []

    def get_misspelled_word_at_position(self, x, y):
        '''右键坐标 (x, y) 处若是拼写错误词，返回 (word, buffer_offset)，否则 None。

        x, y 与 popup_at_cursor 同源（secondary_click_controller 提供的
        source_view **widget** 坐标）。GTK4 的 get_iter_at_location 要求
        **buffer** 坐标（官方文档：事件坐标须经 window_to_buffer_coords 转换），
        直接传 widget 坐标会因滚动偏移/边距/gutter 而错位——表现为右键点的词
        与菜单建议不对齐、甚至检测不到。返回的 buffer_offset 与 TextIter
        offset 同为**字符**偏移（供 replace_word 定位）。
        '''
        if not self.enabled or self.dict is None:
            return None
        buffer_x, buffer_y = self.view.window_to_buffer_coords(
            Gtk.TextWindowType.WIDGET, x, y)
        result = self.view.get_iter_at_location(buffer_x, buffer_y)
        if not isinstance(result, tuple) or not result[0]:
            return None
        it = result[1]
        line = it.get_line()
        line_text = self.document.get_line(line)
        if len(line_text) < _MIN_WORD_LENGTH:
            return None
        line_start = self.buffer.get_iter_at_line(line)[1].get_offset()
        # get_line_offset() 与 re 的 span 均为**字符**偏移，可直接比较。
        col = it.get_line_offset()
        for match in _WORD_RE.finditer(line_text):
            s, e = match.span()
            if s <= col < e:
                word = match.group(0)
                if len(word) >= _MIN_WORD_LENGTH and self.is_misspelled(word):
                    return (word, line_start + s)
                return None
        return None

    def replace_word(self, offset, word, suggestion):
        '''替换 offset 处的 word 为 suggestion。菜单打开期间文档若已变化
        （校验失败）则放弃，宁可不替换也不错替。'''
        buf = self.buffer
        if offset < 0 or offset + len(word) > buf.get_char_count():
            return False
        start = buf.get_iter_at_offset(offset)
        end = buf.get_iter_at_offset(offset + len(word))
        if buf.get_text(start, end, False) != word:
            return False
        buf.begin_user_action()
        buf.delete(start, end)
        buf.place_cursor(buf.get_iter_at_offset(offset))
        buf.insert_at_cursor(suggestion)
        buf.end_user_action()
        return True

    @classmethod
    def ignore_word(cls, word):
        '''会话内忽略（跨文档、不落盘），随后重查所有文档。'''
        cls._session_ignored.add(word.lower())
        cls.recheck_all_documents()

    def add_to_dictionary(self, word):
        '''写入用户词表（~/.config/setzer/spellchecking_pwl.txt），随后重查所有文档。'''
        if self.dict is None:
            return
        try:
            self.dict.add(word)
        except Exception:
            return
        self.recheck_all_documents()

    # ------------------------------------------------------------------
    # 词表管理（偏好设置「管理词表」对话框使用）
    # ------------------------------------------------------------------

    @classmethod
    def get_session_ignored_words(cls):
        '''返回本会话忽略的词（小写，已排序）。'''
        return sorted(cls._session_ignored)

    @classmethod
    def set_session_ignored_words(cls, words):
        '''整体设置会话忽略词表（管理对话框点 Save 时调用）。'''
        cls._session_ignored = {w.lower() for w in words if w.strip()}
        cls.recheck_all_documents()

    @staticmethod
    def _pwl_path():
        return os.path.join(ServiceLocator.get_config_folder(),
                            'spellchecking_pwl.txt')

    @classmethod
    def get_user_dictionary_words(cls):
        '''读取用户词表文件，返回按字母排序的去重词列表。'''
        try:
            with open(cls._pwl_path(), 'r', encoding='utf-8') as f:
                words = [line.strip() for line in f if line.strip()]
        except OSError:
            return []
        return sorted(set(words), key=str.lower)

    @classmethod
    def set_user_dictionary_words(cls, words):
        '''整体覆写用户词表（管理对话框点 Save 时调用）。

        一次写入 PWL 文件并重建词典缓存，让各文档 checker 重新加载。
        '''
        if not ENCHANT_AVAILABLE:
            return False
        words = sorted({w.strip() for w in words if w.strip()}, key=str.lower)
        path = cls._pwl_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(words))
                if words:
                    f.write('\n')
        except OSError:
            return False
        # 文件已变：清空进程级词典缓存，并让各文档 checker 重新加载新词表。
        cls._dict_cache.clear()
        workspace = ServiceLocator.get_workspace()
        if workspace is not None:
            for document in workspace.get_all_documents():
                checker = getattr(document, 'spellchecking', None)
                if checker is None:
                    continue
                if checker.enabled:
                    lang = checker.settings.get_value('preferences',
                                                      'spellchecking_language')
                    checker.dict = SpellChecker._get_dict(lang)
                    checker.schedule_recheck()
        return True

    @staticmethod
    def recheck_all_documents():
        workspace = ServiceLocator.get_workspace()
        if workspace is None:
            return
        for document in workspace.get_all_documents():
            checker = getattr(document, 'spellchecking', None)
            if checker is not None:
                checker.schedule_recheck()

    # ------------------------------------------------------------------
    # 外观与生命周期
    # ------------------------------------------------------------------

    def on_theme_changed(self, style_manager, pspec=None):
        self._apply_tag_color(self.error_tag)

    def _apply_tag_color(self, tag):
        rgba = Gdk.RGBA()
        rgba.parse(ColorManager.get_ui_color_string('error_color'))
        tag.set_property('underline-rgba', rgba)
        tag.set_property('underline', Pango.Underline.ERROR)

    def _cancel_chunk_idle(self):
        if self._chunk_idle_id is not None:
            try:
                GLib.Source.remove(self._chunk_idle_id)
            except (ValueError, RuntimeError):
                pass
            self._chunk_idle_id = None

    def _cancel_work(self):
        '''取消全部挂起回调并使在途分片失效（不清理已有标记）。'''
        self._generation += 1
        if self._debounce_id is not None:
            try:
                GLib.Source.remove(self._debounce_id)
            except (ValueError, RuntimeError):
                pass
            self._debounce_id = None
        self._cancel_chunk_idle()

    def _remove_all_tags(self):
        start = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        self.buffer.remove_tag(self.error_tag, start, end)

    def shutdown(self):
        '''文档关闭时清理：取消回调、断开单例信号连接、清除标记。'''
        self._is_shutdown = True
        self._cancel_work()
        try:
            self.settings.disconnect('settings_changed', self.on_settings_changed)
        except (TypeError, KeyError, AttributeError):
            pass
        try:
            Adw.StyleManager.get_default().disconnect(self._theme_handler_id)
        except (TypeError, AttributeError):
            pass
        try:
            self._remove_all_tags()
        except Exception:
            pass
