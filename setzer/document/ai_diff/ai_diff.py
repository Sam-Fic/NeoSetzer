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

r'''AI 编辑后的 PDF diff 显示：把 `phitex session compare` 的结果接到预览。

数据链：AI 发送时 workspace.snapshot_ai_baseline 快照正文（arm_baseline）
→ agent 改磁盘 → 2s 轮询重载 → 自动构建 → BuildSystem 在 PhiTeX live 构建
成功后调 diff_after_build → 后台线程跑 `session compare <快照> --json`
→ 解析成矩形 + change 列表 → GLib.idle_add 交付 preview.set_diff_result。

与 ChangeHighlight 同一套线程纪律（lock/_running/_stale/_shutdown）：查询
在子进程里（首跑要 prime + 重排标记文本，秒级），而 PhiTeX 增量构建间隔
只有十几毫秒，不防堆积就会线程雪崩。compare 复用同一个 JOB-diff 旁路
session（PhiTeX 语义：第二次 compare 是对上次标记文本的一次编辑），所以
成功后**不**调 compare_stop——停了反而丢掉复用，下一轮要重新 fork。

已知边界：compare 的 REV 传主文件快照路径时只有主文件文本被替换，
\input/\include 子文件读当前版——多文件文档的子文件改动看不出来
（compare.rs 的 OldCopy 语义）。要覆盖子文件得走 git rev，后续可选。
'''

import threading

from gi.repository import GLib

from setzer.document.build_system.phitex_session.phitex_session import PhitexSession
from setzer.helpers import phitex_compare

# compare 的首跑要 prime（定义构建）+ 重排标记文本，大文档远超普通 6ms
# 子命令；给一个宽裕上限，超时视为本轮放弃（横幅不亮，不假装成功）。
COMPARE_TIMEOUT_S = 600


class AiDiff(object):
    '''每文档一个；由 BuildSystem 在构建成功后调用。'''

    def __init__(self, document, build_system):
        self.document = document
        self.build_system = build_system
        # AI 发送时刻的正文快照路径（None = 没有武装，构建后什么都不做）。
        self.baseline_path = None
        self._lock = threading.Lock()
        self._running = False
        # compare 比构建慢时，在跑的查询结束后补一次。
        self._stale = False
        self._shutdown = False
        # 上次成功 compare 时的正文 hash：同一文本不重复查询（自动构建会
        # 在 AI 编辑后连着跑好几轮，没有这条守卫每轮都白跑一次 compare）。
        self._last_compared_text = None

    def arm_baseline(self, path):
        '''AI 发送时由 workspace.snapshot_ai_baseline 调用：换上新快照。

        旧的 diff_result 故意保留：agent 可能跑几分钟，期间用户仍可翻看
        上一轮的审阅结果；新结果送达时自然覆盖。_last_compared_text 归零
        ——它是"本轮已出过结果"的标记，新一轮审阅重新计数。
        '''
        self.baseline_path = path
        self._last_compared_text = None

    def diff_after_build(self):
        '''构建成功后（主线程）决定要不要跑 compare。

        门控顺序（全部主线程执行，buffer 读取安全）：
        * 用户在 buffer 里手改过（_dirty_line_ranges 非空——它只记真实键入，
          磁盘重载/撤销都不算，见 Document._records_user_edits）：本轮 AI
          审阅到此为止。再 diff 就是"AI 改动+用户改动"的混合体，横幅还写着
          AI edited 就是误导。清结果并解除武装，下一次 AI 发送重新武装。
        * == 基线：agent 没改成任何东西（或它自己回退了）：旧结果失效则清。
        * == 上次 compare 时的正文：结果已对应眼前文本（自动构建连跑的后续
          轮次），跳过。
        * 其余：agent 在磁盘上改了文本 → 跑 compare。agent 连续编辑期间每轮
          重载都会送来渐进的中间 diff，JOB-diff 复用使每轮代价是一次增量
          构建；_stale 把并发构建合并成一次补跑。

        注意调用顺序：本方法必须在 ChangeHighlight.highlight_after_build
        **之前**调用——后者会 take 掉脏行区间，这里先偷看一眼（不消费）
        才能判"用户是否手改过"。
        '''
        if self._shutdown or self.baseline_path is None:
            return
        if not PhitexSession.supports_compare():
            return
        if getattr(self.document, '_dirty_line_ranges', None):
            if self._last_compared_text is not None:
                self._clear_result()
            self.baseline_path = None
            return
        text = self.document.get_all_text()
        text_hash = self._hash_text(text)
        if phitex_compare.baseline_matches(text, self.baseline_path):
            if self._last_compared_text is not None:
                self._clear_result()
            return
        if self._last_compared_text is not None:
            if text_hash == self._last_compared_text:
                return
            # 结果之后文本又变了（非用户手改）：agent 又编辑了一轮，
            # 重跑 compare 更新结果——不能按"审阅结束"处理。
            session = self._get_session()
            if session is None:
                return
            self._spawn_query(session, text_hash)
            return
        session = self._get_session()
        if session is None:
            return
        self._spawn_query(session, text_hash)

    def _spawn_query(self, session, text_hash):
        with self._lock:
            if self._running:
                self._stale = True
                return
            self._running = True
        threading.Thread(target=self._query_and_show,
                         args=(session, text_hash),
                         daemon=True).start()

    def stop_compare(self):
        '''结束 PhiTeX 的 JOB-diff 旁路构建（用户放弃审阅 / 文档关闭）。

        后台线程跑：compare_stop 是一条子进程调用，不该卡主线程；watch
        可能已经死了，PhiTeX 对"没在 diff"返回 exit 0，无脑调安全。
        '''
        if self._shutdown:
            return
        session = self._get_session()
        if session is None:
            return
        threading.Thread(target=session.compare_stop, daemon=True).start()

    def shutdown(self):
        '''文档关闭：放弃在途查询，并顺手停掉 JOB-diff。'''
        self._shutdown = True
        self._stale = False
        try:
            self.stop_compare()
        except Exception:
            # shutdown 路径不允许任何异常外泄（文档正在销毁）。
            pass

    def _get_session(self):
        '''PhiTeX 常驻 session 在 latex builder 上（每文档一个）。'''
        builder = self.build_system.builders.get('build_latex')
        if builder is None:
            return None
        session = getattr(builder, 'phitex_session', None)
        if session is None or session.pid is None:
            return None
        return session

    def _hash_text(self, text):
        return hash(text)

    def _query_and_show(self, session, text_hash):
        compared = False
        result = None
        try:
            returncode, stdout = session.compare(self.baseline_path,
                                                 timeout=COMPARE_TIMEOUT_S)
            payload = phitex_compare.parse_compare_json(stdout)
            # 请求级失败（无 watch / REV 被拒）：静默放弃——不弹通知比弹一条
            # 错误状态好，原因已在构建日志/终端可见。
            if payload is not None and not phitex_compare.compare_failed(payload):
                compared = True
                changes = payload.get('changes') or []
                # 零改动（只动了注释/空白，排出来什么都没变）：没有可审阅的
                # 东西，不亮档位也不通知，等价于"没有 diff 结果"。但仍然记下
                # already-compared 文本，免得每次自动构建都白跑一遍。
                if changes:
                    result = {
                        'rectangles': phitex_compare.changes_to_rectangles(changes),
                        'changes': changes,
                        'pdf': payload.get('pdf'),
                        'pages': payload.get('pages'),
                        'change_count': len(changes),
                        'errors': payload.get('errors') or [],
                    }
        finally:
            with self._lock:
                self._running = False
                retry = self._stale and not self._shutdown
                self._stale = False
        if not self._shutdown:
            if compared:
                self._last_compared_text = text_hash
            GLib.idle_add(self._show, result)
        if retry:
            self.diff_after_build()

    def _show(self, result):
        if self._shutdown:
            return False
        preview = getattr(self.document, 'preview', None)
        if preview is None:
            return False
        preview.set_diff_result(result)
        return False

    def _clear_result(self):
        self._last_compared_text = None
        preview = getattr(self.document, 'preview', None)
        if preview is not None:
            preview.set_diff_result(None)
