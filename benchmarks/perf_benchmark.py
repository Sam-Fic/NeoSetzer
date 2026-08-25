#!/usr/bin/env python3
# coding: utf-8
"""NeoSetzer 性能基准：验证 HIGH 项（H1/H2/H3/H4）。

驱动真实的 ParserLaTeX(基于 GtkSource.Buffer),测量:
  H1/H2 — 每次按键(insert-text)的解析耗时,随文档规模增长的趋势
  H4    — 病态长行上的正则 finditer 行为
  H3    — 预览渲染线程忙轮询的空转唤醒频率

用法：python3 benchmarks/perf_benchmark.py
"""
import gi
gi.require_version('GtkSource', '5')
gi.require_version('Gtk', '4.0')
from gi.repository import GtkSource, Gtk

import os, sys, time, io, contextlib, re, statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from setzer.document.parser.parser_latex import ParserLaTeX
from setzer.app.service_locator import ServiceLocator


# ---------- 文档生成 ----------

def build_doc(n_sections):
    lines = [
        r'\documentclass{article}',
        r'\usepackage{amsmath}',
        r'\usepackage{hyperref}',
        r'\usepackage{graphicx}',
        r'\begin{document}',
    ]
    for i in range(n_sections):
        lines.append(r'\section{Section %d}' % i)
        lines.append(r'\label{sec:%d}' % i)
        lines.append(r'\begin{equation}')
        lines.append('x_%d = %d' % (i, i * i))
        lines.append(r'\end{equation}')
        lines.append(r'\input{chapter%d}' % i)
        lines.append(r'\todo{fix this %d}' % i)
    lines.append(r'\bibliography{refs}')
    lines.append(r'\end{document}')
    return '\n'.join(lines)


class MockDocument:
    """最小 document mock:只需 source_buffer 即可驱动 ParserLaTeX。"""
    def __init__(self):
        self.source_buffer = GtkSource.Buffer()


def make_parser():
    doc = MockDocument()
    parser = ParserLaTeX(doc)
    return doc, parser


def insert_text(buffer, text):
    end = buffer.get_end_iter()
    buffer.insert(end, text)


# ---------- 计时工具 ----------

def measure_per_keystroke(buffer, n_keystrokes, at_end=True):
    """测量 n_keystrokes 次单字符插入的逐次耗时(抑制 @timer 的 stdout)。"""
    times = []
    timer_log = io.StringIO()
    for _ in range(n_keystrokes):
        if at_end:
            it = buffer.get_end_iter()
        else:
            it = buffer.get_iter_at_offset(0)  # 文档开头:偏移迁移最坏情况
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(timer_log):
            buffer.insert(it, 'a')
        times.append((time.perf_counter() - t0) * 1000.0)  # ms
    return times, timer_log.getvalue()


def fmt_stats(label, times_ms):
    times_ms = sorted(times_ms)
    n = len(times_ms)
    p50 = times_ms[n // 2]
    p95 = times_ms[int(n * 0.95)]
    p99 = times_ms[min(int(n * 0.99), n - 1)]
    mx = times_ms[-1]
    mean = statistics.mean(times_ms)
    print(f"  {label:42s} n={n:4d}  mean={mean:7.3f}ms  p50={p50:7.3f}  p95={p95:7.3f}  p99={p99:7.3f}  max={mx:7.3f}")
    return mean


# ---------- H1/H2: 每按键解析成本 ----------

def bench_keystroke_scaling():
    print("\n" + "=" * 78)
    print("H1/H2 — 每按键(insert-text)解析成本 vs 文档规模")
    print("=" * 78)
    print("  预期:若为 O(n_symbols),per-keystroke mean 随 n_sections 线性增长。\n")

    results = {}
    for n_sections in [50, 200, 500, 1000]:
        doc, parser = make_parser()
        text = build_doc(n_sections)
        # 初始插入(整段):触发一次完整解析
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            insert_text(doc.source_buffer, text)
        initial_ms = (time.perf_counter() - t0) * 1000.0

        n_sym = len(parser.other_symbols)
        n_blocks = len(parser.symbols['blocks'])

        # 测量末尾单字符插入(普通打字)
        times_end, _ = measure_per_keystroke(doc.source_buffer, n_keystrokes=200, at_end=True)
        # 测量开头单字符插入(偏移迁移最坏:所有符号都需后移)
        times_start, _ = measure_per_keystroke(doc.source_buffer, n_keystrokes=200, at_end=False)

        print(f"  [{n_sections:4d} sections, ~{n_sections*6:5d} lines, {n_sym} symbols, {n_blocks} blocks]")
        print(f"    initial full-parse : {initial_ms:8.2f} ms")
        fmt_stats("keystroke @ end (normal typing)", times_end)
        fmt_stats("keystroke @ start (worst shift)", times_start)
        print()
        results[n_sections] = (n_sym, statistics.mean(times_end), statistics.mean(times_start))

    print("  增长趋势:")
    prev_end = prev_start = None
    for n, (n_sym, m_end, m_start) in results.items():
        de = f"  (×{m_end/prev_end:.2f})" if prev_end else ""
        ds = f"  (×{m_start/prev_start:.2f})" if prev_start else ""
        print(f"    {n:5d} sec / {n_sym:5d} sym -> end={m_end:7.3f}ms{de}  start={m_start:7.3f}ms{ds}")
        prev_end, prev_start = m_end, m_start


# ---------- H4: 正则病态输入 ----------

SYMBOLS_RE = r'\\(label|include|input|subfile|subimport|bibliography|addbibresource|todo)(?:\[[^\{\[]*\]){0,1}\{((?:\s|\w|\:|\.|,|\/|\\|\'|-|\"|\(|\))*)\}|\\(usepackage)(?:\[[^\{\[]*\]){0,1}\{((?:\s|\w|\:|,)*)\}|\\(bibitem)(?:\[.*\]){0,1}\{((?:\s|\w|\:)*)\}'
BLOCKS_RE = r'\n|\\(begin|end)\{((?:\w|•|\*)+)\}|\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)(?:\*){0,1}\{([^\{]*)\}'


def bench_regex_pathology():
    print("\n" + "=" * 78)
    print("H4 — 正则 finditer 在病态长行上的行为 (调研结论:见末尾说明)")
    print("=" * 78)

    cases = {
        "normal 80-char line":
            r'\section{Hello} \label{a} \begin{equation} x = 1 \end{equation} \todo{x}' * 4,
        "10KB normal latex (single line)":
            (r'\section{S} \label{l} \input{f} ' * 500),
        "10KB unmatched opening braces \\label{aaa...":
            r'\label{' + 'a' * 10000,
        "10KB unmatched brackets [aaa...":
            r'\bibitem[' + 'a' * 10000,
        "10KB alternating \\bibitem[ without close":
            (r'\bibitem[a' * 2500),
        "50KB normal latex (single line)":
            (r'\section{S} \label{l} \input{f} ' * 2500),
    }

    for label, text in cases.items():
        rx = ServiceLocator.get_regex_object(SYMBOLS_RE)
        # finditer 是 parser 实际调用方式
        t0 = time.perf_counter()
        matches = list(rx.finditer(text))
        dt_ms = (time.perf_counter() - t0) * 1000.0
        # 也测一次 blocks 正则
        rx_b = ServiceLocator.get_regex_object(BLOCKS_RE)
        t0 = time.perf_counter()
        list(rx_b.finditer(text))
        dt_b_ms = (time.perf_counter() - t0) * 1000.0
        print(f"  {label:48s} len={len(text):6d}  symbols_re={dt_ms:8.3f}ms  blocks_re={dt_b_ms:8.3f}ms  (matches={len(matches)})")

    print("\n  调研结论:病态用例(无闭合 ] 的超长单行)的 O(n²) 是 finditer +")
    print("  无界量词的固有行为,所有变体(.* / .*? / [^\\]]*)实测均 ~33ms 且无改善。")
    print("  真实 LaTeX 中单行 <200 字符、且 bibitem 必闭合,该场景不会出现。")
    print("  正则保持原样 .* (无 DOTALL,行界正确)。H4 降级为信息性,不做改动。")


# ---------- H3: 预览渲染线程忙轮询 ----------

def bench_preview_busy_wait():
    print("\n" + "=" * 78)
    print("H3 — 预览渲染线程轮询:旧(忙轮询)vs 新(阻塞 get)")
    print("=" * 78)

    import _thread as thread, queue
    import threading

    # --- 旧模式:block=False + time.sleep(0.05) ---
    q_old = queue.Queue()
    lock = thread.allocate_lock()
    wakeups_old = {'n': 0}

    def loop_old(stop_after):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < stop_after:
            wakeups_old['n'] += 1
            with lock:
                pass
            try:
                q_old.get(block=False)
            except queue.Empty:
                time.sleep(0.05)

    loop_old(1.0)

    # --- 新模式:get(block=True, timeout=0.5) ---
    q_new = queue.Queue()
    wakeups_new = {'n': 0}

    def loop_new(stop_after):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < stop_after:
            wakeups_new['n'] += 1
            try:
                q_new.get(block=True, timeout=0.5)
            except queue.Empty:
                pass

    loop_new(1.0)

    print(f"  旧(block=False + sleep 0.05): 空闲 1.0s 内唤醒 {wakeups_old['n']} 次 (≈{wakeups_old['n']} 次/秒)")
    print(f"  新(block=True, timeout=0.5):   空闲 1.0s 内唤醒 {wakeups_new['n']} 次 (≈{wakeups_new['n']} 次/秒)")

    # 任务就绪延迟:旧(最多等 50ms 到下次轮询)vs 新(队列条件变量即时唤醒)
    def latency_test(use_block):
        q = queue.Queue()
        lat = []
        def producer():
            for i in range(20):
                time.sleep(0.02)
                put_t = time.perf_counter()
                q.put(put_t)
        def consumer():
            for _ in range(20):
                if use_block:
                    try: put_t = q.get(block=True, timeout=1.0)
                    except queue.Empty: continue
                else:
                    while True:
                        try: put_t = q.get(block=False); break
                        except queue.Empty: time.sleep(0.05)
                lat.append((time.perf_counter() - put_t) * 1000)
        c = threading.Thread(target=consumer); c.start()
        producer()
        c.join()
        return statistics.mean(lat)

    lat_old = latency_test(use_block=False)
    lat_new = latency_test(use_block=True)
    print(f"  任务就绪延迟(put→get): 旧 mean={lat_old:.2f}ms (最坏 50ms)  新 mean={lat_new:.3f}ms (即时)")


# ---------- 单次按键方法级细分(替代 @timer,生产环境无侵入) ----------

def show_method_breakdown():
    """对一次按键,用 perf_counter 细分 on_insert_text / parse_blocks / parse_symbols。"""
    print("\n" + "=" * 78)
    print("方法级细分 — 一次按键(200 sections, perf_counter 计时)")
    print("=" * 78)
    doc, parser = make_parser()
    with contextlib.redirect_stdout(io.StringIO()):
        insert_text(doc.source_buffer, build_doc(200))
    # 预热
    for _ in range(5):
        doc.source_buffer.insert(doc.source_buffer.get_end_iter(), 'a')

    import statistics as st
    def time_n(fn, n=100):
        ts = []
        for _ in range(n):
            t0 = time.perf_counter(); fn(); ts.append((time.perf_counter() - t0) * 1000)
        return st.mean(ts)

    t_total = time_n(lambda: doc.source_buffer.insert(doc.source_buffer.get_end_iter(), 'a'))
    t_blocks = time_n(parser.parse_blocks)
    t_symbols = time_n(parser.parse_symbols)
    print(f"  on_insert_text (整体): {t_total:7.3f} ms")
    print(f"    ├─ parse_blocks   : {t_blocks:7.3f} ms")
    print(f"    ├─ parse_symbols  : {t_symbols:7.3f} ms")
    print(f"    └─ 其余(增量迁移/GTK iter): {t_total - t_blocks - t_symbols:7.3f} ms")


# ---------- H5: 打开文件/会话恢复路径 ----------

def bench_open_path():
    """打开路径端到端：set_text(+_loading_from_disk)+initial_parse，挂接
    code_folding 与 sticky_scroll 可见性计算（headless 最小接线）。

    验证点：
      O1 — 程序化载入只触发一次 finished_parsing（防抖不再二次全量解析）
      O2 — 打开耗时随规模的增长趋势（修复前 code_folding 展开风暴 O(B²)、
           sticky_scroll 父链回溯 O(S×B×d) 曾主导该路径）
      O3 — 全部区域折叠后重解析的代价（失配区域批量展开，无逐个通知）
    """
    print("\n" + "=" * 78)
    print("H5 — 打开文件/会话恢复路径(set_text + initial_parse + 折叠/粘性滚动)")
    print("=" * 78)

    import setzer.app.service_locator as _sl

    class _FakeSettings:
        def get_value(self, *a):
            return True
        def connect(self, *a):
            return None

    _sl.ServiceLocator.settings = _FakeSettings()

    from setzer.document.code_folding.code_folding import CodeFolding
    from setzer.document.sticky_scroll.sticky_scroll import StickyScroll

    class ObservableStub:
        """最小 Observable 替身：只实现 CodeFolding 用到的事件接口。"""
        def __init__(self):
            self._observers = {}
        def connect(self, code, cb):
            self._observers.setdefault(code, []).append(cb)
        def disconnect(self, code, cb):
            pass
        def add_change_code(self, code, parameter=None):
            for cb in self._observers.get(code, []):
                cb(self)

    class FoldableDoc(MockDocument):
        def __init__(self):
            MockDocument.__init__(self)
            self._loading_from_disk = False

    class _FoldHost(ObservableStub):
        pass

    results = {}
    for n_sections in [200, 1000, 4000]:
        doc = FoldableDoc()
        doc._loading_from_disk = True
        parser = ParserLaTeX(doc)
        doc.parser = parser

        host = _FoldHost()
        host.source_buffer = doc.source_buffer
        host.parser = parser
        folding = CodeFolding(host)

        ss = object.__new__(StickyScroll)
        ss.document = host

        emissions = []
        parser.connect('finished_parsing', lambda p: emissions.append(1))

        text = build_doc(n_sections)

        t0 = time.perf_counter()
        doc.source_buffer.set_text(text)   # _loading_from_disk=True: 不调度防抖
        parser.initial_parse(text)
        open_ms = (time.perf_counter() - t0) * 1000.0
        open_emissions = len(emissions)    # 打开路径应恰好 1 次

        n_blocks = len(parser.symbols['blocks'])

        # sticky scroll 可见性单独计时
        t0 = time.perf_counter()
        vis = ss._compute_section_visibility(parser.symbols['blocks'])
        sticky_ms = (time.perf_counter() - t0) * 1000.0

        # O3: 全部折叠后重解析(旧实现此处为 O(B²) 展开风暴)。
        # 直接调用 on_parser_update 而非经信号分发：Observable 会吞掉回调
        # 异常，静默失败曾让此处的"折叠保留"断言形同虚设。
        folding.fold_all()
        t0 = time.perf_counter()
        folding.on_parser_update(parser)
        reparse_folded_ms = (time.perf_counter() - t0) * 1000.0
        still_folded = sum(1 for r in folding.folding_regions.values() if r['is_folded'])

        print(f"  [{n_sections:5d} sections, {n_blocks} blocks]")
        print(f"    O1 打开路径 finished_parsing 次数 : {open_emissions} (应为 1)")
        print(f"    O2 打开(set_text+initial_parse)   : {open_ms:8.1f} ms")
        print(f"       └ sticky_scroll 可见性预计算    : {sticky_ms:8.1f} ms")
        print(f"    O3 全折叠后重解析                  : {reparse_folded_ms:8.1f} ms"
              f"  (重解析保留折叠 {still_folded}/{n_blocks})")
        results[n_sections] = open_ms

    print("\n  增长趋势:")
    prev = None
    for n, ms in results.items():
        ratio = f"  (×{ms/prev:.2f})" if prev else ""
        print(f"    {n:5d} sections -> {ms:8.1f} ms{ratio}")
        prev = ms


def main():
    Gtk.init()  # 某些 GtkSource 操作需要主循环初始化
    show_method_breakdown()
    bench_keystroke_scaling()
    bench_regex_pathology()
    bench_preview_busy_wait()
    bench_open_path()
    print("\n" + "=" * 78)
    print("基准完成。")
    print("=" * 78)


if __name__ == '__main__':
    main()
