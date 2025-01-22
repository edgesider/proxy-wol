import asyncio
import os
import signal
from collections import namedtuple

import aiohttp
from aiohttp import web, WSMessage, ClientWebSocketResponse, ClientConnectorError, ClientConnectionResetError
from aiohttp.web_request import Request
from aiohttp.web_response import Response
from aiohttp.web_runner import AppRunner
from aiohttp.web_ws import WebSocketResponse

from logger import logger
from wake_monitor import WakeMonitor

TargetHost = namedtuple('TargetHost', ['host', 'port', 'agent_port', 'mac'])
type WebSocket = WebSocketResponse | ClientWebSocketResponse


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

    def __init__(self, target: TargetHost):
        self.target = target
        self.app = web.Application()
        self.monitor = WakeMonitor(target.mac, f'{target.host}:{target.agent_port}')
        for method in ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH', 'CONNECT', 'HEAD']:
            self.app.router.add_route(method, '/{tail:.*}', self.handle_http)

        async def on_start(_):
            self.monitor.start(asyncio.get_running_loop())

        self.app.on_startup.append(on_start)

    async def serve(self, host='0.0.0.0', port=4321):
        runner = AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.warning(f'Listening at http://{host}:{port}. Upstream is http://{self.target.host}:{self.target.port}')

    async def _wakeup_and_touch(self) -> bool:
        if self.monitor.is_awake:
            return True

        self.monitor.wakeup()
        if not await self.monitor.wait_awake():
            logger.warning('failed to wake up')
            return False
        asyncio.create_task(self.monitor.touch())  # 异步触发touch
        logger.info('sent wol packet and touch agent')
        return True

    @staticmethod
    def is_websocket_upgrade(request: Request) -> bool:
        upgrade_header = request.headers.get('Upgrade', '').lower()
        return upgrade_header == 'websocket'

    async def handle_http(self, request: Request):
        target_url = f"http://{self.target.host}:{self.target.port}{request.rel_url}"
        if not await self._wakeup_and_touch():
            return Response(status=502, body='wake up failed')
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
    int(os.environ.get('TARGET_AGENT_PORT')),
    os.environ.get('TARGET_MAC'),
))


async def main():
    if os.environ.get('SERVER_SOFTWARE', '').startswith('gunicorn'):
        return proxy.app
    else:
        await proxy.serve('0.0.0.0', 4321)


if __name__ == '__main__':
    def on_exit(loop: asyncio.EventLoop):
        proxy.app.shutdown()
        for task in asyncio.all_tasks():
            task.cancel()
        loop.call_soon(loop.stop)


    loop = asyncio.new_event_loop()
    loop.create_task(main())
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, on_exit, loop)
    loop.run_forever()
