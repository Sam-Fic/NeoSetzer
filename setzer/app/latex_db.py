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
from gi.repository import GLib

import os.path, re, bibtexparser
import xml.etree.ElementTree as ET

import setzer.helpers.path as path_helpers
from setzer.app.service_locator import ServiceLocator


class LaTeXDB():

    static_proposals = dict()
    resources_path = None
    dynamic_commands = dict()
    dynamic_commands['references'] = ['\\ref*', '\\ref', '\\pageref*', '\\pageref', '\\eqref']
    dynamic_commands['citations'] = ['\\citet*', '\\citet', '\\citep*', '\\citep', '\\citealt', '\\citealp', '\\citeauthor*', '\\citeauthor', '\\citeyearpar', '\\citeyear', '\\textcite', '\\parencite', '\\autocite', '\\cite']
    # 预编译 ref/cite 命令前缀的正则。原 get_dynamic_proposals 每次按键都
    # 重新 '|' + re.escape + .replace + ServiceLocator.get_regex_object：
    # 字符串拼接 + escape 扫描整串，autocomplete 激活时（打 \ref{...} 期间）
    # 每个字符都付一次代价。dynamic_commands 在 init 后不变，正则字符串可
    # 缓存。compiled 对象经 ServiceLocator.get_regex_object 二次缓存，但
    # 此处直接持有避免每次哈希查表。
    _ref_regex = None
    _cite_regex = None
    files = dict()
    languages_dict = None
    packages_dict = None
    # LaTeXDB 刷新去抖：add_document / remove_document / parse_result 都会
    # 触发 parse_included_files（对每个 included 文件做 stat + 可能 read +
    # 正则扫描）。原实现同步调用，会话恢复连续打开 5 个文档即触发 5 次全量
    # 刷新。改为 GLib.idle_add 合并：连续多次 schedule 只触发一次实际刷新，
    # 且延迟到主线程空闲时执行，不阻塞当前帧的 UI 更新。
    _refresh_idle_id = None
    # 最近一次 parse_included_files 的错误信息（traceback 文本）。
    # None 表示成功（或尚未运行）；非 None 表示解析失败，\ref/\cite 动态
    # 补全会降级为空列表。autocomplete 据此在补全弹窗显示提示行，避免
    # "补全静默不工作"的问题（UX 报告 #8）。
    last_parse_error = None

    def init(resources_path):
        LaTeXDB.resources_path = resources_path
        # 预编译 ref/cite 前缀正则（dynamic_commands 在此刻已就绪）。
        ref_pattern = '(' + re.escape('|'.join(LaTeXDB.dynamic_commands['references'])).replace('\\|', '|') + ')'
        cite_pattern = '(' + re.escape('|'.join(LaTeXDB.dynamic_commands['citations'])).replace('\\|', '|') + ')'
        LaTeXDB._ref_regex = re.compile(ref_pattern)
        LaTeXDB._cite_regex = re.compile(cite_pattern)
        LaTeXDB.generate_static_proposals()
        LaTeXDB.parse_included_files()
        # 不再注册 3 秒常驻轮询。改为事件驱动：文档打开/关闭/构建完成时
        # 由 workspace / build_system 显式调用 LaTeXDB.schedule_parse_included_files()。
        # LaTeXDB 的数据用于 autocomplete 的 \ref/\cite 补全，仅在用户
        # 打字时查询；文档加载/构建完成时刷新一次即覆盖所有场景。

    def schedule_parse_included_files():
        '''去抖调度 parse_included_files：连续多次调用只触发一次实际刷新。

        调用方：Workspace.add_document / remove_document、BuildSystem.parse_result。
        场景：会话恢复连续打开 5 个文档 → 5 次 schedule 仅 1 次 parse_included_files；
        构建完成 → 延迟到 idle 执行，不阻塞 parse_result 当前帧的 PDF 切换 / build_log 更新。

        GLib.idle_add 默认优先级 DEFAULT，回调在主线程 GTK 事件循环空闲时执行。
        若 schedule 后又调 schedule，旧 idle 仍在队列中，新 schedule 直接 return，
        不重复入队——最终只刷新一次，反映最新的文档列表状态。
        '''
        if LaTeXDB._refresh_idle_id is not None:
            return
        LaTeXDB._refresh_idle_id = GLib.idle_add(LaTeXDB._do_parse_included_files)

    def _do_parse_included_files():
        LaTeXDB._refresh_idle_id = None
        try:
            LaTeXDB.parse_included_files()
            # 解析成功：清除错误标志，使 autocomplete 提示行消失。
            LaTeXDB.last_parse_error = None
        except Exception:
            # 静默吞掉会让 \ref/\cite 自动补全静默失效且无任何诊断线索
            # （用户只感到"补全不工作"但不知原因）。打印 traceback 到 stderr
            # 便于诊断；同时记录到 last_parse_error，autocomplete 据此在
            # 补全弹窗显示"标签数据库不可用"提示行（UX 报告 #8）。
            # 不弹窗——parse 失败时补全降级为空列表，用户仍可正常编辑，
            # 弹窗反而扰人。
            import traceback
            LaTeXDB.last_parse_error = traceback.format_exc()
            traceback.print_exc()
        return False

    def is_dynamic_query(word):
        r'''判断 word 是否为 \ref/\cite 类动态补全查询。

        动态补全依赖 parse_included_files 收集的 labels/bibitems；静态命令
        补全（\section 等）来自 XML，不受 parse 错误影响。autocomplete 仅在
        动态查询且 parse 失败时显示"数据库不可用"提示，避免对静态补全也
        弹出误导性提示。
        '''
        if LaTeXDB._ref_regex is None or LaTeXDB._cite_regex is None:
            return False
        return (LaTeXDB._ref_regex.match(word) is not None or
                LaTeXDB._cite_regex.match(word) is not None)

    def get_items(word, top_item=None):
        r'''返回 word 的补全提案列表（动态 labels/bibitems + 静态命令）。

        排序策略（当 static 与 dynamic 同时存在且 dynamic>4 时）：
            dynamic[:5] + static + dynamic[5:]
        意图：\ref/\cite 补全时，最相关的 5 个 label 排最前（用户刚输入
        前缀时通常想选最近的 label），静态命令（\refeq 等）紧随其后不被
        埋没，剩余 label 排最后。当 dynamic≤4 或无 static 时简单拼接即可。

        top_item（如刚用过的 \ref 命令）无论在 dynamic 还是 static 中，
        都被移到列表首位（下方循环），不会被 dynamic[:5] 覆盖。'''
        # word.lower() 缓存：原代码在 L62 和 L64 各调一次 .lower()，每次按键
        # 都执行。缓存到局部变量避免重复字符串分配。
        word_lower = word.lower()
        try: static_items = LaTeXDB.static_proposals[word_lower]
        except KeyError: static_items = list()
        dynamic_items = LaTeXDB.get_dynamic_proposals(word_lower)
        if len(static_items) > 0 and len(dynamic_items) > 4:
            items = dynamic_items[:5] + static_items + dynamic_items[5:]
        else:
            items = dynamic_items + static_items

        if top_item == None: return items
        result = []
        for item in items:
            if item['command'] == top_item:
                result.insert(0, item)
            else:
                result.append(item)
        return result

    def generate_static_proposals():
        commands = LaTeXDB.get_commands()
        LaTeXDB.static_proposals = dict()
        for command in commands.values():
            if not command['lowpriority']:
                for i in range(2, len(command['command']) + 1):
                    if not command['command'][0:i].lower() in LaTeXDB.static_proposals:
                        LaTeXDB.static_proposals[command['command'][0:i].lower()] = []
                    if len(LaTeXDB.static_proposals[command['command'][0:i].lower()]) < 20:
                        LaTeXDB.static_proposals[command['command'][0:i].lower()].append(command)
        for command in commands.values():
            if command['lowpriority']:
                for i in range(2, len(command['command']) + 1):
                    if not command['command'][0:i].lower() in LaTeXDB.static_proposals:
                        LaTeXDB.static_proposals[command['command'][0:i].lower()] = []
                    if len(LaTeXDB.static_proposals[command['command'][0:i].lower()]) < 20:
                        LaTeXDB.static_proposals[command['command'][0:i].lower()].append(command)

    def get_commands():
        commands = dict()
        for filename in ['additional.xml', 'latex-document.xml', 'dynamic.xml', 'tex.xml', 'textcomp.xml', 'graphicx.xml', 'latex-dev.xml', 'amsmath.xml', 'amsopn.xml', 'amsbsy.xml', 'amsfonts.xml', 'amssymb.xml', 'amsthm.xml', 'color.xml', 'url.xml', 'geometry.xml', 'glossaries.xml', 'beamer.xml', 'hyperref.xml']:
            tree = ET.parse(os.path.join(LaTeXDB.resources_path, 'latexdb', 'commands', filename))
            root = tree.getroot()
            for child in root:
                attrib = child.attrib
                commands[attrib['name']] = {'command': attrib['text'], 'description': _(attrib['description']), 'lowpriority': True if attrib['lowpriority'] == "True" else False, 'dotlabels': attrib['dotlabels']}
        return commands

    def get_dynamic_proposals(word):
        documents = []

        # 使用 init 时预编译的 ref/cite 正则，避免每次按键重新构建字符串 +
        # 重新查表。原实现每次都做 '|' + re.escape + .replace + 哈希查表。
        ref_match = LaTeXDB._ref_regex.match(word) if LaTeXDB._ref_regex is not None else None
        cite_match = LaTeXDB._cite_regex.match(word) if LaTeXDB._cite_regex is not None else None
        if ref_match != None:
            key = 'labels'
            matching = ref_match
        elif cite_match != None:
            key = 'bibitems'
            matching = cite_match
        else:
            return list()

        commands = list()
        # 跨文件去重：单文件内 labels/bibitems 已是 set（见 parse_latex_file/
        # parse_bibtex_file），但同一 label 可能同时出现在 master.tex 和
        # chapter1.tex 的 \label{sec:intro} 中——不去重会在补全列表显示重复项。
        seen = set()
        prefix = matching.group(1)
        for file in LaTeXDB.files.values():
            for value in file[key]:
                command = prefix + '{' + value + '}'
                if command.startswith(word) and command not in seen:
                    seen.add(command)
                    commands.append({'command': command, 'description': '', 'lowpriority': False, 'dotlabels': ''})
        # 按字母排序（大小写不敏感）：大文档可能有数百个 label，无序时用户难定位；
        # 排序后用户可按字母顺序快速跳转。casefold 比 lower 更适合 Unicode，
        # 但 LaTeX label 几乎都是 ASCII，lower 足够且更快。
        commands.sort(key=lambda item: item['command'].lower())
        return commands

    def parse_included_files():
        # 直接读 ServiceLocator.workspace 属性，而非调用 get_workspace()：
        # 后者在 workspace 尚未注入时会主动发出 RuntimeWarning（init-order
        # bug 提示）。此处只是早期守护，workspace 为 None 时安全跳过，
        # 不应制造噪声。
        workspace = ServiceLocator.workspace
        if workspace == None: return

        def get_file_dict(filename):
            if filename in LaTeXDB.files:
                return LaTeXDB.files[filename]
            else:
                return {'last_parse': -1, 'bibitems': list(), 'labels': list(), 'includes': list()}

        files = dict()
        for document in ServiceLocator.get_workspace().open_documents:
            if document.get_filename() != None:
                files[document.get_filename()] = get_file_dict(document.get_filename())
                files[document.get_filename()]['includes'] = list()

                dirname = document.get_dirname()
                for filename, offset in document.parser.symbols['included_latex_files']:
                    filename = path_helpers.get_abspath(filename, dirname)
                    files[document.get_filename()]['includes'].append(filename)
                    files[filename] = get_file_dict(filename)
                for filename in document.parser.symbols['bibliographies']:
                    filename = path_helpers.get_abspath(filename, dirname)
                    files[document.get_filename()]['includes'].append(filename)
                    files[filename] = get_file_dict(filename)
        LaTeXDB.files = files

        for filename, file_dict in LaTeXDB.files.items():
            # 单次 os.stat 替代原 isfile + getmtime 双 stat：FileNotFoundError 即
            # 文件不存在，st_mtime 兼用。last_parse 改记 mtime 而非 time.time()，
            # 消除「当前时间 vs 文件 mtime」的时序窗口——若文件在 stat 后立即被改，
            # 原实现 time.time() > last_modified 会跳过下次解析导致补全过期。
            try:
                st = os.stat(filename)
            except FileNotFoundError:
                continue
            if file_dict['last_parse'] < st.st_mtime:
                if filename.endswith('.tex'):
                    LaTeXDB.parse_latex_file(filename)
                elif filename.endswith('.bib'):
                    LaTeXDB.parse_bibtex_file(filename)
                LaTeXDB.files[filename]['last_parse'] = st.st_mtime

    def parse_latex_file(pathname):
        with open(pathname, 'r') as f:
            text = f.read()
        labels = set()
        bibitems = set()
        latex_parser_regex = ServiceLocator.get_regex_object(r'\\(label|include|input|bibliography|addbibresource)\{((?:\s|\w|\:|\.|,)*)\}|\\(usepackage)(?:\[.*\]){0,1}\{((?:\s|\w|\:|,)*)\}|\\(bibitem)(?:\[.*\]){0,1}\{((?:\s|\w|\:)*)\}')
        for match in latex_parser_regex.finditer(text):
            if match.group(1) == 'label':
                labels.add(match.group(2).strip())
            elif match.group(5) == 'bibitem':
                bibitems.add(match.group(6).strip())

        LaTeXDB.files[pathname]['bibitems'] = bibitems
        LaTeXDB.files[pathname]['labels'] = labels

    def parse_bibtex_file(pathname):
        with open(pathname, 'r') as f:
            db = bibtexparser.load(f)
        bibitems = set()
        for match in db.entries:
            bibitems.add(match['ID'])

        LaTeXDB.files[pathname]['bibitems'] = bibitems

    def get_languages_dict():
        if LaTeXDB.languages_dict == None:
            LaTeXDB.languages_dict = dict()

            resources_path = ServiceLocator.get_resources_path()
            tree = ET.parse(os.path.join(resources_path, 'latexdb', 'languages', 'languages.xml'))
            root = tree.getroot()
            for child in root:
                attrib = child.attrib
                # 语言名使用各语言原生自名（endonym），不再经 gettext 翻译到
                # 某一种界面语言。name 仅用于显示，babel 实际参数由 code 决定。
                LaTeXDB.languages_dict[attrib['code']] = attrib['name']

        return LaTeXDB.languages_dict

    def get_packages_dict():
        if LaTeXDB.packages_dict == None:
            LaTeXDB.packages_dict = dict()

            resources_path = ServiceLocator.get_resources_path()
            tree = ET.parse(os.path.join(resources_path, 'latexdb', 'packages', 'general.xml'))
            root = tree.getroot()
            for child in root:
                attrib = child.attrib
                LaTeXDB.packages_dict[attrib['name']] = {'command': attrib['text'], 'description': _(attrib['description'])}
        return LaTeXDB.packages_dict
