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

'''PhiTeX viewer WS 的传输壳：收发、分发、重连，协议计算全在 helpers。

连接对象活在主线程（GLib 超时/回调驱动），生命周期由 BuildSystem 管：每轮
PhiTeX live 构建成功后拿 session 的 viewer URL 调 ensure_endpoint()，URL 变
了（watch 重启、换了端口/token）就断开重连；shutdown() 关连接。协议细节见
setzer/helpers/viewer_protocol.py 的模块注释（本文件对端钉死的实现）。

对外三件事：
* 页 hash：连接后与每次 settled 后拉 `pages`，回调 on_page_hashes(list)；
  build_system 把它投递给 preview 做页级渲染缓存。
* origins 按需：request_origins(page, callback)——该页字形映射没做好时
  watch 回 pending，这里按 4ms→8ms→…→100ms 轮询、总上限 10s（viewer 自
  己的节流参数）；成功后缓存整页（同一页反复反查不重复拉），origins/settled
  事件到达时整个缓存作废（edits 偏移没有增量搬运，重建比搬运不易错）。
* 断线自愈：on close/error → 退避 1s→2s→3s 重连（watch 死了连接必然失
  败，下一轮构建 ensure_endpoint 换了 URL 会替换这轮重试）。

不订阅也用不到的事件（console/building/stats/diff…）直接忽略；无心跳——
PhiTeX 不踢空闲连接，watch 活着连接就活着。
'''

import json

import gi
gi.require_version('Soup', '3.0')
from gi.repository import GLib, Soup

from setzer.helpers import viewer_protocol

# origins pending 轮询（viewer.js untilMade 的节流参数）：4ms 起步、每次
# 翻倍、上限 100ms，单页总等待上限 10s（超过按"这页拿不到"处理）。
ORIGINS_POLL_INITIAL_MS = 4
ORIGINS_POLL_MAX_MS = 100
ORIGINS_WAIT_BUDGET_S = 10.0

# 断线重连退避：1s → 2s → 3s 封顶、最多 8 次（约半分钟）。watch 真死了不
# 无限重试耗电——每轮 live 构建的 ensure_endpoint 会把连接重新踢起来（构建
# 成功 ⇒ watch 活着），那才是该重连的时刻。
RECONNECT_DELAYS_S = (1.0, 2.0, 3.0)
RECONNECT_MAX_ATTEMPTS = 8


class ViewerSocketClient(object):
    '''一个 watch 的 viewer WS 客户端（主线程）。'''

    def __init__(self, on_page_hashes=None):
        self.on_page_hashes = on_page_hashes
        self.endpoint = None
        self._session = Soup.Session()
        self._connection = None
        self._closed = False
        self._next_id = 1
        self._pending_replies = {}
        # 文件表全局单调（watch 侧只增不减），glyphs 缓存按 0-based 页号。
        self._files = []
        self._origins_cache = {}
        self._origins_waiters = {}
        self._reconnect_step = 0
        self._reconnect_source = None

    # ---- 生命周期 --------------------------------------------------------

    def ensure_endpoint(self, viewer_url):
        '''确保连到 viewer_url 对应的 WS 端点；变了就断旧重新来。

        同 URL 但连接已丢（重连次数耗尽）时也算"该重新来"：每轮 live 构建
        都会调这里，watch 活着就永远有机会接上。
        '''
        endpoint = viewer_protocol.viewer_endpoint(viewer_url)
        if endpoint is None or self._closed:
            return
        if endpoint == self.endpoint and self._connection is not None:
            return
        self._reconnect_step = 0
        self._teardown_connection()
        self.endpoint = endpoint
        self._files = []
        self._origins_cache.clear()
        self._fail_all_waiters()
        self._open()

    def disconnect(self):
        '''临时断开（切引擎 / URL 消失）：之后 ensure_endpoint 还能接回来。'''
        self.endpoint = None
        self._teardown_connection()
        self._cancel_reconnect()
        self._origins_cache.clear()
        self._fail_all_waiters()

    def shutdown(self):
        '''永久关闭（文档销毁）：此后 ensure/请求都不再动作。'''
        self._closed = True
        self.disconnect()

    def _cancel_reconnect(self):
        if self._reconnect_source is not None:
            self._reconnect_source.destroy()
            self._reconnect_source = None

    @property
    def connected(self):
        return self._connection is not None

    def _teardown_connection(self):
        connection = self._connection
        self._connection = None
        # 在途 op 的回调一并作废：连接没了回复不会来，留着只会长（id 单调
        # 递增不复用，永远等不到那个 id）。
        self._pending_replies.clear()
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:
                pass

    def _open(self):
        message = Soup.Message.new('GET', self.endpoint)
        self._session.websocket_connect_async(
            message, None, None, 0, None, self._on_connected, None)

    def _on_connected(self, source, result, _data):
        if self._closed:
            return
        try:
            connection = self._session.websocket_connect_finish(result)
        except Exception:
            self._schedule_reconnect()
            return
        self._connection = connection
        self._reconnect_step = 0
        connection.connect('message', self._on_message)
        connection.connect('closed', lambda *_: self._on_lost())
        # 接上先拉一次页 hash（watch 可能在 NeoSetzer 没连的时候已经 settled
        # 过若干轮，事件补不回来，主动拉一次对齐）。
        self._refresh_hashes()

    def _on_lost(self):
        if self._closed or self._connection is None:
            return
        self._connection = None
        self._fail_all_waiters()
        self._schedule_reconnect()

    def _schedule_reconnect(self):
        if self._closed or self._reconnect_source is not None:
            return
        if self._reconnect_step >= RECONNECT_MAX_ATTEMPTS:
            return
        delay = RECONNECT_DELAYS_S[min(self._reconnect_step, len(RECONNECT_DELAYS_S) - 1)]
        self._reconnect_step += 1

        def _retry():
            self._reconnect_source = None
            if not self._closed and self.endpoint:
                self._open()
            return False

        self._reconnect_source = GLib.timeout_source_new(int(delay * 1000))
        self._reconnect_source.set_callback(_retry)
        self._reconnect_source.attach(None)

    # ---- 收发 ------------------------------------------------------------

    def _send(self, payload):
        if self._connection is None:
            return False
        try:
            self._connection.send_message(
                Soup.WebsocketDataType.TEXT,
                GLib.Bytes.new(json.dumps(payload).encode('utf-8')))
            return True
        except Exception:
            self._on_lost()
            return False

    def _request(self, op, callback):
        request_id = self._next_id
        self._next_id += 1
        message = dict(op)
        message['id'] = request_id
        self._pending_replies[request_id] = callback
        if not self._send(message):
            self._pending_replies.pop(request_id, None)
            callback(None)
            return None
        return request_id

    def _on_message(self, _connection, _type, payload):
        try:
            message = json.loads(bytes(payload.get_data()))
        except Exception:
            return
        if not isinstance(message, dict):
            return
        request_id = message.get('id')
        if request_id is not None:
            callback = self._pending_replies.pop(request_id, None)
            if callback is not None:
                callback(message)
            return
        event = message.get('event')
        if event == 'settled':
            self._refresh_hashes()
        elif event == 'origins':
            # 这些页的字形偏移要跟新文本对不上号了（edits 增量不搬运，直接
            # 作废该页缓存，下次用到重新拉）。没带页号就整体作废。
            pages = message.get('pages')
            if isinstance(pages, list):
                for page in pages:
                    if isinstance(page, int) and not isinstance(page, bool):
                        self._origins_cache.pop(page, None)
            else:
                self._origins_cache.clear()
                self._fail_all_waiters()

    def _refresh_hashes(self):
        def _reply(message):
            if self._closed or not message or not message.get('ok'):
                return
            hashes = viewer_protocol.page_hashes_from_response(message.get('json'))
            if hashes is not None and self.on_page_hashes is not None:
                self.on_page_hashes(hashes)

        self._request({'op': 'pages'}, _reply)

    # ---- origins ---------------------------------------------------------

    def request_origins(self, page, callback):
        '''0-based 页 → callback(glyphs, files)；拿不到 callback(None, None)。

        glyphs 是 viewer_protocol.decode_origins 的产物（file 为 files 表
        下标或 None）。结果按页缓存；pending 轮询期间同一页的重复请求合并
        （只保留最后一个回调——反查是即时行为，旧请求作废没有损失）。
        '''
        if self._closed or self._connection is None:
            callback(None, None)
            return
        cached = self._origins_cache.get(page)
        if cached is not None:
            callback(cached, self._files)
            return
        if page in self._origins_waiters:
            self._origins_waiters[page] = (callback, *self._origins_waiters[page][1:])
            return
        self._origins_waiters[page] = (callback, 0.0, ORIGINS_POLL_INITIAL_MS)
        self._poll_origins(page)

    def cancel_origins(self, page):
        '''作废一次未决请求（调用方超时后不再关心结果；晚到的结果被丢弃）。'''
        self._origins_waiters.pop(page, None)

    def _poll_origins(self, page):
        waiter = self._origins_waiters.get(page)
        if waiter is None:
            return

        def _reply(message):
            current = self._origins_waiters.get(page)
            if current is None or self._closed:
                return
            callback, waited, delay = current
            json_payload = message.get('json') if message and message.get('ok') else None
            if message and message.get('ok') and json_payload and not json_payload.get('pending'):
                files, glyphs = viewer_protocol.decode_origins(json_payload, self._files)
                if glyphs is not None:
                    self._files = files
                    del self._origins_waiters[page]
                    self._origins_cache[page] = glyphs
                    callback(glyphs, files)
                    return
                # 形状不对：按拿不到处理（协议漂移时静默降级到 synctex）。
            elif waited >= ORIGINS_WAIT_BUDGET_S:
                del self._origins_waiters[page]
                callback(None, None)
                return
            # pending：下次重试，间隔翻倍封顶 100ms。
            self._origins_waiters[page] = (callback, waited + delay / 1000.0,
                                           min(delay * 2, ORIGINS_POLL_MAX_MS))
            source = GLib.timeout_source_new(int(self._origins_waiters[page][2]))
            source.set_callback(lambda: (self._poll_origins(page), False)[1])
            source.attach(None)

        self._request({'op': 'origins', 'page': page,
                       'files_from': len(self._files)}, _reply)

    def _fail_all_waiters(self):
        waiters = self._origins_waiters
        self._origins_waiters = {}
        for callback, _waited, _delay in waiters.values():
            callback(None, None)
