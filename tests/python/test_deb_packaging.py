#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

'''Debian 包元数据守卫（无界面源码测试）。

锁住 `build-deb.yml` 里三条容易回退的约束：

1. `Depends` 带版本约束，与代码实际使用的 API 对应。缺少约束时旧系统
   会出现「apt 装成功、启动即 AttributeError」；
2. `gir1.2-webkit-6.0` 只作为 Recommends（帮助面板可降级），不能回到 Depends；
3. 打包后必须在干净容器里安装并启动一次，且该步骤位于上传资产之前。

升级依赖门槛时同步更新本文件与 docs/packaging/debian.md 的「依赖模型」一节。
'''

import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/build-deb.yml'


class TestDebPackaging(unittest.TestCase):
    def setUp(self):
        self.body = WORKFLOW.read_text(encoding='utf-8')

    def control_field(self, name):
        match = re.search(rf'^\s*{name}: (.+)$', self.body, re.MULTILINE)
        self.assertIsNotNone(match, f'control 元数据缺少 {name} 字段')
        return match.group(1)

    def test_depends_pins_api_versions(self):
        depends = self.control_field('Depends')
        for requirement in [
            'python3 (>= 3.10)',
            'gir1.2-gtk-4.0 (>= 4.10)',
            'gir1.2-adw-1 (>= 1.9)',
            'gir1.2-gtksource-5 (>= 5.4)',
        ]:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, depends)

    def test_webkit_is_optional(self):
        self.assertNotIn('gir1.2-webkit-6.0', self.control_field('Depends'))
        self.assertIn('gir1.2-webkit-6.0', self.control_field('Recommends'))

    def test_smoke_test_runs_before_upload(self):
        smoke = self.body.find('Smoke test package in a clean Ubuntu 26.04 container')
        upload = self.body.find('Upload Debian package artifact')
        self.assertNotEqual(smoke, -1, '缺少干净容器冒烟测试步骤')
        self.assertLess(smoke, upload, '冒烟测试必须在上传资产之前')

        step = self.body[smoke:upload]
        self.assertIn('ubuntu:26.04', step)
        self.assertIn('--no-install-recommends', step)
        self.assertIn('setzer --version', step)
        self.assertIn('xvfb-run', step)


if __name__ == '__main__':
    unittest.main()
