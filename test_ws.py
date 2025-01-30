import sys
from argparse import ArgumentParser
from asyncio import CancelledError

import aiohttp
from aiohttp import ClientConnectorError

from utils import ainput, run_until


async def main():
    parser = ArgumentParser()
    parser.add_argument('host')
    parser.add_argument('port', type=int)
    parser.add_argument('url')
    args = parser.parse_args(sys.argv[1:])
    if not args.url.startswith('/'):
        args.url = '/' + args.url

    async with aiohttp.ClientSession() as session:
        try:
            async with session.ws_connect(f'http://{args.host}:{args.port}{args.url}') as conn:
                while True:
                    try:
                        s = await ainput(with_nl=True)
                    except (EOFError, CancelledError):
                        break
                    await conn.send_str(s)
                    print(await conn.receive_str(), end='')
        except ClientConnectorError as e:
            print(e)


if __name__ == '__main__':
    run_until(main())
