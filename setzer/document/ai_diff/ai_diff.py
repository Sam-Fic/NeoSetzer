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
（compare.rs 的 OldCopy 语义）。要覆盖子文件得走 git rev。

Git 页的文件 diff 复用同一管线：REV 直接传 'HEAD'（工作区 vs HEAD，
子文件盲区天然消除），交付走同一 set_diff_result / show_diff_view；
结果 dict 带 source='git'（preview_presenter 的 AI toast 对 git 静默），
且发起前解除武装（baseline_ref=None）防止后续自动构建的 AI compare
覆盖 git diff 结果。'''

import os

import threading

from gi.repository import GLib

from setzer.document.build_system.phitex_session.phitex_session import PhitexSession
from setzer.helpers import phitex_compare

# compare 的首跑要 prime（定义构建）+ 重排标记文本，大文档远超普通 6ms
# 子命令；给一个宽裕上限，超时视为本轮放弃（横幅不亮，不假装成功）。
COMPARE_TIMEOUT_S = 600

# 在正式 PDF 上闪改动位置时，最多反查的 change 数（每个一次 synctex 子进程）。
# 与 ChangeHighlight.MAX_QUERIES 同一取舍：丢的是排序靠后的改动。
MAX_FLASH_CHANGES = 6


class AiDiff(object):
    '''每文档一个；由 BuildSystem 在构建成功后调用。'''

    def __init__(self, document, build_system):
        self.document = document
        self.build_system = build_system
        # AI 发送时刻的基线引用（None = 没有武装，构建后什么都不做）：
        # git 通道是私有快照 sha，文件通道是旧主文件副本路径——compare 两种
        # 都收，diff 粒度不同（git 全量、文件只主文件）。
        self.baseline_ref = None
        # 武装时刻的正文快照："正文变没变"的判定基准（buffer 直比，两条
        # 通道同一逻辑，不再回头读文件）。
        self.baseline_text = None
        self._lock = threading.Lock()
        self._running = False
        # compare 比构建慢时，在跑的查询结束后补一次。
        self._stale = False
        self._shutdown = False
        # 上次成功 compare 时的正文 hash：同一文本不重复查询（自动构建会
        # 在 AI 编辑后连着跑好几轮，没有这条守卫每轮都白跑一次 compare）。
        self._last_compared_text = None
        # 基线武装以来用户是否手改过：只为通知措辞服务（"AI edited" vs
        # "自 AI 任务以来文档有变化"），不影响 compare 与否——diff 始终
        # 是"基线 → 当前正文"的持续视图，人工修改也要出现在里面，否则
        # 只看 diff 视图的人会误以为 AI 的版本还是现状。
        self._human_edited = False

    def arm_baseline(self, ref, text):
        '''AI 发送时由 workspace.snapshot_ai_baseline 调用：换上新基线。

        旧的 diff_result 故意保留：agent 可能跑几分钟，期间用户仍可翻看
        上一轮的审阅结果；新结果送达时自然覆盖。_last_compared_text 归零
        ——它是"本轮已出过结果"的标记，新一轮审阅重新计数；人工修改标记
        也归零（新基线是新起点）。
        '''
        self.baseline_ref = ref
        self.baseline_text = text
        self._last_compared_text = None
        self._human_edited = False

    def end_review(self):
        '''手动结束审阅轮（工具栏结束按钮）。

        解除武装 + 停 JOB-diff + 清文件通道的快照副本（git 通道的快照是
        仓库里不可达的提交对象，没有引用指向它，无需也不能删）。不清
        diff_result——那是 Preview 端的状态，由 Preview.end_diff_review
        统一清并广播，保证档位/视图/结果一处收口。
        '''
        ref = self.baseline_ref
        self.baseline_ref = None
        self.baseline_text = None
        self._last_compared_text = None
        self._human_edited = False
        if isinstance(ref, str) and os.path.isabs(ref):
            phitex_compare.clear_baseline(os.path.dirname(ref), os.path.basename(ref))
        self.stop_compare()

    def disarm(self):
        '''静默解除武装：不碰 JOB-diff、不删快照文件。

        Git 页的「工作区 vs HEAD」diff 复用同一个 JOB-diff 旁路 session：
        发起前必须解除 AI 武装，否则后续自动构建的 AI compare 会把 git
        diff 结果覆盖掉（两者共用 preview 的 diff 槽位）。与 end_review
        的区别：这里不能 stop_compare——那会打断紧接着要跑的 git compare；
        也不清快照（git 通道是 sha；文件通道留着无害，下次发送即覆盖）。
        '''
        self.baseline_ref = None
        self.baseline_text = None
        self._last_compared_text = None
        self._human_edited = False

    def diff_after_build(self, pdf_filename=None):
        '''构建成功后（主线程）决定要不要跑 compare。

        pdf_filename 是本次构建的正式 PDF（build_system 传进来），compare
        成功后用它把 change 的 file:line 反查成正式 PDF 上的矩形闪一下
        ——不切 diff 视图也能看到改动落在哪几页。

        diff 语义（持续视图）：diff 永远是"基线 → 当前正文"——不管中间的
        改动来自 agent 还是用户的手修。曾冻结过一版（手改即停），但旧 diff
        停在 AI 的版本上，只看 diff 视图的人会误以为人工的修改还没发生——
        错误的存量比混合的增量更误导；人工修改混进来只影响**通知措辞**
        （由 _human_edited 区分），不影响 diff 本身的正确性。

        门控顺序（全部主线程执行，buffer 读取安全）：
        * 用户在 buffer 里手改过（_dirty_line_ranges 非空——它只记真实键入，
          磁盘重载/撤销都不算，见 Document._records_user_edits）：记下
          _human_edited（通知措辞切换用），**继续**走 compare——人工修改
          要进 diff。
        * == 基线：agent 没改成任何东西、用户也没有净改动（或都回退了）：
          旧结果失效则清。
        * == 上次 compare 时的正文：结果已对应眼前文本（自动构建连跑的后续
          轮次），跳过。
        * 其余：文本动了（agent 或用户）→ 跑 compare。agent 连续编辑期间
          每轮重载都会送来渐进的中间 diff，JOB-diff 复用使每轮代价是一次
          增量构建；_stale 把并发构建合并成一次补跑。

        注意调用顺序：本方法必须在 ChangeHighlight.highlight_after_build
        **之前**调用——后者会 take 掉脏行区间，这里先偷看一眼（不消费）
        才能判"用户是否手改过"。
        '''
        if self._shutdown or self.baseline_ref is None:
            return
        if not PhitexSession.supports_compare():
            return
        if getattr(self.document, '_dirty_line_ranges', None):
            # 用户在 buffer 里手改过：只标记（通知措辞用），diff 照常更新——
            # 人工修改必须出现在 diff 里，否则视图说的不是文档的现状。
            self._human_edited = True
        text = self.document.get_all_text()
        text_hash = self._hash_text(text)
        if phitex_compare.baseline_matches(text, self.baseline_text):
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
            self._spawn_query(session, text_hash, pdf_filename)
            return
        session = self._get_session()
        if session is None:
            return
        self._spawn_query(session, text_hash, pdf_filename)

    def _spawn_query(self, session, text_hash, pdf_filename):
        with self._lock:
            if self._running:
                self._stale = True
                return
            self._running = True
        threading.Thread(target=self._query_and_show,
                         args=(session, text_hash, pdf_filename),
                         daemon=True).start()

    def is_query_running(self):
        '''是否有在途 compare 查询（主线程读；_running 只在主线程写）。

        Git 页的 PDF diff（REV='HEAD'）复用同一个 JOB-diff session——该
        session 语义下并发 compare 互相污染，故 git 路径发起前先查此标志，
        在途时兜底文本 diff。'''
        return self._running

    @property
    def human_edited_since_baseline(self):
        '''基线武装以来用户是否手改过 buffer（通知措辞用）。'''
        return self._human_edited

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

    def _query_and_show(self, session, text_hash, pdf_filename):
        compared = False
        result = None
        try:
            returncode, stdout = session.compare(self.baseline_ref,
                                                 timeout=COMPARE_TIMEOUT_S)
            payload = phitex_compare.parse_compare_json(stdout)
            # 请求级失败（无 watch / REV 被拒）：静默放弃——不弹通知比弹一条
            # 错误状态好，原因已在构建日志/终端可见。
            if payload is not None and not phitex_compare.compare_failed(payload):
                compared = True
                changes = payload.get('changes') or []
                # 移动配对：PhiTeX 的 diff 引擎没有移动检测，被挪走的段落
                # 会变成一对 del+add——配成 moved（蓝色）语义才对（papilot
                # 的规则：指纹相等，或 ≥20 字母数字且高度相似）。
                phitex_compare.pair_moves(changes)
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
                        # diff 构建自己编译失败（pdf 缺失）：changes 还在但
                        # 定位全 null，没有可看的排版——按失败处理，调用端给
                        # 一条"diff 没排出来"的反馈而不是亮一个空档位。
                        'failed': payload.get('pdf') is None,
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
            if result is not None and not result.get('failed') and pdf_filename:
                # 正式 PDF 上的改动位置闪一下（papilot 的 SyncTeX 关联思路，
                # 但只做"在哪"不做"是什么"——后者是 diff 视图的职责）。
                self._flash_changes_on_result_pdf(result, pdf_filename)
        if retry:
            self.diff_after_build(pdf_filename)

    def _flash_changes_on_result_pdf(self, result, pdf_filename):
        '''后台线程：compare 的 file:line → 正式 PDF 矩形 → 主线程闪一下。

        与 ChangeHighlight 同一管线（synctex 反查 + 同行合并 + 淡出闪烁），
        数据源换成 compare 的精确改动行。容错哲学也一致：synctex 定位不到
        （子文件、数学环境、行号失真）就少闪几处，绝不因定位失败而报错。
        '''
        from setzer.document.change_highlight.change_highlight import merge_rectangles
        from setzer.helpers import synctex_query
        rectangles = []
        try:
            directory = synctex_query.synctex_directory_for(pdf_filename)
            document_dir = os.path.dirname(self.document.get_filename() or '') or None
            for change in result.get('changes')[:MAX_FLASH_CHANGES]:
                filename = change.get('file')
                line = change.get('line')
                if not filename or not isinstance(line, int):
                    continue
                if not os.path.isabs(filename):
                    filename = os.path.join(document_dir or '', filename)
                for rectangle in synctex_query.query_rectangles(
                        filename, pdf_filename, line, directory):
                    rectangles.append(rectangle)
        except Exception:
            # 闪烁是锦上添花：任何定位问题都不该影响 diff 结果的交付。
            rectangles = []
        if rectangles and not self._shutdown:
            GLib.idle_add(self._flash, merge_rectangles(rectangles))

    def _flash(self, rectangles):
        if self._shutdown:
            return False
        preview = getattr(self.document, 'preview', None)
        if preview is None:
            return False
        preview.flash_changed_rectangles(rectangles)
        return False

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
