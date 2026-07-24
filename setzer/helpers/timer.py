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

import os
import time

# 仅在 SETZER_PROFILE 环境变量非空时启用计时输出。未启用时装饰器返回原函数，
# 零开销——避免开发者解开 `#@timer` 注释后对高频函数（update_view/texcount 等）
# 产生大量 print I/O 阻塞。启用方式：SETZER_PROFILE=1 ./setzer
_PROFILING_ENABLED = bool(os.environ.get('SETZER_PROFILE', ''))


def timer(original_function):
    if not _PROFILING_ENABLED:
        return original_function

    def new_function(*args, **kwargs):
        start_time = time.time()
        return_value = original_function(*args, **kwargs)
        print(original_function.__name__ + ': ' + str(time.time() - start_time) + ' seconds')
        return return_value

    return new_function


