#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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


class Observable(object):
    ''' Can send observers messages if the inheriting class has
        changed. Observers can register with the classes and
        get change notifications pushed to them. '''

    def __init__(self):
        self.connected_functions = dict()

    def add_change_code(self, change_code, parameter=None):
        ''' Observables call this method to notify observers of
            changes in their states. '''

        callbacks = self.connected_functions.get(change_code)
        if not callbacks:
            return
        # 拷贝一份再遍历：回调中常见的副作用是 connect/disconnect（例如
        # workspace_presenter 在收到 'new_active_document' 后才连新文档的信号、
        # build_log 在 'build_log_update' 后断开旧文档连接）。原实现直接迭代
        # set，回调内一旦改集合就抛 RuntimeError: Set changed size during
        # iteration，导致后续回调被吞掉且整个通知链中断。tuple() 拷贝 O(k)
        # 仅与已注册观察者数量相关，远小于一次回调本身的代价；不可变更快于 list。
        import time as _t, sys as _sys, os as _os, traceback as _tb
        _timing = _os.environ.get('SETZER_PROFILE') and change_code in ('new_active_document', 'new_document', 'new_inactive_document')
        for callback in tuple(callbacks):
            # 单个回调异常不能中断通知链：例如 10 个观察者监听 settings_changed，
            # 第 3 个因 KeyError 崩溃不应让第 4-10 个收不到通知——否则会导致
            # UI 状态不一致（某面板主题色未更新、build_log 未刷新等）。打印
            # traceback 到 stderr 便于诊断；不弹窗——Observable 是底层基础设施，
            # 不应假设有 UI 可交互，且 notify 可能发生在窗口构造早期。
            try:
                if _timing:
                    _s = _t.perf_counter()
                if parameter is not None:
                    callback(self, parameter)
                else:
                    callback(self)
                if _timing:
                    _d = (_t.perf_counter() - _s) * 1000
                    if _d > 1:
                        print(f'[TIMING] observer {change_code} -> {callback.__qualname__}: {_d:.1f}ms', file=_sys.stderr)
            except Exception:
                print(f'[Observable] callback {getattr(callback, "__qualname__", callback)!r} for {change_code!r} raised:', file=_sys.stderr)
                _tb.print_exc(file=_sys.stderr)

    def connect(self, change_code, callback):
        if change_code in self.connected_functions:
            self.connected_functions[change_code].add(callback)
        else:
            self.connected_functions[change_code] = {callback}

    def disconnect(self, change_code, callback):
        if change_code in self.connected_functions:
            self.connected_functions[change_code].discard(callback)
            if len(self.connected_functions[change_code]) == 0:
                del(self.connected_functions[change_code])


