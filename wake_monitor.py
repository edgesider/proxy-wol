import asyncio
import signal
import time
from asyncio import Future
from typing import Callable

import aiohttp
from aiohttp import ClientConnectorError, ClientSession, ClientTimeout
from wakeonlan import send_magic_packet

from logger import logger

WAIT_AWAKE_TIMEOUT = 15
IS_AWAKE_CHECK_INTERVAL = 1
AUTO_WAKEUP_CHECK_INTERVAL = 10
MIN_TOUCH_DURATION = 30


class WakeMonitor:

    def __init__(
            self,
            mac: str,
            agent_url: str,
            should_awake: Callable[[], bool] = lambda: False
    ):
        """
        :param mac: mac 地址
        :param agent_url: agent 路径，例如：mac.lan:4322
        """
        self.mac = mac
        self.agent_url = agent_url
        self._check_task: asyncio.Task | None = None
        self._auto_wakeup_task: asyncio.Task | None = None
        self._is_awake = False
        self._wakeup_waiters: set[Future] = set()
        self._should_awake = should_awake

    def start(self, loop: asyncio.AbstractEventLoop | None = None):
        if loop is None:
            loop = asyncio.new_event_loop()

        def on_done(task: asyncio.Task):
            ex = task.exception()
            if ex:
                logger.error(f'monitor internal exception {ex.args}', exc_info=ex)

        self._check_task = loop.create_task(self._check(), name='wake-monitor-check')
        self._check_task.add_done_callback(on_done)
        self._auto_wakeup_task = loop.create_task(self._auto_wakeup(), name='wake-monitor-auto-wakeup')
        self._auto_wakeup_task.add_done_callback(on_done)

    def stop(self):
        if self._check_task:
            self._check_task.cancel('monitor stopped')
        if self._auto_wakeup_task:
            self._auto_wakeup_task.cancel('monitor stopped')

    @property
    def is_awake(self) -> bool:
        return self._is_awake

    def wakeup(self):
        if self._is_awake:
            return
        send_magic_packet(self.mac)

    async def wait_awake(self, timeout=WAIT_AWAKE_TIMEOUT) -> bool:
        if self._is_awake:
            return True
        fut = Future()
        self._wakeup_waiters.add(fut)
        done, pending = await asyncio.wait([fut], timeout=timeout)
        if fut in self._wakeup_waiters:
            self._wakeup_waiters.remove(fut)
        return len(done) > 0

    async def _do_touch(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.request('GET', f'http://{self.agent_url}/touch') as resp:
                    body = await resp.read()
                    if resp.status != 200:
                        logger.error(f'failed to touch agent {self.agent_url}: {body.decode()}')
            except Exception as e:
                logger.error(f'touch agent failed {e.args}')

    _last_touch = 0

    async def touch(self):
        now = time.time()
        if now - self._last_touch < MIN_TOUCH_DURATION:  # per 30s
            return
        self._last_touch = now
        await self.touch()

    def _get_agent_path(self, path: str):
        return f'http://{self.agent_url}/{path[1:] if path.startswith('/') else path}'

    async def _check(self):
        while True:
            last_is_awake = self._is_awake
            self._is_awake = await self._http_ping()
            logger.debug(f'is awake check result: {self._is_awake}')
            if self._is_awake != last_is_awake:
                logger.info(f'wake up status changed to {self._is_awake}')
                if self._is_awake:
                    for fut in self._wakeup_waiters:
                        fut.set_result(None)
                    self._wakeup_waiters.clear()
            await asyncio.sleep(IS_AWAKE_CHECK_INTERVAL)

    async def _auto_wakeup(self):
        while True:
            if self._should_awake():
                if self.is_awake:
                    logger.debug('should awake, touching')
                    await self.touch()
                else:
                    logger.debug('should awake, waking up')
                    self.wakeup()
                    await self.wait_awake()
            await asyncio.sleep(AUTO_WAKEUP_CHECK_INTERVAL)

    async def _http_ping(self, timeout=1) -> bool:
        async with ClientSession(timeout=ClientTimeout(timeout)) as session:
            try:
                async with session.get(self._get_agent_path('/ping')) as resp:
                    await resp.read()
            except (ClientConnectorError, TimeoutError) as e:
                logger.debug(f'ping failed {e}')
                return False
        return True


def on_exit(loop: asyncio.AbstractEventLoop):
    for task in asyncio.all_tasks():
        task.cancel()
    loop.call_soon(loop.stop)


def main():
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, on_exit, loop)

    wm = WakeMonitor('7c:10:c9:9e:13:26', 'arch.tt:4322')
    loop.call_soon(wm.start)
    loop.run_forever()


if __name__ == '__main__':
    main()
