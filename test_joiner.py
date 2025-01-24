import asyncio
import logging

import aiohttp
from aiohttp import WSMessage, WSMsgType, web, ClientSession
from aiohttp.web_runner import AppRunner

from proxy import join_ws


async def ainput(prompt: str = ""):
    return await asyncio.to_thread(input, prompt)


def is_websocket_upgrade(request: web.Request) -> bool:
    upgrade_header = request.headers.get('Upgrade', '').lower()
    return upgrade_header == 'websocket'


def serve_echo():
    app = web.Application()

    async def handle_ws(request: web.Request):
        if not is_websocket_upgrade(request):
            return web.Response(status=500, body='not websocket')
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            msg: WSMessage
            if msg.type == WSMsgType.TEXT:
                await ws.send_str(msg.data)
            # elif msg.type == WSMsgType.BINARY:
            #     await ws.send_bytes(msg.data)
            # elif msg.type == WSMsgType.CLOSE:
            #     await ws.close()
            # elif msg.type == WSMsgType.PING:
            #     await ws.ping(msg.data)
            # elif msg.type == WSMsgType.PONG:
            #     await ws.pong(msg.data)
        print('shutdown echo')
        await app.shutdown()
        return ws

    app.router.add_route('GET', '/ws', handle_ws)
    return app


def serve_joiner():
    app = web.Application()

    async def handle_ws(request: web.Request):
        if not is_websocket_upgrade(request):
            return web.Response(status=500, body='not websocket')
        async with ClientSession() as session:
            req_ws = web.WebSocketResponse()
            await req_ws.prepare(request)

            async with session.ws_connect('http://localhost:8080/ws') as target_ws:
                await join_ws(req_ws, target_ws)
        print('shutdown joiner')
        await app.shutdown()
        return req_ws

    app.router.add_route('GET', '/ws', handle_ws)
    return app


async def serve(app: web.Application, host='0.0.0.0', port=8080):
    runner = AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logging.warning(f'Listening at http://{host}:{port}')


async def main():
    await asyncio.sleep(1)  # wait for servers ready
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect('http://localhost:8081/ws') as ws:
            inputs = ['123', '321', '312', '132', '213', '231']
            for text in inputs:
                await ws.send_str(text)
            datas = []
            async for msg in ws:
                msg: WSMessage
                if msg.type == WSMsgType.TEXT:
                    data: str = msg.data
                    datas.append(data)
                if len(datas) == len(inputs):
                    break

            assert datas == ['123', '321', '312', '132', '213', '231']
            print('test pass, closing')
            await ws.close()
    await asyncio.sleep(1)  # wait for servers shutdown


if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    g = asyncio.gather(
        loop.create_task(serve(serve_echo(), port=8080), name='echo-server'),
        loop.create_task(serve(serve_joiner(), port=8081), name='join-server'),
        loop.create_task(main(), name='client'),
        return_exceptions=True
    )
    loop.run_until_complete(g)
