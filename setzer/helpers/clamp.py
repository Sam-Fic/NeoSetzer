# Copyright (C) 2026-present Sam-Fic
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Adw.Clamp 限宽档位（docs/ui-guidelines.md §5.2）。

限宽值属于内容宽度锚点，不进 `--setzer-spacing-*` 六档标度。全库收敛为
三类：官方默认（`Adw.Clamp()` 空构造即 600/400，不写常量）、通用档位
（本模块，NARROW / WIDE）、锚点驱动的特殊值（就地手写并注释，如欢迎页
520 与按钮堆叠 breakpoint 协调）。'''

# 紧凑列表/弹层：对齐 libadwaita demo 的 Boxed Lists 页（先例 preamble 弹窗）。
NARROW = 400
NARROW_THRESHOLD = 300

# 并排双列布局：由并排算术推导，装进更宽的 wizard 对话框
# （先例 document_class 页：348 + 366 + 24 = 738，840px 对话框）。
WIDE = 760
WIDE_THRESHOLD = 520
