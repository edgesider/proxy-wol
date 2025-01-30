from aiohttp.web import RouteTableDef


class FakeAgent:

    def __init__(self):
        pass

    async def wol(self):
        pass

    async def ping(self):
        pass

    async def touch(self):
        pass

