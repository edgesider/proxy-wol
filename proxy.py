import asyncio
import logging
import os
import time
from collections import namedtuple

import aiohttp
from aiohttp import web, WSMessage, ClientWebSocketResponse, ClientConnectorError, ClientConnectionResetError
from aiohttp.web_request import Request
from aiohttp.web_runner import AppRunner
from aiohttp.web_ws import WebSocketResponse
from wakeonlan import send_magic_packet

TargetHost = namedtuple('TargetHost', ['host', 'port', 'mac', 'ssh_cmd'])
type WebSocket = WebSocketResponse | ClientWebSocketResponse

logger = logging.Logger('proxy-wol', logging.DEBUG)


async def pipe_ws(from_ws: WebSocket, to_ws: WebSocket):
    async for msg in from_ws:
        msg: WSMessage
        if msg.type == aiohttp.WSMsgType.TEXT:
            await to_ws.send_str(msg.data)
        elif msg.type == aiohttp.WSMsgType.BINARY:
            await to_ws.send_bytes(msg.data)
        elif msg.type == aiohttp.WSMsgType.CLOSE:
            await to_ws.close()


async def join_ws(ws1: WebSocket, ws2: WebSocket):
    await asyncio.gather(pipe_ws(ws1, ws2), pipe_ws(ws2, ws1))


class ProxyWOL:
    target: TargetHost
    app: web.Application
    _last_wake_up_time = 0

    def __init__(self, target: TargetHost):
        self.target = target
        self.app = web.Application()
        for method in ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH', 'CONNECT', 'HEAD']:
            self.app.router.add_route(method, '/{tail:.*}', self.handle_http)

    async def serve(self, host='0.0.0.0', port=8080):
        runner = AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.warning(f'Listening at http://{host}:{port}. Upstream is http://{self.target.host}:{self.target.port}')

    async def _wakeup_and_keep(self):
        now = time.time()
        if now - self._last_wake_up_time < 30:
            return
        send_magic_packet(self.target.mac)
        self._last_wake_up_time = now
        # noinspection PyAsyncCall
        asyncio.create_task(self._keep_wake_up())
        logger.info('send wol and keep wake up')

    async def _keep_wake_up(self):
        proc = await asyncio.create_subprocess_shell(
            f'{self.target.ssh_cmd} qdbus org.freedesktop.ScreenSaver /ScreenSaver SimulateUserActivity'
        )
        await proc.wait()

    @staticmethod
    def is_websocket_upgrade(request: Request) -> bool:
        upgrade_header = request.headers.get('Upgrade', '').lower()
        return upgrade_header == 'websocket'

    async def handle_http(self, request: Request):
        target_url = f"http://{self.target.host}:{self.target.port}{request.rel_url}"
        await self._wakeup_and_keep()
        async with aiohttp.ClientSession() as session:
            req_headers = request.headers.copy()
            req_headers['Accept-Encoding'] = ''
            try:
                if not self.is_websocket_upgrade(request):
                    async with session.request(
                            request.method,
                            target_url,
                            headers=req_headers,
                            params=request.query,
                            data=await request.read(),
                    ) as resp:
                        resp_headers = resp.headers.copy()
                        return web.Response(status=resp.status,
                                            headers=resp_headers,
                                            body=await resp.read())
                else:
                    target_ws = await session.ws_connect(target_url, headers=req_headers)
            except ClientConnectorError as e:
                logger.error(f'connect {target_url} failed: %s', str(type(e).__name__))
                return web.Response(status=502, body='502 Bad Gateway')

            req_ws = web.WebSocketResponse()
            await req_ws.prepare(request)  # 升级为ws

            try:
                await join_ws(req_ws, target_ws)
            except (ClientConnectorError, ClientConnectionResetError):
                logger.info(f'connection reset {request.url}')
            return req_ws


proxy = ProxyWOL(TargetHost(
    os.environ.get('TARGET_HOST'),
    int(os.environ.get('TARGET_PORT')),
    os.environ.get('TARGET_MAC'),
    os.environ.get('TARGET_SHELL_CMD'),
))


async def main():
    if os.environ.get('SERVER_SOFTWARE', '').startswith('gunicorn'):
        return proxy.app
    else:
        await proxy.serve('0.0.0.0', 8080)


if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    loop.create_task(main())
    try:
        loop.run_forever()
    finally:
        loop.run_until_complete(proxy.app.shutdown())
