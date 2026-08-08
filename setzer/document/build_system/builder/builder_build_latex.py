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

import os
import os.path
import sys
import shutil
import subprocess
import shlex
import threading
import time
from operator import itemgetter

import setzer.document.build_system.builder.builder_build as builder_build
import setzer.document.build_system.latex_log_parser.latex_log_parser as latex_log_parser
from setzer.app.service_locator import ServiceLocator
from setzer.helpers.synctex_folder import synctex_folder


class BuilderBuildLaTeX(builder_build.BuilderBuild):

    def __init__(self):
        builder_build.BuilderBuild.__init__(self)

        self.config_folder = ServiceLocator.get_config_folder()
        self.latex_log_parser = latex_log_parser.LaTeXLogParser()

    def run(self, query):
        build_command_defaults = dict()
        build_command_defaults['pdflatex'] = 'pdflatex -synctex=1 -interaction=nonstopmode'
        build_command_defaults['xelatex'] = 'xelatex -synctex=1 -interaction=nonstopmode'
        build_command_defaults['lualatex'] = 'lualatex --synctex=1 --interaction=nonstopmode'
        build_command_defaults['tectonic'] = 'tectonic --synctex --keep-logs'

        latex_interpreter = query.build_data['latex_interpreter']
        if latex_interpreter == 'tectonic':
            build_command = build_command_defaults[latex_interpreter]
            build_command += ' --outdir "' + os.path.dirname(query.tex_filename) + '" "' 
        elif query.build_data['use_latexmk']:
            if latex_interpreter == 'pdflatex':
                interpreter_option = 'pdf'
            else:
                interpreter_option = latex_interpreter
            build_command = 'latexmk -' + interpreter_option + ' -synctex=1 -interaction=nonstopmode'
            build_command += query.build_data['additional_arguments']
            build_command += ' -output-directory="' + os.path.dirname(query.tex_filename) + '" "'
        else:
            build_command = build_command_defaults[latex_interpreter]
            build_command += query.build_data['additional_arguments']
            build_command += ' -output-directory="' + os.path.dirname(query.tex_filename) + '" "'
        build_command += query.tex_filename + '"'

        try:
            self.process = self._spawn_process(build_command, os.path.dirname(query.tex_filename))
        except (FileNotFoundError, OSError):
            self.cleanup_files(query)
            self.throw_build_error(query, 'interpreter_missing', latex_interpreter)
            return

        self._watch_process()

        # parse results
        try:
            if self.parse_build_log(query):
                return
        except FileNotFoundError as e:
            self.cleanup_files(query)
            self.throw_build_error(query, 'interpreter_not_working', 'log file missing')
            return

        try:
            query.can_sync = self.copy_synctex_file(query)
        except OSError as e:
            # synctex 缓存复制失败（如长文件名导致 OSError: [Errno 36]
            # File name too long）不得中断构建或让 UI 卡死：synctex 仅用于
            # 正向/反向同步，PDF 本身已成功。吞掉异常并标记不可同步，让
            # 构建结果照常返回主线程（否则 _on_query_done 永不被调度，
            # 编译计数器无限增长 = soft-hang）。
            print('Setzer: failed to copy synctex file ({}); sync disabled for this build.'.format(e))
            query.can_sync = False
        self.cleanup_files(query)

        pdf_filename = os.path.splitext(query.tex_filename)[0] + '.pdf'
        if query.error_count > 0:
            if os.path.isfile(pdf_filename):
                os.remove(pdf_filename)
            pdf_filename = None
        # 修复：LaTeX 日志解析器只匹配 `!` 错误行，但 xelatex 的 xdvipdfmx
        # / lualatex 的 fontloader 等子工具链 fatal 错误不会写 `!` 错误
        # （只在 stdout 写 "fatal: ..." 之类），导致 error_count==0 但 PDF
        # 实际没生成/损坏，setzer 误报"成功"。两种 edge case 都覆盖：
        #   1) PDF 文件不存在
        #   2) PDF 文件存在但是空 / 不是合法 PDF（被中断留下的残骸
        #      如 2910 字节无 trailer dictionary）
        # 都合成一个 error 让上层走 Build failed 路径，否则会出现
        # "PDF 预览区 toast 说 build failed, showing previous version"
        # 与"主窗口 toast 说 Build succeeded"互相矛盾的尴尬情况。
        elif not self._is_pdf_valid(pdf_filename):
            # 清理残骸，避免下次 build 之前被 Poppler 当成有效 PDF 读
            if os.path.isfile(pdf_filename):
                try: os.remove(pdf_filename)
                except OSError: pass
            query.error_count = 1
            synthesized_msg = self._extract_silent_failure_reason(query)
            # log_messages 项的格式必须与 latex_log_parser.parse_build_log
            # 输出一致：error/warning/badbox 列表中的元素是
            # (error_type_or_None, line_number, text) tuple。set_build_log_items
            # 会读 item[1] (line_number) / item[2] (text)。之前写成 dict
            # 导致 set_build_log_items 在 item[1] 处抛 KeyError: 1。
            query.log_messages = {
                query.tex_filename: {
                    'error': [(None, -1, synthesized_msg)],
                    'warning': [],
                    'badbox': [],
                }
            }
            pdf_filename = None

        with query.build_result_lock:
            query.build_result = {'pdf_filename': pdf_filename, 
                                  'has_synctex_file': query.can_sync,
                                  'log_messages': query.log_messages,
                                  'bibtex_log_messages': query.bibtex_log_messages,
                                  'error': None,
                                  'error_arg': None}

    def _spawn_process(self, build_command, cwd):
        '''跨平台启动 LaTeX 构建进程。

        Unix 用 shlex.split 拆分为参数列表（避免 shell=True 的注入风险）；
        Windows 用 shell=True（cmd.exe 能正确解析双引号包裹的路径）并设
        CREATE_NO_WINDOW 避免弹出控制台窗口。
        '''
        if sys.platform == 'win32':
            return subprocess.Popen(
                build_command, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                shell=True, creationflags=subprocess.CREATE_NO_WINDOW,
                bufsize=1, text=True, encoding='utf-8', errors='replace')
        else:
            return subprocess.Popen(
                shlex.split(build_command), cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, text=True, encoding='utf-8', errors='replace')

    def _watch_process(self):
        '''监控构建进程输出，复刻原 pexpect 逻辑：
        - 正常输出（逐行到达）→ 继续
        - 20 秒无新输出且已出现 `!` 错误行 → 终止进程（LaTeX 卡死）
        - 进程结束（EOF）→ 退出循环

        用守护线程逐行读取 stdout，主线程轮询检测停滞。

        进程对象绑定到局部变量 process，全程不再读 self.process：
        stop_running（主线程）会把 self.process 置 None，若此处仍用
        self.process.poll() 会触发 AttributeError: 'NoneType' object
        has no attribute 'poll'。局部引用指向同一进程对象，stop_running
        的 terminate() 会让 process.poll() 返回退出码，循环正常退出。'''
        process = self.process
        if process is None:
            return

        output_lines = []
        error_detected = False

        def reader():
            try:
                for line in process.stdout:
                    output_lines.append(line)
            except Exception:
                pass

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()

        last_line_count = 0
        last_change_time = time.time()
        stall_timeout = 20  # 与原 pexpect timeout 一致

        while True:
            if process.poll() is not None:
                reader_thread.join(timeout=2)
                break

            time.sleep(0.5)

            current_count = len(output_lines)
            if current_count > last_line_count:
                last_line_count = current_count
                last_change_time = time.time()
                if not error_detected:
                    for line in output_lines:
                        if line.startswith('!'):
                            error_detected = True
                            break
            elif time.time() - last_change_time > stall_timeout:
                if error_detected:
                    try:
                        process.terminate()
                        process.wait(timeout=5)
                    except Exception:
                        try: process.kill()
                        except Exception: pass
                    break
                # 无错误但停滞：重置计时器继续等（大文档编译可能较慢）
                last_change_time = time.time()

        # 确保进程结束 + 管道关闭
        try:
            process.wait(timeout=5)
        except Exception:
            try: process.kill()
            except Exception: pass
        try: process.stdout.close()
        except Exception: pass

    def stop_running(self):
        if self.process != None:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try: self.process.kill()
                except Exception: pass
            self.process = None

    def parse_build_log(self, query):
        query.log_messages = list()
        query.error_count = 0

        log_items = self.latex_log_parser.parse_build_log(query.tex_filename)
        additional_jobs = self.latex_log_parser.get_additional_jobs(log_items, query)
        file_no = 0

        for job in additional_jobs:
            query.jobs.insert(0, job)
            return True

        for filename, items in log_items.items():
            query.error_count += len(items['error'])
            items['error'].sort(key=itemgetter(1))
            items['warning'].sort(key=itemgetter(1))
            items['badbox'].sort(key=itemgetter(1))
        query.log_messages = log_items

        return False

    def copy_synctex_file(self, query):
        move_from = os.path.splitext(query.tex_filename)[0] + '.synctex.gz'
        folder = synctex_folder(self.config_folder, query.tex_filename)
        move_to = os.path.join(folder, os.path.splitext(os.path.basename(query.tex_filename))[0] + '.synctex.gz')

        if not os.path.exists(folder):
            os.makedirs(folder)

        try: shutil.copyfile(move_from, move_to)
        except (FileNotFoundError, OSError): return False
        else: return True

    def _is_pdf_valid(self, pdf_filename):
        '''轻量级 PDF 有效性检查：文件存在 + 非空 + 头部 %PDF- + 末尾 %%EOF。

        单看 %PDF- 头会被 xdvipdfmx "写完头部就崩" 的残骸骗过（实测
        2910 字节：头是 %PDF-1.7 但无 trailer dictionary）。两个标记
        一起检查，覆盖正常 PDF 必然满足的"有头有尾"。比 Poppler
        解析便宜得多（stat + read head/tail）。'''
        if not os.path.isfile(pdf_filename):
            return False
        try:
            size = os.path.getsize(pdf_filename)
            # 合法 PDF 至少几十字节（%PDF-1.7\n...%%EOF\n），低于 32 字节
            # 几乎肯定是残骸。
            if size < 32:
                return False
            with open(pdf_filename, 'rb') as f:
                head = f.read(5)
                # 末尾 1KB 内必须能找到 %%EOF（PDF spec 允许 EOF marker
                # 前有若干空白，但不会在 1KB 之外）。这样既不读全文件，
                # 又能可靠识别"写到一半被中断"的截断文件。
                f.seek(max(0, size - 1024))
                tail = f.read()
            return head == b'%PDF-' and b'%%EOF' in tail
        except OSError:
            return False

    def _extract_silent_failure_reason(self, query):
        '''engine 退出 0 但 PDF 没生成时，从 .log 末尾 / stdout 抓取错误线索。

        xelatex 的 xdvipdfmx 致命错误不会出现在 LaTeX 日志的 `!` 错误里，
        但会写进 .log 末尾（"xdvipdfmx:fatal: ..."）。在无 .log 或
        无匹配时回落到通用提示，避免用户面对一个空错误。'''
        log_path = os.path.splitext(query.tex_filename)[0] + '.log'
        try:
            with open(log_path, 'rb') as f:
                # 只读末尾 8KB 避免大日志拖累
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 8192))
                tail = f.read().decode('utf-8', errors='replace')
        except (OSError, FileNotFoundError):
            return 'No PDF file was produced. The LaTeX engine exited with code 0 but no output file was written. This often indicates a toolchain failure (e.g. xdvipdfmx/fontloader) that the LaTeX log parser does not detect.'

        # 优先级匹配：xdvipdfmx fatal > 任何 fatal > kpathsea 严重警告
        for pattern in ('xdvipdfmx:fatal', 'dvipdfmx:fatal',
                        'fatal:', 'luaotfload', 'kpathsea'):
            idx = tail.lower().rfind(pattern)
            if idx != -1:
                # 截取错误行（去首尾空白）
                line_end = tail.find('\n', idx)
                snippet = tail[idx:line_end if line_end != -1 else len(tail)].strip()
                if len(snippet) > 200:
                    snippet = snippet[:200] + '…'
                return snippet

        return 'No PDF file was produced. The LaTeX engine exited with code 0 but no output file was written. Check the build log for toolchain-level errors (xdvipdfmx, fontloader, kpathsea).'


