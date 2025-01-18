import asyncio
import logging
import time
from collections import namedtuple
from typing import Union

import aiohttp
from aiohttp import web, WSMessage, ClientWebSocketResponse, ClientConnectionResetError
from aiohttp.web_request import Request
from aiohttp.web_ws import WebSocketResponse
from wakeonlan import send_magic_packet

TargetHost = namedtuple('TargetHost', ['host', 'port', 'mac', 'ssh_cmd'])


class ProxyWOL:
    target: TargetHost
    app: web.Application
    _last_wake_up_time = 0

    def __init__(self, target: TargetHost):
        self.target = target
        self.app = web.Application()
        for method in ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH', 'CONNECT', 'HEAD']:
            self.app.router.add_route(method, '/{tail:.*}', self.handle_http_proxy)  # 支持 GET 请求的反向代理

    def run(self, host='0.0.0.0', port=8080):
        web.run_app(self.app, host=host, port=port)

    async def _wakeup_and_keep(self):
        now = time.time()
        if now - self._last_wake_up_time < 30:
            return
        send_magic_packet(self.target.mac)
        self._last_wake_up_time = now
        # noinspection PyAsyncCall
        asyncio.create_task(self._keep_wake_up())
        logging.info('send wol and keep wake up')

    async def _keep_wake_up(self):
        proc = await asyncio.create_subprocess_shell(
            f'{self.target.ssh_cmd} qdbus org.freedesktop.ScreenSaver /ScreenSaver SimulateUserActivity'
        )
        await proc.wait()

    @staticmethod
    def is_websocket_upgrade(request: Request) -> bool:
        upgrade_header = request.headers.get('Upgrade', '').lower()
        return upgrade_header == 'websocket'

    async def handle_http_proxy(self, request: Request):
        await self._wakeup_and_keep()

        if self.is_websocket_upgrade(request):
            # 如果是 WebSocket 升级请求，处理为 WebSocket
            return await self.handle_ws_proxy(request)

        # 否则，转发普通 HTTP 请求
        target_url = f"http://{self.target.host}:{self.target.port}{request.rel_url}"

        async with aiohttp.ClientSession() as session:
            req_headers = request.headers.copy()
            req_headers['Accept-Encoding'] = ''
            async with session.request(request.method,
                                       target_url,
                                       headers=req_headers,
                                       params=request.query,
                                       data=await request.read()
                                       ) as resp:
                resp_headers = resp.headers.copy()
                # if resp_headers['Content-Encoding']:
                #     del resp_headers['Content-Encoding']
                return web.Response(status=resp.status,
                                    headers=resp_headers,
                                    body=await resp.read())

    async def handle_ws_proxy(self, request: Request):
        req_ws = web.WebSocketResponse()
        await req_ws.prepare(request)  # 升级为ws

        target_url = f'http://{self.target.host}:{self.target.port}{request.path_qs}'
        try:
            session = aiohttp.ClientSession()
            target_ws = await session.ws_connect(target_url, headers=request.headers)
        except Exception as e:
            logging.error(f'connect {target_url} failed', type(e), e)
            await req_ws.close()
            return

        async def join(from_ws: Union[WebSocketResponse, ClientWebSocketResponse],
                       to_ws: Union[WebSocketResponse, ClientWebSocketResponse]):
            try:
                async for msg in from_ws:
                    msg: WSMessage
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await to_ws.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await to_ws.send_bytes(msg.data)
                    elif msg.type == aiohttp.WSMsgType.CLOSE:
                        await to_ws.close()
            except ClientConnectionResetError:
                logging.info(f'connection reset {target_url}')

        try:
            await asyncio.gather(join(req_ws, target_ws), join(target_ws, req_ws))
        finally:
            await asyncio.gather(session.close(), req_ws.close())
        return req_ws


proxy = ProxyWOL(TargetHost('arch.tt', 8080, '7c:10:c9:9e:13:26', 'ssh kai@arch.tt'))


async def get_app():
    return proxy.app


if __name__ == '__main__':
    proxy.run('0.0.0.0', 8080)
