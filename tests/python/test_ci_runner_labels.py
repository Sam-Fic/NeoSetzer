#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

'''CI 运行器标签守卫（无界面源码测试）。

macos-14 的运行器配置（macos-14-arm64-us-default）已被 GitHub 下线：tag
触发的任务会在 1 秒内以 "configuration is not supported" 取消，发版因此拿
不到 macOS 资产，而且没有任何编译错误可看。本文件锁住两条约束：

1. macOS 工作流固定使用当前受支持的 `macos-15`；
2. 任何工作流都不得再出现 `macos-14`（防止从旧文档/旧提交里抄回来）。

升级运行器时同步更新本文件与 docs/packaging/macos.md 的「CI 运行器」一节。
'''

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / '.github/workflows'


def read_workflows():
    return {path.name: path.read_text(encoding='utf-8')
            for path in sorted(WORKFLOWS.glob('*.yml'))}


class TestMacOSRunner(unittest.TestCase):
    def setUp(self):
        self.workflows = read_workflows()

    def test_macos_job_uses_supported_runner(self):
        self.assertIn('build-macos.yml', self.workflows)
        self.assertIn('runs-on: macos-15', self.workflows['build-macos.yml'])

    def test_no_retired_macos_runner(self):
        offenders = []
        for name, body in self.workflows.items():
            for number, line in enumerate(body.splitlines(), 1):
                if line.strip().startswith('runs-on:') and 'macos-14' in line:
                    offenders.append(f'{name}:{number}')
        self.assertEqual(offenders, [], 'macos-14 运行器已下线，不能再用')


if __name__ == '__main__':
    unittest.main()
