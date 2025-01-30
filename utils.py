import asyncio
import signal
import sys
from asyncio import Future, CancelledError
from typing import AnyStr, Callable, Awaitable, Any

from aiohttp import web, WSMessage, WSMsgType, ClientWebSocketResponse
from aiohttp.web_runner import AppRunner
from aiohttp.web_ws import WebSocketResponse

from logger import logger


async def serve(app: web.Application, host='0.0.0.0', port=8080):
    fut = Future()

    async def on_shutdown(_):
        fut.set_result(None)

    app.on_shutdown.append(on_shutdown)

    runner = AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.warning(f'Listening at http://{host}:{port}')

    await fut


type WebSocket = WebSocketResponse | ClientWebSocketResponse


async def pipe_ws(from_ws: WebSocket, to_ws: WebSocket):
    async for msg in from_ws:
        msg: WSMessage
        if msg.type == WSMsgType.TEXT:
            await to_ws.send_str(msg.data)
        elif msg.type == WSMsgType.BINARY:
            await to_ws.send_bytes(msg.data)
        elif msg.type == WSMsgType.CLOSE:
            await to_ws.close()
        elif msg.type == WSMsgType.PING:
            await to_ws.ping(msg.data)
        elif msg.type == WSMsgType.PONG:
            await to_ws.pong(msg.data)
    await to_ws.close()


async def join_ws(ws1: WebSocket, ws2: WebSocket):
    await asyncio.gather(pipe_ws(ws1, ws2), pipe_ws(ws2, ws1), return_exceptions=True)


async def ainput(with_nl=False) -> AnyStr:
    fut: Future[AnyStr] = Future()
    file = sys.stdin

    def on_read():
        line = file.readline()
        if len(line) == 0:
            fut.set_exception(EOFError())
        else:
            fut.set_result(line.rstrip('\n') if not with_nl else line)

    loop = asyncio.get_running_loop()
    loop.add_reader(file.fileno(), on_read)
    try:
        return await fut
    finally:
        loop.remove_reader(file.fileno())


CleanUpFunc = Callable[[], bool | None | Awaitable]


def install_on_exit(loop: asyncio.AbstractEventLoop, cleanup: CleanUpFunc = None):
    def do_exit(_=None):
        running_task_count = 0

        def on_task_done(_):
            nonlocal running_task_count
            running_task_count -= 1
            if running_task_count == 0:
                loop.stop()

        tasks = asyncio.all_tasks(loop)
        for task in tasks:
            if task.done():
                continue
            running_task_count += 1
            task.cancel()
            task.add_done_callback(on_task_done)
        if running_task_count == 0:
            loop.stop()

    def on_exit():
        if cleanup is not None:
            result = cleanup()
            # noinspection PySimplifyBooleanCheck
            if result == False:
                return
            elif isinstance(result, Awaitable):
                loop.create_task(result).add_done_callback(do_exit)
        else:
            do_exit()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, on_exit)


def run_forever(func: Callable[[], Any], cleanup: CleanUpFunc = None):
    loop = asyncio.new_event_loop()
    install_on_exit(loop, cleanup)
    loop.call_soon(func)
    try:
        loop.run_forever()
    except CancelledError:
        pass


def run_until(awaitable: Awaitable | Callable[[], Awaitable], cleanup: CleanUpFunc = None):
    loop = asyncio.new_event_loop()
    install_on_exit(loop, cleanup)

    async def _():
        await (awaitable() if isinstance(awaitable, Callable) else awaitable)

    try:
        loop.run_until_complete(loop.create_task(_(), name='main-task'))
    except CancelledError:
        pass
