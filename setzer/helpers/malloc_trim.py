# coding: utf-8

'''glibc 堆内存归还辅助。

glibc 释放内存后默认把空闲块留在堆 arena 里不归还 OS（brk/arena 只增不减），
导致 RSS 停在历史峰值不回落——编译/预览这类「短时间大量分配后释放」的负载
尤其明显。malloc_trim(0) 遍历所有 arena，把可回收的空闲页还给 OS。

仅在 Linux 生效；glibc 以外的 libc（musl 无 malloc_trim）或加载失败时静默
退化为空操作。
'''

import ctypes
import sys

_trim = None
if sys.platform.startswith('linux'):
    try:
        _libc = ctypes.CDLL('libc.so.6')
        _trim = _libc.malloc_trim
        _trim.argtypes = [ctypes.c_size_t]
        _trim.restype = ctypes.c_int
    except (OSError, AttributeError):
        _trim = None


def trim_malloc_heap():
    '''将 glibc 堆中可回收的空闲页归还 OS，降低 RSS。可安全重复调用。'''
    if _trim is not None:
        try:
            _trim(0)
        except Exception:
            pass
