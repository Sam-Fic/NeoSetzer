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

"""调用外部 Agent CLI 的运行器。

仅保留有头模式（`run_headed`）：在主线程同步启动外部终端窗口跑 Agent 的
交互式 TUI。`start_new_session=True` 让终端脱离 Setzer 进程组，Setzer 退出
不杀终端。组装方式因终端而异（见 TERMINAL_CHAIN 注释）。Flatpak 下用
flatpak-spawn --host 包裹。

无头模式（后台 subprocess）已移除：Agent 在后台直接修改文件存在安全风险
（用户看不到过程、无法审批）。有头模式让用户在终端里看到 Agent 的每一步
操作，更安全。文件重载由 Setzer 现有的 2 秒轮询机制
（auto_reload_on_external_change）自动完成。

模板渲染：把 `{prompt}` / `{file}` / `{cwd}` 占位符替换为实际值后整体作为列表
传给 Popen（不用 shell=True，避免转义与注入问题）。

剪贴板兜底：仅当自定义工具的 headed_template 不含 `{prompt}` 时，把 prompt
复制到剪贴板并返回提示串，由 controller 侧 Toast 展示。
"""

import os
import shlex
import shutil
import subprocess

from setzer.ai_fix.presets import PLACEHOLDER_PROMPT, PLACEHOLDER_FILE, PLACEHOLDER_CWD


# 终端检测链：按优先级尝试。
#
# 每个 spec 支持以下字段（均可选，缺省 None）：
#   - sep:         终端与要执行的命令之间的分隔符。
#                  gnome-terminal 系用 '--'；xterm/urxvt 系用 '-e'；
#                  xfce4-terminal 用 '-x'。
#   - workdir_arg: 显式指定工作目录的 flag。
#                    None  → 不支持，靠 Popen 的 cwd 参数继承
#                    str   → 生成 '--flag=<cwd>'（等号格式，GNOME/GTK 系）
#                    tuple → 生成 '--flag' '<cwd>'（空格格式，kitty/konsole 等）
#   - prefix:      终端名与子命令之间的前缀参数列表。
#                  wezterm 需要 ['start'] 才能进入启动模式。
#   - extra_args:  默认 None。detect_terminal 对用户自定义命令会注入额外参数。
#
# 命令格式参考（2026-07 web 核实）：
#   gnome-terminal --working-directory=/tmp -- cmd arg
#   kgx            --working-directory=/tmp -- cmd arg
#   ptyxis         --working-directory=/tmp -- cmd arg
#   blackbox       --working-directory=/tmp -- cmd arg
#   tilix          --working-directory=/tmp -e cmd arg
#   mate-terminal  --working-directory=/tmp -- cmd arg
#   xfce4-terminal --working-directory=/tmp -x cmd arg
#   terminator     --working-directory=/tmp -x cmd arg
#   alacritty      --working-directory=/tmp -e cmd arg
#   kitty          --directory=<dir> -- cmd arg   （--directory 也接受空格格式）
#   wezterm        start --cwd=<dir> -- cmd arg
#   foot           --working-directory=<dir> -- cmd arg
#   konsole        --workdir <dir> -e cmd arg
#   qterminal      -e cmd arg                      （不支持 workdir flag）
#   xterm          -e cmd arg
#   st             -e cmd arg
#   urxvt / rxvt   -e cmd arg
#   lxterminal     -e cmd arg
#   terminology    -e cmd arg
#   sakura         -e cmd arg
#   cool-retro-term -e cmd arg
TERMINAL_CHAIN = [
    # === GNOME 系：--working-directory=<dir> + -- ===
    ('gnome-terminal',   {'sep': '--', 'workdir_arg': '--working-directory'}),
    ('ptyxis',           {'sep': '--', 'workdir_arg': '--working-directory'}),
    ('kgx',              {'sep': '--', 'workdir_arg': '--working-directory'}),
    ('blackbox',         {'sep': '--', 'workdir_arg': '--working-directory'}),

    # === GTK 系：--working-directory=<dir> + -e/-x/-- ===
    ('tilix',            {'sep': '-e', 'workdir_arg': '--working-directory'}),
    ('mate-terminal',    {'sep': '--', 'workdir_arg': '--working-directory'}),
    ('xfce4-terminal',   {'sep': '-x', 'workdir_arg': '--working-directory'}),
    ('terminator',       {'sep': '-x', 'workdir_arg': '--working-directory'}),

    # === 现代 GPU/独立终端 ===
    ('alacritty',        {'sep': '-e', 'workdir_arg': '--working-directory'}),
    ('kitty',            {'sep': '--', 'workdir_arg': '--directory'}),
    ('wezterm',          {'sep': '--', 'workdir_arg': '--cwd', 'prefix': ['start']}),
    ('foot',             {'sep': '--', 'workdir_arg': '--working-directory'}),

    # === KDE 系 ===
    ('konsole',          {'sep': '-e', 'workdir_arg': ('--workdir',)}),
    ('qterminal',        {'sep': '-e', 'workdir_arg': None}),

    # === 极简系（不支持 workdir flag，靠 Popen cwd 继承）===
    ('xterm',            {'sep': '-e', 'workdir_arg': None}),
    ('st',               {'sep': '-e', 'workdir_arg': None}),
    ('urxvt',            {'sep': '-e', 'workdir_arg': None}),
    ('rxvt',             {'sep': '-e', 'workdir_arg': None}),
    ('lxterminal',       {'sep': '-e', 'workdir_arg': None}),
    ('terminology',      {'sep': '-e', 'workdir_arg': None}),
    ('sakura',           {'sep': '-e', 'workdir_arg': None}),
    ('cool-retro-term',  {'sep': '-e', 'workdir_arg': None}),
]


def is_flatpak():
    '''检测是否运行在 Flatpak 沙盒内。

    缓存结果避免重复 introspection。范式见 page_build_system.py:255-262。
    '''
    if hasattr(is_flatpak, '_cached'):
        return is_flatpak._cached
    result = False
    try:
        import gi
        gi.require_version('Xdp', '1.0')
        from gi.repository import Xdp
        result = bool(Xdp.Portal().running_under_flatpak())
    except (ValueError, ImportError, Exception):
        # Xdp 不可用或调用失败，回退到环境变量探测
        result = os.environ.get('FLATPAK_ID', '') != '' or os.path.exists('/.flatpak-info')
    is_flatpak._cached = result
    return result


def _which_on_host(name, timeout=3):
    '''跨沙盒 which：非 Flatpak 用 shutil.which；Flatpak 下通过
    flatpak-spawn --host which 探测 host 系统 PATH。

    返回 host 系统上可执行文件的绝对路径，或 None。
    '''
    if not name:
        return None
    if not is_flatpak():
        return shutil.which(name)

    # Flatpak：沙盒内 shutil.which 看不到 host 的 /usr/bin，必须用
    # flatpak-spawn --host which <name> 探测。部分 portal 实现可能
    # 没 which，失败后回退到常见绝对路径试探。
    flatpak_spawn = shutil.which('flatpak-spawn')
    if flatpak_spawn:
        try:
            result = subprocess.run(
                [flatpak_spawn, '--host', 'which', name],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=timeout, text=True)
            if result.returncode == 0:
                path = result.stdout.strip().splitlines()[0]
                if path:
                    return path
        except Exception:
            pass

    # 兜底：按常见绝对路径猜（deb 系 /usr/bin 通常就在这些位置）
    for prefix in ['/usr/bin', '/usr/local/bin', '/app/bin', '/bin']:
        candidate = os.path.join(prefix, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def detect_terminal(user_terminal_cmd=None):
    '''检测可用终端，返回 (terminal_executable, spec_dict) 或 None。

    优先级：
      1. 用户在 Preferences 显式指定的 terminal_cmd（若可执行）
         支持带参数，如 'gnome-terminal --maximize'。
      2. 自动检测链（gnome-terminal → ptyxis → kgx → ... → xterm）

    Returns:
        tuple (terminal_path, spec) 或 None（全部不可用）。
        terminal_path 是终端可执行文件的绝对路径（Flatpak 下为 host 路径）。
        spec 可能含 extra_args 字段（用户自定义命令的额外参数）。
    '''
    if user_terminal_cmd:
        # 用 shlex split 处理带参数的自定义终端命令：
        #   'gnome-terminal --maximize' → ['gnome-terminal', '--maximize']
        # 第一个 token 是终端名，其余作为额外参数注入 spec.extra_args。
        parts = shlex.split(user_terminal_cmd)
        if parts:
            name = parts[0]
            extra_args = parts[1:]
            path = _which_on_host(name)
            if path:
                # 用户指定的终端若在已知 spec 表里，复用其 sep/workdir_arg；
                # 否则默认用 '--' 分隔（对大多数现代终端安全）。
                spec = next((dict(s) for n, s in TERMINAL_CHAIN if n == name),
                            {'sep': '--', 'workdir_arg': None})
                if extra_args:
                    spec['extra_args'] = extra_args
                return (path, spec)

    for name, spec in TERMINAL_CHAIN:
        path = _which_on_host(name)
        if path:
            return (path, dict(spec))
    return None


def check_tool_available(tool_config):
    '''检测工具可执行文件是否在 PATH 中。供 Preferences 页可用性提示使用。

    注意：Agent CLI 通常安装在 host 系统，Flatpak 下必须跨沙盒探测。
    '''
    executable = tool_config.get('executable')
    if not executable:
        return False
    return _which_on_host(executable) is not None


def render_template(template, prompt, filename, cwd):
    '''把模板里的占位符替换为实际值，返回新列表（不改原模板）。

    Args:
        template: list[str]，如 ['opencode', 'run', '{prompt}']。
        prompt: str，组装好的 prompt 文本。
        filename: str 或 None，活动文档路径。
        cwd: str 或 None，工作目录。

    Returns:
        list[str]，每个元素的 {prompt}/{file}/{cwd} 被替换。
    '''
    out = []
    for arg in template:
        if not isinstance(arg, str):
            out.append(arg)
            continue
        s = arg
        if PLACEHOLDER_PROMPT in s:
            s = s.replace(PLACEHOLDER_PROMPT, prompt or '')
        if PLACEHOLDER_FILE in s:
            s = s.replace(PLACEHOLDER_FILE, filename or '')
        if PLACEHOLDER_CWD in s:
            s = s.replace(PLACEHOLDER_CWD, cwd or '')
        out.append(s)
    return out


def run_headed(tool_config, prompt, cwd, filename, terminal_cmd=None):
    '''有头模式：启动外部终端跑 Agent 交互式 TUI。

    主线程同步执行（终端自身接管交互，不阻塞 Setzer）。

    Args:
        tool_config: dict，含 headed_template 等。
        prompt: str，组装好的 prompt。
        cwd: str，工作目录。
        filename: str 或 None，活动文档路径。
        terminal_cmd: 用户指定的终端命令（覆盖自动检测），可带参数。

    Returns:
        tuple (success:bool, message:str)。
        message 含给用户的提示（如剪贴板兜底说明 / 失败原因 / 启动成功提示）。
    '''
    template = tool_config.get('headed_template', [])
    rendered = render_template(template, prompt, filename, cwd)

    # 剪贴板兜底：自定义工具模板不含 {prompt} 时，复制 prompt 并提示用户粘贴
    needs_clipboard_fallback = not any(
        PLACEHOLDER_PROMPT in arg for arg in template if isinstance(arg, str)
    )
    if needs_clipboard_fallback:
        try:
            from gi.repository import Gdk
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(prompt or '')
        except Exception:
            pass  # 剪贴板失败也不阻止启动

    terminal_info = detect_terminal(terminal_cmd)
    if terminal_info is None:
        return (False, _('No terminal emulator available. '
                          'Install gnome-terminal / xterm or set a custom terminal in Preferences.'))

    terminal_path, spec = terminal_info
    sep = spec.get('sep', '--')
    workdir_arg = spec.get('workdir_arg')
    prefix = spec.get('prefix')           # 如 wezterm 的 ['start']
    extra_args = spec.get('extra_args')   # 用户自定义终端命令的额外参数

    # 组装最终命令：
    #   [flatpak-spawn --host] + [terminal] + [prefix?] + [extra_args?] +
    #   [workdir?] + [sep] + [agent_cmd...]
    final_cmd = []
    if is_flatpak():
        flatpak_spawn = shutil.which('flatpak-spawn')
        if flatpak_spawn:
            final_cmd.extend([flatpak_spawn, '--host'])

    final_cmd.append(terminal_path)

    # prefix：终端自身需要的子命令（如 wezterm 的 'start'）
    if prefix:
        final_cmd.extend(prefix)

    # 用户自定义终端命令的额外参数（如 'gnome-terminal --maximize' 中的 --maximize）
    if extra_args:
        final_cmd.extend(extra_args)

    # 工作目录
    if workdir_arg and cwd:
        # workdir_arg 支持两种写法：
        #   - str:  生成 '--flag=<cwd>'（等号，GNOME/GTK 系）
        #   - tuple: 生成 '--flag' '<cwd>'（空格，kitty/konsole 等）
        if isinstance(workdir_arg, (list, tuple)):
            final_cmd.extend(list(workdir_arg))
            final_cmd.append(cwd)
        else:
            final_cmd.extend([workdir_arg + '=' + cwd])

    final_cmd.append(sep)
    final_cmd.extend(rendered)

    try:
        # start_new_session=True：脱离 Setzer 进程组，Setzer 退出不杀终端
        # DEVNULL：避免管道死锁（终端不读 stdin/stdout 也无碍）
        subprocess.Popen(
            final_cmd, cwd=cwd or None,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return (False, _('Terminal executable not found: {}').format(terminal_path))
    except Exception as e:
        return (False, _('Failed to launch terminal: {}').format(str(e)))

    tool_name = tool_config.get('name', 'agent')
    if needs_clipboard_fallback:
        return (True, _('Launched {} in terminal. Prompt copied to clipboard — paste it there.').format(tool_name))
    return (True, _('Launched {} in terminal.').format(tool_name))


def _(s):
    '''占位 i18n 钩子。本模块刻意不直接依赖 gettext（避免循环依赖），
    controller 侧若需本地化可重写本模块的 _ 引用。'''
    return s
