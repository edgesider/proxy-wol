import asyncio

from aiohttp import web, WSMessage, WSMsgType, ClientWebSocketResponse
from aiohttp.web_runner import AppRunner
from aiohttp.web_ws import WebSocketResponse

from logger import logger


async def serve(app: web.Application, host='0.0.0.0', port=8080):
    runner = AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.warning(f'Listening at http://{host}:{port}')


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
