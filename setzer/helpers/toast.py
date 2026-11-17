# coding: utf-8
'''
toast 的统一出口：同一句话在短时间内只弹一次。

有些提示天生会被反复命中：缩放到顶后继续滚、在最后一条匹配上继续按 Enter、
普通点击 PDF 里的链接、PDF 还没构建就 Ctrl+点击、构建被新一轮取代。这些
操作每一帧/每一次都触发同一句文案，逐次弹 toast 就从「反馈」变成了打扰。

这里按 key（默认取消息文本）记住上次显示时间，节流窗口内的重复请求直接
丢弃。真正的用户操作结果（复制、导出、会话结束等）各不相同，不需要经过
本模块。

import 本模块不碰 gi：GTK 依赖放在 `show` 内部，纯逻辑的 `should_show`
可以在无显示环境里直接测。
'''

import time

DEFAULT_THROTTLE_MS = 2500

# key -> 上次显示时刻（monotonic 毫秒）
_last_shown_ms = {}

# 防止动态文案（带文件名/数字）把表撑大；key 是有限的几句话，清空重来无害。
_MAX_KEYS = 128


def should_show(key, throttle_ms=DEFAULT_THROTTLE_MS, now=None):
    '''这个 key 现在该不该弹：首次 True，throttle_ms 内的重复 False。'''
    if throttle_ms is None:
        return True
    now = time.monotonic() if now is None else now
    now_ms = now * 1000.0
    last = _last_shown_ms.get(key)
    if last is not None and now_ms - last < throttle_ms:
        return False
    if key not in _last_shown_ms and len(_last_shown_ms) >= _MAX_KEYS:
        _last_shown_ms.clear()
    _last_shown_ms[key] = now_ms
    return True


def reset():
    '''清空节流表（测试与「换文档后重新提示」用）。'''
    _last_shown_ms.clear()


def show(message, timeout=3, throttle_ms=DEFAULT_THROTTLE_MS, key=None,
         overlay=None):
    '''在 overlay（默认主窗口）上弹一条 toast，按消息文本节流。

    throttle_ms=None 表示不去重。返回是否真的弹了。
    '''
    if not message:
        return False
    if not should_show(key or message, throttle_ms):
        return False
    try:
        import gi
        gi.require_version('Adw', '1')
        from gi.repository import Adw
        if overlay is None:
            from setzer.app.service_locator import ServiceLocator
            main_window = ServiceLocator.get_main_window()
            if main_window is None or not hasattr(main_window, 'toast_overlay'):
                return False
            overlay = main_window.toast_overlay
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        overlay.add_toast(toast)
        return True
    except Exception:
        # toast 失败不影响主流程。
        return False
