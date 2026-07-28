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

# WebKit 是可选依赖（见 help_panel_viewgtk.py 的 HAS_WEBKIT 说明）。
# 此模块（model 层）原在 import 期硬性 require_version('WebKit')，导致
# 无 WebKit 的系统上整个 workspace 模块加载失败、应用无法启动——即便
# 帮助面板的搜索功能本身不依赖 WebKit。改为可选 import：仅在
# update_colors 注入 UserStyleSheet 时需要 WebKit API，无 WebKit 时
# 跳过样式注入（WebView 不可用，本就不渲染页面）。
try:
    gi.require_version('WebKit', '6.0')
    from gi.repository import WebKit
    HAS_WEBKIT = True
except (ValueError, ImportError):
    HAS_WEBKIT = False

import os.path
import pickle
import html
import re
import json
from string import Template
from gi.repository import Pango
from setzer.app.font_manager import FontManager

from setzer.helpers.observable import Observable
from setzer.helpers.persistence import load_json, load_pickle_restricted
from setzer.helpers.help_search import build_trigram_index, search as fuzzy_search
import setzer.workspace.help_panel.help_panel_controller as help_panel_controller
import setzer.workspace.help_panel.help_panel_presenter as help_panel_presenter
from setzer.app.service_locator import ServiceLocator
from setzer.app.color_manager import ColorManager


# 帮助页 CSS 模板：$var 占位符由 update_colors 用 string.Template.safe_substitute
# 单次替换为当前主题颜色。替代原 5 次链式 str.replace：新增颜色变量只需在
# 模板加一个 $name 并在 _COLOR_KEYS 补一项，无需再叠一行 replace。
# safe_substitute 对未提供的 $name 保留原样（不抛 KeyError），且 CSS 不含
# '$' 字符，无需 '$$' 转义。字体（family/size）由 update_colors 从编辑器
# 字体设置注入，使帮助文档与编辑器使用同一字体。
_CSS_TEMPLATE = Template('''body {margin: 1em; margin-top: 0px; padding-top: 1px; background: $view_bg_color; color: $view_fg_color; font-family: $editor_font_family; font-size: $editor_font_size; }
a {color: $link_color; }
a:visited {color: $link_color_visited; }
a:active {color: $link_color_active; }
a.external:after {text-decoration: underline; text-decoration-color: $view_bg_color; content: ' 🡭'; }''')

# 占位符名 → ColorManager 键的映射。集中定义便于扩展：未来加颜色变量
# 只需在此 dict 增一行，模板里用对应 $name 即可。
_COLOR_KEYS = {
    'view_bg_color': 'view_bg_color',
    'view_fg_color': 'view_fg_color',
    'link_color_visited': 'accent_color',
    'link_color_active': 'accent_color',
    'link_color': 'accent_color',
}


class HelpPanel(Observable):

    def __init__(self, workspace):
        Observable.__init__(self)

        self.workspace = workspace
        self.view = ServiceLocator.get_main_window().help_panel

        self.path = 'file://' + os.path.join(ServiceLocator.get_resources_path(), 'help')
        self.home_uri = self.path + '/latex2e_0.html'
        self.current_uri = self.home_uri

        self.search_index = None
        # Trigram 索引：与 search_index 同生命周期懒构建。每项是 (key, trigram_set)。
        # 模糊搜索用 Jaccard 相似度比对 query 与 key 的 trigram 集合。
        # 2080 项 × 平均 ~30 trigrams/项 ≈ 6 万短字符串，内存占用可忽略；
        # 构建一次约 5ms，远小于 pickle.load 的一次性开销。
        self._trigram_index = None
        # 懒加载搜索索引：原实现在 workspace 构造（应用启动早期）同步
        # open + pickle.load 读取 search_index.pickle。该索引仅用于帮助面板搜索，
        # 若用户从不打开帮助面板（常见场景），这次 I/O + 反序列化（数千到上万项）
        # 完全是浪费，却推后了主窗口可交互时间。改为记录路径，首次搜索时才加载。
        # search_index.pickle 是程序打包资源（非用户文件），但仍用 pickle.load
        # 存在 RCE 风险（若资源被替换）。改为 JSON 优先 + 受限 pickle 回退：
        # 未来重新生成索引时应输出 search_index.json；现版仍读 .pickle 但用
        # RestrictedUnpickler 限制仅 builtins 容器类型，阻断 RCE。
        self._search_index_path = os.path.join(ServiceLocator.get_resources_path(), 'help', 'search_index.pickle')
        self._search_index_json_path = os.path.join(ServiceLocator.get_resources_path(), 'help', 'search_index.json')
        self.search_results_blank = list()
        self.search_results = self.search_results_blank
        self.query = ''

        self.controller = help_panel_controller.HelpPanelController(self, self.view)
        self.presenter = help_panel_presenter.HelpPanelPresenter(self, self.view)

        self.add_change_code('search_query_changed')

        # 跟踪已注册的 UserStyleSheet：update_colors 在 __init__ 与每次主题切换
        # （WorkspacePresenter.update_colors → HelpPanel.update_colors）时调用。
        # 原实现只 add_style_sheet 不 remove_style_sheet，长时间运行 + 频繁
        # 主题切换会让 UserContentManager 持有 N 个 style sheet，WebKit 每次
        # 页面加载/渲染都要合并全部 CSS 规则，导致帮助页滚动/hover 越用越卡。
        self._current_style_sheet = None
        self.update_colors()

    def _ensure_search_index(self):
        if self.search_index is None:
            # 优先读 JSON（未来重新生成索引时应输出此格式）
            self.search_index = load_json(self._search_index_json_path)
            if self.search_index is None:
                # 回退到旧 search_index.pickle：用 RestrictedUnpickler 限制
                # 仅 builtins 容器类型，阻断 RCE（资源文件被替换时的防御）。
                try:
                    self.search_index = load_pickle_restricted(self._search_index_path)
                except (OSError, pickle.UnpicklingError, EOFError,
                        AttributeError, ValueError) as e:
                    # 索引文件损坏或 Python 版本不兼容时不应让应用崩溃。
                    # 回退到空索引：用户仍可浏览帮助页面，仅搜索功能不可用。
                    print(f'Warning: failed to load help search index: {e}')
                    self.search_index = []
            # 构建模糊搜索用的 trigram 索引（纯逻辑见 help_search.py）。
            # 与 search_index 同生命周期，首次搜索时随索引一起懒加载。
            self._trigram_index = build_trigram_index(self.search_index)
        return self.search_index

    def set_uri(self, uri):
        self.current_uri = uri
        self.add_change_code('uri_changed', uri)

    def set_uri_by_search_item(self, uri_ending, text, location):
        self.current_uri = self.path + '/' + uri_ending

        self.search_results_blank = [item for item in self.search_results_blank if (item[0] != uri_ending or item[1] != text or item[2] != location)]
        self.search_results_blank.append([uri_ending, text, location])

        if len(self.search_results_blank) > 8:
            self.search_results_blank.pop()

        self.add_change_code('uri_changed', self.current_uri)

    def _highlight(self, text, words_lower):
        # 单次扫描替代原实现的多遍 str.replace：
        #   1. html.unescape 解码 HTML 实体（原 4 次 replace，且顺序脆弱）
        #   2. 大小写不敏感正则一次性插入 \x00/\x01 高亮标记（原每词 3 次 replace）
        #   3. html.escape 重新转义（原 6 次 replace）
        #   4. 标记转 <b></b>
        # \x00/\x01 是不可能出现在帮助文本中的控制字符，避免与原文冲突。
        text = html.unescape(text)
        if words_lower:
            pattern = re.compile('|'.join(re.escape(w) for w in words_lower), re.IGNORECASE)
            text = pattern.sub(lambda m: '\x00' + m.group(0) + '\x01', text)
        text = html.escape(text)
        text = text.replace('\x00', '<b>').replace('\x01', '</b>')
        return text

    def set_search_query(self, query):
        self.query = query
        if query == '':
            self.search_results = self.search_results_blank
        else:
            words = query.split()
            # 预小写化查询词：高亮与模糊搜索都用小写比较。
            words_lower = [w.lower() for w in words]
            self.search_results = list()
            index = self._ensure_search_index()
            # 模糊搜索逻辑见 setzer.helpers.help_search：trigram 逐词 Jaccard，
            # 全词精确子串命中优先 → 部分命中 → 纯模糊相似度，至多 8 项。
            ranked_idxs = fuzzy_search(query, index, self._trigram_index)
            for idx in ranked_idxs:
                item = index[idx]
                # 高亮用查询词子串：模糊匹配项可能不含完整查询词，
                # 此时 _highlight 不会插入 <b> 标记，结果仅显示原文（可接受）。
                headline = self._highlight(item[2], words_lower)
                location = self._highlight(item[3], words_lower)
                self.search_results.append([item[1], headline, location])
        self.add_change_code('search_query_changed')

    def update_colors(self):
        # 无 WebKit 时跳过样式注入：WebView 不存在，CSS 无注入目标。
        # view.user_content_manager 在无 WebKit 时为 None（见 viewgtk.py）。
        if not HAS_WEBKIT:
            return

        # 单次 safe_substitute 替换全部颜色变量，替代原 5 次链式 str.replace。
        # substitutions 通过推导式从 _COLOR_KEYS 一次性取全部颜色，新增变量
        # 只需在 _COLOR_KEYS 加一行 + 模板用 $name，无需改此方法。
        substitutions = {name: ColorManager.get_ui_color_string(key)
                         for name, key in _COLOR_KEYS.items()}

        # 编辑器字体：用 FontManager.font_string（当前生效字体，含设置与缩放），
        # 与 GtkSourceView/TextView 走同一来源，使帮助文档与编辑器字体一致。
        # 字体名用 json.dumps 双引号包裹转义，防止字体名里的特殊字符破坏 CSS
        # 结构（参照 font_manager.propagate_font_setting 的写法）。
        font_desc = Pango.FontDescription.from_string(FontManager.font_string)
        font_family = json.dumps(font_desc.get_family())
        font_size = font_desc.get_size() / Pango.SCALE
        substitutions['editor_font_family'] = font_family
        substitutions['editor_font_size'] = f'{font_size}pt'

        css = _CSS_TEMPLATE.safe_substitute(substitutions)

        style_sheet = WebKit.UserStyleSheet.new(css, WebKit.UserContentInjectedFrames.ALL_FRAMES, WebKit.UserStyleLevel.USER, None, None)

        # 先移除上一份 style sheet 再注册新的，避免 UserContentManager 累积多份
        # 语义等价的 CSS（仅颜色不同）。WebKit 在每次页面加载/样式重算时
        # 都会遍历所有已注册 style sheet 做规则合并，N 份累积会让帮助页
        # 渲染开销随主题切换次数线性增长。
        if self._current_style_sheet is not None:
            try:
                self.view.user_content_manager.remove_style_sheet(self._current_style_sheet)
            except (TypeError, AttributeError):
                pass
        self.view.user_content_manager.add_style_sheet(style_sheet)
        self._current_style_sheet = style_sheet


