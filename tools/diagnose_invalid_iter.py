#!/usr/bin/env python3
# coding: utf-8
'''诊断工具：捕获 GTK "Invalid text buffer iterator" 警告并打印 Python 调用栈。

用法（在仓库根目录）：
    python3 tools/diagnose_invalid_iter.py

行为与 scripts/dev/setzer.dev 完全一致（运行 builddir/setzer_dev.py），
只是在启动前给 Gtk / GtkSourceView 日志域安装了 GLib 日志钩子。
当出现失效迭代器警告时，会在 stderr 打印触发它的完整调用栈，
方便定位到底是哪段代码在 buffer 修改后复用了旧的 Gtk.TextIter。
'''

import sys
import os
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True
sys.path.insert(0, ROOT)

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import GLib

WARN_TEXT = 'Invalid text buffer iterator'


def _writer(level, fields, n_fields, user_data):
    '''全局日志写入器：截获所有 GLib 日志（含结构化日志）。
    命中目标警告时打印当前 Python 调用栈，随后交还默认处理。'''
    try:
        message = None
        for field in fields:
            if getattr(field, 'key', None) == 'MESSAGE':
                message = field.value
                break
    except Exception:
        message = None
    if message and WARN_TEXT in str(message):
        sys.stderr.write('\n===== [diagnose] 捕获失效迭代器警告 =====\n')
        traceback.print_stack()
        sys.stderr.write('===== [diagnose] 调用栈结束 =====\n')
        sys.stderr.flush()
    return GLib.LogWriterOutput.UNHANDLED


try:
    GLib.log_set_writer_func(_writer, None)
except Exception as error:
    sys.stderr.write('[diagnose] log_set_writer_func failed: %r\n' % (error,))
    sys.stderr.flush()

    def _log_handler(domain, level, message):
        if WARN_TEXT in message:
            sys.stderr.write('\n===== [diagnose] 捕获失效迭代器警告 (domain=%s) =====\n' % domain)
            traceback.print_stack()
            sys.stderr.write('===== [diagnose] 调用栈结束 =====\n')
            sys.stderr.flush()

    for _domain in ('Gtk', 'GtkSourceView'):
        GLib.log_set_handler(
            _domain,
            GLib.LogLevelFlags.LEVEL_WARNING | GLib.LogLevelFlags.LEVEL_CRITICAL,
            _log_handler,
        )

def _buffer_probe():
    '''每 3 秒打印每个打开文档的缓冲区字符数与修改标记，
    用于确认模拟键盘输入是否真正写入了缓冲区。'''
    try:
        from setzer.app.service_locator import ServiceLocator
        workspace = ServiceLocator.get_workspace()
        if workspace is not None:
            documents = getattr(workspace, 'open_documents', []) or []
            states = []
            for document in documents:
                buffer = getattr(document, 'source_buffer', None)
                if buffer is not None:
                    states.append('%d%s' % (buffer.get_char_count(),
                                            '*' if buffer.get_modified() else ''))
            sys.stderr.write('[probe] buffers=%s\\n' % ','.join(states))
            sys.stderr.flush()
    except Exception:
        pass
    return True


# 必须在导入 setzer_dev（其模块级代码会启动主循环）之前注册定时器。
GLib.timeout_add(3000, _buffer_probe)


_STRESS = {'chars': 'abcdefghij', 'i': 0, 'count': 0}


def _stress_tick():
    '''程序化模拟用户输入：向活动文档缓冲区逐字符插入文本，
    并同步设置结构侧边栏过滤词，复刻“打字 → 解析 → 侧边栏刷新”链路。'''
    try:
        from setzer.app.service_locator import ServiceLocator
        workspace = ServiceLocator.get_workspace()
        if workspace is None:
            return True
        document = getattr(workspace, 'active_document', None)
        if document is not None:
            buffer = document.source_buffer
            char = _STRESS['chars'][_STRESS['i'] % len(_STRESS['chars'])]
            _STRESS['i'] += 1
            buffer.insert_at_cursor(char)
            _STRESS['count'] += 1
            sys.stderr.write('[stress] insert #%d %r len=%d\\n'
                             % (_STRESS['count'], char, buffer.get_char_count()))
            sys.stderr.flush()
            # 同步驱动侧边栏过滤（触发 highlight markup 行重建路径）
            try:
                window = ServiceLocator.get_main_window()
                page = getattr(window, 'document_structure_page', None)
                if page is not None and hasattr(page, 'filter_sections'):
                    page.filter_sections('a')
            except Exception:
                pass
    except Exception as error:
        sys.stderr.write('[stress] error: %r\\n' % (error,))
        sys.stderr.flush()
    return _STRESS['count'] < 30


GLib.timeout_add(400, _stress_tick)

from builddir import setzer_dev  # noqa: E402  导入即启动应用


