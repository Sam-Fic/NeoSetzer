#!/usr/bin/env python3
# coding: utf-8
#
# 引擎键与取值来源的显示文案：文档菜单与文档属性页共用，
# 避免两处各写一份、改一处漏一处。

_INTERPRETER_NAMES = {
    'pdflatex': 'PDFLaTeX',
    'xelatex': 'XeLaTeX',
    'lualatex': 'LuaLaTeX',
    'tectonic': 'Tectonic',
    'phitex': 'PhiTeX',
}


def interpreter_name(engine):
    return _INTERPRETER_NAMES.get(engine, engine)


def interpreter_source(source):
    # 文案在调用时构造：`_` 由应用启动时注入，模块导入阶段可能还不存在。
    return {
        'comment': _('from the document\u2019s % !TEX program comment'),
        'document': _('set for this document'),
        'project': _('from project configuration'),
        'global': _('from preferences'),
    }.get(source, '')
