#!/usr/bin/env python3
# coding: utf-8

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

'''反向同步链路的可选诊断输出（NEOSETZER_SYNC_DEBUG=1）。

PDF→源码的跳转在四五个地方都可能静默 return（layout 为空、页号越界、
can_sync 未就绪、点击被链接拦截），从界面上看全是同一个表现："点了没反应"。
没有输出就只能靠猜是哪一层。默认完全关闭，不产生任何开销以外的行为。
'''

import os
import sys

_ENABLED = os.environ.get('NEOSETZER_SYNC_DEBUG') == '1'


def sync_debug(message):
    if _ENABLED:
        print('setzer[sync]: ' + message, file=sys.stderr)
