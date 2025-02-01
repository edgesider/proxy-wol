import asyncio
import os
import signal
from asyncio import get_running_loop
from collections import namedtuple

import aiohttp
from aiohttp import web, ClientConnectorError, ClientConnectionResetError
from aiohttp.web_request import Request
from aiohttp.web_response import Response

from logger import logger
from utils import serve, WebSocket, join_ws, run_until, get_env, get_env_int
from wake_monitor import WakeMonitor

TargetHost = namedtuple('TargetHost', ['host', 'port', 'agent_port', 'mac'])


class ProxyWOL:

    def __init__(self, target: TargetHost):
        self.target = target
        self.app = web.Application(
            client_max_size=1024 ** 3, # 1G
        )
        self.monitor = WakeMonitor(
            target.mac, f'{target.host}:{target.agent_port}',
            self._keep_awake
        )
        for method in ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH', 'CONNECT', 'HEAD']:
            self.app.router.add_route(method, '/{tail:.*}', self._handle_http)
        self.app.on_startup.append(self._on_start)
        self.app.on_shutdown.append(self._on_shutdown)
        self._session = aiohttp.ClientSession()
        self._active_conn: set[int] = set()

    def _keep_awake(self) -> bool:
        return len(self._active_conn) > 0

    async def _on_start(self, _):
        self.monitor.start()

    async def _on_shutdown(self, _):
        await self._session.close()
        self.monitor.stop()

    async def _wakeup_and_touch(self) -> bool:
        if self.monitor.is_awake:
            self._touch()
            return True

        self.monitor.wakeup()
        if not await self.monitor.wait_awake():
            logger.warning('failed to wake up')
            return False
        self._touch()
        logger.info('sent wol packet and touch agent')
        return True

    def _touch(self):
        asyncio.create_task(self.monitor.touch())  # 异步触发touch

    @staticmethod
    def _is_websocket_upgrade(request: Request) -> bool:
        upgrade_header = request.headers.get('Upgrade', '').lower()
        return upgrade_header == 'websocket'

    def _get_target_url(self, request: Request):
        return f'http://{self.target.host}:{self.target.port}{request.rel_url}'

    async def _handle_http(self, request: Request):
        try:
            self._active_conn.add(id(request))
            return await self._do_handle_http(request)
        finally:
            self._active_conn.remove(id(request))

    async def _do_handle_http(self, request: Request):
        target_url = self._get_target_url(request)
        if not await self._wakeup_and_touch():
            return Response(status=502, body='wake up failed')
        req_headers = request.headers.copy()
        req_headers['Accept-Encoding'] = ''
        try:
            if not self._is_websocket_upgrade(request):
                async with self._session.request(
                        request.method,
                        target_url,
                        headers=req_headers,
                        params=request.query,
                        data=await request.read(),
                ) as resp:
                    resp_headers = resp.headers.copy()
                    return web.Response(
                        status=resp.status,
                        headers=resp_headers,
                        body=await resp.read())
            else:
                target_ws = await self._session.ws_connect(target_url, headers=req_headers)
        except ClientConnectorError as e:
            logger.error(f'connect {target_url} failed: %s', str(type(e).__name__))
            return web.Response(status=502, body='502 Bad Gateway')

        return await self._handle_ws(request, target_ws)

    @staticmethod
    async def _handle_ws(request, target: WebSocket):
        # upgrade to websocket
        req_ws = web.WebSocketResponse()
        await req_ws.prepare(request)

        try:
            await join_ws(req_ws, target)
        except (ClientConnectorError, ClientConnectionResetError):
            logger.info(f'connection reset {request.url}')
        return req_ws


proxy: ProxyWOL | None = None


# noinspection PyProtectedMember
def dump_info(*args):
    print(
        f'Status: running\n' +
        f'Target: {proxy.target}\n' +
        f'Target is awake: {proxy.monitor.is_awake}\n' +
        f'Last touch: {proxy.monitor._last_touch}\n' +
        f'Active websockets: {len(proxy._active_conn)}\n'
        f'Should keep awake: {proxy._keep_awake()}\n'
    )


async def main():
    global proxy
    proxy = ProxyWOL(TargetHost(
        get_env('TARGET_HOST'),
        get_env_int('TARGET_PORT'),
        get_env_int('TARGET_AGENT_PORT'),
        get_env('TARGET_MAC'),
    ))

    get_running_loop().add_signal_handler(signal.SIGUSR1, dump_info)

    logger.info(f'PID={os.getpid()}')
    if os.environ.get('SERVER_SOFTWARE', '').startswith('gunicorn'):
        return proxy.app
    else:
        logger.warning(f'Upstream is http://{proxy.target.host}:{proxy.target.port}')
        await serve(proxy.app, host='0.0.0.0', port=4321)


if __name__ == '__main__':
    run_until(main(), lambda: proxy.app.shutdown())
