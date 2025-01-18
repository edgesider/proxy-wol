import asyncio

import aiohttp
from aiohttp import WSMessage, WSMsgType

async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect('http://localhost:8080') as ws:
            await ws.send_str('123')
            await ws.send_bytes(b'123')
            async for msg in ws:
                msg: WSMessage
                if msg.type == WSMsgType.TEXT:
                    data: str = msg.data
                    print('receive text ' + data)
                elif msg.type == WSMsgType.BINARY:
                    data: bytes = msg.data
                    print('receive binary ' + str(data))

if __name__ == '__main__':
    asyncio.run(main())